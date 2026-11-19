//! Port of `json_export_test.cpp`: openskp's canonical JSON export schema.

mod common;
mod gltf_helper;

use std::collections::BTreeMap;

use openskp::{
    export_json, to_json, to_json_string, InstanceNode, JsonValue, MeshMetadata, ParseOptions,
    Scene, SkpFile, SkpModel,
};

fn parse(name: &str) -> SkpModel {
    SkpFile::open(common::fixture(name))
        .unwrap()
        .parse(&ParseOptions::default())
        .unwrap()
}

fn build(name: &str) -> Scene {
    SkpFile::open(common::fixture(name))
        .unwrap()
        .build_scene(&ParseOptions::default())
        .unwrap()
}

fn get<'a>(v: &'a JsonValue, key: &str) -> &'a JsonValue {
    v.find(key).unwrap_or_else(|| panic!("missing key {key}"))
}

#[test]
fn matches_other_ports_ground_truth() {
    let model = parse("capilla_quiroz_v17.skp");
    let d = to_json(&model, None);

    assert!(d.is_object());
    assert_eq!(get(&d, "format_version").as_str(), Some("1.0"));
    assert_eq!(get(&d, "sketchup_version").as_str(), Some("{17.0.18899}"));
    assert_eq!(get(&d, "total_definitions").as_int(), Some(2));
    assert_eq!(get(&d, "total_layers").as_int(), Some(1));
    assert_eq!(get(&d, "total_meshes").as_int(), Some(0));

    let root = get(&d, "root");
    assert_eq!(get(root, "vertex_count").as_int(), Some(251));
    assert_eq!(get(root, "edge_count").as_int(), Some(390));
    assert_eq!(get(root, "face_count").as_int(), Some(146));
    let instances = get(root, "instances").as_array().unwrap();
    assert_eq!(instances.len(), 3);
    let first = &instances[0];
    assert_eq!(first.as_object().unwrap().len(), 4);
    for key in ["name", "ref_idx", "guid", "matrix"] {
        assert!(first.find(key).is_some(), "{key}");
    }

    let puerta = get(&d, "definitions")
        .as_object()
        .unwrap()
        .iter()
        .map(|(_, v)| v)
        .find(|v| get(v, "name").as_str() == Some("puerta"))
        .expect("puerta");
    assert_eq!(get(puerta, "id").as_int(), Some(40));
    assert_eq!(get(puerta, "vertex_count").as_int(), Some(64));
    assert_eq!(get(puerta, "edge_count").as_int(), Some(95));
    assert_eq!(get(puerta, "face_count").as_int(), Some(24));
    assert_eq!(get(puerta, "edges").as_array().unwrap().len(), 95);
    assert_eq!(get(puerta, "faces").as_array().unwrap().len(), 24);

    let layer_color = get(&get(&d, "layers").as_array().unwrap()[0], "color");
    assert_eq!(get(layer_color, "r").as_int(), Some(255));
    assert_eq!(get(layer_color, "g").as_int(), Some(84));
    assert_eq!(get(layer_color, "b").as_int(), Some(84));

    assert!(get(&d, "mesh_index").as_object().unwrap().is_empty());
    assert!(get(&d, "scene_hierarchy").is_null());

    // The serialized text parses back to the same tree (ints stay ints;
    // %.17g doubles round-trip exactly).
    let text = to_json_string(&d, 2);
    assert_eq!(gltf_helper::parse_json(&text), normalize(&d));
}

/// `%.17g` prints integral doubles without a decimal point, which a parser
/// reads back as an int; mirror that so trees compare equal.
fn normalize(v: &JsonValue) -> JsonValue {
    match v {
        JsonValue::Double(d)
            if d.fract() == 0.0 && d.abs() < 1e17 && !(*d == 0.0 && d.is_sign_negative()) =>
        {
            JsonValue::Int(*d as i64)
        }
        JsonValue::Double(d) if *d == 0.0 => JsonValue::Int(0),
        JsonValue::Array(a) => JsonValue::Array(a.iter().map(normalize).collect()),
        JsonValue::Object(o) => {
            JsonValue::Object(o.iter().map(|(k, v)| (k.clone(), normalize(v))).collect())
        }
        other => other.clone(),
    }
}

/// A modern (VFF) fixture the reader already handles end to end.
#[test]
fn serializes_a_real_model_and_round_trips() {
    let model = parse("Untitled.skp");
    let d = to_json(&model, None);
    assert_eq!(
        get(&d, "total_definitions").as_int(),
        Some(model.definitions.len() as i64)
    );
    assert_eq!(
        get(&d, "definitions").as_object().unwrap().len(),
        model.definitions.len()
    );
    assert_eq!(
        get(get(&d, "root"), "vertex_count").as_int(),
        Some(model.root().vertices.len() as i64)
    );
    assert!(get(&d, "scene_hierarchy").is_null());
    for indent in [0, 2] {
        let text = to_json_string(&d, indent);
        assert_eq!(gltf_helper::parse_json(&text), normalize(&d));
    }
}

fn hand_scene() -> Scene {
    let mut dicts = BTreeMap::new();
    dicts.insert(
        "plugin".to_string(),
        BTreeMap::from([("k".to_string(), "v".to_string())]),
    );
    let child = InstanceNode {
        name: "W1".into(),
        definition_name: "Wall".into(),
        layer: "Layer0".into(),
        position_mm: [1.0, 2.5, -3.0],
        attribute_dictionaries: dicts.clone(),
        ..Default::default()
    };
    let mut scene = Scene {
        scene_hierarchy: InstanceNode {
            name: "ROOT".into(),
            children: vec![child],
            ..Default::default()
        },
        ..Default::default()
    };
    scene.mesh_index.insert(
        "mesh_0".into(),
        MeshMetadata {
            name: "W1".into(),
            definition_name: "Wall".into(),
            layer: "Layer0".into(),
            position_mm: [1.0, 2.5, -3.0],
            properties: BTreeMap::from([("p".to_string(), "1".to_string())]),
            path: "ROOT/W1".into(),
            attribute_dictionaries: dicts,
        },
    );
    scene
}

#[test]
fn serializes_a_hand_built_scene() {
    let model = SkpModel::default();
    let scene = hand_scene();
    let d = to_json(&model, Some(&scene));
    assert_eq!(get(&d, "total_meshes").as_int(), Some(1));
    assert!(get(&d, "units").is_null());

    let compact = to_json_string(get(&d, "mesh_index"), 0);
    assert_eq!(
        compact,
        r#"{"mesh_0":{"name":"W1","definition_name":"Wall","layer":"Layer0","position_mm":[1,2.5,-3],"properties":{"p":"1"},"attribute_dictionaries":{"plugin":{"k":"v"}},"path":"ROOT/W1"}}"#
    );
    let hierarchy = get(&d, "scene_hierarchy");
    assert_eq!(get(hierarchy, "name").as_str(), Some("ROOT"));
    let w1 = &get(hierarchy, "children").as_array().unwrap()[0];
    assert_eq!(
        get(get(get(w1, "attribute_dictionaries"), "plugin"), "k").as_str(),
        Some("v")
    );
    assert!(get(w1, "properties").as_object().unwrap().is_empty());

    let root_text = to_json_string(get(&d, "root"), 2);
    assert_eq!(
        root_text,
        "{\n  \"id\": 0,\n  \"guid\": \"ROOT\",\n  \"name\": \"ROOT_MODEL\",\n  \"vertex_count\": 0,\n  \
         \"edge_count\": 0,\n  \"face_count\": 0,\n  \"vertices\": [],\n  \"edges\": [],\n  \"faces\": [],\n  \
         \"instances\": []\n}"
    );

    let path = std::env::temp_dir().join("openskp-rs-json-export.json");
    export_json(&model, &path, Some(&scene), 2).unwrap();
    assert_eq!(
        std::fs::read_to_string(&path).unwrap(),
        to_json_string(&d, 2)
    );
    std::fs::remove_file(&path).unwrap();
}

#[test]
fn includes_scene_hierarchy_and_mesh_index_when_scene_is_passed() {
    let model = parse("capilla_quiroz_v17.skp");
    let scene = build("capilla_quiroz_v17.skp");
    let d = to_json(&model, Some(&scene));
    assert_eq!(
        get(&d, "total_meshes").as_int(),
        Some(scene.mesh_index.len() as i64)
    );
    let hierarchy = get(&d, "scene_hierarchy");
    assert_eq!(get(hierarchy, "name").as_str(), Some("ROOT"));
    assert!(hierarchy.find("definition_name").is_some());
    assert!(hierarchy.find("position_mm").is_some());
    let mesh_index = get(&d, "mesh_index").as_object().unwrap();
    assert!(!mesh_index.is_empty());
    assert!(mesh_index[0].1.find("definition_name").is_some());
    assert!(mesh_index[0].1.find("position_mm").is_some());
}

fn find_w1(node: &JsonValue) -> Option<&JsonValue> {
    if node.find("name").and_then(JsonValue::as_str) == Some("W1") {
        return Some(node);
    }
    node.find("children")?.as_array()?.iter().find_map(find_w1)
}

/// Every attribute dictionary an instance carries must reach JSON output.
/// Untitled.skp's "W1" instance carries a real "steelframer-dict".
#[test]
fn includes_attribute_dictionaries_from_real_plugin_data() {
    let model = parse("Untitled.skp");
    let scene = build("Untitled.skp");
    let d = to_json(&model, Some(&scene));
    let w1 = find_w1(get(&d, "scene_hierarchy")).expect("W1");
    assert!(get(w1, "properties").as_object().unwrap().is_empty());
    let steel = get(get(w1, "attribute_dictionaries"), "steelframer-dict");
    assert_eq!(
        get(steel, "generator").as_str(),
        Some("SteelFramer::Engine::PanelGenerator")
    );
    assert_eq!(get(steel, "profile").as_str(), Some("362S200-43"));
}

#[test]
fn produces_non_empty_serialized_output() {
    let model = parse("capilla_quiroz_v17.skp");
    let scene = build("capilla_quiroz_v17.skp");
    let text = to_json_string(&to_json(&model, Some(&scene)), 2);
    assert!(!text.is_empty());
    assert!(text.contains("\"format_version\""));
}
