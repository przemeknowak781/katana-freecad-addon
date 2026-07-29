# -*- coding: utf-8 -*-
"""Workbench definition.

Namespace-package style (``freecad.sectionloft``), not the legacy ``InitGui.py``
that FreeCAD executes with ``exec``.  The difference matters for more than
tidiness: only this form can be imported by a plain interpreter, which is what
makes the test suite possible.
"""

import os

import FreeCADGui as Gui

ICON_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons")


class SectionLoftWorkbench(Gui.Workbench):
    MenuText = "SectionLoft"
    ToolTip = "Siatka w bryłę NURBS przez rodzinę przekrojów"
    Icon = os.path.join(ICON_DIR, "SectionLoft_Workbench.svg")

    def Initialize(self):
        # Imported here, not at module level: FreeCAD builds every workbench
        # class at startup and only calls Initialize when one is first opened.
        # Doing the work up front would make every user pay for this addon.
        from freecad.sectionloft.gui import commands

        names = commands.register()
        self.appendToolbar("SectionLoft", names)
        self.appendMenu("SectionLoft", names)

    def GetClassName(self):
        return "Gui::PythonWorkbench"


Gui.addWorkbench(SectionLoftWorkbench())
