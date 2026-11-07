//! Reader-dependent half of the C++ `create_test.cpp`: build a file with the writer, parse it back
//! with the crate's own reader and assert on the model. Ported assertion-for-assertion.
//!
//! The writer only ever emits the legacy (v17 MFC-archive) format, so every test here parses a
//! mesh-index one also needs the scene builder). Run them with `cargo test -- --ignored` once it
//! is.

use std::collections::BTreeMap;
use std::f64::consts::PI;

use openskp::create::{
    create, ArcOptions, AttributeDict, AttributeValue, CircleOptions, DefinitionOptions,
    FaceOptions, GroupInstanceOptions, GroupOptions, GuideOptions, ImageOptions, InstanceOptions,
    LayerOptions, Point3, PolylineOptions, SkpBuilder, UvCorrespondence, DEFAULT_DIMENSION_OFFSET,
    DEFAULT_TEXT_LEADER,
};
use openskp::{AttributeKind, ParseOptions, SkpFile, SkpModel};

const PNG: &str = "tests/fixtures/create_reference/fake.png";

fn round_trip(builder: &mut SkpBuilder) -> SkpModel {
    let bytes = builder.to_bytes().expect("to_bytes");
    SkpFile::from_buffer(bytes)
        .parse(&ParseOptions::default())
        .expect("parse")
}

/// Image bytes the writer embeds verbatim: a PNG signature plus filler (the fixture file).
fn fake_png_bytes() -> Vec<u8> {
    let mut data = vec![0x89, b'P', b'N', b'G', b'\r', b'\n', 0x1a, b'\n'];
    data.extend(std::iter::repeat_n(0x42, 64));
    data
}

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

fn attrs(entries: &[(&str, AttributeValue)]) -> AttributeDict {
    entries
        .iter()
        .map(|(k, v)| ((*k).to_owned(), v.clone()))
        .collect()
}

fn pins(points: [(Point3, [f64; 2]); 3]) -> Option<UvCorrespondence> {
    Some(points.to_vec())
}

fn assert_near(actual: f64, expected: f64, tolerance: f64) {
    assert!(
        (actual - expected).abs() <= tolerance,
        "{actual} != {expected} (+/- {tolerance})"
    );
}

fn expect_matrix(m: &[f64; 9], expected: [f64; 9], tolerance: f64) {
    for i in 0..9 {
        assert!(
            (m[i] - expected[i]).abs() <= tolerance,
            "m[{i}] = {} != {}",
            m[i],
            expected[i]
        );
    }
}

fn simple_definition(
    b: &mut SkpBuilder,
    name: &str,
    points: &[Point3],
) -> openskp::create::DefinitionHandle {
    let mut d = b
        .add_component_definition(name, &DefinitionOptions::default())
        .unwrap();
    d.add_face(points, &FaceOptions::default()).unwrap();
    d.close().unwrap()
}

// ---------------------------------------------------------------------------------------------
// Single face / basic geometry.
// ---------------------------------------------------------------------------------------------

#[test]
fn single_face_round_trips() {
    let mut b = create();
    b.add_face(&square(), &FaceOptions::default()).unwrap();
    let model = round_trip(&mut b);
    let root = model.root();
    assert_eq!(root.faces.len(), 1);
    assert_eq!(root.vertices.len(), 4);
    assert_eq!(root.edges.len(), 4);
    for p in square() {
        assert!(
            root.vertices
                .values()
                .any(|v| v.x == p[0] && v.y == p[1] && v.z == p[2]),
            "expected vertex {p:?} not found"
        );
    }
}

#[test]
fn shared_vertices_and_edges_are_deduplicated() {
    let mut b = create();
    b.add_face(
        &[[0.0, 0.0, 0.0], [10.0, 0.0, 0.0], [10.0, 10.0, 0.0]],
        &FaceOptions::default(),
    )
    .unwrap();
    b.add_face(
        &[[0.0, 0.0, 0.0], [10.0, 10.0, 0.0], [0.0, 10.0, 0.0]],
        &FaceOptions::default(),
    )
    .unwrap();
    let model = round_trip(&mut b);
    assert_eq!(model.root().faces.len(), 2);
    assert_eq!(model.root().vertices.len(), 4); // not 6
    assert_eq!(model.root().edges.len(), 5); // not 6
}

// ---------------------------------------------------------------------------------------------
// Materials.
// ---------------------------------------------------------------------------------------------

#[test]
fn solid_material_round_trips() {
    let mut b = create();
    let red = b.add_material("Red", [255, 0, 0], None).unwrap();
    let small = [
        [0.0, 0.0, 0.0],
        [10.0, 0.0, 0.0],
        [10.0, 10.0, 0.0],
        [0.0, 10.0, 0.0],
    ];
    b.add_face(
        &small,
        &FaceOptions {
            material: Some(red),
            ..Default::default()
        },
    )
    .unwrap();

    let model = round_trip(&mut b);
    assert_eq!(model.materials.len(), 1);
    assert_eq!(model.materials[0].name, "Red");
    assert_eq!(model.materials[0].color[..3], [255, 0, 0]);

    assert_eq!(model.root().faces.len(), 1);
    let face = model.root().faces.values().next().unwrap();
    let material_id = face.material_id.expect("face material");
    let mat = model.material_by_id(material_id).expect("material by id");
    assert_eq!(mat.name, "Red");
}

fn textured_model(
    applied_height: Option<f64>,
    applied_width: Option<f64>,
    opacity: Option<f64>,
) -> SkpModel {
    let mut b = create();
    let brick = b
        .add_texture_material("Brick", PNG, applied_height, applied_width, opacity)
        .unwrap();
    b.add_face(
        &square(),
        &FaceOptions {
            material: Some(brick),
            ..Default::default()
        },
    )
    .unwrap();
    round_trip(&mut b)
}

#[test]
fn textured_material_round_trips() {
    let model = textured_model(None, None, None);
    assert_eq!(model.materials.len(), 1);
    let mat = &model.materials[0];
    assert_eq!(mat.name, "Brick");
    let texture = mat.texture.as_ref().expect("texture");
    assert_eq!(texture.data.as_deref(), Some(&fake_png_bytes()[..]));
}

#[test]
fn textured_material_default_applied_height_is_one_not_corrupted() {
    // Regression: an omitted applied_height once wrote a garbage sentinel (~1.29e-231) that real
    // SketchUp renders as a streaky, vertically-smeared texture.
    let model = textured_model(None, None, None);
    let texture = model.materials[0].texture.as_ref().expect("texture");
    assert_eq!(texture.width, 1.0);
    assert_eq!(texture.height, 1.0);
}

#[test]
fn textured_material_explicit_applied_height_still_overridable() {
    let model = textured_model(Some(48.0), None, None);
    assert_eq!(
        model.materials[0].texture.as_ref().expect("texture").height,
        48.0
    );
}

#[test]
fn textured_material_applied_size_fully_overridable() {
    // Real SketchUp writes the tile size in both axes (SketchUp Web: 8.0 x 16.0 for a brick);
    // without positioning this pair IS the mapping (openskp#252).
    let model = textured_model(Some(16.0), Some(8.0), None);
    let texture = model.materials[0].texture.as_ref().expect("texture");
    assert_eq!(texture.width, 8.0);
    assert_eq!(texture.height, 16.0);
}

#[test]
fn textured_material_carries_opacity() {
    let model = textured_model(None, None, Some(0.5));
    assert_near(model.materials[0].transparency, 0.5, 1e-9);
}

#[test]
fn solid_material_carries_opacity() {
    let mut b = create();
    let glass = b
        .add_material("Glass", [200, 220, 255, 255], Some(0.35))
        .unwrap();
    b.add_face(
        &square(),
        &FaceOptions {
            material: Some(glass),
            ..Default::default()
        },
    )
    .unwrap();
    let model = round_trip(&mut b);
    assert_eq!(model.materials.len(), 1);
    assert_near(model.materials[0].transparency, 0.35, 1e-9);
}

#[test]
fn omitted_opacity_stays_fully_opaque() {
    let mut b = create();
    let red = b.add_material("Red", [255, 0, 0], None).unwrap();
    b.add_face(
        &square(),
        &FaceOptions {
            material: Some(red),
            ..Default::default()
        },
    )
    .unwrap();
    let model = round_trip(&mut b);
    assert_eq!(model.materials.len(), 1);
    assert_eq!(model.materials[0].transparency, 1.0);
}

#[test]
fn add_image_places_a_real_image_entity_not_a_plain_textured_face() {
    let mut b = create();
    let options = ImageOptions {
        translation: [0.0, 0.0, 40.0],
        rotation: Some(([1.0, 0.0, 0.0], PI / 2.0)),
        ..Default::default()
    };
    b.add_image(PNG, 48.0, 36.0, &options).unwrap();
    let model = round_trip(&mut b);

    let image_defs: Vec<_> = model
        .definitions
        .iter()
        .filter(|(_, d)| d.is_image)
        .collect();
    assert_eq!(image_defs.len(), 1);
    let (image_def_id, image_def) = image_defs[0];
    assert_eq!(image_def.faces.len(), 1);
    assert_eq!(model.root().instances.len(), 1);
    assert_eq!(model.root().instances[0].ref_idx, Some(*image_def_id));
}

// ---------------------------------------------------------------------------------------------
// Layers.
// ---------------------------------------------------------------------------------------------

#[test]
fn layer_round_trips() {
    let mut b = create();
    let roof = b
        .add_layer(
            "Roof",
            &LayerOptions {
                color: Some([10, 20, 30, 255]),
                hidden: true,
                ..Default::default()
            },
        )
        .unwrap();
    b.add_face(
        &triangle(),
        &FaceOptions {
            layer: Some(roof),
            ..Default::default()
        },
    )
    .unwrap();
    let model = round_trip(&mut b);
    // Layer0 (from the scaffold) + "Roof".
    assert_eq!(model.layers.len(), 2);
    let roof_layer = model
        .layers
        .iter()
        .find(|l| l.name == "Roof")
        .expect("Roof layer");
    assert!(roof_layer.hidden);
    assert_eq!(roof_layer.color[..3], [10, 20, 30]);
}

// ---------------------------------------------------------------------------------------------
// Component definitions + instances.
// ---------------------------------------------------------------------------------------------

#[test]
fn component_definition_and_multiple_instances() {
    let mut b = create();
    let chair = simple_definition(
        &mut b,
        "Chair",
        &[
            [0.0, 0.0, 0.0],
            [20.0, 0.0, 0.0],
            [20.0, 20.0, 0.0],
            [0.0, 20.0, 0.0],
        ],
    );
    b.add_instance(
        chair,
        &InstanceOptions {
            translation: [100.0, 0.0, 0.0],
            ..Default::default()
        },
    )
    .unwrap();
    let second = InstanceOptions {
        translation: [200.0, 0.0, 0.0],
        name: Some("Second Chair".into()),
        ..Default::default()
    };
    b.add_instance(chair, &second).unwrap();

    let model = round_trip(&mut b);
    assert_eq!(model.definitions.len(), 1);
    let def = model.definitions.values().next().unwrap();
    assert_eq!(def.name, "Chair");
    assert_eq!(def.faces.len(), 1);

    assert_eq!(model.root().instances.len(), 2);
    let (mut found_translation_100, mut found_named) = (false, false);
    for inst in &model.root().instances {
        assert!(inst.matrix.len() >= 12);
        found_translation_100 |= (inst.matrix[9] - 100.0).abs() < 1e-9;
        found_named |= inst.name == "Second Chair";
    }
    assert!(found_translation_100);
    assert!(found_named);
}

// ---------------------------------------------------------------------------------------------
// Groups + nesting.
// ---------------------------------------------------------------------------------------------

#[test]
fn group_self_places() {
    let mut b = create();
    let mut table = b
        .add_group(&GroupOptions {
            name: Some("Table".into()),
            translation: [50.0, 0.0, 0.0],
            ..Default::default()
        })
        .unwrap();
    table
        .add_face(
            &[
                [0.0, 0.0, 0.0],
                [30.0, 0.0, 0.0],
                [30.0, 30.0, 0.0],
                [0.0, 30.0, 0.0],
            ],
            &FaceOptions::default(),
        )
        .unwrap();
    table.close().unwrap();

    let model = round_trip(&mut b);
    assert_eq!(model.definitions.len(), 1);
    assert_eq!(model.root().instances.len(), 1);
    let inst = &model.root().instances[0];
    assert_eq!(inst.name, "Table");
    assert!(inst.matrix.len() >= 12);
    assert_near(inst.matrix[9], 50.0, 1e-9);
}

#[test]
fn a_group_is_written_as_a_group_and_a_component_as_a_component() {
    // SketchUp 2025's own library reports one group and one component definition here (#446).
    let mut b = create();
    let mut table = b
        .add_group(&GroupOptions {
            name: Some("Table".into()),
            ..Default::default()
        })
        .unwrap();
    table.add_face(&square(), &FaceOptions::default()).unwrap();
    table.close().unwrap();
    let chair = simple_definition(&mut b, "Chair", &square());
    b.add_instance(chair, &InstanceOptions::default()).unwrap();

    let model = round_trip(&mut b);
    let mut groups = 0;
    for d in model.definitions.values() {
        if d.is_group {
            groups += 1;
        }
        if d.name == "Chair" {
            assert!(!d.is_group);
        }
    }
    assert_eq!(groups, 1);
}

#[test]
fn a_group_placed_inside_a_component_is_a_group() {
    // SketchUp 2025 reports the placed definition as a group and the enclosing one as a
    // component (#467).
    let mut b = create();
    let engine = simple_definition(&mut b, "Engine", &square());
    let mut car = b
        .add_component_definition("Car", &DefinitionOptions::default())
        .unwrap();
    car.add_face(&square(), &FaceOptions::default()).unwrap();
    car.add_group_instance(
        engine,
        &GroupInstanceOptions {
            translation: [50.0, 0.0, 10.0],
            ..Default::default()
        },
    )
    .unwrap();
    let car = car.close().unwrap();
    b.add_instance(car, &InstanceOptions::default()).unwrap();

    let model = round_trip(&mut b);
    for d in model.definitions.values() {
        if d.name == "Engine" {
            assert!(d.is_group);
        }
        if d.name == "Car" {
            assert!(!d.is_group);
        }
    }
}

#[test]
fn group_attributes_round_trip() {
    // Groups used to hardcode a null attribute pointer; real SketchUp 2020 files carry a real
    // CAttributeContainer on attributed groups (#261). Smoke test, like the C++ one.
    let mut b = create();
    let options = GroupOptions {
        name: Some("Table".into()),
        attributes: attrs(&[("sku", "TBL-1".into()), ("qty", 2.into())]),
        ..Default::default()
    };
    let mut table = b.add_group(&options).unwrap();
    table
        .add_face(
            &[
                [0.0, 0.0, 0.0],
                [30.0, 0.0, 0.0],
                [30.0, 30.0, 0.0],
                [0.0, 30.0, 0.0],
            ],
            &FaceOptions::default(),
        )
        .unwrap();
    table.close().unwrap();
    let model = round_trip(&mut b);
    assert_eq!(model.root().instances.len(), 1);
}

#[test]
fn nested_definition_instance_and_nested_group_instance() {
    let mut b = create();
    let wheel = simple_definition(
        &mut b,
        "Wheel",
        &[
            [0.0, 0.0, 0.0],
            [10.0, 0.0, 0.0],
            [10.0, 10.0, 0.0],
            [0.0, 10.0, 0.0],
        ],
    );
    let engine = simple_definition(
        &mut b,
        "Engine",
        &[
            [0.0, 0.0, 0.0],
            [30.0, 0.0, 0.0],
            [30.0, 30.0, 0.0],
            [0.0, 30.0, 0.0],
        ],
    );
    let mut car = b
        .add_component_definition("Car", &DefinitionOptions::default())
        .unwrap();
    car.add_face(
        &[
            [0.0, 0.0, 0.0],
            [150.0, 0.0, 0.0],
            [150.0, 60.0, 0.0],
            [0.0, 60.0, 0.0],
        ],
        &FaceOptions::default(),
    )
    .unwrap();
    car.add_instance(wheel, &InstanceOptions::default())
        .unwrap();
    car.add_group_instance(
        engine,
        &GroupInstanceOptions {
            translation: [50.0, 0.0, 10.0],
            ..Default::default()
        },
    )
    .unwrap();
    let car = car.close().unwrap();
    b.add_instance(car, &InstanceOptions::default()).unwrap();

    let model = round_trip(&mut b);
    assert_eq!(model.definitions.len(), 3);
    let car_def = model
        .definitions
        .values()
        .find(|d| d.name == "Car")
        .expect("Car");
    assert_eq!(car_def.faces.len(), 1);
    assert_eq!(car_def.instances.len(), 2); // the wheel instance + the engine group instance
}

#[test]
fn nested_group_instance_carries_its_own_attributes_too() {
    let mut b = create();
    let engine = simple_definition(
        &mut b,
        "Engine",
        &[
            [0.0, 0.0, 0.0],
            [30.0, 0.0, 0.0],
            [30.0, 30.0, 0.0],
            [0.0, 30.0, 0.0],
        ],
    );
    let mut car = b
        .add_component_definition("Car", &DefinitionOptions::default())
        .unwrap();
    car.add_face(
        &[
            [0.0, 0.0, 0.0],
            [150.0, 0.0, 0.0],
            [150.0, 60.0, 0.0],
            [0.0, 60.0, 0.0],
        ],
        &FaceOptions::default(),
    )
    .unwrap();
    let options = GroupInstanceOptions {
        translation: [50.0, 0.0, 10.0],
        attributes: attrs(&[("part", "V6".into())]),
        ..Default::default()
    };
    car.add_group_instance(engine, &options).unwrap();
    let car = car.close().unwrap();
    b.add_instance(car, &InstanceOptions::default()).unwrap();

    let model = round_trip(&mut b);
    let car_def = model
        .definitions
        .values()
        .find(|d| d.name == "Car")
        .expect("Car");
    assert_eq!(car_def.instances.len(), 1);
}

#[test]
fn each_nested_level_keeps_its_own_instance_name_in_mesh_index() {
    // Parity check for openskp#240: a shallow instance's name must not overwrite the names of
    // the meshes beneath it in scene.mesh_index.
    let mut b = create();
    let leaf = simple_definition(
        &mut b,
        "Leaf",
        &[
            [0.0, 0.0, 0.0],
            [1.0, 0.0, 0.0],
            [1.0, 1.0, 0.0],
            [0.0, 1.0, 0.0],
        ],
    );
    let mut middle = b
        .add_component_definition("Middle", &DefinitionOptions::default())
        .unwrap();
    middle
        .add_face(
            &[
                [0.0, 0.0, 10.0],
                [1.0, 0.0, 10.0],
                [1.0, 1.0, 10.0],
                [0.0, 1.0, 10.0],
            ],
            &FaceOptions::default(),
        )
        .unwrap();
    middle
        .add_instance(
            leaf,
            &InstanceOptions {
                name: Some("LeafInstance".into()),
                ..Default::default()
            },
        )
        .unwrap();
    let middle = middle.close().unwrap();
    let mut outer = b
        .add_component_definition("Outer", &DefinitionOptions::default())
        .unwrap();
    outer
        .add_face(
            &[
                [0.0, 0.0, 20.0],
                [1.0, 0.0, 20.0],
                [1.0, 1.0, 20.0],
                [0.0, 1.0, 20.0],
            ],
            &FaceOptions::default(),
        )
        .unwrap();
    outer
        .add_instance(
            middle,
            &InstanceOptions {
                name: Some("MiddleInstance".into()),
                ..Default::default()
            },
        )
        .unwrap();
    let outer = outer.close().unwrap();
    b.add_instance(
        outer,
        &InstanceOptions {
            name: Some("OuterInstance".into()),
            ..Default::default()
        },
    )
    .unwrap();

    let scene = SkpFile::from_buffer(b.to_bytes().unwrap())
        .build_scene(&ParseOptions::default())
        .expect("scene");
    let mut names: Vec<String> = scene
        .mesh_index
        .values()
        .map(|meta| meta.name.clone())
        .collect();
    names.sort();
    assert_eq!(names, ["LeafInstance", "MiddleInstance", "OuterInstance"]);
}

// ---------------------------------------------------------------------------------------------
// Rotation + hidden.
// ---------------------------------------------------------------------------------------------

#[test]
fn rotation_convenience_produces_a_rotation_matrix() {
    let mut b = create();
    let part = simple_definition(
        &mut b,
        "Part",
        &[
            [0.0, 0.0, 0.0],
            [10.0, 0.0, 0.0],
            [10.0, 10.0, 0.0],
            [0.0, 10.0, 0.0],
        ],
    );
    b.add_instance(
        part,
        &InstanceOptions {
            rotation: Some(([0.0, 0.0, 1.0], PI / 2.0)),
            ..Default::default()
        },
    )
    .unwrap();
    let model = round_trip(&mut b);
    assert_eq!(model.root().instances.len(), 1);
    let m = &model.root().instances[0].matrix;
    assert!(m.len() >= 9);
    // Row-major +90 degrees around Z: (1,0,0) -> (0,1,0), i.e. m[0] = cos = 0, m[1] = -sin = -1.
    assert_near(m[0], 0.0, 1e-9);
    assert_near(m[1], -1.0, 1e-9);
    assert_near(m[3], 1.0, 1e-9);
    assert_near(m[4], 0.0, 1e-9);
}

#[test]
fn hidden_face_and_instance_round_trip() {
    let mut b = create();
    let part = simple_definition(&mut b, "Part", &triangle());
    b.add_instance(
        part,
        &InstanceOptions {
            hidden: true,
            ..Default::default()
        },
    )
    .unwrap();
    b.add_face(
        &triangle(),
        &FaceOptions {
            hidden: true,
            ..Default::default()
        },
    )
    .unwrap();

    let model = round_trip(&mut b);
    assert!(model.root().faces.values().any(|f| f.hidden));
    assert_eq!(model.root().instances.len(), 1);
    assert!(model.root().instances[0].hidden);
}

// ---------------------------------------------------------------------------------------------
// Holes + auto_triangulate.
// ---------------------------------------------------------------------------------------------

#[test]
fn face_with_hole_has_two_loops() {
    let mut b = create();
    let wall = [
        [0.0, 0.0, 0.0],
        [200.0, 0.0, 0.0],
        [200.0, 100.0, 0.0],
        [0.0, 100.0, 0.0],
    ];
    let window = vec![
        [80.0, 30.0, 0.0],
        [120.0, 30.0, 0.0],
        [120.0, 70.0, 0.0],
        [80.0, 70.0, 0.0],
    ];
    b.add_face(
        &wall,
        &FaceOptions {
            holes: vec![window],
            ..Default::default()
        },
    )
    .unwrap();

    let model = round_trip(&mut b);
    assert_eq!(model.root().faces.len(), 1);
    let face = model.root().faces.values().next().unwrap();
    assert_eq!(face.loops.len(), 2);
    assert_eq!(face.loops[0].len(), 4);
    assert_eq!(face.loops[1].len(), 4);
    // 4 outer + 4 hole vertices/edges, all distinct (the hole does not touch the boundary).
    assert_eq!(model.root().vertices.len(), 8);
    assert_eq!(model.root().edges.len(), 8);
}

#[test]
fn auto_triangulate_splits_a_non_planar_quad_into_two_faces() {
    let mut b = create();
    let warped = [
        [0.0, 0.0, 0.0],
        [10.0, 0.0, 0.0],
        [10.0, 10.0, 5.0],
        [0.0, 10.0, 0.0],
    ];
    b.add_face(
        &warped,
        &FaceOptions {
            auto_triangulate: true,
            ..Default::default()
        },
    )
    .unwrap();
    assert_eq!(round_trip(&mut b).root().faces.len(), 2);
}

#[test]
fn auto_triangulate_leaves_an_already_planar_quad_as_one_face() {
    let mut b = create();
    let flat = [
        [0.0, 0.0, 0.0],
        [10.0, 0.0, 0.0],
        [10.0, 10.0, 0.0],
        [0.0, 10.0, 0.0],
    ];
    b.add_face(
        &flat,
        &FaceOptions {
            auto_triangulate: true,
            ..Default::default()
        },
    )
    .unwrap();
    assert_eq!(round_trip(&mut b).root().faces.len(), 1);
}

// ---------------------------------------------------------------------------------------------
// Circles, arcs, polylines.
// ---------------------------------------------------------------------------------------------

#[test]
fn circle_produces_a_face_with_n_segment_vertices_and_edges() {
    let mut b = create();
    b.add_circle(
        [50.0, 50.0, 0.0],
        [0.0, 0.0, 1.0],
        40.0,
        &CircleOptions {
            num_segments: 16,
            ..Default::default()
        },
    )
    .unwrap();
    let model = round_trip(&mut b);
    assert_eq!(model.root().faces.len(), 1);
    assert_eq!(model.root().vertices.len(), 16);
    assert_eq!(model.root().edges.len(), 16);
}

#[test]
fn arc_produces_edges_but_no_face() {
    let mut b = create();
    b.add_arc(
        [50.0, 50.0, 0.0],
        [0.0, 0.0, 1.0],
        40.0,
        0.0,
        PI / 2.0,
        &ArcOptions {
            num_segments: 8,
            ..Default::default()
        },
    )
    .unwrap();
    let model = round_trip(&mut b);
    assert_eq!(model.root().faces.len(), 0);
    assert_eq!(model.root().vertices.len(), 9); // num_segments + 1 endpoints
    assert_eq!(model.root().edges.len(), 8);
}

#[test]
fn polyline_produces_edges_but_no_face() {
    let mut b = create();
    b.add_polyline(
        &[
            [0.0, 0.0, 0.0],
            [10.0, 10.0, 0.0],
            [20.0, 0.0, 0.0],
            [30.0, 10.0, 0.0],
        ],
        &PolylineOptions::default(),
    )
    .unwrap();
    let model = round_trip(&mut b);
    assert_eq!(model.root().faces.len(), 0);
    assert_eq!(model.root().vertices.len(), 4);
    assert_eq!(model.root().edges.len(), 3);
}

#[test]
fn closed_polyline_connects_last_point_to_first() {
    let mut b = create();
    b.add_polyline(
        &[
            [0.0, 0.0, 0.0],
            [10.0, 0.0, 0.0],
            [10.0, 10.0, 0.0],
            [0.0, 10.0, 0.0],
        ],
        &PolylineOptions {
            closed: true,
            ..Default::default()
        },
    )
    .unwrap();
    let model = round_trip(&mut b);
    assert_eq!(model.root().vertices.len(), 4);
    assert_eq!(model.root().edges.len(), 4);
}

// ---------------------------------------------------------------------------------------------
// Attributes.
// ---------------------------------------------------------------------------------------------

#[test]
fn instance_attributes_round_trip() {
    // Custom attributes are CAttributeNamed dictionaries; the model keeps their types, while
    // `properties` (the stringified dynamic-attributes view) stays empty.
    let mut b = create();
    let part = simple_definition(&mut b, "Part", &triangle());
    let options = InstanceOptions {
        attributes: attrs(&[
            ("count", 42.into()),
            ("label", "widget".into()),
            ("weight", 3.5.into()),
        ]),
        ..Default::default()
    };
    b.add_instance(part, &options).unwrap();

    let model = round_trip(&mut b);
    assert_eq!(model.root().instances.len(), 1);
    let inst = &model.root().instances[0];
    assert!(inst.properties.is_empty());
    let attrs = &inst.attribute_dictionaries["attributes"];
    assert_eq!(attrs["count"].kind(), AttributeKind::Integer);
    assert_eq!(attrs["count"].integer(), 42);
    assert_eq!(attrs["label"].to_string(), "widget");
    assert_eq!(attrs["weight"].kind(), AttributeKind::Float);
    assert_eq!(attrs["weight"].number(), 3.5);
}

#[test]
fn instance_boolean_and_length_attributes_keep_their_types() {
    let mut b = create();
    let part = simple_definition(&mut b, "Part", &triangle());
    let options = InstanceOptions {
        attributes: attrs(&[
            ("visible", true.into()),
            ("hidden", false.into()),
            ("depth", AttributeValue::Length(15.5)),
            ("weight", 3.5.into()),
        ]),
        ..Default::default()
    };
    b.add_instance(part, &options).unwrap();

    let model = round_trip(&mut b);
    assert_eq!(model.root().instances.len(), 1);
    let attrs = &model.root().instances[0].attribute_dictionaries["attributes"];
    assert_eq!(attrs["visible"].kind(), AttributeKind::Boolean);
    assert_eq!(attrs["visible"].integer(), 1);
    assert_eq!(attrs["hidden"].kind(), AttributeKind::Boolean);
    assert_eq!(attrs["hidden"].integer(), 0);
    assert_eq!(attrs["depth"].kind(), AttributeKind::Length);
    assert_eq!(attrs["depth"].number(), 15.5);
    assert_eq!(attrs["weight"].kind(), AttributeKind::Float);
    // Stringified views keep the integer / float text they always had.
    assert_eq!(attrs["visible"].to_string(), "1");
    assert_eq!(attrs["depth"].to_string(), "15.5");
}

#[test]
fn layer_extra_dictionaries_round_trip() {
    let mut b = create();
    let options = LayerOptions {
        extra_dictionaries: BTreeMap::from([("plugin".to_owned(), attrs(&[("n", 7.into())]))]),
        ..Default::default()
    };
    b.add_layer("Walls", &options).unwrap();
    b.add_face(
        &[
            [0.0, 0.0, 0.0],
            [10.0, 0.0, 0.0],
            [10.0, 10.0, 0.0],
            [0.0, 10.0, 0.0],
        ],
        &FaceOptions::default(),
    )
    .unwrap();

    let model = round_trip(&mut b);
    let walls = model
        .layers
        .iter()
        .find(|l| l.name == "Walls")
        .expect("Walls layer");
    let plugin = walls
        .attribute_dictionaries
        .get("plugin")
        .expect("plugin dictionary");
    assert_eq!(plugin["n"].to_string(), "7");
}

#[test]
fn face_and_definition_attributes_do_not_throw() {
    // The reader's model doesn't expose face/definition attributes; smoke test that writing them
    // still produces a file the reader parses.
    let mut b = create();
    let definition_options = DefinitionOptions {
        attributes: attrs(&[("material_code", "ABC-123".into())]),
        ..Default::default()
    };
    let mut part = b
        .add_component_definition("Part", &definition_options)
        .unwrap();
    part.add_face(
        &triangle(),
        &FaceOptions {
            attributes: attrs(&[("area_sqft", 12.5.into())]),
            ..Default::default()
        },
    )
    .unwrap();
    let part = part.close().unwrap();
    b.add_instance(part, &InstanceOptions::default()).unwrap();
    round_trip(&mut b);
}

// ---------------------------------------------------------------------------------------------
// Explicit texture positioning (front_uv/back_uv).
// ---------------------------------------------------------------------------------------------

fn positioned_face_uv(
    applied: Option<(f64, f64)>,
    points: &[Point3],
    uv: Option<UvCorrespondence>,
) -> ([f64; 9], SkpModel) {
    let mut b = create();
    let (height, width) = applied.map_or((None, None), |(h, w)| (Some(h), Some(w)));
    let tex = b
        .add_texture_material("Brick", PNG, height, width, None)
        .unwrap();
    b.add_face(
        points,
        &FaceOptions {
            material: Some(tex),
            front_uv: uv,
            ..Default::default()
        },
    )
    .unwrap();
    let model = round_trip(&mut b);
    assert_eq!(model.root().faces.len(), 1);
    let transform = model
        .root()
        .faces
        .values()
        .next()
        .unwrap()
        .uv_transform
        .expect("uv transform");
    (transform, model)
}

#[test]
fn explicit_front_uv_round_trips() {
    let mut b = create();
    let brick = b.add_material("Brick", [150, 100, 50], None).unwrap();
    let options = FaceOptions {
        material: Some(brick),
        front_uv: pins([
            ([0.0, 0.0, 0.0], [0.0, 0.0]),
            ([50.0, 0.0, 0.0], [1.0, 0.0]),
            ([0.0, 50.0, 0.0], [0.0, 1.0]),
        ]),
        ..Default::default()
    };
    b.add_face(&square(), &options).unwrap();
    let model = round_trip(&mut b);
    assert_eq!(model.root().faces.len(), 1);
    assert!(model
        .root()
        .faces
        .values()
        .next()
        .unwrap()
        .uv_transform
        .is_some());
}

#[test]
fn vertex_order_does_not_turn_the_mapping() {
    // The square listed from another corner (first edge runs +Y), pinned to u = x/50, v = y/50:
    // SketchUp's basis depends on the normal alone, so the matrix stays diag(50, 50).
    let (m, _) = positioned_face_uv(
        None,
        &[
            [100.0, 0.0, 0.0],
            [100.0, 100.0, 0.0],
            [0.0, 100.0, 0.0],
            [0.0, 0.0, 0.0],
        ],
        pins([
            ([0.0, 0.0, 0.0], [0.0, 0.0]),
            ([50.0, 0.0, 0.0], [1.0, 0.0]),
            ([0.0, 50.0, 0.0], [0.0, 1.0]),
        ]),
    );
    expect_matrix(&m, [50.0, 0.0, 0.0, 0.0, 50.0, 0.0, 0.0, 0.0, 1.0], 1e-9);
}

#[test]
fn downward_face_uses_minus_x_plus_y() {
    // Clockwise XY loop -> normal -Z; in SketchUp's downward (-X, +Y) basis the pins store
    // diag(-2, 2).
    let (m, model) = positioned_face_uv(
        None,
        &[
            [0.0, 2.0, 0.0],
            [2.0, 2.0, 0.0],
            [2.0, 0.0, 0.0],
            [0.0, 0.0, 0.0],
        ],
        pins([
            ([0.0, 0.0, 0.0], [0.0, 0.0]),
            ([2.0, 0.0, 0.0], [1.0, 0.0]),
            ([0.0, 2.0, 0.0], [0.0, 1.0]),
        ]),
    );
    let normal = model
        .root()
        .faces
        .values()
        .next()
        .unwrap()
        .normal
        .expect("normal");
    assert_near(normal[2], -1.0, 1e-9);
    expect_matrix(&m, [-2.0, 0.0, 0.0, 0.0, 2.0, 0.0, 0.0, 0.0, 1.0], 1e-9);
}

#[test]
fn pins_are_in_tiles_whatever_the_applied_size() {
    // A 10 in tile with (50,0) -> (1,0) stores a 5x scale, not the unscaled 50.
    let (m, _) = positioned_face_uv(
        Some((10.0, 10.0)),
        &square(),
        pins([
            ([0.0, 0.0, 0.0], [0.0, 0.0]),
            ([50.0, 0.0, 0.0], [1.0, 0.0]),
            ([0.0, 50.0, 0.0], [0.0, 1.0]),
        ]),
    );
    expect_matrix(&m, [5.0, 0.0, 0.0, 0.0, 5.0, 0.0, 0.0, 0.0, 1.0], 1e-9);
}

#[test]
fn tilted_face_edge_aligned_mapping_is_a_pure_scale() {
    let s = 70.710_678_118_654_76; // 100 / sqrt(2)
    let (m, _) = positioned_face_uv(
        None,
        &[
            [0.0, 0.0, 0.0],
            [100.0, 0.0, 0.0],
            [100.0, s, s],
            [0.0, s, s],
        ],
        pins([
            ([0.0, 0.0, 0.0], [0.0, 0.0]),
            ([100.0, 0.0, 0.0], [1.0, 0.0]),
            ([0.0, s, s], [0.0, 1.0]),
        ]),
    );
    expect_matrix(&m, [100.0, 0.0, 0.0, 0.0, 100.0, 0.0, 0.0, 0.0, 1.0], 1e-6);
}

// ---------------------------------------------------------------------------------------------
// The archive-slot 0x7FFF boundary.
// ---------------------------------------------------------------------------------------------

#[test]
fn large_model_crossing_the_slot_boundary_round_trips() {
    let mut b = create();
    for i in 0..5000 {
        let x = f64::from(i) * 10.0;
        b.add_face(
            &[[x, 0.0, 0.0], [x + 1.0, 0.0, 0.0], [x, 1.0, 0.0]],
            &FaceOptions::default(),
        )
        .unwrap();
    }
    assert_eq!(round_trip(&mut b).root().faces.len(), 5000);
}

// ---------------------------------------------------------------------------------------------
// Section plane / dimension / text / construction geometry.
// ---------------------------------------------------------------------------------------------

#[test]
fn section_plane_text_dimension_round_trip() {
    let mut b = create();
    b.add_face(
        &[
            [0.0, 0.0, 0.0],
            [10.0, 0.0, 0.0],
            [10.0, 10.0, 0.0],
            [0.0, 10.0, 0.0],
        ],
        &FaceOptions::default(),
    )
    .unwrap();
    b.add_section_plane([10.0, 20.0, 30.0], [0.0, 0.0, 1.0])
        .unwrap();
    b.add_text("Hello", [5.0, 5.0, 5.0], DEFAULT_TEXT_LEADER)
        .unwrap();
    b.add_dimension([0.0, 0.0, 0.0], [10.0, 0.0, 0.0], DEFAULT_DIMENSION_OFFSET)
        .unwrap();

    let model = round_trip(&mut b);
    assert_eq!(model.root().section_planes.len(), 1);
    let sp = &model.root().section_planes[0];
    assert_near(sp.plane[0], 0.0, 1e-6);
    assert_near(sp.plane[1], 0.0, 1e-6);
    assert_near(sp.plane[2], 1.0, 1e-6);
    assert_near(sp.plane[3], -30.0, 1e-6);
    assert!(!sp.hidden);

    assert_eq!(model.root().texts.len(), 1);
    assert_eq!(model.root().texts[0].text, "Hello");
    assert!(!model.root().texts[0].hidden);

    assert_eq!(model.root().dimensions.len(), 1);
}

#[test]
fn section_plane_normalizes_a_non_unit_normal() {
    let mut b = create();
    b.add_face(
        &[
            [0.0, 0.0, 0.0],
            [10.0, 0.0, 0.0],
            [10.0, 10.0, 0.0],
            [0.0, 10.0, 0.0],
        ],
        &FaceOptions::default(),
    )
    .unwrap();
    b.add_section_plane([0.0, 0.0, 5.0], [0.0, 0.0, 2.0])
        .unwrap();
    let model = round_trip(&mut b);
    assert_eq!(model.root().section_planes.len(), 1);
    assert_near(model.root().section_planes[0].plane[2], 1.0, 1e-6);
    assert_near(model.root().section_planes[0].plane[3], -5.0, 1e-6);
}

#[test]
fn two_dimensions_share_one_embedded_font() {
    // Only the first dimension/text embeds the CSkFont inline; later ones back-reference it.
    let mut b = create();
    b.add_face(
        &[
            [0.0, 0.0, 0.0],
            [10.0, 0.0, 0.0],
            [10.0, 10.0, 0.0],
            [0.0, 10.0, 0.0],
        ],
        &FaceOptions::default(),
    )
    .unwrap();
    b.add_dimension([0.0, 0.0, 0.0], [10.0, 0.0, 0.0], DEFAULT_DIMENSION_OFFSET)
        .unwrap();
    b.add_dimension([0.0, 5.0, 0.0], [10.0, 5.0, 0.0], DEFAULT_DIMENSION_OFFSET)
        .unwrap();
    b.add_text("First", [1.0, 1.0, 1.0], DEFAULT_TEXT_LEADER)
        .unwrap();
    let model = round_trip(&mut b);
    assert_eq!(model.root().dimensions.len(), 2);
    assert_eq!(model.root().texts.len(), 1);
}

#[test]
fn construction_line_and_point_do_not_corrupt_the_file() {
    let mut b = create();
    b.add_face(
        &[
            [0.0, 0.0, 0.0],
            [10.0, 0.0, 0.0],
            [10.0, 10.0, 0.0],
            [0.0, 10.0, 0.0],
        ],
        &FaceOptions::default(),
    )
    .unwrap();
    b.add_construction_point([1.0, 2.0, 3.0], &GuideOptions::default())
        .unwrap();
    b.add_construction_line(
        [0.0; 3],
        Some([10.0, 0.0, 0.0]),
        None,
        &GuideOptions::default(),
    )
    .unwrap();
    b.add_construction_line(
        [0.0; 3],
        None,
        Some([0.0, 0.0, 1.0]),
        &GuideOptions::default(),
    )
    .unwrap();
    b.add_section_plane([10.0, 20.0, 30.0], [0.0, 0.0, 1.0])
        .unwrap(); // still readable afterwards
    let model = round_trip(&mut b);
    assert_eq!(model.root().section_planes.len(), 1);
    assert_near(model.root().section_planes[0].plane[3], -30.0, 1e-6);
}

#[test]
fn nested_definition_construction_round_trips() {
    // Construction last inside the nested definition on purpose: that used to desync the legacy
    // walk (cline trailer calibration against the definition's nrel=0 tail). Root-level guides
    // follow, so a wrong cached trailer length would corrupt those too.
    let mut b = create();
    let mut nested = b
        .add_component_definition("GuideGroup", &DefinitionOptions::default())
        .unwrap();
    nested
        .add_face(
            &[
                [0.0, 0.0, 0.0],
                [10.0, 0.0, 0.0],
                [10.0, 10.0, 0.0],
                [0.0, 10.0, 0.0],
            ],
            &FaceOptions::default(),
        )
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
    b.add_instance(nested, &InstanceOptions::default()).unwrap();
    b.add_construction_point([1.0, 2.0, 3.0], &GuideOptions::default())
        .unwrap();
    b.add_construction_line(
        [0.0, 0.0, 0.0],
        Some([12.0, 0.0, 0.0]),
        None,
        &GuideOptions::default(),
    )
    .unwrap();
    b.add_construction_line(
        [0.0, 0.0, 4.0],
        None,
        Some([0.0, 0.0, 1.0]),
        &GuideOptions::default(),
    )
    .unwrap();

    let model = round_trip(&mut b);
    let root = model.root();
    assert_eq!(root.construction_points.len(), 1);
    assert_near(root.construction_points[0].position[0], 1.0, 1e-9);
    assert_near(root.construction_points[0].position[1], 2.0, 1e-9);
    assert_near(root.construction_points[0].position[2], 3.0, 1e-9);

    assert_eq!(root.construction_lines.len(), 2);
    assert!(root.construction_lines[0].start.is_some() && root.construction_lines[0].end.is_some());
    assert_near(root.construction_lines[0].end.unwrap()[0], 12.0, 1e-9);
    assert!(root.construction_lines[1].start.is_none());
    assert!(root.construction_lines[1].end.is_none());
    assert_near(root.construction_lines[1].point[2], 4.0, 1e-9);

    let group = model
        .definitions
        .values()
        .find(|d| d.name == "GuideGroup")
        .expect("GuideGroup");
    assert_eq!(group.construction_points.len(), 1);
    assert_near(group.construction_points[0].position[0], 4.0, 1e-9);
    assert_eq!(group.construction_lines.len(), 1);
    assert!(
        group.construction_lines[0].start.is_some() && group.construction_lines[0].end.is_some()
    );
}

// ---------------------------------------------------------------------------------------------
// Cross-language parity, read side (Dart's create_test.dart): the Python references for circle
// and arc are byte-compared in create_reference_test.rs; this re-checks them numerically.
// ---------------------------------------------------------------------------------------------

#[test]
fn circle_and_arc_references_decode_to_the_same_vertices() {
    let mut circle = create();
    circle
        .add_circle(
            [50.0, 50.0, 0.0],
            [0.0, 0.0, 1.0],
            40.0,
            &CircleOptions {
                num_segments: 12,
                ..Default::default()
            },
        )
        .unwrap();
    let mut arc = create();
    arc.add_arc(
        [50.0, 50.0, 0.0],
        [0.0, 0.0, 1.0],
        40.0,
        0.0,
        PI / 2.0,
        &ArcOptions {
            num_segments: 8,
            ..Default::default()
        },
    )
    .unwrap();
    for (name, builder) in [("circle", &mut circle), ("arc", &mut arc)] {
        let ours = round_trip(builder);
        let path = format!(
            "{}/tests/fixtures/create_reference/{name}.skp",
            env!("CARGO_MANIFEST_DIR")
        );
        let theirs = SkpFile::from_buffer(std::fs::read(path).unwrap())
            .parse(&ParseOptions::default())
            .expect("parse");
        assert_eq!(ours.root().faces.len(), theirs.root().faces.len());
        assert_eq!(ours.root().edges.len(), theirs.root().edges.len());
        assert_eq!(ours.root().vertices.len(), theirs.root().vertices.len());
        for (a, b) in ours
            .root()
            .vertices
            .values()
            .zip(theirs.root().vertices.values())
        {
            assert_near(a.x, b.x, 1e-9);
            assert_near(a.y, b.y, 1e-9);
            assert_near(a.z, b.z, 1e-9);
        }
    }
}

// Ported from the C++ create_test.cpp (openskp#475): a guide's hidden flag and layer.
#[test]
fn guide_options_round_trip_hidden_flag_and_layer() {
    let mut b = create();
    let guides = b.add_layer("Guides", &LayerOptions::default()).unwrap();
    b.add_face(&square(), &FaceOptions::default()).unwrap();
    let hidden = GuideOptions {
        layer: None,
        hidden: true,
    };
    let on_guides = GuideOptions {
        layer: Some(guides),
        hidden: false,
    };
    let hidden_on_guides = GuideOptions {
        layer: Some(guides),
        hidden: true,
    };
    b.add_construction_point([1.0, 2.0, 3.0], &hidden).unwrap();
    b.add_construction_point([4.0, 5.0, 6.0], &on_guides)
        .unwrap();
    b.add_construction_line([0.0; 3], Some([10.0, 0.0, 0.0]), None, &hidden_on_guides)
        .unwrap();
    b.add_construction_line(
        [0.0, 0.0, 4.0],
        None,
        Some([0.0, 0.0, 1.0]),
        &GuideOptions::default(),
    )
    .unwrap();

    let model = round_trip(&mut b);
    let points = &model.root().construction_points;
    assert_eq!(points.len(), 2);
    assert!(points[0].hidden);
    assert!(points[0].layer.is_empty());
    assert!(!points[1].hidden);
    assert_eq!(points[1].layer, "Guides");
    let lines = &model.root().construction_lines;
    assert_eq!(lines.len(), 2);
    assert!(lines[0].hidden);
    assert_eq!(lines[0].layer, "Guides");
    assert!(!lines[1].hidden);
    assert!(lines[1].layer.is_empty());
}

#[test]
fn guide_options_reject_a_foreign_layer_handle() {
    let mut b = create();
    b.add_face(&square(), &FaceOptions::default()).unwrap();
    let foreign = GuideOptions {
        layer: Some(42),
        hidden: false,
    };
    assert!(b.add_construction_point([1.0, 2.0, 3.0], &foreign).is_err());
    assert!(b
        .add_construction_line([0.0; 3], Some([1.0, 0.0, 0.0]), None, &foreign)
        .is_err());
}
