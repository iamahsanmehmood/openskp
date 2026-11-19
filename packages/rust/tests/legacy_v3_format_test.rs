// Port of openskp#284's "V3" fix (Python #407, C++ #410): SketchUp 3 files
// parse.
//
// Fixture: fixtures/legacy_v3_synthetic.skp - the same small synthetic model
// as legacy_v6_synthetic.skp (4 component definitions - InnerFrame, Frame,
// Panel, Post; InnerFrame also placed as a nested group inside Panel; 10 root
// instances; a custom "Roof" layer; Frame's face painted flat red) built with
// the OpenSKP writer and re-saved at SketchUp 3 through the installed
// SketchUp library. No private content.
//
// A V3 file differs from every later era in a dozen small ways (see FIRST_V4
// in the legacy reader). Parsing without an error was NOT enough to call it
// fixed - an intermediate state "succeeded" with one definition of four, a
// phantom layer and no root instances - so these tests assert the parsed
// structure and cross-check it against the V6 file from the same source.

mod common;

use std::collections::{BTreeMap, BTreeSet};

use common::{assert_near, fixture};
use openskp::{Definition, Instance, ParseOptions, SkpFile, SkpModel};

fn parse(name: &str) -> SkpModel {
    SkpFile::open(fixture(name))
        .unwrap()
        .parse(&ParseOptions::default())
        .unwrap()
}

fn by_name(model: &SkpModel) -> BTreeMap<&str, &Definition> {
    model
        .definitions
        .values()
        .map(|d| (d.name.as_str(), d))
        .collect()
}

fn def_name<'a>(model: &'a SkpModel, inst: &Instance) -> &'a str {
    &model.definitions[&inst.ref_idx.unwrap()].name
}

#[test]
fn parses_and_reports_its_version() {
    assert_eq!(parse("legacy_v3_synthetic.skp").version, "{3.0.1}");
}

#[test]
fn every_definition_is_present_with_its_geometry() {
    let model = parse("legacy_v3_synthetic.skp");
    let defs = by_name(&model);
    for (name, def) in &defs {
        assert_eq!(def.faces.len(), 1, "{name}");
        assert_eq!(def.edges.len(), 4, "{name}");
        assert_eq!(def.vertices.len(), 4, "{name}");
    }
    let names: BTreeSet<&str> = defs.keys().copied().collect();
    assert_eq!(
        names,
        BTreeSet::from(["InnerFrame", "Frame", "Panel", "Post"])
    );
    // Frame is a 20x20 square at the origin
    let frame = defs["Frame"];
    let mut xs: Vec<f64> = frame.vertices.values().map(|v| v.x).collect();
    let mut ys: Vec<f64> = frame.vertices.values().map(|v| v.y).collect();
    xs.sort_by(f64::total_cmp);
    xs.dedup();
    ys.sort_by(f64::total_cmp);
    ys.dedup();
    assert_eq!(xs, [0.0, 20.0]);
    assert_eq!(ys, [0.0, 20.0]);
}

#[test]
fn places_every_root_instance() {
    let model = parse("legacy_v3_synthetic.skp");
    let insts = &model.root().instances;
    assert_eq!(insts.len(), 10);
    let expected = [
        "Frame", "Panel", "Post", "Frame", "Panel", "Post", "Frame", "Panel", "Post", "Frame",
    ];
    for (i, inst) in insts.iter().enumerate() {
        assert_eq!(def_name(&model, inst), expected[i], "{i}");
        // legacy transform: 3x3 rotation (0-8), translation (9-11), scale;
        // translated 40 inches per step (Python's test_legacy_v3_format)
        assert_eq!(inst.matrix[9], i as f64 * 40.0, "{i}");
    }
}

#[test]
fn nested_group_is_placed_inside_panel() {
    let model = parse("legacy_v3_synthetic.skp");
    let panel = by_name(&model)["Panel"];
    assert_eq!(panel.instances.len(), 1);
    assert_eq!(def_name(&model, &panel.instances[0]), "InnerFrame");
}

#[test]
fn face_material_comes_from_the_faces_own_pointer() {
    // SketchUp 3's face opens with its FRONT MATERIAL pointer, not an
    // attribute container: Frame's face was painted red, the others not.
    let model = parse("legacy_v3_synthetic.skp");
    let mats = model.materials_by_id();
    let defs = by_name(&model);
    let painted = defs["Frame"].faces.values().next().unwrap();
    let red = mats[&painted.material_id.expect("painted")];
    assert_eq!(red.name, "Red");
    assert_eq!(&red.color[..3], &[255, 0, 0]);
    for name in ["Panel", "Post", "InnerFrame"] {
        assert!(
            defs[name]
                .faces
                .values()
                .next()
                .unwrap()
                .material_id
                .is_none(),
            "{name}"
        );
    }
}

#[test]
fn no_face_is_spuriously_hidden() {
    let model = parse("legacy_v3_synthetic.skp");
    for def in model.definitions.values() {
        assert!(def.faces.values().all(|f| !f.hidden));
    }
}

#[test]
fn keeps_the_custom_layer() {
    let model = parse("legacy_v3_synthetic.skp");
    assert!(model.layers.iter().any(|l| l.name == "Layer0"));
    assert!(model.layers.iter().any(|l| l.name == "Roof"));
}

// Python extra: each face's own layer id (Python's test_legacy_v3_format
// `test_layers_and_face_layer_assignment`): Post's face is on Roof.
#[test]
fn faces_carry_their_own_layer_id() {
    let model = parse("legacy_v3_synthetic.skp");
    let defs = by_name(&model);
    let post_face = defs["Post"].faces.values().next().unwrap();
    let frame_face = defs["Frame"].faces.values().next().unwrap();
    assert!(post_face.layer_id.is_some() && frame_face.layer_id.is_some());
    assert_ne!(post_face.layer_id, frame_face.layer_id);
}

#[test]
fn matches_sketchup6_built_from_the_same_source() {
    let v3 = parse("legacy_v3_synthetic.skp");
    let v6 = parse("legacy_v6_synthetic.skp");
    let d3 = by_name(&v3);
    let d6 = by_name(&v6);
    assert_eq!(d3.len(), d6.len());
    for (name, a) in &d3 {
        let b = d6[name];
        assert_eq!(a.faces.len(), b.faces.len(), "{name}");
        assert_eq!(a.edges.len(), b.edges.len(), "{name}");
        assert_eq!(a.vertices.len(), b.vertices.len(), "{name}");
    }
    assert_eq!(v3.root().instances.len(), v6.root().instances.len());
    for (a, b) in v3.root().instances.iter().zip(&v6.root().instances) {
        assert_eq!(def_name(&v3, a), def_name(&v6, b));
        assert_eq!(a.matrix.len(), b.matrix.len());
        for (x, y) in a.matrix.iter().zip(&b.matrix) {
            assert_near(*x, *y, 1e-9);
        }
    }
}

// A V3 file with ONE material cannot use the two-material bootstrap; the
// slot-base probe used to read a layer count V3 does not write and rejected
// every such file ("implausible layer count in base probe"). Fixture: one
// 20x20 face painted with "Checker", an 8x8 PNG generated by this project,
// saved down to SketchUp 3.
#[test]
fn single_material_parses_its_inline_png_and_painted_face() {
    let model = parse("legacy_v3_textured.skp");
    assert_eq!(model.version, "{3.0.1}");
    assert_eq!(model.materials.len(), 1);
    assert_eq!(model.materials[0].name, "Checker");
    let d = model.materials[0]
        .texture
        .as_ref()
        .and_then(|t| t.data.as_ref())
        .expect("texture data");
    assert!(d.len() >= 16);
    assert_eq!(&d[..8], &[0x89, b'P', b'N', b'G', 0x0d, 0x0a, 0x1a, 0x0a]);
    assert_eq!(
        &d[d.len() - 8..],
        &[b'I', b'E', b'N', b'D', 0xae, 0x42, 0x60, 0x82]
    );
    let faces: Vec<_> = model
        .root()
        .faces
        .values()
        .chain(
            model
                .definitions
                .values()
                .flat_map(|def| def.faces.values()),
        )
        .collect();
    assert_eq!(faces.len(), 1);
    assert_eq!(faces[0].material_id, model.materials[0].id);
}
