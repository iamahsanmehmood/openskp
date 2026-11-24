"""Per-face layer resolution in baked and instanced scene builders (#615).

Covers:
1. ``chair_and_table.skp`` with ``unpainted_faces="tag"``: leaf definitions
   whose faces all sit on ``Chair`` / ``Table`` resolve tag colours and leaf
   node tags, while parent definitions with child instances keep ``Layer0`` on
   their nodes and baked/instanced stay in parity.
2. Mixed face layers (20 untagged faces + 1 face on a hidden tag + an untagged
   nested component): neither the parent's meshes, the parent node, nor the
   nested child inherit the minority hidden tag.
3. Uniform face layers on a parent with an untagged child: the parent's own
   mesh takes the face tag, the parent node keeps the instance tag because it
   has child instances, and the child never inherits the parent's face tag.
4. Root-level faces all on one non-default tag keep that tag on ``ROOT``
   meshes.
"""
from __future__ import annotations

import pathlib

from openskp import SkpFile, create
from openskp.export.ifc import to_ifc
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


def test_chair_and_table_uses_face_tag_colors_and_uniform_leaf_layers():
    skp = SkpFile(str(CHAIR_AND_TABLE))
    baked = skp.build_scene(unpainted_faces="tag")
    instanced = skp.build_instanced_scene(unpainted_faces="tag")

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

    baked_nodes = dict(_collect_node_layers(baked.scene_hierarchy))
    assert baked_nodes["Chair"] == "Layer0"
    assert baked_nodes["Table"] == "Layer0"
    assert baked_nodes["Leg_Chair"] == "Chair"
    assert baked_nodes["Leg_Table"] == "Table"
    assert _collect_node_layers(instanced.scene_hierarchy) == _collect_node_layers(
        baked.scene_hierarchy
    )


def test_mixed_twenty_untagged_and_one_hidden_face_do_not_hijack_meshes_or_children(tmp_path):
    b = create()
    hidden_tag = b.add_layer("HiddenTag", color=(10, 20, 30), hidden=True)
    with b.add_component_definition("Child") as child_def:
        child_def.add_face([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)])
    with b.add_component_definition("MixedParent") as parent_def:
        for k in range(20):
            x = float(k * 2)
            parent_def.add_face([(x, 0, 0), (x + 1, 0, 0), (x + 1, 1, 0), (x, 1, 0)])
        parent_def.add_face([(0, 5, 0), (1, 5, 0), (1, 6, 0), (0, 6, 0)], layer=hidden_tag)
        parent_def.add_instance(child_def, name="NestedChild", translation=(0, 10, 0))
    b.add_instance(parent_def, name="MixedPlacement")

    out_path = tmp_path / "mixed.skp"
    b.save(out_path)
    skp = SkpFile(out_path)
    baked = skp.build_scene()
    instanced = skp.build_instanced_scene()

    for meta in baked.mesh_index.values():
        assert meta.layer == "Layer0"

    assert _collect_node_layers(baked.scene_hierarchy) == [
        ("ROOT", "Layer0"),
        ("MixedPlacement", "Layer0"),
        ("NestedChild", "Layer0"),
    ]
    assert _collect_node_layers(instanced.scene_hierarchy) == [
        ("ROOT", "Layer0"),
        ("MixedPlacement", "Layer0"),
        ("NestedChild", "Layer0"),
    ]


def test_uniform_face_tag_applies_to_own_mesh_and_childless_nodes_only(tmp_path):
    b = create()
    roof = b.add_layer("Roof", color=(100, 120, 140))
    leaf_tag = b.add_layer("LeafTag", color=(40, 80, 120))
    with b.add_component_definition("UntaggedChild") as child_def:
        child_def.add_face([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)])
    with b.add_component_definition("UniformParent") as parent_def:
        parent_def.add_face([(0, 0, 0), (2, 0, 0), (2, 2, 0), (0, 2, 0)], layer=roof)
        parent_def.add_instance(child_def, name="ChildInst", translation=(5, 0, 0))
    with b.add_component_definition("LeafDef") as leaf_def:
        leaf_def.add_face([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)], layer=leaf_tag)
    b.add_face([(0, 20, 0), (2, 20, 0), (2, 22, 0), (0, 22, 0)], layer=roof)
    b.add_instance(parent_def, name="ParentInst")
    b.add_instance(leaf_def, name="LeafInst", translation=(10, 0, 0))

    out_path = tmp_path / "uniform.skp"
    b.save(out_path)
    skp = SkpFile(out_path)
    baked = skp.build_scene()
    instanced = skp.build_instanced_scene()

    layers_by_path = {}
    for meta in baked.mesh_index.values():
        layers_by_path.setdefault(meta.path, set()).add(meta.layer)

    assert layers_by_path["ROOT"] == {"Roof"}
    assert layers_by_path["ROOT / ParentInst"] == {"Roof"}
    assert layers_by_path["ROOT / ParentInst / ChildInst"] == {"Layer0"}
    assert layers_by_path["ROOT / LeafInst"] == {"LeafTag"}

    expected_nodes = [
        ("ROOT", "Layer0"),
        ("ParentInst", "Layer0"),
        ("ChildInst", "Layer0"),
        ("LeafInst", "LeafTag"),
    ]
    assert _collect_node_layers(baked.scene_hierarchy) == expected_nodes
    assert _collect_node_layers(instanced.scene_hierarchy) == expected_nodes


def test_tagged_placement_and_tagged_parent_keep_their_tag_over_uniform_face_tag(tmp_path):
    b = create()
    wood = b.add_layer("Wood", color=(160, 110, 60))
    furniture_hidden = b.add_layer("FurnitureHidden", color=(40, 40, 40), hidden=True)
    group_tag = b.add_layer("GroupTag", color=(30, 140, 90))

    with b.add_component_definition("WoodPanel") as wood_panel:
        wood_panel.add_face([(0, 0, 0), (2, 0, 0), (2, 2, 0), (0, 2, 0)], layer=wood)
        wood_panel.add_polyline([(0, 3, 0), (2, 3, 0)])
    with b.add_component_definition("ParentGroup") as parent_group:
        parent_group.add_instance(wood_panel, name="UntaggedInGroup", translation=(5, 0, 0))

    b.add_instance(wood_panel, name="HiddenFurniture", layer=furniture_hidden)
    b.add_instance(parent_group, name="TaggedGroup", layer=group_tag, translation=(10, 0, 0))

    out_path = tmp_path / "tagged_placements.skp"
    b.save(out_path)
    skp = SkpFile(out_path)
    baked = skp.build_scene(unpainted_faces="tag")
    instanced = skp.build_instanced_scene(unpainted_faces="tag")

    mesh_layers = {meta.path: meta.layer for meta in baked.mesh_index.values()}
    assert mesh_layers["ROOT / HiddenFurniture"] == "FurnitureHidden"
    assert mesh_layers["ROOT / TaggedGroup / UntaggedInGroup"] == "GroupTag"

    curve_layers = {cs.path: cs.layer for cs in baked.curve_sets}
    assert curve_layers["ROOT / HiddenFurniture"] == "FurnitureHidden"
    assert curve_layers["ROOT / TaggedGroup / UntaggedInGroup"] == "GroupTag"

    expected_nodes = [
        ("ROOT", "Layer0"),
        ("HiddenFurniture", "FurnitureHidden"),
        ("TaggedGroup", "GroupTag"),
        ("UntaggedInGroup", "GroupTag"),
    ]
    assert _collect_node_layers(baked.scene_hierarchy) == expected_nodes
    assert _collect_node_layers(instanced.scene_hierarchy) == expected_nodes

    curve_res_by_id = {cr.id: cr for cr in instanced.curve_resources}
    hf_node = instanced.scene_hierarchy.children[0]
    ug_node = instanced.scene_hierarchy.children[1].children[0]
    assert curve_res_by_id[hf_node.curve_resource_id].curves[0].layer == "FurnitureHidden"
    assert curve_res_by_id[ug_node.curve_resource_id].curves[0].layer == "GroupTag"

    assert (160, 110, 60) in _rgb255_set(baked.gltf_materials)
    assert (160, 110, 60) in _rgb255_set(instanced.gltf_materials)

    ifc_text = to_ifc(baked)
    layer_lines = [
        line for line in ifc_text.splitlines() if "IFCPRESENTATIONLAYERWITHSTYLE(" in line
    ]
    hidden_lines = [line for line in layer_lines if "'FurnitureHidden'" in line]
    assert len(hidden_lines) == 1
    assert ",.F.," in hidden_lines[0]
    assert not any("'Wood'" in line for line in layer_lines)

