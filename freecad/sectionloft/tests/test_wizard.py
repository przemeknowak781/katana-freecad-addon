"""Wizard logic, driven headless.

The panel never imports FreeCADGui, so it can be built against an offscreen Qt
application and its controls exercised like any other object.  That covers the
part that actually breaks - property plumbing and feedback - even though nobody
is looking at the widgets.
"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import FreeCAD as App

try:
    from PySide import QtCore, QtWidgets
except ImportError:
    from PySide6 import QtCore, QtWidgets

from freecad.sectionloft.gui.wizard import (SectionLoftWizard,
                                            factor_to_fidelity,
                                            fidelity_to_factor, plural)
from freecad.sectionloft.tests import fixtures as fx

_app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class TestFidelityMapping(unittest.TestCase):
    def test_ends_of_the_slider(self):
        self.assertAlmostEqual(fidelity_to_factor(0), 2.0)
        self.assertAlmostEqual(fidelity_to_factor(100), 0.2)

    def test_round_trip(self):
        for percent in (0, 25, 50, 75, 100):
            self.assertEqual(factor_to_fidelity(fidelity_to_factor(percent)),
                             percent)

    def test_accurate_end_is_the_tighter_tolerance(self):
        self.assertLess(fidelity_to_factor(100), fidelity_to_factor(0))


class TestPlural(unittest.TestCase):
    FORMS = ("bryła", "bryły", "brył")

    def test_singular(self):
        self.assertEqual(plural(1, *self.FORMS), "bryła")

    def test_few(self):
        for n in (2, 3, 4, 22, 23, 104):
            self.assertEqual(plural(n, *self.FORMS), "bryły", "n=%d" % n)

    def test_many(self):
        for n in (0, 5, 9, 11, 12, 13, 14, 25, 111):
            self.assertEqual(plural(n, *self.FORMS), "brył", "n=%d" % n)


class WizardCase(unittest.TestCase):
    def setUp(self):
        self.doc = App.newDocument("SectionLoftWizard")
        self.mesh = self.doc.addObject("Mesh::Feature", "Mesh")
        self.mesh.Mesh = fx.cylinder_mesh(20.0, 80.0, 48)
        self.wizard = SectionLoftWizard(self.doc, self.mesh)

    def tearDown(self):
        if not self.wizard.finished:
            self.wizard.reject()
        App.closeDocument(self.doc.Name)


class TestWizardSetup(WizardCase):
    def test_creates_the_whole_chain(self):
        self.assertEqual(len(self.sections_wires()), 12)
        self.assertGreater(len(self.wizard.fitted.Shape.Wires), 0)
        self.assertGreater(self.wizard.loft.Volume, 0.0)

    def sections_wires(self):
        return self.wizard.sections.Shape.Wires

    def test_default_direction_follows_the_longest_axis(self):
        # The fixture cylinder is 80 long in Z and 40 across.
        self.assertAlmostEqual(self.wizard.sections.Direction.z, 1.0)

    def test_starts_on_step_one(self):
        self.assertEqual(self.wizard.step, 0)
        self.assertIn("Krok 1 z 3", self.wizard.header.text())
        self.assertFalse(self.wizard.back_button.isEnabled())


class TestWizardControls(WizardCase):
    def test_count_slider_changes_the_sections(self):
        self.wizard.count_slider.setValue(20)
        self.wizard.recompute()
        self.assertEqual(self.wizard.sections.Count, 20)
        self.assertEqual(len(self.wizard.sections.Shape.Wires), 20)
        self.assertEqual(self.wizard.count_label.text(), "20")

    def test_direction_buttons_are_exclusive(self):
        self.wizard._set_direction((1, 0, 0))
        self.assertTrue(self.wizard.direction_buttons["X"].isChecked())
        self.assertFalse(self.wizard.direction_buttons["Auto"].isChecked())
        self.assertAlmostEqual(self.wizard.sections.Direction.x, 1.0)

    def test_fidelity_slider_tightens_the_fit(self):
        self.wizard.fidelity_slider.setValue(0)
        self.wizard.recompute()
        loose = self.wizard.fitted.ToleranceUsed

        self.wizard.fidelity_slider.setValue(100)
        self.wizard.recompute()
        self.assertLess(self.wizard.fitted.ToleranceUsed, loose)
        self.assertLess(self.wizard.fitted.MaxDeviation,
                        self.wizard.fitted.ToleranceUsed)

    def test_corner_checkbox(self):
        self.wizard.corner_check.setChecked(False)
        self.assertFalse(self.wizard.fitted.CornerDetection)

    def test_cap_combo_writes_the_enum_not_the_label(self):
        self.wizard.start_cap.setCurrentIndex(2)      # "Szpic"
        self.assertEqual(str(self.wizard.loft.StartCap), "Point")

    def test_solid_toggle(self):
        self.wizard.shell_radio.setChecked(True)
        self.wizard.recompute()
        self.assertFalse(self.wizard.loft.Solid)
        self.assertEqual(self.wizard.loft.Volume, 0.0)

    def test_advanced_section_is_folded_away_by_default(self):
        """Not merely disabled - hidden.  A greyed-out block still occupies the
        panel and pushes the deviation readout out of view."""
        self.assertFalse(self.wizard.advanced_box.isChecked())
        self.assertFalse(self.wizard.advanced_body.isVisibleTo(
            self.wizard.advanced_box))

        self.wizard.advanced_box.setChecked(True)
        self.assertTrue(self.wizard.advanced_body.isVisibleTo(
            self.wizard.advanced_box))

    def test_advanced_controls_reach_the_objects(self):
        self.wizard.inset_spin.setValue(10)
        self.wizard.seam_combo.setCurrentText("MinTravel")
        self.wizard.recompute()
        self.assertEqual(self.wizard.sections.Inset, 10)
        self.assertEqual(str(self.wizard.fitted.SeamMode), "MinTravel")


class TestWizardNavigation(WizardCase):
    def test_steps_move_and_clamp(self):
        self.wizard._show_step(1)
        self.assertEqual(self.wizard.step, 1)
        self.assertIn("Dopasowanie", self.wizard.header.text())
        self.wizard._show_step(5)
        self.assertEqual(self.wizard.step, 2)
        self.assertFalse(self.wizard.next_button.isEnabled())
        self.wizard._show_step(-3)
        self.assertEqual(self.wizard.step, 0)

    def test_feedback_reports_deviation_on_step_two(self):
        self.wizard._show_step(1)
        self.assertIn("Odchyłka", self.wizard.feedback.text())
        self.assertIn("mm", self.wizard.feedback.text())

    def test_feedback_reports_the_loft_on_step_three(self):
        self.wizard._show_step(2)
        self.assertIn("lofted", self.wizard.feedback.text())


class TestTaskDialogProtocol(WizardCase):
    def test_standard_buttons_are_a_plain_int(self):
        """FreeCAD calls this and passes the result to C++.  Under PySide6 the
        flag combination is an enum that int() rejects, which only shows up in a
        real GUI - hence this test."""
        buttons = self.wizard.getStandardButtons()
        self.assertIsInstance(buttons, int)
        self.assertGreater(buttons, 0)


class TestWizardOutcome(WizardCase):
    def test_accept_keeps_the_objects(self):
        names = [self.wizard.sections.Name, self.wizard.fitted.Name,
                 self.wizard.loft.Name]
        self.wizard.accept()
        for name in names:
            self.assertIsNotNone(self.doc.getObject(name))

    def test_closing_the_panel_without_answering_is_a_cancel(self):
        """Gui.Control.closeDialog() - Escape, a workbench switch, another
        dialog taking over - deletes the widget without calling reject().
        Verified against a real FreeCAD GUI: the objects survived."""
        names = [self.wizard.sections.Name, self.wizard.fitted.Name,
                 self.wizard.loft.Name]
        form = self.wizard.form
        self.wizard.form = None
        form.deleteLater()
        # deleteLater only queues the deletion; the DeferredDelete event has to
        # be delivered before `destroyed` fires.
        _app.processEvents()
        QtCore.QCoreApplication.sendPostedEvents(
            None, QtCore.QEvent.Type.DeferredDelete)
        _app.processEvents()
        self.assertTrue(self.wizard.finished,
                        "the destroyed signal did not reach the panel")
        for name in names:
            self.assertIsNone(self.doc.getObject(name),
                              "%s survived the panel being closed" % name)

    def test_answering_twice_is_harmless(self):
        self.wizard.accept()
        self.wizard.accept()
        self.wizard.reject()
        self.assertIsNotNone(self.doc.getObject(self.wizard.loft.Name),
                             "a late reject undid an accepted result")

    def test_cancel_removes_everything_it_made(self):
        names = [self.wizard.sections.Name, self.wizard.fitted.Name,
                 self.wizard.loft.Name]
        self.wizard.reject()
        for name in names:
            self.assertIsNone(self.doc.getObject(name),
                              "%s survived a cancel" % name)
        # The mesh the user selected is not ours to delete.
        self.assertIsNotNone(self.doc.getObject(self.mesh.Name))


class TestWizardOnMultiBodyMesh(unittest.TestCase):
    def test_two_cylinders_are_reported_as_separate_bodies(self):
        doc = App.newDocument("SectionLoftWizardMulti")
        try:
            mesh = doc.addObject("Mesh::Feature", "Mesh")
            mesh.Mesh = fx.two_cylinders_mesh()
            wizard = SectionLoftWizard(doc, mesh)
            wizard.count_slider.setValue(8)
            wizard.recompute()
            wizard._show_step(2)
            self.assertEqual(len(list(wizard.fitted.ChainSizes)), 2)
            self.assertIn("2 rozdzielone bryły", wizard.feedback.text())
            wizard.reject()
        finally:
            App.closeDocument(doc.Name)


if __name__ == "__main__":
    unittest.main()
