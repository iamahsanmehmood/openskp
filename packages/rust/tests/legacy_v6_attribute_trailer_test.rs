// Port of openskp#284/#385's Python fix (C++ #390): a SketchUp 6 file's
// CAttributeNamed record has no trailing field, unlike v7+.
//
// Fixture: fixtures/legacy_v6_synthetic.skp - a small synthetic model: 4
// component definitions - InnerFrame, Frame, Panel, Post; InnerFrame is also
// placed as a nested group inside Panel carrying an attribute dictionary; 10
// root-level instances - built with openskp.create() and saved via a real
// SketchUp version-downgrade export to v6.
//
// Before the fix, the CAttributeNamed reader read a trailing u32
// unconditionally. That trailer is a v7+ addition. Reading 4 phantom bytes
// silently ate into the next sibling's own tag, which surfaced many reads
// later as an unrelated "back-ref to unwalked slot N" error.

mod common;

use std::collections::BTreeSet;

use common::fixture;
use openskp::{AttributeKind, ParseOptions, ParsedAttribute, SkpFile, SkpModel};

fn parse() -> SkpModel {
    SkpFile::open(fixture("legacy_v6_synthetic.skp"))
        .unwrap()
        .parse(&ParseOptions::default())
        .unwrap()
}

#[test]
fn parses_without_the_unwalked_slot_error() {
    assert_eq!(parse().version, "{6.0.1}");
}

#[test]
fn places_every_root_instance() {
    let model = parse();
    let names: Vec<&str> = model
        .root()
        .instances
        .iter()
        .map(|i| i.name.as_str())
        .collect();
    assert_eq!(
        names,
        ["", "P-1", "P-2", "P-3", "P-4", "P-5", "P-6", "P-7", "P-8", "P-9"]
    );
}

#[test]
fn every_definition_present() {
    let model = parse();
    let names: BTreeSet<&str> = model
        .definitions
        .values()
        .map(|d| d.name.as_str())
        .collect();
    assert_eq!(
        names,
        BTreeSet::from(["InnerFrame", "Frame", "Panel", "Post"])
    );
}

#[test]
fn nested_group_attribute_dictionary_survives() {
    // The nested CGroup (InnerFrame, inside Panel) is exactly the kind of
    // record the phantom trailer corrupted - assert its own attribute
    // dictionary round-trips correctly, not just that parsing didn't crash.
    let model = parse();
    let panel = model
        .definitions
        .values()
        .find(|d| d.name == "Panel")
        .expect("Panel");
    assert_eq!(panel.instances.len(), 1);
    let inner = &panel.instances[0];
    assert_eq!(inner.name, "InnerFrame");

    let dict = &inner.attribute_dictionaries["fbd-info"];
    assert_eq!(dict["role"].kind(), AttributeKind::String);
    assert_eq!(dict["role"], "bracing");
    assert_eq!(dict["count"].kind(), AttributeKind::Integer);
    assert_eq!(dict["count"].integer(), 3);
    // the whole dictionary, typed (Python's test_legacy_v6_attribute_trailer)
    assert_eq!(dict.len(), 2);
    assert_eq!(dict["count"], ParsedAttribute::Integer(3));
}
