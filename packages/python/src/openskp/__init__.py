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

import logging
from collections.abc import Sequence
from importlib.metadata import PackageNotFoundError, version as _version

from .codegen import to_python_code
from .create import ComponentDefinitionBuilder, DocumentOptions, SkpBuilder, SkpWriteError, create
from .create_vff import VffBuilder, VffComponentDefinitionBuilder, create_vff
from .edit import open_existing
from .errors import SkpParseError
from .instanced_scene import (
    InstancedCurveResource,
    InstancedMeshResource,
    InstancedNode,
    InstancedScene,
    LocalCurve,
    LocalPrimitive,
    SceneBounds,
)
from .model import ArcCurve, SkpFile, SkpModel, loose_edge_runs
from .scene import Scene, InstanceNode, MeshMetadata, GlbPrimitive, SceneTexture

logging.getLogger("openskp").addHandler(logging.NullHandler())

try:
    __version__: str = _version("openskp")
except PackageNotFoundError:
    __version__ = "0.0.0+unknown"
__all__: list[str] = [
    "SkpFile",
    "SkpModel",
    "ArcCurve",
    "Scene",
    "InstanceNode",
    "MeshMetadata",
    "GlbPrimitive",
    "SceneTexture",
    "InstancedScene",
    "InstancedNode",
    "InstancedMeshResource",
    "InstancedCurveResource",
    "LocalPrimitive",
    "LocalCurve",
    "SceneBounds",
    "SkpParseError",
    "create",
    "create_vff",
    "DocumentOptions",
    "SkpBuilder",
    "VffBuilder",
    "VffComponentDefinitionBuilder",
    "ComponentDefinitionBuilder",
    "SkpWriteError",
    "open_existing",
    "to_python_code",
    "loose_edge_runs",
    "__version__",
]


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point, delegated to :func:`openskp.cli.main`."""
    from .cli import main as _cli_main

    return _cli_main(argv)
