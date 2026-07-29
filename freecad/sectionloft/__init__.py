"""SectionLoft - mesh cross-sections to fitted B-spline sections to loft.

v0.1 is algorithm-only: no GUI, no parametric document objects.  Everything
lives in :mod:`freecad.sectionloft.core` and is driven either from the macro in
``macro/`` or directly from the FreeCAD Python console.
"""

from .version import __version__

__all__ = ["__version__"]
