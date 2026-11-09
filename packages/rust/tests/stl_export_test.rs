//! Port of `stl_export_test.cpp`.

use openskp::{export_stl, to_stl_ascii, to_stl_binary, GlbPrimitive, Scene};

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
fn serializes_scene_to_stl_ascii_text() {
    let stl = to_stl_ascii(&box_scene(), 1.0);
    assert!(stl.contains("solid OpenSKP_Model"));
    assert!(stl.contains("facet normal 0.000000 0.000000 1.000000"));
    assert!(stl.contains("vertex 0.000000 0.000000 0.000000"));
    assert!(stl.contains("endsolid OpenSKP_Model"));
}

#[test]
fn ascii_applies_scale() {
    assert_eq!(
        to_stl_ascii(&box_scene(), 1000.0),
        "solid OpenSKP_Model\n  facet normal 0.000000 0.000000 1.000000\n    outer loop\n\
         \x20     vertex 0.000000 0.000000 0.000000\n      vertex 1000.000000 0.000000 0.000000\n\
         \x20     vertex 0.000000 1000.000000 0.000000\n    endloop\n  endfacet\nendsolid OpenSKP_Model\n"
    );
}

#[test]
fn serializes_scene_to_stl_binary_data() {
    let data = to_stl_binary(&box_scene(), 1.0);
    assert_eq!(data.len(), 80 + 4 + 50);
    assert!(data.starts_with(b"# OpenSKP Binary STL Export\0"));
    assert_eq!(u32::from_le_bytes(data[80..84].try_into().unwrap()), 1);
    let f = |i: usize| f32::from_le_bytes(data[84 + i * 4..88 + i * 4].try_into().unwrap());
    assert_eq!([f(0), f(1), f(2)], [0.0, 0.0, 1.0]);
    assert_eq!([f(6), f(7), f(8)], [1.0, 0.0, 0.0]);
}

#[test]
fn export_writes_both_forms() {
    let dir = std::env::temp_dir().join("openskp-rs-stl-export");
    let scene = box_scene();
    export_stl(&scene, dir.join("a.stl"), true, 1.0).unwrap();
    export_stl(&scene, dir.join("b.stl"), false, 1.0).unwrap();
    assert_eq!(
        std::fs::read(dir.join("a.stl")).unwrap(),
        to_stl_binary(&scene, 1.0)
    );
    assert_eq!(
        std::fs::read_to_string(dir.join("b.stl")).unwrap(),
        to_stl_ascii(&scene, 1.0)
    );
    std::fs::remove_dir_all(&dir).unwrap();
}
