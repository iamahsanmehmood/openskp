// Port of the Python reader's fixes for SketchUp 4, 5 and 2019 files
// (openskp#284, #440).
//
// Fixtures are small synthetic models, saved down through the installed
// SketchUp library from the same sources as the existing legacy fixtures
// (legacy_v6_synthetic.skp and the one-face textured model behind
// legacy_v3_textured.skp); no private content:
//
//  * legacy_v4/v5/v2019_synthetic.skp: 4 definitions (InnerFrame, Frame,
//    Panel, Post), 10 root instances, a "Roof" layer, 24 materials.
//  * legacy_v4/v5_textured.skp: one painted face with an 8x8 PNG, stored as a
//    bare inline image before SketchUp 6 (not a CDib object).
//  * legacy_v2020_textured.skp: the same model at SketchUp 2020 - a model with
//    NO component definitions, whose root entities used to be read as
//    "definitions" and silently dropped.
//
// Parsing without an error is not enough (an early V3 attempt "succeeded"
// with one definition of four), so these assert parsed structure and
// cross-check it against the V6 file from the same source.

mod common;

use std::collections::{BTreeMap, BTreeSet};

use common::fixture;
use openskp::{EntityId, Instance, ParseOptions, SkpFile, SkpModel, Texture};

fn parse(name: &str) -> SkpModel {
    SkpFile::open(fixture(name))
        .unwrap()
        .parse(&ParseOptions::default())
        .unwrap()
}

fn num(x: f64) -> String {
    format!("{:.5}", if x == 0.0 { 0.0 } else { x })
}

fn matrix_text(m: &[f64]) -> String {
    m.iter().map(|&x| num(x) + ",").collect()
}

fn joined(mut v: Vec<String>) -> String {
    v.sort();
    v.iter().map(|s| format!("{s};")).collect()
}

/// Everything that matters about a model, keyed by names and coordinates (ids
/// differ between versions).
fn canonical(m: &SkpModel, instance_names: bool) -> String {
    let mat_name: BTreeMap<EntityId, &str> = m
        .materials
        .iter()
        .filter_map(|x| x.id.map(|id| (id, x.name.as_str())))
        .collect();
    let def_name = |idx: Option<EntityId>| -> String {
        idx.and_then(|i| m.definitions.get(&i))
            .map_or_else(|| "?".to_owned(), |d| d.name.clone())
    };
    let inst_text = |i: &Instance| {
        format!(
            "{}|{}|{}",
            def_name(i.ref_idx),
            matrix_text(&i.matrix),
            if instance_names { i.name.as_str() } else { "" }
        )
    };

    let mut out = String::from("layers:");
    out += &joined(m.layers.iter().map(|l| l.name.clone()).collect());
    out += "materials:";
    out += &joined(m.materials.iter().map(|x| x.name.clone()).collect());

    let mut defs: Vec<_> = m.definitions.values().collect();
    defs.sort_by(|a, b| a.name.cmp(&b.name));
    for d in defs {
        let verts = d
            .vertices
            .values()
            .map(|v| format!("{},{},{}", num(v.x), num(v.y), num(v.z)))
            .collect();
        let faces = d
            .faces
            .values()
            .map(|f| {
                let mat = f
                    .material_id
                    .and_then(|id| mat_name.get(&id).copied())
                    .unwrap_or("-");
                format!("{mat}/{}", if f.hidden { "1" } else { "0" })
            })
            .collect();
        let insts = d.instances.iter().map(inst_text).collect();
        out += &format!(
            "def {}:v={} e={} f={} i={}|",
            d.name,
            joined(verts),
            d.edges.len(),
            joined(faces),
            joined(insts)
        );
    }
    out += "root:";
    out += &joined(m.root().instances.iter().map(inst_text).collect());
    out
}

struct SyntheticCase {
    file: &'static str,
    version: &'static str,
    instance_names: bool, // SketchUp 4 stores none
}

const SYNTHETIC: [SyntheticCase; 3] = [
    SyntheticCase {
        file: "legacy_v4_synthetic.skp",
        version: "{4.0.1}",
        instance_names: false,
    },
    SyntheticCase {
        file: "legacy_v5_synthetic.skp",
        version: "{5.0.1}",
        instance_names: true,
    },
    SyntheticCase {
        file: "legacy_v2019_synthetic.skp",
        version: "{19.0.1}",
        instance_names: true,
    },
];

#[test]
fn parse_and_report_their_version() {
    for c in &SYNTHETIC {
        assert_eq!(parse(c.file).version, c.version, "{}", c.file);
    }
}

#[test]
fn match_the_v6_file_from_the_same_source() {
    for c in &SYNTHETIC {
        assert_eq!(
            canonical(&parse(c.file), c.instance_names),
            canonical(&parse("legacy_v6_synthetic.skp"), c.instance_names),
            "{}",
            c.file
        );
    }
}

#[test]
fn are_complete_not_just_error_free() {
    for c in &SYNTHETIC {
        let model = parse(c.file);
        let defs: BTreeMap<&str, _> = model
            .definitions
            .values()
            .map(|d| (d.name.as_str(), d))
            .collect();
        for (name, d) in &defs {
            assert_eq!(d.faces.len(), 1, "{} {name}", c.file);
            assert_eq!(d.edges.len(), 4, "{} {name}", c.file);
            assert_eq!(d.vertices.len(), 4, "{} {name}", c.file);
        }
        let names: BTreeSet<&str> = defs.keys().copied().collect();
        assert_eq!(
            names,
            BTreeSet::from(["InnerFrame", "Frame", "Panel", "Post"]),
            "{}",
            c.file
        );
        assert_eq!(model.root().instances.len(), 10, "{}", c.file);
        assert_eq!(model.materials.len(), 24, "{}", c.file);
        assert!(
            model.layers.iter().any(|l| l.name == "Layer0"),
            "{}",
            c.file
        );
        assert!(model.layers.iter().any(|l| l.name == "Roof"), "{}", c.file);
        let panel = &defs["Panel"].instances;
        assert_eq!(panel.len(), 1, "{}", c.file);
        assert_eq!(
            model.definitions[&panel[0].ref_idx.unwrap()].name,
            "InnerFrame",
            "{}",
            c.file
        );
    }
}

#[test]
fn sketchup4_has_no_instance_names_and_sketchup5_does() {
    let v6 = parse("legacy_v6_synthetic.skp");
    let v4 = parse("legacy_v4_synthetic.skp");
    let v5 = parse("legacy_v5_synthetic.skp");
    assert!(v6.root().instances.iter().any(|i| !i.name.is_empty()));
    assert!(v4.root().instances.iter().all(|i| i.name.is_empty()));
    assert_eq!(v5.root().instances.len(), v6.root().instances.len());
    for (k, (a, b)) in v5
        .root()
        .instances
        .iter()
        .zip(&v6.root().instances)
        .enumerate()
    {
        assert_eq!(a.name, b.name, "{k}");
    }
}

fn texture_of(m: &SkpModel) -> Option<&Texture> {
    m.materials
        .iter()
        .filter_map(|x| x.texture.as_ref())
        .find(|t| t.data.is_some())
}

#[test]
fn sketchup4_and5_read_the_bare_inline_texture() {
    // Was: texture object is not a dib.
    let reference = parse("legacy_v3_textured.skp");
    let ref_tex = texture_of(&reference).expect("reference texture");
    for file in ["legacy_v4_textured.skp", "legacy_v5_textured.skp"] {
        let model = parse(file);
        let tex = texture_of(&model).unwrap_or_else(|| panic!("{file}: no texture"));
        let data = tex.data.as_ref().unwrap();
        assert_eq!(data, ref_tex.data.as_ref().unwrap(), "{file}"); // the same PNG, byte for byte
        assert!(data.len() >= 4);
        assert_eq!(data[0], 0x89, "{file}");
        assert_eq!(data[1], b'P', "{file}");
        assert_eq!(model.root().faces.len(), 1, "{file}");
    }
}

#[test]
fn sketchup2020_model_without_definitions_keeps_its_root_entities() {
    // The root entity count was handed over as a "definition count", so the
    // face and its four edges were consumed as definitions and the model came
    // out empty - with no error.
    let model = parse("legacy_v2020_textured.skp");
    assert_eq!(model.root().faces.len(), 1);
    assert_eq!(model.root().edges.len(), 4);
    assert_eq!(model.root().vertices.len(), 4);
    assert!(texture_of(&model).is_some());
}
