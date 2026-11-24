mod gltf_helper;

use std::error::Error;
use std::path::PathBuf;

use gltf_helper::load_glb;
use openskp::create::{create, DefinitionOptions, FaceOptions, InstanceOptions};
use openskp::{
    to_glb, to_instanced_glb, GlbOptions, InstancedGlbOptions, JsonValue, ParseOptions, SkpFile,
};

// Minimal valid 1x1 and 2x1 RGB PNGs (distinct byte lengths so texture dedup
// treats them as two distinct images).
const PNG_1X1: &[u8] = &[
    0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0x00, 0x00, 0x00, 0x0d, 0x49, 0x48, 0x44, 0x52,
    0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x01, 0x08, 0x02, 0x00, 0x00, 0x00, 0x90, 0x77, 0x53,
    0xde, 0x00, 0x00, 0x00, 0x0c, 0x49, 0x44, 0x41, 0x54, 0x78, 0x9c, 0x63, 0x38, 0x91, 0x62, 0x04,
    0x00, 0x03, 0x56, 0x01, 0x5f, 0xe8, 0x17, 0x84, 0x52, 0x00, 0x00, 0x00, 0x00, 0x49, 0x45, 0x4e,
    0x44, 0xae, 0x42, 0x60, 0x82,
];

const PNG_2X1: &[u8] = &[
    0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0x00, 0x00, 0x00, 0x0d, 0x49, 0x48, 0x44, 0x52,
    0x00, 0x00, 0x00, 0x02, 0x00, 0x00, 0x00, 0x01, 0x08, 0x02, 0x00, 0x00, 0x00, 0x7b, 0x40, 0xe8,
    0xdd, 0x00, 0x00, 0x00, 0x0f, 0x49, 0x44, 0x41, 0x54, 0x78, 0x9c, 0x63, 0x30, 0x4a, 0x39, 0x61,
    0x94, 0x72, 0x02, 0x00, 0x08, 0x6d, 0x02, 0xbd, 0xeb, 0xae, 0x9b, 0xe7, 0x00, 0x00, 0x00, 0x00,
    0x49, 0x45, 0x4e, 0x44, 0xae, 0x42, 0x60, 0x82,
];

const PANEL_QUAD: [[f64; 3]; 4] = [
    [0.0, 0.0, 0.0],
    [24.0, 0.0, 0.0],
    [24.0, 12.0, 0.0],
    [0.0, 12.0, 0.0],
];

const ACCENT_QUAD: [[f64; 3]; 4] = [
    [0.0, 20.0, 0.0],
    [10.0, 20.0, 0.0],
    [10.0, 30.0, 0.0],
    [0.0, 30.0, 0.0],
];

struct TempTextureFiles {
    dir: PathBuf,
    brick: PathBuf,
    tile: PathBuf,
}

impl TempTextureFiles {
    fn new(tag: &str) -> Self {
        let dir = std::env::temp_dir().join(format!("openskp-rs-inherited-tex-{tag}"));
        std::fs::create_dir_all(&dir).unwrap();
        let brick = dir.join("brick.png");
        let tile = dir.join("tile.png");
        std::fs::write(&brick, PNG_1X1).unwrap();
        std::fs::write(&tile, PNG_2X1).unwrap();
        Self { dir, brick, tile }
    }
}

impl Drop for TempTextureFiles {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.dir);
    }
}

fn uv_bounds(uvs: &[f32]) -> (f32, f32, f32, f32) {
    let mut min_u = uvs[0];
    let mut max_u = uvs[0];
    let mut min_v = uvs[1];
    let mut max_v = uvs[1];
    let mut i = 0;
    while i + 1 < uvs.len() {
        min_u = min_u.min(uvs[i]);
        max_u = max_u.max(uvs[i]);
        min_v = min_v.min(uvs[i + 1]);
        max_v = max_v.max(uvs[i + 1]);
        i += 2;
    }
    (min_u, max_u, min_v, max_v)
}

fn assert_near(actual: f32, expected: f32) {
    assert!(
        (actual - expected).abs() < 1e-5,
        "expected {expected}, got {actual}"
    );
}

fn alpha_mode(material: &JsonValue) -> &str {
    material
        .find("alphaMode")
        .and_then(JsonValue::as_str)
        .unwrap_or("OPAQUE")
}

fn build_inherited_texture_model(files: &TempTextureFiles) -> Result<SkpFile, Box<dyn Error>> {
    let mut builder = create();
    let brick = builder.add_texture_material("Brick", &files.brick, Some(6.0), Some(12.0), None)?;
    let tile = builder.add_texture_material("Tile", &files.tile, Some(3.0), Some(4.0), None)?;
    let accent = builder.add_material("Accent", [10, 20, 30], None)?;

    let def_opts = DefinitionOptions::default();
    let default_face = FaceOptions::default();

    let mut panel = builder.add_component_definition("Panel", &def_opts)?;
    panel.add_face(&PANEL_QUAD, &default_face)?;
    let panel = panel.close()?;

    let mut wrapper = builder.add_component_definition("Wrapper", &def_opts)?;
    wrapper.add_instance(panel, &InstanceOptions::default())?;
    let wrapper = wrapper.close()?;

    let mut mixed = builder.add_component_definition("MixedPanel", &def_opts)?;
    mixed.add_face(&PANEL_QUAD, &default_face)?;
    let accent_front = FaceOptions {
        material: Some(accent),
        ..Default::default()
    };
    mixed.add_face(&ACCENT_QUAD, &accent_front)?;
    let mixed = mixed.close()?;

    builder.add_instance(
        panel,
        &InstanceOptions {
            name: Some("BrickPanel".to_owned()),
            material: Some(brick),
            ..Default::default()
        },
    )?;
    builder.add_instance(
        panel,
        &InstanceOptions {
            name: Some("TilePanel".to_owned()),
            translation: [30.0, 0.0, 0.0],
            material: Some(tile),
            ..Default::default()
        },
    )?;
    builder.add_instance(
        wrapper,
        &InstanceOptions {
            name: Some("NestedBrick".to_owned()),
            translation: [60.0, 0.0, 0.0],
            material: Some(brick),
            ..Default::default()
        },
    )?;
    builder.add_instance(
        mixed,
        &InstanceOptions {
            name: Some("MixedBrick".to_owned()),
            translation: [90.0, 0.0, 0.0],
            material: Some(brick),
            ..Default::default()
        },
    )?;

    Ok(SkpFile::from_buffer(builder.to_bytes()?))
}

#[test]
fn baked_and_instanced_scenes_inherit_texture_uvs_and_alpha_mode() -> Result<(), Box<dyn Error>> {
    let files = TempTextureFiles::new("main");
    let skp = build_inherited_texture_model(&files)?;
    let opts = ParseOptions::default();
    let scene = skp.build_scene(&opts)?;
    let instanced = skp.build_instanced_scene(&opts)?;

    assert_eq!(scene.textures.len(), 2);
    assert_eq!(scene.textures[0].mime_type, "image/png");
    assert_eq!(scene.textures[0].data, PNG_1X1);
    assert_eq!(scene.textures[1].mime_type, "image/png");
    assert_eq!(scene.textures[1].data, PNG_2X1);

    assert_eq!(scene.glb_primitives.len(), 6);
    for prim in &scene.glb_primitives {
        assert!(!prim.unpainted_back);
        let mat = &scene.gltf_materials[prim.material_index];
        let (min_u, max_u, min_v, max_v) = uv_bounds(&prim.uvs);
        if prim.geom_name.contains("BrickPanel") || prim.geom_name.contains("NestedBrick") {
            assert_eq!(mat.pbr_metallic_roughness.base_color_texture, Some(0));
            assert_near(min_u, 0.0);
            assert_near(max_u, 2.0);
            assert_near(min_v, 0.0);
            assert_near(max_v, 2.0);
        } else if prim.geom_name.contains("TilePanel") {
            assert_eq!(mat.pbr_metallic_roughness.base_color_texture, Some(1));
            assert_near(min_u, 0.0);
            assert_near(max_u, 6.0);
            assert_near(min_v, 0.0);
            assert_near(max_v, 4.0);
        } else if mat.double_sided {
            assert_eq!(mat.pbr_metallic_roughness.base_color_texture, Some(0));
            assert_near(max_u, 2.0);
            assert_near(max_v, 2.0);
        } else if mat.pbr_metallic_roughness.base_color_texture.is_none() {
            assert_near(min_u, 0.0);
            assert_near(max_u, 10.0);
            assert_near(min_v, 20.0);
            assert_near(max_v, 30.0);
        } else {
            assert_eq!(mat.pbr_metallic_roughness.base_color_texture, Some(0));
            assert_near(min_u, 0.0);
            assert_near(max_u, 10.0 / 12.0);
            assert_near(min_v, 20.0 / 6.0);
            assert_near(max_v, 30.0 / 6.0);
        }
    }

    assert_eq!(instanced.textures.len(), 2);
    assert_eq!(instanced.mesh_resources.len(), 3);
    assert_eq!(instanced.scene_hierarchy.children.len(), 4);
    let brick_res_id = instanced.scene_hierarchy.children[0]
        .mesh_resource_id
        .as_deref();
    let tile_res_id = instanced.scene_hierarchy.children[1]
        .mesh_resource_id
        .as_deref();
    let nested_res_id = instanced.scene_hierarchy.children[2].children[0]
        .mesh_resource_id
        .as_deref();
    assert!(brick_res_id.is_some());
    assert!(tile_res_id.is_some());
    assert_eq!(nested_res_id, brick_res_id);
    assert_ne!(tile_res_id, brick_res_id);

    let brick_prim = &instanced.mesh_resources[0].primitives[0];
    let (_, brick_u, _, brick_v) = uv_bounds(&brick_prim.uvs);
    assert_near(brick_u, 2.0);
    assert_near(brick_v, 2.0);
    let brick_tex = instanced.gltf_materials[brick_prim.material_index]
        .pbr_metallic_roughness
        .base_color_texture;
    assert_eq!(brick_tex, Some(0));

    let tile_prim = &instanced.mesh_resources[1].primitives[0];
    let (_, tile_u, _, tile_v) = uv_bounds(&tile_prim.uvs);
    assert_near(tile_u, 6.0);
    assert_near(tile_v, 4.0);
    let tile_tex = instanced.gltf_materials[tile_prim.material_index]
        .pbr_metallic_roughness
        .base_color_texture;
    assert_eq!(tile_tex, Some(1));

    let baked_glb = to_glb(&scene, &GlbOptions { textures: true })?;
    let inst_glb = to_instanced_glb(&instanced, &InstancedGlbOptions { textures: true })?;
    for glb_bytes in [&baked_glb, &inst_glb] {
        let glb = load_glb(glb_bytes);
        assert_eq!(glb.list("images").len(), 2);
        assert_eq!(glb.list("textures").len(), 2);
        let mut mask_count = 0usize;
        let mut opaque_count = 0usize;
        for m in glb.list("materials") {
            let has_tex = m
                .find("pbrMetallicRoughness")
                .and_then(|p| p.find("baseColorTexture"))
                .is_some();
            if has_tex {
                assert_eq!(alpha_mode(m), "MASK");
                mask_count += 1;
            } else {
                assert_eq!(alpha_mode(m), "OPAQUE");
                opaque_count += 1;
            }
        }
        assert!(mask_count >= 2);
        assert_eq!(opaque_count, 1);
    }

    Ok(())
}

#[test]
fn instanced_scene_separates_same_image_materials_by_tile_size() -> Result<(), Box<dyn Error>> {
    let files = TempTextureFiles::new("tile-size");
    let mut builder = create();
    let small = builder.add_texture_material("Small", &files.brick, Some(6.0), Some(12.0), None)?;
    let large =
        builder.add_texture_material("Large", &files.brick, Some(12.0), Some(24.0), None)?;

    let mut panel = builder.add_component_definition("Panel", &DefinitionOptions::default())?;
    panel.add_face(&PANEL_QUAD, &FaceOptions::default())?;
    let panel = panel.close()?;

    builder.add_instance(
        panel,
        &InstanceOptions {
            material: Some(small),
            ..Default::default()
        },
    )?;
    builder.add_instance(
        panel,
        &InstanceOptions {
            translation: [30.0, 0.0, 0.0],
            material: Some(large),
            ..Default::default()
        },
    )?;

    let skp = SkpFile::from_buffer(builder.to_bytes()?);
    let instanced = skp.build_instanced_scene(&ParseOptions::default())?;
    assert_eq!(instanced.textures.len(), 1);
    assert_eq!(instanced.mesh_resources.len(), 2);
    let (_, u0, _, _) = uv_bounds(&instanced.mesh_resources[0].primitives[0].uvs);
    let (_, u1, _, _) = uv_bounds(&instanced.mesh_resources[1].primitives[0].uvs);
    assert_near(u0, 2.0);
    assert_near(u1, 1.0);
    Ok(())
}
