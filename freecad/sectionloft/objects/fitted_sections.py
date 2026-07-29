"""``FittedSections`` - polyline contours in, B-spline wires out.

This is the only object where anything non-trivial happens.  It also owns
orientation and seam placement, because both exist to serve the fit and the
loft, not the slicing.
"""

import numpy as np

import FreeCAD as App
import Part

from ..core import contours as ct
from ..core import pairing as pr
from ..core import polyline as pl
from dataclasses import replace

from ..core.fitting import FitParams, fit_contour, fit_contour_at
from .common import (ViewProviderBase, add_property, log, value, vector, warn,
                     wire_points)

GROUP_SOURCE = "Source"
GROUP_FIT = "Fitting"
GROUP_SHAPE = "Contour shape"
GROUP_SEAM = "Seam"
GROUP_INFO = "Information"

#: Same clamp as the headless pipeline: below the lower bound you are fitting
#: triangulation noise, above the upper one the curve stops resembling the mesh.
TOLERANCE_LIMITS = (0.01, 2.0)


class FittedSections:
    """Approximates each contour with B-spline curves.

    Wires come out ordered chain-major - all sections of one body, then all
    sections of the next - and ``ChainSizes`` says how long each run is.  That
    is the whole interface ``SectionLoft`` needs in order to loft a mesh with
    separate limbs without guessing.
    """

    def __init__(self, obj):
        self.setup(obj)
        obj.Proxy = self

    # -- properties --------------------------------------------------------

    def setup(self, obj):
        add_property(obj, "App::PropertyLink", "Source", GROUP_SOURCE,
                     "SectionSet, or any object whose Shape is a compound of "
                     "contour wires")
        add_property(obj, "App::PropertyBool", "AutoTolerance", GROUP_FIT,
                     "Derive the tolerance from the density of the source mesh",
                     default=True)
        add_property(obj, "App::PropertyFloat", "ToleranceFactor", GROUP_FIT,
                     "Multiplier on the median mesh edge length. Below about "
                     "0.3 you start fitting the triangulation itself",
                     default=0.5)
        add_property(obj, "App::PropertyLength", "Tolerance", GROUP_FIT,
                     "Approximation tolerance, used when AutoTolerance is off",
                     default=0.2)
        add_property(obj, "App::PropertyInteger", "DegreeMin", GROUP_FIT,
                     "Minimum curve degree; C2 continuity needs at least 3",
                     default=3)
        add_property(obj, "App::PropertyInteger", "DegreeMax", GROUP_FIT,
                     "Maximum curve degree; above 5 oscillation gets likely",
                     default=5)
        add_property(obj, "App::PropertyEnumeration", "Continuity", GROUP_FIT,
                     "Continuity of the fitted curve",
                     enum=["C0", "C1", "C2"], default="C2")
        add_property(obj, "App::PropertyBool", "Decimate", GROUP_SHAPE,
                     "Reduce the polyline before fitting. Too many points make "
                     "the least-squares system ill-conditioned and the curve "
                     "comes out wavy", default=True)
        add_property(obj, "App::PropertyFloat", "DecimateFactor", GROUP_SHAPE,
                     "Decimation tolerance as a fraction of Tolerance",
                     default=0.25)
        add_property(obj, "App::PropertyBool", "CornerDetection", GROUP_SHAPE,
                     "Split the contour at corners and fit the pieces "
                     "separately", default=True)
        add_property(obj, "App::PropertyBool", "UniformChainTopology",
                     GROUP_SHAPE,
                     "Refit a chain without corner splitting when its sections "
                     "end up with different edge counts. Lofting needs matching "
                     "profiles; sections split into 4, 9 and 18 edges cannot be "
                     "joined", default=True)
        add_property(obj, "App::PropertyAngle", "CornerAngle", GROUP_SHAPE,
                     "Turn angle counted as a corner", default=30.0)
        add_property(obj, "App::PropertyInteger", "CornerDrift", GROUP_SHAPE,
                     "How many samples a crease may move between neighbouring "
                     "envelope sections and still count as the same crease. A "
                     "feature on a curved surface does not sit at a fixed angle",
                     default=4)
        add_property(obj, "App::PropertyFloat", "CornerAgreement", GROUP_SHAPE,
                     "For envelope sections: the fraction of the chain that "
                     "must see a corner at the same angle before an edge is put "
                     "there. Low values let one section's noise crease the whole "
                     "surface", default=0.35)
        add_property(obj, "App::PropertyBool", "SeamSmoothing", GROUP_SEAM,
                     "Fit closed contours through cyclically extended data so "
                     "the seam is tangent-continuous instead of a crease",
                     default=True)
        add_property(obj, "App::PropertyAngle", "MaxSeamKink", GROUP_SEAM,
                     "Refit at a tighter tolerance while the seam tangents "
                     "differ by more than this", default=5.0)
        add_property(obj, "App::PropertyBool", "UnifyOrientation", GROUP_SEAM,
                     "Make every contour run the same way round, otherwise the "
                     "loft twists between neighbours", default=True)
        add_property(obj, "App::PropertyEnumeration", "SeamMode", GROUP_SEAM,
                     "Where each contour starts",
                     enum=["None", "Axis", "Guide", "MinTravel"],
                     default="Axis")
        add_property(obj, "App::PropertyVector", "SeamAxis", GROUP_SEAM,
                     "Reference direction for SeamMode Axis",
                     default=App.Vector(1, 0, 0))
        add_property(obj, "App::PropertyLink", "SeamGuide", GROUP_SEAM,
                     "Guide curve for SeamMode Guide")
        add_property(obj, "App::PropertyIntegerList", "ChainSizes", GROUP_INFO,
                     "Number of sections in each chain of paired contours",
                     read_only=True)
        add_property(obj, "App::PropertyFloat", "MaxDeviation", GROUP_INFO,
                     "Largest distance from a fitted curve to its source "
                     "polyline, in mm", read_only=True)
        add_property(obj, "App::PropertyFloat", "MaxSeamKinkFound", GROUP_INFO,
                     "Largest tangent break at a seam, in degrees",
                     read_only=True)
        add_property(obj, "App::PropertyFloat", "ToleranceUsed", GROUP_INFO,
                     "Tolerance actually applied, in mm", read_only=True)
        add_property(obj, "App::PropertyIntegerList", "FailedSections",
                     GROUP_INFO, "Sections that could not be fitted",
                     read_only=True)
        add_property(obj, "App::PropertyIntegerList", "AmbiguousSections",
                     GROUP_INFO,
                     "Sections where contour pairing was a close call",
                     read_only=True)
        add_property(obj, "App::PropertyString", "Status", GROUP_INFO,
                     "Result of the last recompute", read_only=True)

    def onDocumentRestored(self, obj):
        self.setup(obj)

    def onChanged(self, obj, prop):
        if prop == "SeamAxis":
            a = obj.SeamAxis
            if a.Length > 1e-12 and abs(a.Length - 1.0) > 1e-9:
                obj.SeamAxis = App.Vector(a).normalize()

    # -- reading the source ------------------------------------------------

    @staticmethod
    def source_shape(obj):
        source = getattr(obj, "Source", None)
        if source is None:
            return None
        shape = getattr(source, "Shape", None)
        if shape is None or shape.isNull() or not shape.Wires:
            return None
        return shape

    @staticmethod
    def grouped_contours(obj, shape):
        """Wires regrouped per section, as ``[(points, closed), ...]`` lists.

        ``ContourCount`` on the source says how many wires belong to each plane.
        Without it - any other object feeding this one - each wire is treated as
        its own section, which is the only assumption that cannot be wrong in a
        way that silently corrupts the result.
        """
        wires = list(shape.Wires)
        counts = list(getattr(obj.Source, "ContourCount", []) or [])
        if not counts or sum(counts) != len(wires):
            return [[wire_points(w)] for w in wires]

        groups = []
        index = 0
        for count in counts:
            groups.append([wire_points(w) for w in wires[index:index + count]])
            index += count
        return groups

    def direction(self, obj, groups):
        """Reference normal for orientation and pairing.

        Taken from the source when it has one.  Otherwise derived from the
        contours themselves via Newell's formula - its sign follows the winding,
        which is useless for unifying orientation, so the first contour sets the
        convention and the rest are matched to it.
        """
        source_direction = getattr(getattr(obj, "Source", None), "Direction", None)
        if source_direction is not None and source_direction.Length > 1e-9:
            return vector(source_direction)
        for section in groups:
            for points, _ in section:
                normal = ct.polygon_normal(points)
                if np.linalg.norm(normal) > 1e-9:
                    return normal
        return np.array([0.0, 0.0, 1.0])

    def tolerance(self, obj, groups):
        """Fitting tolerance in mm.

        Preference order: the mesh behind the source (its median edge length is
        the honest measure of how well the mesh describes the surface), then the
        median segment length of the contours themselves, then the manual value.
        """
        if not obj.AutoTolerance:
            return value(obj.Tolerance)

        mesh_object = getattr(getattr(obj, "Source", None), "Source", None)
        mesh = getattr(mesh_object, "Mesh", None)
        if mesh is not None and mesh.CountFacets:
            from ..core.pipeline import mesh_median_edge_length
            median = mesh_median_edge_length(mesh)
        else:
            lengths = [pl.median_edge_length(points, closed)
                       for section in groups for points, closed in section]
            median = float(np.median(lengths)) if lengths else 0.0

        return float(np.clip(obj.ToleranceFactor * median, *TOLERANCE_LIMITS))

    @staticmethod
    def envelope_source(obj):
        return str(getattr(getattr(obj, "Source", None), "ContourMode",
                           "All")) == "Envelope"

    @staticmethod
    def corners_wanted(obj):
        """Per-contour corner splitting, for ordinary contours.

        Envelope sections take the other route: their corners are found once for
        the whole chain and applied at the same sample indices everywhere, which
        is the only way a crease survives into the loft.  Splitting each envelope
        section on its own put edges wherever the sampling caught them; measured
        on the test mesh at 30 sections, the surface came out enclosing 36 times
        the volume its own sections implied.
        """
        mode = str(getattr(getattr(obj, "Source", None), "ContourMode", "All"))
        return bool(obj.CornerDetection) and mode != "Envelope"

    def fit_envelope_chain(self, obj, prepared, params):
        """Fit a chain of envelope sections, splitting them at tracked creases.

        Every section gets the same *number* of splits, at its own angle for
        each crease, so the loft joins edge to edge along a feature that drifts
        as the surface turns.
        """
        profiles = [entry[2] for entry in prepared]
        per_section = []
        if bool(obj.CornerDetection):
            per_section = pl.track_corner_lines(
                profiles, np.deg2rad(value(obj.CornerAngle)),
                window=max(1, int(obj.CornerDrift)),
                min_fraction=float(obj.CornerAgreement))
        if not per_section:
            return [(entry, fit_contour(entry[2], entry[3], params))
                    for entry in prepared], 0
        return ([(entry, fit_contour_at(entry[2], params, indices))
                 for entry, indices in zip(prepared, per_section)],
                len(per_section[0]))

    def fit_params(self, obj, tolerance):
        return FitParams(
            tolerance=tolerance,
            degree_min=int(obj.DegreeMin),
            degree_max=int(obj.DegreeMax),
            continuity=str(obj.Continuity),
            decimate=bool(obj.Decimate),
            decimate_factor=float(obj.DecimateFactor),
            corner_detection=self.corners_wanted(obj),
            corner_angle=np.deg2rad(value(obj.CornerAngle)),
            seam_smoothing=bool(obj.SeamSmoothing),
            max_seam_kink=np.deg2rad(value(obj.MaxSeamKink)))

    # -- computation -------------------------------------------------------

    def prepare(self, points, closed, direction, obj, previous_start):
        """Orientation and seam for one contour."""
        if closed and obj.UnifyOrientation:
            points, _ = ct.unify_orientation(points, direction)
        if closed and str(obj.SeamMode) != ct.SEAM_NONE:
            guide = None
            if str(obj.SeamMode) == ct.SEAM_GUIDE:
                guide_object = getattr(obj, "SeamGuide", None)
                guide_shape = getattr(guide_object, "Shape", None)
                if guide_shape is None:
                    warn("%s: SeamMode is Guide but no SeamGuide is set, "
                         "falling back to Axis" % obj.Name)
                    return self._seam(points, ct.SEAM_AXIS, obj, None,
                                      previous_start)
                guide = np.array([[p.x, p.y, p.z]
                                  for p in guide_shape.discretize(Number=100)])
            return self._seam(points, str(obj.SeamMode), obj, guide,
                              previous_start)
        return points

    @staticmethod
    def _seam(points, mode, obj, guide, previous_start):
        return ct.apply_seam(points, True, mode, vector(obj.SeamAxis), guide,
                             previous_start)

    @staticmethod
    def common_corner_count(obj, results):
        """How many corners every section of this chain should split at.

        ``None`` means the chain is already consistent and must be left alone.

        ``Part.makeLoft`` needs compatible profiles.  Corner detection is
        per-contour and honest about it: on a scanned mesh one section comes out
        with 4 edges and its neighbour with 18, and the loft fails with nothing
        more helpful than ``BRep_API: command not done``.

        The fix is not to throw the corners away - that turned a sharp-featured
        mask into a blob with 3 mm of error - nor to level down to the smallest
        count, which spans real corners with a single spline and was worse still
        at 1.75 mm.  It is to level *up*: every section splits into as many
        pieces as the richest one, keeping all of its own corners and padding the
        difference along the arc.
        """
        if not getattr(obj, "UniformChainTopology", True):
            return None
        counts = [len(result.edges) for _, result in results if result.ok]
        if len(set(counts)) <= 1:
            return None
        return max(counts)

    def execute(self, obj):
        shape = self.source_shape(obj)
        if shape is None:
            obj.Status = "no contours on Source"
            return

        try:
            groups = self.grouped_contours(obj, shape)
            direction = self.direction(obj, groups)
            tolerance = self.tolerance(obj, groups)
            params = self.fit_params(obj, tolerance)

            centroids = [[ct.centroid(points) for points, _ in section]
                         for section in groups]
            chains, ambiguous = pr.build_chains(centroids, direction)

            wires = []
            chain_sizes = []
            failed = []
            deviation = 0.0
            kink = 0.0

            unified = 0
            shared_corners = 0
            for chain in chains:
                prepared = []
                previous_start = None
                for section_index, contour_index in chain:
                    points, closed = groups[section_index][contour_index]
                    points = self.prepare(points, closed, direction, obj,
                                          previous_start)
                    if closed and len(points):
                        previous_start = points[0]
                    prepared.append((section_index, contour_index, points, closed))

                if self.envelope_source(obj):
                    results, corners = self.fit_envelope_chain(obj, prepared,
                                                               params)
                    shared_corners = max(shared_corners, corners)
                else:
                    results = [(entry, fit_contour(entry[2], entry[3], params))
                               for entry in prepared]

                target = (self.common_corner_count(obj, results)
                          if params.corner_detection else None)
                if target is not None:
                    uniform = replace(params, corner_detection=True,
                                      corner_target=target)
                    results = [(entry, fit_contour(entry[2], entry[3], uniform))
                               for entry in prepared]
                    unified += 1

                fitted = 0
                for (section_index, contour_index, _, _), result in results:
                    if not result.ok:
                        failed.append(section_index)
                        warn("%s: section %d contour %d failed (%s)"
                             % (obj.Name, section_index, contour_index,
                                result.error))
                        continue
                    wires.append(result.wire)
                    fitted += 1
                    deviation = max(deviation, result.deviation)
                    kink = max(kink, result.seam_kink)
                chain_sizes.append(fitted)
        except Exception as exc:  # noqa: BLE001
            obj.Status = "failed: %s" % exc
            warn("%s: %s" % (obj.Name, obj.Status))
            return

        obj.Shape = Part.Compound(wires) if wires else Part.Compound([])
        obj.ChainSizes = [int(n) for n in chain_sizes if n]
        obj.MaxDeviation = float(deviation)
        obj.MaxSeamKinkFound = float(np.rad2deg(kink))
        obj.ToleranceUsed = float(tolerance)
        obj.FailedSections = sorted(set(int(i) for i in failed))
        obj.AmbiguousSections = sorted(set(int(s) for s, _ in ambiguous))

        total = sum(len(chain) for chain in chains)
        obj.Status = ("fitted %d of %d contours, tolerance %.3f mm, "
                      "max deviation %.3f mm" % (len(wires), total, tolerance,
                                                 deviation))
        if len(obj.ChainSizes) > 1:
            obj.Status += ", %d chains" % len(obj.ChainSizes)
        if unified:
            obj.Status += ", %d chains refitted for uniform corners" % unified
        if shared_corners:
            obj.Status += ", %d shared corners" % shared_corners
        elif obj.CornerDetection and self.envelope_source(obj):
            obj.Status += ", no corner agreed across the chain"
        if obj.AmbiguousSections:
            obj.Status += ", %d ambiguous" % len(obj.AmbiguousSections)

    def dumps(self):
        return None

    def loads(self, state):
        return None

    def __getstate__(self):
        return None

    def __setstate__(self, state):
        return None


class ViewProviderFittedSections(ViewProviderBase):
    icon = "SectionLoft_Fitted.svg"


def make_fitted_sections(doc, source=None, name="FittedSections"):
    obj = doc.addObject("Part::FeaturePython", name)
    FittedSections(obj)
    if obj.ViewObject is not None:
        ViewProviderFittedSections(obj.ViewObject)
        obj.ViewObject.LineColor = (0.0, 0.7, 1.0)
        obj.ViewObject.LineWidth = 2.0
    if source is not None:
        obj.Source = source
    return obj
