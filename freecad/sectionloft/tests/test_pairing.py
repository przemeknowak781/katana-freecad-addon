"""Contour pairing between sections.  Pure numpy."""

import unittest

import numpy as np

from freecad.sectionloft.core import pairing as pr

Z = (0.0, 0.0, 1.0)


def stack(columns, heights):
    """centroids_per_section for bodies at fixed (x, y) positions."""
    return [np.array([[x, y, z] for x, y in columns], dtype=float)
            for z in heights]


class TestInPlaneDistance(unittest.TestCase):
    def test_ignores_the_axial_component(self):
        d = np.array([0.0, 0.0, 1.0])
        self.assertAlmostEqual(
            pr.in_plane_distance([0, 0, 0], [3, 4, 100], d), 5.0)


class TestMatchPair(unittest.TestCase):
    def test_obvious_match(self):
        previous = np.array([[0, 0, 0], [50, 0, 0]], dtype=float)
        current = np.array([[51, 0, 10], [1, 0, 10]], dtype=float)
        mapping, ambiguous = pr.match_pair(previous, current, np.array(Z))
        self.assertEqual(mapping, {0: 1, 1: 0})
        self.assertEqual(ambiguous, [])

    def test_is_one_to_one(self):
        previous = np.array([[0, 0, 0], [1, 0, 0]], dtype=float)
        current = np.array([[0.4, 0, 5]], dtype=float)
        mapping, _ = pr.match_pair(previous, current, np.array(Z))
        self.assertEqual(len(set(mapping.values())), len(mapping))

    def test_reports_ambiguity(self):
        previous = np.array([[0, 0, 0]], dtype=float)
        current = np.array([[10, 0, 5], [-11, 0, 5]], dtype=float)
        mapping, ambiguous = pr.match_pair(previous, current, np.array(Z))
        self.assertEqual(mapping, {0: 0})
        self.assertEqual(ambiguous, [0])

    def test_clear_winner_is_not_ambiguous(self):
        previous = np.array([[0, 0, 0]], dtype=float)
        current = np.array([[1, 0, 5], [100, 0, 5]], dtype=float)
        _, ambiguous = pr.match_pair(previous, current, np.array(Z))
        self.assertEqual(ambiguous, [])

    def test_empty_input(self):
        self.assertEqual(pr.match_pair([], [[0, 0, 0]], np.array(Z)), ({}, []))


def run_at(x0, x1, y, z, count=20):
    """A straight wall run along x, at height z."""
    xs = np.linspace(x0, x1, count)
    return np.column_stack((xs, np.full(count, float(y)),
                            np.full(count, float(z))))


class TestOverlap(unittest.TestCase):
    """Wall runs fragment differently from section to section, so the centroid
    of a fragment is an accident of where it broke."""

    def test_the_same_stretch_scores_high(self):
        a = run_at(0, 10, 0, 0)
        b = run_at(0, 10, 0.1, 1)
        self.assertGreater(pr.overlap_score(a, b, np.array(Z), 0.5), 0.9)

    def test_a_different_stretch_scores_zero(self):
        a = run_at(0, 10, 0, 0)
        b = run_at(40, 50, 0, 1)
        self.assertEqual(pr.overlap_score(a, b, np.array(Z), 0.5), 0.0)

    def test_partial_overlap_is_limited_by_the_smaller_share(self):
        """A short run buried inside a long one must not score as a match for
        the whole of it."""
        a = run_at(0, 20, 0, 0)
        b = run_at(0, 5, 0, 1)
        score = pr.overlap_score(a, b, np.array(Z), 0.5)
        self.assertGreater(score, 0.2)
        self.assertLess(score, 0.4)

    def test_centroids_would_have_got_this_wrong(self):
        """Two pieces of one wall, broken at different places: their centroids
        are far apart while the runs plainly overlap.

        Sampled densely on purpose - the threshold has to exceed the spacing
        between points or nothing can ever be found near anything.
        """
        a = run_at(0, 20, 0, 0, count=120)
        b = run_at(10, 30, 0, 1, count=120)
        centre_distance = np.linalg.norm(a.mean(axis=0)[:2] - b.mean(axis=0)[:2])
        self.assertGreater(centre_distance, 4.0)
        self.assertGreater(pr.overlap_score(a, b, np.array(Z), 0.5), 0.4)


class TestOverlapChains(unittest.TestCase):
    def test_a_wall_running_up_the_part_becomes_one_chain(self):
        runs = [[run_at(0, 10, 0, z)] for z in range(6)]
        chains = pr.build_chains_by_overlap(runs, Z, 0.5)
        self.assertEqual(len(chains), 1)
        self.assertEqual(len(chains[0]), 6)

    def test_two_walls_stay_apart(self):
        runs = [[run_at(0, 10, 0, z), run_at(40, 50, 0, z)] for z in range(5)]
        chains = pr.build_chains_by_overlap(runs, Z, 0.5)
        self.assertEqual(len(chains), 2)
        for chain in chains:
            self.assertEqual(len(chain), 5)

    def test_a_wall_that_starts_halfway_gets_its_own_chain(self):
        runs = [[run_at(0, 10, 0, z)] for z in range(3)]
        runs += [[run_at(0, 10, 0, z), run_at(40, 50, 0, z)] for z in range(3, 6)]
        chains = pr.build_chains_by_overlap(runs, Z, 0.5)
        self.assertEqual(sorted(len(c) for c in chains), [3, 6])

    def test_runs_too_far_apart_do_not_chain(self):
        runs = [[run_at(0, 10, 0, 0)], [run_at(0, 10, 40, 1)]]
        chains = pr.build_chains_by_overlap(runs, Z, 0.5)
        self.assertEqual(len(chains), 2)


class TestChains(unittest.TestCase):
    def test_single_body_gives_one_chain(self):
        centroids = stack([(0, 0)], [0, 10, 20, 30])
        chains, ambiguous = pr.build_chains(centroids, Z)
        self.assertEqual(len(chains), 1)
        self.assertEqual(len(chains[0]), 4)
        self.assertEqual(ambiguous, [])

    def test_two_separate_bodies_stay_separate(self):
        centroids = stack([(0, 0), (40, 0)], [0, 10, 20])
        chains, _ = pr.build_chains(centroids, Z)
        self.assertEqual(len(chains), 2)
        for chain in chains:
            self.assertEqual(len(chain), 3)
            xs = {c for _, c in chain}
            self.assertEqual(len(xs), 1, "a chain must not jump between bodies")

    def test_scrambled_contour_order_is_repaired(self):
        """The order contours come out of crossSections is not stable; pairing
        must follow geometry, not index."""
        centroids = [
            np.array([[0, 0, 0], [40, 0, 0]], dtype=float),
            np.array([[40, 0, 10], [0, 0, 10]], dtype=float),   # swapped
            np.array([[0, 0, 20], [40, 0, 20]], dtype=float),
        ]
        chains, _ = pr.build_chains(centroids, Z)
        self.assertEqual(len(chains), 2)
        for chain in chains:
            xs = [centroids[s][c][0] for s, c in chain]
            self.assertLess(max(xs) - min(xs), 1e-9)

    def test_a_limb_appearing_halfway_starts_its_own_chain(self):
        centroids = [
            np.array([[0, 0, 0]], dtype=float),
            np.array([[0, 0, 10]], dtype=float),
            np.array([[0, 0, 20], [40, 0, 20]], dtype=float),
            np.array([[0, 0, 30], [40, 0, 30]], dtype=float),
        ]
        chains, _ = pr.build_chains(centroids, Z)
        self.assertEqual(len(chains), 2)
        self.assertEqual(len(chains[0]), 4)
        self.assertEqual(len(chains[1]), 2)

    def test_a_body_that_ends_closes_its_chain(self):
        centroids = [
            np.array([[0, 0, 0], [40, 0, 0]], dtype=float),
            np.array([[0, 0, 10], [40, 0, 10]], dtype=float),
            np.array([[0, 0, 20]], dtype=float),
        ]
        chains, _ = pr.build_chains(centroids, Z)
        self.assertEqual(sorted(len(c) for c in chains), [2, 3])

    def test_empty_sections_do_not_break_the_chain_list(self):
        centroids = [np.zeros((0, 3)), np.array([[0, 0, 10]], dtype=float)]
        chains, _ = pr.build_chains(centroids, Z)
        self.assertEqual(len(chains), 1)

    def test_symmetric_split_is_flagged_as_ambiguous(self):
        centroids = [
            np.array([[0, 0, 0]], dtype=float),
            np.array([[-10, 0, 10], [10, 0, 10]], dtype=float),
        ]
        _, ambiguous = pr.build_chains(centroids, Z)
        self.assertTrue(ambiguous)


if __name__ == "__main__":
    unittest.main()
