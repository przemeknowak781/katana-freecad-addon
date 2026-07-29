"""Generation of the cutting plane family.

Pure numpy.  A plane is a ``(base_point, normal)`` pair of ``(3,)`` float
arrays; ``normal`` is always unit length.
"""

import math

import numpy as np

MODE_COUNT = "Count"
MODE_SPACING = "Spacing"

#: Fraction of the range that the extreme planes are pulled inwards by when the
#: range is derived automatically from a bounding box.  Cutting exactly at the
#: bbox extreme is tangent to the mesh and yields a degenerate contour (a point,
#: or nothing at all, depending on where the triangulation happens to fall).
DEFAULT_INSET = 0.02


def normalize(vector):
    """Return ``vector`` scaled to unit length.

    Raises ``ValueError`` on a null vector rather than silently producing NaN -
    a zero direction is always a user error worth reporting.
    """
    v = np.asarray(vector, dtype=float).reshape(3)
    n = float(np.linalg.norm(v))
    if n < 1e-12:
        raise ValueError("direction vector has zero length")
    return v / n


def orthonormal_frame(normal):
    """Return ``(u, v, n)``: a right-handed orthonormal frame with ``n`` along
    ``normal``.

    ``u`` is picked deterministically (from the smallest component of ``n``), so
    the same normal always yields the same frame.  That matters: contour
    orientation and seam placement are expressed in this frame and must not
    wobble between recomputes.
    """
    n = normalize(normal)
    axis = np.zeros(3)
    axis[int(np.argmin(np.abs(n)))] = 1.0
    u = np.cross(axis, n)
    u /= np.linalg.norm(u)
    v = np.cross(n, u)
    return u, v, n


def project_range(points, origin, direction):
    """Signed extent of ``points`` along ``direction``, measured from ``origin``.

    Returns ``(t_min, t_max)``.
    """
    pts = np.asarray(points, dtype=float).reshape(-1, 3)
    d = normalize(direction)
    t = (pts - np.asarray(origin, dtype=float).reshape(3)) @ d
    return float(t.min()), float(t.max())


def auto_range(bbox_min, bbox_max, origin, direction, inset=DEFAULT_INSET):
    """Range along ``direction`` covering an axis-aligned bounding box.

    The eight bbox corners are projected onto ``direction`` and the resulting
    span is shrunk by ``inset`` on each side.  With ``inset=0`` the extreme
    planes sit exactly on the silhouette.
    """
    lo = np.asarray(bbox_min, dtype=float).reshape(3)
    hi = np.asarray(bbox_max, dtype=float).reshape(3)
    corners = np.array([[x, y, z] for x in (lo[0], hi[0])
                        for y in (lo[1], hi[1])
                        for z in (lo[2], hi[2])], dtype=float)
    t0, t1 = project_range(corners, origin, direction)
    margin = (t1 - t0) * float(inset)
    return t0 + margin, t1 - margin


def longest_bbox_axis(bbox_min, bbox_max):
    """Unit vector along the longest side of a bounding box.

    The default slicing direction for someone who has not thought about it yet.
    Enclosures are usually longer than they are wide, and sections perpendicular
    to the long axis are almost always the ones you want; getting this right by
    default removes the first question the tool would otherwise have to ask.
    """
    lo = np.asarray(bbox_min, dtype=float).reshape(3)
    hi = np.asarray(bbox_max, dtype=float).reshape(3)
    axis = np.zeros(3)
    axis[int(np.argmax(hi - lo))] = 1.0
    return axis


def auto_range_from_points(points, origin, direction, inset=DEFAULT_INSET):
    """Range along ``direction`` covering an actual point cloud.

    Preferred over :func:`auto_range` whenever the mesh vertices are at hand.
    The bbox-corner version overestimates the extent for any direction that is
    not axis-aligned - a sphere of radius 50 sliced along (1,1,1) gets a range
    of +/-86 instead of +/-50, and the outer planes miss the mesh entirely.
    """
    pts = np.asarray(points, dtype=float).reshape(-1, 3)
    t0, t1 = project_range(pts, origin, direction)
    margin = (t1 - t0) * float(inset)
    return t0 + margin, t1 - margin


def plane_positions(mode, range_start, range_end, count=None, spacing=None):
    """Offsets of the planes along the direction, as a list of floats.

    ``Count`` distributes ``count`` planes inclusively over the range; a single
    plane lands in the middle.  ``Spacing`` walks from ``range_start`` in steps
    of ``spacing`` and stops at ``range_end`` - the last step is kept only if it
    fits, so the actual span may be shorter than requested.
    """
    t0 = float(range_start)
    t1 = float(range_end)
    if t1 < t0:
        t0, t1 = t1, t0
    span = t1 - t0

    if mode == MODE_COUNT:
        if count is None:
            raise ValueError("mode 'Count' requires count")
        n = int(count)
        if n < 1:
            raise ValueError("count must be >= 1")
        if n == 1:
            return [t0 + span / 2.0]
        step = span / (n - 1)
        return [t0 + i * step for i in range(n)]

    if mode == MODE_SPACING:
        if spacing is None:
            raise ValueError("mode 'Spacing' requires spacing")
        s = float(spacing)
        if s <= 0.0:
            raise ValueError("spacing must be > 0")
        # Guard against a spacing so small it would produce a runaway list.
        n = int(math.floor(span / s + 1e-9)) + 1
        return [t0 + i * s for i in range(n)]

    raise ValueError("unknown mode %r" % (mode,))


def avoid_vertex_rows(positions, vertex_offsets, epsilon, nudge, row_threshold=3):
    """Shift plane offsets off rows of coplanar mesh vertices.

    A plane that lands exactly in a ring of mesh vertices - a sphere cut at its
    equator, any revolved part cut through a vertex row - makes
    ``crossSections`` walk the ring out and back instead of round, producing a
    zero-area polyline that is useless as a section.  There is no repairing that
    after the fact; the cure is not to ask the question, so such planes are
    moved by ``nudge``, which is orders of magnitude below any tolerance the
    user cares about.

    ``vertex_offsets`` are the mesh vertices projected onto the direction.
    """
    offsets = np.asarray(vertex_offsets, dtype=float).ravel()
    out = []
    for t in positions:
        for attempt in range(4):
            hits = np.count_nonzero(np.abs(offsets - t) <= epsilon)
            if hits < row_threshold:
                break
            # Alternate sides and grow, so a nudge never walks off the range.
            t += nudge * (1 if attempt % 2 == 0 else -(attempt + 1))
        out.append(float(t))
    return out


def plane_family(origin, direction, mode=MODE_COUNT, count=12, spacing=None,
                 range_start=None, range_end=None):
    """Build the ``(base_point, normal)`` list.

    ``range_start`` / ``range_end`` are offsets along ``direction`` relative to
    ``origin``.  When omitted they default to a symmetric range of length
    ``count * spacing`` (Spacing mode) or to +/-0.5 (Count mode) - callers that
    have a mesh should always pass a range from :func:`auto_range` instead.
    """
    o = np.asarray(origin, dtype=float).reshape(3)
    d = normalize(direction)

    if range_start is None or range_end is None:
        if mode == MODE_SPACING and spacing:
            half = 0.5 * float(spacing) * max(int(count or 2) - 1, 1)
        else:
            half = 0.5
        range_start = -half if range_start is None else range_start
        range_end = half if range_end is None else range_end

    return [(o + t * d, d.copy())
            for t in plane_positions(mode, range_start, range_end, count, spacing)]
