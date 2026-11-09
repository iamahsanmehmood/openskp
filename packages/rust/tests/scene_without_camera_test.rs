// A scene saved without its camera ("Camera Location" unticked) is still a
// scene: it keeps its name and hidden layers, with eye/target/up unset.

mod common;

use openskp::{ParseOptions, SkpFile, SkpModel};

fn model() -> SkpModel {
    SkpFile::open(common::fixture("scene_without_camera_2021.skp"))
        .unwrap()
        .parse(&ParseOptions::default())
        .unwrap()
}

#[test]
fn every_scene_is_read() {
    let model = model();
    let names: Vec<&str> = model.pages.iter().map(|p| p.name.as_str()).collect();
    assert_eq!(names, ["With camera", "No camera", "Plain"]);
}

#[test]
fn a_saved_camera_is_still_read() {
    let model = model();
    assert_eq!(model.pages.len(), 3);
    assert_eq!(model.pages[0].eye, Some([100.0, -200.0, 80.0]));
    assert_eq!(model.pages[0].target, Some([5.0, 5.0, 0.0]));
}

#[test]
fn a_scene_without_a_camera_keeps_its_hidden_layers() {
    let model = model();
    assert_eq!(model.pages.len(), 3);
    assert!(model.pages[1].eye.is_none());
    assert!(model.pages[1].target.is_none());
    assert!(model.pages[1].up.is_none());
    assert_eq!(model.pages[1].hidden_layers, ["Furniture"]);
    assert!(model.pages[2].eye.is_none());
    assert!(model.pages[2].hidden_layers.is_empty());
}
