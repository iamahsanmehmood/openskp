//! Tests for `openskp::edit::open_existing`, ported from the C++ `edit_test.cpp`: build a file
//! with the writer, save it, reopen it with `open_existing()`, and check the rebuilt builder's own
//! re-parsed output preserves the original's structure - plus an end-to-end pass against a real
//! SketchUp-SDK-authored fixture (capilla_quiroz_v17.skp).
//!
//! `open_existing` only accepts legacy-format sources, and the writer only emits that format, so
//! `legacy.rs` is ported. The replay itself is exercised against VFF-parsed models by the unit
//! tests in `src/edit.rs`.

mod common;

use std::path::PathBuf;

use common::fixture;
use openskp::create::{
    create, Color3, DefinitionOptions, FaceOptions, GuideOptions, InstanceOptions, LayerOptions,
    Point3,
};
use openskp::{open_existing, OpenExistingError, ParseOptions, SkpFile, SkpModel};

fn temp_skp(stem: &str) -> PathBuf {
    std::env::temp_dir().join(format!("openskp_edit_test_{stem}.skp"))
}

/// A syntactically-tiny PNG - just the magic bytes plus filler.
fn fake_png_bytes() -> Vec<u8> {
    let mut data = vec![0x89, b'P', b'N', b'G', b'\r', b'\n', 0x1a, b'\n'];
    data.extend(std::iter::repeat_n(0x42, 64));
    data
}

fn parse(bytes: Vec<u8>) -> SkpModel {
    SkpFile::from_buffer(bytes)
        .parse(&ParseOptions::default())
        .expect("parse")
}

fn square(size: f64) -> Vec<Point3> {
    vec![
        [0.0, 0.0, 0.0],
        [size, 0.0, 0.0],
        [size, size, 0.0],
        [0.0, size, 0.0],
    ]
}

#[test]
fn rejects_a_modern_non_legacy_file() {
    let err = open_existing(fixture("Untitled.skp")).unwrap_err();
    assert!(matches!(err, OpenExistingError::Write(_)), "{err:?}");
}

#[test]
fn rejects_a_missing_file() {
    let err = open_existing(fixture("does_not_exist_at_all.skp")).unwrap_err();
    assert!(matches!(err, OpenExistingError::Write(_)), "{err:?}");
}

#[test]
fn round_trips_a_freshly_built_file() {
    let path = temp_skp("roundtrip");
    {
        let mut builder = create();
        let red = builder.add_material("Red", [255, 0, 0], None).unwrap();
        let roof = builder.add_layer("Roof", &LayerOptions::default()).unwrap();

        let mut chair = builder
            .add_component_definition("Chair", &DefinitionOptions::default())
            .unwrap();
        chair
            .add_face(&square(20.0), &FaceOptions::default())
            .unwrap();
        let chair = chair.close().unwrap();
        let iopts = InstanceOptions {
            translation: [200.0, 0.0, 0.0],
            ..Default::default()
        };
        builder.add_instance(chair, &iopts).unwrap();

        let opts = FaceOptions {
            material: Some(red),
            layer: Some(roof),
            ..Default::default()
        };
        builder.add_face(&square(100.0), &opts).unwrap();
        builder.save(&path).unwrap();
    }

    let mut result = open_existing(&path).unwrap();
    assert!(result.builder.materials_by_name().contains_key("Red"));
    assert!(result.builder.layers_by_name().contains_key("Roof"));
    assert!(result.definitions.contains_key("Chair"));

    // The rebuilt file should carry the same face/material/layer/instance/definition counts as
    // the original when parsed back with our own reader.
    let rebuilt = parse(result.builder.to_bytes().unwrap());
    assert_eq!(rebuilt.materials.len(), 1);
    assert_eq!(rebuilt.layers.len(), 2); // Layer0 + Roof
    assert_eq!(rebuilt.definitions.len(), 1);
    assert_eq!(rebuilt.root().faces.len(), 1);
    assert_eq!(rebuilt.root().instances.len(), 1);

    let _ = std::fs::remove_file(&path);
}

#[test]
fn default_projected_texture_gets_an_explicit_uv_pin_not_left_corrupted() {
    // Found via cross-language analysis (2026-08-28): replay_materials wrote every rebuilt
    // texture material with a corrupted default applied height, and replay_uv only replayed a
    // face's UV when it already had an explicit uv_transform - leaving a DEFAULT-projected
    // textured face's material corrupted end to end. Both are fixed; this checks the rebuilt face
    // actually gets a pin and that the material's applied size isn't the corrupted sentinel.
    let png_path = std::env::temp_dir().join("openskp_edit_test_uv_texture.png");
    std::fs::write(&png_path, fake_png_bytes()).unwrap();
    let path = temp_skp("default_projected_texture");
    {
        let mut builder = create();
        let brick = builder
            .add_texture_material("Brick", &png_path, None, None, None)
            .unwrap();
        let opts = FaceOptions {
            material: Some(brick),
            ..Default::default()
        };
        builder.add_face(&square(100.0), &opts).unwrap(); // no front_uv
        builder.save(&path).unwrap();
    }
    let _ = std::fs::remove_file(&png_path);

    let mut result = open_existing(&path).unwrap();
    let rebuilt = parse(result.builder.to_bytes().unwrap());
    let _ = std::fs::remove_file(&path);

    assert_eq!(rebuilt.materials.len(), 1);
    let texture = rebuilt.materials[0].texture.as_ref().expect("texture");
    assert_eq!(texture.width, 1.0);
    assert_eq!(texture.height, 1.0);
    assert_eq!(rebuilt.root().faces.len(), 1);
    assert!(rebuilt
        .root()
        .faces
        .values()
        .next()
        .unwrap()
        .uv_transform
        .is_some());
}

#[test]
fn genuinely_empty_instance_name_is_preserved_not_replaced() {
    // Found via cross-language analysis (2026-08-28): add_instance's own name fallback and
    // replay_instance's `if !inst.name.is_empty()` both silently replaced a genuinely empty
    // instance name with its definition's name.
    let path = temp_skp("empty_instance_name");
    {
        let mut builder = create();
        let mut bx = builder
            .add_component_definition("Box", &DefinitionOptions::default())
            .unwrap();
        bx.add_face(&square(10.0), &FaceOptions::default()).unwrap();
        let bx = bx.close().unwrap();
        let iopts = InstanceOptions {
            name: Some(String::new()),
            ..Default::default()
        };
        builder.add_instance(bx, &iopts).unwrap();
        builder.save(&path).unwrap();
    }

    let source = SkpFile::open(&path)
        .unwrap()
        .parse(&ParseOptions::default())
        .unwrap();
    assert_eq!(source.root().instances.len(), 1);
    assert_eq!(source.root().instances[0].name, "");

    let mut result = open_existing(&path).unwrap();
    let rebuilt = parse(result.builder.to_bytes().unwrap());
    let _ = std::fs::remove_file(&path);

    assert_eq!(rebuilt.root().instances.len(), 1);
    assert_eq!(rebuilt.root().instances[0].name, "");
}

#[test]
fn genuinely_empty_definition_name_is_preserved_not_replaced() {
    // Same bug class as the empty instance name case above: a fabricated "Definition<id>" name
    // used to replace a genuinely empty one. SketchUp Groups are internally just unnamed
    // component definitions, so an empty name is common in real files.
    let path = temp_skp("empty_definition_name");
    {
        let mut builder = create();
        let mut bx = builder
            .add_component_definition("", &DefinitionOptions::default())
            .unwrap();
        bx.add_face(&square(10.0), &FaceOptions::default()).unwrap();
        let bx = bx.close().unwrap();
        builder
            .add_instance(bx, &InstanceOptions::default())
            .unwrap();
        builder.save(&path).unwrap();
    }

    let source = SkpFile::open(&path)
        .unwrap()
        .parse(&ParseOptions::default())
        .unwrap();
    assert_eq!(source.definitions.len(), 1);
    assert_eq!(source.definitions.values().next().unwrap().name, "");

    let mut result = open_existing(&path).unwrap();
    let rebuilt = parse(result.builder.to_bytes().unwrap());
    let _ = std::fs::remove_file(&path);

    assert_eq!(rebuilt.definitions.len(), 1);
    assert_eq!(rebuilt.definitions.values().next().unwrap().name, "");
}

#[test]
fn returned_builder_can_add_more_geometry_and_cannot_register_new_sections() {
    let path = temp_skp("addmore");
    {
        let mut builder = create();
        builder
            .add_face(&square(10.0), &FaceOptions::default())
            .unwrap();
        builder.save(&path).unwrap();
    }

    let mut result = open_existing(&path).unwrap();
    // More geometry is fine.
    result
        .builder
        .add_face(
            &[
                [50.0, 50.0, 0.0],
                [60.0, 50.0, 0.0],
                [60.0, 60.0, 0.0],
                [50.0, 60.0, 0.0],
            ],
            &FaceOptions::default(),
        )
        .unwrap();
    // Registering a genuinely new material/layer is not - the file format's ordering
    // requirement (materials/layers/definitions before any geometry) was already satisfied by
    // replaying the source's own root-level geometry.
    let color: Color3 = [1, 2, 3];
    assert!(result.builder.add_material("New", color, None).is_err());
    assert!(result
        .builder
        .add_layer("New", &LayerOptions::default())
        .is_err());

    let model = parse(result.builder.to_bytes().unwrap());
    assert_eq!(model.root().faces.len(), 2);

    let _ = std::fs::remove_file(&path);
}

#[test]
fn definitions_map_allows_placing_more_instances_of_an_existing_definition() {
    let path = temp_skp("moreinstances");
    {
        let mut builder = create();
        let mut wheel = builder
            .add_component_definition("Wheel", &DefinitionOptions::default())
            .unwrap();
        wheel
            .add_face(&square(10.0), &FaceOptions::default())
            .unwrap();
        let wheel = wheel.close().unwrap();
        builder
            .add_instance(wheel, &InstanceOptions::default())
            .unwrap();
        builder.save(&path).unwrap();
    }

    let mut result = open_existing(&path).unwrap();
    let wheel = *result.definitions.get("Wheel").expect("Wheel");
    let opts = InstanceOptions {
        translation: [500.0, 0.0, 0.0],
        ..Default::default()
    };
    result.builder.add_instance(wheel, &opts).unwrap();

    let model = parse(result.builder.to_bytes().unwrap());
    assert_eq!(model.root().instances.len(), 2);

    let _ = std::fs::remove_file(&path);
}

#[test]
fn empty_blank_scaffold_has_no_geometry_to_replay_but_still_opens() {
    // blank_v17.skp has 0 materials/layers-beyond-Layer0/definitions/root entities - exercises the
    // "nothing to replay" path (no crash, an empty-but-valid builder).
    let mut result = open_existing(fixture("blank_v17.skp")).unwrap();
    assert!(result.definitions.is_empty());
    // Can't call to_bytes() yet - matches create()'s own "no geometry added" guard - but adding a
    // face and saving should work.
    assert!(result.builder.to_bytes().is_err());
    result
        .builder
        .add_face(
            &[[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [1.0, 1.0, 0.0]],
            &FaceOptions::default(),
        )
        .unwrap();
    result.builder.to_bytes().unwrap();
}

// ---------------------------------------------------------------------------------------------
// End-to-end against a real SketchUp-SDK-authored fixture.
// ---------------------------------------------------------------------------------------------

#[test]
fn real_fixture_round_trips_with_consistent_top_level_counts() {
    let original = SkpFile::open(fixture("capilla_quiroz_v17.skp"))
        .unwrap()
        .parse(&ParseOptions::default())
        .unwrap();

    let mut result = open_existing(fixture("capilla_quiroz_v17.skp")).unwrap();
    let rebuilt = parse(result.builder.to_bytes().unwrap());

    // Materials and layers are replayed 1:1.
    assert_eq!(rebuilt.materials.len(), original.materials.len());
    assert_eq!(rebuilt.layers.len(), original.layers.len());
    // Definitions with no replayable geometry are skipped (and warned about), so the definition
    // count can be lower but never higher.
    assert!(rebuilt.definitions.len() <= original.definitions.len());
}

#[test]
fn replays_nested_definition_construction() {
    let path = temp_skp("nested_construction");
    {
        let mut builder = create();
        let mut nested = builder
            .add_component_definition("GuideGroup", &DefinitionOptions::default())
            .unwrap();
        nested
            .add_face(&square(10.0), &FaceOptions::default())
            .unwrap();
        nested
            .add_construction_point([4.0, 5.0, 6.0], &GuideOptions::default())
            .unwrap();
        nested
            .add_construction_line(
                [1.0, 1.0, 1.0],
                Some([2.0, 2.0, 2.0]),
                None,
                &GuideOptions::default(),
            )
            .unwrap();
        let nested = nested.close().unwrap();
        builder
            .add_instance(nested, &InstanceOptions::default())
            .unwrap();
        builder
            .add_face(
                &[[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]],
                &FaceOptions::default(),
            )
            .unwrap();
        builder.save(&path).unwrap();
    }

    let mut result = open_existing(&path).unwrap();
    let rebuilt = parse(result.builder.to_bytes().unwrap());
    let _ = std::fs::remove_file(&path);

    let group = rebuilt
        .definitions
        .values()
        .find(|d| d.name == "GuideGroup")
        .expect("GuideGroup");
    assert_eq!(group.construction_points.len(), 1);
    common::assert_near(group.construction_points[0].position[0], 4.0, 1e-9);
    assert_eq!(group.construction_lines.len(), 1);
    assert!(
        group.construction_lines[0].start.is_some() && group.construction_lines[0].end.is_some()
    );
}

// Ported from the C++ edit_test.cpp (openskp#475): open_existing replays a guide's hidden flag
// and layer.
#[test]
fn replays_guide_hidden_flag_and_layer() {
    let path = temp_skp("guide_hidden_layer");
    {
        let mut builder = create();
        let guides = builder
            .add_layer("Guides", &LayerOptions::default())
            .unwrap();
        builder
            .add_face(
                &[[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]],
                &FaceOptions::default(),
            )
            .unwrap();
        builder
            .add_construction_point(
                [1.0, 2.0, 3.0],
                &GuideOptions {
                    layer: None,
                    hidden: true,
                },
            )
            .unwrap();
        builder
            .add_construction_line(
                [0.0; 3],
                Some([12.0, 0.0, 0.0]),
                None,
                &GuideOptions {
                    layer: Some(guides),
                    hidden: true,
                },
            )
            .unwrap();
        builder.save(&path).unwrap();
    }

    let mut result = open_existing(&path).unwrap();
    let rebuilt = parse(result.builder.to_bytes().unwrap());
    let _ = std::fs::remove_file(&path);

    let points = &rebuilt.root().construction_points;
    assert_eq!(points.len(), 1);
    assert!(points[0].hidden);
    let lines = &rebuilt.root().construction_lines;
    assert_eq!(lines.len(), 1);
    assert!(lines[0].hidden);
    assert_eq!(lines[0].layer, "Guides");
}
