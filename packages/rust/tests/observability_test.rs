mod common;

use std::sync::{Arc, Mutex};

use openskp::{LogLevel, ParseOptions, ParseProgress, ParseStage, SkpFile, SkpParseError};

#[derive(Debug)]
struct Inner;

impl std::fmt::Display for Inner {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str("inner")
    }
}

impl std::error::Error for Inner {}

#[test]
fn errors_context_and_cause() {
    let cause: openskp::errors::ErrorCause = Arc::new(Inner);
    let e = SkpParseError::with_context(
        "boom",
        Some(ParseStage::TlvWalk),
        Some(3),
        Some(10),
        Some("F601".into()),
        None,
        None,
        Some(Arc::clone(&cause)),
    );
    let what = e.to_string();
    assert!(what.contains("stage=tlv_walk"), "{what}");
    assert!(what.contains("record=3/10"), "{what}");
    assert!(Arc::ptr_eq(e.cause().unwrap(), &cause));
    assert_eq!(std::error::Error::source(&e).unwrap().to_string(), "inner");
    let full = SkpParseError::with_context("m", None, None, None, None, Some(0x1f), Some(-4), None);
    assert_eq!(full.to_string(), "m | offset=0x1f | definitionId=-4");
}

#[test]
fn observability_callbacks_are_optional() {
    assert!(SkpFile::from_buffer(vec![b'A'; 20])
        .parse(&ParseOptions::default())
        .is_err());
    SkpFile::open(common::fixture("Untitled.skp"))
        .unwrap()
        .parse(&ParseOptions::default())
        .unwrap();
}

#[test]
fn vff_parse_emits_expected_logs_and_progress() {
    let logs: Arc<Mutex<Vec<(LogLevel, String)>>> = Arc::default();
    let progress: Arc<Mutex<Vec<ParseProgress>>> = Arc::default();
    let (l, p) = (Arc::clone(&logs), Arc::clone(&progress));
    let options = ParseOptions::default()
        .with_log(move |level, message| l.lock().unwrap().push((level, message.to_owned())))
        .with_progress(move |value| p.lock().unwrap().push(*value));

    SkpFile::open(common::fixture("Untitled.skp"))
        .unwrap()
        .parse(&options)
        .unwrap();

    let logs = logs.lock().unwrap();
    assert_eq!(logs.first().unwrap().0, LogLevel::Information);
    assert!(logs.first().unwrap().1.contains("Parsing buffer"));
    assert!(logs.iter().any(|(_, m)| m.contains("Parse complete")));
    let progress = progress.lock().unwrap();
    let last = progress.last().unwrap();
    assert_eq!(last.stage, ParseStage::TlvWalk);
    assert_eq!(last.current, last.total);
}

#[test]
fn parse_emits_expected_logs() {
    let logs: Arc<Mutex<Vec<(LogLevel, String)>>> = Arc::default();
    let l = Arc::clone(&logs);
    let options = ParseOptions::default()
        .with_log(move |level, message| l.lock().unwrap().push((level, message.to_owned())));

    SkpFile::open(common::fixture("capilla_quiroz_v17.skp"))
        .unwrap()
        .parse(&options)
        .unwrap();

    let logs = logs.lock().unwrap();
    assert!(logs.len() >= 4);
    assert_eq!(logs.first().unwrap().0, LogLevel::Information);
    assert!(logs.first().unwrap().1.contains("Parsing buffer"));
    assert!(logs
        .iter()
        .any(|(level, m)| *level == LogLevel::Debug && m.contains("legacy MFC")));
    assert!(logs.iter().any(|(_, m)| m.contains("Parse complete")));
}

#[test]
fn scene_build_emits_expected_logs() {
    let messages: Arc<Mutex<Vec<String>>> = Arc::default();
    let m = Arc::clone(&messages);
    let options = ParseOptions::default()
        .with_log(move |_, message| m.lock().unwrap().push(message.to_owned()));

    SkpFile::open(common::fixture("SU_File.skp"))
        .unwrap()
        .build_scene(&options)
        .unwrap();

    let messages = messages.lock().unwrap();
    assert!(messages.iter().any(|m| m.contains("Building scene")));
    assert!(messages.iter().any(|m| m.contains("Scene build complete")));
}

#[test]
fn progress_includes_legacy_definitions() {
    let progress: Arc<Mutex<Vec<ParseProgress>>> = Arc::default();
    let p = Arc::clone(&progress);
    let options =
        ParseOptions::default().with_progress(move |value| p.lock().unwrap().push(*value));

    let model = SkpFile::open(common::fixture("capilla_quiroz_v17.skp"))
        .unwrap()
        .parse(&options)
        .unwrap();

    let progress = progress.lock().unwrap();
    assert!(!progress.is_empty());
    let found = progress
        .iter()
        .find(|v| v.stage == ParseStage::LegacyDefs)
        .expect("legacy_defs progress");
    assert_eq!(found.current, model.definitions.len() as u64);
    assert_eq!(found.total, model.definitions.len() as u64);
}

#[test]
fn errors_invalid_buffer_reports_header_stage() {
    let error = SkpFile::from_buffer(vec![b'A'; 20])
        .parse(&ParseOptions::default())
        .unwrap_err();
    assert_eq!(error.stage(), Some(ParseStage::Header));
}

#[test]
fn parser_paths_rejects_existing_file_with_wrong_extension() {
    let cargo_toml = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("Cargo.toml");
    let err = SkpFile::open(cargo_toml).unwrap_err();
    assert_eq!(err.kind(), std::io::ErrorKind::InvalidInput);
}
