"""Slicing and the full chain.  Requires FreeCAD - run with freecadcmd."""

import unittest

import numpy as np

import Part

from freecad.sectionloft.core import contours as ct
from freecad.sectionloft.core import pipeline as pp
from freecad.sectionloft.core.fitting import FitParams
from freecad.sectionloft.tests import fixtures as fx


class TestSliceSphere(unittest.TestCase):
    RADIUS = 50.0

    @classmethod
    def setUpClass(cls):
        cls.mesh = fx.sphere_mesh(cls.RADIUS, 80)
        cls.params = pp.SliceParams(count=9, inset=0.05)
        cls.sections = pp.slice_mesh(cls.mesh, cls.params)

    def test_section_count(self):
        self.assertEqual(len(self.sections), 9)

    def test_one_closed_contour_each(self):
        for section in self.sections:
            self.assertEqual(len(section.contours), 1, "section %d" % section.index)
            self.assertTrue(section.contours[0].closed)

    def test_radii_match_the_analytic_sphere(self):
        for section in self.sections:
            pts = section.contours[0].points
            z = float(pts[:, 2].mean())
            expected = np.sqrt(max(self.RADIUS ** 2 - z ** 2, 0.0))
            measured = np.linalg.norm(pts[:, :2], axis=1).mean()
            self.assertLess(abs(measured - expected) / expected, 0.01,
                            "z=%.2f expected %.3f got %.3f" % (z, expected, measured))

    def test_all_contours_are_ccw(self):
        from freecad.sectionloft.core.planes import orthonormal_frame
        for section in self.sections:
            contour = section.contours[0]
            u, v, _ = orthonormal_frame(section.normal)
            area = ct.signed_area(ct.to_plane_coords(contour.points, section.base,
                                                     u, v))
            self.assertGreater(area, 0.0)

    def test_seam_points_share_an_axial_plane(self):
        starts = np.array([s.contours[0].points[0] for s in self.sections])
        self.assertLess(np.abs(starts[:, 1]).max(), 0.5)
        self.assertTrue((starts[:, 0] > 0).all())

    def test_oblique_direction_still_slices(self):
        params = pp.SliceParams(direction=(1.0, 1.0, 1.0), count=5, inset=0.1)
        sections = pp.slice_mesh(self.mesh, params)
        self.assertEqual(len(sections), 5)
        for section in sections:
            self.assertGreaterEqual(len(section.contours), 1)


class TestVertexRowPlane(unittest.TestCase):
    """A sphere cut at its equator lands exactly in a ring of mesh vertices -
    the case where crossSections walks the ring out and back."""

    def test_equator_gives_a_usable_contour(self):
        from freecad.sectionloft.core.planes import orthonormal_frame
        mesh = fx.sphere_mesh(50.0, 80)
        sections = pp.slice_mesh(mesh, pp.SliceParams(count=1, inset=0.0))
        self.assertEqual(len(sections), 1)
        contours = sections[0].contours
        self.assertEqual(len(contours), 1)

        pts = contours[0].points
        u, v, _ = orthonormal_frame(sections[0].normal)
        area = ct.signed_area(ct.to_plane_coords(pts, sections[0].base, u, v))
        self.assertGreater(area, 0.9 * np.pi * 50.0 ** 2)

    def test_the_same_plane_without_the_nudge_is_rejected_not_broken(self):
        mesh = fx.sphere_mesh(50.0, 80)
        sections = pp.slice_mesh(mesh, pp.SliceParams(count=1, inset=0.0,
                                                      avoid_vertex_rows=False))
        self.assertEqual(sections[0].contours, [])
        self.assertGreater(sections[0].rejected, 0)


class TestMinContourLength(unittest.TestCase):
    def test_short_contours_are_rejected(self):
        mesh = fx.sphere_mesh(50.0, 60)
        params = pp.SliceParams(count=5, inset=0.05, min_contour_length=1000.0)
        sections = pp.slice_mesh(mesh, params)
        self.assertTrue(all(len(s.contours) == 0 for s in sections))
        self.assertTrue(all(s.rejected > 0 for s in sections))

    def test_normal_contours_survive(self):
        mesh = fx.sphere_mesh(50.0, 60)
        sections = pp.slice_mesh(mesh, pp.SliceParams(count=5, inset=0.05))
        self.assertTrue(all(len(s.contours) == 1 for s in sections))
        self.assertTrue(all(s.rejected == 0 for s in sections))


class TestMultiContour(unittest.TestCase):
    def test_two_cylinders_give_two_contours_per_section(self):
        mesh = fx.two_cylinders_mesh()
        sections = pp.slice_mesh(mesh, pp.SliceParams(count=5, inset=0.1))
        for section in sections:
            self.assertEqual(len(section.contours), 2, "section %d" % section.index)

    def test_primary_picks_one_and_fitting_warns(self):
        mesh = fx.two_cylinders_mesh()
        sections = pp.slice_mesh(mesh, pp.SliceParams(count=3, inset=0.1))
        fits = pp.fit_sections(sections, FitParams(tolerance=0.3))
        self.assertTrue(all(f is not None and f.ok for f in fits))


class TestAutoTolerance(unittest.TestCase):
    def test_derives_from_median_edge_length(self):
        mesh = fx.sphere_mesh(50.0, 60)
        median = pp.mesh_median_edge_length(mesh)
        self.assertGreater(median, 0.0)
        tol = pp.auto_tolerance(mesh, 0.5)
        self.assertAlmostEqual(tol, min(max(0.5 * median, 0.01), 2.0), places=9)

    def test_clamped_to_limits(self):
        mesh = fx.sphere_mesh(50.0, 60)
        self.assertEqual(pp.auto_tolerance(mesh, 1e-9), pp.TOLERANCE_LIMITS[0])
        self.assertEqual(pp.auto_tolerance(mesh, 1e9), pp.TOLERANCE_LIMITS[1])


class TestFullChain(unittest.TestCase):
    def test_cylinder_loft_volume(self):
        """The real acceptance criterion for v0.1: do the fitted sections loft
        into a solid whose volume matches the analytic one?"""
        radius, height = 20.0, 80.0
        mesh = fx.cylinder_mesh(radius, height, 72)
        # Explicit tolerance: this test is about the fitting chain, not about
        # the auto-tolerance heuristic, which on a coarse mesh is deliberately
        # loose enough to move the radius by more than 0.5% of the volume.
        report = pp.run(mesh,
                        pp.SliceParams(count=10, inset=0.02, auto_tolerance=False),
                        FitParams(tolerance=0.05))

        self.assertEqual(report.failed_sections, [])
        wires = report.fitted_shape.Wires
        self.assertEqual(len(wires), 10)

        solid = Part.makeLoft(wires, True, False, False)
        span = height * (1.0 - 2 * 0.02)
        expected = np.pi * radius ** 2 * span
        self.assertLess(abs(solid.Volume - expected) / expected, 0.005,
                        "volume %.1f expected %.1f" % (solid.Volume, expected))

    def test_status_and_diagnostics_are_populated(self):
        mesh = fx.sphere_mesh(50.0, 60)
        report = pp.run(mesh, pp.SliceParams(count=8, inset=0.05))
        self.assertTrue(report.status)
        self.assertEqual(len(report.contour_count), 8)
        self.assertGreater(report.tolerance, 0.0)
        self.assertLess(report.max_deviation, report.tolerance)

    def test_recompute_with_more_sections_changes_the_result(self):
        mesh = fx.sphere_mesh(50.0, 60)
        few = pp.run(mesh, pp.SliceParams(count=6, inset=0.05))
        many = pp.run(mesh, pp.SliceParams(count=18, inset=0.05))
        self.assertEqual(len(few.fitted_shape.Wires), 6)
        self.assertEqual(len(many.fitted_shape.Wires), 18)


class TestGracefulFailure(unittest.TestCase):
    def test_no_mesh(self):
        report = pp.run(None)
        self.assertEqual(report.status, "no mesh")
        self.assertEqual(report.failed_sections, [])

    def test_mesh_with_holes_does_not_raise(self):
        mesh = fx.holed_mesh()
        report = pp.run(mesh, pp.SliceParams(count=6, inset=0.1))
        self.assertTrue(report.status)
        self.assertIsNotNone(report.fitted_shape)

    def test_document_objects(self):
        import FreeCAD as App
        doc = App.newDocument("SectionLoftTest")
        try:
            report = pp.run(fx.cylinder_mesh(20.0, 60.0, 48),
                            pp.SliceParams(count=5, inset=0.05))
            objects = pp.add_to_document(doc, report)
            self.assertEqual(len(objects), 2)
            self.assertFalse(objects[1].Shape.isNull())
        finally:
            App.closeDocument(doc.Name)


if __name__ == "__main__":
    unittest.main()
