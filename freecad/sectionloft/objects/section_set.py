"""``SectionSet`` - mesh plus a family of planes, out come polyline contours."""

import numpy as np

import FreeCAD as App
import Part

from ..core import contours as ct
from ..core import pipeline as pp
from ..core import planes as pf
from .common import ViewProviderBase, add_property, log, value, vector, warn

GROUP_SOURCE = "Source"
GROUP_PLANES = "Planes"
GROUP_FILTER = "Filtering"
GROUP_INFO = "Information"


class SectionSet:
    """Cuts a mesh with a family of parallel planes.

    Output ``Shape`` is a compound of polygon wires, in plane order.  How many
    belong to each plane is published in ``ContourCount`` - that is what lets
    ``FittedSections`` regroup them without reaching into this object's Python
    state, which would not survive a save/reload.
    """

    def __init__(self, obj):
        self.setup(obj)
        obj.Proxy = self

    # -- properties --------------------------------------------------------

    def setup(self, obj):
        add_property(obj, "App::PropertyLink", "Source", GROUP_SOURCE,
                     "Mesh to cut")
        add_property(obj, "App::PropertyBool", "AutoOrigin", GROUP_PLANES,
                     "Place the plane family at the centre of the mesh",
                     default=True)
        add_property(obj, "App::PropertyVector", "Origin", GROUP_PLANES,
                     "Base point of the plane family, used when AutoOrigin is "
                     "off")
        add_property(obj, "App::PropertyVector", "Direction", GROUP_PLANES,
                     "Plane normal; normalised automatically",
                     default=App.Vector(0, 0, 1))
        add_property(obj, "App::PropertyEnumeration", "Mode", GROUP_PLANES,
                     "Distribute a fixed number of planes, or a fixed spacing",
                     enum=["Count", "Spacing"], default="Count")
        add_property(obj, "App::PropertyInteger", "Count", GROUP_PLANES,
                     "Number of sections", default=12)
        add_property(obj, "App::PropertyLength", "Spacing", GROUP_PLANES,
                     "Distance between sections when Mode is Spacing",
                     default=10.0)
        add_property(obj, "App::PropertyBool", "AutoRange", GROUP_PLANES,
                     "Derive the range from the extent of the mesh",
                     default=True)
        add_property(obj, "App::PropertyDistance", "RangeStart", GROUP_PLANES,
                     "Start of the range along Direction, relative to Origin")
        add_property(obj, "App::PropertyDistance", "RangeEnd", GROUP_PLANES,
                     "End of the range along Direction, relative to Origin")
        add_property(obj, "App::PropertyPercent", "Inset", GROUP_PLANES,
                     "Keep the outermost planes this far inside the extent of "
                     "the mesh; cutting exactly at the silhouette gives a "
                     "degenerate contour", default=2)
        add_property(obj, "App::PropertyBool", "AvoidVertexRows", GROUP_PLANES,
                     "Nudge planes off rows of coplanar mesh vertices, where "
                     "the cross-section walks the ring twice instead of once",
                     default=True)
        add_property(obj, "App::PropertyEnumeration", "ContourMode",
                     GROUP_FILTER,
                     "All keeps every contour a plane cuts. Envelope replaces "
                     "them with their outer boundary - the right choice for "
                     "thin-walled parts, whose sections are ribbons that cannot "
                     "be lofted, and for anything with slots",
                     enum=list(pp.CONTOUR_MODES), default=pp.MODE_ALL)
        add_property(obj, "App::PropertyInteger", "EnvelopeSamples",
                     GROUP_FILTER,
                     "Number of rays used to trace the envelope", default=180)
        add_property(obj, "App::PropertyLength", "Clearance", GROUP_FILTER,
                     "Push the envelope out by this much. A surface lofted "
                     "between planes cuts inside the part wherever it bulges "
                     "in between, so a packaging envelope needs a little air",
                     default=0.0)
        add_property(obj, "App::PropertyLength", "MinContourLength",
                     GROUP_FILTER,
                     "Contours shorter than this are dropped as artefacts",
                     default=1.0)
        add_property(obj, "App::PropertyLength", "CloseTolerance", GROUP_FILTER,
                     "Close a contour whose ends are nearer than this; 0 means "
                     "derive it from the size of the mesh", default=0.0)
        add_property(obj, "App::PropertyIntegerList", "ContourCount", GROUP_INFO,
                     "Contours found on each plane", read_only=True)
        add_property(obj, "App::PropertyFloatList", "PlaneOffsets", GROUP_INFO,
                     "Plane positions along Direction", read_only=True)
        add_property(obj, "App::PropertyString", "Status", GROUP_INFO,
                     "Result of the last recompute", read_only=True)

    def onDocumentRestored(self, obj):
        self.setup(obj)

    def onChanged(self, obj, prop):
        # Normalising here rather than in execute() means a user who types
        # (0, 0, 7) sees (0, 0, 1) immediately instead of wondering.
        if prop == "Direction":
            d = obj.Direction
            if d.Length > 1e-12 and abs(d.Length - 1.0) > 1e-9:
                obj.Direction = App.Vector(d).normalize()
        elif prop == "Source" and getattr(obj, "AutoOrigin", False):
            mesh = self.mesh(obj)
            if mesh is not None:
                obj.Origin = mesh.BoundBox.Center

    # -- computation -------------------------------------------------------

    @staticmethod
    def mesh(obj):
        source = getattr(obj, "Source", None)
        if source is None or not hasattr(source, "Mesh"):
            return None
        return source.Mesh

    def slice_params(self, obj, mesh):
        origin = (vector(mesh.BoundBox.Center) if obj.AutoOrigin
                  else vector(obj.Origin))
        close_tolerance = value(obj.CloseTolerance) or None
        return pp.SliceParams(
            origin=origin,
            direction=vector(obj.Direction),
            mode=str(obj.Mode),
            count=max(1, int(obj.Count)),
            spacing=value(obj.Spacing),
            range_start=None if obj.AutoRange else value(obj.RangeStart),
            range_end=None if obj.AutoRange else value(obj.RangeEnd),
            inset=float(obj.Inset) / 100.0,
            avoid_vertex_rows=bool(obj.AvoidVertexRows),
            contour_mode=str(obj.ContourMode),
            envelope_samples=max(24, int(obj.EnvelopeSamples)),
            clearance=value(obj.Clearance),
            min_contour_length=value(obj.MinContourLength),
            close_tolerance=close_tolerance,
            # Orientation and seam belong to FittedSections: they are fitting
            # concerns, and this object is meant to accept any mesh and hand on
            # raw contours.
            unify_orientation=False,
            seam_mode=ct.SEAM_NONE)

    def execute(self, obj):
        """Never raises: a broken recompute keeps the previous Shape and says
        so in Status, because an exception here poisons the whole document."""
        mesh = self.mesh(obj)
        if mesh is None:
            obj.Status = "no mesh assigned to Source"
            return
        if mesh.CountFacets == 0:
            obj.Status = "the mesh is empty"
            return

        try:
            params = self.slice_params(obj, mesh)
            sections = pp.slice_mesh(mesh, params)
            shape = pp.sections_to_shape(sections)
        except Exception as exc:  # noqa: BLE001
            obj.Status = "failed: %s" % exc
            warn("%s: %s" % (obj.Name, obj.Status))
            return

        obj.Shape = shape
        obj.ContourCount = [len(s.contours) for s in sections]
        direction = vector(obj.Direction)
        origin = (vector(mesh.BoundBox.Center) if obj.AutoOrigin
                  else vector(obj.Origin))
        obj.PlaneOffsets = [float(np.dot(s.base - origin, direction))
                            for s in sections]

        empty = sum(1 for s in sections if not s.contours)
        rejected = sum(s.rejected for s in sections)
        obj.Status = "%d planes, %d contours%s%s" % (
            len(sections), sum(obj.ContourCount),
            ", %d planes empty" % empty if empty else "",
            ", %d rejected" % rejected if rejected else "")

    def dumps(self):
        return None

    def loads(self, state):
        return None

    def __getstate__(self):
        return None

    def __setstate__(self, state):
        return None


class ViewProviderSectionSet(ViewProviderBase):
    icon = "SectionLoft_Sections.svg"


def make_section_set(doc, source=None, name="SectionSet"):
    obj = doc.addObject("Part::FeaturePython", name)
    SectionSet(obj)
    if obj.ViewObject is not None:
        ViewProviderSectionSet(obj.ViewObject)
        obj.ViewObject.LineColor = (1.0, 0.6, 0.0)
        obj.ViewObject.LineWidth = 1.0
    if source is not None:
        obj.Source = source
    return obj
