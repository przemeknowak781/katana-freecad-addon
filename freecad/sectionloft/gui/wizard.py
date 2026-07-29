# -*- coding: utf-8 -*-
"""Three-step task panel: sections, fit, surface.

Two decisions shape everything here.

*The preview is the real thing.*  The panel creates the actual parametric
objects when it opens and edits their properties as the user moves a control.
There is no separate preview code path that could disagree with the result, and
pressing Finish means keeping what is already on screen.  Cancel rolls the whole
lot back through a document transaction.

*The panel does not import FreeCADGui.*  It takes a document and a mesh object
and touches ``ViewObject`` only when there is one.  That keeps the wizard
testable headless, which is the only way its logic gets tested at all.
"""

import numpy as np

import FreeCAD as App

try:
    from PySide import QtCore, QtWidgets
except ImportError:  # standalone PySide6, e.g. under a bare interpreter
    from PySide6 import QtCore, QtWidgets

from ..core.planes import longest_bbox_axis
from ..objects import make_fitted_sections, make_section_loft, make_section_set

#: The fidelity slider runs from smooth to accurate; these are the tolerance
#: factors at its ends.  2.0 smooths through most of the triangulation, 0.2
#: follows the mesh closely enough to inherit its faceting.
FIDELITY_RANGE = (2.0, 0.2)

#: Milliseconds of quiet before a recompute.  Dragging a slider must not queue
#: one recompute per pixel.
DEBOUNCE_MS = 250

STEPS = ("Przekroje", "Dopasowanie", "Powierzchnia")

CAP_LABELS = [("Płaska", "Planar"), ("Otwarta", "None"), ("Szpic", "Point")]


def plural(count, one, few, many):
    """Polish numeral agreement: 1 bryła, 2 bryły, 5 brył.

    Worth the eight lines - "2 rozdzielonych brył" in a panel reads as
    machine-translated, and this tool is meant to look like it was written by
    someone who speaks the language of the person using it.
    """
    n = abs(int(count))
    if n == 1:
        return one
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return few
    return many


def fidelity_to_factor(percent):
    """Slider position (0-100) to ToleranceFactor."""
    low, high = FIDELITY_RANGE
    return low + (high - low) * (float(percent) / 100.0)


def factor_to_fidelity(factor):
    low, high = FIDELITY_RANGE
    return int(round(100.0 * (float(factor) - low) / (high - low)))


class SectionLoftWizard:
    """Task panel driving a SectionSet / FittedSections / SectionLoft chain."""

    def __init__(self, doc, mesh_object):
        self.doc = doc
        self.mesh_object = mesh_object
        self.step = 0
        self.finished = False
        self._transaction_open = True

        self.doc.openTransaction("SectionLoft")
        self.sections = make_section_set(doc, mesh_object)
        self.fitted = make_fitted_sections(doc, self.sections)
        self.loft = make_section_loft(doc, self.fitted)

        bbox = mesh_object.Mesh.BoundBox
        self.sections.Direction = App.Vector(*longest_bbox_axis(
            (bbox.XMin, bbox.YMin, bbox.ZMin), (bbox.XMax, bbox.YMax, bbox.ZMax)))

        self._timer = QtCore.QTimer()
        self._timer.setSingleShot(True)
        self._timer.setInterval(DEBOUNCE_MS)
        self._timer.timeout.connect(self.recompute)

        self.form = self._build()
        self._show_step(0)
        self.recompute()

    # -- construction ------------------------------------------------------

    def _build(self):
        form = QtWidgets.QWidget()
        form.setWindowTitle("SectionLoft")
        layout = QtWidgets.QVBoxLayout(form)

        self.header = QtWidgets.QLabel()
        font = self.header.font()
        font.setBold(True)
        self.header.setFont(font)
        layout.addWidget(self.header)

        self.pages = QtWidgets.QStackedWidget()
        self.pages.addWidget(self._page_sections())
        self.pages.addWidget(self._page_fit())
        self.pages.addWidget(self._page_surface())
        layout.addWidget(self.pages)

        self.feedback = QtWidgets.QLabel()
        self.feedback.setWordWrap(True)
        layout.addWidget(self.feedback)

        # FreeCAD calls reject() for Cancel, but Gui.Control.closeDialog() -
        # pressing Escape, switching workbench, another dialog taking over -
        # simply deletes the widget.  Without this the user is left with three
        # orphan objects and, worse, an open transaction that swallows whatever
        # they do next.
        form.destroyed.connect(self._on_destroyed)

        navigation = QtWidgets.QHBoxLayout()
        self.back_button = QtWidgets.QPushButton("< Wstecz")
        self.next_button = QtWidgets.QPushButton("Dalej >")
        self.back_button.clicked.connect(lambda: self._show_step(self.step - 1))
        self.next_button.clicked.connect(lambda: self._show_step(self.step + 1))
        navigation.addWidget(self.back_button)
        navigation.addWidget(self.next_button)
        layout.addLayout(navigation)

        layout.addStretch(1)
        return form

    def _page_sections(self):
        page = QtWidgets.QWidget()
        layout = QtWidgets.QFormLayout(page)

        layout.addRow("Siatka:", QtWidgets.QLabel(self.mesh_object.Label))

        buttons = QtWidgets.QHBoxLayout()
        self.direction_buttons = {}
        for label, vector in (("Auto", None), ("X", (1, 0, 0)),
                              ("Y", (0, 1, 0)), ("Z", (0, 0, 1))):
            button = QtWidgets.QToolButton()
            button.setText(label)
            button.setCheckable(True)
            button.clicked.connect(
                lambda checked=False, v=vector: self._set_direction(v))
            buttons.addWidget(button)
            self.direction_buttons[label] = button
        self.direction_buttons["Auto"].setChecked(True)
        layout.addRow("Kierunek cięcia:", buttons)

        self.count_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.count_slider.setRange(4, 40)
        self.count_slider.setValue(int(self.sections.Count))
        self.count_label = QtWidgets.QLabel(str(int(self.sections.Count)))
        self.count_slider.valueChanged.connect(self._on_count)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(self.count_slider)
        row.addWidget(self.count_label)
        layout.addRow("Liczba przekrojów:", row)

        self.envelope_check = QtWidgets.QCheckBox(
            "Obwiednia zamiast konturów")
        self.envelope_check.setToolTip(
            "Zastępuje kontury przekroju ich zewnętrznym obrysem. Włącz dla "
            "części cienkościennych i wszystkiego ze szczelinami - przekrój "
            "takiej części to wstęga, której nie da się zloftować.")
        self.envelope_check.toggled.connect(self._on_envelope)
        layout.addRow(self.envelope_check)

        self.clearance_spin = QtWidgets.QDoubleSpinBox()
        self.clearance_spin.setRange(0.0, 50.0)
        self.clearance_spin.setSingleStep(0.1)
        self.clearance_spin.setDecimals(2)
        self.clearance_spin.setSuffix(" mm")
        self.clearance_spin.setToolTip(
            "Odsunięcie obwiedni na zewnątrz. Powierzchnia rozpięta między "
            "płaszczyznami wcina się w część tam, gdzie ta wybrzusza się "
            "pomiędzy nimi.")
        self.clearance_spin.valueChanged.connect(
            lambda v: self._set(self.sections, "Clearance", v))
        self.clearance_row = QtWidgets.QLabel("Luz:")
        layout.addRow(self.clearance_row, self.clearance_spin)
        self._enable_clearance(False)

        hint = QtWidgets.QLabel(
            "Mniej przekrojów daje gładszą powierzchnię. Dokładanie ich "
            "prawie zawsze psuje loft.")
        hint.setWordWrap(True)
        layout.addRow(hint)
        return page

    def _enable_clearance(self, enabled):
        self.clearance_spin.setEnabled(enabled)
        self.clearance_row.setEnabled(enabled)

    def _on_envelope(self, checked):
        """Envelope mode brings its own sensible companions.

        A ruled surface interpolates linearly between sections, which is what
        makes the envelope actually contain the part; a smooth one dips inside
        between planes.  A small inset keeps the outermost planes off the
        silhouette while still reaching the ends.
        """
        self.sections.ContourMode = "Envelope" if checked else "All"
        self._enable_clearance(checked)
        if checked:
            self.sections.Inset = 1
            self.loft.Ruled = True
            self.ruled_check.setChecked(True)
            if self.clearance_spin.value() == 0.0:
                self.clearance_spin.setValue(0.3)
        self._timer.start()

    def _page_fit(self):
        page = QtWidgets.QWidget()
        layout = QtWidgets.QFormLayout(page)

        self.fidelity_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.fidelity_slider.setRange(0, 100)
        self.fidelity_slider.setValue(
            factor_to_fidelity(self.fitted.ToleranceFactor))
        self.fidelity_slider.valueChanged.connect(self._on_fidelity)
        ends = QtWidgets.QHBoxLayout()
        ends.addWidget(QtWidgets.QLabel("Gładko"))
        ends.addStretch(1)
        ends.addWidget(QtWidgets.QLabel("Dokładnie"))
        layout.addRow("Wierność:", self.fidelity_slider)
        layout.addRow("", ends)

        self.corner_check = QtWidgets.QCheckBox("Zachowaj narożniki")
        self.corner_check.setChecked(bool(self.fitted.CornerDetection))
        self.corner_check.toggled.connect(self._on_corners)
        layout.addRow(self.corner_check)

        layout.addRow(self._advanced())
        return page

    def _advanced(self):
        """Collapsible box of expert parameters.

        A checkable QGroupBox on its own only *disables* its children - they
        still take up the panel, greyed out, and push the deviation readout off
        the bottom.  Verified in a real FreeCAD window.  So the contents live in
        an inner widget whose visibility follows the check state, and the box
        genuinely folds away.
        """
        box = QtWidgets.QGroupBox("Zaawansowane")
        box.setCheckable(True)
        box.setChecked(False)
        outer = QtWidgets.QVBoxLayout(box)
        outer.setContentsMargins(0, 0, 0, 0)

        outer.setSpacing(0)

        self.advanced_body = QtWidgets.QWidget()
        self.advanced_body.setVisible(False)
        box.toggled.connect(self.advanced_body.setVisible)
        outer.addWidget(self.advanced_body)

        layout = QtWidgets.QFormLayout(self.advanced_body)
        layout.setContentsMargins(0, 6, 0, 0)

        self.degree_spin = QtWidgets.QSpinBox()
        self.degree_spin.setRange(3, 8)
        self.degree_spin.setValue(int(self.fitted.DegreeMax))
        self.degree_spin.valueChanged.connect(
            lambda v: self._set(self.fitted, "DegreeMax", v))
        layout.addRow("Maksymalny stopień:", self.degree_spin)

        self.corner_angle_spin = QtWidgets.QDoubleSpinBox()
        self.corner_angle_spin.setRange(1.0, 179.0)
        self.corner_angle_spin.setSuffix(" °")
        self.corner_angle_spin.setValue(float(self.fitted.CornerAngle.Value))
        self.corner_angle_spin.valueChanged.connect(
            lambda v: self._set(self.fitted, "CornerAngle", v))
        layout.addRow("Próg narożnika:", self.corner_angle_spin)

        self.decimate_check = QtWidgets.QCheckBox("Decymacja przed dopasowaniem")
        self.decimate_check.setChecked(bool(self.fitted.Decimate))
        self.decimate_check.toggled.connect(
            lambda v: self._set(self.fitted, "Decimate", bool(v)))
        layout.addRow(self.decimate_check)

        self.seam_combo = QtWidgets.QComboBox()
        self.seam_combo.addItems(["None", "Axis", "Guide", "MinTravel"])
        self.seam_combo.setCurrentText(str(self.fitted.SeamMode))
        self.seam_combo.currentTextChanged.connect(
            lambda v: self._set(self.fitted, "SeamMode", v))
        layout.addRow("Szew:", self.seam_combo)

        self.inset_spin = QtWidgets.QSpinBox()
        self.inset_spin.setRange(0, 45)
        self.inset_spin.setSuffix(" %")
        self.inset_spin.setValue(int(self.sections.Inset))
        self.inset_spin.valueChanged.connect(
            lambda v: self._set(self.sections, "Inset", v))
        layout.addRow("Odsunięcie skrajnych:", self.inset_spin)
        self.advanced_box = box
        return box

    def _page_surface(self):
        page = QtWidgets.QWidget()
        layout = QtWidgets.QFormLayout(page)

        self.solid_radio = QtWidgets.QRadioButton("Bryła")
        self.shell_radio = QtWidgets.QRadioButton("Powłoka")
        # An explicit button group rather than two loose radios: with autoExclusive
        # alone, whether the property gets written depends on which button the
        # click landed on, and the two can drift out of step.
        self.result_group = QtWidgets.QButtonGroup(page)
        self.result_group.addButton(self.solid_radio)
        self.result_group.addButton(self.shell_radio)
        self.solid_radio.setChecked(bool(self.loft.Solid))
        self.shell_radio.setChecked(not bool(self.loft.Solid))
        self.result_group.buttonToggled.connect(
            lambda *_: self._set(self.loft, "Solid",
                                 bool(self.solid_radio.isChecked())))
        row = QtWidgets.QHBoxLayout()
        row.addWidget(self.solid_radio)
        row.addWidget(self.shell_radio)
        layout.addRow("Wynik:", row)

        self.start_cap = self._cap_combo("StartCap")
        self.end_cap = self._cap_combo("EndCap")
        layout.addRow("Początek:", self.start_cap)
        layout.addRow("Koniec:", self.end_cap)

        self.ruled_check = QtWidgets.QCheckBox("Powierzchnia prostokreślna")
        self.ruled_check.setChecked(bool(self.loft.Ruled))
        self.ruled_check.toggled.connect(
            lambda v: self._set(self.loft, "Ruled", bool(v)))
        layout.addRow(self.ruled_check)
        return page

    def _cap_combo(self, property_name):
        combo = QtWidgets.QComboBox()
        for label, value in CAP_LABELS:
            combo.addItem(label, value)
        current = str(getattr(self.loft, property_name))
        combo.setCurrentIndex(max(0, [v for _, v in CAP_LABELS].index(current)))
        combo.currentIndexChanged.connect(
            lambda index, name=property_name, c=combo:
            self._set(self.loft, name, c.itemData(index)))
        return combo

    # -- interaction -------------------------------------------------------

    def _set(self, obj, name, value):
        setattr(obj, name, value)
        self._timer.start()

    def _set_direction(self, vector):
        if vector is None:
            bbox = self.mesh_object.Mesh.BoundBox
            vector = longest_bbox_axis((bbox.XMin, bbox.YMin, bbox.ZMin),
                                       (bbox.XMax, bbox.YMax, bbox.ZMax))
            active = "Auto"
        else:
            active = {(1, 0, 0): "X", (0, 1, 0): "Y", (0, 0, 1): "Z"}[vector]
        for label, button in self.direction_buttons.items():
            button.setChecked(label == active)
        self._set(self.sections, "Direction", App.Vector(*vector))

    def _on_count(self, value):
        self.count_label.setText(str(int(value)))
        self._set(self.sections, "Count", int(value))

    def _on_fidelity(self, value):
        self._set(self.fitted, "ToleranceFactor", fidelity_to_factor(value))

    def _on_corners(self, checked):
        self._set(self.fitted, "CornerDetection", bool(checked))

    def _show_step(self, index):
        self.step = max(0, min(index, len(STEPS) - 1))
        self.pages.setCurrentIndex(self.step)
        self.header.setText("Krok %d z %d - %s"
                            % (self.step + 1, len(STEPS), STEPS[self.step]))
        self.back_button.setEnabled(self.step > 0)
        self.next_button.setEnabled(self.step < len(STEPS) - 1)
        # The loft only makes sense once its inputs have been seen; showing it
        # from step one buries the sections it is built from.
        self._set_visible(self.loft, self.step == 2)
        self._set_visible(self.fitted, True)
        self._set_visible(self.sections, self.step == 0)
        self._update_feedback()

    @staticmethod
    def _set_visible(obj, visible):
        view = getattr(obj, "ViewObject", None)
        if view is not None:
            view.Visibility = bool(visible)

    # -- feedback ----------------------------------------------------------

    def recompute(self):
        self.doc.recompute()
        self._update_feedback()

    def _update_feedback(self):
        if self.step == 0:
            counts = list(self.sections.ContourCount)
            multiple = sum(1 for c in counts if c > 1)
            text = self.sections.Status
            if multiple:
                text += "\n%d %s %s więcej niż jeden kontur - powstaną " \
                        "osobne bryły." % (
                            multiple,
                            plural(multiple, "przekrój", "przekroje",
                                   "przekrojów"),
                            plural(multiple, "ma", "mają", "ma"))
        elif self.step == 1:
            text = ("Odchyłka od siatki: %.3f mm (tolerancja %.3f mm)"
                    % (self.fitted.MaxDeviation, self.fitted.ToleranceUsed))
            if list(self.fitted.FailedSections):
                text += "\nNie udało się dopasować przekrojów: %s" % (
                    list(self.fitted.FailedSections),)
            if self.fitted.MaxSeamKinkFound > 5.0:
                text += ("\nZałamanie na szwie %.1f° - przesuń suwak w stronę "
                         "Dokładnie." % self.fitted.MaxSeamKinkFound)
        else:
            text = self.loft.Status
            chains = len(list(self.fitted.ChainSizes))
            if chains > 1:
                text += "\n%d %s %s." % (
                    chains,
                    plural(chains, "rozdzielona", "rozdzielone", "rozdzielonych"),
                    plural(chains, "bryła", "bryły", "brył"))
        self.feedback.setText(text)

    # -- task dialog protocol ---------------------------------------------

    def getStandardButtons(self):
        # FreeCAD wants a plain int.  Under PySide6 the flag combination is a
        # StandardButton enum that int() refuses to convert, so the value has to
        # be unwrapped first; under PySide2 it already is an int.
        box = QtWidgets.QDialogButtonBox
        buttons = box.Ok | box.Cancel
        return int(getattr(buttons, "value", buttons))

    def accept(self):
        if self.finished:
            return True
        self._timer.stop()
        self.recompute()
        for obj in (self.sections, self.fitted):
            self._set_visible(obj, False)
        self._set_visible(self.loft, True)
        self._end_transaction(commit=True)
        self.finished = True
        self._close()
        return True

    def reject(self):
        if self.finished:
            return True
        self._rollback()
        self.finished = True
        self._close()
        return True

    def _on_destroyed(self, *_):
        """The panel went away without an answer - treat it as a cancel."""
        if not self.finished:
            self._rollback()
            self.finished = True

    def _rollback(self):
        self._timer.stop()
        self._end_transaction(commit=False)
        # Aborting should take the objects with it, but the abort is a no-op if
        # the transaction was already closed, so the tree is checked either way
        # rather than trusting it.
        for obj in (self.loft, self.fitted, self.sections):
            try:
                if self.doc.getObject(obj.Name) is not None:
                    self.doc.removeObject(obj.Name)
            except Exception:  # noqa: BLE001 - object already gone
                pass
        self.doc.recompute()

    def _end_transaction(self, commit):
        if not self._transaction_open:
            return
        self._transaction_open = False
        if commit:
            self.doc.commitTransaction()
        else:
            self.doc.abortTransaction()

    @staticmethod
    def _close():
        # freecadcmd provides a FreeCADGui stub that imports fine but has no
        # Control, so the attribute has to be checked, not just the import.
        try:
            import FreeCADGui as Gui
        except ImportError:
            return
        control = getattr(Gui, "Control", None)
        if control is not None:
            control.closeDialog()
