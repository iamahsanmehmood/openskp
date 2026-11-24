use std::collections::{BTreeMap, BTreeSet};
use std::path::PathBuf;

use openskp::{Definition, EntityId, Page, SkpFile, SkpModel};

const FILES: [&str; 4] = [
    "scene_hidden_entities_2017.skp",
    "scene_hidden_entities_2019.skp",
    "scene_hidden_entities_2020.skp",
    "scene_hidden_entities_2024.skp",
];

fn cpp_fixture(name: &str) -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../cpp/tests/fixtures")
        .join(name)
}

fn hidden(page: &Page) -> BTreeSet<EntityId> {
    page.hidden_entity_ids.iter().copied().collect()
}

fn placement_ids(model: &SkpModel) -> BTreeMap<String, EntityId> {
    let mut ids = BTreeMap::new();
    let mut add = |d: &Definition| {
        for inst in &d.instances {
            if let Some(id) = inst.id {
                ids.insert(inst.name.clone(), id);
            }
        }
    };
    add(&model.root);
    for d in model.definitions.values() {
        add(d);
    }
    ids
}

#[test]
fn each_scene_lists_what_it_hides() {
    for name in FILES {
        let model = SkpFile::open(cpp_fixture(name)).unwrap().parse().unwrap();
        assert_eq!(model.pages.len(), 6, "{name}");
        assert_eq!(model.pages[0].name, "All");
        assert!(model.pages[0].hidden_entity_ids.is_empty());
        assert_eq!(model.pages[1].name, "No B, no crate");
        assert_eq!(hidden(&model.pages[1]), BTreeSet::from([71, 73]));
        assert_eq!(model.pages[2].name, "No A, no crate");
        assert_eq!(hidden(&model.pages[2]), BTreeSet::from([70, 73]));
        assert_eq!(model.pages[4].name, "Edge");
        assert_eq!(hidden(&model.pages[4]), BTreeSet::from([17]));
        assert_eq!(model.pages[5].name, "Not saved");
        assert!(model.pages[5].hidden_entity_ids.is_empty());
    }
}

#[test]
fn ids_resolve_through_placements() {
    for name in FILES {
        let model = SkpFile::open(cpp_fixture(name)).unwrap().parse().unwrap();
        assert_eq!(model.pages.len(), 6);
        let ids = placement_ids(&model);
        let part_a = ids["Part A"];
        let part_b = ids["Part B"];
        let crate_id = ids["Crate"];
        assert_eq!(hidden(&model.pages[1]), BTreeSet::from([part_b, crate_id]));
        assert_eq!(hidden(&model.pages[2]), BTreeSet::from([part_a, crate_id]));
    }
}

#[test]
fn hidden_edge_is_box_edge() {
    for name in FILES {
        let model = SkpFile::open(cpp_fixture(name)).unwrap().parse().unwrap();
        let found_edge = model.root.edges.values().any(|e| e.persistent_id == 17);
        let found_face = model.root.faces.values().any(|f| f.persistent_id > 0);
        assert!(found_edge, "{name}");
        assert!(found_face, "{name}");
    }
}

#[test]
fn a_2020_or_newer_file_keeps_nested_placement() {
    for name in [
        "scene_hidden_entities_2020.skp",
        "scene_hidden_entities_2024.skp",
    ] {
        let model = SkpFile::open(cpp_fixture(name)).unwrap().parse().unwrap();
        assert_eq!(model.pages.len(), 6);
        assert_eq!(model.pages[3].name, "No C");
        assert_eq!(hidden(&model.pages[3]), BTreeSet::from([100]));
        assert_eq!(placement_ids(&model)["Part C"], 100);
    }
}

#[test]
fn a_2017_file_keeps_none_like_sketchup() {
    let path = cpp_fixture("scene_hidden_entities_2017.skp");
    let model = SkpFile::open(path).unwrap().parse().unwrap();
    assert_eq!(model.pages.len(), 6);
    assert!(model.pages[3].hidden_entity_ids.is_empty());
}

#[test]
fn gondola_scene_lists_hidden_edges() {
    let path = cpp_fixture("gondola_v20.skp");
    let model = SkpFile::open(path).unwrap().parse().unwrap();
    assert_eq!(model.pages.len(), 1);
    assert_eq!(model.pages[0].hidden_entity_ids.len(), 12);
}
