"""A component instance's own paint and tag in classic (pre-2021) files.

The scene builders read both from the instance's D007 record, which only
2021+ files have, so in a classic file a painted component came out in its
tag's colour and every instance sat on its parent's tag. SketchUp's own library reports
the paint of an instance written this way (SUDrawingElementGetMaterial).
"""
from __future__ import annotations

from openskp import SkpFile, create

SQUARE = [(0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0)]


def _save(builder, tmp_path):
    path = tmp_path / "model.skp"
    path.write_bytes(builder.to_bytes())
    return SkpFile.open(str(path))


def _primitives(built):
    if hasattr(built, "glb_primitives"):
        return built.glb_primitives
    return [p for r in built.mesh_resources for p in r.primitives]


def _painted_model(tmp_path):
    builder = create()
    red = builder.add_material("Red", (200, 10, 10, 255))
    blue = builder.add_material("Blue", (10, 10, 200, 128))
    with builder.add_component_definition("Leaf") as leaf:
        leaf.add_face(SQUARE)
    with builder.add_component_definition("Outer") as outer:
        outer.add_instance(leaf, name="InheritsPaint")
        outer.add_instance(leaf, name="OwnPaint", material=blue, translation=(20, 0, 0))
    builder.add_instance(outer, name="Painted", material=red)
    return _save(builder, tmp_path)


def test_paint_is_applied_and_inherited_by_nested_components(tmp_path):
    skp = _painted_model(tmp_path)
    for built in (skp.build_scene(), skp.build_instanced_scene()):
        materials = {
            (m.get("name"), tuple(m["pbrMetallicRoughness"]["baseColorFactor"][:3]), bool(m.get("doubleSided")))
            for m in (built.gltf_materials[p.material_index] for p in _primitives(built))
        }
        assert materials == {
            ("Red", (200 / 255, 10 / 255, 10 / 255), True),
            ("Blue", (10 / 255, 10 / 255, 200 / 255), True),
        }
        # the paint is inherited whole, so the translucent one stays translucent
        (blue,) = [m for m in built.gltf_materials if m.get("name") == "Blue"]
        assert blue["alphaMode"] == "BLEND"


def test_each_paint_is_its_own_instanced_mesh(tmp_path):
    scene = _painted_model(tmp_path).build_instanced_scene()
    nodes = {n.name: n for n in scene.scene_hierarchy.children[0].children}
    assert nodes["InheritsPaint"].mesh_resource_id != nodes["OwnPaint"].mesh_resource_id


def test_tag_is_the_instances_own(tmp_path):
    builder = create()
    walls = builder.add_layer("Walls")
    with builder.add_component_definition("Part") as part:
        part.add_face(SQUARE)
    builder.add_instance(part, name="Tagged", layer=walls)
    builder.add_instance(part, name="Untagged", translation=(20, 0, 0))
    skp = _save(builder, tmp_path)
    for built in (skp.build_scene(), skp.build_instanced_scene()):
        layers = {n.name: n.layer for n in built.scene_hierarchy.children}
        assert layers == {"Tagged": "Walls", "Untagged": "Layer0"}
