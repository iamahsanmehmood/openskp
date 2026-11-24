use std::collections::BTreeMap;
use std::error::Error;

use openskp::create::{
    create, AttributeDict, AttributeValue, DefinitionOptions, FaceOptions, InstanceOptions,
};
use openskp::{ParseOptions, SkpFile};

const WALL_RECT: [[f64; 3]; 4] = [
    [0.0, 0.0, 0.0],
    [20.0, 0.0, 0.0],
    [20.0, 10.0, 0.0],
    [0.0, 10.0, 0.0],
];

fn build_fixture() -> Result<SkpFile, Box<dyn Error>> {
    let mut b = create();
    let def_opts = DefinitionOptions::default();
    let face_opts = FaceOptions::default();

    let mut wall = b.add_component_definition("Wall", &def_opts)?;
    wall.add_face(&WALL_RECT, &face_opts)?;
    let wall = wall.close()?;

    let w1 = InstanceOptions {
        name: Some("W1".to_owned()),
        attributes: AttributeDict::from([
            ("name".to_owned(), AttributeValue::from("CustomOverride")),
            ("sku".to_owned(), AttributeValue::from("B42")),
        ]),
        ..Default::default()
    };
    b.add_instance(wall, &w1)?;

    let w2 = InstanceOptions {
        name: Some("W2".to_owned()),
        translation: [30.0, 0.0, 0.0],
        attribute_dict_name: "TechSteel".to_owned(),
        attributes: AttributeDict::from([
            ("label".to_owned(), AttributeValue::from("TS-9")),
            ("count".to_owned(), AttributeValue::from(3i32)),
        ]),
        ..Default::default()
    };
    b.add_instance(wall, &w2)?;

    let w3 = InstanceOptions {
        name: Some("W3".to_owned()),
        translation: [60.0, 0.0, 0.0],
        attribute_dict_name: "dynamic_attributes".to_owned(),
        attributes: AttributeDict::from([
            ("name".to_owned(), AttributeValue::from("IgnoredDynamic")),
            ("lenx".to_owned(), AttributeValue::from(20i32)),
        ]),
        ..Default::default()
    };
    b.add_instance(wall, &w3)?;

    Ok(SkpFile::from_buffer(b.to_bytes()?))
}

#[test]
fn classic_instance_attrs_and_names_match_in_both_scene_types() {
    let skp = build_fixture().unwrap();
    let opts = ParseOptions::default();
    let baked = skp.build_scene(&opts).unwrap();
    let instanced = skp.build_instanced_scene(&opts).unwrap();

    let expected_w1 = BTreeMap::from([(
        "attributes".to_owned(),
        BTreeMap::from([
            ("name".to_owned(), "CustomOverride".to_owned()),
            ("sku".to_owned(), "B42".to_owned()),
        ]),
    )]);
    let expected_w2 = BTreeMap::from([(
        "TechSteel".to_owned(),
        BTreeMap::from([
            ("count".to_owned(), "3".to_owned()),
            ("label".to_owned(), "TS-9".to_owned()),
        ]),
    )]);
    let expected_w3_props = BTreeMap::from([
        ("lenx".to_owned(), "20".to_owned()),
        ("name".to_owned(), "IgnoredDynamic".to_owned()),
    ]);

    let bnodes = &baked.scene_hierarchy.children;
    assert_eq!(bnodes.len(), 3);
    assert_eq!(bnodes[0].name, "CustomOverride");
    assert_eq!(bnodes[1].name, "TS-9");
    assert_eq!(bnodes[2].name, "W3");
    assert!(!bnodes[0].name_is_generated);
    assert!(!bnodes[1].name_is_generated);
    assert!(!bnodes[2].name_is_generated);
    assert_eq!(bnodes[0].attribute_dictionaries, expected_w1);
    assert!(bnodes[0].properties.is_empty());
    assert_eq!(bnodes[1].attribute_dictionaries, expected_w2);
    assert!(bnodes[1].properties.is_empty());
    assert!(bnodes[2].attribute_dictionaries.is_empty());
    assert_eq!(bnodes[2].properties, expected_w3_props);

    let inodes = &instanced.scene_hierarchy.children;
    assert_eq!(inodes.len(), 3);
    assert_eq!(inodes[0].name, "CustomOverride");
    assert_eq!(inodes[1].name, "TS-9");
    assert_eq!(inodes[2].name, "W3");
    assert!(!inodes[0].name_is_generated);
    assert!(!inodes[1].name_is_generated);
    assert!(!inodes[2].name_is_generated);
    assert_eq!(inodes[0].attribute_dictionaries, expected_w1);
    assert!(inodes[0].properties.is_empty());
    assert_eq!(inodes[1].attribute_dictionaries, expected_w2);
    assert!(inodes[1].properties.is_empty());
    assert!(inodes[2].attribute_dictionaries.is_empty());
    assert_eq!(inodes[2].properties, expected_w3_props);

    let mut by_name = BTreeMap::new();
    for prim in baked.surface_primitives() {
        let meta = &baked.mesh_index[&prim.geom_name];
        by_name.insert(meta.name.clone(), meta.clone());
    }
    assert_eq!(by_name.len(), 3);
    assert_eq!(by_name["CustomOverride"].attribute_dictionaries, expected_w1);
    assert_eq!(by_name["TS-9"].attribute_dictionaries, expected_w2);
    assert!(by_name["W3"].attribute_dictionaries.is_empty());
    assert_eq!(by_name["W3"].properties, expected_w3_props);
}
