use std::collections::{BTreeMap, BTreeSet};
use std::error::Error;

use openskp::create::{create, DefinitionOptions, FaceOptions, InstanceOptions, LayerOptions};
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
