//! Header, version and meta.dat helpers (`vff.cpp`).

use super::bytes::parse_flat;
use super::strconv::c_stoi;

/// The SKP magic: `FF FE FF 0E`.
pub fn valid_header(d: &[u8]) -> bool {
    d.len() >= 4 && d[0] == 0xff && d[1] == 0xfe && d[2] == 0xff && d[3] == 0x0e
}

/// The `{major.minor.build}` version string from the UTF-16 header, or
/// `"unknown"`.
pub fn extract_version(d: &[u8]) -> String {
    let mut marker = None;
    let mut i = 4;
    while i + 2 < d.len() && i < 512 {
        if d[i] == 0xff && d[i + 1] == 0xfe && d[i + 2] == 0xff {
            marker = Some(i);
            break;
        }
        i += 1;
    }
    let Some(marker) = marker else {
        return "unknown".into();
    };
    let mut s = Vec::new();
    let mut i = marker + 4;
    while i + 1 < d.len() && i < 512 {
        if d[i] != 0 {
            s.push(d[i]);
        }
        i += 2;
    }
    let a = s.iter().position(|&c| c == b'{');
    let b = a.and_then(|a| s[a..].iter().position(|&c| c == b'}').map(|k| a + k));
    match (a, b) {
        (Some(a), Some(b)) => String::from_utf8_lossy(&s[a..=b]).into_owned(),
        _ => "unknown".into(),
    }
}

// meta/meta.dat uses the exact same low-level TLV framing as model.dat (2-byte
// tag + 4-byte little-endian length + payload), but as one flat, non-recursive
// record list wrapped in a single outer record (tag "6400"). Tag "6D00"
// carries the model's unit-system string ("Millimeter" etc.) as plain text.
const META_WRAPPER_TAG: &str = "6400";
const META_UNITS_TAG: &str = "6D00";

/// The model's unit-system string from a VFF file's meta/meta.dat contents, or
/// `None` if the expected tags aren't found.
pub fn read_meta_units(meta_bytes: &[u8]) -> Option<String> {
    read_meta_units_depth(meta_bytes, 0)
}

fn read_meta_units_depth(meta_bytes: &[u8], depth: usize) -> Option<String> {
    if depth > super::bytes::MAX_TLV_DEPTH {
        return None;
    }
    let records = parse_flat(meta_bytes);
    if let Some((_, body)) = records.iter().find(|(tag, _)| *tag == META_WRAPPER_TAG) {
        return read_meta_units_depth(body, depth + 1);
    }
    records
        .iter()
        .find(|(tag, _)| *tag == META_UNITS_TAG)
        .map(|(_, body)| String::from_utf8_lossy(body).into_owned())
}

/// True for a classic (pre-2021, MFC) file: major version <= 20.
pub fn is_legacy(d: &[u8]) -> bool {
    if !valid_header(d) {
        return false;
    }
    let v = extract_version(d);
    let Some(dot) = v.find('.') else {
        return false;
    };
    if v.len() < 2 {
        return false;
    }
    // substr(1, dot - 1); the version starts with '{', so dot >= 1.
    let digits = if dot >= 1 {
        v.get(1..dot).unwrap_or("")
    } else {
        v.get(1..).unwrap_or("")
    };
    matches!(c_stoi(digits), Some(major) if major <= 20)
}
