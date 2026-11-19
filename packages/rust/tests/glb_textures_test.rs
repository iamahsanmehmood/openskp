//! Port of `glb_textures_test.cpp`: GLB texture embedding on
//! capilla_quiroz_v17.skp, which carries 3 real, distinct JPEG textures.

mod common;
mod gltf_helper;

use gltf_helper::{contains_bytes, int, load_glb, string};
use openskp::{export_glb, to_glb, GlbOptions, ParseOptions, Scene, SkpFile};

const JPEG_MAGIC: &[u8] = &[0xff, 0xd8, 0xff];

fn capilla() -> Scene {
    SkpFile::open(common::fixture("capilla_quiroz_v17.skp"))
        .unwrap()
        .build_scene(&ParseOptions::default())
        .unwrap()
}

#[test]
fn scene_deduplicates_textures_and_keys_materials_by_them() {
    let scene = capilla();
    assert_eq!(scene.textures.len(), 3);
    for tex in &scene.textures {
        assert!(tex.mime_type == "image/jpeg" || tex.mime_type == "image/png");
        assert!(!tex.data.is_empty());
    }
    let mut textured = 0;
    for mat in &scene.gltf_materials {
        if let Some(t) = mat.pbr_metallic_roughness.base_color_texture {
            textured += 1;
            assert!(t < scene.textures.len());
        }
    }
    assert_eq!(textured, 4);
}

#[test]
fn export_omits_images_by_default() {
    let bytes = to_glb(&capilla(), &GlbOptions::default()).unwrap();
    assert!(!contains_bytes(&bytes, b"\"images\""));
    assert!(!contains_bytes(&bytes, JPEG_MAGIC));
    assert!(load_glb(&bytes).list("images").is_empty());
}

#[test]
fn export_embeds_textures_when_asked() {
    let scene = capilla();
    let without = to_glb(&scene, &GlbOptions::default()).unwrap();
    let with = to_glb(&scene, &GlbOptions { textures: true }).unwrap();
    assert!(with.len() > without.len());
    assert!(contains_bytes(&with, JPEG_MAGIC));

    let model = load_glb(&with);
    assert_eq!(model.list("images").len(), 3);
    for image in model.list("images") {
        assert!(int(image, "bufferView") >= 0);
        assert!(string(image, "mimeType").starts_with("image/"));
    }
    assert_eq!(model.list("textures").len(), 3);
    for texture in model.list("textures") {
        assert!(int(texture, "source") >= 0);
    }
    let mut found = false;
    for material in model.list("materials") {
        if let Some(info) = material
            .find("pbrMetallicRoughness")
            .and_then(|p| p.find("baseColorTexture"))
        {
            found = true;
            assert!((int(info, "index") as usize) < model.list("textures").len());
        }
    }
    assert!(found);
}

#[test]
fn export_glb_file_writes_embedded_textures() {
    let output = std::env::temp_dir().join("openskp-rs-textures-test.glb");
    export_glb(&capilla(), &output, &GlbOptions { textures: true }).unwrap();
    let actual = std::fs::read(&output).unwrap();
    std::fs::remove_file(&output).unwrap();
    assert!(contains_bytes(&actual, JPEG_MAGIC));
}
