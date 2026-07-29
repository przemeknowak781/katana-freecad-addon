"""Splitting a thin-wall cross-section into its outer and inner wall.

Cutting a thin-walled part does not give a ring with a hole in it.  It gives a
*ribbon*: one closed polyline that runs out along the outer wall and back along
the inner one, measured on the test mesh at 10.33 mm2 of area for 41.6 mm of
perimeter - a wall about half a millimetre thick.

Every attempt to treat that ribbon as a section profile fails the same way.
Lofting ribbon to ribbon self-intersects; resampling it by arc length flips
between the two walls halfway round and shreds the surface; a radial envelope
throws the inner wall away along with the holes.

The part has two surfaces, so the model should have two surfaces.  This module
decides which points belong to which.
"""

import numpy as np

from .contours import as_points

DEFAULT_BINS = 360


def classify_outer(points_2d, centre, bins=DEFAULT_BINS, tolerance=0.05,
                   spread=5):
    """Boolean mask: True where the point sits on the outer wall.

    A point is on the outside when nothing *near its own direction* lies further
    out from ``centre``.  The neighbourhood is the whole trick: comparing a
    point only against its own angular bin classifies everything as outer,
    because a contour of a few hundred points leaves at most one of them per
    bin, so every point is the farthest thing in it.  ``spread`` widens the
    comparison to the bins either side, which is where the opposite wall is.

    Unlike the envelope, this keeps concavities: the comparison is local, so a
    bay in the outer wall is still outer wall.
    """
    pts = np.asarray(points_2d, dtype=float).reshape(-1, 2)
    centre = np.asarray(centre, dtype=float).reshape(2)
    if len(pts) < 3:
        return np.ones(len(pts), dtype=bool)

    offsets = pts - centre
    radii = np.linalg.norm(offsets, axis=1)
    angles = np.arctan2(offsets[:, 1], offsets[:, 0]) % (2.0 * np.pi)
    index = np.minimum((angles / (2.0 * np.pi) * bins).astype(int),
                       int(bins) - 1)

    # -inf, not nan: np.maximum propagates nan and every bin would stay empty.
    furthest = np.full(int(bins), -np.inf)
    np.maximum.at(furthest, index, radii)

    window = max(1, int(spread))
    if window > 1:
        half = window // 2
        furthest = np.vstack([np.roll(furthest, k)
                              for k in range(-half, half + 1)]).max(axis=0)

    return radii >= furthest[index] - float(tolerance)


def smooth_mask(mask, window=5):
    """Majority filter along the contour, wrapping at the ends.

    The raw decision flickers wherever the wall runs nearly tangential to the
    ray, and every flicker becomes another run: on the test mesh the unfiltered
    classification broke 20 sections into 276 fragments.  A surface needs a few
    long runs, not hundreds of short ones.
    """
    mask = np.asarray(mask, dtype=bool)
    window = int(window)
    if window < 3 or len(mask) < window:
        return mask
    if window % 2 == 0:
        window += 1
    half = window // 2
    padded = np.concatenate([mask[-half:], mask, mask[:half]])
    counts = np.convolve(padded.astype(int), np.ones(window, dtype=int),
                         mode="valid")
    return counts > half


def contiguous_runs(mask, closed=True, minimum=2):
    """Index runs where the mask holds, joined across the wrap when closed."""
    mask = np.asarray(mask, dtype=bool)
    size = len(mask)
    runs = []
    current = []
    for i in range(size):
        if mask[i]:
            current.append(i)
        elif current:
            runs.append(current)
            current = []
    if current:
        if closed and runs and mask[0]:
            runs[0] = current + runs[0]
        else:
            runs.append(current)
    return [run for run in runs if len(run) >= minimum]


def split_walls(points, centre_2d, frame, bins=DEFAULT_BINS, tolerance=0.05,
                window=5, minimum_points=4):
    """``(outer_runs, inner_runs)`` as lists of ``(m, 3)`` point arrays.

    ``frame`` is the ``(base, u, v)`` of the section plane.  The runs are open
    polylines: a wall that a slot interrupts comes back as two runs, which is
    exactly how the hole should reach the surface stage - as a gap, not as a
    separate body.
    """
    from .contours import to_plane_coords

    pts = as_points(points)
    base, u, v = frame
    flat = to_plane_coords(pts, base, u, v)

    outer = smooth_mask(classify_outer(flat, centre_2d, bins, tolerance),
                        window)
    runs = []
    for mask in (outer, ~outer):
        runs.append([pts[run] for run in
                     contiguous_runs(mask, closed=True,
                                     minimum=int(minimum_points))])
    return runs[0], runs[1]


def wall_thickness(outer_runs, inner_runs):
    """Rough wall thickness: the median distance from inner points to outer.

    A sanity number, not a measurement of the part: it says whether the split
    found two walls or cut one wall in half.
    """
    if not outer_runs or not inner_runs:
        return 0.0
    outer = np.vstack(outer_runs)
    inner = np.vstack(inner_runs)
    step = max(1, len(inner) // 200)
    distances = [np.linalg.norm(outer - point, axis=1).min()
                 for point in inner[::step]]
    return float(np.median(distances)) if distances else 0.0
