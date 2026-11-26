mod common;

use std::collections::BTreeSet;

use openskp::create::{create, DocumentOptions, FaceOptions};
use openskp::{Color3, ParseOptions, SkpFile};

const WRITTEN: (Color3, Color3) = ([11, 122, 233], [201, 102, 3]);
const APP_DEFAULT: (Color3, Color3) = ([255, 255, 255], [164, 178, 187]);
const SDK_DEFAULT: (Color3, Color3) = ([225, 225, 200], [128, 128, 200]);

#[test]
fn face_colors_are_what_sketchup_reports() {
    let cases: &[(&str, (Color3, Color3))] = &[
        ("face_colors_v4.skp", WRITTEN),
        ("face_colors_v5.skp", WRITTEN),
        ("face_colors_v6.skp", WRITTEN),
        ("face_colors_2016.skp", WRITTEN),
        ("face_colors_2017.skp", WRITTEN),
        ("face_colors_2018.skp", WRITTEN),
        ("face_colors_2019.skp", WRITTEN),
        ("face_colors_2020.skp", WRITTEN),
        ("face_colors_2021.skp", WRITTEN),
        ("capilla_quiroz_v17.skp", APP_DEFAULT),
        ("gondola_v20.skp", APP_DEFAULT),
        ("zero_material_custom_layer_v18.skp", APP_DEFAULT),
        ("SU_File.skp", APP_DEFAULT),
        ("nesting_v6.skp", SDK_DEFAULT),
        ("nesting_2013.skp", SDK_DEFAULT),
        ("nesting_2021.skp", SDK_DEFAULT),
        ("legacy_v4_synthetic.skp", SDK_DEFAULT),
        ("legacy_v5_synthetic.skp", SDK_DEFAULT),
        ("legacy_v2019_synthetic.skp", SDK_DEFAULT),
    ];

    for &(name, (front, back)) in cases {
        let model = SkpFile::open(common::fixture(name))
            .unwrap()
            .parse(&ParseOptions::default())
            .unwrap();
        assert_eq!(model.face_front_color, Some(front), "{name}");
        assert_eq!(model.face_back_color, Some(back), "{name}");
    }
}

#[test]
fn sketchup_3_gives_none() {
    let model = SkpFile::open(common::fixture("legacy_v3_synthetic.skp"))
        .unwrap()
        .parse(&ParseOptions::default())
        .unwrap();
    assert_eq!(model.face_front_color, None);
    assert_eq!(model.face_back_color, None);
}

#[test]
fn current_rendering_options_not_saved_style() {
    let model = SkpFile::open(common::fixture("face_colors_2021.skp"))
        .unwrap()
        .parse(&ParseOptions::default())
        .unwrap();
    let style_pairs: BTreeSet<(Option<Color3>, Option<Color3>)> = model
        .styles
        .iter()
        .map(|s| (s.front_color, s.back_color))
        .collect();
    assert!(style_pairs.contains(&(Some(SDK_DEFAULT.0), Some(SDK_DEFAULT.1))));
    assert!(style_pairs.contains(&(Some(WRITTEN.0), Some(WRITTEN.1))));
    assert_eq!(model.face_front_color, Some(WRITTEN.0));
    assert_eq!(model.face_back_color, Some(WRITTEN.1));
}

#[test]
fn color_by_tag_matches_sketchup() {
    let cases: &[(&str, bool)] = &[
        ("color_by_tag_v4.skp", true),
        ("color_by_tag_v5.skp", true),
        ("color_by_tag_v6.skp", true),
        ("color_by_tag_2017.skp", true),
        ("color_by_tag_2019.skp", true),
        ("color_by_tag_2020.skp", true),
        ("color_by_tag_2020_off.skp", false),
        ("color_by_tag_2021.skp", true),
        ("color_by_tag_2021_style.skp", true),
        ("color_by_tag_2021_off.skp", false),
        ("legacy_v3_synthetic.skp", false),
        ("face_colors_v4.skp", false),
        ("face_colors_v5.skp", false),
        ("face_colors_v6.skp", false),
        ("face_colors_2016.skp", false),
        ("face_colors_2017.skp", false),
        ("face_colors_2018.skp", false),
        ("face_colors_2019.skp", false),
        ("face_colors_2020.skp", false),
        ("face_colors_2021.skp", false),
    ];
    for &(name, expected) in cases {
        let model = SkpFile::open(common::fixture(name))
            .unwrap()
            .parse(&ParseOptions::default())
            .unwrap();
        assert_eq!(model.color_by_tag, expected, "{name}");
    }
}

#[test]
fn color_by_tag_round_trips_classic_writer() {
    let quad = [
        [0.0, 0.0, 0.0],
        [10.0, 0.0, 0.0],
        [10.0, 10.0, 0.0],
        [0.0, 10.0, 0.0],
    ];
    for (setting, expected) in [(None, false), (Some(0.0), false), (Some(1.0), true)] {
        let mut builder = create();
        if let Some(v) = setting {
            let mut opts = DocumentOptions::default();
            opts.style_items.insert(7011, v);
            builder.set_document_options(&opts).unwrap();
        }
        builder.add_face(&quad, &FaceOptions::default()).unwrap();
        let bytes = builder.to_bytes().unwrap();
        let model = SkpFile::from_buffer(bytes)
            .parse(&ParseOptions::default())
            .unwrap();
        assert_eq!(model.color_by_tag, expected);
    }
}

#[test]
fn color_by_tag_does_not_change_scene_output() {
    for (on_name, off_name) in [
        ("color_by_tag_2020.skp", "color_by_tag_2020_off.skp"),
        ("color_by_tag_2021.skp", "color_by_tag_2021_off.skp"),
    ] {
        let skp_on = SkpFile::open(common::fixture(on_name)).unwrap();
        let skp_off = SkpFile::open(common::fixture(off_name)).unwrap();
        assert!(skp_on.parse(&ParseOptions::default()).unwrap().color_by_tag);
        assert!(
            !skp_off
                .parse(&ParseOptions::default())
                .unwrap()
                .color_by_tag
        );

        let scene_on = skp_on.build_scene(&ParseOptions::default()).unwrap();
        let scene_off = skp_off.build_scene(&ParseOptions::default()).unwrap();
        let colors_on: Vec<_> = scene_on
            .gltf_materials
            .iter()
            .map(|m| m.pbr_metallic_roughness.base_color_factor)
            .collect();
        let colors_off: Vec<_> = scene_off
            .gltf_materials
            .iter()
            .map(|m| m.pbr_metallic_roughness.base_color_factor)
            .collect();
        assert_eq!(colors_on, colors_off, "{on_name}");

        let inst_on = skp_on
            .build_instanced_scene(&ParseOptions::default())
            .unwrap();
        let inst_off = skp_off
            .build_instanced_scene(&ParseOptions::default())
            .unwrap();
        let inst_colors_on: Vec<_> = inst_on
            .gltf_materials
            .iter()
            .map(|m| m.pbr_metallic_roughness.base_color_factor)
            .collect();
        let inst_colors_off: Vec<_> = inst_off
            .gltf_materials
            .iter()
            .map(|m| m.pbr_metallic_roughness.base_color_factor)
            .collect();
        assert_eq!(inst_colors_on, inst_colors_off, "{on_name}");
    }
}
