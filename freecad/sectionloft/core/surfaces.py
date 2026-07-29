"""Point grids for surfacing, and the surfaces built from them.

A loft interpolates between profiles by parameter and knows nothing about what
happens in between; every failure in this project traces back to that.  A grid
says it directly: row *k* is section *k*, column *j* is a line running along the
part, and every node sits exactly on a mesh cross-section.
``BSplineSurface.interpolate`` then passes through all of them.

The grid functions are numpy only.  Building the surface needs ``Part`` and is
kept at the bottom.
"""

import numpy as np

from .contours import as_points


def resample_open(points, count):
    """``count`` points spread by arc length along an open polyline.

    Both ends are kept: they are where the wall stops, and a surface that does
    not reach them has trimmed the part.
    """
    pts = as_points(points)
    if len(pts) < 2 or count < 2:
        return None
    steps = np.linalg.norm(np.diff(pts, axis=0), axis=1)
    lengths = np.concatenate([[0.0], np.cumsum(steps)])
    total = float(lengths[-1])
    if total <= 0.0:
        return None

    out = []
    for target in np.linspace(0.0, total, int(count)):
        k = int(np.searchsorted(lengths, target, side="right") - 1)
        k = max(0, min(k, len(pts) - 2))
        span = lengths[k + 1] - lengths[k]
        t = 0.0 if span <= 0 else (target - lengths[k]) / span
        out.append(pts[k] + t * (pts[k + 1] - pts[k]))
    return np.array(out)


def align_runs(runs):
    """Flip runs so that consecutive ones start at the same end.

    Without this the grid's columns cross over between two sections and the
    surface twists through itself.  The direction a cross-section comes out in
    is an accident of the triangulation, so it has to be decided here rather
    than trusted.
    """
    aligned = [as_points(runs[0])]
    for run in runs[1:]:
        pts = as_points(run)
        previous = aligned[-1][0]
        if (np.linalg.norm(pts[0] - previous)
                > np.linalg.norm(pts[-1] - previous)):
            pts = pts[::-1]
        aligned.append(pts)
    return aligned


def grid_from_runs(runs, columns):
    """``(sections, columns, 3)`` array, or None when a run is unusable."""
    if len(runs) < 2 or columns < 2:
        return None
    rows = []
    for run in align_runs(runs):
        sampled = resample_open(run, columns)
        if sampled is None:
            return None
        rows.append(sampled)
    return np.array(rows)


def grid_quality(grid):
    """``(twist, stretch)`` - how usable a grid is before surfacing.

    ``twist`` is the worst angle, in degrees, between the step a column takes
    from one section to the next and the step its neighbour takes.

    ``stretch`` is the worst column step divided by the median one.  It is the
    measure that catches a chain linking two runs that are nowhere near each
    other: the twist can look innocent while the surface has to fly across the
    part to connect them, which is what put large flat sheets through the middle
    of the test result.  A surface between neighbouring sections should not have
    far to travel.
    """
    grid = np.asarray(grid, dtype=float)
    if grid.ndim != 3 or grid.shape[0] < 2 or grid.shape[1] < 2:
        return 0.0, 1.0

    steps = np.diff(grid, axis=0)
    lengths = np.linalg.norm(steps, axis=2)
    safe = np.where(lengths > 1e-12, lengths, 1.0)
    unit = steps / safe[:, :, None]

    dots = np.einsum("ijk,ijk->ij", unit[:, :-1], unit[:, 1:])
    twist = float(np.degrees(np.arccos(np.clip(dots.min(), -1.0, 1.0))))

    median = float(np.median(lengths))
    stretch = 1.0 if median <= 1e-12 else float(lengths.max() / median)
    return twist, stretch


def surface_from_grid(grid):
    """A B-spline surface through every node of the grid."""
    import FreeCAD as App
    import Part

    poles = [[App.Vector(*point) for point in row] for row in grid]
    surface = Part.BSplineSurface()
    surface.interpolate(poles)
    return surface


def gordon_available():
    """Is the Curves workbench, and with it a Gordon implementation, present?"""
    try:
        from freecad.Curves import gordon  # noqa: F401
    except Exception:  # noqa: BLE001
        return False
    return True


def surface_from_grid_gordon(grid, tolerance=1e-3):
    """A Gordon surface interpolating the grid's rows and columns as curves.

    Gordon interpolation treats the grid as a *curve network* - rows as
    profiles, columns as guides - rather than as a cloud of points to pass
    through, which is what it is.  Measured against plain point interpolation on
    the same input, the surfaces come out markedly tighter: 422 mm2 against 748,
    and 27 against 68.

    It does not rescue a bad network.  Given a grid whose columns disagree by
    136 degrees it returned an area of 66000 mm2 for a part 18 mm tall - so the
    quality guards still decide what is worth surfacing.

    Uses the Curves workbench, which carries the DLR TiGL implementation.  The
    caller is expected to have checked :func:`gordon_available` first.
    """
    import FreeCAD as App
    import Part
    from freecad.Curves import gordon

    profiles = []
    for row in grid:
        curve = Part.BSplineCurve()
        curve.interpolate([App.Vector(*point) for point in row])
        profiles.append(curve)

    guides = []
    for column in range(np.asarray(grid).shape[1]):
        curve = Part.BSplineCurve()
        curve.interpolate([App.Vector(*point) for point in grid[:, column]])
        guides.append(curve)

    return gordon.InterpolateCurveNetwork(profiles, guides,
                                          float(tolerance)).surface()
