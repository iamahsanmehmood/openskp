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

from .model import SkpFile, SkpModel

__version__: str = "0.1.1"
__all__: list[str] = ["SkpFile", "SkpModel", "__version__"]


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
            print(f"Instances:   {len(model.scene_hierarchy)}")
        except Exception as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)
