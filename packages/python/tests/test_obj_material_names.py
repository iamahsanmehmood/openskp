"""OBJ and MTL must agree on distinct material names across collision cases."""
from array import array

import pytest

from openskp.export.obj import to_mtl, to_obj
from openskp.scene import GlbPrimitive, Scene


@pytest.mark.parametrize(
    ("names", "expected"),
    [
        (["Wood/Oak", "Wood_Oak", "Wood_Oak_2"], ["Wood_Oak", "Wood_Oak_2_1", "Wood_Oak_2"]),
        (["Oak", "Pine"], ["Oak", "Pine"]),
        (["A", "A", "A"], ["A", "A_2", "A_3"]),
        (["Material_1", None], ["Material_1", "Material_1_2"]),
        ([" ", "   "], ["default_material", "default_material_2"]),
        (["!!!", "???"], ["___", "____2"]),
        (["Oak", "oak"], ["Oak", "oak"]),
    ],
)
def test_material_identifiers_are_unique_and_shared_by_obj_and_mtl(names, expected):
    scene = Scene(
        gltf_materials=[
            {
                **({"name": name} if name is not None else {}),
                "pbrMetallicRoughness": {"baseColorFactor": [1, 0, 0, 1]},
            }
            for name in names
        ],
        glb_primitives=[
            GlbPrimitive(
                geom_name=f"face{i}",
                material_index=i,
                positions=array("f", [0, 0, 0, 1, 0, 0, 0, 1, 0]),
                normals=array("f"),
                uvs=array("f"),
                indices=array("I", [0, 1, 2]),
            )
            for i in range(len(names))
        ],
    )
    mtl_names = [line[7:] for line in to_mtl(scene).splitlines() if line.startswith("newmtl ")]
    obj_names = [line[7:] for line in to_obj(scene).splitlines() if line.startswith("usemtl ")]
    assert mtl_names == expected
    assert obj_names == expected
    assert len(set(mtl_names)) == len(expected)
