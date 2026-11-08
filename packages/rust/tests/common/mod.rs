//! Shared test helpers (`test_helpers.hpp`), plus a minimal ZIP writer that
//! stands in for the miniz writer the C++ tests use.
#![allow(dead_code)]

use std::path::PathBuf;

/// Path of a fixture under `tests/fixtures`.
pub fn fixture(name: &str) -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("tests")
        .join("fixtures")
        .join(name)
}

pub fn read_fixture(name: &str) -> Vec<u8> {
    std::fs::read(fixture(name)).unwrap_or_else(|e| panic!("cannot read fixture {name}: {e}"))
}

pub fn concat(arrays: &[&[u8]]) -> Vec<u8> {
    arrays.concat()
}

fn hex_nibble(c: u8) -> u8 {
    if c >= b'A' {
        c - b'A' + 10
    } else {
        c - b'0'
    }
}

/// A TLV record whose tag is given as 4 upper-case hex digits in byte order
/// (the main tree's convention: "F401" is bytes `F4 01`).
pub fn tlv(tag: &str, payload: &[u8]) -> Vec<u8> {
    let t = tag.as_bytes();
    let mut out = vec![
        (hex_nibble(t[0]) << 4) | hex_nibble(t[1]),
        (hex_nibble(t[2]) << 4) | hex_nibble(t[3]),
    ];
    out.extend_from_slice(&(payload.len() as u32).to_le_bytes());
    out.extend_from_slice(payload);
    out
}

/// Little-endian f64s.
pub fn f64s(values: &[f64]) -> Vec<u8> {
    values.iter().flat_map(|v| v.to_le_bytes()).collect()
}

pub fn bytes(s: &str) -> Vec<u8> {
    s.as_bytes().to_vec()
}

/// One entry for [`build_zip`]: name, content, and compression level
/// (0 = stored, 1-10 = deflated).
pub struct ZipEntry<'a> {
    pub name: &'a str,
    pub data: &'a [u8],
    pub level: u32,
}

/// Builds a ZIP archive in memory (local headers, central directory, EOCD).
pub fn build_zip(entries: &[ZipEntry]) -> Vec<u8> {
    let mut out = Vec::new();
    let mut central = Vec::new();
    for e in entries {
        let crc = openskp::zip::crc32(e.data);
        let (method, body): (u16, Vec<u8>) = if e.level == 0 {
            (0, e.data.to_vec())
        } else {
            (8, openskp::deflate::deflate_raw(e.data, e.level))
        };
        let offset = out.len() as u32;
        let name = e.name.as_bytes();
        // Local file header.
        out.extend_from_slice(&0x0403_4b50u32.to_le_bytes());
        out.extend_from_slice(&20u16.to_le_bytes()); // version needed
        out.extend_from_slice(&0u16.to_le_bytes()); // flags
        out.extend_from_slice(&method.to_le_bytes());
        out.extend_from_slice(&0u16.to_le_bytes()); // time
        out.extend_from_slice(&0x21u16.to_le_bytes()); // date (1980-01-01)
        out.extend_from_slice(&crc.to_le_bytes());
        out.extend_from_slice(&(body.len() as u32).to_le_bytes());
        out.extend_from_slice(&(e.data.len() as u32).to_le_bytes());
        out.extend_from_slice(&(name.len() as u16).to_le_bytes());
        out.extend_from_slice(&0u16.to_le_bytes()); // extra
        out.extend_from_slice(name);
        out.extend_from_slice(&body);
        // Central directory header.
        central.extend_from_slice(&0x0201_4b50u32.to_le_bytes());
        central.extend_from_slice(&20u16.to_le_bytes()); // version made by
        central.extend_from_slice(&20u16.to_le_bytes()); // version needed
        central.extend_from_slice(&0u16.to_le_bytes());
        central.extend_from_slice(&method.to_le_bytes());
        central.extend_from_slice(&0u16.to_le_bytes());
        central.extend_from_slice(&0x21u16.to_le_bytes());
        central.extend_from_slice(&crc.to_le_bytes());
        central.extend_from_slice(&(body.len() as u32).to_le_bytes());
        central.extend_from_slice(&(e.data.len() as u32).to_le_bytes());
        central.extend_from_slice(&(name.len() as u16).to_le_bytes());
        central.extend_from_slice(&0u16.to_le_bytes()); // extra
        central.extend_from_slice(&0u16.to_le_bytes()); // comment
        central.extend_from_slice(&0u16.to_le_bytes()); // disk start
        central.extend_from_slice(&0u16.to_le_bytes()); // internal attr
        central.extend_from_slice(&0u32.to_le_bytes()); // external attr
        central.extend_from_slice(&offset.to_le_bytes());
        central.extend_from_slice(name);
    }
    let cdir_ofs = out.len() as u32;
    let n = entries.len() as u16;
    out.extend_from_slice(&central);
    out.extend_from_slice(&0x0605_4b50u32.to_le_bytes());
    out.extend_from_slice(&0u16.to_le_bytes());
    out.extend_from_slice(&0u16.to_le_bytes());
    out.extend_from_slice(&n.to_le_bytes());
    out.extend_from_slice(&n.to_le_bytes());
    out.extend_from_slice(&(central.len() as u32).to_le_bytes());
    out.extend_from_slice(&cdir_ofs.to_le_bytes());
    out.extend_from_slice(&0u16.to_le_bytes());
    out
}

/// Prefixes a ZIP with a `header_len`-byte VFF header (magic `FF FE FF 0E`,
/// then zeros).
pub fn wrap_vff(zip_bytes: &[u8], header_len: usize) -> Vec<u8> {
    let mut out = vec![0u8; header_len];
    out[..4].copy_from_slice(&[0xFF, 0xFE, 0xFF, 0x0E]);
    out.extend_from_slice(zip_bytes);
    out
}

/// Asserts `|a - b| <= tol`.
#[track_caller]
pub fn assert_near(a: f64, b: f64, tol: f64) {
    assert!(
        (a - b).abs() <= tol,
        "expected {a} to be within {tol} of {b}"
    );
}
