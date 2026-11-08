//! Byte-exact parity with the reference C++ writer.
//!
//! Every scenario below is replayed verbatim from `tests/fixtures/create_reference/
//! oracle_driver.cpp`, which produced the matching `.skp` files with the C++ `create.cpp` (made
//! reproducible: deterministic GUIDs and a fixed timestamp, the same scheme
//! `SkpBuilder::use_deterministic_ids` uses). Several of those files are also byte-identical to
//! the Python writer's own references - see the driver's header.
//!
//! Texture paths are relative to the crate root (cargo's test working directory) because the
//! path string is embedded verbatim in the file.

use std::f64::consts::PI;

use openskp::create::{
    create, ArcOptions, AttributeDict, AttributeValue, CircleOptions, DefinitionOptions,
    DocumentOptions, FaceOptions, GroupInstanceOptions, GroupOptions, GuideOptions, ImageOptions,
    InstanceOptions, LayerOptions, Point3, PolylineOptions, SkpBuilder, SkpWriteError,
    UvCorrespondence, DEFAULT_DIMENSION_OFFSET, DEFAULT_TEXT_LEADER,
};

const PNG: &str = "tests/fixtures/create_reference/fake.png";
const JPG: &str = "tests/fixtures/create_reference/fake.jpg";
const TIMESTAMP: u32 = 1_700_000_000;

type Scenario = fn(&mut SkpBuilder) -> Result<(), SkpWriteError>;

fn build(scenario: Scenario) -> Vec<u8> {
    let mut builder = create();
    builder.use_deterministic_ids(TIMESTAMP);
    scenario(&mut builder).expect("scenario should succeed");
    builder.to_bytes().expect("to_bytes should succeed")
}

fn reference(name: &str) -> Vec<u8> {
    let path = format!(
        "{}/tests/fixtures/create_reference/{name}.skp",
        env!("CARGO_MANIFEST_DIR")
    );
    std::fs::read(&path).unwrap_or_else(|e| panic!("cannot read {path}: {e}"))
}

/// True if byte `i` lies inside some 8-byte window that decodes to nearly-equal finite f64s in
/// both buffers - a 1-ulp libm difference in cos()/sin() on another platform, not a layout bug.
fn within_float_noise(actual: &[u8], expected: &[u8], i: usize) -> bool {
    (0..8).any(|k| {
        let Some(start) = i.checked_sub(k) else {
            return false;
        };
        if start + 8 > actual.len() {
            return false;
        }
        let a = f64::from_le_bytes(actual[start..start + 8].try_into().unwrap());
        let b = f64::from_le_bytes(expected[start..start + 8].try_into().unwrap());
        a.is_finite() && b.is_finite() && (a - b).abs() <= 1e-9 * b.abs().max(1.0)
    })
}

/// Asserts `scenario` reproduces `<name>.skp` exactly. `trig` scenarios (circle/arc points,
/// rotation matrices) tolerate last-bit float differences, since IEEE 754 does not pin cos()/sin()
/// across platforms; layout and every non-float byte must still match exactly.
fn check(name: &str, scenario: Scenario, trig: bool) {
    let actual = build(scenario);
    let expected = reference(name);
    assert_eq!(
        actual.len(),
        expected.len(),
        "{name}: length differs from the C++ reference"
    );
    if actual == expected {
        return;
    }
    let diffs: Vec<usize> = (0..actual.len())
        .filter(|&i| actual[i] != expected[i])
        .collect();
    assert!(
        trig && diffs
            .iter()
            .all(|&i| within_float_noise(&actual, &expected, i)),
        "{name}: {} byte(s) differ from the C++ reference, first at offset {}",
        diffs.len(),
        diffs[0]
    );
}

fn sq(s: f64, z: f64) -> Vec<Point3> {
    vec![[0.0, 0.0, z], [s, 0.0, z], [s, s, z], [0.0, s, z]]
}

fn attrs(entries: &[(&str, AttributeValue)]) -> AttributeDict {
    entries
        .iter()
        .map(|(k, v)| ((*k).to_owned(), v.clone()))
        .collect()
}

fn uv(points: [(Point3, [f64; 2]); 3]) -> Option<UvCorrespondence> {
    Some(points.to_vec())
}

#[test]
fn single_face() {
    check(
        "single_face",
        |b| b.add_face(&sq(100.0, 0.0), &FaceOptions::default()),
        false,
    );
}

#[test]
fn multi_face_shared_edge() {
    check(
        "multi_face_shared_edge",
        |b| {
            b.add_face(&sq(100.0, 0.0), &FaceOptions::default())?;
            b.add_face(
                &[
                    [100.0, 0.0, 0.0],
                    [200.0, 0.0, 0.0],
                    [200.0, 100.0, 0.0],
                    [100.0, 100.0, 0.0],
                ],
                &FaceOptions::default(),
            )
        },
        false,
    );
}

#[test]
fn materials_and_layers() {
    check(
        "materials_and_layers",
        |b| {
            let red = b.add_material("Red", [255, 0, 0], None)?;
            let blue = b.add_material("Blue", [0, 0, 255, 128], None)?;
            let roof = b.add_layer(
                "Roof",
                &LayerOptions {
                    color: Some([10, 20, 30, 255]),
                    ..Default::default()
                },
            )?;
            let walls = b.add_layer(
                "Walls",
                &LayerOptions {
                    hidden: true,
                    ..Default::default()
                },
            )?;
            b.add_face(
                &sq(100.0, 0.0),
                &FaceOptions {
                    material: Some(red),
                    layer: Some(roof),
                    ..Default::default()
                },
            )?;
            b.add_face(
                &sq(100.0, 10.0),
                &FaceOptions {
                    material: Some(blue),
                    back_material: Some(red),
                    layer: Some(walls),
                    ..Default::default()
                },
            )
        },
        false,
    );
}

#[test]
fn holes() {
    check(
        "holes",
        |b| {
            let window = vec![
                [80.0, 30.0, 0.0],
                [120.0, 30.0, 0.0],
                [120.0, 70.0, 0.0],
                [80.0, 70.0, 0.0],
            ];
            b.add_face(
                &[
                    [0.0, 0.0, 0.0],
                    [200.0, 0.0, 0.0],
                    [200.0, 100.0, 0.0],
                    [0.0, 100.0, 0.0],
                ],
                &FaceOptions {
                    holes: vec![window],
                    ..Default::default()
                },
            )
        },
        false,
    );
}

#[test]
fn auto_triangulate() {
    check(
        "auto_triangulate",
        |b| {
            b.add_face(
                &[
                    [0.0, 0.0, 0.0],
                    [10.0, 0.0, 0.0],
                    [10.0, 10.0, 0.0],
                    [0.0, 10.0, 5.0],
                ],
                &FaceOptions {
                    auto_triangulate: true,
                    ..Default::default()
                },
            )
        },
        false,
    );
}

#[test]
fn circle() {
    check(
        "circle",
        |b| {
            b.add_circle(
                [50.0, 50.0, 0.0],
                [0.0, 0.0, 1.0],
                40.0,
                &CircleOptions {
                    num_segments: 12,
                    ..Default::default()
                },
            )
        },
        true,
    );
}

#[test]
fn arc() {
    check(
        "arc",
        |b| {
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
        },
        true,
    );
}

#[test]
fn polyline() {
    check(
        "polyline",
        |b| {
            b.add_polyline(
                &[
                    [0.0, 0.0, 0.0],
                    [10.0, 10.0, 0.0],
                    [20.0, 0.0, 0.0],
                    [30.0, 10.0, 0.0],
                ],
                &PolylineOptions {
                    closed: true,
                    ..Default::default()
                },
            )
        },
        false,
    );
}

#[test]
fn attributes() {
    check(
        "attributes",
        |b| {
            let attributes = attrs(&[
                ("part_number", "A-100".into()),
                ("count", 7.into()),
                ("weight", 3.5.into()),
            ]);
            b.add_face(
                &sq(100.0, 0.0),
                &FaceOptions {
                    attributes,
                    ..Default::default()
                },
            )
        },
        false,
    );
}

#[test]
fn slot_boundary() {
    check(
        "slot_boundary",
        |b| {
            for i in 0..3200 {
                let x = f64::from(i) * 10.0;
                b.add_face(
                    &[[x, 0.0, 0.0], [x + 1.0, 0.0, 0.0], [x, 1.0, 0.0]],
                    &FaceOptions::default(),
                )?;
            }
            // The 0x7FFF escape path (both new slots and shifted tail references) is exercised.
            assert!(b.debug_geometry_next_slot().unwrap() > 0x7FFF);
            Ok(())
        },
        false,
    );
}

#[test]
fn textures() {
    check(
        "textures",
        |b| {
            let t1 = b.add_texture_material("Brick", PNG, None, None, None)?;
            let t2 = b.add_texture_material("Tile", PNG, Some(48.0), None, None)?;
            let t3 = b.add_texture_material("Wood", JPG, Some(16.0), Some(8.0), None)?;
            let t4 = b.add_texture_material("Voile", PNG, None, None, Some(0.5))?;
            let glass = b.add_material("Glass", [200, 220, 255, 255], Some(0.35))?;
            assert_eq!(b.add_material("Brick", [1, 2, 3], None)?, t1);
            b.add_face(
                &sq(100.0, 0.0),
                &FaceOptions {
                    material: Some(t1),
                    ..Default::default()
                },
            )?;
            b.add_face(
                &sq(100.0, 10.0),
                &FaceOptions {
                    material: Some(t2),
                    back_material: Some(t3),
                    ..Default::default()
                },
            )?;
            b.add_face(
                &sq(100.0, 20.0),
                &FaceOptions {
                    material: Some(t4),
                    back_material: Some(glass),
                    ..Default::default()
                },
            )
        },
        false,
    );
}

#[test]
fn uv_positioning() {
    check(
        "uv_positioning",
        |b| {
            let brick = b.add_material("Brick", [150, 100, 50], None)?;
            let tex = b.add_texture_material("Tex", PNG, Some(10.0), Some(10.0), None)?;
            let tex2 = b.add_texture_material("Tex2", PNG, None, None, None)?;
            let pins = uv([
                ([0.0, 0.0, 0.0], [0.0, 0.0]),
                ([50.0, 0.0, 0.0], [1.0, 0.0]),
                ([0.0, 50.0, 0.0], [0.0, 1.0]),
            ]);
            b.add_face(
                &sq(100.0, 0.0),
                &FaceOptions {
                    material: Some(brick),
                    front_uv: pins.clone(),
                    ..Default::default()
                },
            )?;
            b.add_face(
                &[
                    [100.0, 0.0, 5.0],
                    [100.0, 100.0, 5.0],
                    [0.0, 100.0, 5.0],
                    [0.0, 0.0, 5.0],
                ],
                &FaceOptions {
                    material: Some(tex),
                    back_material: Some(tex),
                    front_uv: pins,
                    back_uv: uv([
                        ([0.0, 0.0, 5.0], [0.0, 0.0]),
                        ([20.0, 0.0, 5.0], [1.0, 0.0]),
                        ([0.0, 30.0, 5.0], [0.0, 1.0]),
                    ]),
                    ..Default::default()
                },
            )?;
            b.add_face(
                &[
                    [0.0, 2.0, -5.0],
                    [2.0, 2.0, -5.0],
                    [2.0, 0.0, -5.0],
                    [0.0, 0.0, -5.0],
                ],
                &FaceOptions {
                    material: Some(tex2),
                    front_uv: uv([
                        ([0.0, 0.0, 0.0], [0.0, 0.0]),
                        ([2.0, 0.0, 0.0], [1.0, 0.0]),
                        ([0.0, 2.0, 0.0], [0.0, 1.0]),
                    ]),
                    ..Default::default()
                },
            )?;
            let s = 70.710_678_118_654_76;
            b.add_face(
                &[
                    [0.0, 0.0, 0.0],
                    [100.0, 0.0, 0.0],
                    [100.0, s, s],
                    [0.0, s, s],
                ],
                &FaceOptions {
                    material: Some(tex2),
                    back_uv: uv([
                        ([0.0, 0.0, 0.0], [0.0, 0.0]),
                        ([100.0, 0.0, 0.0], [1.0, 0.0]),
                        ([0.0, s, s], [0.0, 1.0]),
                    ]),
                    attributes: attrs(&[("k", "v".into())]),
                    ..Default::default()
                },
            )?;
            b.add_face(
                &[
                    [0.0, 0.0, 0.0],
                    [10.0, 0.0, 0.0],
                    [10.0, 0.0, 10.0],
                    [0.0, 0.0, 10.0],
                ],
                &FaceOptions {
                    material: Some(tex2),
                    front_uv: uv([
                        ([0.0, 0.0, 0.0], [0.0, 0.0]),
                        ([10.0, 0.0, 0.0], [1.0, 0.0]),
                        ([0.0, 0.0, 10.0], [0.0, 1.0]),
                    ]),
                    ..Default::default()
                },
            )
        },
        false,
    );
}

#[test]
fn components() {
    check(
        "components",
        |b| {
            let red = b.add_material("Red", [255, 0, 0], None)?;
            let furniture = b.add_layer("Furniture", &LayerOptions::default())?;
            let definition_options = DefinitionOptions {
                attributes: attrs(&[("material_code", "ABC-123".into()), ("flag", true.into())]),
                ..Default::default()
            };
            let mut chair = b.add_component_definition("Chair", &definition_options)?;
            chair.add_face(
                &[
                    [0.0, 0.0, 0.0],
                    [20.0, 0.0, 0.0],
                    [20.0, 20.0, 0.0],
                    [0.0, 20.0, 0.0],
                ],
                &FaceOptions {
                    attributes: attrs(&[("area_sqft", 12.5.into())]),
                    material: Some(red),
                    ..Default::default()
                },
            )?;
            chair.add_face(
                &[
                    [0.0, 0.0, 0.0],
                    [20.0, 0.0, 0.0],
                    [20.0, 0.0, 20.0],
                    [0.0, 0.0, 20.0],
                ],
                &FaceOptions::default(),
            )?;
            let chair = chair.close()?;
            b.add_instance(
                chair,
                &InstanceOptions {
                    translation: [100.0, 0.0, 0.0],
                    ..Default::default()
                },
            )?;
            b.add_instance(
                chair,
                &InstanceOptions {
                    translation: [200.0, 0.0, 0.0],
                    name: Some("Second Chair".into()),
                    rotation: Some(([0.0, 0.0, 1.0], PI / 2.0)),
                    material: Some(red),
                    layer: Some(furniture),
                    hidden: true,
                    attributes: attrs(&[
                        ("visible", true.into()),
                        ("hidden", false.into()),
                        ("depth", AttributeValue::Length(15.5)),
                        ("weight", 3.5.into()),
                        ("count", (-42).into()),
                        ("label", "widget \u{e9}\u{1F600}".into()),
                    ]),
                    ..Default::default()
                },
            )?;
            b.add_instance(
                chair,
                &InstanceOptions {
                    matrix3x3: Some([2.0, 0.0, 0.0, 0.0, 2.0, 0.0, 0.0, 0.0, 2.0]),
                    translation: [0.0, 300.0, 0.0],
                    ..Default::default()
                },
            )?;
            b.add_face(&sq(10.0, -1.0), &FaceOptions::default())
        },
        true,
    );
}

#[test]
fn groups() {
    check(
        "groups",
        |b| {
            let red = b.add_material("Red", [255, 0, 0], None)?;
            let l1 = b.add_layer("L1", &LayerOptions::default())?;
            let mut table = b.add_group(&GroupOptions {
                name: Some("Table".into()),
                translation: [50.0, 0.0, 0.0],
                attributes: attrs(&[("sku", "TBL-1".into()), ("qty", 2.into())]),
                ..Default::default()
            })?;
            table.add_face(
                &[
                    [0.0, 0.0, 0.0],
                    [30.0, 0.0, 0.0],
                    [30.0, 30.0, 0.0],
                    [0.0, 30.0, 0.0],
                ],
                &FaceOptions::default(),
            )?;
            table.close()?;
            let mut second = b.add_group(&GroupOptions {
                rotation: Some(([1.0, 1.0, 0.0], 0.3)),
                material: Some(red),
                layer: Some(l1),
                hidden: true,
                ..Default::default()
            })?;
            second.add_face(
                &[[0.0, 0.0, 0.0], [5.0, 0.0, 0.0], [5.0, 5.0, 0.0]],
                &FaceOptions::default(),
            )?;
            second.close()?;
            let mut chair = b.add_component_definition("Chair", &DefinitionOptions::default())?;
            chair.add_face(&sq(100.0, 0.0), &FaceOptions::default())?;
            let chair = chair.close()?;
            b.add_instance(chair, &InstanceOptions::default())
        },
        true,
    );
}

#[test]
fn groups_only() {
    check(
        "groups_only",
        |b| {
            let mut group = b.add_group(&GroupOptions::default())?;
            group.add_face(&sq(10.0, 0.0), &FaceOptions::default())?;
            group.close()?;
            Ok(())
        },
        false,
    );
}

#[test]
fn nested() {
    check(
        "nested",
        |b| {
            let mut wheel = b.add_component_definition("Wheel", &DefinitionOptions::default())?;
            wheel.add_face(&sq(10.0, 0.0), &FaceOptions::default())?;
            let wheel = wheel.close()?;
            let mut engine = b.add_component_definition("Engine", &DefinitionOptions::default())?;
            engine.add_face(&sq(30.0, 0.0), &FaceOptions::default())?;
            let engine = engine.close()?;
            let mut car = b.add_component_definition("Car", &DefinitionOptions::default())?;
            car.add_face(
                &[
                    [0.0, 0.0, 0.0],
                    [150.0, 0.0, 0.0],
                    [150.0, 60.0, 0.0],
                    [0.0, 60.0, 0.0],
                ],
                &FaceOptions::default(),
            )?;
            car.add_instance(
                wheel,
                &InstanceOptions {
                    name: Some("LeafInstance".into()),
                    attributes: attrs(&[("n", 1.into())]),
                    ..Default::default()
                },
            )?;
            car.add_group_instance(
                engine,
                &GroupInstanceOptions {
                    translation: [50.0, 0.0, 10.0],
                    attributes: attrs(&[("part", "V6".into())]),
                    rotation: Some(([0.0, 1.0, 0.0], 1.0)),
                    ..Default::default()
                },
            )?;
            car.add_circle(
                [0.0, 0.0, 5.0],
                [0.0, 0.0, 1.0],
                3.0,
                &CircleOptions::default(),
            )?;
            car.add_arc(
                [0.0, 0.0, 6.0],
                [1.0, 0.0, 0.0],
                2.0,
                0.0,
                PI,
                &ArcOptions::default(),
            )?;
            car.add_polyline(
                &[[0.0, 0.0, 7.0], [1.0, 0.0, 7.0], [1.0, 1.0, 7.0]],
                &PolylineOptions::default(),
            )?;
            car.add_construction_point([4.0, 5.0, 6.0], &GuideOptions::default())?;
            car.add_construction_line(
                [1.0, 1.0, 1.0],
                Some([2.0, 2.0, 2.0]),
                None,
                &GuideOptions::default(),
            )?;
            car.add_construction_line(
                [1.0, 1.0, 1.0],
                None,
                Some([0.0, 3.0, 0.0]),
                &GuideOptions::default(),
            )?;
            let car = car.close()?;
            b.add_instance(car, &InstanceOptions::default())?;
            b.add_construction_point([1.0, 2.0, 3.0], &GuideOptions::default())?;
            b.add_construction_line(
                [0.0, 0.0, 0.0],
                Some([12.0, 0.0, 0.0]),
                None,
                &GuideOptions::default(),
            )?;
            b.add_construction_line(
                [0.0, 0.0, 4.0],
                None,
                Some([0.0, 0.0, 1.0]),
                &GuideOptions::default(),
            )
        },
        true,
    );
}

#[test]
fn image() {
    check(
        "image",
        |b| {
            b.add_image(
                PNG,
                48.0,
                36.0,
                &ImageOptions {
                    translation: [0.0, 0.0, 40.0],
                    rotation: Some(([1.0, 0.0, 0.0], PI / 2.0)),
                    hidden: true,
                    ..Default::default()
                },
            )?;
            b.add_face(&sq(10.0, 0.0), &FaceOptions::default())
        },
        true,
    );
}

#[test]
fn image_jpeg_with_material() {
    check(
        "image_jpeg_with_material",
        |b| {
            b.add_material("Red", [255, 0, 0], None)?;
            b.add_image(
                JPG,
                10.0,
                5.0,
                &ImageOptions {
                    matrix3x3: Some([1.0, 0.0, 0.0, 0.0, 0.0, -1.0, 0.0, 1.0, 0.0]),
                    ..Default::default()
                },
            )
        },
        false,
    );
}

#[test]
fn annotations() {
    check(
        "annotations",
        |b| {
            b.add_face(&sq(10.0, 0.0), &FaceOptions::default())?;
            b.add_section_plane([10.0, 20.0, 30.0], [0.0, 0.0, 1.0])?;
            b.add_section_plane([0.0, 0.0, 5.0], [0.0, 3.0, 2.0])?;
            b.add_text("Hello", [5.0, 5.0, 5.0], DEFAULT_TEXT_LEADER)?;
            b.add_dimension([0.0, 0.0, 0.0], [10.0, 0.0, 0.0], DEFAULT_DIMENSION_OFFSET)?;
            b.add_dimension([0.0, 5.0, 0.0], [10.0, 5.0, 0.0], -3.5)?;
            b.add_text("Unicode \u{e9}\u{1F600}", [1.0, 1.0, 1.0], [1.0, 2.0, 3.0])?;
            b.add_construction_point([1.0, 2.0, 3.0], &GuideOptions::default())?;
            b.add_construction_line(
                [0.0, 0.0, 0.0],
                Some([10.0, 0.0, 0.0]),
                None,
                &GuideOptions::default(),
            )?;
            b.add_construction_line(
                [0.0, 0.0, 0.0],
                None,
                Some([0.0, 0.0, 1.0]),
                &GuideOptions::default(),
            )
        },
        false,
    );
}

#[test]
fn layers_extra() {
    check(
        "layers_extra",
        |b| {
            b.add_material("M1", [1, 2, 3], None)?;
            b.add_material("M2", [4, 5, 6, 7], None)?;
            b.add_material("M3", [8, 9, 10], Some(0.25))?;
            let mut options = LayerOptions {
                color: Some([1, 2, 3, 4]),
                ..Default::default()
            };
            options.extra_dictionaries.insert(
                "plugin".into(),
                attrs(&[("n", 7.into()), ("s", "x".into())]),
            );
            options.extra_dictionaries.insert(
                "another".into(),
                attrs(&[("len", AttributeValue::Length(2.5))]),
            );
            let walls = b.add_layer("Walls", &options)?;
            b.add_layer("Roof", &LayerOptions::default())?;
            assert_eq!(b.add_layer("Walls", &LayerOptions::default())?, walls);
            b.add_layer(
                "Hidden",
                &LayerOptions {
                    hidden: true,
                    ..Default::default()
                },
            )?;
            b.add_face(&sq(10.0, 0.0), &FaceOptions::default())
        },
        false,
    );
}

#[test]
fn edge_flags() {
    check(
        "edge_flags",
        |b| {
            b.add_face(
                &sq(100.0, 0.0),
                &FaceOptions {
                    hidden: true,
                    soft_edges: true,
                    smooth_edges: true,
                    hidden_edges: true,
                    ..Default::default()
                },
            )?;
            b.add_face(
                &[
                    [100.0, 0.0, 0.0],
                    [200.0, 0.0, 0.0],
                    [200.0, 100.0, 0.0],
                    [100.0, 100.0, 0.0],
                ],
                &FaceOptions {
                    soft_edges: true,
                    ..Default::default()
                },
            )?;
            b.add_arc(
                [0.0, 0.0, 50.0],
                [0.0, 1.0, 1.0],
                10.0,
                1.0,
                -2.5,
                &ArcOptions {
                    hidden_edges: true,
                    soft_edges: true,
                    smooth_edges: true,
                    num_segments: 5,
                },
            )?;
            b.add_polyline(
                &[[0.0, 0.0, 60.0], [5.0, 0.0, 60.0], [5.0, 5.0, 61.0]],
                &PolylineOptions {
                    smooth_edges: true,
                    ..Default::default()
                },
            )?;
            b.add_circle(
                [0.0, 0.0, 70.0],
                [0.0, 0.0, 2.0],
                4.0,
                &CircleOptions {
                    num_segments: 7,
                    hidden: true,
                    attributes: attrs(&[("r", 3.0.into())]),
                    attribute_dict_name: "dims".into(),
                    front_uv: uv([
                        ([0.0, 0.0, 70.0], [0.0, 0.0]),
                        ([1.0, 0.0, 70.0], [1.0, 0.0]),
                        ([0.0, 1.0, 70.0], [0.0, 1.0]),
                    ]),
                    ..Default::default()
                },
            )?;
            b.add_circle(
                [0.0, 0.0, 80.0],
                [1.0, 0.0, 0.0],
                4.0,
                &CircleOptions {
                    num_segments: 3,
                    ..Default::default()
                },
            )
        },
        true,
    );
}

#[test]
fn large_triangulate() {
    check(
        "large_triangulate",
        |b| {
            b.add_face(
                &[
                    [0.0, 0.0, 0.0],
                    [10.0, 0.0, 0.0],
                    [12.0, 5.0, 3.0],
                    [10.0, 10.0, 0.0],
                    [0.0, 10.0, 1.0],
                ],
                &FaceOptions {
                    auto_triangulate: true,
                    attributes: attrs(&[("tri", 1.into())]),
                    ..Default::default()
                },
            )?;
            b.add_face(
                &[[0.0, 0.0, 0.0], [10.0, 0.0, 0.0], [5.0, -5.0, 0.0]],
                &FaceOptions::default(),
            )
        },
        false,
    );
}

#[test]
fn group_in_definition_flag() {
    check(
        "group_in_definition_flag",
        |b| {
            let mut engine = b.add_component_definition("Engine", &DefinitionOptions::default())?;
            engine.add_face(&sq(100.0, 0.0), &FaceOptions::default())?;
            let engine = engine.close()?;
            let mut car = b.add_component_definition("Car", &DefinitionOptions::default())?;
            car.add_face(&sq(100.0, 0.0), &FaceOptions::default())?;
            car.add_group_instance(
                engine,
                &GroupInstanceOptions {
                    translation: [50.0, 0.0, 10.0],
                    ..Default::default()
                },
            )?;
            let car = car.close()?;
            b.add_instance(car, &InstanceOptions::default())?;
            b.add_instance(engine, &InstanceOptions::default())
        },
        false,
    );
}

#[test]
fn many_slots_materials() {
    check(
        "many_slots_materials",
        |b| {
            let mut materials = Vec::new();
            for i in 0..40u8 {
                materials.push(b.add_material(&format!("Mat{i}"), [i, 0, 0], None)?);
            }
            for i in 0..10 {
                b.add_layer(&format!("Layer{i}"), &LayerOptions::default())?;
            }
            let mut d = b.add_component_definition("D", &DefinitionOptions::default())?;
            d.add_face(&sq(100.0, 0.0), &FaceOptions::default())?;
            let d = d.close()?;
            for i in 0..5 {
                b.add_instance(
                    d,
                    &InstanceOptions {
                        translation: [f64::from(i), 0.0, 0.0],
                        material: Some(materials[i as usize * 7]),
                        ..Default::default()
                    },
                )?;
            }
            Ok(())
        },
        false,
    );
}

#[test]
fn bundled_scaffold_matches_the_reader_fixture() {
    // The writer's embedded scaffold and the reader-side fixture are the same SDK-authored file.
    let fixture = std::fs::read(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/tests/fixtures/blank_v17.skp"
    ))
    .unwrap();
    let embedded = std::fs::read(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/src/create/scaffold_blank_v17.skp"
    ))
    .unwrap();
    assert_eq!(fixture, embedded);
}

// ---- openskp 1.5.0 writer features ----

#[test]
fn guide_options() {
    check(
        "guide_options",
        |b| {
            let guides = b.add_layer("Guides", &LayerOptions::default())?;
            let hidden = GuideOptions {
                layer: None,
                hidden: true,
            };
            let mut d = b.add_component_definition("GuideDef", &DefinitionOptions::default())?;
            d.add_face(&sq(5.0, 0.0), &FaceOptions::default())?;
            d.add_construction_point(
                [1.0, 1.0, 1.0],
                &GuideOptions {
                    layer: Some(guides),
                    hidden: true,
                },
            )?;
            d.add_construction_line([0.0; 3], Some([5.0, 0.0, 0.0]), None, &hidden)?;
            let d = d.close()?;
            b.add_instance(d, &InstanceOptions::default())?;
            b.add_face(&sq(10.0, 0.0), &FaceOptions::default())?;
            b.add_construction_point([1.0, 2.0, 3.0], &hidden)?;
            b.add_construction_point(
                [4.0, 5.0, 6.0],
                &GuideOptions {
                    layer: Some(guides),
                    hidden: false,
                },
            )?;
            b.add_construction_line(
                [0.0; 3],
                Some([10.0, 0.0, 0.0]),
                None,
                &GuideOptions {
                    layer: Some(guides),
                    hidden: true,
                },
            )?;
            b.add_construction_line(
                [0.0, 0.0, 4.0],
                None,
                Some([0.0, 0.0, 1.0]),
                &GuideOptions::default(),
            )
        },
        false,
    );
}

#[test]
fn document_options() {
    check(
        "document_options",
        |b| {
            b.set_document_options(&DocumentOptions {
                length_unit: 2,
                length_precision: 1,
                style_items: [
                    (4000, f64::from(0xff56_3412u32)),
                    (1006, 0.0),
                    (1007, 5.0),
                    (1000, 3.0),
                    (2004, 1.0),
                ]
                .into_iter()
                .collect(),
            })?;
            b.add_face(&sq(10.0, 0.0), &FaceOptions::default())
        },
        false,
    );
}

#[test]
fn dimension_vectors() {
    check(
        "dimension_vectors",
        |b| {
            let mut d = b.add_component_definition("Dims", &DefinitionOptions::default())?;
            d.add_face(&sq(5.0, 0.0), &FaceOptions::default())?;
            d.add_dimension([0.0; 3], [5.0, 0.0, 0.0], 2.5)?;
            d.add_dimension_with_offset_vector([0.0; 3], [5.0, 0.0, 0.0], [0.0, 0.0, 4.0])?;
            let d = d.close()?;
            b.add_instance(d, &InstanceOptions::default())?;
            b.add_face(&sq(10.0, 0.0), &FaceOptions::default())?;
            b.add_dimension_with_offset_vector([1.0, 2.0, 3.0], [11.0, 2.0, 3.0], [0.0, 0.0, 4.0])?;
            b.add_dimension_with_offset_vector(
                [11.0, 2.0, 3.0],
                [1.0, 2.0, 3.0],
                [0.0, 0.0, -4.0],
            )?;
            b.add_dimension_with_offset_vector(
                [1.0, 2.0, 3.0],
                [1.0, 2.0, 13.0],
                [-4.0, 0.0, 0.0],
            )?;
            b.add_dimension_with_offset_vector([1.0, 2.0, 3.0], [1.0, 2.0, 13.0], [0.0; 3])?;
            b.add_dimension_with_offset_vector([1.0, 2.0, 3.0], [11.0, 2.0, 3.0], [7.0, -4.0, 0.0])
        },
        false,
    );
}
