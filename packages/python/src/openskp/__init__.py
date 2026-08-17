"""OpenSKP — Open-source SketchUp binary file parser.

This is the public entry point for the ``openskp`` package.  Import
:class:`SkpFile` to open and parse ``.skp`` files, and :class:`SkpModel`
to work with the resulting data.

Example::

    from openskp import SkpFile

    skp = SkpFile.open("building.skp")
    model = skp.parse()

    for layer in model.layers:
        print(layer.name)
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version as _version

from .create import ComponentDefinitionBuilder, SkpBuilder, SkpWriteError, create
from .errors import SkpParseError
from .model import SkpFile, SkpModel
from .scene import Scene, InstanceNode, MeshMetadata, GlbPrimitive

try:
    __version__: str = _version("openskp")
except PackageNotFoundError:
    __version__ = "0.0.0+unknown"
__all__: list[str] = [
    "SkpFile",
    "SkpModel",
    "Scene",
    "InstanceNode",
    "MeshMetadata",
    "GlbPrimitive",
    "SkpParseError",
    "create",
    "SkpBuilder",
    "ComponentDefinitionBuilder",
    "SkpWriteError",
    "__version__",
]


def main() -> None:
    """CLI entry point (placeholder).

    Prints version information.  A richer CLI may be added in future
    releases.
    """
    import sys

    print(f"openskp {__version__}")
    if len(sys.argv) > 1:
        filepath = sys.argv[1]
        try:
            skp = SkpFile.open(filepath)
            model = skp.parse()
            print(f"Version:     {model.version}")
            print(f"Definitions: {len(model.definitions)}")
            print(f"Layers:      {len(model.layers)}")
            print(f"Materials:   {len(model.materials)}")
        except Exception as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)
