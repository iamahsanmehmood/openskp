"""Tags with a line style in SketchUp 2019 and 2020 files (issue #514).

Since SketchUp 2019 a tag can carry a line style ("Dash", "Dot", ...). A tag that has one points at a
``CCustomLineStyle`` object right after its own fields (both plain-colour and textured ``CLayer`` records):
nothing for a tag without a line style, the style written in full the first time a tag uses it (a new-class
record, then the object), a class-ref for a second style, and a back-ref for a style that an earlier tag
already wrote. The style is an object in the walk like any other and takes a store-map slot.

Fixtures, all built and saved through the installed SketchUp library (``SUModelSaveToFileWithVersion``
13 for 2020, 12 for 2019); one face per tag, each on its own tag, no private content:

* ``sdk_saved_tag_line_styles_2020.skp`` - plain tags "Tag 0 " (no style), "Tag 1 Dash", "Tag 2 ",
  "Tag 3 Dash", "Tag 4 Dot", "Tag 5 Dash" and no materials (slot-base probe).
* ``sdk_saved_tag_line_styles_materials_2020.skp`` - plain tags "Dash", none, "Dot", "Dash", "Dot" and
  three materials (two-material slot base).
* ``sdk_saved_tag_line_styles_2019.skp`` - the first model saved as 2019 (style preamble written twice).
* ``sdk_saved_textured_tag_line_styles_2019.skp`` / ``_2020.skp`` - textured colour-by-tag layers mixed
  with a plain tag carrying "Dash", none, "Dot", shared "Dash", and "Dot".
"""
from __future__ import annotations

import pathlib

import pytest

from openskp import SkpFile
from openskp.legacy import _walk

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
CPP_FIXTURES = pathlib.Path(__file__).resolve().parents[2] / "cpp" / "tests" / "fixtures"

MODELS = {
    "sdk_saved_tag_line_styles_2020.skp": (
        FIXTURES,
        ["Tag 0 ", "Tag 1 Dash", "Tag 2 ", "Tag 3 Dash", "Tag 4 Dot", "Tag 5 Dash"],
        [],
    ),
    "sdk_saved_tag_line_styles_2019.skp": (
        FIXTURES,
        ["Tag 0 ", "Tag 1 Dash", "Tag 2 ", "Tag 3 Dash", "Tag 4 Dot", "Tag 5 Dash"],
        [],
    ),
    "sdk_saved_tag_line_styles_materials_2020.skp": (
        FIXTURES,
        ["Tag 0 Dash", "Tag 1 ", "Tag 2 Dot", "Tag 3 Dash", "Tag 4 Dot"],
        [
            ("Paint 0", (10, 20, 30, 255)),
            ("Paint 1", (50, 20, 30, 255)),
            ("Paint 2", (90, 20, 30, 255)),
        ],
    ),
    "sdk_saved_textured_tag_line_styles_2019.skp": (
        CPP_FIXTURES,
        ["Tag 0 Tex Dash", "Tag 1 Tex ", "Tag 2 Tex Dot", "Tag 3 Tex Dash", "Tag 4 Plain Dot"],
        [],
    ),
    "sdk_saved_textured_tag_line_styles_2020.skp": (
        CPP_FIXTURES,
        ["Tag 0 Tex Dash", "Tag 1 Tex ", "Tag 2 Tex Dot", "Tag 3 Tex Dash", "Tag 4 Plain Dot"],
        [],
    ),
}


@pytest.mark.parametrize("name", MODELS)
def test_a_model_whose_tags_have_line_styles_opens(name):
    folder, expected_tags, expected_materials = MODELS[name]
    skp = SkpFile.open(str(folder / name))
    model = skp.parse()
    assert [layer.name for layer in model.layers] == ["Layer0", *expected_tags]
    assert len(model.root.faces) == len(expected_tags)
    assert [(m.name, m.color) for m in model.materials] == expected_materials
    id_to_name = skp._parsed["layer_id_to_name"]
    assert [id_to_name[f.layer] for f in model.root.faces.values()] == expected_tags


@pytest.mark.parametrize("name", MODELS)
def test_each_face_stays_on_its_own_tag(name):
    # Reading the line styles takes store-map slots between the tags, so a tag's slot (the id its faces
    # carry) is only right when the walk counted them.
    folder, expected_tags, _ = MODELS[name]
    _, entities, layers, *_ = _walk((folder / name).read_bytes())
    tag_of_slot = {slot: layer["name"] for slot, layer in layers}
    faces = [value for _, cls, value in entities if cls == "CFace"]
    assert [tag_of_slot[f["db"]["layer"]] for f in faces] == expected_tags
