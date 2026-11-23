// Tag colour records (materials/Layer_*) in 2021+ files are excluded from
// SkpModel::materials while keeping tag colours on SkpModel::layers (#551).

mod common;

use std::collections::BTreeMap;

use common::fixture;
use openskp::{Color3, ParseOptions, SkpFile};

#[test]
fn arcs_2021_has_zero_materials_and_keeps_layer0_colour() {
    let model = SkpFile::open(fixture("arcs_2021.skp"))
        .unwrap()
        .parse(&ParseOptions::default())
        .unwrap();
    assert!(model.materials.is_empty());
    assert_eq!(model.layers.len(), 1);
    assert_eq!(model.layers[0].name, "Layer0");
    assert_eq!(model.layers[0].color, [255, 84, 84]);
}

#[test]
fn texture_tga_2021_lists_only_real_material() {
    let model = SkpFile::open(fixture("texture_tga_2021.skp"))
        .unwrap()
        .parse(&ParseOptions::default())
        .unwrap();
    assert_eq!(model.materials.len(), 1);
    assert_eq!(model.materials[0].name, "Tex_TGA");
    assert_eq!(model.layers.len(), 1);
    assert_eq!(model.layers[0].name, "Layer0");
    assert_eq!(model.layers[0].color, [255, 84, 84]);
}

#[test]
fn real_material_named_layer_prefix_is_preserved_alongside_tag_colour() {
    let model = SkpFile::open(fixture("sdk_saved_layer_named_material_2021.skp"))
        .unwrap()
        .parse(&ParseOptions::default())
        .unwrap();
    assert_eq!(model.materials.len(), 1);
    assert_eq!(model.materials[0].name, "Layer_Foo");
    assert_eq!(model.materials[0].color, [200, 10, 20, 255]);

    let mut layers = BTreeMap::<String, Color3>::new();
    for layer in &model.layers {
        layers.insert(layer.name.clone(), layer.color);
    }
    assert_eq!(layers["Foo"], [30, 140, 250]);
    assert_eq!(layers["Layer0"], [255, 84, 84]);
}
