"""Shared plumbing for the FeaturePython objects."""

import os

import numpy as np

import FreeCAD as App

LOG_PREFIX = "[SectionLoft] "

ICON_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "icons")


def icon_path(name):
    """Absolute path to an icon.  FreeCAD wants a path or an XPM, not a name."""
    return os.path.join(ICON_DIR, name)


def warn(message):
    App.Console.PrintWarning(LOG_PREFIX + message + "\n")


def log(message):
    App.Console.PrintMessage(LOG_PREFIX + message + "\n")


def error(message):
    App.Console.PrintError(LOG_PREFIX + message + "\n")


def value(quantity):
    """Plain float from a property.

    ``App::PropertyLength`` / ``Distance`` / ``Angle`` hand back a
    ``Base.Quantity``, while ``Float`` and ``Integer`` hand back a number.  Every
    read goes through here so no call site has to remember which is which.
    """
    return float(getattr(quantity, "Value", quantity))


def add_property(obj, kind, name, group, doc, default=None, enum=None,
                 read_only=False):
    """Add a property unless it is already there.

    Idempotent by design: the same call list runs from ``__init__`` and from
    ``onDocumentRestored``, which is what lets a document saved by an older
    version pick up properties added since without the user noticing.

    Returns True if the property was created.
    """
    if hasattr(obj, name):
        return False
    obj.addProperty(kind, name, group, doc)
    if enum is not None:
        setattr(obj, name, list(enum))
    if default is not None:
        setattr(obj, name, default)
    if read_only:
        obj.setEditorMode(name, 1)
    return True


def vector(v):
    return np.array([v.x, v.y, v.z], dtype=float)


def wire_points(wire):
    """``(points, closed)`` for a polyline wire.

    ``OrderedVertexes`` follows the wire rather than the underlying edge list,
    which matters: for a closed polygon the vertex order out of ``Vertexes`` is
    not the traversal order.
    """
    vertexes = wire.OrderedVertexes
    pts = np.array([[v.Point.x, v.Point.y, v.Point.z] for v in vertexes],
                   dtype=float)
    return pts, bool(wire.isClosed())


class ViewProviderBase:
    """Minimal view provider: an icon, and children that hide under the parent.

    ``dumps``/``loads`` are the FreeCAD 1.x spelling; the ``__getstate__`` pair
    is kept because documents written by older versions call it on restore.
    """

    icon = ""

    def __init__(self, vobj):
        vobj.Proxy = self

    def attach(self, vobj):
        self.Object = vobj.Object

    def getIcon(self):
        return icon_path(self.icon)

    def claimChildren(self):
        obj = getattr(self, "Object", None)
        if obj is None:
            return []
        children = []
        for name in ("Source", "Sections"):
            child = getattr(obj, name, None)
            if child is not None:
                children.append(child)
        return children

    def dumps(self):
        return None

    def loads(self, state):
        return None

    def __getstate__(self):
        return None

    def __setstate__(self, state):
        return None
