// A SketchUp 2020 model with nothing in it (no entities, no component definitions) opens.
// Ported from the C++ legacy_empty_model_test.cpp (openskp#486).
//
// Fixture legacy_empty_2020.skp: an empty model saved as a SketchUp 2020 file (SUModelVersion 13)
// by SketchUp's own library, the blank-template case. SketchUp 2020 writes an empty string record
// and zero padding where the definition count would be; with nothing in the model the only count
// behind it is the root entity count, which is 0, and the filler search skipped it, so the whole
// file was rejected with an invalid definition count. The same file is a fixture of the other
// ports. No private content.

mod common;

use common::fixture;
use openskp::internal::find_empty_root_after_v20_filler;
use openskp::{ParseOptions, SkpFile};

#[test]
fn an_empty_2020_model_parses_to_an_empty_model() {
    let model = SkpFile::open(fixture("legacy_empty_2020.skp"))
        .unwrap()
        .parse(&ParseOptions::default())
        .unwrap();
    assert_eq!(model.layers.len(), 1);
    assert_eq!(model.layers[0].name, "Layer0");
    assert!(model.definitions.is_empty());
    assert!(model.materials.is_empty());
    assert!(model.root().faces.is_empty());
    assert!(model.root().instances.is_empty());
}

/// 3 bytes of the previous record, the empty string record, `zeros` zero bytes, then `tail`.
fn filler(zeros: usize, tail: &[u8]) -> Vec<u8> {
    let mut bytes = vec![0, 0, 0, 0xff, 0xfe, 0xff, 0];
    bytes.extend(std::iter::repeat_n(0u8, zeros));
    bytes.extend_from_slice(tail);
    bytes
}

const NEXT_RECORD: &[u8] = &[0x58, 0xe7, 0x37, 0x6a];

#[test]
fn filler_returns_the_root_count_offset_thirteen_bytes_of_padding_behind_the_length_byte() {
    assert_eq!(
        find_empty_root_after_v20_filler(&filler(26, NEXT_RECORD), 0),
        Some(3 + 4 + 13)
    );
}

#[test]
fn filler_treats_a_non_empty_string_as_real_data() {
    let mut data = filler(30, NEXT_RECORD);
    data[6] = 5;
    assert_eq!(find_empty_root_after_v20_filler(&data, 0), None);
}

#[test]
fn filler_rejects_a_non_zero_byte_in_the_padding_or_the_count() {
    for at in 4..4 + 13 + 4 {
        let mut data = filler(26, NEXT_RECORD);
        data[3 + at] = 1;
        assert_eq!(
            find_empty_root_after_v20_filler(&data, 0),
            None,
            "byte {at}"
        );
    }
}

#[test]
fn filler_rejects_no_filler_nearby_and_a_truncated_filler() {
    assert_eq!(find_empty_root_after_v20_filler(&[0u8; 64], 0), None);
    assert_eq!(find_empty_root_after_v20_filler(&filler(5, &[]), 0), None);
}
