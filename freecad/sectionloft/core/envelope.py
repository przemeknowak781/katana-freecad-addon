"""Outer boundary of a set of section contours, by radial sampling.

Why this exists.  Cutting a thin-walled part gives a *ribbon*: one closed
polyline that runs out along the outer wall and back along the inner one,
sometimes breaking into several pieces where there are slots.  Lofting ribbon to
ribbon cannot work - the profiles are degenerate-thin, their point
correspondence is arbitrary, and the result self-intersects.  Measured on a
17 mm mask: every parameter combination tried gave a volume between 0.01 and
14850 times the mesh, which is another way of saying the answer was noise.

An envelope is the useful thing anyway.  The spec (1.3) builds an enclosure
*around* mechanics and leaves walls to ``Part Thickness``; the outer boundary is
exactly the input that workflow wants.

The method is deliberately the blunt one: fire rays from a centre point and keep
the farthest hit.  It buys three properties that matter more here than fidelity
to a concave detail:

* the result is a simple polygon - it cannot self-intersect,
* every section gets the same number of points in the same angular order, so
  lofting has perfect correspondence and cannot twist,
* every section gets exactly one contour, so there is nothing to pair.

The limit is the obvious one: only star-shaped sections are reproduced
faithfully. A section with a deep concave bay gets it bridged.
"""

import numpy as np

from .contours import as_points, to_plane_coords
from .planes import orthonormal_frame

DEFAULT_SAMPLES = 180


def _ray_hits(origin, direction, starts, ends):
    """Farthest intersection of a ray with a set of 2D segments, or None.

    ``origin``/``direction`` are ``(2,)``; ``starts``/``ends`` are ``(m, 2)``.
    """
    edge = ends - starts
    denom = direction[0] * edge[:, 1] - direction[1] * edge[:, 0]
    usable = np.abs(denom) > 1e-12
    if not np.any(usable):
        return None

    offset = starts - origin
    # t along the ray, s along the segment
    t = (offset[:, 0] * edge[:, 1] - offset[:, 1] * edge[:, 0]) / np.where(
        usable, denom, 1.0)
    s = (offset[:, 0] * direction[1] - offset[:, 1] * direction[0]) / np.where(
        usable, denom, 1.0)

    hit = usable & (t > 1e-9) & (s >= -1e-9) & (s <= 1.0 + 1e-9)
    if not np.any(hit):
        return None
    return float(t[hit].max())


def _segments(polygons):
    """All segments of all closed polygons, as ``(starts, ends)``."""
    starts, ends = [], []
    for poly in polygons:
        if len(poly) < 2:
            continue
        starts.append(poly)
        ends.append(np.roll(poly, -1, axis=0))
    if not starts:
        return None, None
    return np.vstack(starts), np.vstack(ends)


def convex_hull_2d(points):
    """Convex hull of 2D points, counter-clockwise (Andrew's monotone chain)."""
    pts = np.unique(np.asarray(points, dtype=float).reshape(-1, 2), axis=0)
    if len(pts) < 3:
        return pts
    order = np.lexsort((pts[:, 1], pts[:, 0]))
    pts = pts[order]

    def half(sequence):
        chain = []
        for point in sequence:
            while len(chain) >= 2:
                a, b = chain[-2], chain[-1]
                cross = ((b[0] - a[0]) * (point[1] - a[1])
                         - (b[1] - a[1]) * (point[0] - a[0]))
                if cross > 0:
                    break
                chain.pop()
            chain.append(point)
        return chain

    lower = half(pts)
    upper = half(pts[::-1])
    return np.array(lower[:-1] + upper[:-1], dtype=float)


def smooth_radii(radii, window):
    """Circular moving average over the radial profile.

    Rays hitting a faceted mesh return radii that jitter by a fraction of a
    facet, which is enough to make the turn angle between neighbouring samples
    exceed any sensible corner threshold: on the test mesh a perfectly smooth
    section came back with 34 "corners" and a 0.97 mm fit deviation.  Averaging
    over three samples is well below the angular size of any real feature and
    takes both problems out.
    """
    window = int(window)
    if window < 3 or len(radii) < window:
        return radii
    if window % 2 == 0:
        window += 1
    kernel = np.ones(window) / window
    padding = window // 2
    padded = np.concatenate([radii[-padding:], radii, radii[:padding]])
    return np.convolve(padded, kernel, mode="valid")


def envelope_2d(polygons, centre=None, samples=DEFAULT_SAMPLES, smoothing=3,
                collapse_factor=0.5, hull_fraction=0.3, clearance=0.0):
    """Outer boundary of 2D polygons as a fixed-resolution radial profile.

    Returns an ``(n, 2)`` array, or None when nothing was hit.  Angles that hit
    nothing - a section shaped like a C, sampled through its opening - are
    filled in from their neighbours, so the profile stays closed and smooth
    rather than developing a notch that would wreck the loft.
    """
    polys = [np.asarray(p, dtype=float).reshape(-1, 2) for p in polygons
             if len(p) >= 3]
    if not polys:
        return None

    starts, ends = _segments(polys)
    if starts is None:
        return None

    # The convex hull bounds the answer from above and, crucially, gives a
    # sensible radius in directions where the material boundary is not reachable
    # from the centre at all - the open side of a C-shaped section.  Its
    # centroid is guaranteed to be inside it, which the centroid of the raw
    # points is not.
    hull = convex_hull_2d(np.vstack(polys))
    if len(hull) < 3:
        return None
    hull_starts, hull_ends = _segments([hull])
    if centre is None:
        centre = hull.mean(axis=0)
    centre = np.asarray(centre, dtype=float).reshape(2)

    angles = np.linspace(0.0, 2.0 * np.pi, int(samples), endpoint=False)
    radii = np.zeros(len(angles))
    hull_radii = np.zeros(len(angles))
    fallbacks = 0
    for i, angle in enumerate(angles):
        direction = np.array([np.cos(angle), np.sin(angle)])
        outer = _ray_hits(centre, direction, hull_starts, hull_ends)
        hull_radii[i] = 0.0 if outer is None else outer
        material = _ray_hits(centre, direction, starts, ends)
        # A hit far below the hull is not a concavity, it is a miss: the ray
        # left through the opening and struck some small feature on the way.
        # Measured on the test mesh, sections running about 7 mm across produced
        # samples of 0.07 mm, and those notches are what tore the loft apart.
        # Falling back to the hull bridges the opening, which is what an
        # envelope is for - containing the part, not reaching into its gaps.
        if material is None or material < collapse_factor * hull_radii[i]:
            radii[i] = hull_radii[i]
            fallbacks += 1
        else:
            radii[i] = material

    if not np.any(radii > 0.0):
        return None

    # Where the material is sparse - a section cutting through a few thin tabs -
    # most rays fall back and the profile alternates between two different
    # definitions of the boundary, which comes out as a jagged star.  One
    # definition for the whole contour is worth more than a closer fit on part
    # of it: mixing them is what put a flat jagged fan across the top of the
    # test result.
    if fallbacks > hull_fraction * len(angles):
        radii = hull_radii

    radii = smooth_radii(radii, smoothing)

    # A section-lofted surface interpolates between planes and cuts inside the
    # part wherever it bulges in between - measured at 0.62 mm on the test mesh
    # with sections 0.96 mm apart.  For a packaging study the envelope has to
    # contain the part, so the clearance is a radial offset, not a fudge.
    if clearance:
        radii = radii + float(clearance)

    return centre + np.column_stack((radii * np.cos(angles),
                                     radii * np.sin(angles)))


def _point_in_polygon(point, polygon):
    """Ray casting; the polygon is assumed closed and simple."""
    x, y = float(point[0]), float(point[1])
    poly = np.asarray(polygon, dtype=float).reshape(-1, 2)
    inside = False
    for i in range(len(poly)):
        x0, y0 = poly[i]
        x1, y1 = poly[(i + 1) % len(poly)]
        if (y0 > y) != (y1 > y):
            crossing = x0 + (y - y0) * (x1 - x0) / (y1 - y0)
            if x < crossing:
                inside = not inside
    return inside


def radial_max(points_2d, centre, samples=DEFAULT_SAMPLES):
    """Largest radius per angular bin over a point cloud.

    Returns radii with ``nan`` in bins nothing landed in.

    Casting rays at the section polyline only sees the plane it was cut on, and
    a loft between two such planes shaves off whatever the part does in between:
    on the test mesh, with sections 0.96 mm apart, thin tabs poked 0.6 mm
    through their own envelope.  Taking the maximum over every vertex in a slab
    around the plane makes containment a property of the construction instead of
    something to be measured afterwards and apologised for.
    """
    pts = np.asarray(points_2d, dtype=float).reshape(-1, 2) - np.asarray(
        centre, dtype=float).reshape(2)
    if len(pts) == 0:
        return np.full(int(samples), np.nan)

    radii = np.linalg.norm(pts, axis=1)
    angles = np.arctan2(pts[:, 1], pts[:, 0]) % (2.0 * np.pi)
    bins = np.minimum((angles / (2.0 * np.pi) * samples).astype(int),
                      int(samples) - 1)

    # Accumulate into -inf, not nan: np.maximum propagates nan, so seeding with
    # nan leaves every bin empty and the whole thing silently returns nothing.
    out = np.full(int(samples), -np.inf)
    np.maximum.at(out, bins, radii)
    out[np.isneginf(out)] = np.nan
    return out


def dilate_radii(radii, window=3):
    """Rolling maximum around the circle.

    Binning a point cloud by angle leaves the profile as jagged as the sampling;
    a rolling maximum widens each bin's influence to its neighbours, which both
    smooths the steps and can only ever move the boundary outwards.  Averaging
    alone would cut back inside the part, which is the one direction an envelope
    must not go.
    """
    window = int(window)
    if window < 3 or len(radii) < window:
        return radii
    if window % 2 == 0:
        window += 1
    half = window // 2
    stacked = np.vstack([np.roll(radii, k) for k in range(-half, half + 1)])
    return stacked.max(axis=0)


def envelope_from_points(points_2d, centre=None, samples=DEFAULT_SAMPLES,
                         smoothing=0, clearance=0.0, dilation=0):
    """Envelope of a point cloud: its convex hull, sampled radially.

    The hull is the one definition that makes containment a theorem rather than
    a measurement.  Every point is inside it by construction, it is simple, and
    because it is convex it is star-shaped about any interior point - so it can
    be resampled at fixed angles from a shared centre without the profile
    folding.  Sampling loses at most ``r * (1 - cos(pi / samples))``, which at
    the default 180 rays is a few microns.

    What it gives up is concavity: a waisted part comes back barrelled.  That is
    the deliberate trade.  Everything softer that was tried here - farthest ray
    hit, per-bin maxima, hull-only-when-sparse - produced sections that
    contradicted their neighbours, and the lofts folded into shards.  Use
    ContourMode All when the sections themselves are what matters.
    """
    pts = np.asarray(points_2d, dtype=float).reshape(-1, 2)
    if len(pts) < 3:
        return None
    hull = convex_hull_2d(pts)
    if len(hull) < 3:
        return None

    hull_starts, hull_ends = _segments([hull])
    interior = hull.mean(axis=0)
    if centre is None or not _point_in_polygon(centre, hull):
        centre = interior
    centre = np.asarray(centre, dtype=float).reshape(2)

    angles = np.linspace(0.0, 2.0 * np.pi, int(samples), endpoint=False)
    radii = np.zeros(len(angles))
    for i, angle in enumerate(angles):
        direction = np.array([np.cos(angle), np.sin(angle)])
        distance = _ray_hits(centre, direction, hull_starts, hull_ends)
        radii[i] = 0.0 if distance is None else distance
    if not np.any(radii > 0.0):
        return None

    if dilation:
        radii = dilate_radii(radii, dilation)
    if smoothing:
        radii = smooth_radii(radii, smoothing)
    if clearance:
        radii = radii + float(clearance)

    angles = np.linspace(0.0, 2.0 * np.pi, int(samples), endpoint=False)
    return centre + np.column_stack((radii * np.cos(angles),
                                     radii * np.sin(angles)))


def envelope_from_slab(points_3d, base, normal, samples=DEFAULT_SAMPLES,
                       smoothing=3, clearance=0.0, axis_point=None):
    """Envelope of 3D points projected onto a plane, back in 3D.

    ``axis_point`` gives every section the same centre.  Letting each section
    pick its own - the centroid of whatever it happens to contain - makes the
    centre wander from plane to plane, and since the profiles are radial about
    it, the loft twists between them.  Measured: a wandering centre dropped the
    surface to 82% of the volume its own sections implied.
    """
    u, v, _ = orthonormal_frame(normal)
    base = np.asarray(base, dtype=float).reshape(3)
    projected = to_plane_coords(as_points(points_3d), base, u, v)

    centre = None
    if axis_point is not None:
        centre = to_plane_coords(np.asarray(axis_point, dtype=float).reshape(1, 3),
                                 base, u, v)[0]

    profile = envelope_from_points(projected, centre, samples, smoothing,
                                   clearance)
    if profile is None:
        return None
    return base + profile[:, 0:1] * u + profile[:, 1:2] * v


def envelope_contour(contours, base, normal, samples=DEFAULT_SAMPLES,
                     centre=None, smoothing=3, clearance=0.0):
    """Envelope of 3D section contours, back in 3D.

    ``contours`` is a list of ``(n, 3)`` arrays lying in the plane given by
    ``base`` and ``normal``.  Returns an ``(n, 3)`` array of points on that
    plane, or None.
    """
    u, v, _ = orthonormal_frame(normal)
    base = np.asarray(base, dtype=float).reshape(3)
    polygons = [to_plane_coords(as_points(c), base, u, v) for c in contours
                if len(c) >= 3]
    if not polygons:
        return None

    if centre is not None:
        centre = to_plane_coords(np.asarray(centre).reshape(1, 3), base, u, v)[0]

    profile = envelope_2d(polygons, centre, samples, smoothing,
                          clearance=clearance)
    if profile is None:
        return None
    return base + profile[:, 0:1] * u + profile[:, 1:2] * v


def is_ribbon(points, closed=True, threshold=0.25):
    """True when a contour is a thin ribbon rather than a filled outline.

    Compares the enclosed area with the area a compact shape of the same
    perimeter would have.  A circle scores 1.0, a 10:1 rectangle about 0.3, and
    the cross-section of a thin wall lands near zero because it encloses only
    the material between two nearly-coincident boundaries.
    """
    from .contours import contour_length, signed_area

    pts = as_points(points)
    if len(pts) < 3:
        return False
    length = contour_length(pts, closed)
    if length <= 0.0:
        return False
    normal = None
    from .contours import polygon_normal
    normal = polygon_normal(pts)
    if np.linalg.norm(normal) < 1e-9:
        return False
    u, v, _ = orthonormal_frame(normal)
    area = abs(signed_area(to_plane_coords(pts, pts[0], u, v)))
    compact = length * length / (4.0 * np.pi)
    return (area / compact) < threshold if compact > 0 else False
