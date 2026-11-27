// Attribute dictionaries on a definition and on the model, and each scene
// node's copy of its definition's (openskp#530, ported from Python's
// `test_definition_attribute_dictionaries.py`).
//
// SketchUp's Dynamic Components keep a component's attributes, formulas and
// option definitions on its DEFINITION, and only the current values on each
// instance, so a reader needs both.
//
// Fixture: `definition_attribute_dictionaries_2026.skp`, saved by SketchUp
// 2026 (VFF container). It is the component "dc_fixture_shelf" saved as a
// file, so its own dictionaries became the MODEL's: a `dynamic_attributes`
// dictionary with a user option `width` (a LIST of two choices). Inside it is
// one instance of the component "dc_fixture_box", whose definition carries a
// `dynamic_attributes` dictionary (formulas, a label, units, copies) and a
// custom `fixture_custom` one; the instance carries its own `lenx` value and
// formula.

mod common;

use std::collections::BTreeMap;

use common::fixture;
use openskp::{Definition, ParseOptions, ParsedAttribute, SkpFile, SkpModel};

const FIXTURE: &str = "definition_attribute_dictionaries_2026.skp";

fn open() -> SkpFile {
    SkpFile::open(fixture(FIXTURE)).unwrap()
}

fn box_definition(model: &SkpModel) -> &Definition {
    let boxes: Vec<_> = model
        .definitions
        .values()
        .filter(|d| d.name == "dc_fixture_box")
        .collect();
    assert_eq!(boxes.len(), 1);
    boxes[0]
}

fn strings(entries: &[(&str, &str)]) -> BTreeMap<String, String> {
    entries
        .iter()
        .map(|(k, v)| ((*k).to_owned(), (*v).to_owned()))
        .collect()
}

#[test]
fn a_definition_keeps_its_own_dictionaries_with_real_types() {
    let model = open().parse(&ParseOptions::default()).unwrap();
    let dicts = &box_definition(&model).parsed_dictionaries;
    let dynamic = &dicts["dynamic_attributes"];
    assert_eq!(dynamic["_name"], ParsedAttribute::String("Box".into()));
    assert_eq!(
        dynamic["_lenx_formula"],
        ParsedAttribute::String("Shelf!Width/2".into())
    );
    assert_eq!(dynamic["_hasbehaviors"], ParsedAttribute::Float(1.0));
    assert_eq!(dynamic["copies"], ParsedAttribute::Float(2.0));
    assert_eq!(dynamic["lenx"], ParsedAttribute::Float(10.0));
    assert_eq!(dynamic.len(), 8);
    assert_eq!(
        dicts["fixture_custom"]["note"],
        ParsedAttribute::String("definition-level custom dictionary".into())
    );
}

#[test]
fn the_model_keeps_its_own_dictionaries() {
    let model = open().parse(&ParseOptions::default()).unwrap();
    let dynamic = &model.attribute_dictionaries["dynamic_attributes"];
    assert_eq!(dynamic["_name"], ParsedAttribute::String("Shelf".into()));
    assert_eq!(dynamic["width"], ParsedAttribute::Float(25.0));
    assert_eq!(
        dynamic["_width_access"],
        ParsedAttribute::String("LIST".into())
    );
    assert_eq!(
        dynamic["_width_options"],
        ParsedAttribute::String("&Narrow=20&Wide=25&".into())
    );
    assert_eq!(
        dynamic["_width_formlabel"],
        ParsedAttribute::String("Shelf width".into())
    );
    // The same dictionaries as the root definition's.
    assert_eq!(
        model.root().parsed_dictionaries,
        model.attribute_dictionaries
    );
}

#[test]
fn definition_dictionaries_stay_off_the_instance() {
    let scene = open().build_scene(&ParseOptions::default()).unwrap();
    let children = &scene.scene_hierarchy.children;
    assert_eq!(children.len(), 1);
    // The instance keeps exactly its own values; the definition's `lenx` (10)
    // is not mixed in.
    assert_eq!(children[0].properties["lenx"], "12.5");
    assert!(!children[0].properties.contains_key("_lenx_label"));
}

#[test]
fn scene_nodes_carry_their_definitions_dictionaries_stringified() {
    let model = open().parse(&ParseOptions::default()).unwrap();
    let scene = open().build_scene(&ParseOptions::default()).unwrap();
    let node = &scene.scene_hierarchy.children[0];
    assert_eq!(node.definition_name, "dc_fixture_box");

    let expected: BTreeMap<String, String> = box_definition(&model).parsed_dictionaries
        ["dynamic_attributes"]
        .iter()
        .map(|(k, v)| (k.clone(), v.to_string()))
        .collect();
    assert_eq!(
        node.definition_attribute_dictionaries["dynamic_attributes"],
        expected
    );
    assert_eq!(
        node.definition_attribute_dictionaries["dynamic_attributes"]["_lengthunits"],
        "CENTIMETERS"
    );
    assert_eq!(
        node.definition_attribute_dictionaries["fixture_custom"],
        strings(&[("note", "definition-level custom dictionary")])
    );
    // The root places no definition of its own.
    assert!(scene
        .scene_hierarchy
        .definition_attribute_dictionaries
        .is_empty());
}

#[test]
fn a_definition_without_dictionaries_gives_its_nodes_none() {
    let model = SkpFile::open(fixture("nesting_2021.skp"))
        .unwrap()
        .parse(&ParseOptions::default())
        .unwrap();
    for definition in model.definitions.values() {
        assert!(!definition
            .parsed_dictionaries
            .contains_key("dynamic_attributes"));
    }
}
