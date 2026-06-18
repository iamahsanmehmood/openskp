"""Full metadata JSON export.

Serialises the entire :class:`~openskp.model.SkpModel` — definitions,
layers, materials, and scene hierarchy — into a JSON-compatible dict,
and optionally writes it to disk.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any, Dict, Union

from ..model import (
    Definition,
    Instance,
    SkpModel,
)


def _instance_to_dict(inst: Instance) -> Dict[str, Any]:
    """Convert an :class:`Instance` tree to a JSON-compatible dict.

    Args:
        inst: An :class:`Instance` (may have nested children).

    Returns:
        Dict representation including recursive children.
    """
    return {
        "name": inst.name,
        "ref_idx": inst.ref_idx,
        "guid": inst.guid,
        "matrix": inst.matrix,
        "layer": inst.layer,
        "properties": inst.properties,
        "children": [_instance_to_dict(c) for c in inst.children],
    }


def _definition_to_dict(defn: Definition) -> Dict[str, Any]:
    """Convert a :class:`Definition` to a JSON-compatible dict.

    Geometry data (vertices, edges, faces) is summarised by count to
    keep the JSON output manageable.

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
        "vertices": [
            {"id": v.id, "x": v.x, "y": v.y, "z": v.z}
            for v in defn.vertices.values()
        ],
        "instances": [_instance_to_dict(i) for i in defn.instances],
    }


def to_dict(model: SkpModel) -> Dict[str, Any]:
    """Convert the entire model to a JSON-serialisable dict.

    Args:
        model: A fully parsed :class:`SkpModel`.

    Returns:
        A nested dict containing all metadata.
    """
    return {
        "version": model.version,
        "definitions": {
            str(k): _definition_to_dict(v)
            for k, v in model.definitions.items()
        },
        "layers": [
            {
                "name": layer.name,
                "color_r": layer.color_r,
                "color_g": layer.color_g,
                "color_b": layer.color_b,
            }
            for layer in model.layers
        ],
        "materials": [
            {
                "name": mat.name,
                "color": list(mat.color),
                "transparency": mat.transparency,
            }
            for mat in model.materials
        ],
        "scene_hierarchy": [
            _instance_to_dict(inst) for inst in model.scene_hierarchy
        ],
    }


def export(
    model: SkpModel,
    output_path: Union[str, pathlib.Path],
    *,
    indent: int = 2,
) -> None:
    """Export model metadata to a JSON file.

    Args:
        model: A fully parsed :class:`SkpModel`.
        output_path: Destination file path (should end in ``.json``).
        indent: JSON indentation level for pretty-printing.
    """
    out = pathlib.Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    data = to_dict(model)
    with open(out, "w", encoding="utf-8") as fp:
        json.dump(data, fp, indent=indent, ensure_ascii=False)
