"""Headless test runner.

    freecadcmd run_tests.py            # everything
    freecadcmd run_tests.py planes     # a subset, by module suffix
    python run_tests.py                # numpy-only modules, no FreeCAD needed

Exits non-zero on failure, so it drops straight into CI.
"""

import os
import sys
import unittest

# The FreeCAD console is on the system codepage; a failure message containing a
# Polish character otherwise kills the runner while printing the traceback.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# freecadcmd imports its own ``freecad`` namespace package before this script
# runs, so extending sys.path is not enough - the already-imported package needs
# to be told about this checkout.
#
# Prepended, never appended: if the addon is also installed under Mod/, an
# appended path loses and the suite silently tests the installed copy instead of
# the working tree.  Which it did, until this line was written the other way up.
if "freecad" in sys.modules:
    _pkg_path = os.path.join(ROOT, "freecad")
    _ns_path = sys.modules["freecad"].__path__
    while _pkg_path in list(_ns_path):
        _ns_path.remove(_pkg_path)
    _ns_path.insert(0, _pkg_path)

# FreeCAD imports the addon's init.py at startup, so when it is also installed
# under Mod/ the package is already in sys.modules and pointing at the installed
# copy - fixing the namespace path alone changes nothing.  Drop it and let the
# import machinery find the working tree.
for _name in [n for n in sys.modules if n.startswith("freecad.sectionloft")]:
    del sys.modules[_name]

PURE = ["test_planes", "test_contours", "test_polyline", "test_pairing",
        "test_envelope", "test_packaging"]
FREECAD = ["test_fitting", "test_pipeline", "test_objects", "test_wizard"]


def main(argv):
    try:
        import FreeCAD  # noqa: F401
        modules = PURE + FREECAD
    except ImportError:
        modules = PURE
        print("FreeCAD not importable - running numpy-only tests "
              "(use freecadcmd for the full set)\n")

    if argv:
        wanted = [m for m in modules if any(a in m for a in argv)]
    else:
        wanted = modules

    suite = unittest.TestSuite()
    loader = unittest.TestLoader()
    for name in wanted:
        module_suite = loader.loadTestsFromName("freecad.sectionloft.tests." + name)
        print("%-16s %3d tests" % (name, module_suite.countTestCases()))
        suite.addTests(module_suite)
    if loader.errors:
        for message in loader.errors:
            print("LOADER ERROR: %s" % message)
    print("-" * 40)

    # freecadcmd does not surface stderr, which is where unittest writes by
    # default - route the report to stdout so the run is visible.
    result = unittest.TextTestRunner(stream=sys.stdout, verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


# Not guarded by ``__name__ == "__main__"``: freecadcmd executes the script
# under a module name of its own choosing, so the guard would never fire.
_ARGS = [a for a in sys.argv[1:] if not a.lower().endswith(".py")]
_CODE = main(_ARGS)
print("EXIT %d" % _CODE)
if __name__ == "__main__":
    sys.exit(_CODE)
