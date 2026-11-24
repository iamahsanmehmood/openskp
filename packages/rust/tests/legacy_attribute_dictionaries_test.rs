// A classic (pre-2021) file keeps its definitions' and its model's own
// attribute dictionaries (openskp#532, ported from Python's
// `test_legacy_attribute_dictionaries.py`; the classic counterpart of
// `definition_attribute_dictionaries_test.rs`).
//
// SketchUp's Dynamic Components keep a component's formulas and options on
// its DEFINITION, and a component saved as its own file keeps its top level
// on the MODEL. The classic reader dropped both: a definition's attribute
// container was read and thrown away, and the model's lives in the header
// region the object walk never visits.
//
// Fixtures: `legacy_attribute_dictionaries_2017.skp` and `..._2018.skp`,
// saved by SketchUp 2026 in the 2017 and 2018 formats. The component
// "dc_fixture_shelf" saved as a file (so its dictionaries are the MODEL's: a
// `dynamic_attributes` one with a user option `width`) holding one instance
// of "dc_fixture_box", whose definition carries a `dynamic_attributes`
// dictionary and a custom `fixture_custom` one, and whose instance carries
// its own `lenx`.

mod common;

use std::collections::BTreeMap;

use common::fixture;
use openskp::{ParseOptions, ParsedAttrDict, ParsedAttribute, SkpFile, SkpModel};

const VERSIONS: [&str; 2] = ["2017", "2018"];

fn open(version: &str) -> SkpFile {
    SkpFile::open(fixture(&format!(
        "legacy_attribute_dictionaries_{version}.skp"
    )))
    .unwrap()
}

fn parse(version: &str) -> SkpModel {
    open(version).parse(&ParseOptions::default()).unwrap()
}

fn s(v: &str) -> ParsedAttribute {
    ParsedAttribute::String(v.to_owned())
}

fn dict(entries: Vec<(&str, ParsedAttribute)>) -> ParsedAttrDict {
    entries
        .into_iter()
        .map(|(k, v)| (k.to_owned(), v))
        .collect()
}

fn box_dynamic() -> ParsedAttrDict {
    dict(vec![
        ("_name", s("Box")),
        ("_lenx_formula", s("Shelf!Width/2")),
        ("_lengthunits", s("CENTIMETERS")),
        ("_copies_formula", s("2")),
        ("lenx", ParsedAttribute::Float(10.0)),
    ])
}

fn custom() -> ParsedAttrDict {
    dict(vec![("note", s("definition-level custom dictionary"))])
}

#[test]
fn a_definition_keeps_its_own_dictionaries_with_real_types() {
    for version in VERSIONS {
        let model = parse(version);
        let boxes: Vec<_> = model
            .definitions
            .values()
            .filter(|d| d.name == "dc_fixture_box")
            .collect();
        assert_eq!(boxes.len(), 1, "{version}");
        let dicts = &boxes[0].parsed_dictionaries;
        assert_eq!(dicts["dynamic_attributes"], box_dynamic(), "{version}");
        assert_eq!(dicts["fixture_custom"], custom(), "{version}");
    }
}

#[test]
fn the_model_keeps_its_own_dictionaries() {
    for version in VERSIONS {
        let model = parse(version);
        assert_eq!(
            model.attribute_dictionaries["dynamic_attributes"],
            dict(vec![
                ("_name", s("Shelf")),
                ("width", ParsedAttribute::Float(25.0)),
                ("_width_access", s("LIST")),
                ("_width_options", s("&Narrow=20&Wide=25&")),
                ("_width_formlabel", s("Shelf width")),
            ]),
            "{version}"
        );
        // The same dictionaries as the root definition's.
        assert_eq!(
            model.root().parsed_dictionaries,
            model.attribute_dictionaries,
            "{version}"
        );
    }
}

#[test]
fn scene_nodes_carry_their_definitions_dictionaries_stringified() {
    for version in VERSIONS {
        let scene = open(version).build_scene(&ParseOptions::default()).unwrap();
        let children = &scene.scene_hierarchy.children;
        assert_eq!(children.len(), 1, "{version}");
        let node = &children[0];
        assert_eq!(node.definition_name, "dc_fixture_box");
        assert_eq!(node.properties["lenx"], "12.5");
        assert_eq!(node.properties.len(), 1, "{version}");
        let stringified = |d: ParsedAttrDict| -> BTreeMap<String, String> {
            d.into_iter().map(|(k, v)| (k, v.to_string())).collect()
        };
        assert_eq!(
            node.definition_attribute_dictionaries["dynamic_attributes"],
            stringified(box_dynamic()),
            "{version}"
        );
        assert_eq!(
            node.definition_attribute_dictionaries["fixture_custom"],
            stringified(custom()),
            "{version}"
        );
    }
}

#[test]
fn the_models_info_record_is_not_listed_as_a_dictionary() {
    // SketchUp stores the model info (Name, Description, ...) in a container
    // named "ModelProperties" but its API does not list it as a dictionary.
    for version in VERSIONS {
        assert!(!parse(version)
            .attribute_dictionaries
            .contains_key("ModelProperties"));
    }
    let other = SkpFile::open(fixture("legacy_v2019_synthetic.skp"))
        .unwrap()
        .parse(&ParseOptions::default())
        .unwrap();
    assert!(!other.attribute_dictionaries.contains_key("ModelProperties"));
}

#[test]
fn a_classic_file_without_dynamic_attributes_has_none() {
    let model = SkpFile::open(fixture("legacy_v2019_synthetic.skp"))
        .unwrap()
        .parse(&ParseOptions::default())
        .unwrap();
    assert!(!model
        .attribute_dictionaries
        .contains_key("dynamic_attributes"));
    for definition in model.definitions.values() {
        assert!(!definition
            .parsed_dictionaries
            .contains_key("dynamic_attributes"));
    }
}
