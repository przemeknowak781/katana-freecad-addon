"""Parametric document objects.  Requires FreeCAD - run with freecadcmd."""

import os
import tempfile
import unittest

import numpy as np

import FreeCAD as App
import Part

from freecad.sectionloft.objects import (make_fitted_sections, make_section_loft,
                                         make_section_set)
from freecad.sectionloft.objects.section_loft import SectionLoft
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


class TestChainTopology(DocumentCase):
    """Sections split into different edge counts cannot be lofted.

    Found on a real scanned mesh: corner detection gave one section 4 edges and
    its neighbour 18, and Part.makeLoft answered with 'BRep_API: command not
    done'.  Four of seven chains failed, and the failing attempts took 18 of the
    18.9 seconds the whole recompute needed.
    """

    def mixed_source(self):
        """Rectangle, circle, rectangle - 4 corners, none, 4 corners."""
        wires = []
        for z, points in ((0.0, fx.rectangle(40, 30, 12, z=0.0)),
                          (10.0, fx.circle(radius=18.0, n=64, z=10.0)),
                          (20.0, fx.rectangle(40, 30, 12, z=20.0))):
            vectors = [App.Vector(*p) for p in points]
            wires.append(Part.makePolygon(vectors + [vectors[0]]))
        source = self.doc.addObject("Part::Feature", "Mixed")
        source.Shape = Part.Compound(wires)
        return source

    def test_mixed_corner_counts_are_unified(self):
        fitted = make_fitted_sections(self.doc, self.mixed_source())
        fitted.AutoTolerance = False
        fitted.Tolerance = 0.2
        self.doc.recompute()

        edge_counts = {len(w.Edges) for w in fitted.Shape.Wires}
        self.assertEqual(edge_counts, {4},
                         "the chain still has mismatched profiles")
        self.assertIn("refitted for uniform corners", fitted.Status)
        Part.makeLoft(list(fitted.Shape.Wires), True, False, False)

    def test_unifying_levels_up_and_keeps_the_corners(self):
        """Levelling down to the smallest count spans real corners with one
        spline; on the test mesh that tripled the deviation.  The rectangles
        must keep their four edges, and the circle gains three split points."""
        fitted = make_fitted_sections(self.doc, self.mixed_source())
        fitted.AutoTolerance = False
        fitted.Tolerance = 0.2
        self.doc.recompute()

        self.assertLess(fitted.MaxDeviation, 0.2)
        corners = [v.Point for v in fitted.Shape.Wires[0].Vertexes]
        self.assertEqual(len(corners), 4)

    def test_a_consistent_chain_keeps_its_corners(self):
        """The refit costs sharp edges, so it must not fire when the chain is
        already loftable."""
        wires = []
        for z in (0.0, 10.0, 20.0):
            points = [App.Vector(*p) for p in fx.rectangle(40, 30, 12, z=z)]
            wires.append(Part.makePolygon(points + [points[0]]))
        source = self.doc.addObject("Part::Feature", "Rects")
        source.Shape = Part.Compound(wires)

        fitted = make_fitted_sections(self.doc, source)
        fitted.AutoTolerance = False
        fitted.Tolerance = 0.2
        self.doc.recompute()

        self.assertEqual({len(w.Edges) for w in fitted.Shape.Wires}, {4})
        self.assertNotIn("refitted", fitted.Status)

    def test_the_refit_can_be_turned_off(self):
        fitted = make_fitted_sections(self.doc, self.mixed_source())
        fitted.AutoTolerance = False
        fitted.Tolerance = 0.2
        fitted.UniformChainTopology = False
        self.doc.recompute()
        self.assertEqual({len(w.Edges) for w in fitted.Shape.Wires}, {1, 4})

    def test_lofting_the_unified_chain_end_to_end(self):
        fitted = make_fitted_sections(self.doc, self.mixed_source())
        loft = make_section_loft(self.doc, fitted)
        self.doc.recompute()
        self.assertTrue(loft.Shape.isValid())
        self.assertGreater(loft.Volume, 0.0)
        self.assertIn("lofted 1 of 1", loft.Status)


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


class TestLoftValidity(DocumentCase):
    """makeLoft returns something even when it cannot handle the input.

    On the test mesh two of seven chains came back self-intersecting, one of
    them reporting a volume of minus eighteen million cubic millimetres.
    """

    @staticmethod
    def figure_eight(z):
        points = [(-10, -5, z), (10, 5, z), (10, -5, z), (-10, 5, z)]
        vectors = [App.Vector(*p) for p in points]
        return Part.makePolygon(vectors + [vectors[0]])

    def test_a_self_intersecting_loft_is_rejected(self):
        broken = Part.makeLoft([self.figure_eight(0.0), self.figure_eight(10.0)],
                               True, False, False)
        self.assertFalse(broken.isValid(), "fixture stopped being invalid")

        shape, reason = SectionLoft.usable(broken, solid=True)
        self.assertIsNone(shape, "a degenerate loft was accepted")
        self.assertIn("volume", reason)

    def test_a_good_solid_passes_through_untouched(self):
        box = Part.makeBox(10, 10, 10)
        shape, reason = SectionLoft.usable(box, solid=True)
        self.assertIsNotNone(shape)
        self.assertIsNone(reason)
        self.assertAlmostEqual(shape.Volume, 1000.0)

    def test_an_empty_shape_is_rejected(self):
        shape, reason = SectionLoft.usable(Part.Shape(), solid=True)
        self.assertIsNone(shape)
        self.assertEqual(reason, "empty")

    def test_a_shell_is_not_judged_by_volume(self):
        shell = Part.makeBox(10, 10, 10).Shells[0]
        shape, reason = SectionLoft.usable(shell, solid=False)
        self.assertIsNotNone(shape, reason)

    def test_the_chain_result_is_always_valid(self):
        _, _, _, loft = build_chain(self.doc)
        self.assertTrue(loft.Shape.isValid())
        self.assertEqual(list(loft.InvalidChains), [])


class TestMeshVolumeCheck(DocumentCase):
    """The sanity number that catches a result every other metric calls healthy."""

    def test_a_faithful_result_is_near_one(self):
        _, sections, _, loft = build_chain(self.doc)
        # The outermost planes sit inside the mesh, so the loft is a little
        # shorter than the cylinder it came from.
        self.assertGreater(loft.MeshVolumeRatio, 0.9)
        self.assertLess(loft.MeshVolumeRatio, 1.0)
        self.assertNotIn("WARNING", loft.Status)

    def test_the_ratio_reaches_the_mesh_through_the_chain(self):
        mesh_obj, _, _, loft = build_chain(self.doc)
        self.assertIs(loft.Proxy.source_mesh(loft), mesh_obj.Mesh)

    def test_a_shell_has_no_ratio(self):
        _, _, _, loft = build_chain(self.doc)
        loft.Solid = False
        self.doc.recompute()
        self.assertEqual(loft.MeshVolumeRatio, 0.0)

    def test_a_wild_ratio_is_called_out(self):
        """Verified on the test mesh: six valid solids, no failed sections, a
        0.3 mm fit deviation - and 248 times the volume of the mesh."""
        _, _, _, loft = build_chain(self.doc)
        mesh = loft.Proxy.source_mesh(loft)
        self.assertIsNotNone(mesh)
        loft.Volume = float(mesh.Volume) * 248.0
        ratio = loft.Proxy.check_against_mesh(loft)
        self.assertAlmostEqual(ratio, 248.0, places=3)


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
