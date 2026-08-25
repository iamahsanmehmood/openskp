"""GLB/glTF 2.0 export for parsed SketchUp models, PRESERVING SketchUp's
component/group instancing rather than baking it out.

Builds on :func:`openskp.instanced_scene.build_instanced_scene` (openskp#200,
mirroring TypeScript's ``toInstancedGLB``) the same way :mod:`openskp.export.
glb` builds on :func:`openskp.scene.build_scene`: each distinct mesh
resource is added to a ``trimesh.Scene`` ONCE, then referenced by every
placement through the scene's transform graph (``trimesh.Scene.graph``)
instead of being duplicated per placement. trimesh's own GLB exporter
writes one glTF mesh per unique geometry and one glTF node per graph
reference to it, so the file it produces is genuinely instanced too - a
component placed 1,000 times contributes one copy of its vertex/index
buffers plus 1,000 node transforms, the same value proposition as the
TypeScript reference's hand-rolled binary writer, reached here by leaning on
trimesh the same way :func:`openskp.export.glb.export` already does rather
than duplicating a binary GLB writer in Python.

Example::

    from openskp import SkpFile
    from openskp.export import instanced_glb

    skp = SkpFile.open("model.skp")
    model = skp.parse()
    instanced_glb.export(skp, "output.glb")
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict

from .glb import _primitive_to_trimesh


def _gltf_matrix_to_numpy_mm(m):
    """Convert a 16-element column-major, METRE-space glTF matrix
    (:attr:`openskp.instanced_scene.InstancedNode.matrix`) into a 4x4
    row-major numpy matrix with its translation rescaled to millimetres -
    matching :mod:`openskp.export.glb`'s own mm output contract for vertex
    positions. The rotation/scale block is unitless and is not rescaled,
    the same reasoning :func:`openskp.instanced_scene._to_gltf_matrix`
    already applies once (inches -> metres) when building the matrix."""
    import numpy as np

    m_to_mm = 1000.0
    return np.array(
        [
            [m[0], m[4], m[8], m[12] * m_to_mm],
            [m[1], m[5], m[9], m[13] * m_to_mm],
            [m[2], m[6], m[10], m[14] * m_to_mm],
            [m[3], m[7], m[11], m[15]],
        ],
        dtype=float,
    )


def _instanced_node_to_dict(node) -> Dict[str, Any]:
    return {
        "name": node.name,
        "definition_name": node.definition_name,
        "layer": node.layer,
        "position_mm": list(node.position_mm),
        "properties": dict(node.properties),
        "mesh_resource_id": node.mesh_resource_id,
        "children": [_instanced_node_to_dict(c) for c in node.children],
    }


def export(
    skp_file,
    output_path: str,
    *,
    coordinate_system: str = "y-up",
    units: str = "mm",
    textures: bool = False,
) -> str:
    """Export a parsed SkpFile to GLB (binary glTF 2.0), preserving
    SketchUp's component/group instancing instead of baking it out.

    Use this instead of :func:`openskp.export.glb.export` when the model
    reuses components: that function bakes each placement into its own
    world-space vertex buffers, so its output grows with `definition
    geometry x placement count`, while this grows with `unique geometry +
    instance transforms`. A component placed 1,000 times costs one copy of
    its geometry here.

    Args:
        skp_file: An :class:`~openskp.model.SkpFile` instance that has
            already been parsed (i.e., ``skp_file.parse()`` has been called).
        output_path: Filesystem path for the output ``.glb`` file.
        coordinate_system: Target coordinate system. Currently only
            ``"y-up"`` (glTF standard) is supported.
        units: Target unit system. Currently only ``"mm"`` is supported.
        textures: Embed the scene's texture images in the GLB. Off by
            default - see :func:`openskp.export.glb.export`'s own
            ``textures`` parameter for the full explanation; the same
            trade-off and lazy Pillow dependency apply here.

    Returns:
        Absolute path to the written GLB file.

    Raises:
        RuntimeError: If *skp_file* has not been parsed yet.
        NotImplementedError: If *coordinate_system* or *units* request
            anything other than the only values currently implemented.
    """
    import trimesh

    from .. import instanced_scene as _instanced_scene_mod

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
    scene_obj = _instanced_scene_mod.build_instanced_scene(parsed)

    trimesh_scene = trimesh.Scene()

    # Each mesh resource's primitives become named trimesh geometries,
    # added directly to scene.geometry (bypassing add_geometry's own
    # auto-created identity-transform node) - only graph edges built below
    # place anything, so a resource referenced by zero nodes costs nothing.
    geometry_names: Dict[str, list] = {}
    for res in scene_obj.mesh_resources:
        names = []
        for i, prim in enumerate(res.primitives):
            name = f"{res.id}_{i}"
            trimesh_scene.geometry[name] = _primitive_to_trimesh(
                prim, scene_obj.gltf_materials, scene_obj.textures, textures
            )
            names.append(name)
        geometry_names[res.id] = names

    node_counter = [0]

    def emit(node, parent_frame: str) -> None:
        frame = f"n{node_counter[0]}"
        node_counter[0] += 1
        trimesh_scene.graph.update(
            frame_to=frame, frame_from=parent_frame, matrix=_gltf_matrix_to_numpy_mm(node.matrix)
        )
        for i, name in enumerate(geometry_names.get(node.mesh_resource_id, []) if node.mesh_resource_id else []):
            # Geometry sits at the node's own frame - an identity-transform
            # child keeps a resource with several primitives (several
            # materials) all at the same placement without ambiguity about
            # which primitive's frame a nested child instance should attach
            # under (that's always `frame` itself, set above).
            trimesh_scene.graph.update(frame_to=f"{frame}_g{i}", frame_from=frame, geometry=name)
        for child in node.children:
            emit(child, frame)

    emit(scene_obj.scene_hierarchy, "world")

    output_dir = os.path.dirname(os.path.abspath(output_path))
    os.makedirs(output_dir, exist_ok=True)
    trimesh_scene.export(output_path, file_type="glb")

    stem = os.path.splitext(os.path.basename(output_path))[0]
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
        "instanced": True,
        "total_mesh_resources": len(scene_obj.mesh_resources),
        "total_layers": len(layers_list),
        "layers": layers_list,
        "bounds": (
            {
                "min": list(scene_obj.bounds.min),
                "max": list(scene_obj.bounds.max),
                "size": list(scene_obj.bounds.size),
                "center": list(scene_obj.bounds.center),
            }
            if scene_obj.bounds is not None
            else None
        ),
        "mesh_resources": [
            {
                "id": r.id,
                "definition_id": str(r.definition_id),
                "definition_name": r.definition_name,
                "variant_key": r.variant_key,
                "primitive_count": len(r.primitives),
            }
            for r in scene_obj.mesh_resources
        ],
        "scene_hierarchy": _instanced_node_to_dict(scene_obj.scene_hierarchy),
    }

    json_path = os.path.join(output_dir, f"{stem}_metadata.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2, ensure_ascii=False)

    return os.path.abspath(output_path)
