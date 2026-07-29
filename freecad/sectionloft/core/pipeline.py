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
    envelope_convex: bool = False
    envelope_collapse_factor: float = 0.5
    envelope_axial_smoothing: int = 3

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


def _cut(mesh, planes, tolerance):
    """crossSections for a list of ``(base, normal)`` pairs, as numpy arrays."""
    fc_planes = [(App.Vector(*base), App.Vector(*normal))
                 for base, normal in planes]
    raw = mesh.crossSections(fc_planes, float(tolerance))
    return [[np.array([[p.x, p.y, p.z] for p in polygon], dtype=float)
             for polygon in per_plane] for per_plane in raw]


def envelope_sections(mesh, params, planes, origin, direction):
    """Sections whose contour is the outer boundary of a slab of the mesh.

    The boundary is measured off the *cross-sections*, not off the vertices.
    That was the thing holding precision back: a slab of this mesh holds a few
    hundred vertices, so at 180 rays over 120 of the bins came up empty and the
    profile was interpolated noise.  A cross-section is a continuous polyline -
    every ray hits it, and the radius it reports is exact.

    To cover what the part does *between* section planes, each section is
    measured against three cuts: its own plane and the half-steps either side.
    Cutting at half spacing means neighbouring sections share those extra cuts,
    so the whole family costs one crossSections call over 2N+1 planes.
    """
    if len(planes) < 2:
        sub_planes = list(planes)
        groups = [[0]] * len(planes)
    else:
        offsets = [float(np.dot(base - planes[0][0], direction))
                   for base, _ in planes]
        step = float(np.median(np.diff(offsets))) / 2.0
        sub_offsets = [offsets[0] - step]
        for value_ in offsets:
            sub_offsets.extend([value_, value_ + step])
        sub_planes = [(planes[0][0] + t * direction, direction)
                      for t in sub_offsets]
        groups = [[2 * i, 2 * i + 1, 2 * i + 2] for i in range(len(planes))]

    cuts = _cut(mesh, sub_planes, params.slice_tolerance)
    u, v, _n = pf.orthonormal_frame(direction)

    # First pass: project each slab's contours into the shared 2D frame and take
    # their hull, so the axis can be chosen once for the whole family.
    projected = []
    hulls = []
    for (base, _normal), members in zip(planes, groups):
        polygons = []
        for member in members:
            if 0 <= member < len(cuts):
                polygons.extend(ct.to_plane_coords(c, base, u, v)
                                for c in cuts[member] if len(c) >= 3)
        projected.append(polygons)
        hulls.append(ev.convex_hull_2d(np.vstack(polygons))
                     if polygons else None)

    axis, misses = ev.shared_axis(hulls)
    if misses:
        _warn("%d of %d sections do not contain the shared axis and fall back "
              "to their own centre; the loft may twist there"
              % (misses, len(planes)))

    # Second pass: raw radii per section, then resolve the gaps *along the axis*
    # rather than section by section, which is what stops one section bridging
    # where its neighbour follows and putting a step in the surface.
    samples = int(params.envelope_samples)
    collapse = (2.0 if params.envelope_convex
                else float(params.envelope_collapse_factor))
    material = np.full((len(planes), samples), np.nan)
    hull_radii = np.zeros((len(planes), samples))
    centres = [None] * len(planes)
    usable = []
    for index, ((base, _normal), polygons) in enumerate(zip(planes, projected)):
        if not polygons:
            _warn("section %d: nothing to build an envelope from" % index)
            continue
        row, hull_row, used = ev.envelope_profile(polygons, axis, samples,
                                                  collapse)
        if row is None:
            _warn("section %d: could not build an envelope" % index)
            continue
        material[index] = row
        hull_radii[index] = hull_row
        # The radii were measured from whatever centre this section could
        # actually use; rebuilding the outline around a different one inflates
        # it by the distance between the two.
        centres[index] = used
        usable.append(index)

    if not usable:
        return [Section(index=i, base=b, normal=n)
                for i, (b, n) in enumerate(planes)]

    field = ev.fill_along_axis(material[usable], hull_radii[usable],
                               smoothing=int(params.envelope_axial_smoothing))
    if params.clearance:
        field = field + float(params.clearance)

    angles = np.linspace(0.0, 2.0 * np.pi, samples, endpoint=False)
    unit = np.column_stack((np.cos(angles), np.sin(angles)))

    sections = []
    row_by_index = dict(zip(usable, field))
    for index, (base, normal) in enumerate(planes):
        section = Section(index=index, base=base, normal=normal)
        row = row_by_index.get(index)
        if row is not None:
            flat = centres[index] + unit * row[:, None]
            outline = base + flat[:, 0:1] * u + flat[:, 1:2] * v
            outline, _ = ct.unify_orientation(outline, normal, base)
            section.contours = [Contour(outline, True, index, False)]
        sections.append(section)
    return sections


def slice_mesh(mesh, params=None):
    """Cut the mesh and clean up the resulting polylines."""
    params = params or SliceParams()
    origin, direction, planes = build_planes(mesh, params)

    if params.contour_mode == MODE_ENVELOPE:
        return envelope_sections(mesh, params, planes, origin, direction)

    bb = mesh.BoundBox
    close_tol = (params.close_tolerance if params.close_tolerance is not None
                 else max(1e-6, 1e-4 * bb.DiagonalLength))

    fc_planes = [(App.Vector(*base), App.Vector(*normal)) for base, normal in planes]
    raw = mesh.crossSections(fc_planes, float(params.slice_tolerance))

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

        sections.append(section)
    return sections


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
