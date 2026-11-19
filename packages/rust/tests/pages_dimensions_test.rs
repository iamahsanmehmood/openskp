// VFF scenes ("pages") and linear dimensions. Dimensions are exercised against
// Untitled.skp (13 linear dimensions); scenes on a synthetic 0702 record
// shaped like the real ones.

mod common;

use std::collections::{BTreeMap, HashMap};

use common::{concat, f64s};
use openskp::internal::{
    hex_upper, parse_dimensions, parse_pages, scan_pages_for_layers, LegacySlotEntry,
    LegacySlotTable, TlvNode,
};
use openskp::{ParseOptions, SkpFile, Vec3};

// Deliberately NOT common::tlv(): these sub-records use Python's INTEGER tag
// convention (a true little-endian u16), so the numeric tag is packed
// little-endian.
fn tlv(tag: u16, payload: &[u8]) -> Vec<u8> {
    let mut out = tag.to_le_bytes().to_vec();
    out.extend_from_slice(&(payload.len() as u32).to_le_bytes());
    out.extend_from_slice(payload);
    out
}

fn vec3(x: f64, y: f64, z: f64) -> Vec<u8> {
    f64s(&[x, y, z])
}

// ── linear dimensions ────────────────────────────────────────────────────

#[test]
fn untitled_fixture_has_13_dimensions() {
    let model = SkpFile::open(common::fixture("Untitled.skp"))
        .unwrap()
        .parse(&ParseOptions::default())
        .unwrap();
    assert_eq!(model.dimensions.len(), 13);
    for d in &model.dimensions {
        let (a, b) = (d.a.unwrap(), d.b.unwrap());
        let (dx, dy, dz) = (a[0] - b[0], a[1] - b[1], a[2] - b[2]);
        assert!(dx * dx + dy * dy + dz * dz > 0.0); // a real measured segment
        assert!(d.normal.is_some());
        assert!(d.plane_x.is_some());
    }
}

#[test]
fn dimension_free_points_synthetic() {
    // A 5BCC record with two type-1 (free, world-space) connection points.
    let point_block = |wrap: u16, x: f64, y: f64, z: f64| {
        let inner = concat(&[&tlv(0x5209, &[1, 0, 0, 0]), &tlv(0x520a, &vec3(x, y, z))]);
        tlv(wrap, &tlv(0x5208, &inner))
    };
    let body = concat(&[
        &point_block(0x5bcd, 0.0, 0.0, 0.0),
        &point_block(0x5bce, 100.0, 0.0, 0.0),
        &tlv(0x5bcf, &vec3(1.0, 0.0, 0.0)), // plane normal (kept in plane_x)
        &tlv(0x5bd0, &vec3(0.0, 0.0, 1.0)), // plane X axis (kept in normal)
        &tlv(0x5bd2, &f64s(&[15.5])),       // offset
    ]);
    let blob = concat(&[&[0u8; 8], &tlv(0x5bcc, &body), &[0u8; 8]]);

    let dims = parse_dimensions(&blob, &BTreeMap::new(), &BTreeMap::new());
    assert_eq!(dims.len(), 1);
    let d = &dims[0];
    assert_eq!(d.a, [0.0, 0.0, 0.0]);
    assert_eq!(d.b, [100.0, 0.0, 0.0]);
    assert_eq!(d.offset, 15.5);
    assert_eq!(d.plane_x, Some([1.0, 0.0, 0.0]));
    assert_eq!(d.normal, Some([0.0, 0.0, 1.0]));
}

/// A 5BCC body with free endpoints `a`, `b`, the plane normal (0, -1, 0), X axis (1, 0, 0), an
/// optional 5BD1 constrained axis and offset `distance`.
fn constrained_body(a: Vec3, b: Vec3, axis: Option<&[u8]>, distance: f64) -> Vec<u8> {
    let point = |wrap: u16, p: Vec3| {
        let inner = concat(&[&tlv(0x5209, &[1, 0, 0, 0]), &tlv(0x520a, &f64s(&p))]);
        tlv(wrap, &tlv(0x5208, &inner))
    };
    concat(&[
        &point(0x5bcd, a),
        &point(0x5bce, b),
        &tlv(0x5bcf, &vec3(0.0, -1.0, 0.0)), // actual plane normal
        &tlv(0x5bd0, &vec3(1.0, 0.0, 0.0)),  // actual plane X axis
        &axis.map(|x| tlv(0x5bd1, x)).unwrap_or_default(),
        &tlv(0x5bd2, &f64s(&[distance])),
    ])
}

#[test]
fn constrained_offsets_preserve_plane_side_and_endpoint_order() {
    use openskp::internal::{build_model, RawParsed};
    let (a, b): (Vec3, Vec3) = ([1.0, 2.0, 3.0], [1.0, 2.0, 13.0]);
    for axis in [1u8, 2] {
        for distance in [-4.0, 4.0] {
            for reversed in [false, true] {
                let (p, q) = if reversed { (b, a) } else { (a, b) };
                let body = constrained_body(p, q, Some(&[axis, 0, 0, 0]), distance);
                let parsed = RawParsed {
                    dimensions: parse_dimensions(
                        &tlv(0x5bcc, &body),
                        &BTreeMap::new(),
                        &BTreeMap::new(),
                    ),
                    ..Default::default()
                };
                let model = build_model(parsed, &ParseOptions::default());
                assert_eq!(model.dimensions.len(), 1);
                let dim = &model.dimensions[0];
                let expected = if axis == 1 {
                    [0.0, 0.0, distance]
                } else {
                    [-distance, 0.0, 0.0]
                };
                let case = format!("axis {axis}, distance {distance}, reversed {reversed}");
                assert_eq!(dim.offset_vector, Some(expected), "{case}");
                assert_eq!(dim.a, Some(p), "{case}");
                assert_eq!(dim.b, Some(q), "{case}");
            }
        }
    }
}

#[test]
fn unsupported_or_malformed_constraint_keeps_scalar_fallback() {
    let axes: [Option<&[u8]>; 4] = [None, Some(&[2]), Some(&[0, 0, 0, 0]), Some(&[3, 0, 0, 0])];
    for axis in axes {
        let body = constrained_body([1.0, 2.0, 3.0], [1.0, 2.0, 13.0], axis, -4.0);
        let dims = parse_dimensions(&tlv(0x5bcc, &body), &BTreeMap::new(), &BTreeMap::new());
        assert_eq!(dims.len(), 1);
        assert!(dims[0].offset_vector.is_none(), "{axis:?}");
        assert_eq!(dims[0].offset, -4.0);
    }
}

#[test]
fn dimension_connected_point_resolves_through_instance() {
    // A type-2 connection (vertex id + instance id): the vertex position is
    // definition-local and is lifted to world by the instance's transform. An
    // unresolvable reference drops the dimension (fail-safe).
    let vid = [0xaau8, 0xbb, 0x01];
    let iid = [0xccu8, 0xdd, 0x02];
    let connected = |wrap: u16| {
        let mut id_len_prefixed = vec![iid.len() as u8];
        id_len_prefixed.extend_from_slice(&iid);
        let ref_tlv = tlv(
            0x53fc,
            &concat(&[&tlv(0x53fd, &vid), &tlv(0x53fe, &id_len_prefixed)]),
        );
        let inner = concat(&[&tlv(0x5209, &[2, 0, 0, 0]), &tlv(0x520b, &ref_tlv)]);
        tlv(wrap, &tlv(0x5208, &inner))
    };
    let free = |wrap: u16| {
        let inner = concat(&[
            &tlv(0x5209, &[1, 0, 0, 0]),
            &tlv(0x520a, &vec3(0.0, 0.0, 0.0)),
        ]);
        tlv(wrap, &tlv(0x5208, &inner))
    };
    let body = concat(&[
        &connected(0x5bcd),
        &free(0x5bce),
        &tlv(0x5bd2, &f64s(&[0.0])),
    ]);
    let blob = tlv(0x5bcc, &body);

    let world = vec![
        1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 10.0, 20.0, 30.0, 1.0,
    ];
    let id2pos: BTreeMap<String, Vec3> = [(hex_upper(&vid), [1.0, 2.0, 3.0])].into();
    let inst_world: BTreeMap<String, Vec<f64>> = [(hex_upper(&iid), world)].into();

    let dims = parse_dimensions(&blob, &id2pos, &inst_world);
    assert_eq!(dims.len(), 1);
    assert_eq!(dims[0].a, [11.0, 22.0, 33.0]); // local + translation

    // Same record, but the vertex id is unknown: the dimension is dropped.
    assert!(parse_dimensions(&blob, &BTreeMap::new(), &BTreeMap::new()).is_empty());
}

// ── scenes (pages) ──────────────────────────────────────────────────────

fn page_record(name: &str, parallel: bool, hidden_ids: &[u8], id: &[u8]) -> Vec<u8> {
    let cam = concat(&[
        &tlv(0x34bd, &vec3(100.0, -200.0, 50.0)), // eye
        &tlv(0x34be, &vec3(0.0, 0.0, 0.0)),       // target
        &tlv(0x34bf, &vec3(0.0, 0.0, 1.0)),       // up
        &tlv(0x34c4, &f64s(&[35.0])),             // fov
        &tlv(0x34c2, &[if parallel { 0 } else { 1 }]),
        &tlv(0x34c3, &f64s(&[240.0])), // ortho height
    ]);
    let hidden: Vec<u8> = hidden_ids.iter().flat_map(|&i| [1u8, i]).collect();
    let head_id = if id.is_empty() {
        Vec::new()
    } else {
        tlv(0x05dc, &tlv(0x05de, id))
    };
    let body = concat(&[
        &tlv(0x6f54, &concat(&[&head_id, &tlv(0x6f55, name.as_bytes())])),
        &tlv(0x714a, &tlv(0x34bc, &cam)),
        &tlv(0x7150, &hidden),
    ]);
    tlv(0x7148, &body)
}

fn page_node(payload: Vec<u8>) -> TlvNode {
    TlvNode::leaf("0702", payload)
}

#[test]
fn parse_pages_synthetic() {
    let payload = tlv(
        0x6d60,
        &tlv(
            0x6d61,
            &concat(&[
                &page_record("Planta", true, &[2], &[]),
                &page_record("Vista 3D", false, &[], &[]),
            ]),
        ),
    );
    let node = page_node(payload);
    let pages = parse_pages(Some(&node)).unwrap();

    assert_eq!(pages.len(), 2);
    assert_eq!(pages[0].name, "Planta");
    assert_eq!(pages[1].name, "Vista 3D");
    let planta = &pages[0];
    assert!(planta.parallel);
    assert_eq!(planta.ortho_height, 240.0);
    assert_eq!(planta.eye, Some([100.0, -200.0, 50.0]));
    assert_eq!(planta.up, Some([0.0, 0.0, 1.0]));
    assert_eq!(planta.hidden_layer_ids, vec![2]);
    assert!(!pages[1].parallel);
    assert_eq!(pages[1].fov, 35.0);
    assert!(!pages[0].selected); // no 6D62: nothing selected
    assert!(!pages[1].selected);
}

// 6D62 (beside 6D61) names the scene selected when the model was saved by the
// entity id each page carries in 6F54 > DC05 > DE05.
#[test]
fn parse_pages_selected() {
    let payload = tlv(
        0x6d60,
        &concat(&[
            &tlv(
                0x6d61,
                &concat(&[
                    &page_record("Plan", true, &[], &[0x38, 0x02]),
                    &page_record("3D", false, &[], &[0x39, 0x02]),
                ]),
            ),
            &tlv(0x6d62, &[0x39, 0x02]),
        ]),
    );
    let node = page_node(payload);
    let pages = parse_pages(Some(&node)).unwrap();
    assert_eq!(pages.len(), 2);
    assert!(!pages[0].selected);
    assert!(pages[1].selected);
}

#[test]
fn pages_absent_is_empty() {
    assert!(parse_pages(None).unwrap().is_empty());
}

#[test]
fn file_with_no_pages_parses_with_empty_pages_list() {
    let model = SkpFile::open(common::fixture("SU_File.skp"))
        .unwrap()
        .parse(&ParseOptions::default())
        .unwrap();
    assert!(model.pages.is_empty());
}

// ── legacy scenes (pages) ────────────────────────────────────────────────
//
// legacy.cpp's scan_pages_for_layers with a hand-built LegacySlotTable
// (slots 5 and 6 = CLayer): the "TooFull" record (flags 0xfff) is skipped,
// and the noise buffer yields no pages.

fn legacy_page_record(name: &str, flags: u32, hidden_ids: &[u16]) -> Vec<u8> {
    let mut out = Vec::new();
    let marker = [255u8, 254, 255];
    out.extend_from_slice(&marker);
    out.push(name.len() as u8);
    for c in name.bytes() {
        out.extend_from_slice(&[c, 0]);
    }
    out.extend_from_slice(&marker);
    out.push(0); // second name: always empty
    out.extend_from_slice(&flags.to_le_bytes());
    out.extend_from_slice(&[0, 0]); // camera: null ref
    out.extend_from_slice(&[0, 0]); // thumbnail dib: null ref
    const HIDDEN_LAYERS_BIT: u32 = 0x20;
    if flags & HIDDEN_LAYERS_BIT != 0 {
        for &hid in hidden_ids {
            out.extend_from_slice(&hid.to_le_bytes());
        }
        out.extend_from_slice(&[1, 0, 0, 0]); // u32 marker (also stops the u16 list)
    }
    out
}

#[test]
fn scan_pages_legacy_synthetic() {
    let data = concat(&[
        &legacy_page_record("Baseline", 0xc00, &[]),
        &legacy_page_record("WithHidden", 0xc20, &[5, 6]),
        &legacy_page_record("TooFull", 0xfff, &[5]),
    ]);
    let mut slots = HashMap::new();
    let mut class_slot = HashMap::new();
    let mut next = 0u64;
    slots.insert(
        5,
        LegacySlotEntry {
            cls: false,
            name: "CLayer".into(),
            schema: 0,
            v: None,
        },
    );
    slots.insert(
        6,
        LegacySlotEntry {
            cls: false,
            name: "CLayer".into(),
            schema: 0,
            v: None,
        },
    );

    let pages = scan_pages_for_layers(
        &data,
        0,
        LegacySlotTable {
            slots: &mut slots,
            class_slot: &mut class_slot,
            next: &mut next,
        },
    )
    .unwrap();

    assert_eq!(pages.len(), 2);
    assert_eq!(pages[0].name, "Baseline");
    assert_eq!(pages[1].name, "WithHidden");
    assert!(pages[0].hidden_layer_ids.is_empty());
    assert_eq!(pages[1].hidden_layer_ids, [5, 6]);
}

#[test]
fn scan_pages_legacy_ignores_unrelated_noise() {
    // Random bytes containing the marker sequence but not shaped like a real
    // page record must never crash or fabricate a page.
    let noise = [255u8, 254, 255, 5, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10];
    let mut slots = HashMap::new();
    let mut class_slot = HashMap::new();
    let mut next = 0u64;

    let pages = scan_pages_for_layers(
        &noise,
        0,
        LegacySlotTable {
            slots: &mut slots,
            class_slot: &mut class_slot,
            next: &mut next,
        },
    )
    .unwrap();

    assert!(pages.is_empty());
}
