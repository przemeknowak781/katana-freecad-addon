"""B-spline approximation.  Requires FreeCAD - run with freecadcmd."""

import unittest

import numpy as np

import FreeCAD as App
import Part

from freecad.sectionloft.core import fitting as ft
from freecad.sectionloft.core.fitting import FitParams, fit_contour
from freecad.sectionloft.tests import fixtures as fx


class TestFitCircle(unittest.TestCase):
    """The v0.1 question in one test: does approximation of noisy section data
    give a curve worth lofting?"""

    def setUp(self):
        self.radius = 25.0
        self.tolerance = 0.2
        self.points = fx.circle(radius=self.radius, n=180, noise=0.05, seed=11)
        self.params = FitParams(tolerance=self.tolerance)
        self.fit = fit_contour(self.points, True, self.params)

    def test_succeeds(self):
        self.assertTrue(self.fit.ok, self.fit.error)
        self.assertIsInstance(self.fit.wire, Part.Wire)

    def test_deviation_within_tolerance(self):
        self.assertLess(self.fit.deviation, self.tolerance)

    def test_matches_the_analytic_circle(self):
        pts = np.array([[p.x, p.y, p.z] for p in
                        self.fit.wire.discretize(Number=500)])
        radii = np.linalg.norm(pts[:, :2], axis=1)
        self.assertLess(np.abs(radii - self.radius).max(), self.tolerance / 2.0)

    def test_noise_is_smoothed_not_traced(self):
        """The fit must use far fewer poles than there were input points -
        otherwise it is interpolating the triangulation noise."""
        self.assertLess(len(self.fit.points), len(self.points) / 2)

    def test_seam_is_tangent_continuous(self):
        self.assertTrue(self.fit.periodic_fit)
        self.assertLess(np.rad2deg(self.fit.seam_kink), 2.0)

    def test_wire_is_closed(self):
        self.assertTrue(self.fit.wire.isClosed())


class TestSeamSmoothing(unittest.TestCase):
    def test_plain_closure_leaves_a_kink_that_smoothing_removes(self):
        pts = fx.circle(radius=25.0, n=120, noise=0.05, seed=5)
        plain = fit_contour(pts, True, FitParams(tolerance=0.2,
                                                 seam_smoothing=False))
        smooth = fit_contour(pts, True, FitParams(tolerance=0.2,
                                                  seam_smoothing=True))
        self.assertTrue(plain.ok and smooth.ok)
        self.assertLessEqual(smooth.seam_kink, plain.seam_kink + 1e-9)


class TestSeamRefinement(unittest.TestCase):
    """At a loose tolerance OCC returns the minimum pole count and the loop
    cannot close smoothly; the refit is what keeps the seam usable."""

    def setUp(self):
        # 1.5 mm on a 50 mm radius is loose enough to trigger the collapse.
        self.points = fx.circle(radius=50.0, n=64)
        self.loose = dict(tolerance=1.5, decimate=True)

    def test_refinement_removes_the_kink(self):
        without = fit_contour(self.points, True,
                              FitParams(seam_refine_attempts=0, **self.loose))
        with_ = fit_contour(self.points, True,
                            FitParams(seam_refine_attempts=2, **self.loose))
        self.assertTrue(without.ok and with_.ok)
        self.assertGreater(np.rad2deg(without.seam_kink), 3.0)
        self.assertLess(np.rad2deg(with_.seam_kink), 3.0)

    def test_refinement_reports_the_tolerance_it_used(self):
        fit = fit_contour(self.points, True,
                          FitParams(seam_refine_attempts=2, **self.loose))
        self.assertLess(fit.tolerance_used, 1.5)
        self.assertLessEqual(fit.deviation, 1.5)

    def test_a_good_fit_is_not_refined(self):
        fit = fit_contour(fx.circle(radius=50.0, n=200), True,
                          FitParams(tolerance=0.05))
        self.assertAlmostEqual(fit.tolerance_used, 0.05)


class TestExactFitting(unittest.TestCase):
    """Interpolate mode: the section itself is the deliverable."""

    def params(self, **kwargs):
        settings = dict(tolerance=0.2, method="Interpolate")
        settings.update(kwargs)
        return FitParams(**settings)

    def test_passes_through_the_points(self):
        points = fx.circle(radius=25.0, n=60)
        fit = fit_contour(points, True, self.params())
        self.assertTrue(fit.ok, fit.error)
        for point in points:
            self.assertLess(
                Part.Vertex(App.Vector(*point)).distToShape(fit.wire)[0], 1e-6)

    def test_a_closed_contour_comes_back_closed(self):
        fit = fit_contour(fx.circle(radius=10.0, n=40), True, self.params())
        self.assertTrue(fit.wire.isClosed())

    def test_corners_are_kept(self):
        fit = fit_contour(fx.rectangle(40, 30, 12), True, self.params())
        self.assertTrue(fit.ok, fit.error)
        self.assertEqual(len(fit.corners), 4)
        self.assertLess(fit.deviation, 1e-6)

    def test_a_short_run_is_drawn_straight(self):
        """A spline through three or four points has room to bulge between
        them; straight edges through the same points cannot."""
        points = np.array([[0, 0, 0], [1, 0.02, 0], [2, 0, 0], [3, 0.02, 0]],
                          dtype=float)
        edges = ft.interpolate_segment(points, tolerance=0.001)
        self.assertEqual(len(edges), 3)
        for edge in edges:
            self.assertIsInstance(edge.Curve, Part.Line)

    def test_a_bulging_spline_falls_back_to_straight_edges(self):
        points = np.array([[0, 0, 0], [1, 0, 0], [2, 0, 0], [3, 0, 0],
                           [4, 0, 0], [5, 3, 0]], dtype=float)
        loose = ft.interpolate_segment(points, tolerance=10.0)
        strict = ft.interpolate_segment(points, tolerance=1e-6)
        self.assertEqual(len(loose), 1)
        self.assertEqual(len(strict), len(points) - 1)

    def test_beats_approximation_on_fidelity(self):
        points = fx.rectangle(40, 30, 20)
        exact = fit_contour(points, True, self.params())
        smooth = fit_contour(points, True, FitParams(tolerance=0.2))
        self.assertLess(exact.deviation, smooth.deviation + 1e-9)


class TestFitCorners(unittest.TestCase):
    def test_rectangle_fits_as_four_edges(self):
        pts = fx.rectangle(width=40.0, height=20.0, per_side=15)
        fit = fit_contour(pts, True, FitParams(tolerance=0.1))
        self.assertTrue(fit.ok, fit.error)
        self.assertEqual(len(fit.corners), 4)
        self.assertEqual(len(fit.edges), 4)
        self.assertLess(fit.deviation, 0.1)

    def test_corner_detection_off_gives_one_edge(self):
        pts = fx.rectangle(per_side=15)
        fit = fit_contour(pts, True, FitParams(tolerance=0.1,
                                               corner_detection=False))
        self.assertTrue(fit.ok, fit.error)
        self.assertEqual(len(fit.edges), 1)


class TestOpenContour(unittest.TestCase):
    def test_open_arc(self):
        pts = fx.circle(radius=25.0, n=90)[:45]
        fit = fit_contour(pts, False, FitParams(tolerance=0.1))
        self.assertTrue(fit.ok, fit.error)
        self.assertFalse(fit.wire.isClosed())
        self.assertLess(fit.deviation, 0.1)


class TestDegenerateInput(unittest.TestCase):
    def test_single_point_reports_error_without_raising(self):
        fit = fit_contour(np.zeros((1, 3)), True, FitParams())
        self.assertFalse(fit.ok)
        self.assertTrue(fit.error)

    def test_coincident_points_report_error_without_raising(self):
        fit = fit_contour(np.zeros((10, 3)), True, FitParams())
        self.assertFalse(fit.ok)
        self.assertTrue(fit.error)

    def test_two_points_fit_as_a_line(self):
        pts = np.array([[0, 0, 0], [10, 0, 0]], dtype=float)
        fit = fit_contour(pts, False, FitParams())
        self.assertTrue(fit.ok, fit.error)
        self.assertAlmostEqual(fit.wire.Length, 10.0, places=6)


if __name__ == "__main__":
    unittest.main()
