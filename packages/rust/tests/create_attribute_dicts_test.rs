//! Tests for multi-dictionary `attribute_dicts` support across all entity-creating options in
//! `openskp::create` (openskp#285).

use std::collections::BTreeMap;

use openskp::create::{
    create, AttributeDict, AttributeValue, CircleOptions, DefinitionOptions, FaceOptions,
    GroupInstanceOptions, GroupOptions, InstanceOptions, Point3,
};
use openskp::{ParseOptions, ParsedAttribute, SkpFile};

fn square() -> Vec<Point3> {
    vec![
        [0.0, 0.0, 0.0],
        [10.0, 0.0, 0.0],
        [10.0, 10.0, 0.0],
        [0.0, 10.0, 0.0],
    ]
}

fn dict(entries: &[(&str, AttributeValue)]) -> AttributeDict {
    entries
        .iter()
        .map(|(k, v)| ((*k).to_owned(), v.clone()))
        .collect()
}

fn utf16_offset(haystack: &[u8], ascii: &str) -> Option<usize> {
    let mut needle = Vec::with_capacity(ascii.len() * 2);
    for b in ascii.bytes() {
        needle.push(b);
        needle.push(0);
    }
    haystack.windows(needle.len()).position(|w| w == needle)
}

fn assert_ordered(haystack: &[u8], first: &str, second: &str) {
    assert!(utf16_offset(haystack, first).unwrap() < utf16_offset(haystack, second).unwrap());
}

#[test]
fn multiple_attribute_dicts_preserve_order_and_round_trip() {
    let mut builder = create();

    let stud = {
        let def_opts = DefinitionOptions {
            attribute_dicts: vec![
                ("z_def_first".to_owned(), dict(&[("part", "Stud".into())])),
                ("a_def_second".to_owned(), dict(&[("rev", 2.into())])),
            ],
            ..Default::default()
        };
        let mut def = builder.add_component_definition("Stud", &def_opts).unwrap();
        let face_opts = FaceOptions {
            attribute_dicts: vec![
                ("z_face_first".to_owned(), dict(&[("area", 12.5.into())])),
                ("a_face_second".to_owned(), dict(&[("tag", "panel".into())])),
            ],
            ..Default::default()
        };
        def.add_face(&square(), &face_opts).unwrap();
        let circle_opts = CircleOptions {
            attribute_dicts: vec![
                ("z_circ_first".to_owned(), dict(&[("r", 5.0.into())])),
                ("a_circ_second".to_owned(), dict(&[("seg", 24.into())])),
            ],
            ..Default::default()
        };
        def.add_circle([0.0, 0.0, 5.0], [0.0, 0.0, 1.0], 5.0, &circle_opts)
            .unwrap();
        def.close().unwrap()
    };

    let assembly = {
        let mut def = builder
            .add_component_definition("Assembly", &DefinitionOptions::default())
            .unwrap();
        let inst_opts = InstanceOptions {
            attribute_dicts: vec![
                ("z_nest_inst".to_owned(), dict(&[("idx", 1.into())])),
                ("a_nest_inst".to_owned(), dict(&[("code", "N1".into())])),
            ],
            ..Default::default()
        };
        def.add_instance(stud, &inst_opts).unwrap();
        let grp_opts = GroupInstanceOptions {
            name: Some("NestedGroup".to_owned()),
            attribute_dicts: vec![
                ("z_nest_grp".to_owned(), dict(&[("g", 1.into())])),
                ("a_nest_grp".to_owned(), dict(&[("h", 2.into())])),
            ],
            ..Default::default()
        };
        def.add_group_instance(stud, &grp_opts).unwrap();
        def.close().unwrap()
    };

    {
        let grp_opts = GroupOptions {
            name: Some("W-1".to_owned()),
            attribute_dicts: vec![
                ("z_grp_first".to_owned(), dict(&[("depth", 15.5.into())])),
                ("m_grp_second".to_owned(), dict(&[("sku", "GRP-1".into())])),
                ("a_grp_third".to_owned(), dict(&[("guide", 1.into())])),
            ],
            ..Default::default()
        };
        let mut grp = builder.add_group(&grp_opts).unwrap();
        grp.add_face(&square(), &FaceOptions::default()).unwrap();
        grp.close().unwrap();
    }

    let inst_opts = InstanceOptions {
        attribute_dicts: vec![
            (
                "z_inst_first".to_owned(),
                dict(&[("part", "Stud".into()), ("depth", 94.92.into())]),
            ),
            (
                "dynamic_attributes".to_owned(),
                dict(&[("count", 4.into())]),
            ),
            ("a_inst_third".to_owned(), dict(&[("guide", 1.into())])),
        ],
        ..Default::default()
    };
    builder.add_instance(assembly, &inst_opts).unwrap();

    let bytes = builder.to_bytes().unwrap();
    assert_ordered(&bytes, "z_def_first", "a_def_second");
    assert_ordered(&bytes, "z_face_first", "a_face_second");
    assert_ordered(&bytes, "z_circ_first", "a_circ_second");
    assert_ordered(&bytes, "z_nest_inst", "a_nest_inst");
    assert_ordered(&bytes, "z_nest_grp", "a_nest_grp");
    assert_ordered(&bytes, "z_grp_first", "m_grp_second");
    assert_ordered(&bytes, "m_grp_second", "a_grp_third");
    assert_ordered(&bytes, "z_inst_first", "dynamic_attributes");
    assert_ordered(&bytes, "dynamic_attributes", "a_inst_third");

    let model = SkpFile::from_buffer(bytes)
        .parse(&ParseOptions::default())
        .unwrap();
    assert_eq!(model.root().instances.len(), 2);

    let grp_inst = &model.root().instances[0];
    assert_eq!(
        grp_inst.attribute_dictionaries["m_grp_second"]["sku"],
        ParsedAttribute::String("GRP-1".to_owned())
    );
    assert_eq!(
        grp_inst.attribute_dictionaries["z_grp_first"]["depth"],
        ParsedAttribute::Float(15.5)
    );
    assert_eq!(
        grp_inst.attribute_dictionaries["a_grp_third"]["guide"],
        ParsedAttribute::Integer(1)
    );

    let root_inst = &model.root().instances[1];
    assert_eq!(root_inst.properties["count"], "4");
    assert_eq!(
        root_inst.attribute_dictionaries["z_inst_first"]["part"],
        ParsedAttribute::String("Stud".to_owned())
    );
    assert_eq!(
        root_inst.attribute_dictionaries["a_inst_third"]["guide"],
        ParsedAttribute::Integer(1)
    );
}

#[test]
fn passing_both_attributes_and_attribute_dicts_errors() {
    let mut builder = create();
    let chair = {
        let mut def = builder
            .add_component_definition("Chair", &DefinitionOptions::default())
            .unwrap();
        def.add_face(&square(), &FaceOptions::default()).unwrap();
        def.close().unwrap()
    };

    let single = BTreeMap::from([("a".to_owned(), AttributeValue::Int32(1))]);
    let multi = vec![("b".to_owned(), dict(&[("c", 2.into())]))];

    let bad_inst = InstanceOptions {
        attributes: single.clone(),
        attribute_dicts: multi.clone(),
        ..Default::default()
    };
    let err = builder.add_instance(chair, &bad_inst).unwrap_err();
    assert!(err.to_string().contains("not both"));

    let bad_grp = GroupOptions {
        attributes: single.clone(),
        attribute_dicts: multi.clone(),
        ..Default::default()
    };
    assert!(builder.add_group(&bad_grp).is_err());

    let bad_face = FaceOptions {
        attributes: single.clone(),
        attribute_dicts: multi.clone(),
        ..Default::default()
    };
    assert!(builder.add_face(&square(), &bad_face).is_err());

    let bad_circle = CircleOptions {
        attributes: single.clone(),
        attribute_dicts: multi.clone(),
        ..Default::default()
    };
    assert!(builder
        .add_circle([0.0, 0.0, 0.0], [0.0, 0.0, 1.0], 5.0, &bad_circle)
        .is_err());

    let bad_def = DefinitionOptions {
        attributes: single.clone(),
        attribute_dicts: multi.clone(),
        ..Default::default()
    };
    assert!(builder.add_component_definition("Bad", &bad_def).is_err());

    let mut outer = builder
        .add_component_definition("Outer", &DefinitionOptions::default())
        .unwrap();
    outer.add_face(&square(), &FaceOptions::default()).unwrap();
    assert!(outer.add_instance(chair, &bad_inst).is_err());
    let bad_grp_inst = GroupInstanceOptions {
        attributes: single,
        attribute_dicts: multi,
        ..Default::default()
    };
    assert!(outer.add_group_instance(chair, &bad_grp_inst).is_err());
}
