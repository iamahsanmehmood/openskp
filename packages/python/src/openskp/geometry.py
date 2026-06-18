"""Geometry extraction from parsed TLV nodes.

This module walks the TLV node tree produced by :mod:`openskp.parser` and
extracts vertices, edges, and faces into :class:`~openskp.model.Definition`
objects.  It also provides :class:`GeometryBuilder`, which triangulates
faces and builds a mesh index suitable for export.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from .model import Definition, Edge, Face, Instance, TlvNode, Vertex
from .parser import read_f64, read_u32, parse_var_int
from .triangulator import triangulate_face_3d


# ── Tag constants used for geometry nodes ─────────────────────────────────

_TAG_DEFINITION: str = "FF03"
_TAG_VERTEX: str = "0300"
_TAG_EDGE: str = "0400"
_TAG_FACE: str = "0500"
_TAG_LOOP: str = "0600"
_TAG_INSTANCE: str = "FF05"
_TAG_GUID: str = "0100"
_TAG_NAME: str = "0200"


# ── Vertex / Edge / Face extraction ──────────────────────────────────────


def _extract_vertex(node: TlvNode, vid: int) -> Optional[Vertex]:
    """Parse a vertex payload into a :class:`Vertex`.

    The expected payload layout is 24 bytes: three consecutive ``float64``
    values for X, Y, Z.

    Args:
        node: TLV node whose payload contains vertex data.
        vid: Vertex identifier to assign.

    Returns:
        A :class:`Vertex`, or ``None`` if the payload is too short.
    """
    payload = node.payload
    if len(payload) < 24:
        return None
    x = read_f64(payload, 0)
    y = read_f64(payload, 8)
    z = read_f64(payload, 16)
    return Vertex(id=vid, x=x, y=y, z=z)


def _extract_edge(node: TlvNode, eid: int) -> Optional[Edge]:
    """Parse an edge payload into an :class:`Edge`.

    The expected payload layout is (at minimum) 8 bytes: two ``uint32``
    vertex indices.

    Args:
        node: TLV node whose payload contains edge data.
        eid: Edge identifier to assign.

    Returns:
        An :class:`Edge`, or ``None`` if the payload is too short.
    """
    payload = node.payload
    if len(payload) < 8:
        return None
    v1 = read_u32(payload, 0)
    v2 = read_u32(payload, 4)
    return Edge(id=eid, v1_id=v1, v2_id=v2)


def _extract_face_loops(node: TlvNode) -> List[List[Tuple[int, int]]]:
    """Extract edge-loop references from a face node's children.

    Each loop child contains a list of ``(edge_id, orientation)`` pairs
    encoded as consecutive ``uint32`` values.

    Args:
        node: TLV node representing a face.

    Returns:
        A list of loops, each loop being a list of ``(edge_id, orient)``
        tuples.
    """
    loops: List[List[Tuple[int, int]]] = []
    for child in node.children:
        if child.tag == _TAG_LOOP:
            loop: List[Tuple[int, int]] = []
            payload = child.payload
            idx = 0
            while idx + 8 <= len(payload):
                edge_id = read_u32(payload, idx)
                orient = read_u32(payload, idx + 4)
                # Normalise orientation to 1 / -1
                orient_val = 1 if orient == 0 else -1
                loop.append((edge_id, orient_val))
                idx += 8
            if loop:
                loops.append(loop)
    return loops


def _compute_face_normal(
    loops: List[List[Tuple[int, int]]],
    edges: Dict[int, Edge],
    vertices: Dict[int, Vertex],
) -> Optional[Tuple[float, float, float]]:
    """Compute a face normal using the Newell method on its first loop.

    Args:
        loops: Face loops as ``(edge_id, orient)`` tuples.
        edges: Edge lookup by ID.
        vertices: Vertex lookup by ID.

    Returns:
        Unit normal ``(nx, ny, nz)`` or ``None`` on degenerate geometry.
    """
    if not loops:
        return None

    # Collect ordered vertex positions from first loop
    pts: List[Tuple[float, float, float]] = []
    for edge_id, orient in loops[0]:
        e = edges.get(edge_id)
        if e is None:
            continue
        vid = e.v1_id if orient == 1 else e.v2_id
        v = vertices.get(vid)
        if v is not None:
            pts.append((v.x, v.y, v.z))

    if len(pts) < 3:
        return None

    # Newell method
    nx = ny = nz = 0.0
    n = len(pts)
    for i in range(n):
        cur = pts[i]
        nxt = pts[(i + 1) % n]
        nx += (cur[1] - nxt[1]) * (cur[2] + nxt[2])
        ny += (cur[2] - nxt[2]) * (cur[0] + nxt[0])
        nz += (cur[0] - nxt[0]) * (cur[1] + nxt[1])

    length = (nx * nx + ny * ny + nz * nz) ** 0.5
    if length < 1e-12:
        return None
    return (nx / length, ny / length, nz / length)


# ── Definition-level extraction ──────────────────────────────────────────


def _build_definition(
    nodes: List[TlvNode],
    def_id: int,
) -> Definition:
    """Walk a list of TLV children and populate a single Definition.

    Args:
        nodes: Child nodes of a definition container tag.
        def_id: Definition identifier to assign.

    Returns:
        A populated :class:`Definition`.
    """
    defn = Definition(id=def_id)
    vid = 0
    eid = 0
    fid = 0

    for node in nodes:
        if node.tag == _TAG_GUID and node.payload:
            try:
                defn.guid = node.payload.decode("utf-8", errors="replace").strip("\x00")
            except Exception:
                pass

        elif node.tag == _TAG_NAME and node.payload:
            try:
                defn.name = node.payload.decode("utf-8", errors="replace").strip("\x00")
            except Exception:
                pass

        elif node.tag == _TAG_VERTEX:
            v = _extract_vertex(node, vid)
            if v is not None:
                defn.vertices[vid] = v
                vid += 1

        elif node.tag == _TAG_EDGE:
            e = _extract_edge(node, eid)
            if e is not None:
                defn.edges[eid] = e
                eid += 1

        elif node.tag == _TAG_FACE:
            loops = _extract_face_loops(node)
            normal = _compute_face_normal(loops, defn.edges, defn.vertices)
            face = Face(id=fid, loops=loops, normal=normal)
            defn.faces[fid] = face
            fid += 1

        # Recursively extract from children
        if node.children:
            child_def = _build_definition(node.children, def_id)
            # Merge geometry
            for cv_id, cv in child_def.vertices.items():
                new_id = vid
                defn.vertices[new_id] = Vertex(new_id, cv.x, cv.y, cv.z)
                vid += 1
            for ce_id, ce in child_def.edges.items():
                new_id = eid
                defn.edges[new_id] = Edge(new_id, ce.v1_id, ce.v2_id)
                eid += 1
            for cf_id, cf in child_def.faces.items():
                new_id = fid
                defn.faces[new_id] = Face(new_id, cf.loops, cf.normal)
                fid += 1

    return defn


# ── Public API ────────────────────────────────────────────────────────────


def extract_geometry_from_nodes(
    nodes: List[TlvNode],
) -> Dict[int, Definition]:
    """Walk the entire TLV tree and extract all component definitions.

    Each definition container (tag ``FF03``) is parsed into a
    :class:`Definition` containing vertices, edges, and faces.  Nodes
    that are not definition containers contribute geometry to a
    *root definition* at index ``0``.

    Args:
        nodes: Top-level TLV nodes returned by
            :func:`~openskp.parser.parse_tlv_recursive`.

    Returns:
        Mapping of definition index → :class:`Definition`.
    """
    definitions: Dict[int, Definition] = {}
    def_id: int = 0

    # Root definition for loose geometry
    root = Definition(id=def_id, name="__root__")
    definitions[def_id] = root
    def_id += 1

    for node in nodes:
        if node.tag == _TAG_DEFINITION and node.children:
            defn = _build_definition(node.children, def_id)
            defn.id = def_id
            definitions[def_id] = defn
            def_id += 1
        elif node.children:
            # Recurse into non-definition containers
            child_defs = extract_geometry_from_nodes(node.children)
            for cd in child_defs.values():
                if cd.vertices or cd.edges or cd.faces:
                    cd.id = def_id
                    definitions[def_id] = cd
                    def_id += 1

    return definitions


def build_definition_geometry(
    defn: Definition,
) -> List[Tuple[List[Tuple[float, float, float]], Tuple[float, float, float]]]:
    """Resolve face loops to concrete vertex positions and normals.

    For each face in *defn*, the first loop is resolved to an ordered
    list of 3-D points.  The face's normal is included alongside.

    Args:
        defn: A populated :class:`Definition`.

    Returns:
        List of ``(polygon_points, normal)`` tuples where
        *polygon_points* is a list of ``(x, y, z)`` coordinates and
        *normal* is ``(nx, ny, nz)``.
    """
    result: List[Tuple[List[Tuple[float, float, float]], Tuple[float, float, float]]] = []

    for face in defn.faces.values():
        if not face.loops:
            continue

        pts: List[Tuple[float, float, float]] = []
        for edge_id, orient in face.loops[0]:
            e = defn.edges.get(edge_id)
            if e is None:
                continue
            vid = e.v1_id if orient == 1 else e.v2_id
            v = defn.vertices.get(vid)
            if v is not None:
                pts.append((v.x, v.y, v.z))

        if len(pts) < 3:
            continue

        normal = face.normal or (0.0, 0.0, 1.0)
        result.append((pts, normal))

    return result


# ── GeometryBuilder ──────────────────────────────────────────────────────


class GeometryBuilder:
    """Builds triangulated mesh data from definitions.

    The builder iterates every face in a definition, resolves its vertices,
    and triangulates the polygon using :func:`~openskp.triangulator.triangulate_face_3d`.
    The result is stored as a simple dict of position arrays and triangle
    index arrays ready for export.

    Example::

        builder = GeometryBuilder()
        mesh_index = builder.build_mesh_index(model.definitions)
    """

    def build_mesh_index(
        self,
        definitions: Dict[int, Definition],
    ) -> Dict[int, Dict[str, Any]]:
        """Triangulate all definitions and return an export-ready index.

        Args:
            definitions: Mapping of definition IDs to :class:`Definition`
                objects.

        Returns:
            Mapping of definition ID to a dict with:

            * ``"positions"`` — flat list of ``[x, y, z, x, y, z, …]``
              floats.
            * ``"indices"`` — flat list of triangle vertex indices.
            * ``"normals"`` — flat list of per-vertex normals.
        """
        index: Dict[int, Dict[str, Any]] = {}

        for def_id, defn in definitions.items():
            polys = build_definition_geometry(defn)
            if not polys:
                continue

            all_positions: List[float] = []
            all_normals: List[float] = []
            all_indices: List[int] = []
            vert_offset = 0

            for pts, normal in polys:
                tri_indices = triangulate_face_3d(pts, normal)
                if not tri_indices:
                    continue

                for pt in pts:
                    all_positions.extend(pt)
                    all_normals.extend(normal)

                for ti in tri_indices:
                    all_indices.append(ti + vert_offset)

                vert_offset += len(pts)

            if all_positions:
                index[def_id] = {
                    "positions": all_positions,
                    "indices": all_indices,
                    "normals": all_normals,
                }

        return index
