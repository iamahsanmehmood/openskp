"""Attribute string values, keys, dictionary names, and entity names with whitespace.

Fixtures ``attribute_string_whitespace_2021.skp`` (VFF) and
``attribute_string_whitespace_2017.skp`` (classic) were written with SketchUp
2025's C SDK (``SketchUpAPI.dll``) and verified against ``SUTypedValueGetString``,
``SUAttributeDictionaryGetKeys``, ``SUAttributeDictionaryGetName``,
``SUMaterialGetName``, ``SULayerGetName``, ``SUComponentDefinitionGetName``, and
``SUComponentInstanceGetName``. ``attribute_string_whitespace_raw_vff.skp``
additionally exercises on-disk VFF empty/spaces-only ``B636`` keys, untrimmed
``6519`` instance names, and explicit ``name=""`` in ``material.xml``.
"""

from __future__ import annotations

import pathlib
import pytest

from openskp import SkpFile
from openskp.export.json_export import to_dict

FIXTURES = pathlib.Path(__file__).parent / "fixtures"

EXPECTED_ATTRS = {
    "v_padded": "  leading and trailing  ",
    "v_tab_nl": '\t<render>\n  <exposure value="1.5"/>\n</render>\n',
    "v_crlf": "line1\r\nline2\r\n",
    "v_empty": "",
    "v_spaces": "   ",
    "  padded_key  ": "val_padded_key",
    "\ttab_nl_key\n": "val_tab_nl_key",
    "crlf_key\r\n": "val_crlf_key",
}

EXPECTED_SPECIAL_DICTS = {
    "  padded_dict  ": {"probe": "ok"},
    "\ttab_dict\n": {"probe": "ok"},
    "   ": {"probe": "ok"},
    "": {"probe": "ok"},
}


@pytest.mark.parametrize("version", ["2021", "2017"])
def test_attribute_values_keys_and_dictionary_names_match_sketchup(version: str) -> None:
    path = FIXTURES / f"attribute_string_whitespace_{version}.skp"
    model = SkpFile.open(str(path)).parse()

    for owner_dicts in (
        model.attribute_dictionaries,
        model.root.attribute_dictionaries,
    ):
        assert owner_dicts["whitespace_attrs"] == EXPECTED_ATTRS
        assert owner_dicts["dynamic_attributes"] == EXPECTED_ATTRS
        for dname, entries in EXPECTED_SPECIAL_DICTS.items():
            assert owner_dicts[dname] == entries

    (carrier_def,) = [d for d in model.definitions.values() if d.name == "AttrCarrierDef"]
    assert carrier_def.attribute_dictionaries["whitespace_attrs"] == EXPECTED_ATTRS
    assert carrier_def.attribute_dictionaries["dynamic_attributes"] == EXPECTED_ATTRS
    for dname, entries in EXPECTED_SPECIAL_DICTS.items():
        assert carrier_def.attribute_dictionaries[dname] == entries

    (carrier_inst,) = [i for i in model.root.instances if i.name == "AttrCarrierInst"]
    assert carrier_inst.attribute_dictionaries["whitespace_attrs"] == EXPECTED_ATTRS
    assert carrier_inst.attribute_dictionaries["dynamic_attributes"] == EXPECTED_ATTRS
    assert carrier_inst.properties == EXPECTED_ATTRS
    for dname, entries in EXPECTED_SPECIAL_DICTS.items():
        assert carrier_inst.attribute_dictionaries[dname] == entries

    scene = SkpFile.open(str(path)).build_scene()
    (carrier_node,) = [n for n in scene.scene_hierarchy.children if n.definition_name == "AttrCarrierDef"]
    assert carrier_node.properties == EXPECTED_ATTRS
    assert carrier_node.attribute_dictionaries["whitespace_attrs"] == EXPECTED_ATTRS
    for dname, entries in EXPECTED_SPECIAL_DICTS.items():
        assert carrier_node.attribute_dictionaries[dname] == entries
    assert carrier_node.definition_attribute_dictionaries["whitespace_attrs"] == EXPECTED_ATTRS
    assert carrier_node.definition_attribute_dictionaries["dynamic_attributes"] == EXPECTED_ATTRS

    exported = to_dict(model, scene)
    (exported_node,) = [
        n for n in exported["scene_hierarchy"]["children"] if n["definition_name"] == "AttrCarrierDef"
    ]
    assert exported_node["attribute_dictionaries"]["whitespace_attrs"] == EXPECTED_ATTRS


@pytest.mark.parametrize("version", ["2021", "2017"])
def test_entity_names_with_whitespace_match_sketchup(version: str) -> None:
    path = FIXTURES / f"attribute_string_whitespace_{version}.skp"
    model = SkpFile.open(str(path)).parse()

    expected_materials = sorted([
        "  Padded Mat  ",
        "\tTabbed\nMat\n",
        "CR\nLF Mat\n" if version == "2021" else "CR\r\nLF Mat\r\n",
        "   ",
    ])
    assert sorted(m.name for m in model.materials) == expected_materials

    expected_layers = sorted([
        "Layer0",
        "  Padded Tag  ",
        "\tTabbed\nTag\n",
        "CR\r\nLF Tag\r\n",
        "   ",
    ])
    assert sorted(lyr.name for lyr in model.layers) == expected_layers

    expected_defs = sorted([
        "AttrCarrierDef",
        "  Padded Def  ",
        "\tTabbed\nDef\n",
        "CR\r\nLF Def\r\n",
        "   ",
    ])
    assert sorted(d.name for d in model.definitions.values()) == expected_defs

    expected_inst_names = sorted([
        "AttrCarrierInst",
        "Padded Inst",
        "Tabbed\nInst",
        "CR\r\nLF Inst",
        "",
    ])
    assert sorted(i.name for i in model.root.instances) == expected_inst_names


def test_raw_vff_empty_keys_untrimmed_instance_name_and_empty_material_name() -> None:
    path = FIXTURES / "attribute_string_whitespace_raw_vff.skp"
    model = SkpFile.open(str(path)).parse()

    assert model.attribute_dictionaries["raw_keys"] == {
        "": "empty_key_val",
        "   ": "spaces_key_val",
        "  k  ": "  v\n",
    }
    assert [m.name for m in model.materials] == [""]
    assert len(model.root.instances) == 1
    inst = model.root.instances[0]
    assert inst.name == "  Raw Padded Inst\n"
    assert inst.attribute_dictionaries["raw_keys"] == {
        "": "inst_empty_k",
        "   ": "inst_spaces_k",
    }
