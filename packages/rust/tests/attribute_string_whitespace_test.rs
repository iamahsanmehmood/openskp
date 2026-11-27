mod common;

use std::collections::BTreeMap;

use common::fixture;
use openskp::{ParseOptions, ParsedAttrDict, ParsedAttribute, SkpFile};

fn expected_typed_attrs() -> ParsedAttrDict {
    BTreeMap::from([
        (
            "v_padded".to_owned(),
            ParsedAttribute::String("  leading and trailing  ".to_owned()),
        ),
        (
            "v_tab_nl".to_owned(),
            ParsedAttribute::String(
                "\t<render>\n  <exposure value=\"1.5\"/>\n</render>\n".to_owned(),
            ),
        ),
        (
            "v_crlf".to_owned(),
            ParsedAttribute::String("line1\r\nline2\r\n".to_owned()),
        ),
        ("v_empty".to_owned(), ParsedAttribute::String(String::new())),
        (
            "v_spaces".to_owned(),
            ParsedAttribute::String("   ".to_owned()),
        ),
        (
            "  padded_key  ".to_owned(),
            ParsedAttribute::String("val_padded_key".to_owned()),
        ),
        (
            "\ttab_nl_key\n".to_owned(),
            ParsedAttribute::String("val_tab_nl_key".to_owned()),
        ),
        (
            "crlf_key\r\n".to_owned(),
            ParsedAttribute::String("val_crlf_key".to_owned()),
        ),
    ])
}

fn expected_string_attrs() -> BTreeMap<String, String> {
    expected_typed_attrs()
        .into_iter()
        .map(|(k, v)| (k, v.to_string()))
        .collect()
}

#[test]
fn attribute_values_keys_and_dictionary_names_match_sketchup() {
    let expected_typed = expected_typed_attrs();
    let expected_str = expected_string_attrs();
    let probe_typed =
        BTreeMap::from([("probe".to_owned(), ParsedAttribute::String("ok".to_owned()))]);
    let probe_str = BTreeMap::from([("probe".to_owned(), "ok".to_owned())]);
    let special_names = ["  padded_dict  ", "\ttab_dict\n", "   ", ""];

    for fname in [
        "attribute_string_whitespace_2021.skp",
        "attribute_string_whitespace_2017.skp",
    ] {
        let model = SkpFile::open(fixture(fname))
            .unwrap()
            .parse(&ParseOptions::default())
            .unwrap();

        for owner_dicts in [
            &model.attribute_dictionaries,
            &model.root().parsed_dictionaries,
        ] {
            assert_eq!(owner_dicts["whitespace_attrs"], expected_typed, "{fname}");
            assert_eq!(owner_dicts["dynamic_attributes"], expected_typed, "{fname}");
            for dname in special_names {
                assert_eq!(
                    owner_dicts.get(dname),
                    Some(&probe_typed),
                    "{fname} dict={dname:?}"
                );
            }
        }

        let carrier_def = model
            .definitions
            .values()
            .find(|d| d.name == "AttrCarrierDef")
            .expect("AttrCarrierDef");
        assert_eq!(
            carrier_def.parsed_dictionaries["whitespace_attrs"],
            expected_typed
        );
        assert_eq!(
            carrier_def.parsed_dictionaries["dynamic_attributes"],
            expected_typed
        );
        for dname in special_names {
            assert_eq!(
                carrier_def.parsed_dictionaries.get(dname),
                Some(&probe_typed)
            );
        }

        let scene = SkpFile::open(fixture(fname))
            .unwrap()
            .build_scene(&ParseOptions::default())
            .unwrap();
        let carrier_node = scene
            .scene_hierarchy
            .children
            .iter()
            .find(|n| n.definition_name == "AttrCarrierDef")
            .expect("AttrCarrierDef node");
        assert_eq!(carrier_node.properties, expected_str);
        assert_eq!(
            carrier_node.attribute_dictionaries["whitespace_attrs"],
            expected_str
        );
        for dname in special_names {
            assert_eq!(
                carrier_node.attribute_dictionaries.get(dname),
                Some(&probe_str)
            );
        }
        assert_eq!(
            carrier_node.definition_attribute_dictionaries["whitespace_attrs"],
            expected_str
        );
        assert_eq!(
            carrier_node.definition_attribute_dictionaries["dynamic_attributes"],
            expected_str
        );
    }
}

#[test]
fn entity_names_with_whitespace_match_sketchup() {
    for version in ["2021", "2017"] {
        let fname = format!("attribute_string_whitespace_{version}.skp");
        let model = SkpFile::open(fixture(&fname))
            .unwrap()
            .parse(&ParseOptions::default())
            .unwrap();

        let mut expected_mats = vec![
            "  Padded Mat  ".to_owned(),
            "\tTabbed\nMat\n".to_owned(),
            if version == "2021" {
                "CR\nLF Mat\n".to_owned()
            } else {
                "CR\r\nLF Mat\r\n".to_owned()
            },
            "   ".to_owned(),
        ];
        expected_mats.sort();
        let mut actual_mats: Vec<String> = model.materials.iter().map(|m| m.name.clone()).collect();
        actual_mats.sort();
        assert_eq!(actual_mats, expected_mats, "{fname}");

        let mut expected_layers = vec![
            "Layer0".to_owned(),
            "  Padded Tag  ".to_owned(),
            "\tTabbed\nTag\n".to_owned(),
            "CR\r\nLF Tag\r\n".to_owned(),
            "   ".to_owned(),
        ];
        expected_layers.sort();
        let mut actual_layers: Vec<String> = model.layers.iter().map(|l| l.name.clone()).collect();
        actual_layers.sort();
        assert_eq!(actual_layers, expected_layers, "{fname}");

        let mut expected_defs = vec![
            "AttrCarrierDef".to_owned(),
            "  Padded Def  ".to_owned(),
            "\tTabbed\nDef\n".to_owned(),
            "CR\r\nLF Def\r\n".to_owned(),
            "   ".to_owned(),
        ];
        expected_defs.sort();
        let mut actual_defs: Vec<String> =
            model.definitions.values().map(|d| d.name.clone()).collect();
        actual_defs.sort();
        assert_eq!(actual_defs, expected_defs, "{fname}");

        let mut expected_insts = vec![
            "AttrCarrierInst".to_owned(),
            "Padded Inst".to_owned(),
            "Tabbed\nInst".to_owned(),
            "CR\r\nLF Inst".to_owned(),
            String::new(),
        ];
        expected_insts.sort();
        let mut actual_insts: Vec<String> = model
            .root()
            .instances
            .iter()
            .map(|i| i.name.clone())
            .collect();
        actual_insts.sort();
        assert_eq!(actual_insts, expected_insts, "{fname}");
    }
}

#[test]
fn raw_vff_empty_keys_untrimmed_instance_name_and_empty_material_name() {
    let fname = "attribute_string_whitespace_raw_vff.skp";
    let model = SkpFile::open(fixture(fname))
        .unwrap()
        .parse(&ParseOptions::default())
        .unwrap();

    let expected_raw_keys = BTreeMap::from([
        (
            String::new(),
            ParsedAttribute::String("empty_key_val".to_owned()),
        ),
        (
            "   ".to_owned(),
            ParsedAttribute::String("spaces_key_val".to_owned()),
        ),
        (
            "  k  ".to_owned(),
            ParsedAttribute::String("  v\n".to_owned()),
        ),
    ]);
    assert_eq!(model.attribute_dictionaries["raw_keys"], expected_raw_keys);
    assert_eq!(model.materials.len(), 1);
    assert_eq!(model.materials[0].name, "");
    assert_eq!(model.root().instances.len(), 1);
    assert_eq!(model.root().instances[0].name, "  Raw Padded Inst\n");

    let scene = SkpFile::open(fixture(fname))
        .unwrap()
        .build_scene(&ParseOptions::default())
        .unwrap();
    assert_eq!(scene.scene_hierarchy.children.len(), 1);
    let node = &scene.scene_hierarchy.children[0];
    assert_eq!(node.name, "  Raw Padded Inst\n");
    let expected_inst_keys = BTreeMap::from([
        (String::new(), "inst_empty_k".to_owned()),
        ("   ".to_owned(), "inst_spaces_k".to_owned()),
    ]);
    assert_eq!(node.attribute_dictionaries["raw_keys"], expected_inst_keys);
}
