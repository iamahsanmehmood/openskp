// Legacy (MFC) text labels anchored to an entity. Ported from the C++
// legacy_anchored_texts_test.cpp (openskp#473).
//
// 16 bytes after its font, a CText carries the connection block dimensions use,
// [u32 type][u32 4][point3d]: type 0 or 1 when the label is anchored to nothing (1 for a leader
// pointing at a free point stored inline), other types (2, 4, 5 and 7 seen) for a label anchored
// to an entity. As in a dimension, the block is followed by the anchored entity's ref and its
// instance paths, null and empty for types 0 and 1. MFC writes an object in full the first time
// it is referenced, so a path can hold a whole NEW instance: the component a label is anchored to,
// when the label comes first in its entity list. The reader skipped the block as bytes, so that
// instance never got its store-map slots and every later reference was off by them: the
// auditorium (CR4) models of the BRAS database, whose loudspeaker labels are anchored to the
// loudspeakers, did not open, and two of its coupled-room (CR1) models lost root entities (one
// read 5 of its 318). The entity list's declared count holds such an instance, but SketchUp does
// not always list it again with a back-reference.
//
// Synthetic bytes only. A vertex stands in for the anchored instance: its record is short, and
// the list's bookkeeping does not depend on the class.

mod common;

use std::collections::HashMap;

use common::{concat, f64s, fixture};
use openskp::internal::{
    read_legacy_entity_list, LegacyEntityListResult, LegacySlotEntry, LegacySlotTable,
};
use openskp::{ParseOptions, SkpFile};

// pre-registered
const TEXT: u64 = 1;
const FONT_CLASS: u64 = 2;
const FONT: u64 = 3;
const VERTEX_CLASS: u64 = 4;
const FIRST_FREE: u64 = 5;

fn u16le(n: u16) -> Vec<u8> {
    n.to_le_bytes().to_vec()
}

fn u32le(n: u32) -> Vec<u8> {
    n.to_le_bytes().to_vec()
}

/// A SketchUp 2015 archive's slot table that knows CText, a font and CVertex.
struct Slots {
    slots: HashMap<u64, LegacySlotEntry>,
    class_slot: HashMap<String, u64>,
    next: u64,
}

impl Slots {
    fn new() -> Self {
        let entry = |cls: bool, name: &str| LegacySlotEntry {
            cls,
            name: name.into(),
            schema: 0,
            v: None,
        };
        Slots {
            slots: HashMap::from([
                (TEXT, entry(true, "CText")),
                (FONT_CLASS, entry(true, "CSkFont")),
                (FONT, entry(false, "CSkFont")),
                (VERTEX_CLASS, entry(true, "CVertex")),
            ]),
            class_slot: HashMap::from([
                ("CText".to_owned(), TEXT),
                ("CSkFont".to_owned(), FONT_CLASS),
                ("CVertex".to_owned(), VERTEX_CLASS),
            ]),
            next: FIRST_FREE,
        }
    }

    fn read(&mut self, data: &[u8], count: u32) -> LegacyEntityListResult {
        read_legacy_entity_list(
            data,
            0,
            15,
            count,
            LegacySlotTable {
                slots: &mut self.slots,
                class_slot: &mut self.class_slot,
                next: &mut self.next,
            },
        )
        .unwrap()
    }
}

/// A new CText: its class tag and record. `refs` is what follows the connection block: the
/// anchored entity's ref and its instance paths.
fn text_record_with_label(kind: u32, point: [f64; 3], refs: &[u8], label: [f64; 3]) -> Vec<u8> {
    let refs = if refs.is_empty() {
        vec![0; 12]
    } else {
        refs.to_vec()
    };
    concat(&[
        &u16le(0x8000 | TEXT as u16),
        &[0, 0], // no attribute container
        &[0; 8],
        &[0, 0],             // draw block, layer 0
        &u16le(FONT as u16), // the font, a back-reference
        &[0; 16],            // screen fractions
        &u32le(kind),
        &u32le(4),
        &f64s(&point),
        &refs,
        &f64s(&label), // label position
        &f64s(&[-0.577, -0.577, -0.577]),
        &u32le(2),
        &[1, 0, 0, 0, 1, 0, 3, 0, 0, 0, 1], // arrow 3
        &[0xff, 0xfe, 0xff, 3, b'L', 0, b'S', 0, b'1', 0],
        &[0; 5],
    ])
}

fn text_record(kind: u32, point: [f64; 3], refs: &[u8]) -> Vec<u8> {
    text_record_with_label(kind, point, refs, [0.0, 0.0, 0.0])
}

fn new_vertex() -> Vec<u8> {
    concat(&[
        &u16le(0x8000 | VERTEX_CLASS as u16),
        &[0, 0],
        &f64s(&[1.0, 2.0, 3.0]),
    ])
}

/// Anchored to entity 9 inside an instance written here in full.
fn paths_with_new_instance() -> Vec<u8> {
    concat(&[&u16le(9), &[0, 0], &u32le(1), &new_vertex(), &u32le(0)])
}

/// No relationships, then a GUID and the definition's name.
fn definition_tail() -> Vec<u8> {
    concat(&[
        &[0; 4],
        &[0; 2],
        &[0; 16],
        &[0xff, 0xfe, 0xff, 2, b'L', 0, b'S', 0],
    ])
}

fn items(list: &[(u64, &str)]) -> Vec<(u64, String)> {
    list.iter().map(|&(s, n)| (s, n.to_owned())).collect()
}

#[test]
fn an_anchored_label_reads_the_instance_written_in_its_path() {
    let cases = [
        (2, [0.0, 0.0, 0.0]),
        (4, [0.0, 0.0, 0.0]),
        (5, [12.5, -3.0, 40.0]),
        (7, [1.0, 2.0, 3.0]),
    ];
    for (kind, point) in cases {
        let data = concat(&[
            &text_record(kind, point, &paths_with_new_instance()),
            &definition_tail(),
        ]);
        let mut slots = Slots::new();
        let res = slots.read(&data, 2);
        assert_eq!(
            res.items,
            items(&[(FIRST_FREE, "CText"), (FIRST_FREE + 1, "CVertex")]),
            "type {kind}"
        );
        assert_eq!(&*slots.slots[&(FIRST_FREE + 1)].name, "CVertex");
        assert_eq!(
            &data[res.next..],
            definition_tail().as_slice(),
            "type {kind}"
        );
    }
}

#[test]
fn a_free_label_reads_no_paths() {
    let data = concat(&[
        &text_record_with_label(1, [1.0, 2.0, 3.0], &[], [4.0, 5.0, 6.0]),
        &definition_tail(),
    ]);
    let mut slots = Slots::new();
    let res = slots.read(&data, 1);
    assert_eq!(res.items, items(&[(FIRST_FREE, "CText")]));
    assert_eq!(slots.next, FIRST_FREE + 1);
    let geom = &slots.slots[&FIRST_FREE]
        .v
        .as_ref()
        .unwrap()
        .annotation_text
        .as_ref()
        .unwrap()
        .geometry;
    assert_eq!(geom.point, Some([1.0, 2.0, 3.0]));
    assert_eq!(geom.label_point, Some([4.0, 5.0, 6.0]));
    assert_eq!(&data[res.next..], definition_tail().as_slice());
}

#[test]
fn a_vertex_anchored_label_resolves_only_without_extra_or_instance_paths() {
    let direct_refs = concat(&[&u16le(9), &[0, 0], &[0; 8]]);
    let direct = concat(&[
        &text_record(2, [0.0, 0.0, 0.0], &direct_refs),
        &definition_tail(),
    ]);
    let mut s1 = Slots::new();
    let r1 = s1.read(&direct, 1);
    let v1 = s1.slots[&FIRST_FREE]
        .v
        .as_ref()
        .unwrap()
        .annotation_text
        .as_ref()
        .unwrap()
        .vertex;
    assert_eq!(v1, Some(9));
    assert_eq!(&direct[r1.next..], definition_tail().as_slice());

    let extra_refs = concat(&[&u16le(9), &u16le(8), &[0; 8]]);
    let with_extra = concat(&[
        &text_record(2, [0.0, 0.0, 0.0], &extra_refs),
        &definition_tail(),
    ]);
    let mut s2 = Slots::new();
    let r2 = s2.read(&with_extra, 1);
    let v2 = s2.slots[&FIRST_FREE]
        .v
        .as_ref()
        .unwrap()
        .annotation_text
        .as_ref()
        .unwrap()
        .vertex;
    assert_eq!(v2, None);
    assert_eq!(&with_extra[r2.next..], definition_tail().as_slice());
}

#[test]
fn unanchored_vertex_scan_respects_label_bounds() {
    let crafted_x_bytes: [u8; 8] = [0x09, 0x00, 0x00, 0x00, 0x00, 0x00, 0x10, 0x40];
    let crafted_x = f64::from_le_bytes(crafted_x_bytes);
    let rec = concat(&[
        &u16le(0x8000 | TEXT as u16),
        &[0, 0],
        &[0; 8],
        &[0, 0],
        &u16le(FONT as u16),
        &[0; 6],
        &u32le(2),
        &u32le(4),
        &f64s(&[0.0, 0.0, 0.0]),
        &crafted_x_bytes,
        &f64s(&[5.0, 6.0]),
        &f64s(&[-0.577, -0.577, -0.577]),
        &u32le(2),
        &[1, 0, 0, 0, 1, 0, 3, 0, 0, 0, 1],
        &[0xff, 0xfe, 0xff, 3, b'L', 0, b'S', 0, b'1', 0],
        &[0; 5],
        &definition_tail(),
    ]);
    let mut slots = Slots::new();
    let res = slots.read(&rec, 1);
    let txt = slots.slots[&FIRST_FREE]
        .v
        .as_ref()
        .unwrap()
        .annotation_text
        .as_ref()
        .unwrap();
    assert_eq!(txt.vertex, None);
    assert_eq!(txt.geometry.label_point, Some([crafted_x, 5.0, 6.0]));
    assert_eq!(&rec[res.next..], definition_tail().as_slice());
}

#[test]
fn an_instance_written_in_a_label_and_listed_again_is_listed_once() {
    let data = concat(&[
        &text_record(2, [0.0, 0.0, 0.0], &paths_with_new_instance()),
        &u16le(FIRST_FREE as u16 + 1), // its back-reference
        &definition_tail(),
    ]);
    let mut slots = Slots::new();
    let res = slots.read(&data, 2);
    assert_eq!(
        res.items,
        items(&[(FIRST_FREE, "CText"), (FIRST_FREE + 1, "CVertex")])
    );
    assert_eq!(&data[res.next..], definition_tail().as_slice());
}

/// A path runs from the outermost instance in; the inner ones live in other definitions.
#[test]
fn only_the_outermost_instance_of_a_path_is_one_of_the_lists_items() {
    let refs = concat(&[
        &u16le(9),
        &[0, 0],
        &u32le(2),
        &new_vertex(),
        &new_vertex(),
        &u32le(0),
    ]);
    let data = concat(&[&text_record(2, [0.0, 0.0, 0.0], &refs), &definition_tail()]);
    let mut slots = Slots::new();
    let res = slots.read(&data, 2);
    assert_eq!(
        res.items,
        items(&[(FIRST_FREE, "CText"), (FIRST_FREE + 1, "CVertex")])
    );
    assert_eq!(&*slots.slots[&(FIRST_FREE + 2)].name, "CVertex"); // read, not listed
    assert_eq!(&data[res.next..], definition_tail().as_slice());
}

#[test]
fn classic_leader_and_screen_text_fixtures_match_sdk() {
    let model = SkpFile::open(fixture("classic_text_anchors_2017.skp"))
        .unwrap()
        .parse(&ParseOptions::default())
        .unwrap();
    let names: Vec<&str> = model.root().texts.iter().map(|t| t.text.as_str()).collect();
    assert_eq!(
        names,
        vec![
            "Free callout",
            "Vertex callout",
            "View callout",
            "Screen note"
        ]
    );

    let free = &model.root().texts[0];
    assert_eq!(free.point, Some([1.0, 2.0, 3.0]));
    assert_eq!(free.label_point, Some([5.0, 7.0, 9.0]));
    assert_eq!(free.screen_position, Some([0.0, 0.0]));
    assert_eq!(free.leader_type, 2);
    assert_eq!(free.arrow_type, 3);
    assert!(free.display_leader);
    assert!(!free.hidden);
    assert_eq!(free.layer, "");

    let vertex = &model.root().texts[1];
    assert_eq!(vertex.point, Some([20.0, 30.0, 0.0]));
    assert_eq!(vertex.label_point, Some([10.0, 12.0, 14.0]));
    assert_eq!(vertex.screen_position, Some([0.5, 0.5]));
    assert_eq!(vertex.leader_type, 2);
    assert_eq!(vertex.arrow_type, 4);
    assert!(vertex.display_leader);
    assert!(!vertex.hidden);
    assert_eq!(vertex.layer, "");

    let view = &model.root().texts[2];
    assert_eq!(view.point, Some([3.0, 4.0, 5.0]));
    assert_eq!(view.label_point, Some([6.0, 7.0, 8.0]));
    assert_eq!(view.screen_position, Some([0.5, 0.5]));
    assert_eq!(view.leader_type, 1);
    assert_eq!(view.arrow_type, 1);
    assert!(view.display_leader);
    assert!(!view.hidden);
    assert_eq!(view.layer, "");

    let screen = &model.root().texts[3];
    assert_eq!(screen.point, Some([0.0, 0.0, 0.0]));
    assert_eq!(screen.label_point, Some([0.0, 0.0, 0.0]));
    let sp = screen.screen_position.expect("screen_position");
    assert!((sp[0] - 0.35).abs() < 1e-9);
    assert!((sp[1] - 0.65).abs() < 1e-9);
    assert_eq!(screen.leader_type, 0);
    assert_eq!(screen.arrow_type, 0);
    assert!(screen.display_leader);
    assert!(!screen.hidden);
    assert_eq!(screen.layer, "");

    let v20 = SkpFile::open(fixture("sdk_saved_leaderless_text_edges_2020.skp"))
        .unwrap()
        .parse(&ParseOptions::default())
        .unwrap();
    assert_eq!(v20.root().texts.len(), 2);
    let title = &v20.root().texts[0];
    assert_eq!(title.text, "Screen title");
    assert_eq!(title.point, Some([0.0, 0.0, 0.0]));
    assert_eq!(title.label_point, Some([0.0, 0.0, 0.0]));
    assert_eq!(title.screen_position, Some([0.0, 0.0]));
    assert_eq!(title.leader_type, 0);
    assert_eq!(title.arrow_type, 3);
    assert!(!title.display_leader);

    let callout = &v20.root().texts[1];
    assert_eq!(callout.text, "Callout");
    assert_eq!(callout.point, Some([5.0, 5.0, 0.0]));
    assert_eq!(callout.label_point, Some([7.0, 7.0, 4.0]));
    assert_eq!(callout.screen_position, Some([0.0, 0.0]));
    assert_eq!(callout.leader_type, 2);
    assert_eq!(callout.arrow_type, 3);
    assert!(callout.display_leader);
}

#[test]
fn face_anchored_texts_match_sdk_in_classic_and_vff() {
    let assert_vec3 = |actual: Option<[f64; 3]>, expected: [f64; 3]| {
        let p = actual.expect("expected resolved point");
        assert!(
            (p[0] - expected[0]).abs() < 1e-11,
            "x: {p:?} != {expected:?}"
        );
        assert!(
            (p[1] - expected[1]).abs() < 1e-11,
            "y: {p:?} != {expected:?}"
        );
        assert!(
            (p[2] - expected[2]).abs() < 1e-11,
            "z: {p:?} != {expected:?}"
        );
    };

    for fixture_name in [
        "classic_face_text_anchors_2017.skp",
        "vff_face_text_anchors_2025.skp",
    ] {
        let model = SkpFile::open(fixture(fixture_name))
            .unwrap()
            .parse(&ParseOptions::default())
            .unwrap();
        let names: Vec<&str> = model.root().texts.iter().map(|t| t.text.as_str()).collect();
        assert_eq!(
            names,
            vec![
                "Root untextured",
                "Root textured",
                "On component",
                "On group"
            ]
        );
        assert_vec3(model.root().texts[0].point, [9.0, 10.0, 2.0]);
        assert_vec3(model.root().texts[0].label_point, [3.0, 4.0, 5.0]);
        assert_vec3(model.root().texts[1].point, [64.5, 3.6, 0.0]);
        assert_vec3(model.root().texts[1].label_point, [4.0, 5.0, 6.0]);
        assert_vec3(model.root().texts[2].point, [90.0, 56.0, 12.0]);
        assert_vec3(model.root().texts[2].label_point, [5.0, 6.0, 7.0]);
        assert_vec3(model.root().texts[3].point, [56.0, 32.0, 11.0]);
        assert_vec3(model.root().texts[3].label_point, [6.0, 7.0, 8.0]);

        let own_defs: Vec<_> = model
            .definitions
            .values()
            .filter(|d| d.name == "OwnTextPanel")
            .collect();
        assert_eq!(own_defs.len(), 1);
        assert_eq!(own_defs[0].texts.len(), 1);
        assert_eq!(own_defs[0].texts[0].text, "In component");
        assert_vec3(own_defs[0].texts[0].point, [3.0, 5.0, 1.0]);
        assert_vec3(own_defs[0].texts[0].label_point, [2.0, 3.0, 4.0]);
    }

    let capilla = SkpFile::open(fixture("capilla_quiroz_v17.skp"))
        .unwrap()
        .parse(&ParseOptions::default())
        .unwrap();
    let area: Vec<_> = capilla
        .root()
        .texts
        .iter()
        .filter(|t| t.text == "4.06 m²")
        .collect();
    assert_eq!(area.len(), 1);
    assert_vec3(
        area[0].point,
        [19.346940925615975, 154.92910008345638, 112.92862002631385],
    );
}
