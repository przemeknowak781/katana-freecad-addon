"""Splitting a ribbon section into its two walls.  Pure numpy."""

import unittest

import numpy as np

from freecad.sectionloft.core import walls as wl
from freecad.sectionloft.tests import fixtures as fx

FRAME = (np.zeros(3), np.array([1.0, 0.0, 0.0]), np.array([0.0, 1.0, 0.0]))


def ribbon(outer, inner, n=90, z=0.0, gap=0):
    """A thin wall in section: out along one wall, back along the other.

    ``gap`` drops that many points from the outer run, standing in for a slot.
    """
    out = fx.circle(radius=outer, n=n, z=z)
    if gap:
        out = np.vstack([out[:n // 2 - gap], out[n // 2:]])
    back = fx.circle(radius=inner, n=n, z=z)[::-1]
    return np.vstack([out, back])


class TestClassification(unittest.TestCase):
    def test_the_outer_wall_is_found(self):
        points = ribbon(10.0, 9.0)[:, :2]
        outer = wl.classify_outer(points, (0.0, 0.0))
        radii = np.linalg.norm(points, axis=1)
        self.assertGreater(radii[outer].min(), 9.5)
        self.assertLess(radii[~outer].max(), 9.5)

    def test_a_full_circle_is_all_outer(self):
        points = fx.circle(radius=10.0, n=60)[:, :2]
        self.assertTrue(np.all(wl.classify_outer(points, (0.0, 0.0))))

    def test_empty_bins_do_not_swallow_everything(self):
        """np.maximum propagates nan, so seeding the per-bin maximum with nan
        would classify nothing at all."""
        points = ribbon(10.0, 9.0, n=24)[:, :2]
        outer = wl.classify_outer(points, (0.0, 0.0))
        self.assertTrue(np.any(outer))
        self.assertTrue(np.any(~outer))


class TestSmoothing(unittest.TestCase):
    def test_a_single_flicker_is_absorbed(self):
        mask = np.ones(20, dtype=bool)
        mask[7] = False
        self.assertTrue(np.all(wl.smooth_mask(mask, 5)))

    def test_a_real_block_survives(self):
        mask = np.ones(20, dtype=bool)
        mask[6:14] = False
        smoothed = wl.smooth_mask(mask, 5)
        self.assertFalse(smoothed[10])
        self.assertTrue(smoothed[0])

    def test_wrapping(self):
        mask = np.ones(20, dtype=bool)
        mask[0] = False
        self.assertTrue(wl.smooth_mask(mask, 5)[0])


class TestRuns(unittest.TestCase):
    def test_runs_join_across_the_wrap(self):
        mask = np.array([True, True, False, False, True, True], dtype=bool)
        runs = wl.contiguous_runs(mask, closed=True)
        self.assertEqual(len(runs), 1)
        self.assertEqual(len(runs[0]), 4)

    def test_open_contours_do_not_wrap(self):
        mask = np.array([True, True, False, False, True, True], dtype=bool)
        self.assertEqual(len(wl.contiguous_runs(mask, closed=False)), 2)

    def test_short_runs_are_dropped(self):
        mask = np.array([True, False, False, False, True, True, True],
                        dtype=bool)
        runs = wl.contiguous_runs(mask, closed=False, minimum=3)
        self.assertEqual(len(runs), 1)


class TestSplitWalls(unittest.TestCase):
    def test_a_ribbon_splits_into_two_walls(self):
        points = ribbon(10.0, 9.0)
        outer, inner = wl.split_walls(points, (0.0, 0.0), FRAME)
        self.assertTrue(outer)
        self.assertTrue(inner)
        self.assertGreater(np.linalg.norm(np.vstack(outer)[:, :2],
                                          axis=1).min(), 9.5)
        self.assertLess(np.linalg.norm(np.vstack(inner)[:, :2],
                                       axis=1).max(), 9.5)

    def test_a_slot_becomes_a_gap_not_a_body(self):
        """A wall interrupted by a slot must come back as two runs."""
        points = ribbon(10.0, 9.0, gap=12)
        outer, _inner = wl.split_walls(points, (0.0, 0.0), FRAME)
        self.assertGreaterEqual(len(outer), 2)

    def test_the_split_keeps_every_point(self):
        points = ribbon(10.0, 9.0)
        outer, inner = wl.split_walls(points, (0.0, 0.0), FRAME,
                                      minimum_points=2)
        kept = sum(len(r) for r in outer) + sum(len(r) for r in inner)
        self.assertEqual(kept, len(points))

    def test_thickness_of_a_known_wall(self):
        points = ribbon(10.0, 9.0)
        outer, inner = wl.split_walls(points, (0.0, 0.0), FRAME)
        self.assertAlmostEqual(wl.wall_thickness(outer, inner), 1.0, places=1)

    def test_a_solid_section_has_no_inner_wall(self):
        points = fx.circle(radius=10.0, n=60)
        outer, inner = wl.split_walls(points, (0.0, 0.0), FRAME)
        self.assertTrue(outer)
        self.assertEqual(inner, [])


if __name__ == "__main__":
    unittest.main()
