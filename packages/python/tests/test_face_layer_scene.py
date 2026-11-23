"""Per-face layer fallback parity between baked and instanced scene builders (#543).

In classic (pre-2021) files such as ``chair_and_table.skp``, placed component
instances sit on ``Layer0`` while each definition's faces carry the real tag
(``Chair`` or ``Table``) on their drawbase record. Both ``build_scene()`` and
``build_instanced_scene()`` must resolve a definition's fallback layer from
its faces' majority non-default layer so unpainted face sides and nested nodes
get the definition's tag rather than ``Layer0``'s red ``(255, 84, 84)``.
"""
from __future__ import annotations

import pathlib

from openskp import SkpFile
from test_instanced_scene import (
    _compare_triangles_in_order,
    _flatten_baked,
    _flatten_instanced,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
CHAIR_AND_TABLE = REPO_ROOT / "examples" / "web-viewer" / "samples" / "chair_and_table.skp"


def _rgb255_set(gltf_materials):
    out = set()
    for gm in gltf_materials:
        bcf = gm["pbrMetallicRoughness"]["baseColorFactor"]
        out.add((round(bcf[0] * 255), round(bcf[1] * 255), round(bcf[2] * 255)))
    return out


def _collect_node_layers(node):
    result = [(node.name, node.layer)]
    for child in node.children:
        result.extend(_collect_node_layers(child))
    return result


def test_chair_and_table_uses_face_majority_layer_in_both_builders():
    skp = SkpFile(str(CHAIR_AND_TABLE))
    baked = skp.build_scene()
    instanced = skp.build_instanced_scene()

    baked_colors = _rgb255_set(baked.gltf_materials)
    instanced_colors = _rgb255_set(instanced.gltf_materials)

    assert (255, 84, 84) not in baked_colors
    assert (255, 84, 84) not in instanced_colors
    assert (120, 150, 190) in baked_colors
    assert (190, 150, 120) in baked_colors
    assert instanced_colors == baked_colors

    actual = _flatten_instanced(instanced)
    expected = _flatten_baked(baked)
    assert len(actual) == len(expected)
    worst_delta, first_mismatch, material_mismatches = _compare_triangles_in_order(
        actual, expected, instanced.gltf_materials, baked.gltf_materials
    )
    assert first_mismatch is None, f"geometry mismatch (worst={worst_delta}): {first_mismatch}"
    assert material_mismatches == 0

    assert _collect_node_layers(instanced.scene_hierarchy) == _collect_node_layers(
        baked.scene_hierarchy
    )
