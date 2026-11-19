//! Reader-independent tests for `openskp::create`, ported from the C++ `create_test.cpp` (the
//! cases that only need the writer itself) plus writer contract checks of our own. Cases that
//! parse the output back live in `create_roundtrip_test.rs`; byte-exact parity with the C++
//! writer lives in `create_reference_test.rs`.

use std::f64::consts::PI;

use openskp::create::{
    create, ArcOptions, AttributeValue, CircleOptions, DefinitionOptions, FaceOptions,
    GroupOptions, GuideOptions, ImageOptions, InstanceOptions, LayerOptions, Point3,
    PolylineOptions, SkpBuilder,
};

const PNG: &str = "tests/fixtures/create_reference/fake.png";

fn square() -> Vec<Point3> {
    vec![
        [0.0, 0.0, 0.0],
        [100.0, 0.0, 0.0],
        [100.0, 100.0, 0.0],
        [0.0, 100.0, 0.0],
    ]
}

fn triangle() -> Vec<Point3> {
    vec![[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [1.0, 1.0, 0.0]]
}

fn u32_at(bytes: &[u8], pos: usize) -> u32 {
    u32::from_le_bytes(bytes[pos..pos + 4].try_into().unwrap())
}

// ---------------------------------------------------------------------------------------------
// Ported from create_test.cpp.
// ---------------------------------------------------------------------------------------------

#[test]
fn face_requires_at_least_3_points() {
    let mut b = create();
    assert!(b
        .add_face(&[[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]], &FaceOptions::default())
        .is_err());
}

#[test]
fn non_coplanar_face_raises_without_auto_triangulate() {
    let mut b = create();
    let warped = [
        [0.0, 0.0, 0.0],
        [10.0, 0.0, 0.0],
        [10.0, 10.0, 5.0],
        [0.0, 10.0, 0.0],
    ];
    assert!(b.add_face(&warped, &FaceOptions::default()).is_err());
}

#[test]
fn add_face_rejects_a_layer_handle_passed_as_material() {
    // The real-world mistake this guards against: a layer handle in `material` used to become a
    // dangling material reference that real SketchUp rejects outright.
    let mut b = create();
    let layer = b.add_layer("Layer0", &LayerOptions::default()).unwrap();
    let options = FaceOptions {
        material: Some(layer),
        ..Default::default()
    };
    assert!(b.add_face(&square(), &options).is_err());
}

#[test]
fn add_face_rejects_a_material_handle_passed_as_layer() {
    let mut b = create();
    let mat = b.add_material("Red", [255, 0, 0], None).unwrap();
    let options = FaceOptions {
        layer: Some(mat),
        ..Default::default()
    };
    assert!(b.add_face(&square(), &options).is_err());
}

#[test]
fn add_face_rejects_an_unrelated_back_material_handle() {
    let mut b = create();
    let layer = b.add_layer("Layer0", &LayerOptions::default()).unwrap();
    let options = FaceOptions {
        back_material: Some(layer),
        ..Default::default()
    };
    assert!(b.add_face(&square(), &options).is_err());
}

#[test]
fn add_face_rejects_a_handle_from_a_different_builder() {
    let mut other = create();
    let stray = other.add_material("Blue", [0, 0, 255], None).unwrap();
    let mut b = create();
    let options = FaceOptions {
        material: Some(stray),
        ..Default::default()
    };
    assert!(b.add_face(&square(), &options).is_err());
}

#[test]
fn add_instance_rejects_a_layer_handle_passed_as_material() {
    let mut b = create();
    let layer = b.add_layer("Layer0", &LayerOptions::default()).unwrap();
    let mut chair = b
        .add_component_definition("Chair", &DefinitionOptions::default())
        .unwrap();
    chair.add_face(&square(), &FaceOptions::default()).unwrap();
    let chair = chair.close().unwrap();
    let options = InstanceOptions {
        material: Some(layer),
        ..Default::default()
    };
    assert!(b.add_instance(chair, &options).is_err());
}

#[test]
fn add_group_rejects_an_unrelated_layer_handle() {
    let mut b = create();
    let mat = b.add_material("Red", [255, 0, 0], None).unwrap();
    let options = GroupOptions {
        name: Some("Table".into()),
        layer: Some(mat),
        ..Default::default()
    };
    assert!(b.add_group(&options).is_err());
}

#[test]
fn component_scope_add_face_rejects_an_unrelated_handle() {
    let mut b = create();
    let layer = b.add_layer("Layer0", &LayerOptions::default()).unwrap();
    let mut chair = b
        .add_component_definition("Chair", &DefinitionOptions::default())
        .unwrap();
    let options = FaceOptions {
        material: Some(layer),
        ..Default::default()
    };
    assert!(chair.add_face(&square(), &options).is_err());
    // the definition must still be usable after a rejected call
    chair.add_face(&square(), &FaceOptions::default()).unwrap();
}

#[test]
fn add_face_accepts_a_real_material_and_layer() {
    let mut b = create();
    let mat = b.add_material("Red", [255, 0, 0], None).unwrap();
    let layer = b.add_layer("MyLayer", &LayerOptions::default()).unwrap();
    let options = FaceOptions {
        material: Some(mat),
        layer: Some(layer),
        ..Default::default()
    };
    b.add_face(&square(), &options).unwrap();
    assert!(!b.to_bytes().unwrap().is_empty());
}

#[test]
fn duplicate_material_name_returns_same_handle() {
    let mut b = create();
    let a = b.add_material("Blue", [0, 0, 255], None).unwrap();
    let again = b.add_material("Blue", [10, 10, 10], None).unwrap(); // different color is ignored
    assert_eq!(a, again);
    assert_eq!(b.materials_by_name()["Blue"], a);
}

#[test]
fn materials_must_precede_geometry() {
    let mut b = create();
    b.add_face(&triangle(), &FaceOptions::default()).unwrap();
    assert!(b.add_material("TooLate", [1, 2, 3], None).is_err());
}

#[test]
fn instance_must_reference_a_closed_definition_from_the_same_builder() {
    let mut b = create();
    let mut chair = b
        .add_component_definition("Chair", &DefinitionOptions::default())
        .unwrap();
    chair
        .add_face(&triangle(), &FaceOptions::default())
        .unwrap();
    let handle = chair.handle();
    // Not yet closed.
    assert!(b.add_instance(handle, &InstanceOptions::default()).is_err());
    b.definition(handle).unwrap().close().unwrap();

    let mut other = create();
    assert!(other
        .add_instance(handle, &InstanceOptions::default())
        .is_err());
}

#[test]
fn definition_cannot_nest_an_instance_of_itself() {
    let mut b = create();
    let mut a = b
        .add_component_definition("A", &DefinitionOptions::default())
        .unwrap();
    a.add_face(&triangle(), &FaceOptions::default()).unwrap();
    let own = a.handle();
    assert!(a.add_instance(own, &InstanceOptions::default()).is_err());
}

#[test]
fn matrix_and_rotation_are_mutually_exclusive() {
    let mut b = create();
    let mut part = b
        .add_component_definition("Part", &DefinitionOptions::default())
        .unwrap();
    part.add_face(&triangle(), &FaceOptions::default()).unwrap();
    let part = part.close().unwrap();
    let options = InstanceOptions {
        matrix3x3: Some([1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]),
        rotation: Some(([0.0, 0.0, 1.0], 1.0)),
        ..Default::default()
    };
    assert!(b.add_instance(part, &options).is_err());
}

#[test]
fn hole_needs_at_least_3_points() {
    let mut b = create();
    let options = FaceOptions {
        holes: vec![vec![[1.0, 1.0, 0.0], [2.0, 2.0, 0.0]]],
        ..Default::default()
    };
    let outer = [
        [0.0, 0.0, 0.0],
        [10.0, 0.0, 0.0],
        [10.0, 10.0, 0.0],
        [0.0, 10.0, 0.0],
    ];
    assert!(b.add_face(&outer, &options).is_err());
}

#[test]
fn add_section_plane_rejects_a_zero_normal() {
    let mut b = create();
    b.add_face(&square(), &FaceOptions::default()).unwrap();
    assert!(b.add_section_plane([0.0; 3], [0.0; 3]).is_err());
}

#[test]
fn add_construction_line_requires_exactly_one_of_point2_or_direction() {
    let mut b = create();
    b.add_face(&square(), &FaceOptions::default()).unwrap();
    assert!(b
        .add_construction_line([0.0; 3], None, None, &GuideOptions::default())
        .is_err());
    assert!(b
        .add_construction_line(
            [0.0; 3],
            Some([1.0, 0.0, 0.0]),
            Some([0.0, 1.0, 0.0]),
            &GuideOptions::default()
        )
        .is_err());
}

#[test]
fn large_model_crossing_the_slot_boundary_builds() {
    // Unique (non-shared) vertices per triangle - the worst case a flattened CAD import produces,
    // landing the 0x7FFF crossing at a non-hand-picked slot. (The round-trip half of the C++ test
    // is in create_roundtrip_test.rs.)
    let mut b = create();
    for i in 0..5000 {
        let x = f64::from(i) * 10.0;
        b.add_face(
            &[[x, 0.0, 0.0], [x + 1.0, 0.0, 0.0], [x, 1.0, 0.0]],
            &FaceOptions::default(),
        )
        .unwrap();
    }
    assert!(b.debug_geometry_next_slot().unwrap() > 0x7FFF);
    let bytes = b.to_bytes().unwrap();
    // 5000 * (3 edges + 1 face) root entities, in the root-count field (no inserted sections).
    assert_eq!(u32_at(&bytes, 3511), 20_000);
}

// ---------------------------------------------------------------------------------------------
// Writer contract checks of our own.
// ---------------------------------------------------------------------------------------------

#[test]
fn an_empty_model_cannot_be_saved() {
    assert!(create().to_bytes().is_err());
    let mut b = create();
    b.add_material("Red", [255, 0, 0], None).unwrap();
    assert!(b.to_bytes().is_err());
}

#[test]
fn single_face_root_entity_count() {
    let mut b = create();
    b.add_face(&square(), &FaceOptions::default()).unwrap();
    let bytes = b.to_bytes().unwrap();
    assert_eq!(u32_at(&bytes, 3511), 5); // 4 edges + the face
    assert_eq!(&bytes[..3], &[0xFF, 0xFE, 0xFF]); // the scaffold header survives
}

#[test]
fn to_bytes_is_repeatable_and_save_writes_the_same_bytes() {
    let mut b = create();
    let mut chair = b
        .add_component_definition("Chair", &DefinitionOptions::default())
        .unwrap();
    chair.add_face(&square(), &FaceOptions::default()).unwrap();
    let chair = chair.close().unwrap();
    b.add_instance(chair, &InstanceOptions::default()).unwrap();
    let first = b.to_bytes().unwrap();
    assert_eq!(first, b.to_bytes().unwrap());

    let path = std::env::temp_dir().join(format!("openskp_create_save_{}.skp", std::process::id()));
    b.save(&path).unwrap();
    let saved = std::fs::read(&path).unwrap();
    std::fs::remove_file(&path).ok();
    assert_eq!(saved, first);
}

#[test]
fn random_guids_differ_between_builds_but_layout_does_not() {
    let build = || {
        let mut b = create();
        let mut chair = b
            .add_component_definition("Chair", &DefinitionOptions::default())
            .unwrap();
        chair.add_face(&square(), &FaceOptions::default()).unwrap();
        let chair = chair.close().unwrap();
        b.add_instance(chair, &InstanceOptions::default()).unwrap();
        b.to_bytes().unwrap()
    };
    let (a, b) = (build(), build());
    assert_eq!(a.len(), b.len());
    assert_ne!(a, b, "two files should not share definition/instance GUIDs");
}

#[test]
fn deterministic_ids_make_output_reproducible() {
    let build = || {
        let mut b = create();
        b.use_deterministic_ids(42);
        let mut group = b.add_group(&GroupOptions::default()).unwrap();
        group.add_face(&square(), &FaceOptions::default()).unwrap();
        group.close().unwrap();
        b.to_bytes().unwrap()
    };
    assert_eq!(build(), build());
}

#[test]
fn definition_lifecycle_errors() {
    let mut b = create();
    let mut empty = b
        .add_component_definition("Empty", &DefinitionOptions::default())
        .unwrap();
    assert!(
        empty.close().is_err(),
        "a definition with no geometry cannot close"
    );
    let empty_handle = empty.handle();
    // Still open: neither another definition nor root geometry may start.
    assert!(b
        .add_component_definition("Other", &DefinitionOptions::default())
        .is_err());
    assert!(b.add_face(&square(), &FaceOptions::default()).is_err());

    let mut empty = b.definition(empty_handle).unwrap();
    empty.add_face(&square(), &FaceOptions::default()).unwrap();
    empty.close().unwrap();
    assert!(empty.closed());
    assert!(empty.close().is_err(), "close() twice");
    assert!(
        empty.add_face(&square(), &FaceOptions::default()).is_err(),
        "add after close"
    );
    assert!(empty
        .add_construction_point([0.0; 3], &GuideOptions::default())
        .is_err());
    assert_eq!(empty.name(), "Empty");
}

#[test]
fn section_order_is_enforced() {
    let mut b = create();
    b.add_layer("L", &LayerOptions::default()).unwrap();
    assert!(b.add_material("M", [1, 2, 3], None).is_err());
    assert!(b.add_texture_material("T", PNG, None, None, None).is_err());

    let mut b = create();
    let mut d = b
        .add_component_definition("D", &DefinitionOptions::default())
        .unwrap();
    d.add_face(&square(), &FaceOptions::default()).unwrap();
    d.close().unwrap();
    assert!(b.add_layer("L", &LayerOptions::default()).is_err());
    assert!(b.add_material("M", [1, 2, 3], None).is_err());

    let mut b = create();
    b.add_face(&square(), &FaceOptions::default()).unwrap();
    assert!(b.add_layer("L", &LayerOptions::default()).is_err());
    assert!(b.add_group(&GroupOptions::default()).is_err());
}

#[test]
fn a_second_image_cannot_follow_geometry() {
    // add_image registers a texture material, which must precede all geometry - including the
    // first image's own placement (same as the C++ writer).
    let mut b = create();
    b.add_image(PNG, 10.0, 10.0, &ImageOptions::default())
        .unwrap();
    assert!(b
        .add_image(PNG, 10.0, 10.0, &ImageOptions::default())
        .is_err());
    assert!(b.to_bytes().is_ok());
}

#[test]
fn texture_files_must_exist_and_be_png_or_jpeg() {
    let mut b = create();
    assert!(b
        .add_texture_material(
            "Missing",
            "tests/fixtures/create_reference/missing.png",
            None,
            None,
            None
        )
        .is_err());
    let not_an_image = concat!(env!("CARGO_MANIFEST_DIR"), "/Cargo.toml");
    assert!(b
        .add_texture_material("Toml", not_an_image, None, None, None)
        .is_err());
    assert!(b.add_texture_material("Png", PNG, None, None, None).is_ok());
}

#[test]
fn curve_parameter_validation() {
    let mut b = create();
    let circle = |n| CircleOptions {
        num_segments: n,
        ..Default::default()
    };
    assert!(b
        .add_circle([0.0; 3], [0.0, 0.0, 1.0], 1.0, &circle(2))
        .is_err());
    assert!(b
        .add_circle([0.0; 3], [0.0, 0.0, 1.0], 1.0, &circle(256))
        .is_err());
    assert!(
        b.add_circle([0.0; 3], [0.0; 3], 1.0, &circle(24)).is_err(),
        "zero normal"
    );
    assert!(b
        .add_arc(
            [0.0; 3],
            [0.0, 0.0, 1.0],
            1.0,
            1.0,
            1.0,
            &ArcOptions::default()
        )
        .is_err());
    assert!(b
        .add_arc(
            [0.0; 3],
            [0.0, 0.0, 1.0],
            1.0,
            0.0,
            PI,
            &ArcOptions {
                num_segments: 300,
                ..Default::default()
            }
        )
        .is_err());
    assert!(b
        .add_polyline(&[[0.0; 3]], &PolylineOptions::default())
        .is_err());
    assert!(
        b.add_dimension([1.0; 3], [1.0; 3], 10.0).is_err(),
        "coincident endpoints"
    );
}

#[test]
fn texture_positioning_validation() {
    let mut b = create();
    let collinear = FaceOptions {
        front_uv: Some(vec![
            ([0.0, 0.0, 0.0], [0.0, 0.0]),
            ([1.0, 0.0, 0.0], [1.0, 0.0]),
            ([2.0, 0.0, 0.0], [2.0, 0.0]),
        ]),
        ..Default::default()
    };
    assert!(b.add_face(&square(), &collinear).is_err());
    let two_pins = FaceOptions {
        front_uv: Some(vec![
            ([0.0, 0.0, 0.0], [0.0, 0.0]),
            ([1.0, 0.0, 0.0], [1.0, 0.0]),
        ]),
        ..Default::default()
    };
    assert!(b.add_face(&square(), &two_pins).is_err());
    let triangulated_and_pinned = FaceOptions {
        auto_triangulate: true,
        front_uv: Some(vec![
            ([0.0, 0.0, 0.0], [0.0, 0.0]),
            ([1.0, 0.0, 0.0], [1.0, 0.0]),
            ([0.0, 1.0, 0.0], [0.0, 1.0]),
        ]),
        ..Default::default()
    };
    let warped = [
        [0.0, 0.0, 0.0],
        [10.0, 0.0, 0.0],
        [10.0, 10.0, 5.0],
        [0.0, 10.0, 0.0],
    ];
    assert!(b.add_face(&warped, &triangulated_and_pinned).is_err());
}

#[test]
fn strings_over_254_utf16_units_are_rejected() {
    let mut b = create();
    assert!(b.add_material(&"x".repeat(255), [1, 2, 3], None).is_err());
    let mut b = create();
    assert!(b.add_material(&"x".repeat(254), [1, 2, 3], None).is_ok());
    // An astral character is 2 UTF-16 code units.
    let mut b = create();
    assert!(b
        .add_material(&"\u{1F600}".repeat(128), [1, 2, 3], None)
        .is_err());
}

#[test]
fn foreign_definition_handles_are_rejected() {
    let mut a = create();
    let mut d = a
        .add_component_definition("D", &DefinitionOptions::default())
        .unwrap();
    d.add_face(&square(), &FaceOptions::default()).unwrap();
    let d = d.close().unwrap();

    let mut b = create();
    assert!(b.definition(d).is_err());
    let mut host = b
        .add_component_definition("Host", &DefinitionOptions::default())
        .unwrap();
    host.add_face(&square(), &FaceOptions::default()).unwrap();
    assert!(host.add_instance(d, &InstanceOptions::default()).is_err());
}

#[test]
fn attribute_values_convert_from_plain_types() {
    assert_eq!(
        AttributeValue::from("a"),
        AttributeValue::String("a".into())
    );
    assert_eq!(AttributeValue::from(3), AttributeValue::Int32(3));
    assert_eq!(AttributeValue::from(2.5), AttributeValue::Double(2.5));
    assert_eq!(AttributeValue::from(true), AttributeValue::Bool(true));
}

#[test]
fn builder_default_matches_create() {
    let mut b = SkpBuilder::default();
    b.add_face(&square(), &FaceOptions::default()).unwrap();
    let mut c = create();
    c.add_face(&square(), &FaceOptions::default()).unwrap();
    assert_eq!(b.to_bytes().unwrap(), c.to_bytes().unwrap());
}
