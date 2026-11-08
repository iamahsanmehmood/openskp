// Definition::is_group comes from the file itself (the definition kind in a
// 2021+ file, a flag in the definition record in an older one), not from how
// often a definition is placed. Each fixture: group "Outer" > component
// "Shelf" > group "Inner".

mod common;

use std::collections::BTreeMap;

use common::fixture;
use openskp::{Definition, EntityId, Instance, ParseOptions, SkpFile, SkpModel};

const LEGACY: [&str; 4] = [
    "nesting_v6.skp",
    "nesting_2013.skp",
    "nesting_2017.skp",
    "nesting_2020.skp",
];
const VFF: [&str; 1] = ["nesting_2021.skp"];

fn parse(name: &str) -> SkpModel {
    SkpFile::open(fixture(name))
        .unwrap()
        .parse(&ParseOptions::default())
        .unwrap()
}

fn groups_and_the_component_are_told_apart(name: &str) {
    let model = parse(name);
    let groups = model.definitions.values().filter(|d| d.is_group).count();
    assert_eq!(groups, 2, "{name}");
    assert_eq!(model.definitions.len() - groups, 1, "{name}");
}

fn the_flag_follows_the_nesting(name: &str) {
    let model = parse(name);
    let of = |i: &Instance| -> &Definition { &model.definitions[&i.ref_idx.unwrap()] };
    assert_eq!(model.root().instances.len(), 1, "{name}");
    let outer = &model.root().instances[0];
    assert_eq!(outer.name, "Outer");
    assert!(of(outer).is_group);
    assert_eq!(of(outer).instances.len(), 1);
    let shelf = &of(outer).instances[0];
    assert_eq!(of(shelf).name, "Shelf");
    assert!(!of(shelf).is_group);
    assert_eq!(of(shelf).instances.len(), 1);
    let inner = &of(shelf).instances[0];
    assert_eq!(inner.name, "Inner");
    assert!(of(inner).is_group);
}

fn a_definition_placed_once_is_not_taken_for_a_group(name: &str) {
    let model = parse(name);
    let mut placements: BTreeMap<EntityId, usize> = BTreeMap::new();
    for d in model.definitions.values() {
        for i in &d.instances {
            *placements.entry(i.ref_idx.unwrap()).or_default() += 1;
        }
    }
    for i in &model.root().instances {
        *placements.entry(i.ref_idx.unwrap()).or_default() += 1;
    }
    let (shelf_id, shelf) = model
        .definitions
        .iter()
        .find(|(_, d)| d.name == "Shelf")
        .expect("Shelf");
    assert_eq!(placements[shelf_id], 1);
    assert!(!shelf.is_group);
}

#[test]
fn groups_and_the_component_are_told_apart_vff() {
    VFF.iter()
        .for_each(|n| groups_and_the_component_are_told_apart(n));
}

#[test]
fn the_flag_follows_the_nesting_vff() {
    VFF.iter().for_each(|n| the_flag_follows_the_nesting(n));
}

#[test]
fn a_definition_placed_once_is_not_taken_for_a_group_vff() {
    VFF.iter()
        .for_each(|n| a_definition_placed_once_is_not_taken_for_a_group(n));
}

#[test]
fn groups_and_the_component_are_told_apart_legacy() {
    LEGACY
        .iter()
        .for_each(|n| groups_and_the_component_are_told_apart(n));
}

#[test]
fn the_flag_follows_the_nesting_legacy() {
    LEGACY.iter().for_each(|n| the_flag_follows_the_nesting(n));
}

#[test]
fn a_definition_placed_once_is_not_taken_for_a_group_legacy() {
    LEGACY
        .iter()
        .for_each(|n| a_definition_placed_once_is_not_taken_for_a_group(n));
}
