"""Closing, orientation and seam placement.  Pure numpy."""

import unittest

import numpy as np

from freecad.sectionloft.core import contours as ct
from freecad.sectionloft.core.planes import orthonormal_frame
from freecad.sectionloft.tests import fixtures as fx


class TestClosing(unittest.TestCase):
    def test_strips_repeated_endpoint(self):
        pts = fx.circle(n=20)
        raw = np.vstack([pts, pts[0]])
        out, closed, discarded = ct.close_contour(raw, 1e-6)
        self.assertTrue(closed)
        self.assertEqual(len(out), 20)
        self.assertEqual(discarded, 1)

    def test_gap_within_tolerance_closes(self):
        pts = fx.circle(n=20)
        out, closed, _ = ct.close_contour(pts, 10.0)
        self.assertTrue(closed)

    def test_open_stays_open(self):
        pts = fx.circle(n=20)[:10]
        out, closed, discarded = ct.close_contour(pts, 1e-6)
        self.assertFalse(closed)
        self.assertEqual(len(out), 10)
        self.assertEqual(discarded, 0)

    def test_retraced_contour_is_trimmed_at_first_return(self):
        """A plane lying in a ring of coplanar mesh edges makes OCC walk the
        loop twice; the second pass must be dropped, not averaged in."""
        pts = fx.circle(n=40)
        doubled = np.vstack([pts, pts[0], pts[::-1], pts[0]])
        out, closed, discarded = ct.close_contour(doubled, 1e-6)
        self.assertTrue(closed)
        self.assertEqual(len(out), 40)
        self.assertGreater(discarded, 40)
        np.testing.assert_allclose(out, pts)

    def test_retraced_contour_has_usable_area(self):
        pts = fx.circle(radius=10.0, n=40)
        doubled = np.vstack([pts, pts[0], pts[::-1], pts[0]])
        out, closed, _ = ct.close_contour(doubled, 1e-6)
        u, v, _ = orthonormal_frame((0, 0, 1))
        self.assertGreater(ct.signed_area(ct.to_plane_coords(out, out[0], u, v)),
                           0.0)


class TestDuplicates(unittest.TestCase):
    def test_removes_coincident_points(self):
        pts = np.array([[0, 0, 0], [0, 0, 0], [1, 0, 0], [1, 0, 0], [2, 0, 0]],
                       dtype=float)
        out = ct.drop_duplicates(pts, 1e-6)
        self.assertEqual(len(out), 3)


class TestLength(unittest.TestCase):
    def test_closed_circle_perimeter(self):
        pts = fx.circle(radius=10.0, n=2000)
        self.assertAlmostEqual(ct.contour_length(pts, True), 2 * np.pi * 10.0,
                               places=2)

    def test_open_is_shorter_by_one_segment(self):
        pts = fx.circle(radius=10.0, n=100)
        closed = ct.contour_length(pts, True)
        open_ = ct.contour_length(pts, False)
        self.assertGreater(closed, open_)


class TestOrientation(unittest.TestCase):
    def test_ccw_contour_is_left_alone(self):
        pts = fx.circle(n=32)
        out, flipped = ct.unify_orientation(pts, (0, 0, 1))
        self.assertFalse(flipped)
        np.testing.assert_allclose(out, pts)

    def test_cw_contour_is_reversed(self):
        pts = fx.circle(n=32)[::-1].copy()
        out, flipped = ct.unify_orientation(pts, (0, 0, 1))
        self.assertTrue(flipped)
        u, v, _ = orthonormal_frame((0, 0, 1))
        self.assertGreater(ct.signed_area(ct.to_plane_coords(out, out[0], u, v)), 0)

    def test_random_orientations_all_end_positive(self):
        rng = np.random.default_rng(7)
        for _ in range(20):
            normal = rng.normal(size=3)
            normal /= np.linalg.norm(normal)
            u, v, n = orthonormal_frame(normal)
            t = np.linspace(0, 2 * np.pi, 40, endpoint=False)
            pts = np.array([np.cos(a) * u * 10 + np.sin(a) * v * 10 for a in t])
            if rng.random() < 0.5:
                pts = pts[::-1].copy()
            out, _ = ct.unify_orientation(pts, normal)
            area = ct.signed_area(ct.to_plane_coords(out, out.mean(axis=0), u, v))
            self.assertGreater(area, 0.0)


class TestSeam(unittest.TestCase):
    def test_axis_picks_extreme_point(self):
        pts = fx.circle(radius=10.0, n=36)
        out = ct.apply_seam(pts, True, ct.SEAM_AXIS, (1, 0, 0))
        self.assertAlmostEqual(out[0][0], 10.0, places=6)
        out = ct.apply_seam(pts, True, ct.SEAM_AXIS, (0, 1, 0))
        self.assertAlmostEqual(out[0][1], 10.0, places=6)

    def test_axis_is_consistent_across_a_stack(self):
        starts = []
        for z in np.linspace(-40, 40, 11):
            r = np.sqrt(max(50.0 ** 2 - z ** 2, 1.0))
            pts = fx.circle(radius=r, n=41, z=z)
            pts = np.roll(pts, int(abs(z)) % 41, axis=0)   # scramble the start
            starts.append(ct.apply_seam(pts, True, ct.SEAM_AXIS, (1, 0, 0))[0])
        starts = np.array(starts)
        # All start points must lie in the y = 0 axial plane.
        self.assertLess(np.abs(starts[:, 1]).max(), 1e-6)

    def test_guide_picks_nearest_point(self):
        pts = fx.circle(radius=10.0, n=36, z=5.0)
        guide = np.array([[0.0, -10.0, 0.0], [0.0, -10.0, 10.0]])
        out = ct.apply_seam(pts, True, ct.SEAM_GUIDE, guide=guide)
        self.assertAlmostEqual(out[0][1], -10.0, places=6)

    def test_min_travel_follows_previous_start(self):
        pts = fx.circle(radius=10.0, n=36)
        previous = np.array([0.0, -10.0, 0.0])
        out = ct.apply_seam(pts, True, ct.SEAM_MIN_TRAVEL, previous_start=previous)
        self.assertAlmostEqual(out[0][1], -10.0, places=6)

    def test_none_leaves_contour_untouched(self):
        pts = fx.circle(n=20)
        np.testing.assert_allclose(ct.apply_seam(pts, True, ct.SEAM_NONE), pts)

    def test_open_contour_keeps_its_start(self):
        pts = fx.circle(n=20)[:10]
        np.testing.assert_allclose(ct.apply_seam(pts, False, ct.SEAM_AXIS), pts)

    def test_rotation_preserves_cycle(self):
        pts = fx.circle(n=13)
        out = ct.rotate_to_seam(pts, 5)
        np.testing.assert_allclose(out[0], pts[5])
        np.testing.assert_allclose(out[-1], pts[4])


if __name__ == "__main__":
    unittest.main()
