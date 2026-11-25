use std::collections::{BTreeMap, BTreeSet};
use std::path::PathBuf;

use openskp::{EntityId, Page, ParseOptions, SkpFile, SkpModel};

const YEARS: [i32; 6] = [2015, 2016, 2017, 2018, 2019, 2020];

const SCENE_ORDER: [&str; 11] = [
    "Camera",
    "RenderingOptions",
    "ShadowInfo",
    "Axes",
    "Hidden",
    "HiddenGeometry",
    "HiddenObjects",
    "HiddenLayers",
    "SectionPlanes",
    "ShadowsCamera",
    "AxesCamera",
];

fn cpp_fixture(name: &str) -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../cpp/tests/fixtures")
        .join(name)
}

fn parse_year(year: i32) -> SkpModel {
    SkpFile::open(cpp_fixture(&format!("scene_capture_flags_{year}.skp")))
        .unwrap()
        .parse(&ParseOptions::default())
        .unwrap()
}

#[test]
fn classic_2015_to_2020_reads_all_scenes_with_cameras_and_hidden_layers() {
    for year in YEARS {
        let model = parse_year(year);
        let names: Vec<&str> = model.pages.iter().map(|p| p.name.as_str()).collect();
        assert_eq!(names, SCENE_ORDER, "{year}");
        let by_name: BTreeMap<&str, &Page> =
            model.pages.iter().map(|p| (p.name.as_str(), p)).collect();

        let cam = by_name["Camera"];
        assert_eq!(cam.eye, Some([120.0, -240.0, 90.0]), "{year}");
        assert_eq!(cam.target, Some([5.0, 5.0, 0.0]), "{year}");
        let cam_up = cam.up.expect("cam up");
        assert!((cam_up[0] - (-0.13407807210149128)).abs() < 1e-9, "{year}");
        assert!((cam_up[1] - 0.28564458839013357).abs() < 1e-9, "{year}");
        assert!((cam_up[2] - 0.9489100271917137).abs() < 1e-9, "{year}");
        assert!(!cam.parallel, "{year}");
        assert!((cam.fov - 42.0).abs() < 1e-9, "{year}");

        let shadows_cam = by_name["ShadowsCamera"];
        assert_eq!(shadows_cam.eye, Some([0.0, -300.0, 150.0]), "{year}");
        assert_eq!(shadows_cam.target, Some([0.0, 0.0, 0.0]), "{year}");
        let sh_up = shadows_cam.up.expect("shadows up");
        assert!(sh_up[0].abs() < 1e-9, "{year}");
        assert!((sh_up[1] - 0.44721359549995804).abs() < 1e-9, "{year}");
        assert!((sh_up[2] - 0.8944271909999161).abs() < 1e-9, "{year}");
        assert!(shadows_cam.parallel, "{year}");
        assert!((shadows_cam.ortho_height - 80.0).abs() < 1e-9, "{year}");

        let axes_cam = by_name["AxesCamera"];
        assert_eq!(axes_cam.eye, Some([-180.0, -180.0, 110.0]), "{year}");
        assert_eq!(axes_cam.target, Some([10.0, 10.0, 0.0]), "{year}");
        let ax_up = axes_cam.up.expect("axes up");
        assert!((ax_up[0] - 0.2678945632789157).abs() < 1e-9, "{year}");
        assert!((ax_up[1] - 0.2678945632789157).abs() < 1e-9, "{year}");
        assert!((ax_up[2] - 0.9254539458726179).abs() < 1e-9, "{year}");
        assert!(!axes_cam.parallel, "{year}");
        assert!((axes_cam.fov - 55.0).abs() < 1e-9, "{year}");

        for name in SCENE_ORDER {
            let p = by_name[name];
            if !matches!(name, "Camera" | "ShadowsCamera" | "AxesCamera") {
                assert!(p.eye.is_none(), "{year} {name}");
                assert!(p.target.is_none(), "{year} {name}");
            }
            if name == "HiddenLayers" {
                assert_eq!(p.hidden_layers, ["HiddenTag"], "{year}");
            } else {
                assert!(p.hidden_layers.is_empty(), "{year} {name}");
            }
        }
    }
}

#[test]
fn classic_2015_to_2020_resolves_hidden_entities_by_version() {
    for year in YEARS {
        let model = parse_year(year);
        let by_name: BTreeMap<&str, &Page> =
            model.pages.iter().map(|p| (p.name.as_str(), p)).collect();
        if year < 2017 {
            for p in &model.pages {
                assert!(p.hidden_entity_ids.is_empty(), "{year} {}", p.name);
            }
        } else {
            let crate_inst = model
                .root()
                .instances
                .iter()
                .find(|i| i.name == "Crate")
                .expect("Crate instance");
            assert_eq!(crate_inst.id, Some(30), "{year}");
            assert!(
                model.root().edges.values().any(|e| e.persistent_id == 21),
                "{year}"
            );
            let hidden_set: BTreeSet<EntityId> = by_name["Hidden"]
                .hidden_entity_ids
                .iter()
                .copied()
                .collect();
            assert_eq!(hidden_set, BTreeSet::from([21, 30]), "{year}");
            assert_eq!(by_name["HiddenGeometry"].hidden_entity_ids, [21], "{year}");
            assert_eq!(by_name["HiddenObjects"].hidden_entity_ids, [30], "{year}");
            for name in SCENE_ORDER {
                if !matches!(name, "Hidden" | "HiddenGeometry" | "HiddenObjects") {
                    assert!(by_name[name].hidden_entity_ids.is_empty(), "{year} {name}");
                }
            }
        }
    }
}
