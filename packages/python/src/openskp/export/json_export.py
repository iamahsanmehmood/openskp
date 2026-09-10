"""Full metadata JSON export.

Serialises a parsed :class:`~openskp.model.SkpModel` - definitions
(with full vertex/edge/face arrays and their unresolved instance trees),
layers, materials - into a JSON-compatible dict, and optionally writes it
to disk. Pass the result of :meth:`SkpFile.build_scene` as *scene* to
also include the resolved, world-space scene hierarchy; omit it for a
lighter summary covering just the raw model (``scene_hierarchy`` is then
``None`` rather than a placeholder).

This is openskp's canonical JSON export schema, shared with the
TypeScript port's ``toJSON`` (and, from here, Dart/.NET/C++). It used to
diverge from TypeScript's in two real ways - this module kept only
vertex/edge/face *counts*, dropping the full ``edges``/``faces`` arrays
TypeScript included, while TypeScript never included ``root`` or any
per-definition ``instances`` tree at all - so a consumer switching
between the two ports got a genuinely different shape, not just
missing/extra fields. Both now match this one schema.

Note ``Instance``'s ``layer``/``properties``/``children`` fields are
deliberately *not* included in a definition's raw (pre-bake)
``instances`` list here: they're always empty defaults in Python's (and
Dart's/.NET's) parsed model - never assigned during parsing (``children``
included: a definition's placed instances are always a flat list at
parse time, matching TypeScript's ``Instance`` type, which doesn't
declare any of the three) - and only C++ actually populates ``layer``/
``properties``. Encoding known-dead placeholders into a schema meant to
be identical across languages would just bake that inconsistency in. The
*resolved*, genuinely nested per-instance tree (with correct layer/
properties) is already available via ``scene_hierarchy`` (pass
``scene=SkpFile.build_scene()``).
"""

from __future__ import annotations

import json
import pathlib
from typing import Any, Dict, Optional, Union

from ..model import Definition, Edge, Face, Instance, SkpModel, Vertex
from ..scene import InstanceNode, Scene


def _instance_to_dict(inst: Instance) -> Dict[str, Any]:
    """Convert an unresolved (per-definition) :class:`Instance` to a
    JSON-compatible dict.

    Args:
        inst: An :class:`Instance`.

    Returns:
        Dict representation.
    """
    return {
        "name": inst.name,
        "ref_idx": inst.ref_idx,
        "guid": inst.guid,
        "matrix": inst.matrix,
    }


def _vertex_to_dict(v: Vertex) -> Dict[str, Any]:
    return {"id": v.id, "x": v.x, "y": v.y, "z": v.z}


def _edge_to_dict(e: Edge) -> Dict[str, Any]:
    return {"id": e.id, "v1_id": e.v1_id, "v2_id": e.v2_id}


def _face_to_dict(f: Face) -> Dict[str, Any]:
    return {
        "id": f.id,
        "loops": [
            [{"edge_id": edge_id, "orientation": orient} for edge_id, orient in loop]
            for loop in f.loops
        ],
        "normal": list(f.normal) if f.normal is not None else None,
    }


def _instance_node_to_dict(node: InstanceNode) -> Dict[str, Any]:
    """Convert a baked, world-space :class:`~openskp.scene.InstanceNode`
    (from :meth:`SkpFile.build_scene`) to a JSON-compatible dict.

    Args:
        node: An :class:`~openskp.scene.InstanceNode` (may have nested
            children).

    Returns:
        Dict representation including recursive children.
    """
    return {
        "name": node.name,
        "definition_name": node.definition_name,
        "layer": node.layer,
        "position_mm": list(node.position_mm),
        "properties": node.properties,
        # Every OTHER attribute dictionary this instance carries (e.g. a
        # third-party plugin's own named dictionary) - already correctly
        # resolved by build_scene(), but previously never reached this
        # export at all.
        "attribute_dictionaries": {k: dict(v) for k, v in node.attribute_dictionaries.items()},
        "children": [_instance_node_to_dict(c) for c in node.children],
    }


def _definition_to_dict(defn: Definition) -> Dict[str, Any]:
    """Convert a :class:`Definition` to a JSON-compatible dict, with full
    vertex/edge/face arrays (not just counts) and its unresolved
    ``instances`` tree.

    Args:
        defn: A :class:`Definition`.

    Returns:
        Dict representation.
    """
    return {
        "id": defn.id,
        "guid": defn.guid,
        "name": defn.name,
        "vertex_count": len(defn.vertices),
        "edge_count": len(defn.edges),
        "face_count": len(defn.faces),
        "vertices": [_vertex_to_dict(v) for v in defn.vertices.values()],
        "edges": [_edge_to_dict(e) for e in defn.edges.values()],
        "faces": [_face_to_dict(f) for f in defn.faces.values()],
        "instances": [_instance_to_dict(i) for i in defn.instances],
    }


def to_dict(model: SkpModel, scene: Optional[Scene] = None) -> Dict[str, Any]:
    """Convert a parsed model (and optionally a baked scene) to a
    JSON-serialisable dict.

    Args:
        model: A fully parsed :class:`SkpModel` (the result of
            :meth:`SkpFile.parse`).
        scene: Optional result of :meth:`SkpFile.build_scene`. When given,
            ``scene_hierarchy`` in the output is the real, resolved
            world-space instance tree; when omitted, it's ``None``
            rather than baking a scene implicitly (matching this
            project's parse()/buildScene() split - a plain export never
            pays for the heavier scene bake unless asked).

    Returns:
        A nested dict containing all metadata, including ``root`` (the
        model's implicit top-level definition - non-componentized geometry
        and instances placed directly in the model, matching
        :attr:`SkpModel.root`) alongside ``definitions`` (numeric-ID-keyed
        component/group definitions).
    """
    definitions_dict = {
        str(k): _definition_to_dict(v) for k, v in model.definitions.items()
    }
    return {
        "format_version": "1.0",
        "sketchup_version": model.version,
        "units": model.units,
        "total_definitions": len(model.definitions),
        "total_layers": len(model.layers),
        "total_meshes": len(scene.mesh_index) if scene is not None else 0,
        "root": _definition_to_dict(model.root),
        "definitions": definitions_dict,
        "layers": [
            {
                "name": layer.name,
                "color": {"r": layer.color_r, "g": layer.color_g, "b": layer.color_b},
                "hidden": layer.hidden,
            }
            for layer in model.layers
        ],
        "materials": [
            {
                "name": mat.name,
                "color": {"r": mat.color[0], "g": mat.color[1], "b": mat.color[2], "a": mat.color[3]},
                "transparency": mat.transparency,
            }
            for mat in model.materials
        ],
        "mesh_index": (
            {
                name: {
                    "name": m.name,
                    "definition_name": m.definition_name,
                    "layer": m.layer,
                    "position_mm": list(m.position_mm),
                    "properties": dict(m.properties),
                    "attribute_dictionaries": {
                        k: dict(v) for k, v in m.attribute_dictionaries.items()
                    },
                    "path": m.path,
                }
                for name, m in scene.mesh_index.items()
            }
            if scene is not None else {}
        ),
        "scene_hierarchy": (
            _instance_node_to_dict(scene.scene_hierarchy) if scene is not None else None
        ),
    }


def export(
    model: SkpModel,
    output_path: Union[str, pathlib.Path],
    *,
    scene: Optional[Scene] = None,
    indent: int = 2,
) -> None:
    """Export model (and optionally scene) metadata to a JSON file.

    Args:
        model: A fully parsed :class:`SkpModel`.
        output_path: Destination file path (should end in ``.json``).
        scene: Optional result of :meth:`SkpFile.build_scene` - see
            :func:`to_dict`.
        indent: JSON indentation level for pretty-printing.
    """
    out = pathlib.Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    data = to_dict(model, scene)
    with open(out, "w", encoding="utf-8") as fp:
        json.dump(data, fp, indent=indent, ensure_ascii=False)
