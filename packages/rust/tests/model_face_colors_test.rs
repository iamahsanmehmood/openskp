mod common;

use std::collections::BTreeSet;

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
