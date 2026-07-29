from setuptools import setup, find_packages

with open("freecad/sectionloft/version.py") as handle:
    namespace = {}
    exec(handle.read(), namespace)          # noqa: S102 - single-line assignment
    version = namespace["__version__"]

setup(
    name="freecad-sectionloft",
    version=version,
    packages=find_packages(include=["freecad", "freecad.*"]),
    maintainer="SectionLoft contributors",
    url="https://github.com/example/sectionloft",
    description="Mesh cross-sections to fitted B-spline sections to loft, "
                "for FreeCAD",
    license="LGPL-2.1-or-later",
    python_requires=">=3.8",
    install_requires=["numpy"],             # already present in FreeCAD
    include_package_data=True,
)
