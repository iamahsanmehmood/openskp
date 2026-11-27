"""A classic (pre-2021) file keeps its definitions' and its model's own
attribute dictionaries (the legacy counterpart of
``test_definition_attribute_dictionaries.py``).

SketchUp's Dynamic Components keep a component's formulas and options on its
DEFINITION, and a component saved as its own file keeps its top level on the
MODEL. The classic reader dropped both: a definition's attribute container was
read and thrown away, and the model's lives in the header region that the
object walk never visits, so ``Definition.attribute_dictionaries``,
``SkpModel.attribute_dictionaries`` and ``InstanceNode.
definition_attribute_dictionaries`` came back empty for every file saved
before 2021.

Fixtures: ``legacy_attribute_dictionaries_2017.skp`` and ``..._2018.skp``,
saved by SketchUp 2026 in the 2017 and 2018 formats. The component
"dc_fixture_shelf" saved as a file (so its dictionaries are the MODEL's: a
``dynamic_attributes`` one with a user option ``width``) holding one instance
of "dc_fixture_box", whose definition carries a ``dynamic_attributes``
dictionary and a custom ``fixture_custom`` one, and whose instance carries its
own ``lenx``. A 10 x 5 x 2 box, nothing else. The local path and the account
key SketchUp wrote into the files were overwritten with filler of the same
length.
"""
from __future__ import annotations

import pathlib

import pytest

from openskp import SkpFile

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
VERSIONS = ("2017", "2018")

BOX_DYNAMIC = {
    "_name": "Box",
    "_lenx_formula": "Shelf!Width/2",
    "_lengthunits": "CENTIMETERS",
    "_copies_formula": "2",
    "lenx": 10.0,
}


def _path(version):
    return str(FIXTURES / f"legacy_attribute_dictionaries_{version}.skp")


@pytest.mark.parametrize("version", VERSIONS)
def test_a_definition_keeps_its_own_dictionaries_with_real_types(version):
    model = SkpFile.open(_path(version)).parse()
    (box,) = [d for d in model.definitions.values() if d.name == "dc_fixture_box"]
    assert box.attribute_dictionaries["dynamic_attributes"] == BOX_DYNAMIC
    assert box.attribute_dictionaries["fixture_custom"] == {
        "note": "definition-level custom dictionary"}


@pytest.mark.parametrize("version", VERSIONS)
def test_the_model_keeps_its_own_dictionaries(version):
    model = SkpFile.open(_path(version)).parse()
    dynamic = model.attribute_dictionaries["dynamic_attributes"]
    assert dynamic == {
        "_name": "Shelf",
        "width": 25.0,
        "_width_access": "LIST",
        "_width_options": "&Narrow=20&Wide=25&",
        "_width_formlabel": "Shelf width",
    }
    # The same dictionaries as the root definition's.
    assert model.root.attribute_dictionaries == model.attribute_dictionaries


@pytest.mark.parametrize("version", VERSIONS)
def test_scene_nodes_carry_their_definitions_dictionaries_stringified(version):
    scene = SkpFile.open(_path(version)).build_scene()
    assert scene.scene_hierarchy.definition_attribute_dictionaries == {}
    (node,) = scene.scene_hierarchy.children
    assert node.definition_name == "dc_fixture_box"
    assert node.properties == {"lenx": "12.5"}
    assert node.definition_attribute_dictionaries["dynamic_attributes"] == {
        k: str(v) for k, v in BOX_DYNAMIC.items()}
    assert node.definition_attribute_dictionaries["fixture_custom"] == {
        "note": "definition-level custom dictionary"}


@pytest.mark.parametrize("version", VERSIONS)
def test_the_models_info_record_is_not_listed_as_a_dictionary(version):
    # SketchUp stores the model info (Name, Description, ...) in a container
    # named "ModelProperties" but its API does not list it as a dictionary.
    model = SkpFile.open(_path(version)).parse()
    assert "ModelProperties" not in model.attribute_dictionaries
    other = SkpFile.open(str(FIXTURES / "legacy_v2019_synthetic.skp")).parse()
    assert "ModelProperties" not in other.attribute_dictionaries


def test_a_classic_file_without_dynamic_attributes_has_none():
    model = SkpFile.open(str(FIXTURES / "legacy_v2019_synthetic.skp")).parse()
    assert "dynamic_attributes" not in model.attribute_dictionaries
    for definition in model.definitions.values():
        assert "dynamic_attributes" not in definition.attribute_dictionaries
