//! Native component and root scale-grip suppression masks (#519).

use std::path::{Path, PathBuf};

use openskp::create::{
    create, DefinitionOptions, DocumentOptions, FaceOptions, InstanceOptions, SkpBuilder,
};
use openskp::{open_existing, to_rust_code, ParseOptions, SkpFile, SkpModel};

fn cpp_fixture(name: &str) -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../cpp/tests/fixtures")
        .join(name)
}

fn read(builder: &SkpBuilder) -> SkpModel {
    SkpFile::from_buffer(builder.to_bytes().unwrap())
        .parse(&ParseOptions::default())
        .unwrap()
}

#[test]
fn native_scale_mask_round_trip() {
    for mask in [0u32, 1, 2, 4, 8, 16, 32, 64, 127, 0x1234_5678] {
        let mut builder = create();
        let document = DocumentOptions {
            no_scale_mask: mask,
            ..DocumentOptions::default()
        };
        builder.set_document_options(&document).unwrap();
        let options = DefinitionOptions {
            always_faces_camera: true,
            shadows_face_sun: true,
            no_scale_mask: mask,
            ..DefinitionOptions::default()
        };
        let mut def = builder
            .add_component_definition("MaskProbe", &options)
            .unwrap();
        let tri = [[0.0, 0.0, 0.0], [10.0, 0.0, 0.0], [0.0, 10.0, 0.0]];
        def.add_face(&tri, &FaceOptions::default()).unwrap();
        let handle = def.close().unwrap();
        builder
            .add_instance(handle, &InstanceOptions::default())
            .unwrap();

        let model = read(&builder);
        assert_eq!(model.definitions.len(), 1);
        let actual = model.definitions.values().next().unwrap();
        assert_eq!(actual.no_scale_mask, mask);
        assert_eq!(model.root().no_scale_mask, mask);
        assert!(actual.always_faces_camera);
        assert!(actual.shadows_face_sun);
        assert!(!actual.is_group);
    }
}

#[test]
fn reads_sdk_scale_mask_fixtures() {
    for name in ["behavior_mask_2017.skp", "behavior_mask_2021.skp"] {
        let model = SkpFile::open(cpp_fixture(name))
            .unwrap()
            .parse(&ParseOptions::default())
            .unwrap();
        assert_eq!(model.definitions.len(), 1);
        let actual = model.definitions.values().next().unwrap();
        assert_eq!(actual.no_scale_mask, 127);
    }
}

#[test]
fn reads_sdk_root_scale_mask_fixtures() {
    for name in [
        "root_behavior_mask_2013.skp",
        "root_behavior_mask_2017.skp",
        "root_behavior_mask_2021.skp",
    ] {
        let model = SkpFile::open(cpp_fixture(name))
            .unwrap()
            .parse(&ParseOptions::default())
            .unwrap();
        let expected = if name.contains("2021") { 127 } else { 2 };
        assert_eq!(model.root().no_scale_mask, expected);
        assert_eq!(model.definitions.len(), 1);
        let actual = model.definitions.values().next().unwrap();
        assert_eq!(actual.no_scale_mask, 0);
    }
}

#[test]
fn app_saved_files_have_no_scale_mask() {
    for name in [
        "capilla_quiroz_v17.skp",
        "gondola_v20.skp",
        "zero_material_custom_layer_v18.skp",
    ] {
        let model = SkpFile::open(cpp_fixture(name))
            .unwrap()
            .parse(&ParseOptions::default())
            .unwrap();
        assert_eq!(model.root().no_scale_mask, 0, "{name}");
        for defn in model.definitions.values() {
            assert_eq!(defn.no_scale_mask, 0, "{name}");
        }
    }
}

#[test]
fn scale_mask_survives_edit_and_code_generation() {
    let mut builder = create();
    let document = DocumentOptions {
        no_scale_mask: 16,
        ..DocumentOptions::default()
    };
    builder.set_document_options(&document).unwrap();
    let options = DefinitionOptions {
        no_scale_mask: 64,
        ..DefinitionOptions::default()
    };
    let mut def = builder
        .add_component_definition("Masked", &options)
        .unwrap();
    let tri = [[0.0, 0.0, 0.0], [10.0, 0.0, 0.0], [0.0, 10.0, 0.0]];
    def.add_face(&tri, &FaceOptions::default()).unwrap();
    let handle = def.close().unwrap();
    builder
        .add_instance(handle, &InstanceOptions::default())
        .unwrap();

    let file_name = format!("openskp_scale_mask_edit_{}.skp", std::process::id());
    let path = std::env::temp_dir().join(file_name);
    builder.save(&path).unwrap();
    let edited = open_existing(&path).unwrap();
    let _ = std::fs::remove_file(&path);

    let model = read(&edited.builder);
    assert_eq!(model.definitions.len(), 1);
    let actual = model.definitions.values().next().unwrap();
    assert_eq!(actual.no_scale_mask, 64);
    assert_eq!(model.root().no_scale_mask, 16);

    let code = to_rust_code(&model);
    assert!(code.contains(".no_scale_mask = 64;"));
    assert!(code.contains("document.no_scale_mask = 16;"));
}
