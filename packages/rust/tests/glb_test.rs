//! Port of `glb_test.cpp`.

mod common;
mod gltf_helper;

use gltf_helper::{int, load_glb, numbers, string};
use openskp::{
    export_glb, to_glb, to_instanced_glb, GlbError, GlbOptions, GlbPrimitive, GltfMaterial,
    InstancedGlbOptions, InstancedMeshResource, InstancedNode, InstancedScene, LocalPrimitive,
    PbrMetallicRoughness, Scene, SceneTexture, SkpFile,
};

fn srgb_to_linear(c: f64) -> f64 {
    if c <= 0.04045 {
        c / 12.92
    } else {
        ((c + 0.055) / 1.055).powf(2.4)
    }
}

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
        ..Default::default()
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
        unpainted_back: false,
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
        unpainted_back: false,
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
    let bcf = numbers(pbr, "baseColorFactor");
    assert!((bcf[0] - srgb_to_linear(0.25)).abs() < 1e-12);
    assert!((bcf[1] - srgb_to_linear(0.5)).abs() < 1e-12);
    assert!((bcf[2] - srgb_to_linear(0.75)).abs() < 1e-12);
    assert_eq!(bcf[3], 1.0);
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
        r#""materials":[{"doubleSided":true,"pbrMetallicRoughness":{"baseColorFactor":[0.05087608817155679,0.21404114048223255,0.5225215539683921,1.0],"metallicFactor":0.1,"roughnessFactor":0.9}}],"#,
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

    let embedded = load_glb(&to_glb(&scene, &GlbOptions::new().with_textures(true)).unwrap());
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

#[test]
fn converts_srgb_base_color_factor_to_linear_in_baked_and_instanced_exports() {
    let mats = vec![
        material(
            "LayerRed",
            [1.0, 84.0 / 255.0, 84.0 / 255.0, 1.0],
            0.0,
            0.8,
            false,
        ),
        material("White", [1.0, 1.0, 1.0, 1.0], 0.0, 0.8, false),
        material("Black", [0.0, 0.0, 0.0, 1.0], 0.0, 0.8, false),
        material(
            "MidGrey",
            [188.0 / 255.0, 188.0 / 255.0, 188.0 / 255.0, 1.0],
            0.0,
            0.8,
            false,
        ),
        material(
            "DarkToe",
            [10.0 / 255.0, 10.0 / 255.0, 10.0 / 255.0, 128.0 / 255.0],
            0.0,
            0.8,
            false,
        ),
    ];

    let mut baked = Scene {
        gltf_materials: mats.clone(),
        ..Default::default()
    };
    for i in 0..mats.len() {
        let mut p = flat_triangle();
        p.material_index = i;
        baked.glb_primitives.push(p);
    }

    let mut res = InstancedMeshResource {
        id: "res_0".into(),
        definition_id: Some(1),
        definition_name: "Def".into(),
        variant_key: "1".into(),
        primitives: Vec::new(),
    };
    for i in 0..mats.len() {
        res.primitives.push(LocalPrimitive {
            positions: vec![0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0],
            normals: vec![0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 0.0, 0.0, 1.0],
            uvs: vec![0.0, 0.0, 1.0, 0.0, 0.0, 1.0],
            indices: vec![0, 1, 2],
            material_index: i,
            unpainted_back: false,
        });
    }
    let instanced = InstancedScene {
        gltf_materials: mats,
        mesh_resources: vec![res],
        scene_hierarchy: InstancedNode {
            name: "ROOT".into(),
            mesh_resource_id: Some("res_0".into()),
            ..Default::default()
        },
        ..Default::default()
    };

    let factor_of = |mat: &openskp::JsonValue| -> Vec<f64> {
        mat.find("pbrMetallicRoughness")
            .and_then(|p| p.find("baseColorFactor"))
            .map(|_| numbers(mat.find("pbrMetallicRoughness").unwrap(), "baseColorFactor"))
            .unwrap_or_else(|| vec![1.0, 1.0, 1.0, 1.0])
    };

    let baked_bytes = glb(&baked);
    let instanced_bytes = to_instanced_glb(&instanced, &InstancedGlbOptions::default()).unwrap();
    for bytes in [baked_bytes, instanced_bytes] {
        let model = load_glb(&bytes);
        let exported = model.list("materials");
        assert_eq!(exported.len(), 5);

        let red = factor_of(&exported[0]);
        assert_eq!(red[0], 1.0);
        assert!((red[1] - 0.0887).abs() < 1e-4);
        assert!((red[2] - 0.0887).abs() < 1e-4);
        assert_eq!(red[3], 1.0);

        assert_eq!(factor_of(&exported[1]), vec![1.0, 1.0, 1.0, 1.0]);
        assert_eq!(factor_of(&exported[2]), vec![0.0, 0.0, 0.0, 1.0]);

        let mid = factor_of(&exported[3]);
        assert!((mid[0] - 0.503).abs() < 1e-3);
        assert!((mid[0] - srgb_to_linear(188.0 / 255.0)).abs() < 1e-12);

        let toe = factor_of(&exported[4]);
        assert!((toe[0] - (10.0 / 255.0) / 12.92).abs() < 1e-12);
        assert!((toe[3] - 128.0 / 255.0).abs() < 1e-12);
    }

    // Scene gltf_materials stay sRGB.
    assert_eq!(
        baked.gltf_materials[0]
            .pbr_metallic_roughness
            .base_color_factor[1],
        84.0 / 255.0
    );
    assert_eq!(
        baked.gltf_materials[3]
            .pbr_metallic_roughness
            .base_color_factor[0],
        188.0 / 255.0
    );

    // Plain textured materials with embedded images write [1, 1, 1, alpha] while
    // keeping scene.gltf_materials untouched (#657).
    let capilla_skp = SkpFile::open(common::fixture("capilla_quiroz_v17.skp")).unwrap();
    let capilla_baked = capilla_skp.build_scene(&Default::default()).unwrap();
    let capilla_inst = capilla_skp
        .build_instanced_scene(&Default::default())
        .unwrap();
    assert!(capilla_baked.gltf_materials.iter().any(|m| {
        m.pbr_metallic_roughness.base_color_texture.is_some()
            && m.pbr_metallic_roughness.base_color_factor[0] < 0.6
            && m.pbr_metallic_roughness.base_color_factor[2] < 0.2
    }));
    for tex_bytes in [
        to_glb(&capilla_baked, &GlbOptions::new().with_textures(true)).unwrap(),
        to_instanced_glb(
            &capilla_inst,
            &InstancedGlbOptions::new().with_textures(true),
        )
        .unwrap(),
    ] {
        let tex_model = load_glb(&tex_bytes);
        let mut tex_count = 0usize;
        let mut half_alpha_count = 0usize;
        for gm in tex_model.list("materials") {
            let has_tex = gm
                .find("pbrMetallicRoughness")
                .and_then(|p| p.find("baseColorTexture"))
                .is_some();
            if has_tex {
                tex_count += 1;
                let bcf = factor_of(gm);
                assert_eq!(bcf[0], 1.0);
                assert_eq!(bcf[1], 1.0);
                assert_eq!(bcf[2], 1.0);
                if bcf[3] < 1.0 {
                    assert!((bcf[3] - 0.5).abs() < 1e-2);
                    half_alpha_count += 1;
                }
            }
        }
        assert_eq!(tex_count, 4);
        assert_eq!(half_alpha_count, 2);
    }

    // End-to-end model -> scene -> GLB on a real .skp containing one plain and one
    // colorized material sharing the same RGB (200, 80, 60) and PNG image (#657).
    let cpp_fixture = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("..")
        .join("cpp")
        .join("tests")
        .join("fixtures")
        .join("colorized_and_plain_texture.skp");
    let both_skp = SkpFile::open(&cpp_fixture).unwrap();
    let both_baked = both_skp.build_scene(&Default::default()).unwrap();
    let both_inst = both_skp.build_instanced_scene(&Default::default()).unwrap();
    for mats in [&both_baked.gltf_materials, &both_inst.gltf_materials] {
        assert_eq!(mats.len(), 2);
        let plain_count = mats
            .iter()
            .filter(|m| !m.colorized && m.colorize_type == 0)
            .count();
        let col_count = mats
            .iter()
            .filter(|m| m.colorized && m.colorize_type == 1)
            .count();
        assert_eq!(plain_count, 1);
        assert_eq!(col_count, 1);
        for m in mats {
            assert_eq!(
                m.pbr_metallic_roughness.base_color_factor,
                [200.0 / 255.0, 80.0 / 255.0, 60.0 / 255.0, 1.0]
            );
        }
    }

    for with_tex_bytes in [
        to_glb(&both_baked, &GlbOptions::new().with_textures(true)).unwrap(),
        to_instanced_glb(&both_inst, &InstancedGlbOptions::new().with_textures(true)).unwrap(),
    ] {
        let m = load_glb(&with_tex_bytes);
        let exported = m.list("materials");
        assert_eq!(exported.len(), 2);
        let mut saw_plain = 0usize;
        let mut saw_col = 0usize;
        for gm in exported {
            let pbr = gm.find("pbrMetallicRoughness").unwrap();
            assert_eq!(int(pbr.find("baseColorTexture").unwrap(), "index"), 0);
            if let Some(extras) = gm.find("extras") {
                let skp = extras.find("sketchup").unwrap();
                assert_eq!(skp.find("colorized"), Some(&openskp::JsonValue::Bool(true)));
                assert_eq!(int(skp, "colorize_type"), 1);
                let bcf = factor_of(gm);
                assert!((bcf[0] - 200.0 / 255.0).abs() < 1e-12);
                assert!((bcf[1] - 80.0 / 255.0).abs() < 1e-12);
                assert!((bcf[2] - 60.0 / 255.0).abs() < 1e-12);
                assert_eq!(bcf[3], 1.0);
                saw_col += 1;
            } else {
                assert_eq!(factor_of(gm), vec![1.0, 1.0, 1.0, 1.0]);
                saw_plain += 1;
            }
        }
        assert_eq!(saw_plain, 1);
        assert_eq!(saw_col, 1);
    }

    for no_tex_bytes in [
        to_glb(&both_baked, &GlbOptions::new().with_textures(false)).unwrap(),
        to_instanced_glb(&both_inst, &InstancedGlbOptions::new().with_textures(false)).unwrap(),
    ] {
        let m = load_glb(&no_tex_bytes);
        assert!(m.json.find("textures").is_none());
        let exported = m.list("materials");
        assert_eq!(exported.len(), 2);
        for gm in exported {
            let pbr = gm.find("pbrMetallicRoughness").unwrap();
            assert!(pbr.find("baseColorTexture").is_none());
            let bcf = factor_of(gm);
            assert!((bcf[0] - srgb_to_linear(200.0 / 255.0)).abs() < 1e-12);
            assert!((bcf[1] - srgb_to_linear(80.0 / 255.0)).abs() < 1e-12);
            assert!((bcf[2] - srgb_to_linear(60.0 / 255.0)).abs() < 1e-12);
            assert_eq!(bcf[3], 1.0);
        }
    }
}
