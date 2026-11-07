// Textured and colorized materials in a classic file read as SketchUp reads them. Ported from the
// C++ legacy_textured_colorize_test.cpp (openskp#477, #485, #487).
//
// Fixture legacy_textured_colorize_2017.skp: four materials with the same 2x1 texture, made and
// saved as a SketchUp 2017 file by SketchUp's own library: plain (type 1, textured), tint and
// shift (type 2, colorized, colorize mode tint 1 and shift 0) and tint_half (a tint at opacity
// 0.5). The same file is a fixture of the other ports. No private content.

mod common;

use std::collections::BTreeMap;

use common::{fixture, read_fixture};
use openskp::{Material, ParseOptions, SkpFile};

#[test]
fn texture_type_colorize_mode_and_paint_colour() {
    let model = SkpFile::open(fixture("legacy_textured_colorize_2017.skp"))
        .unwrap()
        .parse(&ParseOptions::default())
        .unwrap();
    let by_name: BTreeMap<&str, &Material> = model
        .materials
        .iter()
        .map(|m| (m.name.as_str(), m))
        .collect();
    assert_eq!(by_name.len(), 4);

    let expect = |name: &str, rgb: [u8; 3], colorized: bool, mode: i32, transparency: f64| {
        let m = by_name[name];
        assert_eq!(&m.color[..3], &rgb, "{name}");
        assert_eq!(m.colorized, colorized, "{name}");
        assert_eq!(m.colorize_type, mode, "{name}");
        assert!(
            (m.transparency - transparency).abs() < 1e-9,
            "{name}: {}",
            m.transparency
        );
    };
    expect("plain", [120, 40, 120], false, 0, 1.0);
    expect("tint", [30, 160, 60], true, 1, 1.0);
    expect("shift", [200, 90, 20], true, 0, 1.0);
    expect("tint_half", [10, 20, 230], true, 1, 0.5);
}

/// blender-openskp#9: a SketchUp 2017 textured material stores two colours side by side and the
/// first one's alpha byte can be 0 (as in the box the report attached). Taking the colour and its
/// alpha from the first made every textured material fully transparent in the Blender add-on.
/// Here the first colour of "plain" (120, 40, 120) gets alpha 0, as in that file.
#[test]
fn colour_alpha_is_the_material_colours_not_the_image_averages() {
    let mut data = read_fixture("legacy_textured_colorize_2017.skp");
    // image average, 00, colour
    let pair = [0x78, 0x28, 0x78, 0xff, 0x00, 0x78, 0x28, 0x78, 0xff];
    let hits: Vec<usize> = data
        .windows(pair.len())
        .enumerate()
        .filter(|(_, w)| *w == pair)
        .map(|(i, _)| i)
        .collect();
    assert_eq!(hits.len(), 1);
    data[hits[0] + 3] = 0;
    let model = SkpFile::from_buffer(data)
        .parse(&ParseOptions::default())
        .unwrap();
    let plain = model
        .materials
        .iter()
        .find(|m| m.name == "plain")
        .expect("no material named plain");
    assert_eq!(plain.color, [120, 40, 120, 255]);
}
