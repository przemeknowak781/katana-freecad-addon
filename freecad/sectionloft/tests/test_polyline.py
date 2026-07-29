"""Decimation, corner detection, deviation.  Pure numpy."""

import unittest

import numpy as np

from freecad.sectionloft.core import polyline as pl
from freecad.sectionloft.tests import fixtures as fx


class TestDouglasPeucker(unittest.TestCase):
    def test_collinear_points_collapse_to_endpoints(self):
        pts = np.column_stack((np.linspace(0, 10, 50), np.zeros(50), np.zeros(50)))
        out = pl.douglas_peucker(pts, 0.01)
        self.assertEqual(len(out), 2)

    def test_reduces_dense_circle_within_tolerance(self):
        pts = fx.circle(radius=25.0, n=400)
        out = pl.douglas_peucker(pts, 0.05, closed=True)
        self.assertLess(len(out), len(pts))
        # Every original point must still lie within tolerance of the reduction.
        self.assertLessEqual(pl.max_deviation(pts, out, closed=True), 0.05 + 1e-9)

    def test_keeps_seam_point(self):
        pts = fx.circle(radius=25.0, n=200)
        out = pl.douglas_peucker(pts, 0.5, closed=True)
        np.testing.assert_allclose(out[0], pts[0])

    def test_keeps_corners_of_a_rectangle(self):
        pts = fx.rectangle(per_side=25)
        out = pl.douglas_peucker(pts, 0.01, closed=True)
        self.assertEqual(len(out), 4)

    def test_zero_tolerance_is_a_no_op(self):
        pts = fx.circle(n=20)
        np.testing.assert_allclose(pl.douglas_peucker(pts, 0.0), pts)

    def test_never_collapses_a_loop_below_three_points(self):
        pts = fx.circle(radius=1.0, n=12)
        out = pl.douglas_peucker(pts, 1000.0, closed=True)
        self.assertGreaterEqual(len(out), 3)


class TestCorners(unittest.TestCase):
    def test_rectangle_has_exactly_four_corners(self):
        pts = pl.douglas_peucker(fx.rectangle(per_side=12), 0.01, closed=True)
        for degrees in (10, 20, 30, 45, 60, 80):
            corners = pl.detect_corners(pts, np.deg2rad(degrees), closed=True)
            self.assertEqual(len(corners), 4, "threshold %d deg" % degrees)

    def test_circle_has_no_corners(self):
        pts = pl.douglas_peucker(fx.circle(radius=25.0, n=200), 0.02, closed=True)
        self.assertEqual(pl.detect_corners(pts, np.deg2rad(30.0), closed=True), [])

    def test_noisy_circle_has_no_corners_after_decimation(self):
        pts = fx.circle(radius=25.0, n=300, noise=0.05, seed=3)
        pts = pl.douglas_peucker(pts, 0.25, closed=True)
        self.assertEqual(pl.detect_corners(pts, np.deg2rad(30.0), closed=True), [])

    def test_open_polyline_ignores_endpoints(self):
        pts = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [2, 1, 0]], dtype=float)
        corners = pl.detect_corners(pts, np.deg2rad(30.0), closed=False)
        self.assertEqual(corners, [1, 2])


class TestSplit(unittest.TestCase):
    def test_closed_rectangle_splits_into_four_sides(self):
        pts = pl.douglas_peucker(fx.rectangle(per_side=12), 0.01, closed=True)
        corners = pl.detect_corners(pts, np.deg2rad(30.0), closed=True)
        segments = pl.split_at_corners(pts, corners, closed=True)
        self.assertEqual(len(segments), 4)
        for seg in segments:
            self.assertEqual(len(seg), 2)
        # Segments must chain end-to-start all the way round.
        for i, seg in enumerate(segments):
            nxt = segments[(i + 1) % 4]
            np.testing.assert_allclose(seg[-1], nxt[0])

    def test_no_corners_yields_one_segment(self):
        pts = fx.circle(n=30)
        self.assertEqual(len(pl.split_at_corners(pts, [], closed=True)), 1)

    def test_open_split_covers_the_whole_polyline(self):
        pts = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [2, 1, 0]], dtype=float)
        segments = pl.split_at_corners(pts, [1, 2], closed=False)
        self.assertEqual(len(segments), 3)
        np.testing.assert_allclose(segments[0][0], pts[0])
        np.testing.assert_allclose(segments[-1][-1], pts[-1])


class TestDeviation(unittest.TestCase):
    def test_identical_polylines_have_zero_deviation(self):
        pts = fx.circle(n=40)
        self.assertLess(pl.max_deviation(pts, pts, closed=True), 1e-12)

    def test_offset_is_measured(self):
        pts = np.column_stack((np.linspace(0, 10, 20), np.zeros(20), np.zeros(20)))
        shifted = pts + np.array([0.0, 0.3, 0.0])
        self.assertAlmostEqual(pl.max_deviation(pts, shifted), 0.3, places=9)


class TestMedianEdge(unittest.TestCase):
    def test_uniform_polyline(self):
        pts = np.column_stack((np.arange(11.0), np.zeros(11), np.zeros(11)))
        self.assertAlmostEqual(pl.median_edge_length(pts), 1.0)


if __name__ == "__main__":
    unittest.main()
