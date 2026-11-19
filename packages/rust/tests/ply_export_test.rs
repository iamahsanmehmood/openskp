//! Port of `ply_export_test.cpp`.

use openskp::{export_ply, to_ply_ascii, to_ply_binary, GlbPrimitive, GltfMaterial, Scene};

fn box_scene() -> Scene {
    let mut scene = Scene::default();
    scene.glb_primitives.push(GlbPrimitive {
        geom_name: "Box".into(),
        material_index: 0,
        positions: vec![0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0],
        normals: vec![0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 0.0, 0.0, 1.0],
        uvs: vec![0.0, 0.0, 1.0, 0.0, 0.0, 1.0],
        indices: vec![0, 1, 2],
    });
    scene
}

#[test]
fn serializes_scene_to_ply_ascii_text() {
    let ply = to_ply_ascii(&box_scene());
    assert!(ply.contains("format ascii 1.0"));
    assert!(ply.contains("element vertex 3"));
    assert!(ply.contains("element face 1"));
    assert!(ply.contains("3 0 1 2"));
    // No material: light grey.
    assert!(ply.contains(
        "0.000000 0.000000 0.000000 0.000000 0.000000 1.000000 0.000000 0.000000 200 200 200 255\n"
    ));
}

#[test]
fn colors_come_from_the_material_and_offsets_accumulate() {
    let mut scene = box_scene();
    let mut mat = GltfMaterial::default();
    mat.pbr_metallic_roughness.base_color_factor = [1.0, 0.5, 0.0, 0.25];
    scene.gltf_materials.push(mat);
    scene.glb_primitives.push(scene.glb_primitives[0].clone());
    let ply = to_ply_ascii(&scene);
    assert!(ply.contains("element vertex 6\n"));
    assert!(ply.contains(" 255 128 0 64\n"));
    assert!(ply.ends_with("3 0 1 2\n3 3 4 5\n"));
}

#[test]
fn serializes_scene_to_ply_binary_data() {
    let data = to_ply_binary(&box_scene());
    let text = String::from_utf8_lossy(&data);
    assert!(text.contains("format binary_little_endian 1.0"));
    assert!(text.contains("element vertex 3"));
    assert!(text.contains("element face 1"));
    let header_len = text.find("end_header\n").unwrap() + "end_header\n".len();
    assert_eq!(data.len(), header_len + 3 * 36 + 13);
    assert_eq!(
        &data[data.len() - 13..],
        &[3, 0, 0, 0, 0, 1, 0, 0, 0, 2, 0, 0, 0]
    );
}

#[test]
fn export_writes_both_forms() {
    let dir = std::env::temp_dir().join("openskp-rs-ply-export");
    let scene = box_scene();
    export_ply(&scene, dir.join("a.ply"), true).unwrap();
    export_ply(&scene, dir.join("b.ply"), false).unwrap();
    assert_eq!(
        std::fs::read(dir.join("a.ply")).unwrap(),
        to_ply_binary(&scene)
    );
    assert_eq!(
        std::fs::read_to_string(dir.join("b.ply")).unwrap(),
        to_ply_ascii(&scene)
    );
    std::fs::remove_dir_all(&dir).unwrap();
}
