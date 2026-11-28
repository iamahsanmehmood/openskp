mod common;

use std::collections::{BTreeMap, BTreeSet};
use std::error::Error;
use std::sync::Arc;

use openskp::create::{
    create, DefinitionOptions, FaceOptions, GroupOptions, InstanceOptions, LayerOptions,
    TextureMaterialOptions,
};
use openskp::instanced_scene_build::build_instanced_scene_raw;
use openskp::internal::full_parse;
use openskp::{GltfMaterial, ParseOptions, SkpFile};

const SQUARE: [[f64; 3]; 4] = [
    [0.0, 0.0, 0.0],
    [10.0, 0.0, 0.0],
    [10.0, 10.0, 0.0],
    [0.0, 10.0, 0.0],
];

fn round6(value: f64) -> i64 {
    (value * 1_000_000.0).round() as i64
}

fn summarize_material(mat: &GltfMaterial) -> (i64, i64, i64, i64, bool) {
    let bcf = mat.pbr_metallic_roughness.base_color_factor;
    (
        round6(bcf[0]),
        round6(bcf[1]),
        round6(bcf[2]),
        round6(bcf[3]),
        mat.double_sided,
    )
}

fn painted_model() -> Result<SkpFile, Box<dyn Error>> {
    let mut builder = create();
    let red = builder.add_material("Red", [200, 10, 10, 255], None)?;
    let blue = builder.add_material("Blue", [10, 10, 200, 128], None)?;

    let def_opts = DefinitionOptions::default();
    let face_opts = FaceOptions::default();

    let mut leaf = builder.add_component_definition("Leaf", &def_opts)?;
    leaf.add_face(&SQUARE, &face_opts)?;
    let leaf = leaf.close()?;

    let mut outer = builder.add_component_definition("Outer", &def_opts)?;
    let inherits_opts = InstanceOptions {
        name: Some("InheritsPaint".to_owned()),
        ..Default::default()
    };
    outer.add_instance(leaf, &inherits_opts)?;
    let own_opts = InstanceOptions {
        name: Some("OwnPaint".to_owned()),
        material: Some(blue),
        translation: [20.0, 0.0, 0.0],
        ..Default::default()
    };
    outer.add_instance(leaf, &own_opts)?;
    let outer = outer.close()?;

    let painted_opts = InstanceOptions {
        name: Some("Painted".to_owned()),
        material: Some(red),
        ..Default::default()
    };
    builder.add_instance(outer, &painted_opts)?;

    Ok(SkpFile::from_buffer(builder.to_bytes()?))
}

#[test]
fn paint_is_applied_and_inherited_by_nested_components() -> Result<(), Box<dyn Error>> {
    let opts = ParseOptions::default();
    let skp = painted_model()?;
    let baked = skp.build_scene(&opts)?;
    let instanced = skp.build_instanced_scene(&opts)?;

    let expected = BTreeSet::from([
        (
            round6(200.0 / 255.0),
            round6(10.0 / 255.0),
            round6(10.0 / 255.0),
            round6(1.0),
            true,
        ),
        (
            round6(10.0 / 255.0),
            round6(10.0 / 255.0),
            round6(200.0 / 255.0),
            round6(128.0 / 255.0),
            true,
        ),
    ]);

    let mut baked_mats = BTreeSet::new();
    for prim in &baked.glb_primitives {
        baked_mats.insert(summarize_material(
            &baked.gltf_materials[prim.material_index],
        ));
    }
    assert_eq!(baked_mats, expected);

    let mut instanced_mats = BTreeSet::new();
    for res in &instanced.mesh_resources {
        for prim in &res.primitives {
            instanced_mats.insert(summarize_material(
                &instanced.gltf_materials[prim.material_index],
            ));
        }
    }
    assert_eq!(instanced_mats, expected);
    Ok(())
}

#[test]
fn each_paint_is_its_own_instanced_mesh() -> Result<(), Box<dyn Error>> {
    let opts = ParseOptions::default();
    let scene = painted_model()?.build_instanced_scene(&opts)?;
    assert_eq!(scene.scene_hierarchy.children.len(), 1);
    let mut nodes = BTreeMap::new();
    for node in &scene.scene_hierarchy.children[0].children {
        nodes.insert(node.name.as_str(), node);
    }
    let inherits_id = nodes["InheritsPaint"].mesh_resource_id.as_deref();
    let own_id = nodes["OwnPaint"].mesh_resource_id.as_deref();
    assert!(inherits_id.is_some());
    assert!(own_id.is_some());
    assert_ne!(inherits_id, own_id);
    Ok(())
}

#[test]
fn tag_is_the_instances_own() -> Result<(), Box<dyn Error>> {
    let mut builder = create();
    let layer_opts = LayerOptions::default();
    let def_opts = DefinitionOptions::default();
    let face_opts = FaceOptions::default();

    let walls = builder.add_layer("Walls", &layer_opts)?;
    let mut part = builder.add_component_definition("Part", &def_opts)?;
    part.add_face(&SQUARE, &face_opts)?;
    let part = part.close()?;

    let tagged_opts = InstanceOptions {
        name: Some("Tagged".to_owned()),
        layer: Some(walls),
        ..Default::default()
    };
    builder.add_instance(part, &tagged_opts)?;

    let untagged_opts = InstanceOptions {
        name: Some("Untagged".to_owned()),
        translation: [20.0, 0.0, 0.0],
        ..Default::default()
    };
    builder.add_instance(part, &untagged_opts)?;

    let opts = ParseOptions::default();
    let skp = SkpFile::from_buffer(builder.to_bytes()?);
    let baked = skp.build_scene(&opts)?;
    let instanced = skp.build_instanced_scene(&opts)?;

    let expected = BTreeMap::from([
        ("Tagged".to_owned(), "Walls".to_owned()),
        ("Untagged".to_owned(), "Layer0".to_owned()),
    ]);

    let mut baked_layers = BTreeMap::new();
    for node in &baked.scene_hierarchy.children {
        baked_layers.insert(node.name.clone(), node.layer.clone());
    }
    assert_eq!(baked_layers, expected);

    let mut instanced_layers = BTreeMap::new();
    for node in &instanced.scene_hierarchy.children {
        instanced_layers.insert(node.name.clone(), node.layer.clone());
    }
    assert_eq!(instanced_layers, expected);
    Ok(())
}

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

fn uv_bounds(uvs: &[f32]) -> (f32, f32, f32, f32) {
    let (mut min_u, mut max_u, mut min_v, mut max_v) = (uvs[0], uvs[0], uvs[1], uvs[1]);
    for i in (0..uvs.len()).step_by(2) {
        min_u = min_u.min(uvs[i]);
        max_u = max_u.max(uvs[i]);
        min_v = min_v.min(uvs[i + 1]);
        max_v = max_v.max(uvs[i + 1]);
    }
    (min_u, max_u, min_v, max_v)
}

#[test]
fn sdk_classic_and_vff_textured_painted_instances_and_tags() -> Result<(), Box<dyn Error>> {
    let opts = ParseOptions::default();
    for fname in [
        "instance_textured_paint_2020.skp",
        "instance_textured_paint_2021.skp",
    ] {
        let skp = SkpFile::open(common::fixture(fname))?;
        let baked = skp.build_scene(&opts)?;
        let instanced = skp.build_instanced_scene(&opts)?;

        assert_eq!(baked.textures.len(), 2, "{fname}");
        assert_eq!(instanced.textures.len(), 2, "{fname}");
        assert_eq!(baked.glb_primitives.len(), 10, "{fname}");
        assert_eq!(instanced.mesh_resources.len(), 6, "{fname}");

        let expected_top = BTreeMap::from([
            ("BrickPanel".to_owned(), "ExteriorTag".to_owned()),
            ("WoodPanel".to_owned(), "InteriorTag".to_owned()),
            ("PlainPanel".to_owned(), "Layer0".to_owned()),
            ("BrickGroup".to_owned(), "ExteriorTag".to_owned()),
            ("OuterInst".to_owned(), "ExteriorTag".to_owned()),
        ]);
        let baked_top: BTreeMap<_, _> = baked
            .scene_hierarchy
            .children
            .iter()
            .map(|n| (n.name.clone(), n.layer.clone()))
            .collect();
        let inst_top: BTreeMap<_, _> = instanced
            .scene_hierarchy
            .children
            .iter()
            .map(|n| (n.name.clone(), n.layer.clone()))
            .collect();
        assert_eq!(baked_top, expected_top, "{fname}");
        assert_eq!(inst_top, expected_top, "{fname}");

        let expected_nested = BTreeMap::from([
            ("ChildInherits".to_owned(), "ExteriorTag".to_owned()),
            ("ChildOverrides".to_owned(), "InteriorTag".to_owned()),
        ]);
        let baked_outer = baked
            .scene_hierarchy
            .children
            .iter()
            .find(|n| n.name == "OuterInst")
            .unwrap();
        let inst_outer = instanced
            .scene_hierarchy
            .children
            .iter()
            .find(|n| n.name == "OuterInst")
            .unwrap();
        let baked_nested: BTreeMap<_, _> = baked_outer
            .children
            .iter()
            .map(|n| (n.name.clone(), n.layer.clone()))
            .collect();
        let inst_nested: BTreeMap<_, _> = inst_outer
            .children
            .iter()
            .map(|n| (n.name.clone(), n.layer.clone()))
            .collect();
        assert_eq!(baked_nested, expected_nested, "{fname}");
        assert_eq!(inst_nested, expected_nested, "{fname}");

        let by_id: BTreeMap<_, _> = instanced
            .mesh_resources
            .iter()
            .map(|r| (r.id.as_str(), r))
            .collect();
        let top_by_name: BTreeMap<_, _> = instanced
            .scene_hierarchy
            .children
            .iter()
            .map(|n| (n.name.as_str(), n))
            .collect();
        let nested_by_name: BTreeMap<_, _> = inst_outer
            .children
            .iter()
            .map(|n| (n.name.as_str(), n))
            .collect();

        let grp_prim = &by_id[top_by_name["BrickGroup"]
            .mesh_resource_id
            .as_deref()
            .unwrap()]
        .primitives[0];
        let inh_prim = &by_id[nested_by_name["ChildInherits"]
            .mesh_resource_id
            .as_deref()
            .unwrap()]
        .primitives[0];
        let ovr_prim = &by_id[nested_by_name["ChildOverrides"]
            .mesh_resource_id
            .as_deref()
            .unwrap()]
        .primitives[0];

        let grp_tex = instanced.gltf_materials[grp_prim.material_index]
            .pbr_metallic_roughness
            .base_color_texture;
        let ovr_tex = instanced.gltf_materials[ovr_prim.material_index]
            .pbr_metallic_roughness
            .base_color_texture;
        assert!(grp_tex.is_some());
        assert!(ovr_tex.is_some());
        assert_ne!(grp_tex, ovr_tex);

        let (_, gu1, _, gv1) = uv_bounds(&grp_prim.uvs);
        let (_, iu1, _, iv1) = uv_bounds(&inh_prim.uvs);
        let (_, ou1, _, ov1) = uv_bounds(&ovr_prim.uvs);
        assert!((gu1 - 1.0).abs() < 1e-4);
        assert!((gv1 - 1.0).abs() < 1e-4);
        assert!((iu1 - 1.0).abs() < 1e-4);
        assert!((iv1 - 1.0).abs() < 1e-4);
        assert!((ou1 - 2.5).abs() < 1e-4);
        assert!((ov1 - 2.5).abs() < 1e-4);
    }
    Ok(())
}

#[test]
fn writer_unnamed_and_named_textured_materials_on_instances_and_groups(
) -> Result<(), Box<dyn Error>> {
    let dir = std::env::temp_dir().join(format!("openskp-rs-559-{}", std::process::id()));
    std::fs::create_dir_all(&dir)?;
    let tex1 = dir.join("a.png");
    let tex2 = dir.join("b.png");
    std::fs::write(&tex1, PNG_1X1)?;
    std::fs::write(&tex2, PNG_2X1)?;

    let rect = [
        [0.0, 0.0, 0.0],
        [10.0, 0.0, 0.0],
        [10.0, 20.0, 0.0],
        [0.0, 20.0, 0.0],
    ];
    let mut builder = create();
    let named_mat = builder.add_texture_material("Brick", &tex1, Some(20.0), Some(10.0), None)?;
    let unnamed_mat = builder.add_texture_material("", &tex2, Some(8.0), Some(4.0), None)?;
    let ext = builder.add_layer("Ext", &LayerOptions::default())?;
    let inter = builder.add_layer("Int", &LayerOptions::default())?;

    let mut panel = builder.add_component_definition("Panel", &DefinitionOptions::default())?;
    panel.add_face(&rect, &FaceOptions::default())?;
    let panel = panel.close()?;

    let mut grp = builder.add_group(&GroupOptions {
        name: Some("PaintedGroup".to_owned()),
        translation: [40.0, 0.0, 0.0],
        material: Some(named_mat),
        layer: Some(ext),
        ..Default::default()
    })?;
    grp.add_face(&rect, &FaceOptions::default())?;
    grp.close()?;

    builder.add_instance(
        panel,
        &InstanceOptions {
            name: Some("NamedInst".to_owned()),
            material: Some(named_mat),
            layer: Some(ext),
            ..Default::default()
        },
    )?;
    builder.add_instance(
        panel,
        &InstanceOptions {
            name: Some("UnnamedInst".to_owned()),
            translation: [20.0, 0.0, 0.0],
            material: Some(unnamed_mat),
            layer: Some(inter),
            ..Default::default()
        },
    )?;

    let bytes = builder.to_bytes()?;
    let _ = std::fs::remove_dir_all(&dir);

    let opts = ParseOptions::default();
    let skp = SkpFile::from_buffer(bytes);
    let baked = skp.build_scene(&opts)?;
    let instanced = skp.build_instanced_scene(&opts)?;

    assert_eq!(baked.textures.len(), 2);
    assert_eq!(instanced.textures.len(), 2);
    let expected_layers = BTreeMap::from([
        ("PaintedGroup".to_owned(), "Ext".to_owned()),
        ("NamedInst".to_owned(), "Ext".to_owned()),
        ("UnnamedInst".to_owned(), "Int".to_owned()),
    ]);
    let baked_layers: BTreeMap<_, _> = baked
        .scene_hierarchy
        .children
        .iter()
        .map(|n| (n.name.clone(), n.layer.clone()))
        .collect();
    let inst_layers: BTreeMap<_, _> = instanced
        .scene_hierarchy
        .children
        .iter()
        .map(|n| (n.name.clone(), n.layer.clone()))
        .collect();
    assert_eq!(baked_layers, expected_layers);
    assert_eq!(inst_layers, expected_layers);

    let by_id: BTreeMap<_, _> = instanced
        .mesh_resources
        .iter()
        .map(|r| (r.id.as_str(), r))
        .collect();
    let nodes: BTreeMap<_, _> = instanced
        .scene_hierarchy
        .children
        .iter()
        .map(|n| (n.name.as_str(), n))
        .collect();
    let named_id = nodes["NamedInst"].mesh_resource_id.as_deref().unwrap();
    let unnamed_id = nodes["UnnamedInst"].mesh_resource_id.as_deref().unwrap();
    assert_ne!(named_id, unnamed_id);
    let (_, nu1, _, nv1) = uv_bounds(&by_id[named_id].primitives[0].uvs);
    let (_, uu1, _, uv1) = uv_bounds(&by_id[unnamed_id].primitives[0].uvs);
    assert!((nu1 - 1.0).abs() < 1e-4);
    assert!((nv1 - 1.0).abs() < 1e-4);
    assert!((uu1 - 2.5).abs() < 1e-4);
    assert!((uv1 - 2.5).abs() < 1e-4);
    Ok(())
}

#[test]
fn instanced_scene_isolates_same_name_same_rgb_inherited_material_variants(
) -> Result<(), Box<dyn Error>> {
    let raw = std::fs::read(common::fixture("instance_textured_paint_2021.skp"))?;
    let opts = ParseOptions::default();
    let mut parsed = full_parse(&raw, &opts)?;
    parsed.materials.remove("BrickTex");
    parsed.materials.remove("WoodTex");
    {
        let brick = Arc::make_mut(parsed.materials_by_folder.get_mut("BrickTex").unwrap());
        brick.name = "Shared".to_owned();
        brick.r = 170;
        brick.g = 58;
        brick.b = 40;
    }
    let wood_arc = {
        let wood_ref = parsed.materials_by_folder.get_mut("WoodTex").unwrap();
        let wood = Arc::make_mut(wood_ref);
        wood.name = "Shared".to_owned();
        wood.r = 170;
        wood.g = 58;
        wood.b = 40;
        Arc::clone(wood_ref)
    };
    parsed.materials.insert("Shared".to_owned(), wood_arc);

    let instanced = build_instanced_scene_raw(parsed, &opts)?;
    let by_id: BTreeMap<_, _> = instanced
        .mesh_resources
        .iter()
        .map(|r| (r.id.as_str(), r))
        .collect();
    let top: BTreeMap<_, _> = instanced
        .scene_hierarchy
        .children
        .iter()
        .map(|n| (n.name.as_str(), n))
        .collect();
    let nested: BTreeMap<_, _> = top["OuterInst"]
        .children
        .iter()
        .map(|n| (n.name.as_str(), n))
        .collect();

    assert_ne!(
        top["BrickPanel"].mesh_resource_id,
        top["WoodPanel"].mesh_resource_id
    );
    let inh_id = nested["ChildInherits"].mesh_resource_id.as_deref().unwrap();
    let ovr_id = nested["ChildOverrides"]
        .mesh_resource_id
        .as_deref()
        .unwrap();
    assert_ne!(inh_id, ovr_id);
    let (_, iu1, _, iv1) = uv_bounds(&by_id[inh_id].primitives[0].uvs);
    let (_, ou1, _, ov1) = uv_bounds(&by_id[ovr_id].primitives[0].uvs);
    assert!((iu1 - 1.0).abs() < 1e-4);
    assert!((iv1 - 1.0).abs() < 1e-4);
    assert!((ou1 - 2.5).abs() < 1e-4);
    assert!((ov1 - 2.5).abs() < 1e-4);
    Ok(())
}

const PNG_BRICK_4X4: &[u8] = &[
    0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0x00, 0x00, 0x00, 0x0d, 0x49, 0x48, 0x44, 0x52,
    0x00, 0x00, 0x00, 0x04, 0x00, 0x00, 0x00, 0x04, 0x08, 0x02, 0x00, 0x00, 0x00, 0x26, 0x93, 0x09,
    0x29, 0x00, 0x00, 0x00, 0x18, 0x49, 0x44, 0x41, 0x54, 0x78, 0xda, 0x63, 0xd8, 0xe0, 0x62, 0x00,
    0x41, 0x97, 0xce, 0x1e, 0x66, 0x80, 0x50, 0x10, 0x3e, 0x03, 0x4e, 0x19, 0x00, 0x5a, 0xca, 0x17,
    0x39, 0xf4, 0x43, 0xab, 0x79, 0x00, 0x00, 0x00, 0x00, 0x49, 0x45, 0x4e, 0x44, 0xae, 0x42, 0x60,
    0x82,
];

fn build_ground_truth_writer_skp() -> Result<SkpFile, Box<dyn Error>> {
    let dir = std::env::temp_dir().join(format!("openskp-rs-gt-559-{}", std::process::id()));
    std::fs::create_dir_all(&dir)?;
    let brick_png = dir.join("brick.png");
    std::fs::write(&brick_png, PNG_BRICK_4X4)?;

    let mut b = create();
    let plain_red = b.add_material("PlainRed", [210, 50, 40], None)?;
    let brick_tex = b.add_texture_material_with_options(
        "BrickTex",
        &brick_png,
        &TextureMaterialOptions {
            applied_height: Some(20.0),
            applied_width: Some(10.0),
            opacity: None,
            filename: Some("brick.png".to_owned()),
        },
    )?;
    let own_gold = b.add_material("OwnGold", [230, 180, 30], None)?;
    let tag = b.add_layer(
        "PaintedTag",
        &LayerOptions {
            color: Some([40, 120, 200, 255]),
            ..Default::default()
        },
    )?;

    let f_unp = [
        [0.0, 0.0, 0.0],
        [10.0, 0.0, 0.0],
        [10.0, 20.0, 0.0],
        [0.0, 20.0, 0.0],
    ];
    let f_own = [
        [0.0, 30.0, 0.0],
        [10.0, 30.0, 0.0],
        [10.0, 50.0, 0.0],
        [0.0, 50.0, 0.0],
    ];
    let own_opts = FaceOptions {
        material: Some(own_gold),
        back_material: Some(own_gold),
        ..Default::default()
    };

    let mut panel_b = b.add_component_definition("PanelDef", &DefinitionOptions::default())?;
    panel_b.add_face(&f_unp, &FaceOptions::default())?;
    panel_b.add_face(&f_own, &own_opts)?;
    let panel = panel_b.close()?;

    let mut outer_b = b.add_component_definition("OuterDef", &DefinitionOptions::default())?;
    outer_b.add_instance(
        panel,
        &InstanceOptions {
            name: Some("InnerUnpainted".to_owned()),
            translation: [5.0, 0.0, 0.0],
            ..Default::default()
        },
    )?;
    let outer = outer_b.close()?;

    let mut g1 = b.add_group(&GroupOptions {
        name: Some("PlainGroup".to_owned()),
        translation: [60.0, 0.0, 0.0],
        material: Some(plain_red),
        layer: Some(tag),
        ..Default::default()
    })?;
    g1.add_face(&f_unp, &FaceOptions::default())?;
    g1.add_face(&f_own, &own_opts)?;
    g1.close()?;

    let mut g2 = b.add_group(&GroupOptions {
        name: Some("TexturedGroup".to_owned()),
        translation: [90.0, 0.0, 0.0],
        material: Some(brick_tex),
        layer: Some(tag),
        ..Default::default()
    })?;
    g2.add_face(&f_unp, &FaceOptions::default())?;
    g2.add_face(&f_own, &own_opts)?;
    g2.close()?;

    b.add_instance(
        panel,
        &InstanceOptions {
            name: Some("PlainComp".to_owned()),
            translation: [0.0, 0.0, 0.0],
            material: Some(plain_red),
            layer: Some(tag),
            ..Default::default()
        },
    )?;
    b.add_instance(
        panel,
        &InstanceOptions {
            name: Some("TexturedComp".to_owned()),
            translation: [30.0, 0.0, 0.0],
            material: Some(brick_tex),
            layer: Some(tag),
            ..Default::default()
        },
    )?;
    b.add_instance(
        outer,
        &InstanceOptions {
            name: Some("OuterTextured".to_owned()),
            translation: [120.0, 0.0, 0.0],
            material: Some(brick_tex),
            layer: Some(tag),
            ..Default::default()
        },
    )?;

    let bytes = b.to_bytes()?;
    let _ = std::fs::remove_dir_all(&dir);
    Ok(SkpFile::from_buffer(bytes))
}

#[test]
fn matches_sketchup_ground_truth_across_2017_2020_2021_and_writer_files(
) -> Result<(), Box<dyn Error>> {
    type ParseRow = (bool, String, Option<String>, bool);
    type PartTuple = (String, [i32; 4], bool, [i64; 4]);

    let opts = ParseOptions::default();
    let sources = vec![
        (
            "2017",
            SkpFile::open(common::fixture("sdk_scene_instance_paint_2017.skp"))?,
        ),
        (
            "2020",
            SkpFile::open(common::fixture("sdk_scene_instance_paint_2020.skp"))?,
        ),
        (
            "2021",
            SkpFile::open(common::fixture("sdk_scene_instance_paint_2021.skp"))?,
        ),
        ("writer", build_ground_truth_writer_skp()?),
    ];

    let expected_parse = BTreeMap::from([
        (
            "PlainComp".to_owned(),
            (
                false,
                "PaintedTag".to_owned(),
                Some("PlainRed".to_owned()),
                false,
            ),
        ),
        (
            "TexturedComp".to_owned(),
            (
                false,
                "PaintedTag".to_owned(),
                Some("BrickTex".to_owned()),
                true,
            ),
        ),
        (
            "PlainGroup".to_owned(),
            (
                true,
                "PaintedTag".to_owned(),
                Some("PlainRed".to_owned()),
                false,
            ),
        ),
        (
            "TexturedGroup".to_owned(),
            (
                true,
                "PaintedTag".to_owned(),
                Some("BrickTex".to_owned()),
                true,
            ),
        ),
        (
            "OuterTextured".to_owned(),
            (
                false,
                "PaintedTag".to_owned(),
                Some("BrickTex".to_owned()),
                true,
            ),
        ),
        (
            "OuterTextured / InnerUnpainted".to_owned(),
            (false, String::new(), None, false),
        ),
    ]);
    let expected_node_layers: BTreeMap<String, String> = expected_parse
        .keys()
        .map(|k| (k.clone(), "PaintedTag".to_owned()))
        .collect();

    let plain_parts: Vec<PartTuple> = vec![
        (
            "OwnGold".to_owned(),
            [230, 180, 30, 255],
            false,
            [0, 100000, 300000, 500000],
        ),
        (
            "PlainRed".to_owned(),
            [210, 50, 40, 255],
            false,
            [0, 100000, 0, 200000],
        ),
    ];

    for (label, skp) in &sources {
        let brick_rgba = if *label == "writer" {
            [255, 255, 255, 255]
        } else {
            [184, 102, 84, 255]
        };
        let textured_parts: Vec<PartTuple> = vec![
            (
                "BrickTex".to_owned(),
                brick_rgba,
                true,
                [0, 10000, 0, 10000],
            ),
            (
                "OwnGold".to_owned(),
                [230, 180, 30, 255],
                false,
                [0, 100000, 300000, 500000],
            ),
        ];
        let expected_leaf_parts = BTreeMap::from([
            ("PlainComp".to_owned(), plain_parts.clone()),
            ("PlainGroup".to_owned(), plain_parts.clone()),
            ("TexturedComp".to_owned(), textured_parts.clone()),
            ("TexturedGroup".to_owned(), textured_parts.clone()),
            ("OuterTextured / InnerUnpainted".to_owned(), textured_parts),
        ]);
        let model = skp.parse(&opts)?;
        let mut parsed_nodes: BTreeMap<String, ParseRow> = BTreeMap::new();
        fn walk_model(
            model: &openskp::SkpModel,
            defn: &openskp::Definition,
            prefix: &str,
            out: &mut BTreeMap<String, ParseRow>,
        ) {
            for ins in &defn.instances {
                let cdef = &model.definitions[&ins.ref_idx.unwrap()];
                let p = if prefix.is_empty() {
                    ins.name.clone()
                } else {
                    format!("{prefix} / {}", ins.name)
                };
                let mat = ins.material_id.and_then(|id| model.material_by_id(id));
                let has_tex = mat
                    .and_then(|mt| mt.texture.as_ref())
                    .and_then(|tx| tx.data.as_ref())
                    .is_some_and(|d| !d.is_empty());
                out.insert(
                    p.clone(),
                    (
                        cdef.is_group,
                        ins.layer.clone(),
                        mat.map(|mt| mt.name.clone()),
                        has_tex,
                    ),
                );
                walk_model(model, cdef, &p, out);
            }
        }
        walk_model(&model, model.root(), "", &mut parsed_nodes);
        assert_eq!(parsed_nodes, expected_parse, "{label}");

        let baked = skp.build_scene(&opts)?;
        let instanced = skp.build_instanced_scene(&opts)?;
        assert_eq!(baked.textures.len(), 1, "{label}");
        assert_eq!(instanced.textures.len(), 1, "{label}");
        assert_eq!(baked.glb_primitives.len(), 10, "{label}");
        assert_eq!(instanced.mesh_resources.len(), 4, "{label}");

        let mut baked_layers = BTreeMap::new();
        fn walk_baked(
            node: &openskp::InstanceNode,
            prefix: &str,
            out: &mut BTreeMap<String, String>,
        ) {
            for ch in &node.children {
                let p = if prefix.is_empty() {
                    ch.name.clone()
                } else {
                    format!("{prefix} / {}", ch.name)
                };
                out.insert(p.clone(), ch.layer.clone());
                walk_baked(ch, &p, out);
            }
        }
        walk_baked(&baked.scene_hierarchy, "", &mut baked_layers);
        assert_eq!(baked_layers, expected_node_layers, "{label}");

        let mut inst_layers = BTreeMap::new();
        let mut inst_nodes = BTreeMap::new();
        fn walk_inst<'a>(
            node: &'a openskp::InstancedNode,
            prefix: &str,
            layers: &mut BTreeMap<String, String>,
            nodes: &mut BTreeMap<String, &'a openskp::InstancedNode>,
        ) {
            for ch in &node.children {
                let p = if prefix.is_empty() {
                    ch.name.clone()
                } else {
                    format!("{prefix} / {}", ch.name)
                };
                layers.insert(p.clone(), ch.layer.clone());
                nodes.insert(p.clone(), ch);
                walk_inst(ch, &p, layers, nodes);
            }
        }
        walk_inst(
            &instanced.scene_hierarchy,
            "",
            &mut inst_layers,
            &mut inst_nodes,
        );
        assert_eq!(inst_layers, expected_node_layers, "{label}");

        let part_tuple = |gm: &GltfMaterial, uvs: &[f32]| -> PartTuple {
            let pbr = &gm.pbr_metallic_roughness;
            let bcf = pbr.base_color_factor;
            let rgba = [
                (bcf[0] * 255.0).round() as i32,
                (bcf[1] * 255.0).round() as i32,
                (bcf[2] * 255.0).round() as i32,
                (bcf[3] * 255.0).round() as i32,
            ];
            let (u0, u1, v0, v1) = uv_bounds(uvs);
            let q = |x: f32| (f64::from(x) * 10000.0).round() as i64;
            assert!(gm.double_sided, "{label}");
            (
                gm.name.clone(),
                rgba,
                pbr.base_color_texture == Some(0),
                [q(u0), q(u1), q(v0), q(v1)],
            )
        };

        let mut baked_by_path: BTreeMap<String, Vec<PartTuple>> = BTreeMap::new();
        for p in &baked.glb_primitives {
            let meta = &baked.mesh_index[&p.geom_name];
            assert_eq!(meta.layer, "PaintedTag", "{label}");
            let rel = meta
                .path
                .strip_prefix("ROOT / ")
                .unwrap_or(&meta.path)
                .to_owned();
            baked_by_path
                .entry(rel)
                .or_default()
                .push(part_tuple(&baked.gltf_materials[p.material_index], &p.uvs));
        }
        for v in baked_by_path.values_mut() {
            v.sort();
        }
        assert_eq!(baked_by_path, expected_leaf_parts, "{label}");

        assert!(
            inst_nodes["OuterTextured"].mesh_resource_id.is_none(),
            "{label}"
        );
        assert_eq!(
            inst_nodes["TexturedComp"].mesh_resource_id,
            inst_nodes["OuterTextured / InnerUnpainted"].mesh_resource_id,
            "{label}"
        );
        assert_ne!(
            inst_nodes["PlainComp"].mesh_resource_id, inst_nodes["TexturedComp"].mesh_resource_id,
            "{label}"
        );

        let by_id: BTreeMap<_, _> = instanced
            .mesh_resources
            .iter()
            .map(|r| (r.id.as_str(), r))
            .collect();
        let mut inst_by_path: BTreeMap<String, Vec<PartTuple>> = BTreeMap::new();
        for k in expected_leaf_parts.keys() {
            let res = by_id[inst_nodes[k].mesh_resource_id.as_deref().unwrap()];
            let mut parts: Vec<PartTuple> = res
                .primitives
                .iter()
                .map(|pr| part_tuple(&instanced.gltf_materials[pr.material_index], &pr.uvs))
                .collect();
            parts.sort();
            inst_by_path.insert(k.clone(), parts);
        }
        assert_eq!(inst_by_path, expected_leaf_parts, "{label}");
    }
    Ok(())
}
