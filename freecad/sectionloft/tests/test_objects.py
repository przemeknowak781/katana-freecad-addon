"""Parametric document objects.  Requires FreeCAD - run with freecadcmd."""

import os
import tempfile
import unittest

import numpy as np

import FreeCAD as App
import Part

from freecad.sectionloft.objects import (make_fitted_sections, make_section_loft,
                                         make_section_set)
from freecad.sectionloft.tests import fixtures as fx


def build_chain(doc, mesh=None, count=10):
    mesh_obj = doc.addObject("Mesh::Feature", "Mesh")
    mesh_obj.Mesh = mesh if mesh is not None else fx.cylinder_mesh(20.0, 80.0, 64)
    sections = make_section_set(doc, mesh_obj)
    sections.Count = count
    fitted = make_fitted_sections(doc, sections)
    loft = make_section_loft(doc, fitted)
    doc.recompute()
    return mesh_obj, sections, fitted, loft


class DocumentCase(unittest.TestCase):
    def setUp(self):
        self.doc = App.newDocument("SectionLoftObjects")

    def tearDown(self):
        App.closeDocument(self.doc.Name)


class TestSectionSet(DocumentCase):
    def test_produces_one_wire_per_plane(self):
        _, sections, _, _ = build_chain(self.doc)
        self.assertEqual(len(sections.Shape.Wires), 10)
        self.assertEqual(list(sections.ContourCount), [1] * 10)
        self.assertIn("10 planes", sections.Status)

    def test_direction_is_normalised_on_input(self):
        _, sections, _, _ = build_chain(self.doc)
        sections.Direction = App.Vector(0, 0, 7)
        self.assertAlmostEqual(sections.Direction.Length, 1.0)

    def test_missing_source_is_reported_not_raised(self):
        obj = make_section_set(self.doc)
        self.doc.recompute()
        self.assertIn("no mesh", obj.Status)

    def test_spacing_mode(self):
        _, sections, _, _ = build_chain(self.doc)
        sections.Mode = "Spacing"
        sections.Spacing = 20.0
        self.doc.recompute()
        offsets = list(sections.PlaneOffsets)
        self.assertGreater(len(offsets), 2)
        gaps = np.diff(offsets)
        np.testing.assert_allclose(gaps, 20.0, atol=1e-6)


class TestFittedSections(DocumentCase):
    def test_fits_every_section(self):
        _, _, fitted, _ = build_chain(self.doc)
        self.assertEqual(len(fitted.Shape.Wires), 10)
        self.assertEqual(list(fitted.FailedSections), [])
        self.assertGreater(fitted.ToleranceUsed, 0.0)
        self.assertLess(fitted.MaxDeviation, fitted.ToleranceUsed)

    def test_seam_is_smooth(self):
        _, _, fitted, _ = build_chain(self.doc)
        self.assertLess(fitted.MaxSeamKinkFound, 3.0)

    def test_wires_are_closed_and_spline_based(self):
        _, _, fitted, _ = build_chain(self.doc)
        for wire in fitted.Shape.Wires:
            self.assertTrue(wire.isClosed())
        curves = {type(e.Curve).__name__ for e in fitted.Shape.Edges}
        self.assertIn("BSplineCurve", curves)

    def test_manual_tolerance_is_respected(self):
        _, _, fitted, _ = build_chain(self.doc)
        fitted.AutoTolerance = False
        fitted.Tolerance = 0.05
        self.doc.recompute()
        self.assertAlmostEqual(fitted.ToleranceUsed, 0.05, places=6)
        self.assertLess(fitted.MaxDeviation, 0.05)

    def test_accepts_a_plain_wire_compound(self):
        """Source is documented as 'a SectionSet or any compound of wires'."""
        wires = []
        for z in (0.0, 10.0, 20.0):
            pts = [App.Vector(*p) for p in fx.circle(radius=10.0, n=40, z=z)]
            wires.append(Part.makePolygon(pts + [pts[0]]))
        plain = self.doc.addObject("Part::Feature", "Plain")
        plain.Shape = Part.Compound(wires)
        fitted = make_fitted_sections(self.doc, plain)
        self.doc.recompute()
        self.assertEqual(len(fitted.Shape.Wires), 3)
        self.assertEqual(list(fitted.ChainSizes), [3])


class TestSectionLoft(DocumentCase):
    def test_volume_matches_the_analytic_cylinder(self):
        _, sections, fitted, loft = build_chain(self.doc)
        fitted.AutoTolerance = False
        fitted.Tolerance = 0.05
        self.doc.recompute()
        span = 80.0 * (1.0 - 2 * sections.Inset / 100.0)
        expected = np.pi * 20.0 ** 2 * span
        self.assertLess(abs(loft.Volume - expected) / expected, 0.005)
        self.assertTrue(loft.Shape.isValid())

    def test_shell_when_an_end_is_left_open(self):
        _, _, _, loft = build_chain(self.doc)
        loft.StartCap = "None"
        self.doc.recompute()
        self.assertEqual(loft.Volume, 0.0)
        self.assertIn("shell", loft.Status)

    def test_point_cap_converges_to_a_tip(self):
        def planar_faces(shape):
            return sum(1 for f in shape.Faces
                       if type(f.Surface).__name__ == "Plane")

        _, _, fitted, loft = build_chain(self.doc)
        self.assertEqual(planar_faces(loft.Shape), 2)
        apex = fitted.Shape.Wires[0].CenterOfMass

        loft.StartCap = "Point"
        self.doc.recompute()
        self.assertTrue(loft.Shape.isValid())
        # The flat cap is gone and the surface actually reaches the apex.
        self.assertEqual(planar_faces(loft.Shape), 1)
        self.assertLess(Part.Vertex(apex).distToShape(loft.Shape)[0], 1e-6)

    def test_ruled_surface(self):
        _, _, _, loft = build_chain(self.doc)
        loft.Ruled = True
        self.doc.recompute()
        self.assertTrue(loft.Shape.isValid())


class TestMultipleBodies(DocumentCase):
    def test_two_cylinders_give_two_chains_and_two_solids(self):
        _, _, fitted, loft = build_chain(self.doc, fx.two_cylinders_mesh(), 8)
        self.assertEqual(list(fitted.ChainSizes), [8, 8])
        self.assertEqual(len(loft.Shape.Solids), 2)
        self.assertTrue(loft.Shape.isValid())

    def test_each_solid_stays_over_its_own_cylinder(self):
        _, _, _, loft = build_chain(self.doc, fx.two_cylinders_mesh(), 8)
        centres = sorted(s.CenterOfMass.x for s in loft.Shape.Solids)
        self.assertLess(abs(centres[0] - 0.0), 2.0)
        self.assertLess(abs(centres[1] - 40.0), 2.0)


class TestRecomputeChain(DocumentCase):
    def test_changing_count_propagates_to_the_loft(self):
        _, sections, fitted, loft = build_chain(self.doc)
        before = (len(fitted.Shape.Wires), loft.Volume)

        sections.Count = 20
        self.doc.recompute()

        self.assertEqual(len(sections.Shape.Wires), 20)
        self.assertEqual(len(fitted.Shape.Wires), 20)
        self.assertNotEqual(len(fitted.Shape.Wires), before[0])
        self.assertGreater(loft.Volume, 0.0)

    def test_changing_tolerance_propagates(self):
        _, _, fitted, loft = build_chain(self.doc)
        fitted.AutoTolerance = False
        fitted.Tolerance = 1.5
        self.doc.recompute()
        coarse = fitted.MaxDeviation
        fitted.Tolerance = 0.05
        self.doc.recompute()
        self.assertLess(fitted.MaxDeviation, coarse)


class TestSerialization(DocumentCase):
    def test_objects_come_back_parametric(self):
        path = os.path.join(tempfile.gettempdir(), "sectionloft_roundtrip.FCStd")
        _, sections, fitted, loft = build_chain(self.doc)
        volume_before = loft.Volume
        names = (sections.Name, fitted.Name, loft.Name)
        self.doc.saveAs(path)
        App.closeDocument(self.doc.Name)

        self.doc = App.openDocument(path)
        try:
            reloaded_sections = self.doc.getObject(names[0])
            reloaded_fitted = self.doc.getObject(names[1])
            reloaded_loft = self.doc.getObject(names[2])

            for obj in (reloaded_sections, reloaded_fitted, reloaded_loft):
                self.assertIsNotNone(obj.Proxy,
                                     "%s came back dead" % obj.Name)
                self.assertTrue(hasattr(obj.Proxy, "execute"))

            # The real test of "still parametric": change an input and see the
            # far end of the chain move.
            reloaded_sections.Count = 15
            self.doc.recompute()
            self.assertEqual(len(reloaded_fitted.Shape.Wires), 15)
            self.assertAlmostEqual(reloaded_loft.Volume, volume_before,
                                   delta=0.02 * volume_before)
        finally:
            os.unlink(path)

    def test_missing_properties_are_restored(self):
        """A document saved by an older version must not break when a property
        is added later."""
        _, sections, _, _ = build_chain(self.doc)
        sections.removeProperty("AvoidVertexRows")
        self.assertFalse(hasattr(sections, "AvoidVertexRows"))

        sections.Proxy.onDocumentRestored(sections)
        self.assertTrue(hasattr(sections, "AvoidVertexRows"))
        self.assertTrue(sections.AvoidVertexRows)


class TestGracefulFailure(DocumentCase):
    def test_holed_mesh_does_not_raise(self):
        _, sections, fitted, loft = build_chain(self.doc, fx.holed_mesh(), 6)
        self.assertTrue(sections.Status)
        self.assertTrue(fitted.Status)
        self.assertTrue(loft.Status)

    def test_a_broken_source_keeps_the_previous_shape(self):
        _, sections, fitted, _ = build_chain(self.doc)
        good = len(fitted.Shape.Wires)
        sections.Count = -5          # nonsense, clamped to a single plane
        self.doc.recompute()
        self.assertGreaterEqual(len(fitted.Shape.Wires), 1)
        self.assertGreater(good, 0)


if __name__ == "__main__":
    unittest.main()
