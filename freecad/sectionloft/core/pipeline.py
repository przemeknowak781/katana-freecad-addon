"""Mesh -> sections -> fitted wires, wired together.

This is the v0.1 stand-in for the ``SectionSet`` and ``FittedSections``
document objects: the same steps in the same order, driven by plain parameter
objects instead of FreeCAD properties.  When the parametric objects arrive in
v0.2 they call straight into here.
"""

from dataclasses import dataclass, field

import numpy as np

import FreeCAD as App
import Part

from . import contours as ct
from . import envelope as ev
from . import planes as pf
from . import polyline as pl
from .fitting import FitParams, fit_contour

LOG_PREFIX = "[SectionLoft] "

#: Clamp for the automatically derived tolerance, in millimetres.  Below the
#: lower bound you are fitting triangulation noise; above the upper one the
#: curve stops resembling the mesh.
TOLERANCE_LIMITS = (0.01, 2.0)


def _warn(message):
    App.Console.PrintWarning(LOG_PREFIX + message + "\n")


def _log(message):
    App.Console.PrintMessage(LOG_PREFIX + message + "\n")


MODE_ALL = "All"
MODE_ENVELOPE = "Envelope"
CONTOUR_MODES = (MODE_ALL, MODE_ENVELOPE)


@dataclass
class SliceParams:
    origin: object = None                 # None -> bbox centre
    direction: tuple = (0.0, 0.0, 1.0)
    mode: str = pf.MODE_COUNT
    count: int = 12
    spacing: float = None
    range_start: float = None             # None -> derived from the bbox
    range_end: float = None
    inset: float = pf.DEFAULT_INSET
    min_contour_length: float = 1.0
    close_tolerance: float = None         # None -> 1e-4 * bbox diagonal
    slice_tolerance: float = 1e-6
    avoid_vertex_rows: bool = True
    vertex_row_nudge: float = 1e-3        # fraction of the span along Direction
    contour_mode: str = MODE_ALL
    envelope_samples: int = 180
    clearance: float = 0.0

    # Fitting tolerance derivation, kept here because it depends on the mesh.
    auto_tolerance: bool = True
    tolerance_factor: float = 0.5

    seam_mode: str = ct.SEAM_AXIS
    seam_axis: tuple = (1.0, 0.0, 0.0)
    seam_guide: object = None
    unify_orientation: bool = True


@dataclass
class Contour:
    points: object                        # (n, 3) unique points
    closed: bool
    plane_index: int
    reversed_: bool = False


@dataclass
class Section:
    index: int
    base: object
    normal: object
    contours: list = field(default_factory=list)
    rejected: int = 0                     # contours dropped as too short

    @property
    def primary(self):
        """The contour v0.1 works with: the longest closed one."""
        closed = [c for c in self.contours if c.closed]
        pool = closed or self.contours
        if not pool:
            return None
        return max(pool, key=lambda c: ct.contour_length(c.points, c.closed))


@dataclass
class Report:
    sections: list = field(default_factory=list)
    fits: list = field(default_factory=list)
    section_shape: object = None
    fitted_shape: object = None
    contour_count: list = field(default_factory=list)
    failed_sections: list = field(default_factory=list)
    max_deviation: float = 0.0
    max_seam_kink: float = 0.0
    tolerance: float = 0.0
    status: str = ""


def mesh_points(mesh):
    """Mesh vertices as an ``(n, 3)`` array."""
    return np.array([[p.x, p.y, p.z] for p in mesh.Topology[0]], dtype=float)


def mesh_median_edge_length(mesh):
    """Median triangle edge length - the yardstick for the auto tolerance.

    A mesh represents a surface to roughly the accuracy of its edge length;
    fitting far below that means fitting the triangulation, not the shape.
    """
    points, facets = mesh.Topology
    pts = np.array([[p.x, p.y, p.z] for p in points], dtype=float)
    idx = np.array(facets, dtype=int)
    if len(idx) == 0:
        return 0.0
    edges = np.vstack([idx[:, [0, 1]], idx[:, [1, 2]], idx[:, [2, 0]]])
    lengths = np.linalg.norm(pts[edges[:, 0]] - pts[edges[:, 1]], axis=1)
    return float(np.median(lengths))


def auto_tolerance(mesh, factor):
    tol = factor * mesh_median_edge_length(mesh)
    return float(np.clip(tol, *TOLERANCE_LIMITS))


def build_planes(mesh, params):
    bb = mesh.BoundBox
    origin = (np.array([bb.Center.x, bb.Center.y, bb.Center.z])
              if params.origin is None
              else np.asarray(params.origin, dtype=float).reshape(3))
    direction = pf.normalize(params.direction)

    vertices = mesh_points(mesh)
    offsets = (vertices - origin) @ direction
    span = float(offsets.max() - offsets.min()) or 1.0

    start, end = params.range_start, params.range_end
    if start is None or end is None:
        margin = span * float(params.inset)
        auto_start = float(offsets.min()) + margin
        auto_end = float(offsets.max()) - margin
        start = auto_start if start is None else start
        end = auto_end if end is None else end

    positions = pf.plane_positions(params.mode, start, end, params.count,
                                   params.spacing)
    if params.avoid_vertex_rows:
        positions = pf.avoid_vertex_rows(positions, offsets,
                                         epsilon=1e-7 * span,
                                         nudge=params.vertex_row_nudge * span)

    planes = [(origin + t * direction, direction.copy()) for t in positions]
    return origin, direction, planes


#: A closed contour whose area falls below this fraction of its length squared
#: is a sliver, not a section.  A circle sits at 1/(4*pi) ~ 0.08, a 100:1
#: rectangle at 0.0025, an out-and-back retrace at 0.
DEGENERACY_RATIO = 1e-6


def _is_degenerate(points, normal, base, length):
    if length <= 0.0:
        return True
    u, v, _ = pf.orthonormal_frame(normal)
    area = abs(ct.signed_area(ct.to_plane_coords(points, base, u, v)))
    return area < DEGENERACY_RATIO * length * length


def slice_mesh(mesh, params=None):
    """Cut the mesh and clean up the resulting polylines."""
    params = params or SliceParams()
    origin, direction, planes = build_planes(mesh, params)

    bb = mesh.BoundBox
    close_tol = (params.close_tolerance if params.close_tolerance is not None
                 else max(1e-6, 1e-4 * bb.DiagonalLength))

    fc_planes = [(App.Vector(*base), App.Vector(*normal)) for base, normal in planes]
    raw = mesh.crossSections(fc_planes, float(params.slice_tolerance))

    vertices = axis_point = None
    slab_half_width = 0.0
    if params.contour_mode == MODE_ENVELOPE:
        vertices = mesh_points(mesh)
        # One axis for the whole family, so the radial profiles of neighbouring
        # sections are measured from the same place.
        axis_point = origin
        # Half the plane spacing, so the slabs tile the part without overlapping
        # more than they have to.
        offsets = [float(np.dot(base - planes[0][0], direction))
                   for base, _ in planes]
        gaps = np.diff(offsets) if len(offsets) > 1 else np.array([0.0])
        # A full spacing, not half.  Half-width slabs tile the part, but the
        # loft interpolates between two neighbouring profiles, and a point
        # sitting between them is only guaranteed to be contained if *both*
        # profiles already cover its height.
        slab_half_width = float(np.median(np.abs(gaps))) if len(gaps) else 0.0
    direction = planes[0][1] if planes else np.array([0.0, 0.0, 1.0])

    sections = []
    previous_start = None
    for i, (plane, per_plane) in enumerate(zip(planes, raw)):
        base, normal = plane
        section = Section(index=i, base=base, normal=normal)
        for polygon in per_plane:
            pts = np.array([[p.x, p.y, p.z] for p in polygon], dtype=float)
            pts, closed, discarded = ct.close_contour(pts, close_tol)
            # One or two discarded points is just the closing point (possibly
            # duplicated by a coincident mesh vertex) and is not worth a word.
            if discarded > max(2, 0.05 * len(polygon)):
                _warn("section %d: contour retraced itself, dropped %d trailing "
                      "points (plane lies in a row of coplanar mesh edges)"
                      % (i, discarded))
            if len(pts) < 3:
                section.rejected += 1
                continue
            length = ct.contour_length(pts, closed)
            if length < params.min_contour_length:
                section.rejected += 1
                continue
            if closed and _is_degenerate(pts, normal, base, length):
                # Backstop for the coplanar-row case that survived the nudge:
                # a contour that encloses no area cannot be oriented, seamed or
                # lofted, so it is rejected rather than passed on broken.
                _warn("section %d: contour encloses no area, rejected" % i)
                section.rejected += 1
                continue
            reversed_ = False
            if params.unify_orientation and closed:
                pts, reversed_ = ct.unify_orientation(pts, normal, base)
            if closed:
                pts = ct.apply_seam(pts, closed, params.seam_mode, params.seam_axis,
                                    params.seam_guide, previous_start)
                previous_start = pts[0]
            section.contours.append(Contour(pts, closed, i, reversed_))

        if params.contour_mode == MODE_ENVELOPE:
            _replace_with_envelope(section, params, vertices, direction,
                                   slab_half_width, axis_point)
        sections.append(section)
    return sections


def _replace_with_envelope(section, params, vertices=None, direction=None,
                           slab=0.0, axis_point=None):
    """Collapse a section's contours to their outer boundary, in place.

    Everything downstream gets simpler: one contour per plane means no pairing,
    a radial profile cannot self-intersect, and every section carries the same
    points in the same angular order, so the loft has nothing left to twist.

    Given the mesh vertices, the envelope is taken over a slab around the plane
    rather than the cut itself, so that features falling between planes are
    contained rather than shaved off.
    """
    if not section.contours:
        return

    outline = None
    if vertices is not None and slab > 0.0:
        offsets = (vertices - section.base) @ direction
        slab_points = vertices[np.abs(offsets) <= slab]
        if len(slab_points) >= 8:
            outline = ev.envelope_from_slab(
                slab_points, section.base, section.normal,
                samples=int(params.envelope_samples),
                clearance=float(params.clearance),
                axis_point=axis_point)

    if outline is None:
        outline = ev.envelope_contour(
            [c.points for c in section.contours], section.base, section.normal,
            samples=int(params.envelope_samples),
            clearance=float(params.clearance))
    if outline is None or len(outline) < 3:
        _warn("section %d: could not build an envelope, keeping the contours"
              % section.index)
        return

    outline, _ = ct.unify_orientation(outline, section.normal, section.base)
    section.rejected += max(0, len(section.contours) - 1)
    section.contours = [Contour(outline, True, section.index, False)]


def sections_to_shape(sections, primary_only=False):
    """Compound of straight-segment wires, one per contour."""
    wires = []
    for section in sections:
        pool = ([section.primary] if primary_only else section.contours)
        for contour in pool:
            if contour is None or len(contour.points) < 2:
                continue
            pts = [App.Vector(*p) for p in contour.points]
            if contour.closed:
                pts.append(pts[0])
            wires.append(Part.makePolygon(pts))
    return Part.Compound(wires) if wires else Part.Compound([])


def fit_sections(sections, fit_params=None, primary_only=True):
    """Approximate each section's contour.  Failures are isolated per section."""
    fit_params = fit_params or FitParams()
    results = []
    for section in sections:
        contours = ([section.primary] if primary_only else list(section.contours))
        contours = [c for c in contours if c is not None]
        if not contours:
            results.append(None)
            continue
        # v0.1 deliberately handles one contour per plane; pairing several
        # contours across sections is a v0.2 problem (see spec 11.2).
        if primary_only and len(section.contours) > 1:
            _warn("section %d has %d contours, using the longest closed one"
                  % (section.index, len(section.contours)))
        contour = contours[0]
        results.append(fit_contour(contour.points, contour.closed, fit_params))
    return results


def run(mesh, slice_params=None, fit_params=None):
    """Full v0.1 chain.  Returns a :class:`Report`; never raises on bad input."""
    slice_params = slice_params or SliceParams()
    fit_params = fit_params or FitParams()
    report = Report()

    if mesh is None or mesh.CountFacets == 0:
        report.status = "no mesh"
        _warn("no input mesh")
        return report

    if slice_params.auto_tolerance:
        fit_params.tolerance = auto_tolerance(mesh, slice_params.tolerance_factor)
    report.tolerance = fit_params.tolerance

    report.sections = slice_mesh(mesh, slice_params)
    report.contour_count = [len(s.contours) for s in report.sections]
    report.section_shape = sections_to_shape(report.sections)

    report.fits = fit_sections(report.sections, fit_params)

    wires = []
    for i, fit in enumerate(report.fits):
        if fit is None:
            report.failed_sections.append(i)
            _warn("section %d produced no usable contour" % i)
            continue
        if not fit.ok:
            report.failed_sections.append(i)
            _warn("section %d failed to fit (%s)" % (i, fit.error))
            continue
        wires.append(fit.wire)
        report.max_deviation = max(report.max_deviation, fit.deviation)
        report.max_seam_kink = max(report.max_seam_kink, fit.seam_kink)

    report.fitted_shape = Part.Compound(wires) if wires else Part.Compound([])
    report.status = ("fitted %d of %d sections, tolerance %.3f mm, "
                     "max deviation %.3f mm, max seam kink %.2f deg"
                     % (len(wires), len(report.sections), report.tolerance,
                        report.max_deviation, np.rad2deg(report.max_seam_kink)))
    _log(report.status)
    return report


def add_to_document(doc, report, prefix="SectionLoft"):
    """Drop the two compounds into a document as plain ``Part::Feature`` objects.

    v0.1 output is deliberately non-parametric - the parametric chain is what
    v0.2 is for, and shipping half of it now would mean documents that need
    migrating twice.
    """
    created = []
    if report.section_shape is not None:
        obj = doc.addObject("Part::Feature", prefix + "_Sections")
        obj.Shape = report.section_shape
        if getattr(obj, "ViewObject", None) is not None:
            obj.ViewObject.LineColor = (1.0, 0.6, 0.0)
        created.append(obj)
    if report.fitted_shape is not None:
        obj = doc.addObject("Part::Feature", prefix + "_Fitted")
        obj.Shape = report.fitted_shape
        created.append(obj)
    doc.recompute()
    return created
