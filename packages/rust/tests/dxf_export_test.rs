// DXF export (`dxf_export_test.cpp`, plus the Python port's `test_to_dxf`),
// and byte-identity checks of whole files against reference output.

use std::collections::BTreeSet;

use openskp::{to_dxf, DxfError, GlbPrimitive, GltfMaterial, Scene, METRES_TO_INCHES};

fn prim(name: &str, material_index: usize, positions: &[f32], indices: &[u32]) -> GlbPrimitive {
    let n = positions.len() / 3;
    GlbPrimitive {
        positions: positions.to_vec(),
        normals: [0.0, 0.0, 1.0].repeat(n),
        uvs: [0.0, 0.0].repeat(n),
        indices: indices.to_vec(),
        material_index,
        geom_name: name.to_string(),
    }
}

fn material(rgba: [f64; 4]) -> GltfMaterial {
    let mut mat = GltfMaterial::default();
    mat.pbr_metallic_roughness.base_color_factor = rgba;
    mat
}

fn walls_scene() -> Scene {
    Scene {
        glb_primitives: vec![prim(
            "Walls",
            0,
            &[0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0],
            &[0, 1, 2],
        )],
        ..Scene::default()
    }
}

#[test]
fn serializes_scene_to_3d_dxf_text() {
    let scene = walls_scene();

    let dxf_text = to_dxf(&scene, METRES_TO_INCHES, "polyface").unwrap();
    assert!(dxf_text.contains("$ACADVER"));
    assert!(dxf_text.contains("AC1015"));
    assert!(dxf_text.contains("POLYLINE"));
    assert!(dxf_text.contains("AcDbPolyFaceMesh"));
    assert!(dxf_text.contains("Walls"));
    assert!(dxf_text.contains("EOF"));

    let dxf_3d = to_dxf(&scene, METRES_TO_INCHES, "3dface").unwrap();
    assert!(dxf_3d.contains("3DFACE"));
    assert!(dxf_3d.contains("AcDbFace"));
}

#[test]
fn every_line_ends_in_crlf_and_the_handseed_is_filled_in() {
    let text = to_dxf(&walls_scene(), METRES_TO_INCHES, "polyface").unwrap();
    assert!(text.ends_with("EOF\r\n"));
    assert_eq!(text.matches('\n').count(), text.matches("\r\n").count());
    assert!(!text.contains("__HANDSEED__"));
    // One layer handle (0x691), then POLYLINE + 3 vertices + 1 face record +
    // SEQEND: the next free handle is 0x698, and $HANDSEED is 0x10 past it.
    let lines: Vec<&str> = text.split("\r\n").collect();
    let seed = lines.iter().position(|l| *l == "$HANDSEED").unwrap();
    assert_eq!(lines[seed + 1], "  5");
    assert_eq!(lines[seed + 2], "6A8");
}

#[test]
fn polyface_vertices_are_scaled_and_faces_are_1_based() {
    let scene = Scene {
        glb_primitives: vec![prim(
            "P",
            0,
            &[0.5, -0.25, 2.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0],
            &[2, 0, 1],
        )],
        ..Scene::default()
    };
    let text = to_dxf(&scene, 2.0, "polyface").unwrap();
    assert!(
        text.contains(" 10\r\n1.000000\r\n 20\r\n-0.500000\r\n 30\r\n4.000000\r\n 70\r\n192\r\n")
    );
    assert!(text.contains(" 71\r\n3\r\n 72\r\n1\r\n 73\r\n2\r\n"));
    // 71/72 on the POLYLINE itself: vertex and face counts.
    assert!(text.contains(" 70\r\n64\r\n 71\r\n3\r\n 72\r\n1\r\n"));
}

#[test]
fn a_scene_without_primitives_has_only_the_built_in_layers() {
    let text = to_dxf(&Scene::default(), METRES_TO_INCHES, "polyface").unwrap();
    let parsed = inspect(&text);
    assert_eq!(
        parsed.layers,
        BTreeSet::from(["0".into(), "Defpoints".into()])
    );
    assert!(!text.contains("POLYLINE"));
}

// ---------------------------------------------------------------------------
// Byte identity against the C++ port (verified to open in desktop AutoCAD).
// Lengths and CRC-32s are of `openskp::to_dxf` output from the C++ at
// 6c37b07 (#499: Unicode-escaped, unique layer names and validated meshes),
// built with g++ 11 on the identical scenes.
// ---------------------------------------------------------------------------

#[test]
fn each_entity_takes_its_own_primitives_color_not_its_layers() {
    let scene = Scene {
        glb_primitives: vec![
            prim(
                "Walls",
                0,
                &[0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0],
                &[0, 1, 2],
            ),
            prim(
                "Walls",
                1,
                &[0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0],
                &[0, 1, 2],
            ),
        ],
        gltf_materials: vec![
            material([1.0, 0.0, 0.0, 1.0]),
            material([0.0, 1.0, 1.0, 1.0]),
        ],
        ..Scene::default()
    };
    let text = to_dxf(&scene, 1.0, "3dface").unwrap();
    // The layer takes the first primitive's color (red, ACI 1)...
    assert!(text.contains("AcDbLayerTableRecord\r\n  2\r\nWalls\r\n 70\r\n0\r\n 62\r\n1\r\n"));
    // ...and each 3DFACE its own: red, then cyan (ACI 4).
    let faces: Vec<&str> = text.split("3DFACE\r\n").skip(1).collect();
    assert_eq!(faces.len(), 2);
    assert!(faces[0].contains("  8\r\nWalls\r\n 62\r\n1\r\n"));
    assert!(faces[1].contains("  8\r\nWalls\r\n 62\r\n4\r\n"));
}

fn mixed_scene() -> Scene {
    Scene {
        glb_primitives: vec![
            prim(
                "Walls",
                0,
                &[0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0],
                &[0, 1, 2],
            ),
            prim(
                "Door<1>: \"main\"",
                1,
                &[
                    0.123_456_79,
                    -2.5,
                    1e-7,
                    -1e-7,
                    3.25,
                    7.0,
                    10.0,
                    0.333333,
                    -0.75,
                    5.5,
                    5.5,
                    5.5,
                ],
                &[0, 1, 2, 2, 3, 0],
            ),
            prim(
                "  ",
                7,
                &[1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0],
                &[2, 1, 0],
            ),
            prim("Empty", 1, &[1.0, 2.0, 3.0], &[]),
            prim(
                "Walls",
                0,
                &[9.0, 9.0, 9.0, 8.0, 8.0, 8.0, 7.0, 7.0, 7.0],
                &[0, 1, 2],
            ),
        ],
        gltf_materials: vec![
            material([0.8, 0.2, 0.2, 1.0]),
            material([0.1, 0.9, 0.95, 0.9]),
        ],
        ..Scene::default()
    }
}

fn assert_bytes(name: &str, text: &str, len: usize, crc: u32) {
    let actual_crc = openskp::zip::crc32(text.as_bytes());
    if text.len() != len || actual_crc != crc {
        let path = std::env::temp_dir().join(format!("openskp_dxf_{name}.dxf"));
        let _ = std::fs::write(&path, text);
        panic!(
            "{name}: {} bytes, CRC-32 {actual_crc:#010X}; expected {len} bytes, {crc:#010X} (output saved to {})",
            text.len(),
            path.display()
        );
    }
}

#[test]
fn an_empty_scene_matches_the_reference_bytes() {
    assert_bytes(
        "empty_polyface",
        &to_dxf(&Scene::default(), METRES_TO_INCHES, "polyface").unwrap(),
        16676,
        0xFDEA_A351,
    );
}

#[test]
fn polyface_output_matches_the_reference_bytes() {
    assert_bytes(
        "mixed_polyface",
        &to_dxf(&mixed_scene(), METRES_TO_INCHES, "polyface").unwrap(),
        21051,
        0x1325_7F1B,
    );
}

#[test]
fn three_d_face_output_matches_the_reference_bytes() {
    assert_bytes(
        "mixed_3dface",
        &to_dxf(&mixed_scene(), METRES_TO_INCHES, "3dface").unwrap(),
        18559,
        0xFDD7_AEEA,
    );
}

#[test]
fn a_custom_scale_matches_the_reference_bytes() {
    assert_bytes(
        "mixed_polyface_mm",
        &to_dxf(&mixed_scene(), 1000.0, "polyface").unwrap(),
        21088,
        0xF7D0_872C,
    );
}

#[test]
fn export_dxf_writes_the_same_bytes_to_a_file() {
    let dir = std::env::temp_dir().join(format!("openskp_dxf_export_{}", std::process::id()));
    let path = dir.join("nested").join("walls.dxf");
    openskp::export_dxf(&walls_scene(), &path, METRES_TO_INCHES, "polyface").unwrap();
    let written = std::fs::read(&path).unwrap();
    assert_eq!(
        written,
        to_dxf(&walls_scene(), METRES_TO_INCHES, "polyface")
            .unwrap()
            .into_bytes()
    );
    let _ = std::fs::remove_dir_all(&dir);
}

// ---------------------------------------------------------------------------
// Unicode layer names, unique layers and malformed meshes - port of
// `dxf_unicode_safety_test.cpp` (#499). Its locale test has no counterpart:
// Rust formatting never reads the process locale. Nor does its malformed
// UTF-8 test: a `GlbPrimitive::geom_name` is a `String`, always valid UTF-8.
// ---------------------------------------------------------------------------

fn triangle(name: &str) -> GlbPrimitive {
    prim(
        name,
        0,
        &[0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0],
        &[0, 1, 2],
    )
}

struct Parsed {
    layers: BTreeSet<String>,
    mesh_layers: Vec<String>,
}

/// Reads `dxf` back and checks what AutoCAD needs of it: ASCII only, 16-bit
/// fields in range, unique LAYER names that decode to 1-255 UTF-16 units
/// with no split surrogate pair, a correct LAYER table count, and every
/// entity on a layer that exists.
fn inspect(dxf: &str) -> Parsed {
    assert!(
        dxf.is_ascii(),
        "R2000 DXF contains raw non-ASCII bytes instead of Unicode escapes"
    );
    let lines: Vec<&str> = dxf.strip_suffix("\r\n").unwrap().split("\r\n").collect();
    assert!(
        lines.len().is_multiple_of(2),
        "incomplete DXF code/value pair"
    );
    let mut parsed = Parsed {
        layers: BTreeSet::new(),
        mesh_layers: Vec::new(),
    };
    let mut references = Vec::new();
    let mut record = "";
    let mut table_count = 0usize;
    let mut layer_table = false;
    let mut entity = false;
    for pair in lines.chunks(2) {
        let group: i32 = pair[0].trim().parse().unwrap();
        let value = pair[1];
        if (60..=79).contains(&group) {
            let v: i64 = value.parse().unwrap();
            assert!((-32768..=32767).contains(&v), "16-bit DXF field overflow");
        }
        if group == 0 {
            record = value;
            entity = false;
        }
        if group == 100 && value == "AcDbEntity" {
            entity = true;
        }
        if record == "TABLE" && group == 2 {
            layer_table = value == "LAYER";
        }
        if record == "TABLE" && layer_table && group == 70 {
            table_count = value.parse().unwrap();
        }
        if record == "LAYER" && group == 2 {
            assert!(
                parsed.layers.insert(value.to_string()),
                "duplicate LAYER {value}"
            );
            assert!(
                value.len() <= 2049,
                "encoded layer exceeds R2000 string limit"
            );
            let mut decoded: Vec<u16> = Vec::new();
            let mut rest = value;
            while let Some(c) = rest.chars().next() {
                if c == '\\' {
                    assert!(
                        rest.starts_with("\\U+") && rest.len() >= 7,
                        "bad Unicode escape"
                    );
                    decoded.push(u16::from_str_radix(&rest[3..7], 16).unwrap());
                    rest = &rest[7..];
                } else {
                    assert!(c >= ' ' && c != '\x7F', "control character in LAYER");
                    decoded.push(c as u16);
                    rest = &rest[1..];
                }
            }
            assert!(
                !decoded.is_empty() && decoded.len() <= 255,
                "invalid logical layer name length"
            );
            assert!(
                char::decode_utf16(decoded.iter().copied()).all(|c| c.is_ok()),
                "split or unpaired Unicode surrogate in {value}"
            );
        }
        if group == 8 && entity {
            references.push(value.to_string());
            if record == "POLYLINE" || record == "3DFACE" {
                parsed.mesh_layers.push(value.to_string());
            }
        }
    }
    assert_eq!(
        table_count,
        parsed.layers.len(),
        "incorrect LAYER table count"
    );
    for r in &references {
        assert!(
            parsed.layers.contains(r),
            "entity refers to missing layer {r}"
        );
    }
    parsed
}

const CHINESE: &str = "mesh_27_ROOT__\u{4e0a}\u{67dc}(\u{4fa7}\u{5305}\u{9876}\u{5e95})__\u{9876}\u{677f}_\u{540a}\u{67dc}";

/// The valid-UTF-8 names of the C++ test, plus a long ASCII name.
fn unicode_scene() -> Scene {
    let long_name = "\u{67dc}".repeat(260);
    let names = [
        CHINESE.to_string(),
        "Caf\u{e9}".into(),
        "\u{67dc}\u{1f600}".into(),
        format!("{long_name}A"),
        format!("{long_name}B"),
        format!("{}\u{1f600}{}", "x".repeat(245), "x".repeat(20)),
        "A/B".into(),
        "A:B".into(),
        "walls".into(),
        "WALLS".into(),
        "0".into(),
        "Defpoints".into(),
        String::new(),
        "\t ".into(),
        "line\nbreak\tname".into(),
        CHINESE.to_string(),
        "y".repeat(300),
    ];
    Scene {
        glb_primitives: names.iter().map(|n| triangle(n)).collect(),
        ..Scene::default()
    }
}

#[test]
fn unicode_names_are_escaped_unique_and_match_the_reference_bytes() {
    let scene = unicode_scene();
    let escaped = "mesh_27_ROOT__\\U+4E0A\\U+67DC(\\U+4FA7\\U+5305\\U+9876\\U+5E95)__\\U+9876\\U+677F_\\U+540A\\U+67DC";
    for mode in ["polyface", "3dface"] {
        let parsed = inspect(&to_dxf(&scene, 1.0, mode).unwrap());
        assert!(
            parsed.layers.contains(escaped),
            "Chinese layer name not preserved"
        );
        assert!(
            parsed.layers.contains("Caf\\U+00E9"),
            "accented layer name not preserved"
        );
        assert!(
            parsed.layers.contains("\\U+67DC\\U+D83D\\U+DE00"),
            "supplementary Unicode character lost"
        );
        assert_eq!(
            parsed.mesh_layers.len(),
            scene.glb_primitives.len(),
            "missing geometry"
        );
        assert_eq!(
            parsed.mesh_layers[0], parsed.mesh_layers[15],
            "same source name got different layers"
        );
        let mut unique = parsed.mesh_layers.clone();
        unique.remove(15);
        let keys: BTreeSet<String> = unique.iter().map(|n| n.to_ascii_lowercase()).collect();
        assert_eq!(
            keys.len(),
            unique.len(),
            "distinct names collided after sanitization/truncation"
        );
    }
    assert_bytes(
        "unicode_polyface",
        &to_dxf(&scene, 1.0, "polyface").unwrap(),
        62908,
        0x5613_16A9,
    );
    assert_bytes(
        "unicode_3dface",
        &to_dxf(&scene, 1.0, "3dface").unwrap(),
        31833,
        0x89DC_D865,
    );
}

#[test]
fn a_primitive_without_triangles_adds_no_layer() {
    let mut unused = triangle("Unused");
    unused.indices.clear();
    let scene = Scene {
        glb_primitives: vec![unused],
        ..Scene::default()
    };
    let parsed = inspect(&to_dxf(&scene, 1.0, "polyface").unwrap());
    assert_eq!(parsed.layers.len(), 2);
}

#[test]
fn meshes_past_polyfaces_16_bit_limits_fall_back_to_3dface() {
    let mut positions = vec![0.0f32; 32768 * 3];
    positions[3] = 1.0;
    positions[7] = 1.0;
    let large = prim("Large mesh", 0, &positions, &[0, 1, 32767]);
    let many = prim(
        "Many faces",
        0,
        &[0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0],
        &[0, 1, 2].repeat(32768),
    );
    let scene = Scene {
        glb_primitives: vec![large, many, triangle("Fits")],
        ..Scene::default()
    };
    let dxf = to_dxf(&scene, 1.0, "polyface").unwrap();
    let parsed = inspect(&dxf);
    // One 3DFACE for the large mesh, 32768 for the many faces, and one
    // POLYLINE for the triangle that fits.
    assert_eq!(parsed.mesh_layers.len(), 1 + 32768 + 1);
    assert_eq!(dxf.matches("\r\nPOLYLINE\r\n").count(), 1);
    assert_bytes("limits_polyface", &dxf, 8_863_208, 0x1445_729A);
}

#[test]
fn malformed_meshes_and_scales_are_rejected() {
    type Breaker = fn(&mut GlbPrimitive);
    let cases: [(&str, Breaker, f64, DxfError); 5] = [
        (
            "short positions",
            |p| {
                p.positions.pop();
            },
            1.0,
            DxfError::IncompleteMesh,
        ),
        (
            "short indices",
            |p| {
                p.indices.pop();
            },
            1.0,
            DxfError::IncompleteMesh,
        ),
        (
            "index past the vertices",
            |p| p.indices[0] = 3,
            1.0,
            DxfError::IndexOutOfRange,
        ),
        (
            "NaN coordinate",
            |p| p.positions[0] = f32::NAN,
            1.0,
            DxfError::NonFiniteCoordinate,
        ),
        (
            "infinite scale",
            |_| {},
            f64::INFINITY,
            DxfError::NonFiniteScale,
        ),
    ];
    for (what, break_it, scale, expected) in cases {
        let mut bad = triangle("Invalid mesh");
        break_it(&mut bad);
        let scene = Scene {
            glb_primitives: vec![bad],
            ..Scene::default()
        };
        for mode in ["polyface", "3dface"] {
            assert_eq!(
                to_dxf(&scene, scale, mode),
                Err(expected),
                "{what} ({mode})"
            );
        }
    }
    // A finite coordinate that overflows once scaled is rejected too.
    let mut huge = triangle("Huge");
    huge.positions[3] = 2.0;
    let scene = Scene {
        glb_primitives: vec![huge],
        ..Scene::default()
    };
    assert!(matches!(
        to_dxf(&scene, f64::MAX, "3dface"),
        Err(DxfError::NonFiniteCoordinate)
    ));
    let path = std::env::temp_dir().join("openskp_dxf_never_written.dxf");
    let err = openskp::export_dxf(&scene, &path, f64::NAN, "3dface").unwrap_err();
    assert_eq!(err.kind(), std::io::ErrorKind::InvalidInput);
    assert!(!path.exists());
}
