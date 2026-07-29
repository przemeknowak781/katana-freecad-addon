# -*- coding: utf-8 -*-
"""Toolbar and menu commands.

One command is the whole tool for most people - ``SectionLoft_Wizard``.  The
other four exist for the case where somebody wants to build the chain by hand
or splice their own object into the middle of it.
"""

import os

import FreeCAD as App
import FreeCADGui as Gui
import Part

from ..objects import make_fitted_sections, make_section_loft, make_section_set
from .wizard import SectionLoftWizard

ICON_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "icons")


def icon(name):
    return os.path.join(ICON_DIR, name)


def selected(kind=None, attribute=None):
    """Selected objects, optionally filtered by FreeCAD type or by attribute."""
    result = []
    for obj in Gui.Selection.getSelection():
        if kind is not None and not obj.isDerivedFrom(kind):
            continue
        if attribute is not None and not hasattr(obj, attribute):
            continue
        result.append(obj)
    return result


def active_document():
    doc = App.ActiveDocument
    if doc is None:
        App.Console.PrintError("[SectionLoft] no active document\n")
    return doc


class WizardCommand:
    """Select a mesh, answer three screens, get a solid."""

    def GetResources(self):
        return {"Pixmap": icon("SectionLoft_Wizard.svg"),
                "MenuText": "Utwórz z siatki...",
                "ToolTip": "Zamień siatkę w bryłę NURBS w trzech krokach"}

    def IsActive(self):
        return bool(App.ActiveDocument) and bool(selected("Mesh::Feature"))

    def Activated(self):
        doc = active_document()
        if doc is None:
            return
        meshes = selected("Mesh::Feature")
        if len(meshes) != 1:
            App.Console.PrintError(
                "[SectionLoft] zaznacz dokładnie jedną siatkę\n")
            return
        Gui.Control.showDialog(SectionLoftWizard(doc, meshes[0]))


class SectionsCommand:
    def GetResources(self):
        return {"Pixmap": icon("SectionLoft_Sections.svg"),
                "MenuText": "Przekroje siatki",
                "ToolTip": "Utwórz SectionSet z zaznaczonej siatki"}

    def IsActive(self):
        return bool(App.ActiveDocument) and bool(selected("Mesh::Feature"))

    def Activated(self):
        doc = active_document()
        if doc is None:
            return
        for mesh in selected("Mesh::Feature"):
            doc.openTransaction("SectionSet")
            make_section_set(doc, mesh)
            doc.commitTransaction()
        doc.recompute()


class FittedCommand:
    def GetResources(self):
        return {"Pixmap": icon("SectionLoft_Fitted.svg"),
                "MenuText": "Dopasuj krzywe",
                "ToolTip": "Utwórz FittedSections z zaznaczonych konturów"}

    def IsActive(self):
        return bool(App.ActiveDocument) and bool(selected(attribute="Shape"))

    def Activated(self):
        doc = active_document()
        if doc is None:
            return
        for source in selected(attribute="Shape"):
            doc.openTransaction("FittedSections")
            make_fitted_sections(doc, source)
            doc.commitTransaction()
        doc.recompute()


class LoftCommand:
    def GetResources(self):
        return {"Pixmap": icon("SectionLoft_Loft.svg"),
                "MenuText": "Loft z przekrojów",
                "ToolTip": "Utwórz SectionLoft z zaznaczonego FittedSections"}

    def IsActive(self):
        return bool(App.ActiveDocument) and bool(selected(attribute="Shape"))

    def Activated(self):
        doc = active_document()
        if doc is None:
            return
        for source in selected(attribute="Shape"):
            doc.openTransaction("SectionLoft")
            make_section_loft(doc, source)
            doc.commitTransaction()
        doc.recompute()


class ShowFailedCommand:
    """Draw the sections that would not fit, in red, so they can be found.

    An index in a property list tells you a section failed; it does not tell you
    where it is or what is wrong with it.  This puts the offending polylines in
    the viewport next to the geometry that caused them.
    """

    def GetResources(self):
        return {"Pixmap": icon("SectionLoft_Failed.svg"),
                "MenuText": "Pokaż nieudane przekroje",
                "ToolTip": "Wyświetl kontury, których nie udało się dopasować"}

    def IsActive(self):
        return bool(App.ActiveDocument) and bool(
            selected(attribute="FailedSections"))

    def Activated(self):
        doc = active_document()
        if doc is None:
            return
        for fitted in selected(attribute="FailedSections"):
            indices = list(fitted.FailedSections)
            if not indices:
                App.Console.PrintMessage(
                    "[SectionLoft] %s: wszystkie przekroje dopasowane\n"
                    % fitted.Label)
                continue

            source = getattr(fitted, "Source", None)
            shape = getattr(source, "Shape", None)
            if shape is None:
                continue
            counts = list(getattr(source, "ContourCount", [])) or \
                [1] * len(shape.Wires)

            wires = []
            start = 0
            for section_index, count in enumerate(counts):
                if section_index in indices:
                    wires.extend(shape.Wires[start:start + count])
                start += count
            if not wires:
                continue

            doc.openTransaction("Show failed sections")
            marker = doc.addObject("Part::Feature", fitted.Name + "_Failed")
            marker.Shape = Part.Compound(wires)
            if marker.ViewObject is not None:
                marker.ViewObject.LineColor = (1.0, 0.0, 0.0)
                marker.ViewObject.LineWidth = 4.0
            doc.commitTransaction()
            App.Console.PrintWarning(
                "[SectionLoft] %s: %d nieudanych przekrojów zaznaczonych na "
                "czerwono\n" % (fitted.Label, len(indices)))
        doc.recompute()


COMMANDS = (
    ("SectionLoft_Wizard", WizardCommand),
    ("SectionLoft_Sections", SectionsCommand),
    ("SectionLoft_Fitted", FittedCommand),
    ("SectionLoft_Loft", LoftCommand),
    ("SectionLoft_ShowFailed", ShowFailedCommand),
)


def register():
    """Register every command; returns the list of names, in toolbar order."""
    for name, cls in COMMANDS:
        Gui.addCommand(name, cls())
    return [name for name, _ in COMMANDS]
