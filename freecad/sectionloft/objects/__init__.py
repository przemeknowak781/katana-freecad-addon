"""Parametric document objects.

Thin wrappers: read properties, call :mod:`freecad.sectionloft.core`, assign
``Shape``.  No geometry logic lives here - that way the algorithm stays testable
without a document, and the objects stay small enough to audit.

The classes must be importable at document-load time or restored objects come
back dead, so they live in modules and are never defined inside a script.
"""

from .section_set import SectionSet, make_section_set
from .fitted_sections import FittedSections, make_fitted_sections
from .section_loft import SectionLoft, make_section_loft

__all__ = [
    "SectionSet", "make_section_set",
    "FittedSections", "make_fitted_sections",
    "SectionLoft", "make_section_loft",
]
