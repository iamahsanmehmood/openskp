// Tags with a line style in SketchUp 2019 and 2020 files (issue #514).
//
// Since SketchUp 2019 a tag can carry a line style ("Dash", "Dot", ...). A tag
// that has one points at a CCustomLineStyle object right after its own fields
// (both plain-colour and textured CLayer records): nothing for a tag without a
// line style, the style written in full the first time a tag uses it (a
// new-class record, then the object), a class-ref for a second style, and a
// back-ref for a style that an earlier tag already wrote. The style is an
// object in the walk like any other and takes a store-map slot.

mod common;

use std::fs;
use std::path::PathBuf;

use common::fixture;
use openskp::legacy::parse_legacy;
use openskp::{Color4, ParseOptions, SkpFile};

struct ExpectedModel {
    name: &'static str,
    from_cpp_fixtures: bool,
    tags: &'static [&'static str],
    materials: &'static [(&'static str, Color4)],
}

const MODELS: &[ExpectedModel] = &[
    ExpectedModel {
        name: "sdk_saved_tag_line_styles_2020.skp",
        from_cpp_fixtures: false,
        tags: &[
            "Tag 0 ",
            "Tag 1 Dash",
            "Tag 2 ",
            "Tag 3 Dash",
            "Tag 4 Dot",
            "Tag 5 Dash",
        ],
        materials: &[],
    },
    ExpectedModel {
        name: "sdk_saved_tag_line_styles_2019.skp",
        from_cpp_fixtures: false,
        tags: &[
            "Tag 0 ",
            "Tag 1 Dash",
            "Tag 2 ",
            "Tag 3 Dash",
            "Tag 4 Dot",
            "Tag 5 Dash",
        ],
        materials: &[],
    },
    ExpectedModel {
        name: "sdk_saved_tag_line_styles_materials_2020.skp",
        from_cpp_fixtures: false,
        tags: &[
            "Tag 0 Dash",
            "Tag 1 ",
            "Tag 2 Dot",
            "Tag 3 Dash",
            "Tag 4 Dot",
        ],
        materials: &[
            ("Paint 0", [10, 20, 30, 255]),
            ("Paint 1", [50, 20, 30, 255]),
            ("Paint 2", [90, 20, 30, 255]),
        ],
    },
    ExpectedModel {
        name: "sdk_saved_textured_tag_line_styles_2019.skp",
        from_cpp_fixtures: true,
        tags: &[
            "Tag 0 Tex Dash",
            "Tag 1 Tex ",
            "Tag 2 Tex Dot",
            "Tag 3 Tex Dash",
            "Tag 4 Plain Dot",
        ],
        materials: &[],
    },
    ExpectedModel {
        name: "sdk_saved_textured_tag_line_styles_2020.skp",
        from_cpp_fixtures: true,
        tags: &[
            "Tag 0 Tex Dash",
            "Tag 1 Tex ",
            "Tag 2 Tex Dot",
            "Tag 3 Tex Dash",
            "Tag 4 Plain Dot",
        ],
        materials: &[],
    },
];

fn resolve_fixture(m: &ExpectedModel) -> PathBuf {
    if m.from_cpp_fixtures {
        PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("../cpp/tests/fixtures")
            .join(m.name)
    } else {
        fixture(m.name)
    }
}

#[test]
fn model_whose_tags_have_line_styles_opens() {
    for expected in MODELS {
        let model = SkpFile::open(resolve_fixture(expected))
            .unwrap()
            .parse(&ParseOptions::default())
            .unwrap();
        let mut expected_layers = vec!["Layer0"];
        expected_layers.extend_from_slice(expected.tags);
        let actual_layers: Vec<&str> = model.layers.iter().map(|l| l.name.as_str()).collect();
        assert_eq!(actual_layers, expected_layers, "{}", expected.name);
        assert_eq!(
            model.root().faces.len(),
            expected.tags.len(),
            "{}",
            expected.name
        );
        let actual_materials: Vec<(&str, Color4)> = model
            .materials
            .iter()
            .map(|m| (m.name.as_str(), m.color))
            .collect();
        assert_eq!(actual_materials, expected.materials, "{}", expected.name);
    }
}

#[test]
fn each_face_stays_on_its_own_tag() {
    // Reading the line styles takes store-map slots between the tags, so a
    // tag's slot (the id its faces carry) is only right when the walk counted
    // them.
    for expected in MODELS {
        let bytes = fs::read(resolve_fixture(expected)).unwrap();
        let raw = parse_legacy(&bytes, &ParseOptions::default()).unwrap();
        let mut face_tags = Vec::new();
        for face in raw.root.builder.faces.values() {
            let id = face.layer_id.expect("legacy face carries layer_id");
            face_tags.push(raw.layer_id_to_name[&id].as_str());
        }
        assert_eq!(face_tags, expected.tags, "{}", expected.name);
    }
}
