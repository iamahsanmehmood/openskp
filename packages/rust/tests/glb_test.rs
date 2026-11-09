//! Port of `glb_test.cpp`.

mod common;
mod gltf_helper;

use gltf_helper::{int, load_glb, numbers, string};
use openskp::{
    export_glb, to_glb, GlbError, GlbOptions, GlbPrimitive, GltfMaterial, PbrMetallicRoughness,
    Scene, SceneTexture, SkpFile,
};

fn material(
    name: &str,
    color: [f64; 4],
    metallic: f64,
    roughness: f64,
    double_sided: bool,
) -> GltfMaterial {
    GltfMaterial {
        name: name.into(),
        texture_path: String::new(),
        pbr_metallic_roughness: PbrMetallicRoughness {
            base_color_factor: color,
            metallic_factor: metallic,
            roughness_factor: roughness,
            base_color_texture: None,
        },
        double_sided,
    }
}

fn flat_triangle() -> GlbPrimitive {
    GlbPrimitive {
        positions: vec![0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0],
        normals: vec![0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 0.0, 0.0, 1.0],
        uvs: vec![0.0, 0.0, 1.0, 0.0, 0.0, 1.0],
        indices: vec![0, 1, 2],
        material_index: 0,
        geom_name: "triangle".into(),
    }
}

fn triangle_scene() -> Scene {
    let mut scene = Scene::default();
    scene.gltf_materials.push(material(
        "Material_0",
        [0.25, 0.5, 0.75, 1.0],
        0.1,
        0.9,
        true,
    ));
    scene.glb_primitives.push(GlbPrimitive {
        positions: vec![1.0, 2.0, 3.0, -4.0, 5.0, 0.0, 2.0, -1.0, 7.0],
        normals: vec![0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 0.0, 0.0, 1.0],
        uvs: vec![0.0, 0.0, 1.0, 0.0, 0.0, 1.0],
        indices: vec![0, 1, 2],
        material_index: 0,
        geom_name: "triangle".into(),
    });
    scene
}

fn glb(scene: &Scene) -> Vec<u8> {
    to_glb(scene, &GlbOptions::default()).expect("to_glb")
}

fn alpha_mode(material: &openskp::JsonValue) -> &str {
    material
        .find("alphaMode")
        .and_then(|v| v.as_str())
        .unwrap_or("OPAQUE")
}

#[test]
fn serializes_scene_and_binary_data() {
    let bytes = glb(&triangle_scene());
    assert!(bytes.len() >= 12);
    assert_eq!(&bytes[..4], b"glTF");

    let model = load_glb(&bytes);
    assert_eq!(int(&model.json, "scene"), 0);
    let scenes = model.list("scenes");
    assert_eq!(scenes.len(), 1);
    assert_eq!(numbers(&scenes[0], "nodes"), vec![0.0]);
    assert_eq!(model.list("nodes").len(), 1);
    assert_eq!(int(&model.list("nodes")[0], "mesh"), 0);
    let meshes = model.list("meshes");
    assert_eq!(meshes.len(), 1);
    let prims = meshes[0].find("primitives").unwrap().as_array().unwrap();
    assert_eq!(prims.len(), 1);

    let primitive = &prims[0];
    assert_eq!(int(primitive, "mode"), 4);
    assert_eq!(int(primitive, "material"), 0);
    let attrs = primitive.find("attributes").unwrap();

    let positions = model.accessor(int(attrs, "POSITION"));
    assert_eq!(int(positions, "componentType"), 5126);
    assert_eq!(string(positions, "type"), "VEC3");
    assert_eq!(int(positions, "count"), 3);
    assert_eq!(numbers(positions, "min"), vec![-4.0, -1.0, 0.0]);
    assert_eq!(numbers(positions, "max"), vec![2.0, 5.0, 7.0]);
    assert_eq!(model.f32_at(positions, 0), 1.0);
    assert_eq!(model.f32_at(positions, 4), 5.0);

    let normals = model.accessor(int(attrs, "NORMAL"));
    assert_eq!(int(normals, "count"), 3);
    assert_eq!(model.f32_at(normals, 8), 1.0);

    let uvs = model.accessor(int(attrs, "TEXCOORD_0"));
    assert_eq!(int(uvs, "componentType"), 5126);
    assert_eq!(string(uvs, "type"), "VEC2");
    assert_eq!(int(uvs, "count"), 3);
    assert_eq!(model.f32_at(uvs, 2), 1.0);

    let indices = model.accessor(int(primitive, "indices"));
    assert_eq!(int(indices, "componentType"), 5125);
    assert_eq!(string(indices, "type"), "SCALAR");
    assert_eq!(int(indices, "count"), 3);
    assert_eq!(model.u32_at(indices, 2), 2);

    let materials = model.list("materials");
    assert_eq!(materials.len(), 1);
    let pbr = materials[0].find("pbrMetallicRoughness").unwrap();
    assert_eq!(numbers(pbr, "baseColorFactor"), vec![0.25, 0.5, 0.75, 1.0]);
    assert_eq!(pbr.find("metallicFactor").unwrap().as_double(), Some(0.1));
    assert_eq!(pbr.find("roughnessFactor").unwrap().as_double(), Some(0.9));
    assert_eq!(alpha_mode(&materials[0]), "OPAQUE");
    assert_eq!(
        materials[0].find("doubleSided").unwrap().as_bool(),
        Some(true)
    );
}

/// The exact document TinyGLTF serializes for this scene (sorted keys,
/// default values omitted, nlohmann number formatting) - pins the writer to
/// the C++ port's byte layout.
#[test]
fn json_chunk_matches_tinygltf_layout() {
    let model = load_glb(&glb(&triangle_scene()));
    let expected = concat!(
        r#"{"accessors":[{"bufferView":0,"componentType":5126,"count":3,"max":[2.0,5.0,7.0],"min":[-4.0,-1.0,0.0],"type":"VEC3"},"#,
        r#"{"bufferView":1,"componentType":5126,"count":3,"type":"VEC3"},"#,
        r#"{"bufferView":2,"componentType":5126,"count":3,"type":"VEC2"},"#,
        r#"{"bufferView":3,"componentType":5125,"count":3,"type":"SCALAR"}],"#,
        r#""asset":{"generator":"OpenSKP","version":"2.0"},"#,
        r#""bufferViews":[{"buffer":0,"byteLength":36,"target":34962},{"buffer":0,"byteLength":36,"byteOffset":36,"target":34962},"#,
        r#"{"buffer":0,"byteLength":24,"byteOffset":72,"target":34962},{"buffer":0,"byteLength":12,"byteOffset":96,"target":34963}],"#,
        r#""buffers":[{"byteLength":108}],"#,
        r#""materials":[{"doubleSided":true,"pbrMetallicRoughness":{"baseColorFactor":[0.25,0.5,0.75,1.0],"metallicFactor":0.1,"roughnessFactor":0.9}}],"#,
        r#""meshes":[{"name":"OpenSKP Mesh","primitives":[{"attributes":{"NORMAL":1,"POSITION":0,"TEXCOORD_0":2},"indices":3,"material":0,"mode":4}]}],"#,
        r#""nodes":[{"mesh":0,"name":"OpenSKP Scene"}],"scene":0,"scenes":[{"name":"OpenSKP Scene","nodes":[0]}]}"#
    );
    assert_eq!(model.json_text.trim_end_matches(' '), expected);
    assert_eq!(model.bin.len(), 108);
}

#[test]
fn declares_blend_alpha_mode_for_translucent_materials() {
    let mut scene = Scene::default();
    scene
        .gltf_materials
        .push(material("Glass", [0.16, 0.27, 0.39, 0.5], 0.0, 0.8, false));
    scene.glb_primitives.push(flat_triangle());
    let model = load_glb(&glb(&scene));
    assert_eq!(model.list("materials").len(), 1);
    assert_eq!(alpha_mode(&model.list("materials")[0]), "BLEND");
}

#[test]
fn declares_mask_alpha_mode_for_textured_opaque_materials() {
    // A textured material with an opaque base color needs MASK, or a
    // renderer ignores the image's own cutout alpha.
    let mut scene = Scene::default();
    let mut mat = material("Leaf", [0.2, 0.5, 0.1, 1.0], 0.0, 0.8, false);
    mat.pbr_metallic_roughness.base_color_texture = Some(0);
    scene.gltf_materials.push(mat);
    scene.textures.push(SceneTexture {
        data: vec![0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a],
        mime_type: "image/png".into(),
        filename: "leaf.png".into(),
    });
    scene.glb_primitives.push(flat_triangle());

    let model = load_glb(&glb(&scene));
    assert_eq!(model.list("materials").len(), 1);
    assert_eq!(alpha_mode(&model.list("materials")[0]), "MASK");
    // Not embedding: no texture reference, no images.
    assert!(model.list("materials")[0]
        .find("pbrMetallicRoughness")
        .unwrap()
        .find("baseColorTexture")
        .is_none());
    assert!(model.list("images").is_empty());

    let embedded = load_glb(&to_glb(&scene, &GlbOptions { textures: true }).unwrap());
    let images = embedded.list("images");
    assert_eq!(images.len(), 1);
    assert_eq!(string(&images[0], "mimeType"), "image/png");
    let view = &embedded.list("bufferViews")[int(&images[0], "bufferView") as usize];
    assert!(view.find("target").is_none());
    let off = int(view, "byteOffset") as usize;
    assert_eq!(&embedded.bin[off..off + 8], &scene.textures[0].data[..]);
    assert_eq!(int(&embedded.list("textures")[0], "source"), 0);
    assert_eq!(int(&embedded.list("samplers")[0], "wrapS"), 10497);
    let info = embedded.list("materials")[0]
        .find("pbrMetallicRoughness")
        .unwrap()
        .find("baseColorTexture")
        .unwrap();
    assert_eq!(int(info, "index"), 0);
}

#[test]
fn serializes_an_empty_scene() {
    let model = load_glb(&glb(&Scene::default()));
    let scenes = model.list("scenes");
    assert_eq!(scenes.len(), 1);
    assert!(scenes[0].find("nodes").is_none());
    assert!(model.list("meshes").is_empty());
    assert!(model.list("buffers").is_empty());
    assert!(model.bin.is_empty());
}

fn assert_invalid(scene: &Scene) {
    match to_glb(scene, &GlbOptions::default()) {
        Err(GlbError::InvalidArgument(_)) => {}
        other => panic!("expected InvalidArgument, got {other:?}"),
    }
}

#[test]
fn rejects_malformed_geometry() {
    let mut scene = triangle_scene();
    scene.glb_primitives[0].positions.pop();
    assert_invalid(&scene);

    let mut scene = triangle_scene();
    scene.glb_primitives[0].normals.pop();
    assert_invalid(&scene);

    let mut scene = triangle_scene();
    scene.glb_primitives[0].uvs.pop();
    assert_invalid(&scene);

    let mut scene = triangle_scene();
    scene.glb_primitives[0].indices.pop();
    assert_invalid(&scene);

    let mut scene = triangle_scene();
    scene.glb_primitives[0].indices[2] = 3;
    assert_invalid(&scene);

    let mut scene = triangle_scene();
    scene.glb_primitives[0].material_index = 1;
    assert_invalid(&scene);
}

#[test]
fn rejects_non_finite_and_invalid_pbr_values() {
    let mut scene = triangle_scene();
    scene.glb_primitives[0].positions[0] = f32::INFINITY;
    assert_invalid(&scene);

    let mut scene = triangle_scene();
    scene.glb_primitives[0].normals[0] = f32::NAN;
    assert_invalid(&scene);

    let mut scene = triangle_scene();
    scene.glb_primitives[0].uvs[0] = f32::NAN;
    assert_invalid(&scene);

    let mut scene = triangle_scene();
    scene.gltf_materials[0]
        .pbr_metallic_roughness
        .base_color_factor[0] = -0.1;
    assert_invalid(&scene);

    let mut scene = triangle_scene();
    scene.gltf_materials[0]
        .pbr_metallic_roughness
        .metallic_factor = 1.1;
    assert_invalid(&scene);

    let mut scene = triangle_scene();
    scene.gltf_materials[0]
        .pbr_metallic_roughness
        .roughness_factor = f64::NAN;
    assert_invalid(&scene);
}

#[test]
fn export_writes_the_same_bytes() {
    let scene = triangle_scene();
    let expected = glb(&scene);
    let output = std::env::temp_dir().join("openskp-rs-glb-hand-scene.glb");
    export_glb(&scene, &output, &GlbOptions::default()).unwrap();
    let actual = std::fs::read(&output).unwrap();
    std::fs::remove_file(&output).unwrap();
    assert_eq!(actual, expected);
}

#[test]
fn exports_real_fixture_byte_for_byte() {
    let scene = SkpFile::open(common::fixture("SU_File.skp"))
        .unwrap()
        .build_scene(&Default::default())
        .unwrap();
    let expected = glb(&scene);
    let output = std::env::temp_dir().join("openskp-rs-export-test.bin");
    export_glb(&scene, &output, &GlbOptions::default()).unwrap();
    let actual = std::fs::read(&output).unwrap();
    std::fs::remove_file(&output).unwrap();
    assert_eq!(actual, expected);
    let model = load_glb(&actual);
    let prims = model.list("meshes")[0]
        .find("primitives")
        .unwrap()
        .as_array()
        .unwrap();
    assert_eq!(prims.len(), scene.glb_primitives.len());
}

#[test]
fn reports_file_failures_without_creating_directories() {
    let output = std::env::temp_dir()
        .join("openskp-rs-missing-parent")
        .join("asset.any-extension");
    assert!(matches!(
        export_glb(&triangle_scene(), &output, &GlbOptions::default()),
        Err(GlbError::Io(_))
    ));
    assert!(!output.parent().unwrap().exists());
}
