// Tags with a line style in SketchUp 2019 and 2020 files (issue #514).
//
// Since SketchUp 2019 a tag can carry a line style ("Dash", "Dot", ...). A tag
// that has one points at a CCustomLineStyle object right after its own fields:
// nothing for a tag without a line style, the style written in full the first
// time a tag uses it (a new-class record, then the object), a class-ref for a
// second style, and a back-ref for a style that an earlier tag already wrote.
// The style is an object in the walk like any other and takes a store-map slot.

mod common;

use std::fs;

use common::fixture;
use openskp::legacy::parse_legacy;
use openskp::{ParseOptions, SkpFile};

const MODELS: &[(&str, &[&str])] = &[
    (
        "sdk_saved_tag_line_styles_2020.skp",
        &[
            "Tag 0 ",
            "Tag 1 Dash",
            "Tag 2 ",
            "Tag 3 Dash",
            "Tag 4 Dot",
            "Tag 5 Dash",
        ],
    ),
    (
        "sdk_saved_tag_line_styles_2019.skp",
        &[
            "Tag 0 ",
            "Tag 1 Dash",
            "Tag 2 ",
            "Tag 3 Dash",
            "Tag 4 Dot",
            "Tag 5 Dash",
        ],
    ),
    (
        "sdk_saved_tag_line_styles_materials_2020.skp",
        &[
            "Tag 0 Dash",
            "Tag 1 ",
            "Tag 2 Dot",
            "Tag 3 Dash",
            "Tag 4 Dot",
        ],
    ),
];

#[test]
fn model_whose_tags_have_line_styles_opens() {
    for &(name, expected_tags) in MODELS {
        let model = SkpFile::open(fixture(name))
            .unwrap()
            .parse(&ParseOptions::default())
            .unwrap();
        let mut expected_layers = vec!["Layer0"];
        expected_layers.extend_from_slice(expected_tags);
        let mut actual_layers = Vec::new();
        for layer in &model.layers {
            actual_layers.push(layer.name.as_str());
        }
        assert_eq!(actual_layers, expected_layers, "{name}");
        assert_eq!(model.root().faces.len(), expected_tags.len(), "{name}");
    }
}

#[test]
fn each_face_stays_on_its_own_tag() {
    // Reading the line styles takes store-map slots between the tags, so a
    // tag's slot (the id its faces carry) is only right when the walk counted
    // them.
    for &(name, expected_tags) in MODELS {
        let bytes = fs::read(fixture(name)).unwrap();
        let raw = parse_legacy(&bytes, &ParseOptions::default()).unwrap();
        let mut face_tags = Vec::new();
        for face in raw.root.builder.faces.values() {
            let id = face.layer_id.expect("legacy face carries layer_id");
            face_tags.push(raw.layer_id_to_name[&id].as_str());
        }
        assert_eq!(face_tags, expected_tags, "{name}");
    }
}
