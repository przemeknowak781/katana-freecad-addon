"""Performance budget check from the spec, section 7.3.

    freecadcmd bench.py

Target: 50 000 triangles, 30 sections, whole chain under 5 seconds.
"""

import os
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
if "freecad" in sys.modules:
    _pkg = os.path.join(ROOT, "freecad")
    if _pkg not in list(sys.modules["freecad"].__path__):
        sys.modules["freecad"].__path__.append(_pkg)

import Mesh  # noqa: E402

from freecad.sectionloft.core import pipeline as pp  # noqa: E402
from freecad.sectionloft.core.fitting import FitParams  # noqa: E402


def timed(label, fn):
    start = time.perf_counter()
    value = fn()
    elapsed = time.perf_counter() - start
    print("%-28s %7.3f s" % (label, elapsed))
    return value, elapsed


def main():
    mesh = Mesh.createSphere(50.0, 160)
    print("mesh facets: %d" % mesh.CountFacets)

    params = pp.SliceParams(count=30, inset=0.03)
    fit = FitParams()

    total = 0.0
    _, t = timed("median edge length", lambda: pp.mesh_median_edge_length(mesh))
    total += t
    fit.tolerance = pp.auto_tolerance(mesh, params.tolerance_factor)
    params.auto_tolerance = False

    sections, t = timed("slice", lambda: pp.slice_mesh(mesh, params))
    total += t
    fits, t = timed("fit", lambda: pp.fit_sections(sections, fit))
    total += t
    _, t = timed("compound", lambda: pp.sections_to_shape(sections))
    total += t

    points = sum(len(c.points) for s in sections for c in s.contours)
    print("-" * 40)
    print("sections %d, contour points %d, tolerance %.3f mm"
          % (len(sections), points, fit.tolerance))
    print("max deviation %.4f mm"
          % max(f.deviation for f in fits if f is not None))
    print("TOTAL %.3f s (budget 5.000 s) -> %s"
          % (total, "OK" if total < 5.0 else "OVER BUDGET"))


main()
