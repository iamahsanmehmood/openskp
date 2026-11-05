// DXF export (`dxf_export_test.cpp`, plus the Python port's `test_to_dxf`),
// and byte-identity checks of whole files against reference output.

use openskp::{to_dxf, GlbPrimitive, GltfMaterial, Scene, METRES_TO_INCHES};

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

    let dxf_text = to_dxf(&scene, METRES_TO_INCHES, "polyface");
    assert!(dxf_text.contains("$ACADVER"));
    assert!(dxf_text.contains("AC1015"));
    assert!(dxf_text.contains("POLYLINE"));
    assert!(dxf_text.contains("AcDbPolyFaceMesh"));
    assert!(dxf_text.contains("Walls"));
    assert!(dxf_text.contains("EOF"));

    let dxf_3d = to_dxf(&scene, METRES_TO_INCHES, "3dface");
    assert!(dxf_3d.contains("3DFACE"));
    assert!(dxf_3d.contains("AcDbFace"));
}

#[test]
fn every_line_ends_in_crlf_and_the_handseed_is_filled_in() {
    let text = to_dxf(&walls_scene(), METRES_TO_INCHES, "polyface");
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
    let text = to_dxf(&scene, 2.0, "polyface");
    assert!(
        text.contains(" 10\r\n1.000000\r\n 20\r\n-0.500000\r\n 30\r\n4.000000\r\n 70\r\n192\r\n")
    );
    assert!(text.contains(" 71\r\n3\r\n 72\r\n1\r\n 73\r\n2\r\n"));
    // 71/72 on the POLYLINE itself: vertex and face counts.
    assert!(text.contains(" 70\r\n64\r\n 71\r\n3\r\n 72\r\n1\r\n"));
}

#[test]
fn a_scene_without_primitives_still_has_layer_0() {
    let text = to_dxf(&Scene::default(), METRES_TO_INCHES, "polyface");
    assert!(text.contains("AcDbLayerTableRecord\r\n  2\r\n0\r\n 70\r\n0\r\n 62\r\n9\r\n"));
    assert!(!text.contains("POLYLINE"));
}

// ---------------------------------------------------------------------------
// Byte identity. The C++ output was verified to open in desktop AutoCAD; the
// Python port writes the same scaffold and the same entities, so its output
// for these scenes is the reference. Lengths and CRC-32s were taken from
// `openskp.export.dxf.to_dxf` (Python 1.4.0) on the identical scenes. The
// scenes stay clear of the two places the ports differ: exact .5 rounding
// ties (Python's round() vs C++'s std::round()), and a layer whose
// primitives have different colors (Python colors every entity with its
// layer's color, C++ with its own primitive's - see below).
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
    let text = to_dxf(&scene, 1.0, "3dface");
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
        &to_dxf(&Scene::default(), METRES_TO_INCHES, "polyface"),
        16820,
        0xD04B_991E,
    );
}

#[test]
fn polyface_output_matches_the_reference_bytes() {
    assert_bytes(
        "mixed_polyface",
        &to_dxf(&mixed_scene(), METRES_TO_INCHES, "polyface"),
        21136,
        0x998B_911C,
    );
}

#[test]
fn three_d_face_output_matches_the_reference_bytes() {
    assert_bytes(
        "mixed_3dface",
        &to_dxf(&mixed_scene(), METRES_TO_INCHES, "3dface"),
        18689,
        0xA1DD_F9B9,
    );
}

#[test]
fn a_custom_scale_matches_the_reference_bytes() {
    assert_bytes(
        "mixed_polyface_mm",
        &to_dxf(&mixed_scene(), 1000.0, "polyface"),
        21173,
        0x8FAC_0B5E,
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
        to_dxf(&walls_scene(), METRES_TO_INCHES, "polyface").into_bytes()
    );
    let _ = std::fs::remove_dir_all(&dir);
}
