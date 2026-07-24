"""Scene baking: flatten a parsed file's placed instances into a
world-space, triangulated 3D scene ready for rendering or GLB export.

This is deliberately a *separate*, opt-in step from :func:`SkpFile.parse`.
Baking walks the entire placed scene graph - so a file that reuses a
handful of definitions across many thousands of instances can produce far
more data here than the file's raw (un-instanced) geometry. Keeping it
separate means a plain ``SkpFile.open(path).parse()`` never pays for this
heavier computation, matching the same design used by the TypeScript,
C#, and Dart ports (``buildScene()`` / ``BuildScene()`` there).

Ported from the TypeScript reference implementation
(``packages/typescript/src/model.ts``'s ``buildSceneFromParsed``), reusing
this package's own proven ``_core.py`` primitives (``transform_point``,
``multiply_matrices``, ``triangulate_face_3d``) rather than duplicating
them.
"""

from __future__ import annotations

from array import array
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from . import _core

INCHES_TO_MM = 25.4
INCHES_TO_M = 0.0254


@dataclass
class InstanceNode:
    """One node in the baked, world-space instance tree."""

    name: str = ""
    definition_name: str = ""
    layer: str = ""
    position_mm: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    properties: Dict[str, str] = field(default_factory=dict)
    children: List["InstanceNode"] = field(default_factory=list)


@dataclass
class MeshMetadata:
    """Metadata for one baked mesh, keyed the same as its GlbPrimitive's
    ``geom_name`` in :attr:`Scene.glb_primitives`."""

    name: str = ""
    definition_name: str = ""
    layer: str = ""
    position_mm: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    properties: Dict[str, str] = field(default_factory=dict)
    path: str = ""


@dataclass
class GlbPrimitive:
    """One triangulated, world-space mesh: all faces sharing a single
    resolved color from one flattened scene-graph position. Ready to hand
    straight to a GLB/glTF exporter or any other renderer.

    Attributes:
        positions: Flat [x, y, z, x, y, z, ...] vertex positions, in
            metres, Y-up.
        normals: Flat [x, y, z, ...] vertex normals, matching *positions*
            1:1.
        indices: Triangle vertex indices into *positions*/*normals*
            (3 per triangle).
        material_index: Index into :attr:`Scene.gltf_materials` for this
            primitive's resolved color.
        geom_name: Matches the corresponding key in
            :attr:`Scene.mesh_index`.
    """

    positions: array
    normals: array
    indices: array
    material_index: int
    geom_name: str


@dataclass
class Scene:
    """The result of baking a parsed file's placed instances into a flat,
    world-space 3D scene."""

    scene_hierarchy: InstanceNode = field(default_factory=InstanceNode)
    mesh_index: Dict[str, MeshMetadata] = field(default_factory=dict)
    glb_primitives: List[GlbPrimitive] = field(default_factory=list)
    gltf_materials: List[Dict[str, Any]] = field(default_factory=list)


def _reconstruct_loop_vertices(loop, edges) -> List[int]:
    loop_verts: List[int] = []
    for edge_id, orient in loop:
        if edge_id in edges:
            v1, v2 = edges[edge_id]
            v_start = v1 if orient == 1 else v2
            if not loop_verts or loop_verts[-1] != v_start:
                loop_verts.append(v_start)
    if len(loop_verts) > 1 and loop_verts[0] == loop_verts[-1]:
        loop_verts = loop_verts[:-1]
    return loop_verts


def build_scene(parsed: Dict[str, Any]) -> Scene:
    """Bake every instance actually placed in ``parsed`` (the output of
    :func:`openskp._core.full_parse` / ``full_parse_legacy``) into
    world-space, triangulated mesh data.

    Args:
        parsed: Output of ``_core.full_parse()``. Callers normally get
            this by calling :meth:`SkpFile.parse` first is *not* required -
            :meth:`SkpFile.build_scene` re-runs the raw parse independently,
            so a plain ``parse()`` call never carries this cost.

    Returns:
        A populated :class:`Scene`.
    """
    defs_dict = parsed["defs_dict"]
    layer_colors = parsed["layer_colors"]
    layer_id_to_name = parsed["layer_id_to_name"]
    material_id_to_name = parsed.get("material_id_to_name", {})
    materials = parsed["materials"]
    materials_by_folder = parsed.get("materials_by_folder", {})

    mesh_counter = [0]
    mesh_index: Dict[str, MeshMetadata] = {}
    glb_primitives: List[GlbPrimitive] = []

    color_to_material_index: Dict[Tuple[int, int, int], int] = {}
    gltf_materials: List[Dict[str, Any]] = []

    def get_layer_color(name: str) -> Tuple[int, int, int]:
        return layer_colors.get(name, (136, 136, 136))

    def get_material_index(color: Tuple[int, int, int]) -> int:
        if color in color_to_material_index:
            return color_to_material_index[color]
        idx = len(gltf_materials)
        r, g, b = color
        gltf_materials.append(
            {
                "pbrMetallicRoughness": {
                    "baseColorFactor": [r / 255, g / 255, b / 255, 1.0],
                    "metallicFactor": 0.0,
                    "roughnessFactor": 0.8,
                }
            }
        )
        color_to_material_index[color] = idx
        return idx

    def instantiate(
        def_id,
        current_matrix,
        parent_layer: str = "Layer0",
        path_name: str = "ROOT",
        inherited_color: Optional[Tuple[int, int, int]] = None,
    ) -> List[InstanceNode]:
        d = defs_dict.get(def_id)
        if d is None:
            return []
        builder = d["builder"]

        if builder.faces:
            # Group faces sharing a resolved color into one mesh each -
            # same grouping the TS reference uses, to keep primitive count
            # proportional to actual color variety rather than face count.
            face_groups: Dict[Tuple[int, int, int], Dict[str, Any]] = {}

            for f_id, f_data in builder.faces.items():
                face_color = inherited_color
                face_mat_id = f_data.get("material_id")
                if face_mat_id is not None:
                    mat_name = material_id_to_name.get(face_mat_id)
                    mat = materials.get(mat_name) or materials_by_folder.get(mat_name)
                    if mat:
                        c = mat["color"]
                        face_color = (c["r"], c["g"], c["b"])
                if face_color is None:
                    face_color = get_layer_color(parent_layer)

                group = face_groups.get(face_color)
                if group is None:
                    group = {
                        "color": face_color,
                        "local_verts": [],
                        "local_faces": [],
                        "local_v_map": {},
                        "face_list": [],
                    }
                    face_groups[face_color] = group

                loops = []
                for loop in f_data["loops"]:
                    loop_verts = _reconstruct_loop_vertices(loop, builder.edges)
                    if loop_verts:
                        loops.append(loop_verts)
                if not loops:
                    continue

                triangles = _core.triangulate_face_3d(builder.vertices, loops, f_data["normal"])
                start_face_idx = len(group["local_faces"])
                for tri in triangles:
                    face_indices = []
                    for v_id in tri:
                        if v_id in builder.vertices:
                            idx = group["local_v_map"].get(v_id)
                            if idx is None:
                                group["local_verts"].append(builder.vertices[v_id])
                                idx = len(group["local_verts"]) - 1
                                group["local_v_map"][v_id] = idx
                            face_indices.append(idx)
                    if len(face_indices) == 3:
                        group["local_faces"].append(face_indices)
                end_face_idx = len(group["local_faces"])
                group["face_list"].append((f_id, f_data, start_face_idx, end_face_idx))

            for face_color, group in face_groups.items():
                local_faces = group["local_faces"]
                if not local_faces:
                    continue

                is_root = path_name == "ROOT"
                tx = 0.0 if is_root else (current_matrix[9] if len(current_matrix) > 9 else 0.0) * INCHES_TO_MM
                ty = 0.0 if is_root else (current_matrix[10] if len(current_matrix) > 10 else 0.0) * INCHES_TO_MM
                tz = 0.0 if is_root else (current_matrix[11] if len(current_matrix) > 11 else 0.0) * INCHES_TO_MM

                safe_path = path_name.replace(" / ", "__").replace(" ", "_")[:80]
                color_suffix = f"_{face_color[0]}_{face_color[1]}_{face_color[2]}" if len(face_groups) > 1 else ""
                geom_name = f"mesh_{mesh_counter[0]}_{safe_path}_{parent_layer}{color_suffix}"
                mesh_counter[0] += 1

                mesh_index[geom_name] = MeshMetadata(
                    name="ROOT" if is_root else (path_name.split(" / ")[-1] or ""),
                    definition_name=d.get("name") or "",
                    layer=parent_layer,
                    position_mm=(round(tx, 2), round(ty, 2), round(tz, 2)),
                    properties={},
                    path=path_name,
                )

                local_verts = group["local_verts"]
                positions = array("f", [0.0]) * (len(local_verts) * 3)
                normals = array("f", [0.0]) * (len(local_verts) * 3)
                vertex_normals_accum = [[0.0, 0.0, 0.0] for _ in local_verts]

                for f_id, f_data, _start, _end in group["face_list"]:
                    loops = []
                    for loop in f_data["loops"]:
                        loop_verts = _reconstruct_loop_vertices(loop, builder.edges)
                        if loop_verts:
                            loops.append(loop_verts)
                    if not loops:
                        continue
                    fn = f_data["normal"]
                    for loop in loops:
                        for v_id in loop:
                            idx = group["local_v_map"].get(v_id)
                            if idx is not None:
                                vertex_normals_accum[idx][0] += fn[0]
                                vertex_normals_accum[idx][1] += fn[1]
                                vertex_normals_accum[idx][2] += fn[2]

                for i, v in enumerate(local_verts):
                    pt = _core.transform_point(v, current_matrix)
                    positions[i * 3] = pt[0] * INCHES_TO_M
                    positions[i * 3 + 1] = pt[2] * INCHES_TO_M
                    positions[i * 3 + 2] = -pt[1] * INCHES_TO_M

                    raw_n = vertex_normals_accum[i]
                    norm_len = (raw_n[0] ** 2 + raw_n[1] ** 2 + raw_n[2] ** 2) ** 0.5
                    if norm_len > 1e-6:
                        n = (raw_n[0] / norm_len, raw_n[1] / norm_len, raw_n[2] / norm_len)
                    else:
                        n = (0.0, 0.0, 1.0)

                    nx = current_matrix[0] * n[0] + current_matrix[1] * n[1] + current_matrix[2] * n[2]
                    ny = current_matrix[3] * n[0] + current_matrix[4] * n[1] + current_matrix[5] * n[2]
                    nz = current_matrix[6] * n[0] + current_matrix[7] * n[1] + current_matrix[8] * n[2]
                    length = (nx * nx + ny * ny + nz * nz) ** 0.5
                    if length > 1e-6:
                        normals[i * 3] = nx / length
                        normals[i * 3 + 1] = nz / length
                        normals[i * 3 + 2] = -ny / length
                    else:
                        normals[i * 3] = 0.0
                        normals[i * 3 + 1] = 1.0
                        normals[i * 3 + 2] = 0.0

                indices = array("I", [0]) * (len(local_faces) * 3)
                for i, tri in enumerate(local_faces):
                    indices[i * 3] = tri[0]
                    indices[i * 3 + 1] = tri[1]
                    indices[i * 3 + 2] = tri[2]

                material_index = get_material_index(face_color)
                glb_primitives.append(
                    GlbPrimitive(
                        positions=positions,
                        normals=normals,
                        indices=indices,
                        material_index=material_index,
                        geom_name=geom_name,
                    )
                )

        child_instances_info: List[InstanceNode] = []
        for inst in builder.instances:
            ref_idx = inst["ref_idx"]
            inst_matrix = inst["matrix"]
            new_matrix = _core.multiply_matrices(current_matrix, inst_matrix)

            l_name = parent_layer
            inst_color = inherited_color
            properties: Dict[str, str] = {}

            d007 = next((c for c in inst["children"] if c["tag"] == "D007"), None)
            if d007:
                d207 = next((c for c in d007["children"] if c["tag"] == "D207"), None)
                if d207 and d207["payload"]:
                    p = d207["payload"]
                    l_id = p[0] if len(p) == 1 else _core.parse_var_int(p, 0, len(p))
                    l_name = layer_id_to_name.get(l_id, parent_layer)

                d107 = next((c for c in d007["children"] if c["tag"] == "D107"), None)
                if d107:
                    inst_mat_id = _core.parse_var_int(d107["payload"], 0, len(d107["payload"]))
                    mat_name = material_id_to_name.get(inst_mat_id)
                    mat = materials.get(mat_name) or materials_by_folder.get(mat_name)
                    if mat:
                        c = mat["color"]
                        inst_color = (c["r"], c["g"], c["b"])

                try:
                    properties = _core.extract_dynamic_properties(d007)
                except Exception:
                    pass

            inst_name = inst["name"] or f"Component_{ref_idx}"
            full_path_name = f"{path_name} / {inst_name}"
            child_nodes = instantiate(ref_idx, new_matrix, l_name, full_path_name, inst_color)

            tx = new_matrix[9] * INCHES_TO_MM if len(new_matrix) > 9 else 0.0
            ty = new_matrix[10] * INCHES_TO_MM if len(new_matrix) > 10 else 0.0
            tz = new_matrix[11] * INCHES_TO_MM if len(new_matrix) > 11 else 0.0

            inst_info = InstanceNode(
                name=inst["name"] or "",
                definition_name=(defs_dict.get(ref_idx) or {}).get("name") or "",
                layer=l_name,
                position_mm=(round(tx, 2), round(ty, 2), round(tz, 2)),
                properties=properties,
                children=child_nodes,
            )
            child_instances_info.append(inst_info)

            safe_child_path = full_path_name.replace(" / ", "__").replace(" ", "_")[:80]
            for geom_name, existing in mesh_index.items():
                if safe_child_path in geom_name:
                    existing.properties = properties
                    existing.name = inst["name"] or ""

        return child_instances_info

    identity_mat = [1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1.0]
    root_children = instantiate("ROOT", identity_mat)

    for geom_name, existing in mesh_index.items():
        if existing.path == "ROOT":
            existing.name = "ROOT"
            existing.definition_name = "ROOT_MODEL"
            existing.layer = "Layer0"
            existing.position_mm = (0.0, 0.0, 0.0)
            existing.properties = {}

    scene_hierarchy = InstanceNode(
        name="ROOT",
        definition_name="ROOT_MODEL",
        layer="Layer0",
        position_mm=(0.0, 0.0, 0.0),
        properties={},
        children=root_children,
    )

    return Scene(
        scene_hierarchy=scene_hierarchy,
        mesh_index=mesh_index,
        glb_primitives=glb_primitives,
        gltf_materials=gltf_materials,
    )
