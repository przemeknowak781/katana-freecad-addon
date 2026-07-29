"""Contour housekeeping: closing, filtering, orientation, seam placement.

A contour is an ``(n, 3)`` float array of *unique* points plus a ``closed``
flag - the closing point is never stored twice.  Rotating the seam is then a
plain ``np.roll`` and every downstream length/area formula has one obvious form.
"""

import numpy as np

from .planes import orthonormal_frame

SEAM_NONE = "None"
SEAM_AXIS = "Axis"
SEAM_GUIDE = "Guide"
SEAM_MIN_TRAVEL = "MinTravel"

SEAM_MODES = (SEAM_NONE, SEAM_AXIS, SEAM_GUIDE, SEAM_MIN_TRAVEL)


def as_points(points):
    return np.asarray(points, dtype=float).reshape(-1, 3)


def close_contour(points, close_tolerance):
    """Normalise a raw cross-section polyline.

    Returns ``(unique_points, closed, discarded)``.

    Two cases are handled, and the second one is not academic.  Normally
    ``crossSections`` closes a contour by repeating the first point at the end,
    and all this does is strip that repeat.  But when the cutting plane lies
    exactly in a ring of coplanar mesh edges - a sphere cut at its equator, a
    revolved part cut through a vertex row - OCC walks the ring twice, out and
    back, and hands back a polyline of double length whose signed area is zero.
    Left alone it produces a zero-area "contour" that silently breaks the
    orientation test and then the loft.

    So the closing point is located as the *first* return to the start, not the
    last, and everything after it is dropped and reported.
    """
    pts = as_points(points)
    if len(pts) < 2:
        return pts, False, 0

    tol = float(close_tolerance)
    distances = np.linalg.norm(pts[2:] - pts[0], axis=1)
    hits = np.where(distances <= tol)[0]
    if len(hits):
        k = int(hits[0]) + 2
        return pts[:k], True, len(pts) - k

    if float(np.linalg.norm(pts[-1] - pts[0])) <= tol:
        return pts[:-1], True, 1
    return pts, False, 0


def drop_duplicates(points, tolerance):
    """Remove consecutive points closer than ``tolerance``.

    Zero-length segments are the single most common cause of an ``OCCError``
    out of the approximation, and cross-sections of a mesh produce them wherever
    the plane passes through a vertex.
    """
    pts = as_points(points)
    if len(pts) < 2:
        return pts
    keep = [0]
    for i in range(1, len(pts)):
        if np.linalg.norm(pts[i] - pts[keep[-1]]) > tolerance:
            keep.append(i)
    return pts[keep]


def contour_length(points, closed=False):
    pts = as_points(points)
    if len(pts) < 2:
        return 0.0
    seg = np.diff(pts, axis=0)
    total = float(np.linalg.norm(seg, axis=1).sum())
    if closed:
        total += float(np.linalg.norm(pts[0] - pts[-1]))
    return total


def to_plane_coords(points, origin, u, v):
    """Project 3D points onto the plane frame; returns an ``(n, 2)`` array."""
    pts = as_points(points) - np.asarray(origin, dtype=float).reshape(3)
    return np.column_stack((pts @ u, pts @ v))


def signed_area(points_2d):
    """Shoelace area.  Positive means counter-clockwise in the ``(u, v)`` frame,
    i.e. the contour runs right-handed about the plane normal."""
    p = np.asarray(points_2d, dtype=float).reshape(-1, 2)
    if len(p) < 3:
        return 0.0
    x, y = p[:, 0], p[:, 1]
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))


def unify_orientation(points, normal, origin=None):
    """Return ``(points, reversed_flag)`` with the contour running CCW about
    ``normal``.

    Without this, adjacent sections whose triangulation happened to produce
    opposite winding make the loft twist through 180 degrees between them.
    """
    pts = as_points(points)
    if len(pts) < 3:
        return pts, False
    u, v, _ = orthonormal_frame(normal)
    base = pts[0] if origin is None else origin
    if signed_area(to_plane_coords(pts, base, u, v)) < 0.0:
        return pts[::-1].copy(), True
    return pts, False


def polygon_normal(points):
    """Best-fit plane normal of a closed polyline, by Newell's method.

    Robust to non-planarity and to collinear runs, unlike a cross product of two
    edges.  The sign follows the winding, so this tells you the plane of a
    contour but never on its own which way round it goes.
    """
    pts = as_points(points)
    if len(pts) < 3:
        return np.zeros(3)
    nxt = np.roll(pts, -1, axis=0)
    normal = np.array([
        np.sum((pts[:, 1] - nxt[:, 1]) * (pts[:, 2] + nxt[:, 2])),
        np.sum((pts[:, 2] - nxt[:, 2]) * (pts[:, 0] + nxt[:, 0])),
        np.sum((pts[:, 0] - nxt[:, 0]) * (pts[:, 1] + nxt[:, 1]))])
    length = np.linalg.norm(normal)
    return normal / length if length > 1e-12 else np.zeros(3)


def centroid(points):
    return as_points(points).mean(axis=0)


def _point_polyline_distance(point, polyline):
    """Shortest distance from a point to an open polyline."""
    poly = as_points(polyline)
    if len(poly) == 1:
        return float(np.linalg.norm(point - poly[0]))
    a = poly[:-1]
    b = poly[1:]
    ab = b - a
    denom = np.einsum("ij,ij->i", ab, ab)
    denom[denom < 1e-24] = 1e-24
    t = np.clip(np.einsum("ij,ij->i", point - a, ab) / denom, 0.0, 1.0)
    proj = a + t[:, None] * ab
    return float(np.linalg.norm(proj - point, axis=1).min())


def seam_index(points, mode=SEAM_AXIS, axis=(1.0, 0.0, 0.0), guide=None,
               previous_start=None):
    """Index of the point that should become the start of the contour."""
    pts = as_points(points)
    if len(pts) < 2 or mode == SEAM_NONE:
        return 0

    if mode == SEAM_AXIS:
        a = np.asarray(axis, dtype=float).reshape(3)
        n = np.linalg.norm(a)
        if n < 1e-12:
            return 0
        return int(np.argmax((pts - pts.mean(axis=0)) @ (a / n)))

    if mode == SEAM_GUIDE:
        if guide is None:
            raise ValueError("seam mode 'Guide' requires a guide polyline")
        g = as_points(guide)
        return int(np.argmin([_point_polyline_distance(p, g) for p in pts]))

    if mode == SEAM_MIN_TRAVEL:
        if previous_start is None:
            # First section of the chain: nothing to align to yet.  Fall back to
            # the axis rule so the chain has a deterministic anchor.
            return seam_index(pts, SEAM_AXIS, axis)
        prev = np.asarray(previous_start, dtype=float).reshape(3)
        return int(np.argmin(np.linalg.norm(pts - prev, axis=1)))

    raise ValueError("unknown seam mode %r" % (mode,))


def rotate_to_seam(points, index):
    """Cyclically rotate a closed contour so that ``index`` becomes point 0."""
    pts = as_points(points)
    if index % len(pts) == 0:
        return pts
    return np.roll(pts, -int(index) % len(pts), axis=0)


def apply_seam(points, closed, mode=SEAM_AXIS, axis=(1.0, 0.0, 0.0), guide=None,
               previous_start=None):
    """Place the seam.  Open contours are returned untouched - their start is
    dictated by the geometry, not by us."""
    pts = as_points(points)
    if not closed or mode == SEAM_NONE or len(pts) < 3:
        return pts
    return rotate_to_seam(pts, seam_index(pts, mode, axis, guide, previous_start))
