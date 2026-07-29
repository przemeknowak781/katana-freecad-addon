"""Everything that only breaks at install time.

These are cheap and they catch the class of bug that no algorithm test can: a
renamed icon, a command pointing at a file that is not shipped, a GUI module
with a syntax error that nobody notices until FreeCAD is restarted.
"""

import ast
import os
import re
import unittest

PACKAGE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(os.path.dirname(PACKAGE))
ICON_DIR = os.path.join(PACKAGE, "icons")


def python_files():
    for folder, _, names in os.walk(PACKAGE):
        if "__pycache__" in folder:
            continue
        for name in names:
            if name.endswith(".py"):
                yield os.path.join(folder, name)


class TestSyntax(unittest.TestCase):
    def test_every_module_parses(self):
        """Covers gui/ too, which cannot be imported without a running GUI."""
        for path in python_files():
            with open(path, encoding="utf-8") as handle:
                source = handle.read()
            try:
                ast.parse(source, filename=path)
            except SyntaxError as exc:
                self.fail("%s: %s" % (path, exc))

    def test_the_macro_parses(self):
        path = os.path.join(ROOT, "macro", "SectionLoft.FCMacro")
        with open(path, encoding="utf-8") as handle:
            ast.parse(handle.read(), filename=path)


class TestIcons(unittest.TestCase):
    def referenced_icons(self):
        names = set()
        pattern = re.compile(r'"(SectionLoft_[A-Za-z]+\.svg)"')
        for path in python_files():
            with open(path, encoding="utf-8") as handle:
                names.update(pattern.findall(handle.read()))
        return names

    def test_every_referenced_icon_exists(self):
        referenced = self.referenced_icons()
        self.assertTrue(referenced, "no icons referenced - pattern went stale")
        for name in sorted(referenced):
            self.assertTrue(os.path.isfile(os.path.join(ICON_DIR, name)),
                            "missing icon: %s" % name)

    def test_icons_are_valid_xml_svg(self):
        import xml.etree.ElementTree as ET
        for name in os.listdir(ICON_DIR):
            path = os.path.join(ICON_DIR, name)
            root = ET.parse(path).getroot()
            self.assertTrue(root.tag.endswith("svg"), "%s is not an SVG" % name)
            self.assertIn("viewBox", root.attrib, "%s has no viewBox" % name)

    def test_no_orphan_icons(self):
        shipped = {n for n in os.listdir(ICON_DIR) if n.endswith(".svg")}
        used = self.referenced_icons() | {"SectionLoft_Workbench.svg"}
        self.assertEqual(shipped - used, set(),
                         "icons shipped but never referenced")


class TestPackageMetadata(unittest.TestCase):
    def test_version_matches_package_xml(self):
        from freecad.sectionloft.version import __version__
        with open(os.path.join(ROOT, "package.xml"), encoding="utf-8") as handle:
            content = handle.read()
        self.assertIn("<version>%s</version>" % __version__, content)

    def test_init_gui_defines_a_workbench(self):
        path = os.path.join(PACKAGE, "init_gui.py")
        with open(path, encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        classes = [n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
        self.assertIn("SectionLoftWorkbench", classes)


if __name__ == "__main__":
    unittest.main()
