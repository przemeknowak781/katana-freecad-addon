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


def envelope_profile(polygons, centre, samples=DEFAULT_SAMPLES,
                     collapse_factor=0.5):
    """Raw radii for one section: ``(material, hull, centre)``.

    The centre comes back because it may not be the one that was asked for: an
    axis outside this section's hull gets slid inside, and radii measured from
    one point but rebuilt around another produce a profile far larger than the
    part.  On the test mesh that showed up as sections reaching 15 mm where the
    mesh only reaches 9.5, and as a large flat fin on the lofted surface.

    ``material`` carries ``nan`` where the ray found nothing, or found only
    something so far inside the hull that it must have escaped through an
    opening.  Deciding what to do about those gaps is the caller's business -
    and it should not be decided section by section, because a section that
    bridges where its neighbour follows produces a step in the surface.  On the
    test mesh those steps reached 10 mm and were most of the corrugation.
    """
    polys = [np.asarray(p, dtype=float).reshape(-1, 2) for p in polygons
             if len(p) >= 3]
    if not polys:
        return None, None, None
    starts, ends = _segments(polys)
    if starts is None:
        return None, None, None

    hull = convex_hull_2d(np.vstack(polys))
    if len(hull) < 3:
        return None, None, None
    hull_starts, hull_ends = _segments([hull])
    centre = (hull.mean(axis=0) if centre is None
              else clamp_into(centre, hull))
    centre = np.asarray(centre, dtype=float).reshape(2)

    angles = np.linspace(0.0, 2.0 * np.pi, int(samples), endpoint=False)
    material = np.full(len(angles), np.nan)
    hull_radii = np.zeros(len(angles))
    for i, angle in enumerate(angles):
        direction = np.array([np.cos(angle), np.sin(angle)])
        outer = _ray_hits(centre, direction, hull_starts, hull_ends)
        hull_radii[i] = 0.0 if outer is None else outer
        hit = _ray_hits(centre, direction, starts, ends)
        if hit is not None and hit >= collapse_factor * hull_radii[i]:
            material[i] = hit
    return material, hull_radii, centre


def fill_along_axis(material, hull, smoothing=3):
    """Turn a stack of raw section profiles into a coherent radial field.

    ``material`` and ``hull`` are ``(sections, angles)``.  Gaps are filled from
    the *same angle in neighbouring sections* rather than from the hull of the
    section they are in: a feature that a ray misses at one height is almost
    always visible just above and below, and interpolating along the axis keeps
    the surface continuous where bridging per section put a step in it.  An
    angle no section can see falls back to the hull, which is the only honest
    answer there.

    A median along the axis then removes single-section spikes.  It is a median
    and not an average because a real step - the edge of a flat facet - has to
    survive, and averaging would ramp it over three sections.
    """
    material = np.array(material, dtype=float)
    hull = np.array(hull, dtype=float)
    sections, angles = material.shape

    for i in range(angles):
        column = material[:, i]
        known = ~np.isnan(column)
        if not np.any(known):
            material[:, i] = hull[:, i]
        elif not np.all(known):
            index = np.arange(sections)
            material[:, i] = np.interp(index, index[known], column[known])

    window = int(smoothing)
    if window >= 3 and sections >= window:
        if window % 2 == 0:
            window += 1
        half = window // 2
        padded = np.vstack([material[:1]] * half + [material]
                           + [material[-1:]] * half)
        material = np.array([np.median(padded[k:k + window], axis=0)
                             for k in range(sections)])
    return material


def envelope_2d(polygons, centre=None, samples=DEFAULT_SAMPLES, smoothing=0,
                collapse_factor=0.5, hull_fraction=0.3, clearance=0.0,
                convex=False):
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
    # A centre outside the shape is not a centre: rays from it miss the material
    # over a wide arc and the profile collapses to zero radius there.
    centre = (hull.mean(axis=0) if centre is None
              else clamp_into(centre, hull))
    centre = np.asarray(centre, dtype=float).reshape(2)

    angles = np.linspace(0.0, 2.0 * np.pi, int(samples), endpoint=False)
    radii = np.zeros(len(angles))
    hull_radii = np.zeros(len(angles))
    fallbacks = 0
    if convex:
        collapse_factor = 2.0        # force every sample onto the hull
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


def clip_convex(polygon, clipper):
    """Sutherland-Hodgman clip of a convex polygon by a convex clipper.

    Both are counter-clockwise, so the interior of each clipper edge is its left
    side.  Returns the intersection, possibly empty.
    """
    result = np.asarray(polygon, dtype=float).reshape(-1, 2)
    clip = np.asarray(clipper, dtype=float).reshape(-1, 2)

    for i in range(len(clip)):
        if len(result) == 0:
            return result
        a = clip[i]
        b = clip[(i + 1) % len(clip)]
        edge = b - a

        def side(point):
            return edge[0] * (point[1] - a[1]) - edge[1] * (point[0] - a[0])

        output = []
        for k in range(len(result)):
            current = result[k]
            previous = result[k - 1]
            sc, sp = side(current), side(previous)
            if sc >= 0.0:
                if sp < 0.0:
                    t = sp / (sp - sc)
                    output.append(previous + t * (current - previous))
                output.append(current)
            elif sp >= 0.0:
                t = sp / (sp - sc)
                output.append(previous + t * (current - previous))
        result = np.array(output, dtype=float).reshape(-1, 2)
    return result


def common_interior(hulls):
    """Intersection of every hull, or None when they share no interior."""
    valid = [np.asarray(h, dtype=float).reshape(-1, 2) for h in hulls
             if h is not None and len(h) >= 3]
    if not valid:
        return None
    region = valid[0]
    for hull in valid[1:]:
        region = clip_convex(region, hull)
        if len(region) < 3:
            return None
    return region


def shared_axis(hulls):
    """A 2D point inside as many of the given hulls as possible.

    Radial profiles only line up between sections if they are measured from the
    same place, so the axis has to be chosen once for the whole family.  But it
    also has to be *inside* each section, and the obvious candidate - the centre
    of the mesh bounding box - is not: on the test mesh it fell outside the hull
    of a section near the top, rays from it missed the material over a 200 degree
    arc, and the profile collapsed to zero radius there.

    Returns ``(axis, misses)``: the chosen point and how many hulls still do not
    contain it.  Sections in that list have to fall back to their own centroid,
    which is worth reporting because it is where a twist can creep back in.
    """
    valid = [np.asarray(h, dtype=float).reshape(-1, 2) for h in hulls
             if h is not None and len(h) >= 3]
    if not valid:
        return None, 0

    # Every section's hull is convex, so their intersection is too, and any
    # point in it is inside all of them.  When it is non-empty this is exact -
    # no section has to fall back to a centre of its own.
    region = common_interior(valid)
    if region is not None and len(region) >= 3:
        return region.mean(axis=0), 0

    centroids = [h.mean(axis=0) for h in valid]
    candidates = [np.mean(centroids, axis=0)] + centroids

    best = None
    best_hits = -1
    for candidate in candidates:
        hits = sum(1 for hull in valid if _point_in_polygon(candidate, hull))
        if hits > best_hits:
            best, best_hits = candidate, hits
        if hits == len(valid):
            break
    return best, len(valid) - best_hits


def clamp_into(point, hull, margin=0.05):
    """Nearest point inside ``hull`` to ``point``, stepped in by ``margin``.

    Better than falling back to the hull's centroid.  The centroid of one
    section sits somewhere quite different from the centroid of the next, and
    since the profiles are radial about it, sections measured from different
    centres carry different angles for the same feature.  A smooth loft across
    that mismatch grows a fin - which is exactly what the test mesh did, at the
    three sections that did not contain the shared axis.  Sliding the shared
    axis just inside the hull keeps every section's angular reference as close
    to the others as the geometry allows.
    """
    hull = np.asarray(hull, dtype=float).reshape(-1, 2)
    point = np.asarray(point, dtype=float).reshape(2)
    if _point_in_polygon(point, hull):
        return point

    centroid = hull.mean(axis=0)
    best = centroid
    best_distance = np.inf
    for i in range(len(hull)):
        a = hull[i]
        b = hull[(i + 1) % len(hull)]
        edge = b - a
        length = float(np.dot(edge, edge))
        t = 0.0 if length <= 0 else np.clip(np.dot(point - a, edge) / length,
                                            0.0, 1.0)
        candidate = a + t * edge
        distance = float(np.linalg.norm(candidate - point))
        if distance < best_distance:
            best, best_distance = candidate, distance

    inward = best + (centroid - best) * float(margin)
    return inward if _point_in_polygon(inward, hull) else centroid


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


def envelope_contour(contours, base, normal, samples=DEFAULT_SAMPLES,
                     centre=None, smoothing=0, clearance=0.0, convex=False,
                     collapse_factor=0.5):
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
                          collapse_factor=collapse_factor, clearance=clearance,
                          convex=convex)
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
