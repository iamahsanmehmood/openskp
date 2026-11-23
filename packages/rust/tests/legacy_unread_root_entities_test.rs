mod common;

use std::sync::{Arc, Mutex};

use openskp::create::{create, FaceOptions, DEFAULT_TEXT_LEADER};
use openskp::{LogLevel, ParseOptions, SkpFile};

// The classic root reader keeps what it read before the first record it can't
// parse and drops the rest of the root. SkpModel::unread_root_entities says
// how many entities that dropped and ParseOptions::log emits LogLevel::Warning
// when it is nonzero, so a partial read isn't silent.

#[test]
fn complete_files_report_none() {
    for name in [
        "legacy_annotations.skp",
        "capilla_quiroz_v17.skp",
        "gondola_v20.skp",
        "legacy_text_arrows_2017.skp",
        "nesting_2017.skp",
        "SU_File.skp",
    ] {
        let logs: Arc<Mutex<Vec<(LogLevel, String)>>> = Arc::default();
        let l = Arc::clone(&logs);
        let options = ParseOptions::default()
            .with_log(move |level, message| l.lock().unwrap().push((level, message.to_owned())));
        let model = SkpFile::open(common::fixture(name))
            .unwrap()
            .parse(&options)
            .unwrap();
        assert_eq!(model.unread_root_entities, 0, "{name}");
        assert!(
            logs.lock()
                .unwrap()
                .iter()
                .all(|(level, _)| *level != LogLevel::Warning),
            "{name}"
        );
    }
}

#[test]
fn an_unreadable_text_drops_the_rest_of_the_root_and_warns() {
    let mut builder = create();
    builder
        .add_face(
            &[
                [0.0, 0.0, 0.0],
                [10.0, 0.0, 0.0],
                [10.0, 10.0, 0.0],
                [0.0, 10.0, 0.0],
            ],
            &FaceOptions::default(),
        )
        .unwrap();
    builder
        .add_text("First", [0.0, 0.0, 0.0], DEFAULT_TEXT_LEADER)
        .unwrap();
    builder
        .add_text("Broken", [5.0, 0.0, 0.0], DEFAULT_TEXT_LEADER)
        .unwrap();
    builder
        .add_face(
            &[
                [20.0, 0.0, 0.0],
                [30.0, 0.0, 0.0],
                [30.0, 10.0, 0.0],
                [20.0, 10.0, 0.0],
            ],
            &FaceOptions::default(),
        )
        .unwrap();

    let mut bytes = builder.to_bytes().unwrap();
    let complete = SkpFile::from_buffer(bytes.clone())
        .parse(&ParseOptions::default())
        .unwrap();
    assert_eq!(complete.root().texts.len(), 2);
    assert_eq!(complete.unread_root_entities, 0);

    // Break the second text's string marker so the reader can't find its text.
    let marker = [0x01, 0xff, 0xfe, 0xff, 0x06, b'B', 0x00];
    let at = bytes
        .windows(marker.len())
        .position(|w| w == marker)
        .expect("second text string marker");
    bytes[at..at + 4].fill(0x00);

    let logs: Arc<Mutex<Vec<(LogLevel, String)>>> = Arc::default();
    let l = Arc::clone(&logs);
    let options = ParseOptions::default()
        .with_log(move |level, message| l.lock().unwrap().push((level, message.to_owned())));
    let model = SkpFile::from_buffer(bytes).parse(&options).unwrap();
    assert_eq!(model.root().texts.len(), 1);
    assert_eq!(model.root().texts[0].text, "First");
    assert!(model.unread_root_entities > 0);
    let unread_token = format!("{} unread", model.unread_root_entities);
    assert!(logs.lock().unwrap().iter().any(|(level, msg)| {
        *level == LogLevel::Warning && msg.contains("stopped early") && msg.contains(&unread_token)
    }));
}
