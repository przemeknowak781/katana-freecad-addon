# -*- coding: utf-8 -*-
"""Console-mode entry point.

FreeCAD imports this in both GUI and console mode, and ``init_gui.py`` only in
GUI mode.  Nothing needs to happen here - the objects are imported on demand -
but the file has to exist for the addon to register cleanly when FreeCAD is run
headless, and this is the right place for anything that must work without a GUI.
"""
