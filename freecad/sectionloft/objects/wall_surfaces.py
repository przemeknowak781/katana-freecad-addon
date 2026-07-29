"""``WallSurfaces`` - a surface per chain of wall runs."""

import numpy as np

import FreeCAD as App
import Part

from ..core import contours as ct
from ..core import pairing as pr
from ..core import surfaces as sf
from .common import ViewProviderBase, add_property, value, warn, wire_points

GROUP_SOURCE = "Source"
GROUP_GRID = "Grid"
GROUP_INFO = "Information"


class WallSurfaces:
    """Builds one B-spline surface per wall, from a grid of section points.

    Every node of the grid lies on a mesh cross-section, so the surface passes
    through the part rather than near it.  Columns run along the part - they are
    the lines between the features, the thing a loft never had.
    """

    def __init__(self, obj):
        self.setup(obj)
        obj.Proxy = self

    def setup(self, obj):
        add_property(obj, "App::PropertyLink", "Source", GROUP_SOURCE,
                     "SectionSet in Walls mode, or any object whose Shape is a "
                     "compound of open section runs")
        add_property(obj, "App::PropertyInteger", "Columns", GROUP_GRID,
                     "Points sampled along each run. More follows the section "
                     "more closely and costs surface poles", default=48)
        add_property(obj, "App::PropertyInteger", "MinSections", GROUP_GRID,
                     "A chain shorter than this is skipped: two sections cannot "
                     "say what the surface does between them", default=3)
        add_property(obj, "App::PropertyAngle", "MaxTwist", GROUP_GRID,
                     "Reject a grid whose columns disagree about direction by "
                     "more than this. A folded grid makes a folded surface, and "
                     "it is far cheaper to catch here", default=60.0)
        add_property(obj, "App::PropertyFloat", "MaxStretch", GROUP_GRID,
                     "Reject a grid whose worst step between sections exceeds "
                     "this multiple of its median step. Catches a chain that "
                     "linked two runs on opposite sides of the part - the twist "
                     "can look innocent while the surface flies across it",
                     default=3.0)
        add_property(obj, "App::PropertyLength", "OverlapDistance", GROUP_GRID,
                     "How far apart two runs may lie and still count as the "
                     "same wall. 0 derives it from the section spacing",
                     default=0.0)
        add_property(obj, "App::PropertyFloat", "MinOverlap", GROUP_GRID,
                     "Fraction of a run that must lie over its neighbour before "
                     "they are chained. Wall runs fragment differently from "
                     "section to section, and the centroid of a fragment is an "
                     "accident of where it broke", default=0.3)
        add_property(obj, "App::PropertyEnumeration", "Method", GROUP_GRID,
                     "Interpolate passes a surface through the grid points. "
                     "Gordon treats the grid as a curve network - rows as "
                     "profiles, columns as guides - which is what it is, and "
                     "gives a markedly tighter surface. Gordon needs the Curves "
                     "workbench and falls back when it is not installed",
                     enum=["Gordon", "Interpolate"], default="Gordon")
        add_property(obj, "App::PropertyIntegerList", "SurfaceSizes",
                     GROUP_INFO, "Sections in each surface", read_only=True)
        add_property(obj, "App::PropertyIntegerList", "RejectedChains",
                     GROUP_INFO, "Chains dropped, by index", read_only=True)
        add_property(obj, "App::PropertyFloat", "WorstTwist", GROUP_INFO,
                     "Largest column disagreement found, in degrees",
                     read_only=True)
        add_property(obj, "App::PropertyString", "Status", GROUP_INFO,
                     "Result of the last recompute", read_only=True)

    def onDocumentRestored(self, obj):
        self.setup(obj)

    # -- computation -------------------------------------------------------

    @staticmethod
    def runs_by_section(obj):
        """``[[points, ...], ...]`` grouped per section, from the source."""
        source = getattr(obj, "Source", None)
        shape = getattr(source, "Shape", None)
        if shape is None or shape.isNull() or not shape.Wires:
            return []

        wires = list(shape.Wires)
        counts = [int(n) for n in (getattr(source, "ContourCount", []) or [])]
        if not counts or sum(counts) != len(wires):
            return [[wire_points(w)[0]] for w in wires]

        groups = []
        index = 0
        for count in counts:
            groups.append([wire_points(w)[0] for w in wires[index:index + count]])
            index += count
        return groups

    @staticmethod
    def auto_threshold(groups, normal):
        """How far a wall may move sideways between two sections.

        Taken from the sections themselves: the distance between neighbouring
        planes, times a small factor.  A wall on a steep part moves further per
        section than one on a straight part, and the spacing is the only thing
        in the data that knows the difference.
        """
        offsets = []
        for section in groups:
            if section:
                offsets.append(float(np.dot(ct.centroid(section[0]), normal)))
        if len(offsets) < 2:
            return 1.0
        spacing = float(np.median(np.abs(np.diff(sorted(offsets)))))
        return max(spacing * 2.0, 1e-6)

    def execute(self, obj):
        groups = self.runs_by_section(obj)
        if len(groups) < 2:
            obj.Status = "need at least two sections of runs"
            return

        try:
            direction = getattr(getattr(obj, "Source", None), "Direction", None)
            normal = (np.array([direction.x, direction.y, direction.z])
                      if direction is not None else np.array([0.0, 0.0, 1.0]))
            threshold = value(obj.OverlapDistance)
            if threshold <= 0.0:
                threshold = self.auto_threshold(groups, normal)
            chains = pr.build_chains_by_overlap(
                groups, normal, threshold, float(obj.MinOverlap))

            wanted_gordon = str(obj.Method) == "Gordon" and sf.gordon_available()
            if str(obj.Method) == "Gordon" and not wanted_gordon:
                warn("%s: the Curves workbench is not installed, falling back "
                     "to point interpolation" % obj.Name)

            faces = []
            sizes = []
            rejected = []
            worst_twist = 0.0
            for index, chain in enumerate(chains):
                if len(chain) < int(obj.MinSections):
                    rejected.append(index)
                    continue
                runs = [groups[section][run] for section, run in chain]
                grid = sf.grid_from_runs(runs, max(2, int(obj.Columns)))
                if grid is None:
                    rejected.append(index)
                    continue

                twist, stretch = sf.grid_quality(grid)
                worst_twist = max(worst_twist, twist)
                if twist > value(obj.MaxTwist) or stretch > float(obj.MaxStretch):
                    rejected.append(index)
                    warn("%s: chain %d twists %.0f deg and stretches %.1fx, "
                         "skipped" % (obj.Name, index, twist, stretch))
                    continue

                surface = None
                if wanted_gordon:
                    try:
                        surface = sf.surface_from_grid_gordon(grid)
                    except Exception as exc:  # noqa: BLE001
                        warn("%s: chain %d - Gordon failed (%s), interpolating"
                             % (obj.Name, index, str(exc)[:60]))
                if surface is None:
                    try:
                        surface = sf.surface_from_grid(grid)
                    except Exception as exc:  # noqa: BLE001
                        rejected.append(index)
                        warn("%s: chain %d could not be surfaced (%s)"
                             % (obj.Name, index, exc))
                        continue
                faces.append(surface.toShape())
                sizes.append(len(chain))
        except Exception as exc:  # noqa: BLE001
            obj.Status = "failed: %s" % exc
            warn("%s: %s" % (obj.Name, obj.Status))
            return

        obj.Shape = Part.Compound(faces) if faces else Part.Compound([])
        obj.SurfaceSizes = [int(n) for n in sizes]
        obj.RejectedChains = [int(i) for i in rejected]
        obj.WorstTwist = float(worst_twist)
        obj.Status = ("%d surfaces from %d chains via %s, worst twist %.0f deg"
                      % (len(faces), len(chains),
                         "Gordon" if wanted_gordon else "interpolation",
                         worst_twist))
        if rejected:
            obj.Status += ", %d rejected" % len(rejected)

    def dumps(self):
        return None

    def loads(self, state):
        return None

    def __getstate__(self):
        return None

    def __setstate__(self, state):
        return None


class ViewProviderWallSurfaces(ViewProviderBase):
    icon = "SectionLoft_Loft.svg"


def make_wall_surfaces(doc, source=None, name="WallSurfaces"):
    obj = doc.addObject("Part::FeaturePython", name)
    WallSurfaces(obj)
    if obj.ViewObject is not None:
        ViewProviderWallSurfaces(obj.ViewObject)
    if source is not None:
        obj.Source = source
    return obj
