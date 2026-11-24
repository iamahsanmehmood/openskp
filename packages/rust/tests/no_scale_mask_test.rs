// SketchUp's native scale-grip suppression mask (`no_scale_mask`) on
// definitions and on the root model: read from classic and VFF files,
// written, and kept by `open_existing` and code generation (openskp#519,
// ported from C++'s `io_fidelity_test.cpp`).
//
// Fixtures (from the C++ package, made with the SketchUp 2026 C API; see
// `packages/cpp/tests/fixtures/behavior_masks.md`): each holds one empty
// definition `MaskProbe` and one root instance of it.
//   behavior_mask_2017.skp       classic SU2017, definition mask 127, root 0
//   behavior_mask_2021.skp       VFF, definition mask 127, root 0
//   root_behavior_mask_2013.skp  classic SU2013, root mask 2, definition 0
//   root_behavior_mask_2017.skp  classic SU2017, root mask 2, definition 0
//   root_behavior_mask_2021.skp  VFF, root mask 127, definition 0

mod common;

use common::fixture;
use openskp::create::{
    create, DefinitionOptions, DocumentOptions, FaceOptions, InstanceOptions, SkpBuilder,
};
use openskp::{open_existing, parse_skp, to_rust_code, ParseOptions, SkpFile, SkpModel};

fn parse_file(name: &str) -> SkpModel {
    SkpFile::open(fixture(name))
        .unwrap()
        .parse(&ParseOptions::default())
        .unwrap()
}

fn read(builder: &mut SkpBuilder) -> SkpModel {
    parse_skp(&builder.to_bytes().unwrap(), &ParseOptions::default()).unwrap()
}

fn masked_builder(root_mask: u32, definition_mask: u32) -> SkpBuilder {
    let mut builder = create();
    builder
        .set_document_options(&DocumentOptions {
            no_scale_mask: root_mask,
            ..DocumentOptions::default()
        })
        .unwrap();
    let options = DefinitionOptions {
        no_scale_mask: definition_mask,
        ..DefinitionOptions::default()
    };
    let mut def = builder
        .add_component_definition("MaskProbe", &options)
        .unwrap();
    def.add_face(
        &[[0.0, 0.0, 0.0], [10.0, 0.0, 0.0], [0.0, 10.0, 0.0]],
        &FaceOptions::default(),
    )
    .unwrap();
    let handle = def.close().unwrap();
    builder
        .add_instance(handle, &InstanceOptions::default())
        .unwrap();
    builder
}

#[test]
fn defaults_to_zero() {
    assert_eq!(DocumentOptions::default().no_scale_mask, 0);
    assert_eq!(DefinitionOptions::default().no_scale_mask, 0);
    let model = read(&mut masked_builder(0, 0));
    assert_eq!(model.root().no_scale_mask, 0);
    assert_eq!(model.definitions.values().next().unwrap().no_scale_mask, 0);
}

#[test]
fn round_trips_every_bit_and_unknown_bits() {
    for mask in [0u32, 1, 2, 4, 8, 16, 32, 64, 127, 0x1234_5678] {
        let model = read(&mut masked_builder(mask, mask));
        assert_eq!(model.definitions.len(), 1);
        let definition = model.definitions.values().next().unwrap();
        assert_eq!(definition.no_scale_mask, mask);
        assert_eq!(model.root().no_scale_mask, mask);
        assert!(!definition.is_group);
        assert!(!definition.always_faces_camera);
        assert!(!definition.shadows_face_sun);
    }
}

#[test]
fn reads_sdk_definition_masks() {
    for name in ["behavior_mask_2017.skp", "behavior_mask_2021.skp"] {
        let model = parse_file(name);
        assert_eq!(model.definitions.len(), 1, "{name}");
        assert_eq!(
            model.definitions.values().next().unwrap().no_scale_mask,
            127,
            "{name}"
        );
        assert_eq!(model.root().no_scale_mask, 0, "{name}");
    }
}

#[test]
fn reads_sdk_root_masks() {
    for (name, mask) in [
        ("root_behavior_mask_2013.skp", 2),
        ("root_behavior_mask_2017.skp", 2),
        ("root_behavior_mask_2021.skp", 127),
    ] {
        let model = parse_file(name);
        assert_eq!(model.root().no_scale_mask, mask, "{name}");
        assert_eq!(model.definitions.len(), 1, "{name}");
        assert_eq!(
            model.definitions.values().next().unwrap().no_scale_mask,
            0,
            "{name}"
        );
    }
}

// Files saved by SketchUp itself carry a CDib thumbnail between the version
// map and the root behavior, so a fixed offset from the terminator would land
// inside the PNG; the SDK reports 0 for all three.
#[test]
fn app_saved_files_have_no_scale_mask() {
    for name in [
        "capilla_quiroz_v17.skp",
        "gondola_v20.skp",
        "zero_material_custom_layer_v18.skp",
    ] {
        let model = parse_file(name);
        assert_eq!(model.root().no_scale_mask, 0, "{name}");
        for definition in model.definitions.values() {
            assert_eq!(definition.no_scale_mask, 0, "{name} {}", definition.name);
        }
    }
}

#[test]
fn survives_edit_and_code_generation() {
    let path = std::env::temp_dir().join(format!(
        "openskp_scale_mask_edit_{}.skp",
        std::process::id()
    ));
    masked_builder(16, 64).save(&path).unwrap();
    let edited = open_existing(&path);
    std::fs::remove_file(&path).unwrap();
    let mut edited = edited.unwrap();

    let model = read(&mut edited.builder);
    assert_eq!(model.definitions.len(), 1);
    assert_eq!(model.definitions.values().next().unwrap().no_scale_mask, 64);
    assert_eq!(model.root().no_scale_mask, 16);

    let code = to_rust_code(&model);
    assert!(
        code.contains("&DefinitionOptions { no_scale_mask: 64, ..Default::default() }"),
        "{code}"
    );
    assert!(
        code.contains(
            "builder.set_document_options(&DocumentOptions { no_scale_mask: 16, ..Default::default() })?;"
        ),
        "{code}"
    );
}

#[test]
fn code_without_masks_is_unchanged() {
    let model = read(&mut masked_builder(0, 0));
    let code = to_rust_code(&model);
    assert!(!code.contains("no_scale_mask"));
    assert!(!code.contains("set_document_options"));
}
