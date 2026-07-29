"""Procedurally generated test data.

No binary meshes in the repository: every fixture is an analytic shape, so the
expected result is a closed-form expression rather than a number someone once
measured and pasted into the test.

The polyline fixtures are pure numpy.  The mesh fixtures need FreeCAD and are
imported lazily so that the numpy-only tests still run under plain CPython.
"""

import numpy as np


# --------------------------------------------------------------------------
# Polylines
# --------------------------------------------------------------------------

def circle(radius=25.0, n=64, z=0.0, noise=0.0, seed=0):
    """Closed circular polyline (unique points, no repeated endpoint)."""
    rng = np.random.default_rng(seed)
    t = np.linspace(0.0, 2.0 * np.pi, n, endpoint=False)
    r = radius + (rng.normal(0.0, noise, n) if noise else 0.0)
    return np.column_stack((r * np.cos(t), r * np.sin(t), np.full(n, z)))


def rectangle(width=40.0, height=20.0, per_side=10, z=0.0):
    """Closed rectangular polyline with corners at exactly four vertices."""
    w, h = width / 2.0, height / 2.0
    corners = [(-w, -h), (w, -h), (w, h), (-w, h)]
    pts = []
    for i in range(4):
        a = np.array(corners[i], dtype=float)
        b = np.array(corners[(i + 1) % 4], dtype=float)
        for k in range(per_side):
            p = a + (b - a) * (k / per_side)
            pts.append((p[0], p[1], z))
    return np.array(pts, dtype=float)


def helix_guide(radius=30.0, height=100.0, turns=0.25, n=50):
    t = np.linspace(0.0, 1.0, n)
    a = 2.0 * np.pi * turns * t
    return np.column_stack((radius * np.cos(a), radius * np.sin(a), height * t))


# --------------------------------------------------------------------------
# Meshes (FreeCAD required)
# --------------------------------------------------------------------------

def sphere_mesh(radius=50.0, sampling=60):
    import Mesh
    return Mesh.createSphere(float(radius), int(sampling))


def cylinder_mesh(radius=20.0, height=80.0, sampling=64):
    """Closed cylinder from z=0 to z=height, axis along +Z.

    ``Mesh.createCylinder`` builds it along **X**, which is easy to miss and
    turns every "slice the cylinder along Z" test into "slice a lying cylinder",
    where the cross-sections are rectangles.
    """
    import Mesh
    mesh = Mesh.createCylinder(float(radius), float(height), True, 1.0,
                               int(sampling))
    mesh.rotate(0.0, np.pi / 2.0, 0.0)   # +X axis -> -Z
    mesh.translate(0.0, 0.0, float(height))
    return mesh


def box_mesh(length=40.0, width=30.0, height=60.0):
    import Mesh
    return Mesh.createBox(float(length), float(width), float(height))


def two_cylinders_mesh(radius=10.0, height=60.0, separation=40.0, sampling=48):
    """Two parallel Z cylinders - every cross-section has exactly two contours.

    Note ``translate()``, not ``Placement``: on a ``Mesh.Mesh`` kernel object
    (as opposed to a ``Mesh::Feature`` document object) assigning ``Placement``
    does not move the points.
    """
    import Mesh
    a = cylinder_mesh(radius, height, sampling)
    b = cylinder_mesh(radius, height, sampling)
    b.translate(float(separation), 0.0, 0.0)
    merged = Mesh.Mesh(a)
    merged.addMesh(b)
    return merged


def holed_mesh():
    """A non-manifold mesh: a box with a few facets removed."""
    import Mesh
    mesh = box_mesh()
    facets = mesh.Topology[1]
    keep = [f for i, f in enumerate(facets) if i % 5 != 0]
    broken = Mesh.Mesh()
    points = mesh.Topology[0]
    for f in keep:
        broken.addFacet(points[f[0]], points[f[1]], points[f[2]])
    return broken
