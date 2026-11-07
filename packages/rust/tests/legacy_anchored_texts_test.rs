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

use common::{concat, f64s};
use openskp::internal::{
    read_legacy_entity_list, LegacyEntityListResult, LegacySlotEntry, LegacySlotTable,
};

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
fn text_record(kind: u32, point: [f64; 3], refs: &[u8]) -> Vec<u8> {
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
        &f64s(&[0.0, 0.0, 0.0]), // label position
        &f64s(&[-0.577, -0.577, -0.577]),
        &u32le(2),
        &[1, 0, 0, 0, 1, 0, 3, 0, 0, 0, 1], // arrow 3
        &[0xff, 0xfe, 0xff, 3, b'L', 0, b'S', 0, b'1', 0],
        &[0; 5],
    ])
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
    let data = concat(&[&text_record(1, [1.0, 2.0, 3.0], &[]), &definition_tail()]);
    let mut slots = Slots::new();
    let res = slots.read(&data, 1);
    assert_eq!(res.items, items(&[(FIRST_FREE, "CText")]));
    assert_eq!(slots.next, FIRST_FREE + 1);
    assert_eq!(&data[res.next..], definition_tail().as_slice());
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
