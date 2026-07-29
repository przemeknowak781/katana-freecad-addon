"""Point grids for surfacing.  Pure numpy."""

import unittest

import numpy as np

from freecad.sectionloft.core import surfaces as sf
from freecad.sectionloft.tests import fixtures as fx


def arc_run(radius, z, count=30, turn=np.pi):
    angles = np.linspace(0.0, turn, count)
    return np.column_stack((radius * np.cos(angles), radius * np.sin(angles),
                            np.full(count, float(z))))


class TestResample(unittest.TestCase):
    def test_keeps_both_ends(self):
        run = arc_run(10.0, 0.0)
        sampled = sf.resample_open(run, 12)
        self.assertEqual(len(sampled), 12)
        np.testing.assert_allclose(sampled[0], run[0], atol=1e-9)
        np.testing.assert_allclose(sampled[-1], run[-1], atol=1e-9)

    def test_spacing_is_even_along_the_arc(self):
        sampled = sf.resample_open(arc_run(10.0, 0.0, count=200), 20)
        steps = np.linalg.norm(np.diff(sampled, axis=0), axis=1)
        self.assertLess(steps.std() / steps.mean(), 0.02)

    def test_degenerate_input(self):
        self.assertIsNone(sf.resample_open(np.zeros((1, 3)), 5))
        self.assertIsNone(sf.resample_open(np.zeros((4, 3)), 5))


class TestAlignment(unittest.TestCase):
    def test_a_reversed_run_is_flipped_back(self):
        first = arc_run(10.0, 0.0)
        second = arc_run(10.0, 1.0)[::-1]
        aligned = sf.align_runs([first, second])
        self.assertLess(np.linalg.norm(aligned[1][0] - first[0]),
                        np.linalg.norm(second[0] - first[0]))

    def test_an_aligned_run_is_left_alone(self):
        first = arc_run(10.0, 0.0)
        second = arc_run(10.0, 1.0)
        aligned = sf.align_runs([first, second])
        np.testing.assert_allclose(aligned[1], second)


class TestGrid(unittest.TestCase):
    def test_shape(self):
        runs = [arc_run(10.0, z) for z in range(5)]
        grid = sf.grid_from_runs(runs, 16)
        self.assertEqual(grid.shape, (5, 16, 3))

    def test_a_reversed_section_does_not_twist_the_grid(self):
        """The direction a cross-section comes out in is an accident of the
        triangulation; unflipped, the columns cross over and the surface folds."""
        runs = [arc_run(10.0, 0.0), arc_run(10.0, 1.0)[::-1],
                arc_run(10.0, 2.0)]
        twist, _stretch = sf.grid_quality(sf.grid_from_runs(runs, 16))
        self.assertLess(twist, 5.0)

    def test_too_few_runs(self):
        self.assertIsNone(sf.grid_from_runs([arc_run(10.0, 0.0)], 8))


class TestQuality(unittest.TestCase):
    def test_a_clean_stack_scores_well(self):
        runs = [arc_run(10.0, z) for z in range(6)]
        twist, stretch = sf.grid_quality(sf.grid_from_runs(runs, 20))
        self.assertLess(twist, 1.0)
        self.assertLess(stretch, 1.2)

    def test_stretch_catches_a_run_from_somewhere_else(self):
        """A chain that linked two runs on opposite sides of the part: the
        twist can look innocent while the surface flies across it."""
        runs = [arc_run(10.0, 0.0), arc_run(10.0, 1.0),
                arc_run(10.0, 2.0) + np.array([80.0, 0.0, 0.0])]
        _twist, stretch = sf.grid_quality(sf.grid_from_runs(runs, 16))
        self.assertGreater(stretch, 3.0)

    def test_degenerate_grid(self):
        self.assertEqual(sf.grid_quality(np.zeros((1, 1, 3))), (0.0, 1.0))


if __name__ == "__main__":
    unittest.main()
