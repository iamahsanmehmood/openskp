"""Attribute-dictionary boolean and time values in a genuine 2021+ file.

VFF (SketchUp 2021+) attribute dictionaries already decoded strings, ints,
floats, lengths, points, vectors, arrays and null. Booleans and Time values
were left undecoded because their tags had never been seen in real data:
the classic reader's counterparts are its ``0x07`` and ``0x09`` types, but
VFF stores booleans as ``AA38`` (one byte) and Time as ``AC38`` (u32 unix
seconds). This test pins both against a file SketchUp itself saved.

Fixture: ``attribute_bool_time_2025.skp``, saved by SketchUp 2025 from a
Ruby script whose ``set_attribute`` calls store true, false,
``Time.at(1700000000)``, nil, a string, an int and a float (plus a length,
point, vector and arrays) on the model, on the component definition
"AttrComp", on its placed instance, on a group and on a face.
"""
from __future__ import annotations

import pathlib

from openskp import SkpFile

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "attribute_bool_time_2025.skp"


def _face_dictionaries(data: bytes):
    """Every face's decoded dictionaries, through openskp's VFF tree reader."""
    from openskp._core import (
        CONTAINER_TAGS,
        extract_attribute_dictionaries,
        parse_tlv_recursive,
    )
    from openskp.vff import extract_skp_contents

    model_data = extract_skp_contents(data)["model_data"]
    roots = parse_tlv_recursive(model_data, 0, len(model_data), CONTAINER_TAGS)
    found = []

    def walk(node):
        if node.tag == "AC0D":
            drawing = next((child for child in node.children if child.tag == "D007"), None)
            if drawing is not None:
                found.append(extract_attribute_dictionaries(drawing))
            return
        for child in node.children:
            walk(child)

    for root in roots:
        walk(root)
    return found


def test_model_decodes_boolean_and_time_values():
    model = SkpFile.open(str(FIXTURE)).parse()
    attrs = model.attribute_dictionaries["stage3"]
    assert attrs["bool_true"] is True
    assert attrs["bool_false"] is False
    assert attrs["timestamp"] == 1700000000
    assert attrs["nothing"] is None
    assert attrs["string"] == "hello"
    assert attrs["int"] == 42
    assert attrs["float"] == 1.5


def test_definition_keeps_its_own_boolean_and_time_values():
    model = SkpFile.open(str(FIXTURE)).parse()
    (definition,) = [d for d in model.definitions.values() if d.name == "AttrComp"]
    attrs = definition.attribute_dictionaries["stage3"]
    assert attrs["bool_true"] is True
    assert attrs["bool_false"] is False
    assert attrs["timestamp"] == 1700000000
    assert definition.attribute_dictionaries["stage3_other"] == {"key": "value"}


def test_instance_and_group_placements_decode_the_same_values():
    model = SkpFile.open(str(FIXTURE)).parse()
    definitions = {definition.id: definition for definition in model.definitions.values()}
    placements = {definitions[i.ref_idx].name: i for i in model.root.instances}
    assert set(placements) == {"AttrComp", "Group1#1"}
    assert definitions[placements["Group1#1"].ref_idx].is_group
    for name in ("AttrComp", "Group1#1"):
        attrs = placements[name].attribute_dictionaries["stage3"]
        assert attrs["bool_true"] is True
        assert attrs["bool_false"] is False
        assert attrs["timestamp"] == 1700000000


def test_face_dictionary_decodes_boolean_time_and_null_values():
    data = FIXTURE.read_bytes()
    face_dicts = _face_dictionaries(data)
    stage3_faces = [d["stage3"] for d in face_dicts if "stage3" in d]
    assert len(stage3_faces) == 1
    attrs = stage3_faces[0]
    assert attrs["bool_true"] is True
    assert attrs["bool_false"] is False
    assert attrs["timestamp"] == 1700000000
    assert attrs["nothing"] is None


def test_genuine_booleans_stringify_like_the_classic_reader():
    from openskp._core import _stringify_vff_attr_value
    from openskp.legacy import _stringify_attr_value

    model = SkpFile.open(str(FIXTURE)).parse()
    attrs = model.attribute_dictionaries["stage3"]
    assert _stringify_vff_attr_value(attrs["bool_true"]) == "1"
    assert _stringify_vff_attr_value(attrs["bool_false"]) == "0"
    assert _stringify_vff_attr_value(attrs["bool_true"]) == _stringify_attr_value(1)
    assert _stringify_vff_attr_value(attrs["bool_false"]) == _stringify_attr_value(0)
