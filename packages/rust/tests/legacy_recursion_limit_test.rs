// Classic (MFC archive) reader recursion limits (#557).

use openskp::create::{create, GuideOptions};
use openskp::{ParseOptions, ParseStage, SkpFile};

const MAX_LEGACY_DEPTH: usize = 64;

fn add_u16(out: &mut Vec<u8>, x: u16) {
    out.extend_from_slice(&x.to_le_bytes());
}

fn add_u32(out: &mut Vec<u8>, x: u32) {
    out.extend_from_slice(&x.to_le_bytes());
}

fn add_wstr(out: &mut Vec<u8>, s: &str) {
    out.extend_from_slice(&[0xFF, 0xFE, 0xFF, s.len() as u8]);
    for c in s.encode_utf16() {
        out.extend_from_slice(&c.to_le_bytes());
    }
}

fn add_cdecl(out: &mut Vec<u8>, name: &str, schema: u16) {
    add_u16(out, 0xFFFF);
    add_u16(out, schema);
    add_u16(out, name.len() as u16);
    out.extend_from_slice(name.as_bytes());
}

fn find_ascii(buf: &[u8], needle: &[u8]) -> usize {
    buf.windows(needle.len())
        .position(|w| w == needle)
        .expect("needle found")
}

fn splice_at(raw: &[u8], pos: usize, replace_len: usize, mid: &[u8]) -> Vec<u8> {
    let mut out = Vec::with_capacity(raw.len() - replace_len + mid.len());
    out.extend_from_slice(&raw[..pos]);
    out.extend_from_slice(mid);
    out.extend_from_slice(&raw[pos + replace_len..]);
    out
}

fn make_nested_object_skp(total_depth: usize, on_layer: bool) -> Vec<u8> {
    let mut builder = create();
    builder
        .add_construction_point([1.0, 2.0, 3.0], &GuideOptions::default())
        .unwrap();
    let raw = builder.to_bytes().unwrap();
    let (tag, attr_cls_slot): (&[u8], u16) = if on_layer {
        (b"CLayer", 11)
    } else {
        (b"CConstructionPoint", 13)
    };
    let pos = find_ascii(&raw, tag) + tag.len();
    if total_depth <= 1 {
        return raw;
    }
    let k = total_depth - 1;
    let mut chain = Vec::new();
    add_cdecl(&mut chain, "CAttributeContainer", 0);
    for _ in 0..(k - 1) {
        add_u16(&mut chain, 0x8000 | attr_cls_slot);
    }
    add_u16(&mut chain, 0);
    for _ in 0..k {
        chain.push(0);
        add_u16(&mut chain, 0);
    }
    splice_at(&raw, pos, 2, &chain)
}

fn make_nested_attr_array_skp(array_depth: usize) -> Vec<u8> {
    let mut builder = create();
    builder
        .add_construction_point([1.0, 2.0, 3.0], &GuideOptions::default())
        .unwrap();
    let raw = builder.to_bytes().unwrap();
    let pos = find_ascii(&raw, b"CConstructionPoint") + b"CConstructionPoint".len();
    let mut payload = Vec::new();
    add_cdecl(&mut payload, "CAttributeContainer", 0);
    add_u16(&mut payload, 0);
    payload.push(0);
    add_cdecl(&mut payload, "CAttributeNamed", 0);
    add_u16(&mut payload, 0);
    payload.push(0);
    add_u32(&mut payload, 0);
    add_wstr(&mut payload, "Dict");
    add_wstr(&mut payload, "k");
    for _ in 0..array_depth {
        payload.push(0x0B);
        add_u32(&mut payload, 1);
    }
    payload.push(0x04);
    add_u32(&mut payload, 7);
    add_wstr(&mut payload, "");
    add_u32(&mut payload, 0);
    add_u16(&mut payload, 0);
    splice_at(&raw, pos, 2, &payload)
}

#[test]
fn classic_object_nesting_within_limit_parses() {
    for on_layer in [false, true] {
        for depth in [1, 10, MAX_LEGACY_DEPTH] {
            let skp = make_nested_object_skp(depth, on_layer);
            let model = SkpFile::from_buffer(skp)
                .parse(&ParseOptions::default())
                .unwrap();
            assert_eq!(model.unread_root_entities, 0);
            assert_eq!(model.root().construction_points.len(), 1);
        }
    }
}

#[test]
fn classic_object_nesting_above_limit_returns_parse_error() {
    for on_layer in [false, true] {
        for depth in [MAX_LEGACY_DEPTH + 1, 5000] {
            let skp = make_nested_object_skp(depth, on_layer);
            let err = SkpFile::from_buffer(skp)
                .parse(&ParseOptions::default())
                .unwrap_err();
            assert_eq!(err.stage(), Some(ParseStage::LegacyWalk));
        }
    }
}

#[test]
fn classic_attr_array_nesting_within_limit_parses() {
    for depth in [1, 10, MAX_LEGACY_DEPTH] {
        let skp = make_nested_attr_array_skp(depth);
        let model = SkpFile::from_buffer(skp)
            .parse(&ParseOptions::default())
            .unwrap();
        assert_eq!(model.unread_root_entities, 0);
        assert_eq!(model.root().construction_points.len(), 1);
    }
}

#[test]
fn classic_attr_array_nesting_above_limit_returns_parse_error() {
    for depth in [MAX_LEGACY_DEPTH + 1, 5000] {
        let skp = make_nested_attr_array_skp(depth);
        let err = SkpFile::from_buffer(skp)
            .parse(&ParseOptions::default())
            .unwrap_err();
        assert_eq!(err.stage(), Some(ParseStage::LegacyWalk));
    }
}
