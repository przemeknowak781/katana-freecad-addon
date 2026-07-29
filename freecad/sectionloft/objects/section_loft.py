"""``SectionLoft`` - fitted section wires in, a solid or a shell out."""

import numpy as np

import FreeCAD as App
import Part

from .common import ViewProviderBase, add_property, warn

GROUP_SOURCE = "Source"
GROUP_SURFACE = "Surface"
GROUP_CAPS = "Caps"
GROUP_INFO = "Information"


class SectionLoft:
    """Lofts each chain of paired sections separately.

    One loft per chain, compounded: a mesh whose limbs split partway up gives
    one body per limb instead of a single failed operation.
    """

    def __init__(self, obj):
        self.setup(obj)
        obj.Proxy = self

    # -- properties --------------------------------------------------------

    def setup(self, obj):
        add_property(obj, "App::PropertyLink", "Sections", GROUP_SOURCE,
                     "FittedSections to loft")
        add_property(obj, "App::PropertyLinkList", "Rails", GROUP_SOURCE,
                     "Optional guide rails (not used yet, reserved for v0.3)")
        add_property(obj, "App::PropertyBool", "Ruled", GROUP_SURFACE,
                     "Straight surface between sections instead of a smooth one",
                     default=False)
        add_property(obj, "App::PropertyBool", "Closed", GROUP_SURFACE,
                     "Join the last section back to the first", default=False)
        add_property(obj, "App::PropertyBool", "Solid", GROUP_SURFACE,
                     "Produce a solid rather than a shell", default=True)
        add_property(obj, "App::PropertyEnumeration", "StartCap", GROUP_CAPS,
                     "How the first section is closed. Point converges the "
                     "surface to a tip, for the nose of an enclosure where a "
                     "flat cap would be wrong",
                     enum=["Planar", "None", "Point"], default="Planar")
        add_property(obj, "App::PropertyEnumeration", "EndCap", GROUP_CAPS,
                     "How the last section is closed",
                     enum=["Planar", "None", "Point"], default="Planar")
        add_property(obj, "App::PropertyFloat", "Volume", GROUP_INFO,
                     "Volume of the result in mm3, 0 for a shell",
                     read_only=True)
        add_property(obj, "App::PropertyString", "Status", GROUP_INFO,
                     "Result of the last recompute", read_only=True)

    def onDocumentRestored(self, obj):
        self.setup(obj)

    # -- computation -------------------------------------------------------

    @staticmethod
    def chains(obj):
        """Wire runs to loft, one list per body."""
        sections = getattr(obj, "Sections", None)
        shape = getattr(sections, "Shape", None)
        if shape is None or shape.isNull() or not shape.Wires:
            return []

        wires = list(shape.Wires)
        sizes = [int(n) for n in (getattr(sections, "ChainSizes", []) or [])]
        if not sizes or sum(sizes) != len(wires):
            return [wires]

        chains = []
        index = 0
        for size in sizes:
            chains.append(wires[index:index + size])
            index += size
        return chains

    @staticmethod
    def apply_caps(profiles, obj):
        """Replace the end profiles with vertices where a point cap is asked for.

        ``makeLoft`` accepts a vertex as the first or last profile, which is how
        a loft converges to a tip; a planar cap is just what ``solid=True``
        already does.
        """
        profiles = list(profiles)
        if str(obj.StartCap) == "Point":
            profiles[0] = Part.Vertex(profiles[0].CenterOfMass)
        if str(obj.EndCap) == "Point":
            profiles[-1] = Part.Vertex(profiles[-1].CenterOfMass)
        return profiles

    def execute(self, obj):
        chains = self.chains(obj)
        if not chains:
            obj.Status = "no sections to loft"
            return

        solid = bool(obj.Solid)
        # A shell is what you get when an end is deliberately left open; asking
        # for a solid at the same time is a contradiction, and OCC answers it
        # with an unhelpful error rather than a question.
        if "None" in (str(obj.StartCap), str(obj.EndCap)):
            solid = False

        shapes = []
        failures = []
        for index, wires in enumerate(chains):
            if len(wires) < 2:
                failures.append("chain %d has fewer than 2 sections" % index)
                continue
            try:
                profiles = self.apply_caps(wires, obj)
                shapes.append(Part.makeLoft(profiles, solid, bool(obj.Ruled),
                                            bool(obj.Closed)))
            except Exception as exc:  # noqa: BLE001
                failures.append("chain %d: %s" % (index, exc))

        if not shapes:
            obj.Status = "loft failed: %s" % ("; ".join(failures) or "no input")
            warn("%s: %s" % (obj.Name, obj.Status))
            return

        result = shapes[0] if len(shapes) == 1 else Part.Compound(shapes)
        obj.Shape = result
        obj.Volume = float(result.Volume) if solid else 0.0
        obj.Status = "lofted %d of %d chains%s" % (
            len(shapes), len(chains),
            ", volume %.1f mm3" % obj.Volume if solid else " (shell)")
        for message in failures:
            warn("%s: %s" % (obj.Name, message))

    def dumps(self):
        return None

    def loads(self, state):
        return None

    def __getstate__(self):
        return None

    def __setstate__(self, state):
        return None


class ViewProviderSectionLoft(ViewProviderBase):
    icon = "SectionLoft_Loft.svg"


def make_section_loft(doc, sections=None, name="SectionLoft"):
    obj = doc.addObject("Part::FeaturePython", name)
    SectionLoft(obj)
    if obj.ViewObject is not None:
        ViewProviderSectionLoft(obj.ViewObject)
    if sections is not None:
        obj.Sections = sections
    return obj
