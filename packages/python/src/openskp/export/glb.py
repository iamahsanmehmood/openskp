"""GLB/glTF 2.0 export for parsed SketchUp models.

Builds on :func:`openskp.scene.build_scene` - the same cross-language-parity
``Scene``/``GlbPrimitive`` baking step used by the TypeScript, Dart, C#, and
C++ ports' ``buildScene()``/``BuildScene()`` - then hands the resulting
primitives to trimesh purely for GLB binary serialization.

This module used to run its own, independently-implemented scene-baking
pass (duplicating ``scene.py``'s instantiation/triangulation/material logic
against a live ``trimesh.Scene`` instead of ``Scene``/``GlbPrimitive``
objects). That duplication had already drifted out of sync with the shared
implementation in two concrete ways: it had no recursion/cycle guard for
self-referencing component definitions (a real crash-on-malicious-input bug
that ``scene.py`` was patched against separately), and it never resolved
``back_material_id``/``uv_transform_back``, so faces painted with different
front/back materials rendered with the front color on both sides. Building
on ``scene.build_scene()`` directly means every fix made there - present and
future - reaches real ``.glb`` output automatically, with no separate
pipeline left to fall out of sync.

Example::

    from openskp import SkpFile
    from openskp.export import glb

    skp = SkpFile.open("model.skp")
    model = skp.parse()
    glb.export(skp, "output.glb")
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List

# glTF's meter convention is what scene.build_scene()'s GlbPrimitive
# positions are already expressed in (matching every other language's
# BuildScene()/buildScene()) - this module's own public contract has always
# been millimetres, though, so the conversion below undoes that scaling
# rather than changing what callers of export() actually receive.
_M_TO_MM = 1000.0


def _decode_texture_image(data: bytes):
    """Decode raw image bytes into a PIL Image for trimesh's
    ``baseColorTexture``. Imported lazily so callers who never pass
    ``textures=True`` never pay for (or need) Pillow."""
    try:
        from PIL import Image
    except ImportError as e:
        raise ImportError(
            "export(..., textures=True) requires Pillow to decode texture "
            "images. Install it with `pip install pillow`."
        ) from e
    import io

    return Image.open(io.BytesIO(data))


def _primitive_to_trimesh(
    prim, materials: List[Dict[str, Any]], scene_textures: List[Any], embed_textures: bool,
):
    import trimesh

    n_verts = len(prim.positions) // 3
    vertices = [
        (
            prim.positions[i * 3] * _M_TO_MM,
            prim.positions[i * 3 + 1] * _M_TO_MM,
            prim.positions[i * 3 + 2] * _M_TO_MM,
        )
        for i in range(n_verts)
    ]
    normals = [
        (prim.normals[i * 3], prim.normals[i * 3 + 1], prim.normals[i * 3 + 2])
        for i in range(n_verts)
    ]
    uvs = [(prim.uvs[i * 2], prim.uvs[i * 2 + 1]) for i in range(n_verts)]
    faces = [
        (prim.indices[i * 3], prim.indices[i * 3 + 1], prim.indices[i * 3 + 2])
        for i in range(len(prim.indices) // 3)
    ]

    # process=False: trimesh's default post-construction cleanup merges/
    # drops vertices by position alone, which would misalign the UV/normal
    # arrays below (built 1:1 against `vertices`) and silently undo the
    # UV-based vertex splitting scene.build_scene() already did.
    mesh = trimesh.Trimesh(
        vertices=vertices, faces=faces, vertex_normals=normals, process=False,
    )

    mat = materials[prim.material_index]
    pbr = mat["pbrMetallicRoughness"]
    base_color_texture = None
    tex_ref = pbr.get("baseColorTexture") if embed_textures else None
    if tex_ref is not None:
        tex = scene_textures[tex_ref["index"]]
        base_color_texture = _decode_texture_image(tex.data)
    mesh.visual = trimesh.visual.TextureVisuals(
        uv=uvs,
        material=trimesh.visual.material.PBRMaterial(
            baseColorFactor=pbr["baseColorFactor"],
            baseColorTexture=base_color_texture,
            metallicFactor=pbr["metallicFactor"],
            roughnessFactor=pbr["roughnessFactor"],
            doubleSided=mat.get("doubleSided", False),
        ),
    )
    return mesh


def _instance_node_to_dict(node) -> Dict[str, Any]:
    return {
        "name": node.name,
        "definition_name": node.definition_name,
        "layer": node.layer,
        "position_mm": list(node.position_mm),
        "properties": dict(node.properties),
        "children": [_instance_node_to_dict(c) for c in node.children],
    }


def _json_safe_material(mat: Dict[str, Any]) -> Dict[str, Any]:
    # Raw texture image bytes aren't JSON-serializable and don't belong in
    # a text metadata sidecar anyway - the .glb file itself carries the
    # actual texture data.
    safe = dict(mat)
    tex = safe.get("texture")
    if tex is not None:
        safe_tex = dict(tex)
        safe_tex.pop("data", None)
        safe["texture"] = safe_tex
    return safe


def export(
    skp_file,
    output_path: str,
    *,
    coordinate_system: str = "y-up",
    units: str = "mm",
    textures: bool = False,
) -> str:
    """Export a parsed SkpFile to GLB (binary glTF 2.0) format.

    Bakes the scene via :func:`openskp.scene.build_scene` (the same
    baking step :meth:`SkpFile.build_scene` exposes directly), then
    serializes the resulting primitives to a GLB file using trimesh.

    Args:
        skp_file: An :class:`~openskp.model.SkpFile` instance that has
            already been parsed (i.e., ``skp_file.parse()`` has been called).
        output_path: Filesystem path for the output ``.glb`` file.
        coordinate_system: Target coordinate system. Currently only
            ``"y-up"`` (glTF standard) is supported.
        units: Target unit system. Currently only ``"mm"`` is supported.
        textures: Embed the scene's texture images in the GLB, so each
            textured material's ``baseColorTexture`` points at real image
            data instead of just a resolved color. Off by default:
            photographic textures can multiply the file size, and the
            geometry alone is what most callers are after. Requires
            Pillow (``pip install pillow``) - imported lazily, so callers
            who leave this off never need it installed.

    Returns:
        Absolute path to the written GLB file.

    Raises:
        RuntimeError: If *skp_file* has not been parsed yet.
        NotImplementedError: If *coordinate_system* or *units* request
            anything other than the only values currently implemented
            (``"y-up"`` / ``"mm"``). The underlying conversion (glTF
            y-up, millimetres) is hardcoded in the scene builder, so a
            caller requesting e.g. ``units="inches"`` would otherwise
            silently get millimetre output with no indication anything
            was ignored.
    """
    import trimesh

    from .. import scene as _scene_mod

    if skp_file._parsed is None:
        raise RuntimeError(
            "SkpFile must be parsed before exporting. Call skp_file.parse() first."
        )

    if coordinate_system != "y-up":
        raise NotImplementedError(
            f"coordinate_system={coordinate_system!r} is not implemented; "
            "only 'y-up' (glTF standard) is currently supported."
        )
    if units != "mm":
        raise NotImplementedError(
            f"units={units!r} is not implemented; only 'mm' is currently supported."
        )

    parsed = skp_file._parsed
    scene_obj = _scene_mod.build_scene(parsed)

    trimesh_scene = trimesh.Scene()
    for prim in scene_obj.glb_primitives:
        mesh = _primitive_to_trimesh(prim, scene_obj.gltf_materials, scene_obj.textures, textures)
        trimesh_scene.add_geometry(mesh, geom_name=prim.geom_name)

    output_dir = os.path.dirname(os.path.abspath(output_path))
    os.makedirs(output_dir, exist_ok=True)
    trimesh_scene.export(output_path, file_type="glb")

    stem = os.path.splitext(os.path.basename(output_path))[0]
    defs_dict = parsed["defs_dict"]
    layer_colors = parsed["layer_colors"]
    layers_list = [
        {"name": name, "color": {"r": c[0], "g": c[1], "b": c[2]}}
        for name, c in layer_colors.items()
    ]

    metadata = {
        "format_version": "1.0",
        "source_file": stem,
        "model_source": "sketchup",
        "sketchup_version": parsed["version"],
        "total_definitions": len(defs_dict) - 1,
        "total_meshes": len(scene_obj.mesh_index),
        "total_layers": len(layers_list),
        "layers": layers_list,
        "materials": [_json_safe_material(m) for m in parsed["materials"].values()],
        "mesh_index": {
            name: {
                "name": m.name,
                "definition_name": m.definition_name,
                "layer": m.layer,
                "position_mm": list(m.position_mm),
                "properties": dict(m.properties),
                "path": m.path,
            }
            for name, m in scene_obj.mesh_index.items()
        },
        "scene_hierarchy": _instance_node_to_dict(scene_obj.scene_hierarchy),
    }

    json_path = os.path.join(output_dir, f"{stem}_metadata.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2, ensure_ascii=False)

    if parsed.get("thumbnail_data"):
        thumb_path = os.path.join(output_dir, f"{stem}_thumbnail.png")
        with open(thumb_path, "wb") as f:
            f.write(parsed["thumbnail_data"])

    return os.path.abspath(output_path)
