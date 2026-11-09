"""Tags with a line style in SketchUp 2019 and 2020 files (issue #514).

Since SketchUp 2019 a tag can carry a line style ("Dash", "Dot", ...). A tag that has one points at a
``CCustomLineStyle`` object right after its own fields: nothing for a tag without a line style, the style
written in full the first time a tag uses it (a new-class record, then the object), a class-ref for a
second style, and a back-ref for a style that an earlier tag already wrote. The style is an object in the
walk like any other and takes a store-map slot. The reader had no reader for it, so any such model failed
with ``no reader for class CCustomLineStyle``.

Fixtures, all built and saved through the installed SketchUp library (``SUModelSaveToFileWithVersion``
13 for 2020, 12 for 2019); one face per tag, each on its own tag, no private content:

* ``sdk_saved_tag_line_styles_2020.skp`` - tags "Tag 0 " (no style), "Tag 1 Dash", "Tag 2 ", "Tag 3 Dash",
  "Tag 4 Dot", "Tag 5 Dash" and no materials, which takes the slot-base probe.
* ``sdk_saved_tag_line_styles_materials_2020.skp`` - tags "Dash", none, "Dot", "Dash", "Dot" and three
  materials, which takes the two-material slot base instead.
* ``sdk_saved_tag_line_styles_2019.skp`` - the first model saved as 2019, which writes the style's entity
  preamble twice.
"""
from __future__ import annotations

import pathlib

import pytest

from openskp import SkpFile
from openskp.legacy import _walk

FIXTURES = pathlib.Path(__file__).parent / "fixtures"

MODELS = {
    "sdk_saved_tag_line_styles_2020.skp": ["Tag 0 ", "Tag 1 Dash", "Tag 2 ", "Tag 3 Dash", "Tag 4 Dot", "Tag 5 Dash"],
    "sdk_saved_tag_line_styles_2019.skp": ["Tag 0 ", "Tag 1 Dash", "Tag 2 ", "Tag 3 Dash", "Tag 4 Dot", "Tag 5 Dash"],
    "sdk_saved_tag_line_styles_materials_2020.skp": ["Tag 0 Dash", "Tag 1 ", "Tag 2 Dot", "Tag 3 Dash", "Tag 4 Dot"],
}


@pytest.mark.parametrize("name", MODELS)
def test_a_model_whose_tags_have_line_styles_opens(name):
    model = SkpFile.open(str(FIXTURES / name)).parse()
    assert [layer.name for layer in model.layers] == ["Layer0", *MODELS[name]]
    assert len(model.root.faces) == len(MODELS[name])


@pytest.mark.parametrize("name", MODELS)
def test_each_face_stays_on_its_own_tag(name):
    # Reading the line styles takes store-map slots between the tags, so a tag's slot (the id its faces
    # carry) is only right when the walk counted them.
    _, entities, layers, *_ = _walk((FIXTURES / name).read_bytes())
    tag_of_slot = {slot: layer["name"] for slot, layer in layers}
    faces = [value for _, cls, value in entities if cls == "CFace"]
    assert [tag_of_slot[f["db"]["layer"]] for f in faces] == MODELS[name]

