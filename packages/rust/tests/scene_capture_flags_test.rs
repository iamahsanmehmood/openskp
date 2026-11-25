use std::collections::{BTreeMap, BTreeSet};
use std::path::PathBuf;

use openskp::{
    EntityId, Page, ParseOptions, SkpFile, SkpModel, PAGE_USE_AXES, PAGE_USE_CAMERA,
    PAGE_USE_HIDDEN, PAGE_USE_HIDDEN_GEOMETRY, PAGE_USE_HIDDEN_LAYERS, PAGE_USE_HIDDEN_OBJECTS,
    PAGE_USE_RENDERING_OPTIONS, PAGE_USE_SECTION_PLANES, PAGE_USE_SHADOW_INFO,
};

const YEARS: [i32; 6] = [2015, 2016, 2017, 2018, 2019, 2020];

const HIDDEN_ENTITIES: [&str; 4] = [
    "scene_hidden_entities_2017.skp",
    "scene_hidden_entities_2019.skp",
    "scene_hidden_entities_2020.skp",
    "scene_hidden_entities_2024.skp",
];

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

type UseGetter = fn(&Page) -> Option<bool>;

const ALL_USE_PROPS: [(&str, UseGetter); 8] = [
    ("use_camera", Page::use_camera),
    ("use_rendering_options", Page::use_rendering_options),
    ("use_shadow_info", Page::use_shadow_info),
    ("use_axes", Page::use_axes),
    ("use_hidden_layers", Page::use_hidden_layers),
    ("use_section_planes", Page::use_section_planes),
    ("use_hidden_geometry", Page::use_hidden_geometry),
    ("use_hidden_objects", Page::use_hidden_objects),
];

fn local_fixture(name: &str) -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures")
        .join(name)
}

fn cpp_fixture(name: &str) -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../cpp/tests/fixtures")
        .join(name)
}

fn pages_by_name(path: PathBuf) -> BTreeMap<String, Page> {
    SkpFile::open(path)
        .unwrap()
        .parse(&ParseOptions::default())
        .unwrap()
        .pages
        .into_iter()
        .map(|p| (p.name.clone(), p))
        .collect()
}

fn parse_year(year: i32) -> SkpModel {
    SkpFile::open(cpp_fixture(&format!("scene_capture_flags_{year}.skp")))
        .unwrap()
        .parse(&ParseOptions::default())
        .unwrap()
}

#[test]
fn exposes_capture_flag_bit_constants() {
    assert_eq!(PAGE_USE_CAMERA, 0x001);
    assert_eq!(PAGE_USE_RENDERING_OPTIONS, 0x002);
    assert_eq!(PAGE_USE_SHADOW_INFO, 0x004);
    assert_eq!(PAGE_USE_AXES, 0x008);
    assert_eq!(PAGE_USE_HIDDEN, 0x010);
    assert_eq!(PAGE_USE_HIDDEN_LAYERS, 0x020);
    assert_eq!(PAGE_USE_SECTION_PLANES, 0x040);
    assert_eq!(PAGE_USE_HIDDEN_GEOMETRY, 0x080);
    assert_eq!(PAGE_USE_HIDDEN_OBJECTS, 0x100);
}

#[test]
fn empty_hidden_list_is_told_apart_and_saved() {
    for name in HIDDEN_ENTITIES {
        let pages = pages_by_name(cpp_fixture(name));
        assert!(pages["All"].hidden_entity_ids.is_empty(), "{name}");
        assert!(pages["Not saved"].hidden_entity_ids.is_empty(), "{name}");
        assert_eq!(pages["All"].capture_flags, Some(0x191), "{name}");
        assert_eq!(pages["All"].use_hidden_objects(), Some(true), "{name}");
        assert_eq!(pages["All"].use_hidden_geometry(), Some(true), "{name}");
        assert_eq!(pages["Not saved"].capture_flags, Some(0x001), "{name}");
        assert_eq!(
            pages["Not saved"].use_hidden_objects(),
            Some(false),
            "{name}"
        );
        assert_eq!(
            pages["Not saved"].use_hidden_geometry(),
            Some(false),
            "{name}"
        );
        assert_eq!(pages["Not saved"].use_camera(), Some(true), "{name}");

        for page in pages.values() {
            if !page.hidden_entity_ids.is_empty() {
                assert!(
                    page.use_hidden_objects() == Some(true)
                        || page.use_hidden_geometry() == Some(true),
                    "{name} {}",
                    page.name
                );
            }
        }
    }
}

#[test]
fn scenes_that_save_no_camera_and_built_scenes() {
    let path = local_fixture("scene_without_camera_2021.skp");
    let pages = pages_by_name(path.clone());
    assert_eq!(pages["With camera"].capture_flags, Some(0x001));
    assert_eq!(pages["With camera"].use_camera(), Some(true));
    assert_eq!(pages["With camera"].use_hidden_layers(), Some(false));
    assert_eq!(pages["No camera"].capture_flags, Some(0x020));
    assert_eq!(pages["No camera"].use_camera(), Some(false));
    assert_eq!(pages["No camera"].use_hidden_layers(), Some(true));
    assert!(pages["No camera"].eye.is_none());
    assert_eq!(pages["Plain"].capture_flags, Some(0x000));
    assert_eq!(pages["Plain"].use_camera(), Some(false));
    assert_eq!(pages["Plain"].use_hidden_layers(), Some(false));

    let skp = SkpFile::open(path).unwrap();
    let scene = skp.build_scene(&ParseOptions::default()).unwrap();
    let instanced = skp.build_instanced_scene(&ParseOptions::default()).unwrap();
    let flags: Vec<Option<u32>> = scene.pages.iter().map(|p| p.capture_flags).collect();
    assert_eq!(flags, vec![Some(0x001), Some(0x020), Some(0x000)]);
    assert_eq!(instanced.pages, scene.pages);
}

#[test]
fn all_or_nothing_and_application_saved_scene() {
    for name in ["sdk_saved_sections_2019.skp", "sdk_saved_sections_2020.skp"] {
        let pages = pages_by_name(local_fixture(name));
        let everything = &pages["Everything"];
        let plain = &pages["Plain"];
        assert_eq!(everything.capture_flags, Some(0x1FF), "{name}");
        assert_eq!(plain.capture_flags, Some(0x000), "{name}");
        for &(prop_name, getter) in &ALL_USE_PROPS {
            assert_eq!(getter(everything), Some(true), "{name} {prop_name}");
            assert_eq!(getter(plain), Some(false), "{name} {prop_name}");
        }
    }

    let gondola = pages_by_name(local_fixture("gondola_v20.skp"));
    assert_eq!(gondola.len(), 1);
    let page = gondola.values().next().unwrap();
    assert_eq!(page.capture_flags, Some(0x1FF));
    assert_eq!(page.use_hidden_geometry(), Some(true));
}

#[test]
fn older_combined_bit_and_unset_flags() {
    let old = Page {
        capture_flags: Some(0x011),
        ..Default::default()
    };
    assert_eq!(old.use_hidden_geometry(), Some(true));
    assert_eq!(old.use_hidden_objects(), Some(true));

    let split = Page {
        capture_flags: Some(0x111),
        ..Default::default()
    };
    assert_eq!(split.use_hidden_objects(), Some(true));
    assert_eq!(split.use_hidden_geometry(), Some(false));

    let unset = Page::default();
    assert_eq!(unset.capture_flags, None);
    for &(prop_name, getter) in &ALL_USE_PROPS {
        assert_eq!(getter(&unset), None, "{prop_name}");
    }
}

#[test]
fn classic_2015_to_2020_reads_all_scenes_with_cameras_and_hidden_layers() {
    let expected: [(&str, u32, &[&str]); 11] = [
        ("Camera", 0x001, &["use_camera"]),
        ("RenderingOptions", 0x002, &["use_rendering_options"]),
        ("ShadowInfo", 0x004, &["use_shadow_info"]),
        ("Axes", 0x008, &["use_axes"]),
        (
            "Hidden",
            0x190,
            &["use_hidden_geometry", "use_hidden_objects"],
        ),
        ("HiddenGeometry", 0x090, &["use_hidden_geometry"]),
        ("HiddenObjects", 0x110, &["use_hidden_objects"]),
        ("HiddenLayers", 0x020, &["use_hidden_layers"]),
        ("SectionPlanes", 0x040, &["use_section_planes"]),
        ("ShadowsCamera", 0x005, &["use_shadow_info", "use_camera"]),
        ("AxesCamera", 0x009, &["use_axes", "use_camera"]),
    ];

    for year in YEARS {
        let model = parse_year(year);
        let names: Vec<&str> = model.pages.iter().map(|p| p.name.as_str()).collect();
        assert_eq!(names, SCENE_ORDER, "{year}");
        let by_name: BTreeMap<&str, &Page> =
            model.pages.iter().map(|p| (p.name.as_str(), p)).collect();

        for &(scene_name, want_flags, true_props) in &expected {
            let p = by_name[scene_name];
            assert_eq!(p.capture_flags, Some(want_flags), "{year} {scene_name}");
            for &(prop_name, getter) in &ALL_USE_PROPS {
                assert_eq!(
                    getter(p),
                    Some(true_props.contains(&prop_name)),
                    "{year} {scene_name}.{prop_name}"
                );
            }
        }

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
