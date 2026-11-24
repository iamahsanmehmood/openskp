"""Classic instance attribute dictionaries and name overrides in scenes (openskp#633)."""

from __future__ import annotations

import pathlib

from openskp import SkpFile, create

WALL_RECT = [(0.0, 0.0, 0.0), (20.0, 0.0, 0.0), (20.0, 10.0, 0.0), (0.0, 10.0, 0.0)]


def _save_fixture(path: pathlib.Path) -> str:
    b = create()
    with b.add_component_definition("Wall") as wall:
        wall.add_face(WALL_RECT)
    b.add_instance(
        wall,
        name="W1",
        attributes={"name": "CustomOverride", "sku": "B42"},
    )
    b.add_instance(
        wall,
        name="W2",
        translation=(30.0, 0.0, 0.0),
        attributes={"label": "TS-9", "count": 3},
        attribute_dict_name="TechSteel",
    )
    b.add_instance(
        wall,
        name="W3",
        translation=(60.0, 0.0, 0.0),
        attributes={"name": "IgnoredDynamic", "lenx": 20},
        attribute_dict_name="dynamic_attributes",
    )
    out = str(path / "classic_instance_attrs.skp")
    b.save(out)
    return out


def test_classic_instance_attrs_and_names_in_both_scenes(tmp_path: pathlib.Path) -> None:
    skp = SkpFile.open(_save_fixture(tmp_path))
    baked = skp.build_scene()
    instanced = skp.build_instanced_scene()

    for scene_nodes in (baked.scene_hierarchy.children, instanced.scene_hierarchy.children):
        assert [n.name for n in scene_nodes] == ["CustomOverride", "TS-9", "W3"]
        assert [n.name_is_generated for n in scene_nodes] == [False, False, False]
        assert scene_nodes[0].attribute_dictionaries == {
            "attributes": {"name": "CustomOverride", "sku": "B42"}
        }
        assert scene_nodes[0].properties == {}
        assert scene_nodes[1].attribute_dictionaries == {
            "TechSteel": {"label": "TS-9", "count": "3"}
        }
        assert scene_nodes[1].properties == {}
        assert scene_nodes[2].attribute_dictionaries == {}
        assert scene_nodes[2].properties == {"name": "IgnoredDynamic", "lenx": "20"}

    by_name = {
        baked.mesh_index[p.geom_name].name: baked.mesh_index[p.geom_name]
        for p in baked.surface_primitives
    }
    assert set(by_name) == {"CustomOverride", "TS-9", "W3"}
    assert by_name["CustomOverride"].attribute_dictionaries == {
        "attributes": {"name": "CustomOverride", "sku": "B42"}
    }
    assert by_name["TS-9"].attribute_dictionaries == {
        "TechSteel": {"label": "TS-9", "count": "3"}
    }
    assert by_name["W3"].attribute_dictionaries == {}
    assert by_name["W3"].properties == {"name": "IgnoredDynamic", "lenx": "20"}
