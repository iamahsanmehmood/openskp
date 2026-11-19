// Regression test for legacy (pre-2021 MFC) .skp files with fewer than two
// materials.
//
// The archive's absolute slot numbering is normally bootstrapped by parsing
// two CMaterial records with a throwaway archive and reading the second one's
// own class-ref tag - that trick needs at least 2 materials and doesn't work
// for a file with 0 or 1 (openskp#158).
//
// Fixtures: blank_v17.skp (0 materials) and single_material_v17.skp (1
// material named "RedMat") - both saved as legacy v17 directly via the
// official SketchUp SDK, so their content is SketchUp's own built-in
// empty-document boilerplate plus one synthetic material.
//
// Plus the SketchUp 2018 zero-material layout with a custom tag listed ahead
// of Layer0 (`legacy_zero_material_custom_layer_test.cpp`).

mod common;

use common::fixture;
use openskp::{ParseOptions, SkpFile, SkpModel};

fn parse(name: &str) -> SkpModel {
    SkpFile::open(fixture(name))
        .unwrap()
        .parse(&ParseOptions::default())
        .unwrap()
}

#[test]
fn parses_a_zero_material_legacy_file() {
    // No CMaterial record anywhere in this file - exercises the CLayer-pattern
    // fallback for locating the walk's start position, not just the bootstrap
    // trick itself.
    let model = parse("blank_v17.skp");
    assert_eq!(model.version, "{17.0.1}");
    assert!(model.materials.is_empty());
    assert_eq!(model.layers.len(), 1);
    assert_eq!(model.layers[0].name, "Layer0");
    assert!(model.definitions.is_empty());
    assert!(model.root().instances.is_empty());
}

#[test]
fn parses_a_single_material_legacy_file() {
    let model = parse("single_material_v17.skp");
    assert_eq!(model.version, "{17.0.1}");
    assert_eq!(model.materials.len(), 1);
    assert_eq!(model.materials[0].name, "RedMat");
    assert_eq!(model.layers.len(), 1);
    assert_eq!(model.layers[0].name, "Layer0");
}

// SketchUp 2018 (file version 18) saves with no CMaterial records and a custom
// tag listed ahead of Layer0 take probe_layer_anchor_bases (the two-material
// bootstrap needs mc >= 2). The declared layer_count is 1; Layer0 follows
// after a 16-byte colour-layer extension. Missing that extension left the
// probe on padding: `base probe: anchor resolved to`. The fixture is a
// SketchUp 2018 layout sample (two construction lines, no faces) with
// identifiable strings replaced by same-length ASCII.
#[test]
fn parses_v18_custom_tag_ahead_of_layer0() {
    let model = parse("zero_material_custom_layer_v18.skp");
    assert_eq!(model.version, "{18.0.16975}");
    assert!(model.materials.is_empty());
    assert!(model.layers.len() >= 2);
    assert!(model.layers.iter().any(|l| l.name == "Layer0"));
    assert!(model.layers.iter().any(|l| l.name == "Guide"));
    assert!(model.root().faces.is_empty());
    assert_eq!(model.root().construction_lines.len(), 2);
}

// A leader text with any arrow type, and a font with a multi-byte pid, in a
// classic file (openskp#442). Fixture legacy_text_arrows_2017.skp: three
// leader texts written by openskp.create with arrow types 1 (dot), 2 (closed
// arrow) and 4 (open arrow), and a text font whose pid is two bytes. SketchUp
// 2025 opens it and reports the same three texts. Before #442 this reader
// stopped on the first text (`legacy_text_arrows_test.cpp`).
#[test]
fn every_leader_text_is_read() {
    let model = parse("legacy_text_arrows_2017.skp");
    let texts: Vec<&str> = model.root().texts.iter().map(|t| t.text.as_str()).collect();
    assert_eq!(texts, ["Dot", "Closed arrow", "Open arrow"]);
}
