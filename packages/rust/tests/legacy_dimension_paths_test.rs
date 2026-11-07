// Port of openskp#384's Python fix to C++ (openskp#412): legacy (MFC) linear
// dimensions anchored inside groups.
//
// Each connection ref of a CDimensionLinear is followed by an entity ref and
// two lists of entity refs - the instance paths of the anchored entity. On
// loose geometry they are a null ref and two empty lists (the zeros a fixed
// 42/82-byte layout used to skip); anchored inside nested components they
// carry a ref per component, and a fixed-size read slid off the record,
// silently cutting the root entity list short. The first time an instance is
// referenced MFC writes it in full, so a path can also hold a whole NEW
// object.
//
// Synthetic bytes only: the real file that exposed this (a SketchUp 2017
// cabinet, 21 of 96 root instances lost) is private and is not committed.

mod common;

use common::concat;
use openskp::internal::{legacy_connection_paths, Error, ErrorKind};

/// A 6-byte big reference (0x7fff escape) to an existing object.
fn big(slot: u32) -> Vec<u8> {
    let s = slot.to_le_bytes();
    vec![0xff, 0x7f, s[0], s[1], s[2], s[3]]
}

fn u32le(n: u32) -> Vec<u8> {
    n.to_le_bytes().to_vec()
}

const TAIL: &[u8] = b"TAIL";

fn never_new(_: usize) -> Result<(u64, usize), Error> {
    Err(Error::runtime("unexpected new object"))
}

#[test]
fn loose_geometry_paths_are_empty_and_ten_bytes() {
    let data = concat(&[&[0, 0], &u32le(0), &u32le(0), TAIL]);
    let res = legacy_connection_paths(&data, 0, never_new).unwrap();
    assert!(res.extra.is_none());
    assert!(res.first.is_empty());
    assert!(res.second.is_empty());
    assert_eq!(res.next, 10); // the old fixed layout's zeros
}

#[test]
fn paths_inside_nested_components_are_read_ref_by_ref() {
    let data = concat(&[
        &big(0x0f69ca),
        &u32le(2),
        &big(0x166157),
        &big(0x165bbc),
        &u32le(2),
        &big(0x166157),
        &big(0x165bbd),
        TAIL,
    ]);
    let res = legacy_connection_paths(&data, 0, never_new).unwrap();
    assert_eq!(res.extra, Some(0x0f69ca));
    assert_eq!(res.first, [Some(0x166157), Some(0x165bbc)]);
    assert_eq!(res.second, [Some(0x166157), Some(0x165bbd)]);
    assert_eq!(res.first_new, [false, false]);
    assert_eq!(res.second_new, [false, false]);
    assert_eq!(&data[res.next..], TAIL);
}

#[test]
fn a_path_can_hold_a_whole_new_object() {
    // MFC serializes an object in full the first time it is referenced.
    let new_tag = [0xff, 0x7f, 0x67, 0x9f, 0x00, 0x80];
    let data = concat(&[&[0, 0], &u32le(1), &new_tag, b"OBJ", &u32le(0), TAIL]);
    let mut asked_at = 0;
    let res = legacy_connection_paths(&data, 0, |at| {
        asked_at = at;
        Ok((4242, at + 6 + 3)) // the object's own bytes
    })
    .unwrap();
    assert_eq!(asked_at, 2 + 4);
    assert!(res.extra.is_none());
    assert_eq!(res.first, [Some(4242)]);
    assert_eq!(res.first_new, [true]);
    assert!(res.second.is_empty());
    assert_eq!(&data[res.next..], TAIL);
}

#[test]
fn implausible_path_length_is_rejected() {
    let data = concat(&[&[0, 0], &u32le(100000)]);
    let err = legacy_connection_paths(&data, 0, never_new).unwrap_err();
    assert_eq!(err.kind, ErrorKind::Runtime);
}
