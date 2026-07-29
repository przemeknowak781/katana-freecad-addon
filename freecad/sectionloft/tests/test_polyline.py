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


class TestUniformSplits(unittest.TestCase):
    """Making every section of a chain split into the same number of pieces."""

    def rectangle(self):
        pts = pl.douglas_peucker(fx.rectangle(40, 30, 12), 0.01, closed=True)
        return pts, pl.detect_corners(pts, np.deg2rad(30.0), closed=True)

    def test_strongest_corners_keeps_the_sharpest(self):
        pts, corners = self.rectangle()
        kept = pl.strongest_corners(pts, corners, 2, closed=True)
        self.assertEqual(len(kept), 2)
        self.assertTrue(set(kept).issubset(set(corners)))

    def test_strongest_corners_is_a_no_op_below_the_limit(self):
        pts, corners = self.rectangle()
        self.assertEqual(pl.strongest_corners(pts, corners, 10, closed=True),
                         corners)

    def test_padding_reaches_the_target_by_inserting_points(self):
        """A decimated rectangle is four points long, so the target can only be
        reached by adding vertices - choosing among the existing ones caps the
        count at four."""
        pts, corners = self.rectangle()
        self.assertEqual(len(pts), 4)
        for target in (4, 6, 9, 13):
            padded, splits = pl.pad_split_points(pts, corners, target,
                                                 closed=True)
            self.assertEqual(len(splits), target, "target %d" % target)
            self.assertEqual(len(pl.split_at_corners(padded, splits, True)),
                             target)

    def test_inserted_points_lie_on_the_original_polyline(self):
        pts, corners = self.rectangle()
        padded, _ = pl.pad_split_points(pts, corners, 11, closed=True)
        self.assertLess(pl.max_deviation(padded, pts, closed=True), 1e-9)

    def test_padding_keeps_every_detected_corner(self):
        pts, corners = self.rectangle()
        padded, splits = pl.pad_split_points(pts, corners, 9, closed=True)
        kept = [tuple(np.round(padded[i], 9)) for i in splits]
        for corner in corners:
            self.assertIn(tuple(np.round(pts[corner], 9)), kept)

    def test_padding_a_contour_with_no_corners(self):
        circle = fx.circle(radius=10.0, n=8)
        padded, splits = pl.pad_split_points(circle, [], 5, closed=True)
        self.assertEqual(len(splits), 5)
        self.assertLess(pl.max_deviation(padded, circle, closed=True), 1e-9)

    def test_padding_below_the_existing_count_is_a_no_op(self):
        pts, corners = self.rectangle()
        padded, splits = pl.pad_split_points(pts, corners, 2, closed=True)
        self.assertEqual(splits, corners)
        np.testing.assert_allclose(padded, pts)


class TestSharedCorners(unittest.TestCase):
    """Envelope sections are sampled at the same angles, so a corner can be
    named by its index and agreed on across the whole chain."""

    @staticmethod
    def profile(radius, corner_at=None, n=72, bump=1.6):
        angles = np.linspace(0, 2 * np.pi, n, endpoint=False)
        radii = np.full(n, float(radius))
        if corner_at is not None:
            radii[corner_at] *= bump
        return np.column_stack((radii * np.cos(angles), radii * np.sin(angles),
                                np.zeros(n)))

    def test_a_corner_every_section_agrees_on_is_kept(self):
        profiles = [self.profile(10.0 + i * 0.2, corner_at=18)
                    for i in range(6)]
        indices = pl.common_corner_indices(profiles, np.deg2rad(30.0))
        self.assertIn(18, indices)

    def test_one_section_alone_does_not_crease_the_chain(self):
        profiles = [self.profile(10.0) for _ in range(6)]
        profiles[2] = self.profile(10.0, corner_at=40)
        indices = pl.common_corner_indices(profiles, np.deg2rad(30.0),
                                           min_fraction=0.5)
        self.assertNotIn(40, indices)

    def test_a_corner_drifting_by_a_sample_still_counts(self):
        profiles = [self.profile(10.0, corner_at=18 + (i % 2))
                    for i in range(6)]
        indices = pl.common_corner_indices(profiles, np.deg2rad(30.0))
        self.assertTrue(any(abs(i - 18) <= 2 for i in indices))

    def test_broad_peaks_yield_one_corner_not_five(self):
        profiles = [self.profile(10.0, corner_at=18) for _ in range(6)]
        indices = pl.common_corner_indices(profiles, np.deg2rad(20.0))
        close = [i for i in indices if min(abs(i - 18), 72 - abs(i - 18)) <= 3]
        self.assertLessEqual(len(close), 1)

    def test_mismatched_lengths_are_refused(self):
        self.assertEqual(
            pl.common_corner_indices([self.profile(10.0, n=72),
                                      self.profile(10.0, n=36)],
                                     np.deg2rad(30.0)), [])


class TestCornerTracking(unittest.TestCase):
    """A crease on a curved part drifts angularly from section to section."""

    @staticmethod
    def profile(corners, n=72, radius=10.0, bump=1.6):
        angles = np.linspace(0, 2 * np.pi, n, endpoint=False)
        radii = np.full(n, float(radius))
        for index in corners:
            radii[index % n] *= bump
        return np.column_stack((radii * np.cos(angles), radii * np.sin(angles),
                                np.zeros(n)))

    def test_follows_a_drifting_crease(self):
        profiles = [self.profile([10 + i, 40 + i]) for i in range(8)]
        lines = pl.track_corner_lines(profiles, np.deg2rad(30.0), window=4)
        self.assertEqual(len(lines), 8)
        self.assertTrue(all(len(row) == len(lines[0]) for row in lines),
                        "every section must split into the same count")
        first = [row[0] for row in lines]
        self.assertEqual(max(first) - min(first), 7,
                         "the tracked line should move with the crease")

    def test_sections_that_miss_a_crease_carry_it_through(self):
        profiles = [self.profile([10, 40]) for _ in range(8)]
        profiles[4] = self.profile([])          # a section with no corner at all
        lines = pl.track_corner_lines(profiles, np.deg2rad(30.0))
        self.assertEqual(len(lines), 8)
        self.assertEqual(lines[4], lines[3],
                         "the crease should run straight through the gap")

    def test_a_crease_nobody_else_sees_is_dropped(self):
        profiles = [self.profile([10, 40]) for _ in range(8)]
        profiles[2] = self.profile([10, 40, 60])
        lines = pl.track_corner_lines(profiles, np.deg2rad(30.0),
                                      min_fraction=0.5)
        self.assertEqual(len(lines[0]), 2)

    def test_nothing_to_track(self):
        profiles = [self.profile([]) for _ in range(5)]
        self.assertEqual(pl.track_corner_lines(profiles, np.deg2rad(30.0)), [])

    def test_mismatched_lengths_are_refused(self):
        self.assertEqual(
            pl.track_corner_lines([self.profile([10]), self.profile([10], n=36)],
                                  np.deg2rad(30.0)), [])


class TestSplitAtIndices(unittest.TestCase):
    def test_splits_where_it_is_told(self):
        pts = fx.circle(radius=10.0, n=40)
        segments = pl.split_at_indices(pts, [0, 10, 20, 30])
        self.assertEqual(len(segments), 4)
        for segment in segments:
            self.assertEqual(len(segment), 11)

    def test_segments_chain_end_to_start(self):
        pts = fx.circle(radius=10.0, n=40)
        segments = pl.split_at_indices(pts, [3, 17, 29])
        for i, segment in enumerate(segments):
            np.testing.assert_allclose(segment[-1],
                                       segments[(i + 1) % len(segments)][0])

    def test_keeps_full_resolution(self):
        """Unlike corner splitting, this must not thin the polyline - the
        indices refer to the points as given."""
        pts = fx.circle(radius=10.0, n=40)
        total = sum(len(s) - 1 for s in pl.split_at_indices(pts, [0, 20]))
        self.assertEqual(total, 40)

    def test_too_few_indices_is_a_no_op(self):
        pts = fx.circle(radius=10.0, n=20)
        self.assertEqual(len(pl.split_at_indices(pts, [5])), 1)


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
