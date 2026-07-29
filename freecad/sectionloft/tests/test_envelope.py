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


class TestConcavity(unittest.TestCase):
    """The star profile follows what the axis can see; the convex one bridges."""

    @staticmethod
    def notched_square():
        pts = [(-10, -10), (10, -10), (10, 10), (1, 10), (1, 2), (-1, 2),
               (-1, 10), (-10, 10)]
        return np.array(pts, dtype=float)

    def test_star_form_follows_the_notch(self):
        result = ev.envelope_2d([self.notched_square()], centre=(0.0, -5.0),
                                samples=180, collapse_factor=0.0)
        top = result[np.abs(result[:, 0]) < 0.5]
        self.assertTrue(len(top) > 0)
        self.assertLess(top[:, 1].max(), 4.0,
                        "the notch should not have been bridged")

    def test_convex_form_bridges_the_notch(self):
        result = ev.envelope_2d([self.notched_square()], centre=(0.0, -5.0),
                                samples=180, convex=True)
        top = result[np.abs(result[:, 0]) < 0.5]
        self.assertGreater(top[:, 1].max(), 9.0)

    def test_bridging_threshold_decides(self):
        deep = np.array([(-10, -10), (10, -10), (10, 10), (0.5, 10),
                         (0.5, -8), (-0.5, -8), (-0.5, 10), (-10, 10)],
                        dtype=float)
        followed = ev.envelope_2d([deep], centre=(-5.0, 0.0), samples=180,
                                  collapse_factor=0.0)
        bridged = ev.envelope_2d([deep], centre=(-5.0, 0.0), samples=180,
                                 collapse_factor=0.9)
        self.assertGreater(_area(bridged), _area(followed))


class TestAxialField(unittest.TestCase):
    """Gaps are resolved along the slicing direction, not per section.

    Bridging a missing sample inside one section while its neighbour follows the
    material puts a step in the surface: measured at up to 10 mm on the test
    mesh, and most of what made the result look corrugated.
    """

    def test_a_gap_is_filled_from_the_sections_above_and_below(self):
        material = np.array([[5.0], [np.nan], [7.0]])
        hull = np.array([[9.0], [9.0], [9.0]])
        filled = ev.fill_along_axis(material, hull, smoothing=0)
        self.assertAlmostEqual(filled[1, 0], 6.0,
                               msg="should interpolate, not jump to the hull")

    def test_an_angle_no_section_can_see_falls_back_to_the_hull(self):
        material = np.full((3, 1), np.nan)
        hull = np.array([[9.0], [8.0], [7.0]])
        filled = ev.fill_along_axis(material, hull, smoothing=0)
        np.testing.assert_allclose(filled[:, 0], [9.0, 8.0, 7.0])

    def test_the_median_removes_a_single_section_zigzag(self):
        material = np.array([[5.0], [5.0], [9.0], [5.0], [5.0]])
        filled = ev.fill_along_axis(material, np.zeros((5, 1)), smoothing=3)
        self.assertAlmostEqual(filled[2, 0], 5.0)

    def test_the_median_keeps_a_real_step(self):
        """A median and not an average, so the edge of a flat facet survives
        instead of being ramped over three sections."""
        material = np.array([[5.0], [5.0], [5.0], [9.0], [9.0], [9.0]])
        filled = ev.fill_along_axis(material, np.zeros((6, 1)), smoothing=3)
        np.testing.assert_allclose(filled[:, 0], [5, 5, 5, 9, 9, 9])

    def test_smoothing_off_leaves_the_data_alone(self):
        material = np.array([[5.0], [9.0], [5.0]])
        filled = ev.fill_along_axis(material, np.zeros((3, 1)), smoothing=0)
        np.testing.assert_allclose(filled[:, 0], [5.0, 9.0, 5.0])


class TestEnvelopeProfile(unittest.TestCase):
    def test_reports_gaps_as_nan_rather_than_deciding(self):
        arc = fx.circle(radius=10.0, n=80)[:40, :2]
        material, hull = ev.envelope_profile([np.vstack([arc, arc[::-1] * 0.9])],
                                             centre=(0.0, 0.0), samples=72)
        self.assertEqual(len(material), 72)
        self.assertTrue(np.any(np.isnan(material)),
                        "angles with nothing to hit must come back as nan")
        self.assertTrue(np.all(hull > 0.0))

    def test_a_full_circle_has_no_gaps(self):
        poly = fx.circle(radius=10.0, n=90)[:, :2]
        material, _ = ev.envelope_profile([poly], centre=(0.0, 0.0), samples=72)
        self.assertFalse(np.any(np.isnan(material)))
        self.assertLess(abs(np.nanmean(material) - 10.0), 0.05)


class TestSharedAxis(unittest.TestCase):
    """Profiles only line up between sections if they share an origin, and the
    origin has to be inside every one of them."""

    def test_intersection_of_overlapping_hulls(self):
        a = np.array([[0, 0], [10, 0], [10, 10], [0, 10]], dtype=float)
        b = a + np.array([4.0, 0.0])
        region = ev.common_interior([a, b])
        self.assertIsNotNone(region)
        self.assertGreater(_area(region), 0.0)
        self.assertLess(region[:, 0].min(), 4.5)
        self.assertGreater(region[:, 0].min(), 3.5)

    def test_disjoint_hulls_have_no_common_interior(self):
        a = np.array([[0, 0], [1, 0], [1, 1], [0, 1]], dtype=float)
        b = a + np.array([50.0, 0.0])
        self.assertIsNone(ev.common_interior([a, b]))

    def test_axis_lands_inside_every_hull_when_it_can(self):
        hulls = [np.array([[0, 0], [10, 0], [10, 10], [0, 10]], dtype=float),
                 np.array([[2, 2], [12, 2], [12, 12], [2, 12]], dtype=float)]
        axis, misses = ev.shared_axis(hulls)
        self.assertEqual(misses, 0)
        for hull in hulls:
            self.assertTrue(ev._point_in_polygon(axis, hull))

    def test_reports_the_sections_it_could_not_satisfy(self):
        hulls = [np.array([[0, 0], [4, 0], [4, 4], [0, 4]], dtype=float),
                 np.array([[50, 0], [54, 0], [54, 4], [50, 4]], dtype=float)]
        axis, misses = ev.shared_axis(hulls)
        self.assertIsNotNone(axis)
        self.assertEqual(misses, 1)

    def test_no_hulls(self):
        self.assertEqual(ev.shared_axis([]), (None, 0))


def _area(polygon):
    x, y = polygon[:, 0], polygon[:, 1]
    return abs(0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))))


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
