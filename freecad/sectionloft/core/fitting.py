"""B-spline approximation of contours.

Requires FreeCAD (``Part``), so this module does not import under a plain
CPython interpreter.  It calls ``Part.BSplineCurve().approximate()`` directly -
the same ``GeomAPI_PointsToBSpline`` that Curves WB wraps - rather than taking a
runtime dependency on a community addon for a one-line call.
"""

from dataclasses import dataclass, field, replace

import numpy as np

import FreeCAD as App
import Part

from . import contours as ct
from . import polyline as pl

CONTINUITIES = ("C0", "G1", "C1", "G2", "C2", "C3")


@dataclass
class FitParams:
    """Everything the fitter needs.  Mirrors the future ``FittedSections``
    properties one-to-one, so the v0.2 document object is a pure translation
    layer."""

    tolerance: float = 0.1
    degree_min: int = 3
    degree_max: int = 5
    continuity: str = "C2"
    decimate: bool = True
    decimate_factor: float = 0.25
    corner_detection: bool = True
    corner_angle: float = np.deg2rad(30.0)
    #: Exact number of pieces a contour must be split into; 0 means "however
    #: many corners there are".  Set per chain so that every section of a loft
    #: has the same number of edges - see FittedSections.common_corner_count.
    #: Too many detected corners are thinned to the sharpest, too few are padded
    #: along the arc.
    corner_target: int = 0
    seam_smoothing: bool = True

    #: A closed fit whose seam tangents differ by more than this is refitted at
    #: a tighter tolerance.  The failure mode it targets is specific: at a loose
    #: tolerance OCC returns the minimum possible number of poles (7 for a whole
    #: loop at degree 5), and a loop with two spans cannot close smoothly.
    #: Halving the tolerance once typically takes a 7 degree break to under 0.5.
    #: Fitting tighter than asked is safe - the tolerance is an upper bound on
    #: deviation, not a target.
    max_seam_kink: float = np.deg2rad(5.0)
    seam_refine_factor: float = 0.5
    seam_refine_attempts: int = 2


@dataclass
class FitResult:
    """Outcome for a single contour."""

    wire: object = None
    edges: list = field(default_factory=list)
    points: object = None          # the decimated polyline actually fitted
    closed: bool = False
    corners: list = field(default_factory=list)
    deviation: float = 0.0
    seam_kink: float = 0.0         # tangent discontinuity at the seam, radians
    seam_gap: float = 0.0          # end-to-end gap closed by enforce_closure
    tolerance_used: float = 0.0    # may be tighter than asked, see max_seam_kink
    periodic_fit: bool = False     # True if the wrap-around fit was used
    error: str = ""

    @property
    def ok(self):
        return self.wire is not None and not self.error


def to_vectors(points):
    """``(n, 3)`` array to a list of ``App.Vector``.

    FreeCAD 1.1 accepts either a vector list or a tuple list in
    ``approximate()`` (#16319 is fixed there), but 1.0 does not, and the
    conversion is free next to the fit itself.
    """
    return [App.Vector(float(p[0]), float(p[1]), float(p[2]))
            for p in np.asarray(points, dtype=float).reshape(-1, 3)]


def chord_parameters(points):
    """Normalised cumulative chord length; strictly increasing."""
    pts = np.asarray(points, dtype=float).reshape(-1, 3)
    d = np.linalg.norm(np.diff(pts, axis=0), axis=1)
    d = np.maximum(d, 1e-9)
    t = np.concatenate([[0.0], np.cumsum(d)])
    return (t / t[-1]).tolist()


def _degrees(params, n_points):
    """Clamp the degree window to something the point count can support."""
    dmax = max(1, int(params.degree_max))
    dmin = max(1, min(int(params.degree_min), dmax))
    # approximate() needs at least degree+1 points to have any freedom left.
    dmax = min(dmax, max(1, n_points - 1))
    dmin = min(dmin, dmax)
    return dmin, dmax


def approximate_segment(points, params):
    """Fit one open segment.  Returns a ``Part`` curve.

    Two points degenerate to a line: handing a 2-point set to the approximator
    works but produces a spline where a line is both exact and cheaper.
    """
    pts = np.asarray(points, dtype=float).reshape(-1, 3)
    if len(pts) < 2:
        raise ValueError("segment has fewer than 2 points")
    if len(pts) == 2:
        return Part.LineSegment(App.Vector(*pts[0]), App.Vector(*pts[1]))

    dmin, dmax = _degrees(params, len(pts))
    curve = Part.BSplineCurve()
    curve.approximate(Points=to_vectors(pts), DegMin=dmin, DegMax=dmax,
                      Tolerance=float(params.tolerance),
                      Continuity=str(params.continuity))
    return curve


def approximate_closed(points, params):
    """Fit a closed contour as a single curve.

    Two strategies, in order:

    1. *Wrap-around* (``seam_smoothing``): the point list is padded cyclically
       at both ends, fitted with explicit chord-length parameters, and then
       trimmed back to exactly one loop.  The fit therefore "sees" data on both
       sides of the seam and comes out tangent-continuous there.
    2. Plain closure: append the first point and fit.  Simple, but the seam is
       only C0 - a visible crease running down the loft.

    Returns ``(curve, periodic_fit)``.
    """
    pts = np.asarray(points, dtype=float).reshape(-1, 3)
    n = len(pts)

    if params.seam_smoothing and n >= 6:
        k = int(min(max(3, n // 8), n - 1))
        ext = np.vstack([pts[-k:], pts, pts[:k + 1]])
        try:
            knots = chord_parameters(ext)
            dmin, dmax = _degrees(params, len(ext))
            curve = Part.BSplineCurve()
            curve.approximate(Points=to_vectors(ext), Parameters=knots,
                              DegMin=dmin, DegMax=dmax,
                              Tolerance=float(params.tolerance),
                              Continuity=str(params.continuity))
            curve.segment(knots[k], knots[k + n])
            return curve, True
        except Exception:  # noqa: BLE001 - OCC failures carry no useful type
            pass

    closed_pts = np.vstack([pts, pts[0]])
    dmin, dmax = _degrees(params, len(closed_pts))
    curve = Part.BSplineCurve()
    curve.approximate(Points=to_vectors(closed_pts), DegMin=dmin, DegMax=dmax,
                      Tolerance=float(params.tolerance),
                      Continuity=str(params.continuity))
    return curve, False


def enforce_closure(curve, tolerance):
    """Snap the two ends of a nominally closed B-spline onto each other.

    Approximation leaves a sub-tolerance gap at the seam - both ends chase the
    same source point but from opposite sides - and OCC then reports the wire as
    open, which blocks ``makeLoft`` with a solid.  The fix is to move the first
    and last pole to their midpoint: a shift of half the gap, well under the
    fitting tolerance, in exchange for exact topological closure.

    Returns the gap that was closed.  A gap larger than ``tolerance`` is left
    alone - that is a genuine fitting failure, not a rounding artefact.
    """
    try:
        first = curve.getPole(1)
        last = curve.getPole(curve.NbPoles)
    except Exception:  # noqa: BLE001 - not a poled curve
        return 0.0
    gap = float((last - first).Length)
    if gap <= 1e-12 or gap > float(tolerance):
        return gap
    mid = (first + last).multiply(0.5)
    curve.setPole(1, mid)
    curve.setPole(curve.NbPoles, mid)
    return gap


def seam_kink(curve):
    """Angle between the end and start tangents of a closed curve, in radians."""
    try:
        t0 = curve.tangent(curve.FirstParameter)[0]
        t1 = curve.tangent(curve.LastParameter)[0]
    except Exception:  # noqa: BLE001
        return 0.0
    if t0.Length < 1e-12 or t1.Length < 1e-12:
        return 0.0
    cos = max(-1.0, min(1.0, t0.normalize().dot(t1.normalize())))
    return float(np.arccos(cos))


def curve_samples(edges, density):
    """Dense polyline through a list of edges, for deviation measurement."""
    out = []
    for edge in edges:
        n = max(4, int(density))
        try:
            pts = edge.discretize(Number=n)
        except Exception:  # noqa: BLE001
            continue
        out.extend([[p.x, p.y, p.z] for p in pts])
    return np.asarray(out, dtype=float).reshape(-1, 3)


def fit_closed_refined(source_points, params):
    """Fit a closed contour, tightening the tolerance while the seam kinks.

    Returns ``(curve, points, periodic, kink, gap, tolerance_used)`` for the
    best attempt - best meaning smallest seam kink, not last.
    """
    tolerance = float(params.tolerance)
    best = None
    for _ in range(max(0, int(params.seam_refine_attempts)) + 1):
        local = replace(params, tolerance=tolerance)
        pts = (pl.douglas_peucker(source_points,
                                  tolerance * params.decimate_factor, closed=True)
               if params.decimate else source_points)
        curve, periodic = approximate_closed(pts, local)
        gap = enforce_closure(curve, tolerance)
        kink = seam_kink(curve)
        if best is None or kink < best[3]:
            best = (curve, pts, periodic, kink, gap, tolerance)
        if kink <= params.max_seam_kink:
            break
        tolerance *= params.seam_refine_factor
    return best


def fit_contour(points, closed, params):
    """Fit one contour, backing off the decimation until the tolerance holds.

    Decimation is measured against the polyline, but the *curve* is not: a
    spline through sparse points overshoots between them.  Measured on a real
    section, 180 points reduced to 23 with a decimation error of 0.06 mm, and
    the curve fitted through those 23 came out 4.4 mm from the original - 18
    times the tolerance it was asked for.  Fitting the full point set gave
    0.19 mm.

    So the tolerance is treated as the promise it is: the deviation is measured
    against the *original* points, and if it is not met the fit is retried with
    less decimation and finally with none.  Never raises - a failure is reported
    through ``FitResult.error`` so one bad section cannot take down the rest.
    """
    best = None
    for attempt in decimation_backoff(params):
        result = _fit_once(points, closed, attempt)
        if best is None or (result.ok and not best.ok) or (
                result.ok and best.ok and result.deviation < best.deviation):
            best = result
        if result.ok and result.deviation <= attempt.tolerance:
            return result
    return best


def decimation_backoff(params):
    """The decimation settings to try, in order, until the tolerance holds.

    A spline through sparse points overshoots between them, so meeting the
    tolerance against the decimated polyline says nothing about the original.
    Every fitting path needs this ladder, not just the main one.
    """
    attempts = [params]
    if params.decimate and params.decimate_factor > 0.0:
        attempts.append(replace(params,
                                decimate_factor=params.decimate_factor / 4.0))
        attempts.append(replace(params, decimate=False))
    return attempts


def fit_contour_at(points, params, split_indices):
    """Fit a closed contour split at explicit vertex indices.

    Each piece is decimated and approximated on its own, so the corners stay
    exactly where they were asked for.  Used for envelope sections, where the
    split positions have to agree across the whole chain or the loft runs a
    smooth surface straight past a crease.
    """
    best = None
    for attempt in decimation_backoff(params):
        result = _fit_at_once(points, attempt, split_indices)
        if best is None or (result.ok and not best.ok) or (
                result.ok and best.ok and result.deviation < best.deviation):
            best = result
        if result.ok and result.deviation <= attempt.tolerance:
            return result
    return best


def _fit_at_once(points, params, split_indices):
    result = FitResult(closed=True)
    result.tolerance_used = float(params.tolerance)
    try:
        segments = pl.split_at_indices(points, split_indices, closed=True)
        result.corners = sorted({int(i) for i in split_indices})

        edges = []
        kept = []
        for segment in segments:
            piece = ct.drop_duplicates(segment, max(1e-7, params.tolerance * 1e-3))
            if params.decimate and len(piece) > 2:
                piece = pl.douglas_peucker(
                    piece, params.tolerance * params.decimate_factor)
            if len(piece) < 2:
                continue
            kept.append(piece)
            edges.append(approximate_segment(piece, params).toShape())

        if not edges:
            result.error = "every segment collapsed"
            return result

        result.points = np.vstack(kept)
        result.edges = edges
        result.wire = Part.Wire(edges)
        samples = curve_samples(edges, density=max(16, 4 * len(points)
                                                   // max(1, len(edges))))
        result.deviation = pl.max_deviation(ct.as_points(points), samples,
                                            closed=True)
    except Exception as exc:  # noqa: BLE001
        result.wire = None
        result.error = "%s: %s" % (type(exc).__name__, exc)
    return result


def _fit_once(points, closed, params):
    """One pass: decimate, split at corners, approximate, measure.

    Note on ordering - the spec lists corner detection before decimation.  It is
    done the other way round here on purpose: Douglas-Peucker keeps genuine
    corners (they are by definition the maximum-deviation vertices) while
    removing the per-triangle jitter that otherwise trips the angle threshold on
    every fillet.  Detecting corners first means choosing between missing real
    chamfers and inventing dozens of fake ones.
    """
    result = FitResult(closed=bool(closed))
    result.tolerance_used = float(params.tolerance)
    try:
        clean = ct.drop_duplicates(points, max(1e-7, params.tolerance * 1e-3))
        if len(clean) < 2:
            result.error = "contour collapsed to a single point"
            return result

        pts = (pl.douglas_peucker(clean, params.tolerance * params.decimate_factor,
                                  closed=closed)
               if params.decimate else clean)
        result.points = pts

        corners = (pl.detect_corners(pts, params.corner_angle, closed)
                   if params.corner_detection else [])
        if params.corner_target:
            corners = pl.strongest_corners(pts, corners, params.corner_target,
                                           closed)
            pts, corners = pl.pad_split_points(pts, corners,
                                               params.corner_target, closed)
            result.points = pts
        result.corners = corners

        if closed and not corners:
            curve, pts, periodic, kink, gap, used = fit_closed_refined(clean, params)
            result.points = pts
            result.periodic_fit = periodic
            result.seam_kink = kink
            result.seam_gap = gap
            result.tolerance_used = used
            edges = [curve.toShape()]
        else:
            segments = (pl.split_at_corners(pts, corners, closed)
                        if corners else [pts if not closed
                                         else np.vstack([pts, pts[0]])])
            edges = [approximate_segment(seg, params).toShape() for seg in segments]

        result.edges = edges
        result.wire = Part.Wire(edges)

        samples = curve_samples(edges, density=max(32, 4 * len(pts) // max(1, len(edges))))
        result.deviation = pl.max_deviation(
            ct.as_points(points), samples, closed=closed and len(edges) == 1)
    except Exception as exc:  # noqa: BLE001
        result.wire = None
        result.error = "%s: %s" % (type(exc).__name__, exc)
    return result
