"""Regression: the PLY writer must use glTF's nested PBR color."""
from array import array

from openskp.export.ply import to_ply_ascii, to_ply_binary
from openskp.scene import GlbPrimitive, Scene


def test_nested_material_color_in_ascii_and_binary():
    prim = GlbPrimitive(
        geom_name="red",
        material_index=0,
        positions=array("f", [0.0, 0.0, 0.0]),
        normals=array("f", [0.0, 0.0, 1.0]),
        uvs=array("f", [0.0, 0.0]),
        indices=array("I"),
    )
    scene = Scene(
        glb_primitives=[prim],
        gltf_materials=[
            {"pbrMetallicRoughness": {"baseColorFactor": [1.0, 0.0, 0.0, 0.5]}}
        ],
    )
    text = to_ply_ascii(scene)
    vertex = text.split("end_header\n", 1)[1].splitlines()[0]
    assert vertex.endswith("255 0 0 128")

    binary = to_ply_binary(scene)
    start = binary.index(b"end_header\n") + len(b"end_header\n")
    assert binary[start + 32:start + 36] == bytes([255, 0, 0, 128])
