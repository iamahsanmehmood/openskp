"""Instanced scene building: the placed scene graph with SketchUp's
component/group instancing PRESERVED rather than baked out.

Where :func:`openskp.scene.build_scene` emits one world-space vertex buffer
per placement, :func:`build_instanced_scene` triangulates each distinct
definition (in its own rendering context) ONCE, in local space, and refers
to it from every placement. Scene size therefore scales with *unique
geometry + instance transforms* instead of *definition geometry x placement
count* - the same value proposition for a furniture layout or a structural
grid with many repeated components as the TypeScript reference
(``buildInstancedScene()`` / ``toInstancedGLB()`` in
``packages/typescript/src/{instanced,instanced-glb}.ts``, openskp#200).

This is lossless: no decimation, quantisation or geometry approximation of
any kind. The triangles are the same triangles :func:`build_scene` produces
- via the SAME extracted :func:`openskp._face_groups.build_local_face_groups`
- just stored once and referenced N times instead of baked into N
world-space copies.
"""

from __future__ import annotations

import logging
import time
from array import array
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from . import _core
from ._face_groups import FaceGroupContext, build_local_face_groups
from .errors import SkpParseError
from .scene import SceneTexture, _sniff_image_mime

logger = logging.getLogger("openskp.instanced_scene")

_PROGRESS_INTERVAL = 500
INCHES_TO_MM = 25.4
INCHES_TO_M = 0.0254

IDENTITY_GLTF: Tuple[float, ...] = (
    1.0, 0.0, 0.0, 0.0,
    0.0, 1.0, 0.0, 0.0,
    0.0, 0.0, 1.0, 0.0,
    0.0, 0.0, 0.0, 1.0,
)


@dataclass
class LocalPrimitive:
    """One reusable, DEFINITION-LOCAL triangulated mesh: the instanced
    counterpart of :class:`openskp.scene.GlbPrimitive`, minus the world
    transform.

    Positions and normals stay in the definition's own local frame (metres,
    glTF Y-up - already converted, same as ``GlbPrimitive``), so N
    placements of the same definition share this one buffer set instead of
    getting N transformed copies of it. Normal transformation is deferred to
    the consumer/renderer's node transform (glTF's own inverse-transpose
    rule), which is what keeps mirrored/non-uniform-scale placements correct
    without a per-instance normal copy.
    """

    positions: array
    normals: array
    uvs: array
    indices: array
    material_index: int


@dataclass
class InstancedMeshResource:
    """A definition's geometry, resolved for one specific rendering context
    and ready to be referenced by any number of :class:`InstancedNode`\\ s.

    One SketchUp definition can yield MORE than one resource: the same
    component painted with two different colors renders differently and
    therefore needs a separate variant - see :attr:`variant_key`.
    """

    id: str
    definition_id: Any
    definition_name: str
    variant_key: str
    primitives: List[LocalPrimitive] = field(default_factory=list)


@dataclass
class InstancedNode:
    """One placed node in the instanced scene graph.

    Carries the transform that places its :attr:`mesh_resource_id` (and its
    whole subtree) into the scene, instead of that transform having been
    baked into vertex data.
    """

    name: str = ""
    definition_name: str = ""
    layer: str = ""
    # This node's transform RELATIVE TO ITS PARENT, as a 16-element
    # column-major glTF matrix (metres, Y-up) - directly usable as a glTF
    # node `matrix`. The root node's matrix is the identity.
    matrix: Tuple[float, ...] = IDENTITY_GLTF
    position_mm: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    properties: Dict[str, str] = field(default_factory=dict)
    mesh_resource_id: Optional[str] = None
    children: List["InstancedNode"] = field(default_factory=list)


@dataclass
class SceneBounds:
    """Axis-aligned bounds of the scene as PLACED, metres and Y-up."""

    min: Tuple[float, float, float]
    max: Tuple[float, float, float]
    size: Tuple[float, float, float]
    center: Tuple[float, float, float]


@dataclass
class InstancedScene:
    """The result of :func:`build_instanced_scene`."""

    bounds: Optional[SceneBounds]
    scene_hierarchy: InstancedNode
    mesh_resources: List[InstancedMeshResource]
    gltf_materials: List[Dict[str, Any]]
    # Distinct texture images the placed materials use, deduplicated by
    # source bytes - same as Scene.textures.
    textures: List[SceneTexture]


def _to_gltf_matrix(m: List[float]) -> Tuple[float, ...]:
    """Convert one instance's 13-element SketchUp matrix (inches, Z-up)
    into a 16-element column-major glTF matrix (metres, Y-up).

    The axis change is the similarity transform C * M * C^-1 with
    C: (x, y, z) -> (x, z, -y), so it composes correctly through nesting:
    converting each level and multiplying gives the same result as
    converting the fully-composed SketchUp matrix. Translation is scaled to
    metres; the rotation/scale block is unitless and is not.
    """
    a, b, c = m[0], m[1], m[2]
    d, e, f = m[3], m[4], m[5]
    g, h, i = m[6], m[7], m[8]
    tx = m[9] if len(m) > 9 else 0.0
    ty = m[10] if len(m) > 10 else 0.0
    tz = m[11] if len(m) > 11 else 0.0

    r00, r01, r02 = a, c, -b
    r10, r11, r12 = g, i, -h
    r20, r21, r22 = -d, -f, e

    return (
        r00, r10, r20, 0.0,
        r01, r11, r21, 0.0,
        r02, r12, r22, 0.0,
        tx * INCHES_TO_M, tz * INCHES_TO_M, -ty * INCHES_TO_M, 1.0,
    )


def _mul4(a: Tuple[float, ...], b: Tuple[float, ...]) -> Tuple[float, ...]:
    """Multiply two 16-element column-major matrices (out = a * b)."""
    out = [0.0] * 16
    for col in range(4):
        for row in range(4):
            s = 0.0
            for k in range(4):
                s += a[k * 4 + row] * b[col * 4 + k]
            out[col * 4 + row] = s
    return tuple(out)


def build_instanced_scene(parsed: Dict[str, Any]) -> InstancedScene:
    """Build an instanced scene from already-parsed raw data.

    Walks the same placed scene graph as :func:`openskp.scene.build_scene`
    and resolves layers, instance materials and dynamic properties
    identically - but emits each definition's triangulated geometry ONCE
    per distinct rendering context, with the placement kept on the node.

    Args:
        parsed: Output of ``_core.full_parse()`` (same input as
            :func:`openskp.scene.build_scene`).

    Returns:
        A populated :class:`InstancedScene`.
    """
    t0 = time.monotonic()
    defs_dict = parsed["defs_dict"]
    layer_colors = parsed["layer_colors"]
    layer_id_to_name = parsed["layer_id_to_name"]
    material_id_to_name = parsed.get("material_id_to_name", {})
    materials = parsed["materials"]
    materials_by_folder = parsed.get("materials_by_folder", {})

    logger.info("Building instanced scene: %d definitions available", len(defs_dict))

    instance_counter = [0]
    active_definitions: set = set()

    def get_layer_color(name: str) -> Tuple[int, int, int]:
        return layer_colors.get(name, (136, 136, 136))

    # Textures deduplicated by bytes, exactly as the baked path does.
    textures: List[SceneTexture] = []
    texture_index_by_key: Dict[str, int] = {}

    def texture_index_for(tex: Optional[Dict[str, Any]]) -> Optional[int]:
        if not tex:
            return None
        data = tex.get("data")
        if not data:
            return None
        mime_type = _sniff_image_mime(data)
        if mime_type is None:
            return None  # a format glTF cannot carry
        key = f"{len(data)}:{data[:16].hex()}"
        hit = texture_index_by_key.get(key)
        if hit is not None:
            return hit
        idx = len(textures)
        textures.append(SceneTexture(data=data, mime_type=mime_type, filename=tex.get("filename", "")))
        texture_index_by_key[key] = idx
        return idx

    color_to_material_index: Dict[Tuple[Tuple[int, int, int], bool, Optional[int], float], int] = {}
    gltf_materials: List[Dict[str, Any]] = []

    def get_material_index(
        color: Tuple[int, int, int],
        double_sided: bool,
        texture_index: Optional[int],
        transparency: float = 1.0,
    ) -> int:
        key = (color, double_sided, texture_index, transparency)
        if key in color_to_material_index:
            return color_to_material_index[key]
        idx = len(gltf_materials)
        r, g, b = color
        pbr: Dict[str, Any] = {
            "baseColorFactor": [r / 255, g / 255, b / 255, transparency],
            "metallicFactor": 0.0,
            "roughnessFactor": 0.8,
        }
        if texture_index is not None:
            pbr["baseColorTexture"] = {"index": texture_index}
        mat_dict: Dict[str, Any] = {"pbrMetallicRoughness": pbr}
        if double_sided:
            mat_dict["doubleSided"] = True
        # See scene.py's get_material_index for why: BLEND for genuinely
        # translucent materials, MASK (safe no-op on an opaque-alpha
        # texture) for textured ones, otherwise glTF's default OPAQUE.
        if transparency < 1.0:
            mat_dict["alphaMode"] = "BLEND"
        elif texture_index is not None:
            mat_dict["alphaMode"] = "MASK"
        gltf_materials.append(mat_dict)
        color_to_material_index[key] = idx
        return idx

    mesh_resources: List[InstancedMeshResource] = []
    resource_id_by_key: Dict[str, str] = {}

    def mesh_resource_for(
        def_id: Any, inherited_color: Optional[Tuple[int, int, int]], layer: str
    ) -> Optional[str]:
        """Build (or reuse) the local-space mesh resource for a definition
        rendered in the given context. Returns ``None`` when the definition
        has no face geometry of its own.

        Identity of a mesh resource: (definition, effective fallback color)
        - the ONLY inputs that can change what
        ``_face_groups.build_local_face_groups`` produces for this
        definition, since (faithfully to the baked path this was extracted
        from - see ``_face_groups.py``'s own docstring) it resolves each
        face's material from the face's OWN material id only, never from an
        instance's painted material. Caching on the definition id alone
        would still be wrong: the same definition renders a different
        fallback color depending on the layer/paint context it's placed in,
        and merging those would silently repaint geometry.
        """
        d = defs_dict.get(def_id)
        if d is None or not d["builder"].faces:
            return None

        fallback_color = inherited_color if inherited_color is not None else get_layer_color(layer)
        key = f"{def_id}|{fallback_color[0]},{fallback_color[1]},{fallback_color[2]}"
        hit = resource_id_by_key.get(key)
        if hit is not None:
            return hit

        face_groups = build_local_face_groups(
            d["builder"],
            FaceGroupContext(
                material_id_to_name=material_id_to_name,
                materials=materials,
                materials_by_folder=materials_by_folder,
                texture_index_for=texture_index_for,
                fallback_color=fallback_color,
                definition_id=def_id,
            ),
        )

        primitives: List[LocalPrimitive] = []
        for (color, double_sided, tex_index, transparency), group in face_groups.items():
            local_faces = group["local_faces"]
            if not local_faces:
                continue

            local_verts = group["local_verts"]
            local_uvs = group["local_uvs"]
            positions = array("f", [0.0]) * (len(local_verts) * 3)
            normals = array("f", [0.0]) * (len(local_verts) * 3)
            uvs = array("f", [0.0]) * (len(local_verts) * 2)
            vertex_normals_accum = group["normals_accum"]

            for i, v in enumerate(local_verts):
                # Local space, so no instance matrix is applied - only the
                # inches->metres scale and SketchUp Z-up -> glTF Y-up axis
                # swap, the same fixed conventions the baked path applies.
                positions[i * 3] = v[0] * INCHES_TO_M
                positions[i * 3 + 1] = v[2] * INCHES_TO_M
                positions[i * 3 + 2] = -v[1] * INCHES_TO_M

                uvs[i * 2] = local_uvs[i][0]
                uvs[i * 2 + 1] = local_uvs[i][1]

                raw_n = vertex_normals_accum[i]
                norm_len = (raw_n[0] ** 2 + raw_n[1] ** 2 + raw_n[2] ** 2) ** 0.5
                if norm_len > 1e-6:
                    n = (raw_n[0] / norm_len, raw_n[1] / norm_len, raw_n[2] / norm_len)
                else:
                    n = (0.0, 0.0, 1.0)
                # Same axis swap as positions. No instance-matrix normal
                # transform here: that belongs to the node, and deferring it
                # is precisely what keeps mirrored/non-uniform scales
                # correct per placement.
                normals[i * 3] = n[0]
                normals[i * 3 + 1] = n[2]
                normals[i * 3 + 2] = -n[1]

            indices = array("I", [0]) * (len(local_faces) * 3)
            for i, tri in enumerate(local_faces):
                indices[i * 3] = tri[0]
                indices[i * 3 + 1] = tri[1]
                indices[i * 3 + 2] = tri[2]

            primitives.append(
                LocalPrimitive(
                    positions=positions,
                    normals=normals,
                    uvs=uvs,
                    indices=indices,
                    material_index=get_material_index(color, double_sided, tex_index, transparency),
                )
            )

        if not primitives:
            return None

        resource_id = f"mesh_{len(mesh_resources)}"
        mesh_resources.append(
            InstancedMeshResource(
                id=resource_id,
                definition_id=def_id,
                definition_name=d.get("name") or "",
                variant_key=key,
                primitives=primitives,
            )
        )
        resource_id_by_key[key] = resource_id
        return resource_id

    def walk(
        def_id: Any,
        current_matrix: List[float],
        parent_layer: str,
        inherited_color: Optional[Tuple[int, int, int]],
    ) -> List[InstancedNode]:
        """Walk a definition's placed instances, emitting one node each.

        ``current_matrix`` is the accumulated SketchUp-space matrix and is
        used ONLY to report each node's absolute ``position_mm`` (matching
        the baked path's metadata); the geometry itself never sees it.
        """
        d = defs_dict.get(def_id)
        if d is None:
            return []

        nodes: List[InstancedNode] = []
        for inst in d["builder"].instances:
            ref_idx = inst["ref_idx"]
            new_matrix = _core.multiply_matrices(current_matrix, inst["matrix"])

            l_name = parent_layer
            inst_color = inherited_color
            properties: Dict[str, str] = dict(inst.get("properties") or {})

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
                    logger.debug(
                        "Failed to extract dynamic properties for instance %r (ref_idx=%r)",
                        inst.get("name"), ref_idx, exc_info=True,
                    )

            instance_counter[0] += 1
            if instance_counter[0] % _PROGRESS_INTERVAL == 0:
                logger.debug("Processed %d placed instances", instance_counter[0])

            if ref_idx in active_definitions:
                raise SkpParseError(
                    "Recursive component definition",
                    stage="build_scene", definition_id=ref_idx,
                )
            active_definitions.add(ref_idx)
            children = walk(ref_idx, new_matrix, l_name, inst_color)
            active_definitions.discard(ref_idx)

            tx = new_matrix[9] * INCHES_TO_MM if len(new_matrix) > 9 else 0.0
            ty = new_matrix[10] * INCHES_TO_MM if len(new_matrix) > 10 else 0.0
            tz = new_matrix[11] * INCHES_TO_MM if len(new_matrix) > 11 else 0.0

            nodes.append(
                InstancedNode(
                    name=inst["name"] or "",
                    definition_name=(defs_dict.get(ref_idx) or {}).get("name") or "",
                    layer=l_name,
                    matrix=_to_gltf_matrix(inst["matrix"]),
                    position_mm=(round(tx, 2), round(ty, 2), round(tz, 2)),
                    properties=properties,
                    mesh_resource_id=mesh_resource_for(ref_idx, inst_color, l_name),
                    children=children,
                )
            )

        return nodes

    identity_mat = [1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1.0]
    root_children = walk("ROOT", identity_mat, "Layer0", None)

    # Loose geometry drawn straight into the model (not inside any
    # component/group) is kept, as the baked path keeps it: it becomes the
    # root node's own mesh resource.
    root_mesh_resource_id = mesh_resource_for("ROOT", None, "Layer0")

    scene_hierarchy = InstancedNode(
        name="ROOT",
        definition_name="ROOT_MODEL",
        layer="Layer0",
        matrix=IDENTITY_GLTF,
        position_mm=(0.0, 0.0, 0.0),
        properties={},
        mesh_resource_id=root_mesh_resource_id,
        children=root_children,
    )

    # Bounds of the scene AS PLACED: walk the tree, transform each
    # resource's local corners by the accumulated node matrix. Only the 8
    # corners of each resource's local box are transformed rather than every
    # vertex - an affine transform maps a box's corners to the corners of
    # the transformed box, so the result is exact for the axis-aligned
    # bounds, at a fraction of the cost.
    resource_by_id = {r.id: r for r in mesh_resources}
    local_box_cache: Dict[str, Optional[Tuple[List[float], List[float]]]] = {}

    def local_box(resource_id: str) -> Optional[Tuple[List[float], List[float]]]:
        if resource_id in local_box_cache:
            return local_box_cache[resource_id]
        res = resource_by_id.get(resource_id)
        box = None
        if res:
            lo = [float("inf")] * 3
            hi = [float("-inf")] * 3
            for prim in res.primitives:
                for i in range(0, len(prim.positions), 3):
                    for k in range(3):
                        v = prim.positions[i + k]
                        if v < lo[k]:
                            lo[k] = v
                        if v > hi[k]:
                            hi[k] = v
            if lo[0] != float("inf"):
                box = (lo, hi)
        local_box_cache[resource_id] = box
        return box

    b_min = [float("inf")] * 3
    b_max = [float("-inf")] * 3

    def accumulate(node: InstancedNode, parent: Tuple[float, ...]) -> None:
        world = _mul4(parent, node.matrix)
        if node.mesh_resource_id is not None:
            box = local_box(node.mesh_resource_id)
            if box:
                lo, hi = box
                for c in range(8):
                    x = hi[0] if c & 1 else lo[0]
                    y = hi[1] if c & 2 else lo[1]
                    z = hi[2] if c & 4 else lo[2]
                    wx = world[0] * x + world[4] * y + world[8] * z + world[12]
                    wy = world[1] * x + world[5] * y + world[9] * z + world[13]
                    wz = world[2] * x + world[6] * y + world[10] * z + world[14]
                    if wx < b_min[0]:
                        b_min[0] = wx
                    if wy < b_min[1]:
                        b_min[1] = wy
                    if wz < b_min[2]:
                        b_min[2] = wz
                    if wx > b_max[0]:
                        b_max[0] = wx
                    if wy > b_max[1]:
                        b_max[1] = wy
                    if wz > b_max[2]:
                        b_max[2] = wz
        for child in node.children:
            accumulate(child, world)

    accumulate(scene_hierarchy, IDENTITY_GLTF)

    bounds: Optional[SceneBounds] = None
    if b_min[0] != float("inf"):
        bounds = SceneBounds(
            min=(b_min[0], b_min[1], b_min[2]),
            max=(b_max[0], b_max[1], b_max[2]),
            size=(b_max[0] - b_min[0], b_max[1] - b_min[1], b_max[2] - b_min[2]),
            center=((b_min[0] + b_max[0]) / 2, (b_min[1] + b_max[1]) / 2, (b_min[2] + b_max[2]) / 2),
        )

    logger.info(
        "Instanced scene build complete: %d instances, %d mesh resources (%.2fs)",
        instance_counter[0], len(mesh_resources), time.monotonic() - t0,
    )

    return InstancedScene(
        bounds=bounds,
        scene_hierarchy=scene_hierarchy,
        mesh_resources=mesh_resources,
        gltf_materials=gltf_materials,
        textures=textures,
    )
