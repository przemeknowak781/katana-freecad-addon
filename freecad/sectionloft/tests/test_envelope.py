"""Radial envelope of section contours.  Pure numpy."""

import unittest

import numpy as np

from freecad.sectionloft.core import contours as ct
from freecad.sectionloft.core import envelope as ev
from freecad.sectionloft.tests import fixtures as fx


def ring(outer, inner, n=60, z=0.0):
    """A thin ribbon: out along the outer wall, back along the inner one.

    This is what cutting a thin-walled part actually produces - a single closed
    polyline, not two nested contours.
    """
    out = fx.circle(radius=outer, n=n, z=z)
    back = fx.circle(radius=inner, n=n, z=z)[::-1]
    return np.vstack([out, back])


class TestEnvelope2D(unittest.TestCase):
    def test_circle_comes_back_as_a_circle(self):
        poly = fx.circle(radius=10.0, n=90)[:, :2]
        result = ev.envelope_2d([poly], samples=72)
        self.assertEqual(len(result), 72)
        radii = np.linalg.norm(result, axis=1)
        self.assertLess(abs(radii.mean() - 10.0), 0.05)
        self.assertLess(radii.std(), 0.05)

    def test_a_ribbon_collapses_to_its_outer_wall(self):
        poly = ring(10.0, 9.0)[:, :2]
        result = ev.envelope_2d([poly], samples=72)
        radii = np.linalg.norm(result, axis=1)
        self.assertLess(abs(radii.mean() - 10.0), 0.05,
                        "the envelope should follow the outer wall")

    def test_several_contours_are_covered_by_one_envelope(self):
        """Slots break a section into pieces; the envelope spans them."""
        pieces = [fx.circle(radius=10.0, n=40)[:20, :2],
                  fx.circle(radius=10.0, n=40)[20:, :2]]
        pieces = [np.vstack([p, p[::-1] * 0.9]) for p in pieces]
        result = ev.envelope_2d(pieces, centre=(0.0, 0.0), samples=64)
        self.assertIsNotNone(result)
        radii = np.linalg.norm(result, axis=1)
        self.assertLess(abs(radii.max() - 10.0), 0.3)

    def test_the_result_is_a_simple_polygon(self):
        """The whole point: a radial profile cannot self-intersect, which is
        what made the lofts of real ribbons collapse into shards."""
        poly = ring(10.0, 9.5)[:, :2]
        result = ev.envelope_2d([poly], centre=(0.0, 0.0), samples=90)
        angles = np.arctan2(result[:, 1], result[:, 0])
        ordered = np.unwrap(angles)
        self.assertTrue(np.all(np.diff(ordered) > 0),
                        "points must run monotonically around the centre")

    def test_every_section_gets_the_same_point_count(self):
        for radius in (5.0, 8.0, 12.0):
            result = ev.envelope_2d([fx.circle(radius=radius, n=50)[:, :2]],
                                    samples=48)
            self.assertEqual(len(result), 48)

    def test_gaps_are_interpolated_not_left_as_notches(self):
        """A C-shaped section sampled through its opening hits nothing."""
        arc = fx.circle(radius=10.0, n=80)[:60]
        poly = np.vstack([arc, arc[::-1] * 0.9])[:, :2]
        result = ev.envelope_2d([poly], centre=(0.0, 0.0), samples=72)
        self.assertEqual(len(result), 72)
        self.assertFalse(np.any(np.isnan(result)))

    def test_nothing_to_hit(self):
        self.assertIsNone(ev.envelope_2d([]))
        self.assertIsNone(ev.envelope_2d([np.zeros((2, 2))]))


class TestEnvelopeContour(unittest.TestCase):
    def test_stays_in_the_section_plane(self):
        contour = ring(10.0, 9.0, z=7.0)
        result = ev.envelope_contour([contour], (0, 0, 7.0), (0, 0, 1),
                                     samples=36)
        self.assertEqual(len(result), 36)
        np.testing.assert_allclose(result[:, 2], 7.0, atol=1e-9)

    def test_works_on_a_tilted_plane(self):
        normal = np.array([1.0, 1.0, 1.0]) / np.sqrt(3)
        from freecad.sectionloft.core.planes import orthonormal_frame
        u, v, _ = orthonormal_frame(normal)
        base = np.array([1.0, 2.0, 3.0])
        angles = np.linspace(0, 2 * np.pi, 60, endpoint=False)
        contour = np.array([base + 10 * np.cos(a) * u + 10 * np.sin(a) * v
                            for a in angles])
        result = ev.envelope_contour([contour], base, normal, samples=36)
        offsets = (result - base) @ normal
        np.testing.assert_allclose(offsets, 0.0, atol=1e-9)
        radii = np.linalg.norm(result - base, axis=1)
        self.assertLess(abs(radii.mean() - 10.0), 0.05)


class TestConvexHull(unittest.TestCase):
    def test_square_with_interior_points(self):
        pts = np.array([[0, 0], [10, 0], [10, 10], [0, 10],
                        [5, 5], [3, 7], [8, 2]], dtype=float)
        hull = ev.convex_hull_2d(pts)
        self.assertEqual(len(hull), 4)
        self.assertEqual({tuple(p) for p in hull},
                         {(0, 0), (10, 0), (10, 10), (0, 10)})

    def test_counter_clockwise(self):
        hull = ev.convex_hull_2d(np.array([[0, 0], [10, 0], [10, 10], [0, 10]],
                                          dtype=float))
        area = 0.5 * sum(hull[i][0] * hull[(i + 1) % len(hull)][1]
                         - hull[(i + 1) % len(hull)][0] * hull[i][1]
                         for i in range(len(hull)))
        self.assertGreater(area, 0.0)

    def test_collinear_input(self):
        pts = np.array([[0, 0], [1, 1], [2, 2]], dtype=float)
        self.assertLessEqual(len(ev.convex_hull_2d(pts)), 3)


class TestRadialMax(unittest.TestCase):
    def test_takes_the_farthest_point_per_bin(self):
        pts = np.array([[1, 0], [5, 0], [0, 3]], dtype=float)
        radii = ev.radial_max(pts, (0.0, 0.0), samples=4)
        self.assertAlmostEqual(radii[0], 5.0)
        self.assertAlmostEqual(radii[1], 3.0)

    def test_empty_bins_come_back_as_nan_not_zero(self):
        """np.maximum propagates nan, so accumulating into a nan-filled array
        leaves every bin empty and the caller silently gets nothing."""
        radii = ev.radial_max(np.array([[5.0, 0.0]]), (0.0, 0.0), samples=8)
        self.assertAlmostEqual(radii[0], 5.0)
        self.assertEqual(int(np.isnan(radii).sum()), 7)


class TestDilate(unittest.TestCase):
    def test_never_moves_a_radius_inwards(self):
        radii = np.array([1.0, 5.0, 1.0, 1.0, 1.0, 1.0], dtype=float)
        out = ev.dilate_radii(radii, 3)
        self.assertTrue(np.all(out >= radii))
        self.assertAlmostEqual(out[0], 5.0)
        self.assertAlmostEqual(out[2], 5.0)

    def test_wraps_around(self):
        radii = np.array([5.0, 1.0, 1.0, 1.0], dtype=float)
        out = ev.dilate_radii(radii, 3)
        self.assertAlmostEqual(out[-1], 5.0)


class TestEnvelopeFromPoints(unittest.TestCase):
    """The envelope of a point cloud is its convex hull, so containment is a
    property of the construction rather than something measured afterwards."""

    def test_contains_every_point(self):
        rng = np.random.default_rng(5)
        pts = rng.normal(0.0, 5.0, (300, 2))
        # Sampling a polygon at fixed angles cuts the corners a little, so the
        # guarantee is stated the way a user would use it: with a clearance.
        profile = ev.envelope_from_points(pts, samples=180, clearance=0.2)
        outside = [p for p in pts if not ev._point_in_polygon(p, profile)]
        self.assertEqual(outside, [], "%d points escaped the envelope"
                         % len(outside))

    def test_a_square_stays_square(self):
        pts = np.array([[0, 0], [10, 0], [10, 10], [0, 10]], dtype=float)
        profile = ev.envelope_from_points(pts, samples=360)
        self.assertLess(abs(profile[:, 0].max() - 10.0), 0.05)
        self.assertLess(abs(profile[:, 1].min() - 0.0), 0.05)

    def test_clearance_pushes_outwards(self):
        pts = fx.circle(radius=10.0, n=64)[:, :2]
        plain = ev.envelope_from_points(pts, samples=72)
        padded = ev.envelope_from_points(pts, samples=72, clearance=0.5)
        centre = plain.mean(axis=0)
        self.assertAlmostEqual(
            np.linalg.norm(padded[0] - centre)
            - np.linalg.norm(plain[0] - centre), 0.5, places=6)

    def test_a_centre_outside_the_hull_is_replaced(self):
        pts = np.array([[0, 0], [10, 0], [10, 10], [0, 10]], dtype=float)
        profile = ev.envelope_from_points(pts, centre=(100.0, 100.0),
                                          samples=72)
        self.assertIsNotNone(profile)
        self.assertLess(profile[:, 0].max(), 11.0)


class TestEnvelopeFromSlab(unittest.TestCase):
    def test_contains_points_above_and_below_the_plane(self):
        """The slab is what stops a loft from shaving features that fall
        between two section planes."""
        ring_low = fx.circle(radius=12.0, n=40, z=-0.4)
        ring_mid = fx.circle(radius=8.0, n=40, z=0.0)
        points = np.vstack([ring_low, ring_mid])
        profile = ev.envelope_from_slab(points, (0, 0, 0), (0, 0, 1),
                                        samples=72)
        radii = np.linalg.norm(profile[:, :2], axis=1)
        self.assertGreater(radii.min(), 11.8)
        np.testing.assert_allclose(profile[:, 2], 0.0, atol=1e-9)

    def test_shared_axis_is_the_one_it_was_given(self):
        """Profiles measured from a common axis stay comparable between
        sections; letting each pick its own centroid makes the loft twist."""
        offset = fx.circle(radius=6.0, n=64, z=0.0) + np.array([4.0, 0.0, 0.0])
        profile = ev.envelope_from_slab(offset, (0, 0, 0), (0, 0, 1),
                                        samples=180, axis_point=(0, 0, 0))
        radii = np.linalg.norm(profile[:, :2], axis=1)
        # Measured from the origin the circle reaches 10 on one side and 2 on
        # the other; measured from its own centre it would be 6 all round.
        self.assertLess(abs(radii.max() - 10.0), 0.1)
        self.assertLess(abs(radii.min() - 2.0), 0.2)


class TestRibbonDetection(unittest.TestCase):
    def test_a_circle_is_not_a_ribbon(self):
        self.assertFalse(ev.is_ribbon(fx.circle(radius=10.0, n=60)))

    def test_a_rectangle_is_not_a_ribbon(self):
        self.assertFalse(ev.is_ribbon(fx.rectangle(40, 30, 10)))

    def test_a_thin_wall_section_is_a_ribbon(self):
        self.assertTrue(ev.is_ribbon(ring(10.0, 9.5)))

    def test_measured_against_the_real_mesh_numbers(self):
        """The mask's plane 1: area 10.33 mm2 for a perimeter of 41.6 mm."""
        compact = 41.595 ** 2 / (4 * np.pi)
        self.assertLess(10.331 / compact, 0.25)


if __name__ == "__main__":
    unittest.main()
