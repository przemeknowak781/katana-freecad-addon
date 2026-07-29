"""Domain logic, free of FreeCAD GUI and of the document model.

``planes``, ``contours`` and ``polyline`` are pure numpy and import under a
plain CPython interpreter.  ``fitting`` and ``pipeline`` need ``Part`` / ``Mesh``
and therefore only run inside FreeCAD (``freecadcmd`` is enough).
"""
