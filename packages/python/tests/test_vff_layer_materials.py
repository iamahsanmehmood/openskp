"""Tag colour records (materials/Layer_*) in 2021+ files are not model materials (#551).

SketchUp 2021+ stores each tag's display colour in the ZIP archive under
``materials/Layer_<tag>/material.xml``, alongside real materials, while
``model.dat`` only lists real materials in its ``C832`` material manager.
``SkpModel.materials`` must match SketchUp's own material list while preserving
tag colours on ``SkpModel.layers`` (and keeping any genuine user material whose
name starts with ``Layer_``).
"""
from __future__ import annotations

import pathlib

from openskp import SkpFile

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def test_arcs_2021_has_zero_materials_and_keeps_layer0_colour():
    model = SkpFile.open(str(FIXTURES / "arcs_2021.skp")).parse()
    assert model.materials == []
    assert [(layer.name, layer.color_r, layer.color_g, layer.color_b) for layer in model.layers] == [
        ("Layer0", 255, 84, 84)
    ]


def test_texture_tga_2021_lists_only_real_material():
    model = SkpFile.open(str(FIXTURES / "texture_tga_2021.skp")).parse()
    assert [m.name for m in model.materials] == ["Tex_TGA"]
    assert [(layer.name, layer.color_r, layer.color_g, layer.color_b) for layer in model.layers] == [
        ("Layer0", 255, 84, 84)
    ]


def test_real_material_named_layer_prefix_is_preserved_alongside_tag_colour():
    model = SkpFile.open(str(FIXTURES / "sdk_saved_layer_named_material_2021.skp")).parse()
    assert [(m.name, m.color[:3]) for m in model.materials] == [
        ("Layer_Foo", (200, 10, 20))
    ]
    layers = {layer.name: (layer.color_r, layer.color_g, layer.color_b) for layer in model.layers}
    assert layers["Foo"] == (30, 140, 250)
    assert layers["Layer0"] == (255, 84, 84)
