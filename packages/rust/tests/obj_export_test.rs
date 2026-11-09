//! Port of `obj_export_test.cpp`.

use openskp::{export_obj, to_mtl, to_obj, GlbPrimitive, GltfMaterial, Scene};

fn cube_scene() -> Scene {
    let mut scene = Scene::default();
    scene.glb_primitives.push(GlbPrimitive {
        geom_name: "Cube".into(),
        material_index: 0,
        positions: vec![0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0],
        normals: vec![0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 0.0, 0.0, 1.0],
        uvs: vec![0.0, 0.0, 1.0, 0.0, 0.0, 1.0],
        indices: vec![0, 1, 2],
    });
    scene
}

#[test]
fn serializes_scene_to_obj_text() {
    let obj = to_obj(&cube_scene(), "");
    assert!(obj.contains("# OpenSKP OBJ Export"));
    assert!(obj.contains("o Cube"));
    assert!(obj.contains("v 0.000000 0.000000 0.000000"));
    // Matching UV and normal counts select the v/vt/vn face form.
    assert!(obj.contains("f 1/1/1 2/2/2 3/3/3"));
}

#[test]
fn full_obj_and_mtl_text() {
    let mut scene = cube_scene();
    let mut mat = GltfMaterial {
        name: "Red Brick".into(),
        texture_path: "tex/brick.jpg".into(),
        ..Default::default()
    };
    mat.pbr_metallic_roughness.base_color_factor = [1.0, 0.5, 0.25, 0.75];
    scene.gltf_materials.push(mat);
    let mut second = scene.glb_primitives[0].clone();
    second.geom_name = "Bare".into();
    second.uvs.clear();
    second.normals.clear();
    scene.glb_primitives.push(second);

    assert_eq!(
        to_obj(&scene, "x.mtl"),
        "# OpenSKP OBJ Export\n# Primitives: 2\nmtllib x.mtl\n\n\
         o Cube\nv 0.000000 0.000000 0.000000\nv 1.000000 0.000000 0.000000\nv 0.000000 1.000000 0.000000\n\
         vt 0.000000 0.000000\nvt 1.000000 0.000000\nvt 0.000000 1.000000\n\
         vn 0.000000 0.000000 1.000000\nvn 0.000000 0.000000 1.000000\nvn 0.000000 0.000000 1.000000\n\
         usemtl Red_Brick\nf 1/1/1 2/2/2 3/3/3\n\n\
         o Bare\nv 0.000000 0.000000 0.000000\nv 1.000000 0.000000 0.000000\nv 0.000000 1.000000 0.000000\n\
         usemtl Red_Brick\nf 4 5 6\n\n"
    );
    assert_eq!(
        to_mtl(&scene),
        "# OpenSKP MTL Material Library Export\n# Materials: 1\n\n\
         newmtl Red_Brick\nKa 1.000000 1.000000 1.000000\nKd 1.000000 0.500000 0.250000\n\
         Ks 0.200000 0.200000 0.200000\nNs 32.000000\nd 0.750000\nillum 2\nmap_Kd brick.jpg\n\n"
    );
}

#[test]
fn export_writes_obj_and_mtl() {
    let dir = std::env::temp_dir()
        .join("openskp-rs-obj-export")
        .join("nested");
    let _ = std::fs::remove_dir_all(&dir);
    let path = dir.join("model.obj");
    let scene = cube_scene();
    export_obj(&scene, &path, true).unwrap();
    assert_eq!(
        std::fs::read_to_string(&path).unwrap(),
        to_obj(&scene, "model.mtl")
    );
    assert_eq!(
        std::fs::read_to_string(dir.join("model.mtl")).unwrap(),
        to_mtl(&scene)
    );
    std::fs::remove_dir_all(&dir).unwrap();
}
