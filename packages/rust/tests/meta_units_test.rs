// SkpModel::units - the model's unit-system string, read from meta/meta.dat
// in VFF files: the same low-level TLV framing as model.dat, one flat record
// list wrapped in a single outer record (tag 0x6400); tag 0x6D00 carries the
// units string as plain text.

mod common;

use common::{bytes, tlv};
use openskp::internal::read_meta_units;

fn hex_to_bytes(hex: &str) -> Vec<u8> {
    (0..hex.len() / 2)
        .map(|i| u8::from_str_radix(&hex[i * 2..i * 2 + 2], 16).unwrap())
        .collect()
}

#[test]
fn extracts_units_from_exact_real_fixture_bytes() {
    // The leading records of the real meta/meta.dat payload from Untitled.skp
    // (SketchUp 25.0.575), byte-for-byte, truncated right after the "6D00"
    // units record. The outer record's declared length matches the truncated
    // payload (242 bytes).
    let payload = hex_to_bytes(concat!(
        "6400",
        "f2000000",
        "7500",
        "08000000",
        "32352e302e353735", // "25.0.575"
        "7600",
        "02000000",
        "1800",
        "7700",
        "02000000",
        "0200",
        "7300",
        "02000000",
        "0100",
        "7400",
        "02000000",
        "1100",
        "6600",
        "10000000",
        "dcd4752a383d724783022fa29cda3224",
        "6700",
        "2e000000",
        "2823",
        "28000000",
        "2923",
        "04000000",
        "04000000",
        "2a23",
        "18000000",
        "6d6574612f6d6f64656c5f7468756d626e61696c2e706e67", // "meta/model_thumbnail.png"
        "6800",
        "30000000",
        "2823",
        "2a000000",
        "2923",
        "04000000",
        "04000000",
        "2a23",
        "1a000000",
        "6d6574612f707265766965775f7468756d626e61696c2e706e67", // "meta/preview_thumbnail.png"
        "6900",
        "01000000",
        "01",
        "6a00",
        "00000000",
        "6b00",
        "00000000",
        "6c00",
        "00000000",
        "6e00",
        "00000000",
        "7100",
        "01000000",
        "00",
        "7900",
        "01000000",
        "00",
        "7200",
        "01000000",
        "00",
        "6d00",
        "0a000000",
        "4d696c6c696d65746572", // "Millimeter"
    ));
    assert_eq!(read_meta_units(&payload).as_deref(), Some("Millimeter"));
}

#[test]
fn extracts_units_from_minimal_synthetic_record() {
    let outer = tlv("6400", &tlv("6D00", &bytes("Inches")));
    assert_eq!(read_meta_units(&outer).as_deref(), Some("Inches"));
}

#[test]
fn returns_none_when_units_tag_absent() {
    let outer = tlv("6400", &tlv("7500", &bytes("25.0.575")));
    assert!(read_meta_units(&outer).is_none());
}

#[test]
fn returns_none_for_empty_or_truncated_bytes() {
    assert!(read_meta_units(&[]).is_none());
    assert!(read_meta_units(&[1, 2, 3]).is_none());
}
