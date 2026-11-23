mod common;

use openskp::{from_fragments, to_fragments, ParseOptions, SkpFile};

#[test]
fn scene_and_instanced_scene_pages_equal_parsed_model_pages() {
    let cases: &[(&str, &[&str])] = &[
        ("gondola_v20.skp", &["Cena1"]),
        ("sdk_saved_2020.skp", &["With camera", "No camera", "Plain"]),
        (
            "scene_without_camera_2021.skp",
            &["With camera", "No camera", "Plain"],
        ),
        ("nesting_2017.skp", &[]),
    ];

    for &(name, expected_names) in cases {
        let skp = SkpFile::open(common::fixture(name)).unwrap();
        let model = skp.parse(&ParseOptions::default()).unwrap();
        let scene = skp.build_scene(&ParseOptions::default()).unwrap();
        let instanced = skp.build_instanced_scene(&ParseOptions::default()).unwrap();

        let names: Vec<&str> = model.pages.iter().map(|p| p.name.as_str()).collect();
        assert_eq!(names, expected_names, "{name}");
        assert_eq!(scene.pages, model.pages, "{name}");
        assert_eq!(instanced.pages, model.pages, "{name}");
    }
}

#[test]
fn scene_pages_keep_native_inches_and_hidden_layers_and_clear_on_fragments() {
    let skp = SkpFile::open(common::fixture("scene_without_camera_2021.skp")).unwrap();
    let scene = skp.build_scene(&ParseOptions::default()).unwrap();
    let instanced = skp.build_instanced_scene(&ParseOptions::default()).unwrap();

    for pages in [&scene.pages, &instanced.pages] {
        assert_eq!(pages.len(), 3);
        assert_eq!(pages[0].eye, Some([100.0, -200.0, 80.0]));
        assert_eq!(pages[0].target, Some([5.0, 5.0, 0.0]));
        assert_eq!(pages[1].eye, None);
        assert_eq!(pages[1].hidden_layers, vec!["Furniture".to_string()]);
    }

    let reloaded = from_fragments(&to_fragments(&instanced, false)).unwrap();
    assert!(reloaded.pages.is_empty());
}
