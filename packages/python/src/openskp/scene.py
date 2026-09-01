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

import logging
import time
from array import array
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from . import _core
from ._face_groups import FaceGroupContext, build_local_face_groups
from .errors import SkpParseError

logger = logging.getLogger("openskp.scene")

# Mirrors _core._PROGRESS_INTERVAL - counts placed instances (not
# definitions), since a handful of definitions can be instanced thousands
# of times and that's where scene-baking's own cost actually scales.
_PROGRESS_INTERVAL = 500

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
    """One triangulated, world-space mesh: all faces (or, for a face whose
    front/back colors genuinely differ, all *one side* of those faces)
    sharing a single resolved color from one flattened scene-graph
    position. Ready to hand straight to a GLB/glTF exporter or any other
    renderer.

    Attributes:
        positions: Flat [x, y, z, x, y, z, ...] vertex positions, in
            metres, Y-up.
        normals: Flat [x, y, z, ...] vertex normals, matching *positions*
            1:1.
        uvs: Flat [u, v, u, v, ...] texture coordinates, matching
            *positions* 1:1. Computed from each source face's
            ``uv_transform`` (or the default face-plane projection when a
            face has none) - see ``Face.uv_transform`` in model.py for the
            formula. A vertex shared by two faces that disagree on UV is
            split, since indexed glTF meshes need position/normal/uv
            aligned per vertex. Faces with ``uv_projected`` set (terrain
            drape textures) still use the face-plane formula here, since
            the real projection-plane basis isn't captured in the parsed
            data - their UVs will be approximate.
        indices: Triangle vertex indices into *positions*/*normals*/*uvs*
            (3 per triangle).
        material_index: Index into :attr:`Scene.gltf_materials` for this
            primitive's resolved color.
        geom_name: Matches the corresponding key in
            :attr:`Scene.mesh_index`.
    """

    positions: array
    normals: array
    uvs: array
    indices: array
    material_index: int
    geom_name: str


@dataclass
class SceneTexture:
    """One texture image referenced by :attr:`Scene.gltf_materials`.

    Attributes:
        data: The image file's raw bytes, exactly as stored in the .skp.
        mime_type: Sniffed from the bytes, not from ``filename`` -
            SketchUp records the authoring machine's path, whose
            extension can disagree with the content.
        filename: Best-effort original filename, for diagnostics only.
    """

    data: bytes
    mime_type: str
    filename: str = ""


@dataclass
class Scene:
    """The result of baking a parsed file's placed instances into a flat,
    world-space 3D scene."""

    scene_hierarchy: InstanceNode = field(default_factory=InstanceNode)
    mesh_index: Dict[str, MeshMetadata] = field(default_factory=dict)
    glb_primitives: List[GlbPrimitive] = field(default_factory=list)
    gltf_materials: List[Dict[str, Any]] = field(default_factory=list)
    # Distinct texture images the placed materials use, deduplicated by
    # source bytes. Empty when nothing placed in the scene is textured.
    # GLB export only embeds these when explicitly asked (export(...,
    # textures=True)) - most callers just want geometry, and photographic
    # textures can multiply file size.
    textures: List[SceneTexture] = field(default_factory=list)


def _sniff_image_mime(data: bytes) -> Optional[str]:
    """Identify an image's MIME type from its magic bytes. Returns ``None``
    for anything glTF cannot carry (glTF only allows PNG and JPEG)."""
    if len(data) >= 3 and data[0] == 0xff and data[1] == 0xd8 and data[2] == 0xff:
        return "image/jpeg"
    if len(data) >= 8 and data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    return None


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
    t0 = time.monotonic()
    defs_dict = parsed["defs_dict"]
    layer_colors = parsed["layer_colors"]
    layer_id_to_name = parsed["layer_id_to_name"]
    material_id_to_name = parsed.get("material_id_to_name", {})
    materials = parsed["materials"]
    materials_by_folder = parsed.get("materials_by_folder", {})

    logger.info("Building scene: %d definitions available", len(defs_dict))

    instance_counter = [0]
    mesh_counter = [0]
    mesh_index: Dict[str, MeshMetadata] = {}
    glb_primitives: List[GlbPrimitive] = []

    # Instance path -> (properties, name) updates, collected in O(1) per
    # instance and applied once after instantiation completes (see the
    # path-walk loop below), instead of scanning the entire mesh_index per
    # placed instance - an O(instances x meshes) substring scan that both
    # dominated build_scene on models with many placed instances and could
    # match the wrong meshes (a shallow instance's path is always a string
    # prefix of every deeper descendant's path too, so "in" matched far
    # more than intended - see openskp#240).
    path_updates: Dict[str, Tuple[Dict[str, str], str]] = {}

    # Textures deduplicated by bytes: the same image routinely backs
    # several materials, and re-embedding it per material would multiply
    # the export size for nothing.
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
        # length plus a short byte prefix is enough to tell real images
        # apart without hashing megabytes on every face
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

    # Definitions currently being instantiated on the active recursion
    # path (not "ever visited" - the same definition legitimately reused
    # by sibling instances is fine). Guards against a component that
    # directly or transitively instances itself, which would otherwise
    # recurse until the stack overflows.
    active_definitions: set = set()

    def get_layer_color(name: str) -> Tuple[int, int, int]:
        return layer_colors.get(name, (136, 136, 136))

    def get_material_index(
        color: Tuple[int, int, int],
        double_sided: bool,
        texture_index: Optional[int],
        transparency: float = 1.0,
    ) -> int:
        # The texture is part of the identity, not just the color: two
        # different images can average to the same RGB (real files do
        # this), and keying on color alone would merge them into one
        # material and lose one of the images.
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
        # baseColorFactor stays as the resolved color even with a texture
        # attached: glTF multiplies the two, and SketchUp's own colorized
        # materials rely on exactly that tint.
        if texture_index is not None:
            pbr["baseColorTexture"] = {"index": texture_index}
        mat_dict: Dict[str, Any] = {"pbrMetallicRoughness": pbr}
        if double_sided:
            mat_dict["doubleSided"] = True
        # glTF's default alphaMode is OPAQUE, which tells a conformant
        # renderer to ignore alpha entirely - both the material's own
        # opacity and any texture's alpha channel. Genuinely translucent
        # materials (glass, water) need BLEND so baseColorFactor's alpha
        # (and the texture's, if any) actually takes effect. A
        # textured-but-otherwise-opaque material gets MASK instead: many
        # SketchUp Warehouse assets (tree foliage, fences, signage) rely on
        # the image's own alpha channel to cut a shape out of an otherwise
        # flat quad, and without MASK a renderer would show the full
        # rectangle. MASK is a no-op for a texture with no real cutout - a
        # fully-opaque alpha channel (or none, as in JPEG) stays above the
        # cutoff everywhere - so this is safe to set unconditionally rather
        # than trying to detect which textures need it.
        if transparency < 1.0:
            mat_dict["alphaMode"] = "BLEND"
        elif texture_index is not None:
            mat_dict["alphaMode"] = "MASK"
        gltf_materials.append(mat_dict)
        color_to_material_index[key] = idx
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
            # Group faces sharing a resolved (color, double_sided, texture)
            # identity into one mesh each, in local space - shared with the
            # instanced builder (openskp#200) via _face_groups.py: a face
            # whose front/back resolve to the SAME color is emitted once,
            # with its glTF material marked doubleSided so it's visible from
            # either side without needing duplicate geometry; a face whose
            # front/back genuinely differ is emitted as TWO single-sided
            # triangle sets - one normal-wound using the front material, one
            # reverse-wound using the back material - so each side renders
            # its own correct color instead of the front material leaking
            # onto (or the back vanishing from) the far side.
            fallback_color = inherited_color if inherited_color is not None else get_layer_color(parent_layer)
            face_groups = build_local_face_groups(
                builder,
                FaceGroupContext(
                    material_id_to_name=material_id_to_name,
                    materials=materials,
                    materials_by_folder=materials_by_folder,
                    texture_index_for=texture_index_for,
                    fallback_color=fallback_color,
                    definition_id=def_id,
                ),
            )

            for (face_color, double_sided, tex_index, transparency), group in face_groups.items():
                local_faces = group["local_faces"]
                if not local_faces:
                    continue

                is_root = path_name == "ROOT"
                tx = 0.0 if is_root else (current_matrix[9] if len(current_matrix) > 9 else 0.0) * INCHES_TO_MM
                ty = 0.0 if is_root else (current_matrix[10] if len(current_matrix) > 10 else 0.0) * INCHES_TO_MM
                tz = 0.0 if is_root else (current_matrix[11] if len(current_matrix) > 11 else 0.0) * INCHES_TO_MM

                safe_path = path_name.replace(" / ", "__").replace(" ", "_")[:80]
                color_suffix = (
                    f"_{face_color[0]}_{face_color[1]}_{face_color[2]}_{'ds' if double_sided else 'ss'}"
                    if len(face_groups) > 1 else ""
                )
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
                local_uvs = group["local_uvs"]
                positions = array("f", [0.0]) * (len(local_verts) * 3)
                normals = array("f", [0.0]) * (len(local_verts) * 3)
                uvs = array("f", [0.0]) * (len(local_verts) * 2)
                vertex_normals_accum = group["normals_accum"]

                for i, v in enumerate(local_verts):
                    pt = _core.transform_point(v, current_matrix)
                    positions[i * 3] = pt[0] * INCHES_TO_M
                    positions[i * 3 + 1] = pt[2] * INCHES_TO_M
                    positions[i * 3 + 2] = -pt[1] * INCHES_TO_M

                    uvs[i * 2] = local_uvs[i][0]
                    uvs[i * 2 + 1] = local_uvs[i][1]

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

                material_index = get_material_index(face_color, double_sided, tex_index, transparency)
                glb_primitives.append(
                    GlbPrimitive(
                        positions=positions,
                        normals=normals,
                        uvs=uvs,
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
            # Legacy (pre-2021 MFC) instances carry a precomputed
            # "properties" dict (see legacy._extract_legacy_dynamic_
            # properties) - VFF instances don't set this key at all, so
            # this stays {} for them and gets overwritten below via the
            # D007/DC05 TLV walk instead.
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

            inst_name = inst["name"] or f"Component_{ref_idx}"
            full_path_name = f"{path_name} / {inst_name}"
            instance_counter[0] += 1
            if instance_counter[0] % _PROGRESS_INTERVAL == 0:
                logger.debug("Processed %d placed instances", instance_counter[0])

            if ref_idx in active_definitions:
                raise SkpParseError(
                    "Recursive component definition",
                    stage="build_scene", definition_id=ref_idx,
                )
            active_definitions.add(ref_idx)
            child_nodes = instantiate(ref_idx, new_matrix, l_name, full_path_name, inst_color)
            active_definitions.discard(ref_idx)

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

            path_updates[full_path_name] = (properties, inst["name"] or "")

        return child_instances_info

    identity_mat = [1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1.0]
    root_children = instantiate("ROOT", identity_mat)

    # Deferred mesh backfill: each mesh's own path was recorded verbatim as
    # a path_updates key by the exact instance that placed the definition
    # that mesh's own faces belong to (never an ancestor's), so a direct
    # O(1) lookup per mesh is enough - no cascading from an ancestor down
    # to its descendants' own meshes. Properties/name are per-instance
    # (each definition's own Dynamic Component attributes and placement
    # name), not inherited by nested sub-parts, matching how
    # scene_hierarchy already builds each InstanceNode from that same
    # instance's own `inst["name"]`/`properties` directly above - a mesh
    # ending up with some ancestor's name/properties instead of its own
    # was exactly this bug (openskp#240).
    for existing in mesh_index.values():
        if existing.path in path_updates:
            existing.properties, existing.name = path_updates[existing.path]

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

    logger.info(
        "Scene build complete: %d instances, %d meshes, %d primitives (%.2fs)",
        instance_counter[0], len(mesh_index), len(glb_primitives),
        time.monotonic() - t0,
    )

    return Scene(
        scene_hierarchy=scene_hierarchy,
        mesh_index=mesh_index,
        glb_primitives=glb_primitives,
        gltf_materials=gltf_materials,
        textures=textures,
    )
