"""Attribute dictionaries on a definition and on the model (2021+ files).

SketchUp's Dynamic Components keep a component's attributes, formulas and
option definitions on its DEFINITION, and only the current values on each
instance. Python read the instance's dictionaries alone, so a placed dynamic
component reached a reader without its options. The C++ port reads both
(``Definition::parsed_dictionaries``, #478).

Fixture: ``definition_attribute_dictionaries_2026.skp``, saved by SketchUp
2026 (VFF container). It is the component "dc_fixture_shelf" saved as a file,
so its own dictionaries became the MODEL's: a ``dynamic_attributes`` dictionary
with a user option ``width`` (a LIST of two choices). Inside it is one instance
of the component "dc_fixture_box", whose definition carries a
``dynamic_attributes`` dictionary (formulas, a label, units, copies) and a
custom ``fixture_custom`` one; the instance carries its own ``lenx`` value and
formula (SketchUp's Dynamic Components plugin marks both with
``_hasbehaviors`` when it opens them). A 10 x 5 x 2 box, no private content
and no geographic location.
"""
from __future__ import annotations

import pathlib

from openskp import SkpFile

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "definition_attribute_dictionaries_2026.skp"

BOX_DYNAMIC = {
    "_name": "Box",
    "_lenx_formula": "Shelf!Width/2",
    "_lenx_label": "LenX",
    "_lengthunits": "CENTIMETERS",
    "_copies_formula": "2",
    "_hasbehaviors": 1.0,
    "copies": 2.0,
    "lenx": 10.0,
}


def _box(model):
    (box,) = [d for d in model.definitions.values() if d.name == "dc_fixture_box"]
    return box


def test_a_definition_keeps_its_own_dictionaries_with_real_types():
    model = SkpFile.open(str(FIXTURE)).parse()
    dicts = _box(model).attribute_dictionaries
    assert dicts["dynamic_attributes"] == BOX_DYNAMIC
    assert dicts["fixture_custom"] == {"note": "definition-level custom dictionary"}


def test_the_model_keeps_its_own_dictionaries():
    model = SkpFile.open(str(FIXTURE)).parse()
    dynamic = model.attribute_dictionaries["dynamic_attributes"]
    assert dynamic["_name"] == "Shelf"
    assert dynamic["width"] == 25.0
    assert dynamic["_width_access"] == "LIST"
    assert dynamic["_width_options"] == "&Narrow=20&Wide=25&"
    assert dynamic["_width_formlabel"] == "Shelf width"
    # The same dictionaries as the root definition's.
    assert model.root.attribute_dictionaries == model.attribute_dictionaries


def test_definition_dictionaries_stay_off_the_instance():
    """Only the definition's direct entity base is read: an instance's own
    values are not mixed in, and the instance keeps exactly its own."""
    model = SkpFile.open(str(FIXTURE)).parse()
    assert _box(model).attribute_dictionaries["dynamic_attributes"]["lenx"] == 10.0
    scene = SkpFile.open(str(FIXTURE)).build_scene()
    (node,) = scene.scene_hierarchy.children
    assert node.properties == {"_hasbehaviors": "1.0",
                               "_lenx_formula": "Shelf!Width/2", "lenx": "12.5"}


def test_scene_nodes_carry_their_definitions_dictionaries_stringified():
    scene = SkpFile.open(str(FIXTURE)).build_scene()
    assert scene.scene_hierarchy.definition_attribute_dictionaries == {}
    (node,) = scene.scene_hierarchy.children
    assert node.definition_name == "dc_fixture_box"
    dynamic = node.definition_attribute_dictionaries["dynamic_attributes"]
    assert dynamic == {k: str(v) for k, v in BOX_DYNAMIC.items()}
    assert node.definition_attribute_dictionaries["fixture_custom"] == {
        "note": "definition-level custom dictionary"}


def test_a_definition_without_dictionaries_has_none():
    model = SkpFile.open(str(pathlib.Path(__file__).parent / "fixtures" / "nesting_2021.skp")).parse()
    for definition in model.definitions.values():
        assert "dynamic_attributes" not in definition.attribute_dictionaries
