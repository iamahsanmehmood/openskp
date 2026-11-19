//! Fields the writer stores the way SketchUp itself writes them. Ported from the C++
//! native_compatibility_test.cpp (openskp#477).

use openskp::create::{
    create, DefinitionOptions, DocumentOptions, FaceOptions, GroupOptions, InstanceOptions,
    LayerOptions, Point3, SkpBuilder,
};
use openskp::{ParseOptions, SkpFile, SkpModel};

fn uint_le(bytes: &[u8], pos: usize, size: usize) -> u64 {
    (0..size).fold(0, |acc, i| acc | (u64::from(bytes[pos + i]) << (8 * i)))
}

fn locate(bytes: &[u8], needle: &[u8]) -> usize {
    bytes
        .windows(needle.len())
        .position(|w| w == needle)
        .expect("native field not found")
}

fn count(bytes: &[u8], needle: &[u8]) -> usize {
    bytes.windows(needle.len()).filter(|w| *w == needle).count()
}

/// The int32 value of the native named option `name` (a CString key, then a typed value).
fn native_option(bytes: &[u8], name: &str) -> u32 {
    let mut key = vec![0xff, 0xfe, 0xff, name.len() as u8];
    for c in name.bytes() {
        key.extend_from_slice(&[c, 0]);
    }
    let pos = locate(bytes, &key) + key.len();
    assert_eq!(bytes[pos], 4, "{name}: native int32 typed value");
    uint_le(bytes, pos + 1, 4) as u32
}

fn parse(bytes: Vec<u8>) -> SkpModel {
    SkpFile::from_buffer(bytes)
        .parse(&ParseOptions::default())
        .unwrap()
}

/// Adds `points` at the root, or inside a component definition placed once at the root.
fn add_face(builder: &mut SkpBuilder, points: &[Point3], options: &FaceOptions, nested: bool) {
    if nested {
        let mut def = builder
            .add_component_definition("part", &DefinitionOptions::default())
            .unwrap();
        def.add_face(points, options).unwrap();
        let def = def.close().unwrap();
        builder
            .add_instance(def, &InstanceOptions::default())
            .unwrap();
    } else {
        builder.add_face(points, options).unwrap();
    }
}

#[test]
fn face_plane_agrees_with_vertices() {
    let polygons: [Vec<Point3>; 4] = [
        vec![
            [0.0, 0.0, 3.0],
            [2.0, 0.0, 3.0],
            [2.0, 2.0, 3.0],
            [0.0, 2.0, 3.0],
        ],
        vec![
            [0.0, 2.0, 3.0],
            [2.0, 2.0, 3.0],
            [2.0, 0.0, 3.0],
            [0.0, 0.0, 3.0],
        ],
        vec![
            [-7.0, 0.0, 0.0],
            [-7.0, 2.0, 0.0],
            [-7.0, 2.0, 2.0],
            [-7.0, 0.0, 2.0],
        ],
        vec![
            [10.0, 20.0, 35.0],
            [12.0, 20.0, 37.0],
            [12.0, 22.0, 39.0],
            [10.0, 22.0, 37.0],
        ],
    ];
    for polygon in &polygons {
        for nested in [false, true] {
            let mut builder = create();
            add_face(&mut builder, polygon, &FaceOptions::default(), nested);
            let bytes = builder.to_bytes().unwrap();
            let marker = [0xff, 0xff, 3, 0, 5, 0, b'C', b'F', b'a', b'c', b'e'];
            let mut pos = locate(&bytes, &marker) + marker.len();
            assert_eq!(uint_le(&bytes, pos, 2), 0);
            pos += 2;
            let mask = bytes[pos];
            pos += 1 + mask.count_ones() as usize;
            pos += 10; // material and drawing flags
            let plane: Vec<f64> = (0..4)
                .map(|i| f64::from_bits(uint_le(&bytes, pos + 8 * i, 8)))
                .collect();
            // SketchUp's GetPlane recomputes the plane, so a raw check is essential here.
            assert!(plane[3].abs() > 1.0, "{plane:?}");
            for p in polygon {
                let r = plane[0] * p[0] + plane[1] * p[1] + plane[2] * p[2] + plane[3];
                assert!(r.abs() < 1e-9, "{p:?} is off the stored plane {plane:?}");
            }
        }
    }
}

#[test]
fn degenerate_fan_does_not_corrupt_following_entities() {
    let polygon = [
        [0.0, 0.0, 0.0],
        [1.0, 0.0, 0.0],
        [1.0, 1.0, 0.0],
        [2.0, 2.0, 0.0],
        [0.0, 1.0, 1.0],
    ];
    let options = FaceOptions {
        auto_triangulate: true,
        ..Default::default()
    };
    for nested in [false, true] {
        let mut builder = create();
        add_face(&mut builder, &polygon, &options, nested);
        builder
            .add_face(
                &[[10.0, 0.0, 0.0], [11.0, 0.0, 0.0], [10.0, 1.0, 0.0]],
                &FaceOptions::default(),
            )
            .unwrap();
        let model = parse(builder.to_bytes().unwrap());
        let faces = model.root().faces.len()
            + model
                .definitions
                .values()
                .map(|d| d.faces.len())
                .sum::<usize>();
        assert_eq!(faces, 3, "nested {nested}");
    }
}

#[test]
fn persistent_ids_are_document_wide() {
    let mut builder = create();
    builder.add_material("paint", [12u8, 34, 56], None).unwrap();
    builder.add_layer("tag", &LayerOptions::default()).unwrap();
    add_face(
        &mut builder,
        &[[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]],
        &FaceOptions::default(),
        true,
    );
    let bytes = builder.to_bytes().unwrap();
    // Reserved ids [0,18), material 18, layer 19/20, definition 21, vertices/edges/face 22..28
    // and root instance 29. The SketchUp-native header is a u64.
    assert_eq!(uint_le(&bytes, 1987, 8), 30);
    let name = b"CComponentInstance";
    let pos = locate(&bytes, name) + name.len() + 7;
    assert_eq!(bytes[pos], 1);
    assert_eq!(bytes[pos + 1], 29);
}

#[test]
fn persistent_id_counter_does_not_truncate_at_16_bits() {
    let mut builder = create();
    for i in 0..10_000 {
        let x = f64::from(i) * 2.0;
        builder
            .add_face(
                &[[x, 0.0, 0.0], [x + 1.0, 0.0, 0.0], [x, 1.0, 0.0]],
                &FaceOptions::default(),
            )
            .unwrap();
    }
    assert_eq!(uint_le(&builder.to_bytes().unwrap(), 1987, 8), 70_018);
}

#[test]
fn writes_native_units_and_both_styles_without_scaling() {
    for unit in 0..=5 {
        let mut builder = create();
        let options = DocumentOptions {
            length_unit: unit,
            length_precision: 2,
            style_items: [(4000, f64::from(0xff56_3412u32)), (1006, 0.0), (1007, 5.0)]
                .into_iter()
                .collect(),
        };
        builder.set_document_options(&options).unwrap();
        builder
            .add_face(
                &[[0.0, 0.0, 0.0], [2.0, 0.0, 0.0], [0.0, 3.0, 0.0]],
                &FaceOptions::default(),
            )
            .unwrap();
        let bytes = builder.to_bytes().unwrap();
        let unit = unit as u32;
        assert_eq!(native_option(&bytes, "LengthUnit"), unit);
        assert_eq!(native_option(&bytes, "LengthFormat"), 0);
        assert_eq!(native_option(&bytes, "LengthPrecision"), 2);
        assert_eq!(native_option(&bytes, "AreaUnit"), unit);
        assert_eq!(native_option(&bytes, "VolumeUnit"), unit);
        assert_eq!(uint_le(&bytes, 3177, 4), 0xff56_3412); // current rendering background
        assert_eq!(bytes[3206], 0); // profile edges disabled
        assert_eq!(uint_le(&bytes, 3207, 4), 5);
        // Both saved and working styles carry the same item, independently of current options.
        let item = [
            0xa0, 0x0f, 0, 0, 1, 0, 0, 0, 4, 0, 0, 0, 0x12, 0x34, 0x56, 0xff,
        ];
        assert_eq!(count(&bytes, &item), 2);
        let model = parse(bytes);
        assert!(model
            .root()
            .vertices
            .values()
            .any(|v| v.x == 2.0 && v.y == 0.0 && v.z == 0.0));
    }
}

#[test]
fn rejects_invalid_document_options() {
    let mut builder = create();
    let mut options = DocumentOptions {
        length_unit: 6,
        ..Default::default()
    };
    assert!(builder.set_document_options(&options).is_err());
    options.length_unit = 0;
    options.length_precision = 7;
    assert!(builder.set_document_options(&options).is_err());
    options.length_precision = 2;
    options.style_items.insert(1000, f64::NAN);
    assert!(builder.set_document_options(&options).is_err());
}

/// A valid 2x1 RGB PNG.
const PNG: [u8; 72] = [
    0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0, 0, 0, 0x0d, 0x49, 0x48, 0x44, 0x52, 0, 0, 0,
    2, 0, 0, 0, 1, 8, 2, 0, 0, 0, 0x7b, 0x40, 0xe8, 0xdd, 0, 0, 0, 0x0f, 0x49, 0x44, 0x41, 0x54,
    0x78, 0x9c, 0x63, 0xf8, 0xcf, 0xc0, 0xc0, 0xf0, 0x9f, 1, 0, 7, 0xff, 1, 0xff, 1, 0x7f, 0x89,
    0xa7, 0, 0, 0, 0, 0x49, 0x45, 0x4e, 0x44, 0xae, 0x42, 0x60, 0x82,
];

#[test]
fn reads_texture_type_mode_and_paint_independently() {
    let path = std::env::temp_dir().join(format!(
        "openskp_native_material_test_{}.png",
        std::process::id()
    ));
    std::fs::write(&path, PNG).unwrap();
    let original = {
        let mut builder = create();
        let mat = builder
            .add_texture_material("paint", &path, None, None, None)
            .unwrap();
        let mut group = builder.add_group(&GroupOptions::default()).unwrap();
        group
            .add_face(
                &[[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]],
                &FaceOptions::default(),
            )
            .unwrap();
        group.close().unwrap();
        let face = FaceOptions {
            material: Some(mat),
            ..Default::default()
        };
        builder
            .add_face(&[[2.0, 0.0, 0.0], [3.0, 0.0, 0.0], [2.0, 1.0, 0.0]], &face)
            .unwrap();
        builder.to_bytes().unwrap()
    };
    let _ = std::fs::remove_file(&path);

    let image_end = locate(&original, &PNG) + PNG.len();
    // Skip the tile dimensions; then the native CString of the path.
    let s = image_end + 16;
    assert_eq!(&original[s..s + 3], &[0xff, 0xfe, 0xff]);
    assert!(original[s + 3] < 0xff);
    let colors = s + 4 + 2 * usize::from(original[s + 3]);
    let kind_at = colors + 9 + 4; // two colours and an empty CString
    assert_eq!(uint_le(&original, kind_at, 4), 1);
    assert_eq!(uint_le(&original, kind_at + 4, 4), 0);
    for kind in [1u8, 2] {
        for mode in [0u8, 1] {
            let mut bytes = original.clone();
            bytes[colors..colors + 4].copy_from_slice(&[17, 18, 19, 255]);
            bytes[colors + 5..colors + 8].copy_from_slice(&[72, 83, 94]);
            bytes[kind_at] = kind;
            bytes[kind_at + 4] = mode;
            let model = parse(bytes);
            assert_eq!(model.materials.len(), 1);
            let m = &model.materials[0];
            assert_eq!(m.color, [72, 83, 94, 255]);
            assert_eq!(m.colorized, kind == 2);
            assert_eq!(m.colorize_type, i32::from(mode));
        }
    }
}
