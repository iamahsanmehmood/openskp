// Scenes in a classic (pre-2021) file (openskp#445).
//
// gondola_v20.skp: SketchUp 2020's own save, one scene ("Cena1", capture
// flags 0x1FF) with a parallel-projection camera. sdk_saved_2019/2020.skp:
// built and saved through SketchUp's C API, which writes the scene class at a
// newer schema than the desktop application. Three scenes: "With camera" (eye
// 100, -200, 80 looking at 5, 5, 0), "No camera" (no saved camera, hides the
// layer "Furniture") and "Plain". SketchUp's own library reads the same scenes
// back. sdk_saved_sections_2019/2020.skp: the same model with two scenes, the
// first capturing everything a scene can (flags 0x1FF), so the second is only
// found if the first was read to its last byte. No private content.

mod common;

use common::{assert_near, fixture};
use openskp::{ParseOptions, SkpFile, SkpModel};

fn parse(name: &str) -> SkpModel {
    SkpFile::open(fixture(name))
        .unwrap()
        .parse(&ParseOptions::default())
        .unwrap()
}

#[test]
fn gondola_reads_its_scene_with_the_camera() {
    let model = parse("gondola_v20.skp");
    assert_eq!(model.pages.len(), 1);
    let p = &model.pages[0];
    assert_eq!(p.name, "Cena1");
    let eye = p.eye.expect("eye");
    assert_near(eye[0], 2093.78994893, 1e-6);
    assert_near(eye[1], 412.425280402, 1e-6);
    assert_near(eye[2], 135.404197356, 1e-6);
    let target = p.target.expect("target");
    assert_near(target[0], 1802.70152746, 1e-6);
    assert!(p.parallel);
    assert_near(p.ortho_height, 180.650232984, 1e-6);
    assert!(p.hidden_layers.is_empty());
}

#[test]
fn sdk_saved_scenes_are_read_with_their_cameras_and_hidden_layers() {
    for name in ["sdk_saved_2019.skp", "sdk_saved_2020.skp"] {
        let model = parse(name);
        assert_eq!(model.pages.len(), 3, "{name}");
        assert_eq!(model.pages[0].name, "With camera", "{name}");
        assert_eq!(model.pages[1].name, "No camera", "{name}");
        assert_eq!(model.pages[2].name, "Plain", "{name}");
        assert_eq!(model.pages[0].eye, Some([100.0, -200.0, 80.0]), "{name}");
        assert_eq!(model.pages[0].target, Some([5.0, 5.0, 0.0]), "{name}");
        assert_eq!(model.pages[0].fov, 30.0, "{name}");
        assert!(!model.pages[0].parallel, "{name}");
        assert!(model.pages[0].hidden_layers.is_empty(), "{name}");
        assert!(model.pages[1].eye.is_none(), "{name}");
        assert!(model.pages[1].target.is_none(), "{name}");
        assert_eq!(model.pages[1].hidden_layers, ["Furniture"], "{name}");
        assert!(model.pages[2].eye.is_none(), "{name}");
        assert!(model.pages[2].hidden_layers.is_empty(), "{name}");
    }
}

#[test]
fn a_scene_capturing_every_property_is_read_to_its_last_byte() {
    for name in ["sdk_saved_sections_2019.skp", "sdk_saved_sections_2020.skp"] {
        let model = parse(name);
        assert_eq!(model.pages.len(), 2, "{name}");
        assert_eq!(model.pages[0].name, "Everything", "{name}");
        assert_eq!(model.pages[1].name, "Plain", "{name}");
        assert_eq!(model.pages[0].eye, Some([100.0, -200.0, 80.0]), "{name}");
        assert_eq!(model.pages[0].hidden_layers, ["Furniture"], "{name}");
        assert!(model.pages[1].eye.is_none(), "{name}");
    }
}
