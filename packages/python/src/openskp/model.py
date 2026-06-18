"""Data model for parsed SketchUp files.

This module defines the core dataclasses that represent every entity
extracted from an SKP file — vertices, edges, faces, layers, materials,
component definitions, instances, and the top-level model container.

It also provides the :class:`SkpFile` entry-point that orchestrates
parsing from a ``.skp`` file path.

Typical usage::

    from openskp import SkpFile

    skp = SkpFile.open("building.skp")
    model = skp.parse()
    print(model.version, len(model.definitions))
"""

from __future__ import annotations

import pathlib
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


# ── TLV node (raw parse tree) ─────────────────────────────────────────────


@dataclass
class TlvNode:
    """A single Tag-Length-Value node in the binary parse tree.

    Attributes:
        offset: Byte offset of this node in the original buffer.
        tag: Two-byte tag encoded as upper-case hex (e.g. ``"FF01"``).
        size: Payload length in bytes.
        children: Nested child nodes (empty for leaf nodes).
        payload: Raw payload bytes (empty when *children* is populated).
    """

    offset: int
    tag: str
    size: int
    children: List["TlvNode"] = field(default_factory=list)
    payload: bytes = b""


# ── Geometry primitives ───────────────────────────────────────────────────


@dataclass
class Vertex:
    """A 3-D vertex.

    Attributes:
        id: Unique vertex identifier within its definition.
        x: X coordinate (inches in SketchUp's internal unit).
        y: Y coordinate.
        z: Z coordinate.
    """

    id: int
    x: float
    y: float
    z: float


@dataclass
class Edge:
    """A directed edge connecting two vertices.

    Attributes:
        id: Unique edge identifier within its definition.
        v1_id: Start-vertex ID.
        v2_id: End-vertex ID.
    """

    id: int
    v1_id: int
    v2_id: int


@dataclass
class Face:
    """A planar polygon face defined by edge loops.

    Attributes:
        id: Unique face identifier within its definition.
        loops: Ordered list of loops.  Each loop is a list of
            ``(edge_id, orientation)`` tuples where *orientation* is
            ``1`` for forward or ``-1`` for reversed.
        normal: Optional outward-facing normal vector ``(nx, ny, nz)``.
    """

    id: int
    loops: List[List[Tuple[int, int]]] = field(default_factory=list)
    normal: Optional[Tuple[float, float, float]] = None


# ── Layers & Materials ────────────────────────────────────────────────────


@dataclass
class Layer:
    """A SketchUp layer (tag).

    Attributes:
        name: Human-readable layer name.
        color_r: Red channel (0–255).
        color_g: Green channel (0–255).
        color_b: Blue channel (0–255).
    """

    name: str
    color_r: int = 200
    color_g: int = 200
    color_b: int = 200


@dataclass
class Material:
    """A surface material.

    Attributes:
        name: Material name.
        color: RGBA colour tuple ``(r, g, b, a)`` with each value in 0–255.
        transparency: Opacity factor where ``0.0`` is fully transparent and
            ``1.0`` is fully opaque.
    """

    name: str
    color: Tuple[int, int, int, int] = (200, 200, 200, 255)
    transparency: float = 1.0


# ── Component hierarchy ───────────────────────────────────────────────────


@dataclass
class Instance:
    """A placed instance (component or group) in the scene graph.

    Attributes:
        name: Display name of the instance.
        ref_idx: Index into :attr:`SkpModel.definitions` for the
            referenced component definition.
        guid: Globally-unique identifier string.
        matrix: 4×4 transformation matrix stored as a flat 16-element list
            in **column-major** order.
        layer: Layer name this instance belongs to.
        properties: Arbitrary key/value dynamic attributes.
        children: Nested child instances forming a subtree.
    """

    name: str = ""
    ref_idx: int = -1
    guid: str = ""
    matrix: List[float] = field(default_factory=lambda: [
        1, 0, 0, 0,
        0, 1, 0, 0,
        0, 0, 1, 0,
        0, 0, 0, 1,
    ])
    layer: str = ""
    properties: Dict[str, str] = field(default_factory=dict)
    children: List["Instance"] = field(default_factory=list)


@dataclass
class Definition:
    """A component definition containing reusable geometry.

    Attributes:
        id: Internal definition index.
        guid: Globally-unique identifier.
        name: Human-readable component name.
        vertices: Mapping of vertex ID → :class:`Vertex`.
        edges: Mapping of edge ID → :class:`Edge`.
        faces: Mapping of face ID → :class:`Face`.
        instances: Child instances placed inside this definition.
    """

    id: int = 0
    guid: str = ""
    name: str = ""
    vertices: Dict[int, Vertex] = field(default_factory=dict)
    edges: Dict[int, Edge] = field(default_factory=dict)
    faces: Dict[int, Face] = field(default_factory=dict)
    instances: List[Instance] = field(default_factory=list)


# ── Top-level model ──────────────────────────────────────────────────────


@dataclass
class SkpModel:
    """Complete parsed representation of a SketchUp file.

    Attributes:
        version: SketchUp file-format version number.
        definitions: Mapping of definition index → :class:`Definition`.
        layers: List of :class:`Layer` objects found in the file.
        materials: List of :class:`Material` objects found in the file.
        scene_hierarchy: Top-level :class:`Instance` list forming the
            scene graph.
        mesh_index: Pre-built index mapping definition IDs to triangulated
            mesh data for fast export.
    """

    version: str = "unknown"
    definitions: Dict[int, Definition] = field(default_factory=dict)
    layers: List[Layer] = field(default_factory=list)
    materials: List[Material] = field(default_factory=list)
    scene_hierarchy: List[Instance] = field(default_factory=list)
    mesh_index: Dict[int, Any] = field(default_factory=dict)


# ── SkpFile entry-point ──────────────────────────────────────────────────


class SkpFile:
    """High-level entry point for opening and parsing ``.skp`` files.

    Example::

        skp = SkpFile.open("model.skp")
        model = skp.parse()
        for layer in model.layers:
            print(layer.name)

    Attributes:
        path: Resolved path to the ``.skp`` file.
    """

    def __init__(self, path: pathlib.Path) -> None:
        self.path: pathlib.Path = path
        self._raw_data: Optional[bytes] = None
        self._model_data: Optional[bytes] = None
        self._material_files: Dict[str, bytes] = {}

    # ── Construction ──────────────────────────────────────────────────

    @classmethod
    def open(cls, filepath: str | pathlib.Path) -> "SkpFile":
        """Open a SketchUp file for parsing.

        Args:
            filepath: Path to a ``.skp`` file on disk.

        Returns:
            A new :class:`SkpFile` ready for :meth:`parse`.

        Raises:
            FileNotFoundError: If *filepath* does not exist.
            ValueError: If *filepath* does not have a ``.skp`` extension.
        """
        p = pathlib.Path(filepath).resolve()
        if not p.exists():
            raise FileNotFoundError(f"File not found: {p}")
        if p.suffix.lower() != ".skp":
            raise ValueError(f"Expected a .skp file, got: {p.suffix}")
        return cls(p)

    # ── Parsing pipeline ─────────────────────────────────────────────

    def parse(self) -> SkpModel:
        """Run the full parsing pipeline and return a populated model.

        The pipeline:

        1. Read the file bytes and validate / extract the VFF container.
        2. Parse TLV nodes from ``model.dat``.
        3. Extract geometry (vertices, edges, faces) into definitions.
        4. Parse material XMLs and layer colours.
        5. Resolve metadata — dynamic properties, layer IDs, hierarchy.

        Returns:
            A fully-populated :class:`SkpModel`.

        Raises:
            ValueError: If the file is not a valid SketchUp container.
        """
        from . import _core

        # Use the proven core engine
        parsed = _core.full_parse(str(self.path))

        model = SkpModel()
        model.version = parsed.get("version", "unknown")

        # Convert defs_dict to Definition dataclasses
        for def_id, d in parsed["defs_dict"].items():
            builder = d["builder"]
            defn = Definition(
                id=def_id if isinstance(def_id, int) else 0,
                guid=d.get("guid", ""),
                name=d.get("name", "") or "",
            )
            # Populate vertices
            for v_id, (x, y, z) in builder.vertices.items():
                defn.vertices[v_id] = Vertex(id=v_id, x=x, y=y, z=z)
            # Populate edges
            for e_id, (v1, v2) in builder.edges.items():
                defn.edges[e_id] = Edge(id=e_id, v1_id=v1 or 0, v2_id=v2 or 0)
            # Populate faces
            for f_id, f_data in builder.faces.items():
                defn.faces[f_id] = Face(
                    id=f_id,
                    loops=f_data.get("loops", []),
                    normal=f_data.get("normal"),
                )
            # Populate instances
            for inst in builder.instances:
                defn.instances.append(Instance(
                    name=inst.get("name", "") or "",
                    ref_idx=inst.get("ref_idx"),
                    guid=inst.get("ref_guid", ""),
                    matrix=inst.get("matrix", []),
                ))
            model.definitions[def_id] = defn

        # Convert layers
        for name, (r, g, b) in parsed["layer_colors"].items():
            model.layers.append(Layer(name=name, color_r=r, color_g=g, color_b=b))

        # Convert materials
        for mat_data in parsed["materials"].values():
            c = mat_data.get("color", {})
            model.materials.append(Material(
                name=mat_data.get("name", ""),
                color=(c.get("r", 128), c.get("g", 128), c.get("b", 128)),
                transparency=mat_data.get("transparency", 0.5),
            ))

        # Store raw parsed data for export use
        self._parsed = parsed

        return model

