"""``SectionLoft`` - fitted section wires in, a solid or a shell out."""

import numpy as np

import FreeCAD as App
import Part

from .common import ViewProviderBase, add_property, value, warn

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
        add_property(obj, "App::PropertyBool", "SkipInvalid", GROUP_SURFACE,
                     "Drop a chain whose loft comes out invalid instead of "
                     "putting broken geometry in the result", default=True)
        add_property(obj, "App::PropertyFloat", "Volume", GROUP_INFO,
                     "Volume of the result in mm3, 0 for a shell",
                     read_only=True)
        add_property(obj, "App::PropertyIntegerList", "InvalidChains",
                     GROUP_INFO, "Chains whose loft was rejected as invalid",
                     read_only=True)
        add_property(obj, "App::PropertyFloat", "MeshVolumeRatio", GROUP_INFO,
                     "Result volume divided by the volume of the source mesh. "
                     "Meaningful only when the mesh is a solid body: for a thin "
                     "wall the mesh encloses just the material, so an envelope "
                     "is legitimately many times larger", read_only=True)
        add_property(obj, "App::PropertyFloat", "SectionVolumeRatio", GROUP_INFO,
                     "Result volume divided by the volume its own sections "
                     "imply. 1.0 means the surface follows the sections it was "
                     "built from; this is the number that catches a loft which "
                     "has folded over itself", read_only=True)
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

    @staticmethod
    def usable(shape, solid):
        """``(shape, reason)`` - a repaired shape, or None and why it was dropped.

        ``makeLoft`` returns something even for input it cannot really handle:
        on a scanned mesh two of seven chains came back as self-intersecting
        wires with an unorientable shell, one reporting a volume of minus
        eighteen million cubic millimetres.  Handing that over labelled as a
        result is worse than saying nothing.

        Validity alone is not enough of a test.  ``fix()`` will happily turn a
        self-intersecting loft into a *valid* shape enclosing no volume at all,
        so a solid also has to enclose something positive: a negative volume
        means the shell came out inside-out, and zero means it collapsed.
        """
        if shape is None or shape.isNull():
            return None, "empty"

        candidate = shape
        if not candidate.isValid():
            try:
                repaired = shape.copy()
                repaired.fix(1e-7, 1e-7, 1e-7)
                candidate = repaired if repaired.isValid() else None
            except Exception:  # noqa: BLE001 - fix() is best effort
                candidate = None
            if candidate is None:
                return None, "invalid geometry (self-intersecting or unorientable)"

        if solid:
            # A negative volume is an inside-out shell, not a bad one: the sign
            # depends on which way makeLoft happened to orient the surface.  Flip
            # it rather than throwing away geometry that is otherwise correct.
            if candidate.Volume < 0.0:
                try:
                    flipped = candidate.copy()
                    flipped.reverse()
                    if flipped.isValid() and flipped.Volume > 0.0:
                        candidate = flipped
                except Exception:  # noqa: BLE001
                    pass
            if candidate.Volume <= 1e-9:
                return None, ("encloses no volume (%.3f mm3), the sections do "
                              "not bound a body" % candidate.Volume)
        return candidate, None

    @staticmethod
    def source_mesh(obj):
        """Walk SectionLoft -> FittedSections -> SectionSet -> Mesh."""
        node = getattr(obj, "Sections", None)
        for _ in range(4):
            if node is None:
                return None
            mesh = getattr(node, "Mesh", None)
            if mesh is not None:
                return mesh
            node = getattr(node, "Source", None)
        return None

    @staticmethod
    def section_volume(obj):
        """Volume the section wires imply, by the trapezoid rule.

        Each wire is turned into a face to get its area, the areas are
        integrated along the loft direction, and the answer is what the solid
        would measure if the surface simply joined its sections without folding.
        Unlike a comparison against the mesh this works for a thin wall too,
        where the mesh encloses only the material and an envelope is
        legitimately many times bigger.
        """
        sections = getattr(obj, "Sections", None)
        shape = getattr(sections, "Shape", None)
        if shape is None or shape.isNull() or len(shape.Wires) < 2:
            return 0.0

        sizes = [int(n) for n in (getattr(sections, "ChainSizes", []) or [])]
        wires = list(shape.Wires)
        if not sizes or sum(sizes) != len(wires):
            sizes = [len(wires)]

        total = 0.0
        index = 0
        for size in sizes:
            chain = wires[index:index + size]
            index += size
            samples = []
            for wire in chain:
                try:
                    face = Part.Face(wire)
                    area = abs(face.Area)
                    centre = face.CenterOfMass
                    normal = face.normalAt(0, 0)
                except Exception:  # noqa: BLE001 - a non-planar wire has no face
                    continue
                samples.append((np.array([centre.x, centre.y, centre.z]),
                                np.array([normal.x, normal.y, normal.z]), area))
            for k in range(len(samples) - 1):
                (centre_a, normal_a, area_a) = samples[k]
                (centre_b, _, area_b) = samples[k + 1]
                step = abs(float(np.dot(centre_b - centre_a, normal_a)))
                total += 0.5 * (area_a + area_b) * step
        return total

    def check_against_sections(self, obj):
        implied = self.section_volume(obj)
        if implied <= 1e-9:
            return 0.0
        ratio = float(obj.Volume) / implied
        if not 0.75 <= ratio <= 1.25:
            warn("%s: the surface encloses %.2fx the volume its own sections "
                 "imply - it is folding over itself somewhere"
                 % (obj.Name, ratio))
        return ratio

    def check_against_mesh(self, obj):
        """Compare the result volume with the mesh it came from.

        Every other number this object reports can look healthy while the result
        is nonsense: on a thin shell with slots the chain produced six valid
        solids, no failed sections and a 0.3 mm fit deviation, and the geometry
        was a starburst of shards enclosing 248 times the volume of the mesh.
        Fit deviation measures curves against contours; nothing measured the
        surface against the object. This is the cheapest number that does.
        """
        mesh = self.source_mesh(obj)
        if mesh is None or not mesh.isSolid() or mesh.Volume <= 0:
            return 0.0
        return float(obj.Volume) / float(mesh.Volume)

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
        invalid = []
        for index, wires in enumerate(chains):
            if len(wires) < 2:
                failures.append("chain %d has fewer than 2 sections" % index)
                continue
            try:
                profiles = self.apply_caps(wires, obj)
                result = Part.makeLoft(profiles, solid, bool(obj.Ruled),
                                       bool(obj.Closed))
            except Exception as exc:  # noqa: BLE001
                failures.append("chain %d: %s" % (index, exc))
                continue

            if bool(obj.SkipInvalid):
                result, reason = self.usable(result, solid)
                if result is None:
                    invalid.append(index)
                    failures.append("chain %d: %s" % (index, reason))
                    continue
            shapes.append(result)

        if not shapes:
            obj.Status = "loft failed: %s" % ("; ".join(failures) or "no input")
            warn("%s: %s" % (obj.Name, obj.Status))
            return

        result = shapes[0] if len(shapes) == 1 else Part.Compound(shapes)
        obj.Shape = result
        obj.Volume = float(result.Volume) if solid else 0.0
        obj.InvalidChains = [int(i) for i in invalid]
        obj.Status = "lofted %d of %d chains%s" % (
            len(shapes), len(chains),
            ", volume %.1f mm3" % obj.Volume if solid else " (shell)")
        if invalid:
            obj.Status += ", %d rejected as invalid" % len(invalid)

        obj.MeshVolumeRatio = self.check_against_mesh(obj) if solid else 0.0
        obj.SectionVolumeRatio = self.check_against_sections(obj) if solid else 0.0
        if obj.SectionVolumeRatio and not 0.75 <= obj.SectionVolumeRatio <= 1.25:
            obj.Status += (", WARNING %.2fx the volume its sections imply"
                           % obj.SectionVolumeRatio)
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
