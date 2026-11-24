mod common;

use std::sync::{Arc, Mutex};

use openskp::{LogLevel, ParseOptions, SkpFile};

#[test]
fn leaderless_text_before_shared_vertex_edges_matches_sdk() {
    let logs: Arc<Mutex<Vec<(LogLevel, String)>>> = Arc::default();
    let l = Arc::clone(&logs);
    let options = ParseOptions::default()
        .with_log(move |level, message| l.lock().unwrap().push((level, message.to_owned())));

    let skp = SkpFile::open(common::fixture("sdk_saved_leaderless_text_edges_2020.skp")).unwrap();
    let model = skp.parse(&options).unwrap();
    let scene = skp.build_scene(&ParseOptions::default()).unwrap();
    let instanced = skp.build_instanced_scene(&ParseOptions::default()).unwrap();

    assert_eq!(model.definitions.len(), 1);
    let post = model.definitions.values().next().unwrap();
    assert_eq!(post.name, "Post");
    assert!(!post.is_group);
    assert_eq!(model.root().faces.len(), 0);
    assert_eq!(model.root().edges.len(), 3);
    assert_eq!(model.root().instances.len(), 1);
    let texts: Vec<&str> = model.root().texts.iter().map(|t| t.text.as_str()).collect();
    assert_eq!(texts, vec!["Screen title", "Callout"]);
    assert_eq!(model.unread_root_entities, 0);
    assert_eq!(scene.glb_primitives.len(), 1);
    assert_eq!(instanced.mesh_resources.len(), 1);
    assert!(logs
        .lock()
        .unwrap()
        .iter()
        .all(|(level, _)| *level != LogLevel::Warning));
}

#[test]
fn root_edge_with_non_vertex_ref_reports_partial_read() {
    let mut raw =
        std::fs::read(common::fixture("sdk_saved_leaderless_text_edges_2020.skp")).unwrap();
    assert_eq!(raw[5323], 0x31);
    assert_eq!(raw[5324], 0x00);
    raw[5323] = 0x2f; // point 2nd root edge v1 at 1st root CEdge

    let logs: Arc<Mutex<Vec<(LogLevel, String)>>> = Arc::default();
    let l = Arc::clone(&logs);
    let options = ParseOptions::default()
        .with_log(move |level, message| l.lock().unwrap().push((level, message.to_owned())));

    let skp = SkpFile::from_buffer(raw);
    let model = skp.parse(&options).unwrap();
    let scene = skp.build_scene(&ParseOptions::default()).unwrap();
    let instanced = skp.build_instanced_scene(&ParseOptions::default()).unwrap();

    assert_eq!(model.root().instances.len(), 1);
    let texts: Vec<&str> = model.root().texts.iter().map(|t| t.text.as_str()).collect();
    assert_eq!(texts, vec!["Screen title", "Callout"]);
    assert_eq!(model.root().edges.len(), 1);
    assert_eq!(model.unread_root_entities, 2);
    assert_eq!(scene.glb_primitives.len(), 1);
    assert_eq!(instanced.mesh_resources.len(), 1);
    assert!(logs
        .lock()
        .unwrap()
        .iter()
        .any(|(level, msg)| *level == LogLevel::Warning && msg.contains("2 unread")));
}

#[test]
fn definition_edge_with_non_vertex_ref_returns_parse_error() {
    let mut raw =
        std::fs::read(common::fixture("sdk_saved_leaderless_text_edges_2020.skp")).unwrap();
    assert_eq!(raw[4039], 0x14);
    assert_eq!(raw[4040], 0x00);
    raw[4039] = 0x11; // point 2nd definition edge v1 at 1st definition CEdge

    let skp = SkpFile::from_buffer(raw);
    assert!(skp.parse(&ParseOptions::default()).is_err());
    assert!(skp.build_scene(&ParseOptions::default()).is_err());
    assert!(skp.build_instanced_scene(&ParseOptions::default()).is_err());
}
