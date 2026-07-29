"""Polyline reduction, corner detection and deviation measurement.

Pure numpy.  Everything here operates on ``(n, 3)`` arrays of unique points plus
a ``closed`` flag, matching :mod:`.contours`.
"""

import numpy as np

from .contours import as_points


def _perpendicular_distances(points, start, end):
    """Distance from each point to the infinite line ``start``-``end``."""
    d = end - start
    length = float(np.linalg.norm(d))
    if length < 1e-12:
        return np.linalg.norm(points - start, axis=1)
    return np.linalg.norm(np.cross(points - start, d), axis=1) / length


def douglas_peucker(points, tolerance, closed=False):
    """Ramer-Douglas-Peucker reduction.

    The point of decimating before approximation is not cosmetic: a dense,
    noisy point set makes the least-squares system in ``approximate()``
    ill-conditioned and the result comes out wavy.

    For a closed contour the reduction runs on the sequence
    ``points + points[0]``, which pins the seam - so decimation never moves it.
    """
    pts = as_points(points)
    tol = float(tolerance)
    if tol <= 0.0 or len(pts) < 3:
        return pts

    work = np.vstack([pts, pts[0]]) if closed else pts
    n = len(work)
    keep = np.zeros(n, dtype=bool)
    keep[0] = keep[-1] = True

    stack = [(0, n - 1)]
    while stack:
        i0, i1 = stack.pop()
        if i1 <= i0 + 1:
            continue
        dist = _perpendicular_distances(work[i0 + 1:i1], work[i0], work[i1])
        if len(dist) == 0:
            continue
        k = int(np.argmax(dist))
        if dist[k] > tol:
            idx = i0 + 1 + k
            keep[idx] = True
            stack.append((i0, idx))
            stack.append((idx, i1))

    result = work[keep]
    if closed:
        result = result[:-1]
        # A loop that collapses below three points is degenerate; keep the
        # original rather than hand a two-point "contour" to the fitter.
        if len(result) < 3:
            return pts
    return result


def turn_angles(points, closed=False):
    """Turn angle in radians at each vertex.

    Open contours get ``nan`` at the two endpoints, so index arithmetic stays
    aligned with ``points``.
    """
    pts = as_points(points)
    n = len(pts)
    angles = np.full(n, np.nan)
    if n < 3:
        return angles

    idx = range(n) if closed else range(1, n - 1)
    for i in idx:
        a = pts[i] - pts[(i - 1) % n]
        b = pts[(i + 1) % n] - pts[i]
        na = np.linalg.norm(a)
        nb = np.linalg.norm(b)
        if na < 1e-12 or nb < 1e-12:
            angles[i] = 0.0
            continue
        cos = float(np.clip(np.dot(a, b) / (na * nb), -1.0, 1.0))
        angles[i] = np.arccos(cos)
    return angles


def detect_corners(points, angle_threshold, closed=False):
    """Indices of vertices whose turn angle exceeds ``angle_threshold`` (rad).

    Run this on an already-decimated polyline.  On raw cross-section output the
    per-triangle jitter routinely exceeds any threshold low enough to catch a
    real chamfer, and you get a corner every few points on a smooth fillet.
    """
    angles = turn_angles(points, closed)
    thr = float(angle_threshold)
    return [int(i) for i in np.where(np.nan_to_num(angles, nan=-1.0) > thr)[0]]


def strongest_corners(points, corners, limit, closed=False):
    """Keep only the ``limit`` sharpest of the given corners."""
    if limit is None or limit <= 0 or len(corners) <= limit:
        return list(corners)
    angles = turn_angles(points, closed)
    ranked = sorted(corners, key=lambda i: angles[i], reverse=True)
    return sorted(ranked[:int(limit)])


def _cumulative_length(points, closed):
    pts = as_points(points)
    steps = np.linalg.norm(np.diff(pts, axis=0), axis=1)
    if closed:
        steps = np.append(steps, np.linalg.norm(pts[0] - pts[-1]))
    return np.concatenate([[0.0], np.cumsum(steps)])


def pad_split_points(points, corners, target, closed=False):
    """Extend a set of split points to exactly ``target`` entries.

    Returns ``(points, splits)`` - the points come back because reaching the
    target usually means **inserting** vertices, not just choosing among the
    ones already there.  After decimation a rectangular contour is four points
    long, so picking existing vertices caps the split count at four however many
    the chain needs.  The inserted points lie exactly on the polyline, so they
    change the geometry not at all.

    Every detected corner is kept; the extra splits land at the arc-length
    midpoint of the longest remaining stretch.  That is what makes a chain
    loftable without giving anything up: a loft needs the same number of edges
    in every section, and a section that genuinely has fewer corners than its
    neighbours gets the difference made up at corresponding places rather than
    losing the corners it does have.
    """
    pts = as_points(points).copy()
    target = int(target)
    splits = sorted({int(i) % len(pts) for i in corners})
    if len(pts) < 3 or target <= len(splits):
        return pts, splits
    if not splits:
        splits = [0]

    while len(splits) < target:
        lengths = _cumulative_length(pts, closed)
        total = float(lengths[-1])
        if total <= 0.0:
            break

        # Longest stretch between consecutive splits, wrapping when closed.
        gaps = []
        for k, start in enumerate(splits):
            if k + 1 < len(splits):
                gaps.append((lengths[splits[k + 1]] - lengths[start], start))
            elif closed:
                gaps.append((total - lengths[start] + lengths[splits[0]], start))
        if not gaps:
            break
        span, start = max(gaps)
        if span <= 1e-12:
            break

        midpoint = lengths[start] + span / 2.0
        segment = int(np.searchsorted(lengths, midpoint, side="right") - 1)
        segment = max(0, min(segment, len(pts) - 1))
        step = lengths[segment + 1] - lengths[segment]
        ratio = 0.5 if step <= 1e-12 else (midpoint - lengths[segment]) / step
        following = (segment + 1) % len(pts)
        new_point = pts[segment] + ratio * (pts[following] - pts[segment])

        insert_at = segment + 1
        pts = np.insert(pts, insert_at, new_point, axis=0)
        splits = [i + 1 if i >= insert_at else i for i in splits]
        splits.append(insert_at)
        splits.sort()
    return pts, splits


def split_at_corners(points, corners, closed=False):
    """Split a contour into segments at the given vertex indices.

    Each segment shares its end vertices with its neighbours, so reassembling
    them gives back a continuous wire (C0 at the corners, which is the point).
    Returns a list of ``(m, 3)`` arrays; a contour with no corners comes back as
    a single segment.
    """
    pts = as_points(points)
    n = len(pts)
    idx = sorted({int(i) % n for i in corners})
    if not idx or n < 3:
        return [pts]

    if closed:
        segments = []
        for k, start in enumerate(idx):
            end = idx[(k + 1) % len(idx)]
            if end > start:
                seg = pts[start:end + 1]
            else:  # wraps past the seam
                seg = np.vstack([pts[start:], pts[:end + 1]])
            if len(seg) >= 2:
                segments.append(seg)
        return segments or [pts]

    bounds = [0] + [i for i in idx if 0 < i < n - 1] + [n - 1]
    segments = [pts[bounds[k]:bounds[k + 1] + 1] for k in range(len(bounds) - 1)]
    return [s for s in segments if len(s) >= 2] or [pts]


def common_corner_indices(profiles, angle_threshold, min_fraction=0.35,
                          tolerance=2, separation=3):
    """Corner positions shared by a family of equally-sampled profiles.

    Envelope sections all carry the same number of points in the same angular
    order, so a corner can be named by its sample index and compared across
    sections.  That is what makes creases survivable: split every section at the
    *same* indices and the loft joins edge to edge instead of running a smooth
    surface past a sharp feature.

    A corner drifts by a sample or two between neighbouring sections, so votes
    are pooled over a small window, and only positions that a decent fraction of
    the family agrees on are kept - one section's triangulation noise should not
    put an edge into every other section.
    """
    profiles = [as_points(p) for p in profiles]
    if not profiles:
        return []
    size = len(profiles[0])
    if size < 8 or any(len(p) != size for p in profiles):
        return []

    # One vote per section per neighbourhood, not one per sharp vertex: a single
    # bump in a profile turns sharply on the way in, at the tip and on the way
    # out, and counting those separately let one section out-vote the threshold
    # on its own.
    pooled = np.zeros(size)
    for points in profiles:
        seen = np.zeros(size, dtype=bool)
        for index in detect_corners(points, angle_threshold, closed=True):
            for offset in range(-int(tolerance), int(tolerance) + 1):
                seen[(index + offset) % size] = True
        pooled += seen

    needed = max(1.0, min_fraction * len(profiles))
    flagged = pooled >= needed
    if not np.any(flagged):
        return []

    # A corner in a radial profile registers on *both* sides of the peak - the
    # polyline turns sharply going in and again coming out - so the raw hits
    # straddle the feature rather than landing on it.  Neighbouring hits are
    # therefore grouped and each group contributes one split, at its
    # highest-voted index.
    groups = []
    current = []
    for index in range(size):
        if flagged[index]:
            current.append(index)
        elif current:
            groups.append(current)
            current = []
    if current:
        if groups and flagged[0] and (size - current[-1]) <= 1:
            groups[0] = current + groups[0]      # the run wraps past zero
        else:
            groups.append(current)

    chosen = []
    for group in groups:
        # Pooling makes the whole neighbourhood tie, so the middle of the tied
        # run is the honest position - taking the first would put every split a
        # sample early.
        peak = max(pooled[i % size] for i in group)
        tied = [i for i in group if pooled[i % size] >= peak - 1e-9]
        best = tied[len(tied) // 2]
        if all(min(abs(best - k), size - abs(best - k)) >= separation
               for k in chosen):
            chosen.append(best % size)
    return sorted(chosen)


def merge_adjacent(indices, size, separation=2):
    """Collapse runs of neighbouring indices to one entry each.

    A corner in a radial profile registers on both sides of the peak - the
    polyline turns sharply going in and again coming out - so a single feature
    arrives as two or three indices.  Left alone they seed two or three separate
    creases at the same place.
    """
    marks = sorted({int(i) % int(size) for i in indices})
    if len(marks) < 2:
        return marks

    groups = [[marks[0]]]
    for index in marks[1:]:
        if index - groups[-1][-1] <= separation:
            groups[-1].append(index)
        else:
            groups.append([index])
    # The first and last runs may be the same feature seen either side of zero.
    if len(groups) > 1 and (size - groups[-1][-1] + groups[0][0]) <= separation:
        groups[0] = groups.pop() + groups[0]
    return sorted(group[len(group) // 2] % int(size) for group in groups)


def track_corner_lines(profiles, angle_threshold, window=4, min_fraction=0.35):
    """Follow creases along a family of equally-sampled profiles.

    Returns one list of split indices per profile, all the same length, or an
    empty list when nothing is worth splitting at.

    A crease on a curved part does not sit at a fixed angle: it drifts by a
    sample or two per section as the surface turns.  Pinning every section to
    one index loses it - measured on the test mesh at 30 sections, the shared
    index found two corners where the sections themselves turn through 178
    degrees.  So each crease is tracked instead: found in one section, then
    followed into its neighbours within ``window`` samples, and carried straight
    through sections that do not see it rather than being abandoned.

    A line that only a ``min_fraction`` of the family ever supports is dropped -
    that is one section's triangulation noise, not a feature.
    """
    profiles = [as_points(p) for p in profiles]
    if len(profiles) < 2:
        return []
    size = len(profiles[0])
    if size < 8 or any(len(p) != size for p in profiles):
        return []

    detected = [merge_adjacent(detect_corners(p, angle_threshold, closed=True),
                               size)
                for p in profiles]
    if not any(detected):
        return []

    anchor = max(range(len(profiles)), key=lambda i: len(detected[i]))
    lines = [{"index": {anchor: i}, "support": 1} for i in detected[anchor]]
    if not lines:
        return []

    def follow(order):
        for previous, current in order:
            taken = set()
            for line in lines:
                if previous not in line["index"]:
                    continue
                position = line["index"][previous]
                options = [c for c in detected[current] if c not in taken
                           and min(abs(c - position), size - abs(c - position))
                           <= window]
                if options:
                    best = min(options,
                               key=lambda c: min(abs(c - position),
                                                 size - abs(c - position)))
                    taken.add(best)
                    line["index"][current] = best
                    line["support"] += 1
                else:
                    line["index"][current] = position

    follow([(i, i - 1) for i in range(anchor, 0, -1)])
    follow([(i, i + 1) for i in range(anchor, len(profiles) - 1)])

    needed = max(1.0, min_fraction * len(profiles))
    lines = [line for line in lines if line["support"] >= needed]
    if len(lines) < 2:
        return []

    lines.sort(key=lambda line: line["index"][anchor])
    return [[line["index"][section] for line in lines]
            for section in range(len(profiles))]


def split_at_indices(points, indices, closed=True):
    """Split a contour at explicit vertex indices, keeping full resolution.

    Unlike :func:`split_at_corners` this takes the split positions as given,
    which is what lets every section of a chain break at the same angles.
    """
    pts = as_points(points)
    size = len(pts)
    marks = sorted({int(i) % size for i in indices})
    if len(marks) < 2 or size < 3:
        return [pts]

    segments = []
    for k, start in enumerate(marks):
        end = marks[(k + 1) % len(marks)]
        if end > start:
            segment = pts[start:end + 1]
        else:
            segment = np.vstack([pts[start:], pts[:end + 1]])
        if len(segment) >= 2:
            segments.append(segment)
    return segments or [pts]


def max_deviation(points, samples, closed=False):
    """Largest distance from ``points`` to the polyline through ``samples``.

    ``samples`` is the fitted curve discretised more densely than the source,
    so this measures how far the approximation strayed from the mesh data.
    """
    pts = as_points(points)
    poly = as_points(samples)
    if len(pts) == 0 or len(poly) < 2:
        return 0.0
    if closed and np.linalg.norm(poly[-1] - poly[0]) > 1e-12:
        poly = np.vstack([poly, poly[0]])

    a = poly[:-1]
    ab = poly[1:] - a
    denom = np.einsum("ij,ij->i", ab, ab)
    denom[denom < 1e-24] = 1e-24

    worst = 0.0
    for p in pts:
        t = np.clip(np.einsum("ij,ij->i", p - a, ab) / denom, 0.0, 1.0)
        d = np.linalg.norm(a + t[:, None] * ab - p, axis=1).min()
        if d > worst:
            worst = float(d)
    return worst


def median_edge_length(points, closed=False):
    """Median segment length of a polyline - the basis for the auto tolerance."""
    pts = as_points(points)
    if len(pts) < 2:
        return 0.0
    seg = np.linalg.norm(np.diff(pts, axis=0), axis=1)
    if closed:
        seg = np.append(seg, np.linalg.norm(pts[0] - pts[-1]))
    return float(np.median(seg))
