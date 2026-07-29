"""Plane family generation.  Pure numpy - runs without FreeCAD."""

import unittest

import numpy as np

from freecad.sectionloft.core import planes as pf


class TestNormalize(unittest.TestCase):
    def test_scales_to_unit_length(self):
        self.assertAlmostEqual(np.linalg.norm(pf.normalize((0, 0, 7))), 1.0)
        np.testing.assert_allclose(pf.normalize((0, 0, 7)), [0, 0, 1])

    def test_rejects_null_vector(self):
        with self.assertRaises(ValueError):
            pf.normalize((0, 0, 0))


class TestFrame(unittest.TestCase):
    def test_orthonormal_and_right_handed(self):
        for normal in [(0, 0, 1), (1, 0, 0), (1, 2, 3), (-0.3, 0.9, 0.1)]:
            u, v, n = pf.orthonormal_frame(normal)
            for vec in (u, v, n):
                self.assertAlmostEqual(np.linalg.norm(vec), 1.0)
            self.assertAlmostEqual(np.dot(u, v), 0.0, places=12)
            self.assertAlmostEqual(np.dot(u, n), 0.0, places=12)
            self.assertAlmostEqual(np.dot(v, n), 0.0, places=12)
            np.testing.assert_allclose(np.cross(u, v), n, atol=1e-12)

    def test_deterministic(self):
        a = pf.orthonormal_frame((1, 2, 3))
        b = pf.orthonormal_frame((1, 2, 3))
        for x, y in zip(a, b):
            np.testing.assert_array_equal(x, y)


class TestPlanePositions(unittest.TestCase):
    def test_count_spans_range_inclusively(self):
        pos = pf.plane_positions(pf.MODE_COUNT, -10.0, 10.0, count=5)
        np.testing.assert_allclose(pos, [-10, -5, 0, 5, 10])

    def test_count_one_lands_in_the_middle(self):
        self.assertEqual(pf.plane_positions(pf.MODE_COUNT, 0.0, 8.0, count=1), [4.0])

    def test_spacing_walks_from_start(self):
        pos = pf.plane_positions(pf.MODE_SPACING, 0.0, 10.0, spacing=3.0)
        np.testing.assert_allclose(pos, [0, 3, 6, 9])

    def test_spacing_includes_exact_end(self):
        pos = pf.plane_positions(pf.MODE_SPACING, 0.0, 9.0, spacing=3.0)
        np.testing.assert_allclose(pos, [0, 3, 6, 9])

    def test_reversed_range_is_normalised(self):
        pos = pf.plane_positions(pf.MODE_COUNT, 10.0, -10.0, count=3)
        np.testing.assert_allclose(pos, [-10, 0, 10])

    def test_invalid_arguments(self):
        with self.assertRaises(ValueError):
            pf.plane_positions(pf.MODE_COUNT, 0, 1, count=0)
        with self.assertRaises(ValueError):
            pf.plane_positions(pf.MODE_SPACING, 0, 1, spacing=0.0)
        with self.assertRaises(ValueError):
            pf.plane_positions("Nonsense", 0, 1, count=3)


class TestPlaneFamily(unittest.TestCase):
    def test_positions_and_normals(self):
        family = pf.plane_family((0, 0, 0), (0, 0, 2), pf.MODE_COUNT, count=4,
                                 range_start=-3.0, range_end=3.0)
        self.assertEqual(len(family), 4)
        zs = [p[0][2] for p in family]
        np.testing.assert_allclose(zs, [-3, -1, 1, 3])
        for _, normal in family:
            np.testing.assert_allclose(normal, [0, 0, 1])

    def test_oblique_direction(self):
        d = np.array([1.0, 1.0, 0.0]) / np.sqrt(2)
        family = pf.plane_family((5, 5, 5), d, pf.MODE_COUNT, count=3,
                                 range_start=0.0, range_end=2.0)
        np.testing.assert_allclose(family[0][0], [5, 5, 5])
        np.testing.assert_allclose(family[2][0], [5 + 2 * d[0], 5 + 2 * d[1], 5])


class TestAvoidVertexRows(unittest.TestCase):
    def test_plane_on_a_vertex_row_is_nudged(self):
        offsets = np.concatenate([np.full(50, 0.0), np.linspace(-10, 10, 200)])
        out = pf.avoid_vertex_rows([0.0], offsets, epsilon=1e-6, nudge=0.01)
        self.assertNotAlmostEqual(out[0], 0.0)
        self.assertLess(abs(out[0]), 0.05)

    def test_plane_in_open_space_is_left_alone(self):
        offsets = np.array([-10.0, -5.0, 5.0, 10.0])
        out = pf.avoid_vertex_rows([0.0], offsets, epsilon=1e-6, nudge=0.01)
        self.assertEqual(out[0], 0.0)

    def test_two_stray_vertices_are_not_a_row(self):
        offsets = np.array([0.0, 0.0, 3.0, -3.0])
        out = pf.avoid_vertex_rows([0.0], offsets, epsilon=1e-6, nudge=0.01)
        self.assertEqual(out[0], 0.0)


class TestAutoRange(unittest.TestCase):
    def test_covers_bbox_with_inset(self):
        start, end = pf.auto_range((-10, -10, -20), (10, 10, 20), (0, 0, 0),
                                   (0, 0, 1), inset=0.0)
        self.assertAlmostEqual(start, -20.0)
        self.assertAlmostEqual(end, 20.0)

    def test_inset_shrinks_symmetrically(self):
        start, end = pf.auto_range((-10, -10, -20), (10, 10, 20), (0, 0, 0),
                                   (0, 0, 1), inset=0.1)
        self.assertAlmostEqual(start, -16.0)
        self.assertAlmostEqual(end, 16.0)

    def test_point_cloud_range_is_tighter_than_the_bbox_diagonal(self):
        """The whole reason auto_range_from_points exists: for a sphere the
        bbox corners overstate the extent along any oblique direction."""
        t = np.linspace(0, np.pi, 40)
        p = np.linspace(0, 2 * np.pi, 40)
        tt, pp_ = np.meshgrid(t, p)
        sphere = np.column_stack([
            (50 * np.sin(tt) * np.cos(pp_)).ravel(),
            (50 * np.sin(tt) * np.sin(pp_)).ravel(),
            (50 * np.cos(tt)).ravel()])
        d = np.ones(3) / np.sqrt(3)
        start, end = pf.auto_range_from_points(sphere, (0, 0, 0), d, inset=0.0)
        self.assertAlmostEqual(end, 50.0, places=1)
        bbox_start, bbox_end = pf.auto_range((-50,) * 3, (50,) * 3, (0, 0, 0), d,
                                             inset=0.0)
        self.assertGreater(bbox_end, 80.0)

    def test_diagonal_direction_uses_corners(self):
        d = np.ones(3) / np.sqrt(3)
        start, end = pf.auto_range((0, 0, 0), (10, 10, 10), (5, 5, 5), d, inset=0.0)
        self.assertAlmostEqual(end, np.sqrt(3) * 5.0)
        self.assertAlmostEqual(start, -np.sqrt(3) * 5.0)


if __name__ == "__main__":
    unittest.main()
