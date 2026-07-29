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
