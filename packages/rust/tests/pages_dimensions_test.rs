// VFF scenes ("pages") and linear dimensions. Dimensions are exercised against
// Untitled.skp (13 linear dimensions); scenes on a synthetic 0702 record
// shaped like the real ones.

mod common;

use std::collections::{BTreeMap, HashMap};

use common::{concat, f64s};
use openskp::internal::{
    assign_vff_annotations_with_limit, compose_instance_world, hex_upper, parse_dimensions,
    parse_pages, scan_pages_for_layers, transform_point, LegacySlotEntry, LegacySlotTable,
    RawDefinition, RawInstance, RawParsed, TlvNode,
};
use openskp::{Definition, ParseOptions, SkpFile, Vec3};

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

#[test]
fn compose_instance_world_nests_parent_times_local() {
    let panel = RawDefinition::default();
    let mut cabinet = RawDefinition::default();
    cabinet.builder.instances.push(RawInstance {
        persistent_id: "AA".into(),
        ref_idx: Some(2),
        matrix: vec![
            1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 1.0, 2.0, 3.0, 1.0,
        ],
        ..Default::default()
    });
    let mut root = RawDefinition::default();
    root.builder.instances.push(RawInstance {
        persistent_id: "BB".into(),
        ref_idx: Some(1),
        matrix: vec![
            1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 10.0, 20.0, 30.0, 1.0,
        ],
        ..Default::default()
    });
    let mut defs = BTreeMap::new();
    defs.insert(1, cabinet);
    defs.insert(2, panel);

    let mut world = BTreeMap::new();
    compose_instance_world(&root, &defs, &mut world);
    assert_eq!(
        transform_point(&world["BB"], [0.0, 0.0, 0.0]),
        [10.0, 20.0, 30.0]
    );
    assert_eq!(
        transform_point(&world["AA"], [0.0, 0.0, 0.0]),
        [11.0, 22.0, 33.0]
    );
}

#[test]
fn dimension_connected_point_uses_composed_nested_world() {
    let vid = [0xaau8, 0xbb, 0x01];
    let iid = [0xccu8, 0xdd, 0x02];
    let connected = |wrap_tag: u16| {
        let mut id_len_prefixed = vec![iid.len() as u8];
        id_len_prefixed.extend_from_slice(&iid);
        let ref_tlv = tlv(
            0x53fc,
            &concat(&[&tlv(0x53fd, &vid), &tlv(0x53fe, &id_len_prefixed)]),
        );
        let inner = concat(&[&tlv(0x5209, &[2, 0, 0, 0]), &tlv(0x520b, &ref_tlv)]);
        tlv(wrap_tag, &tlv(0x5208, &inner))
    };
    let free = |wrap_tag: u16| {
        let inner = concat(&[
            &tlv(0x5209, &[1, 0, 0, 0]),
            &tlv(0x520a, &vec3(0.0, 0.0, 0.0)),
        ]);
        tlv(wrap_tag, &tlv(0x5208, &inner))
    };
    let body = concat(&[
        &connected(0x5bcd),
        &free(0x5bce),
        &tlv(0x5bd2, &f64s(&[0.0])),
    ]);
    let blob = tlv(0x5bcc, &body);

    let leaf = RawDefinition::default();
    let mut parent_def = RawDefinition::default();
    parent_def.builder.instances.push(RawInstance {
        persistent_id: hex_upper(&iid),
        ref_idx: Some(2),
        matrix: vec![
            1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 1.0, 2.0, 3.0, 1.0,
        ],
        ..Default::default()
    });
    let mut root = RawDefinition::default();
    root.builder.instances.push(RawInstance {
        persistent_id: "PARENT".into(),
        ref_idx: Some(1),
        matrix: vec![
            1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 10.0, 20.0, 30.0, 1.0,
        ],
        ..Default::default()
    });
    let mut defs = BTreeMap::new();
    defs.insert(1, parent_def);
    defs.insert(2, leaf);

    let mut world = BTreeMap::new();
    compose_instance_world(&root, &defs, &mut world);
    let id2pos: BTreeMap<String, Vec3> = [(hex_upper(&vid), [0.0, 0.0, 0.0])].into();
    let dims = parse_dimensions(&blob, &id2pos, &world);
    assert_eq!(dims.len(), 1);
    assert_eq!(dims[0].a, [11.0, 22.0, 33.0]);
}

#[test]
fn dimension_connected_point_uses_innermost_instance_in_path() {
    let vid = [0xaau8, 0xbb, 0x01];
    let outer_id = [0x11u8, 0x01];
    let inner_id = [0x22u8, 0x02];
    let mut path = vec![outer_id.len() as u8];
    path.extend_from_slice(&outer_id);
    path.push(inner_id.len() as u8);
    path.extend_from_slice(&inner_id);

    let connected = |wrap_tag: u16| {
        let ref_tlv = tlv(0x53fc, &concat(&[&tlv(0x53fd, &vid), &tlv(0x53fe, &path)]));
        let inner = concat(&[&tlv(0x5209, &[2, 0, 0, 0]), &tlv(0x520b, &ref_tlv)]);
        tlv(wrap_tag, &tlv(0x5208, &inner))
    };
    let free = |wrap_tag: u16| {
        let inner = concat(&[
            &tlv(0x5209, &[1, 0, 0, 0]),
            &tlv(0x520a, &vec3(0.0, 0.0, 0.0)),
        ]);
        tlv(wrap_tag, &tlv(0x5208, &inner))
    };
    let blob = tlv(
        0x5bcc,
        &concat(&[
            &connected(0x5bcd),
            &free(0x5bce),
            &tlv(0x5bd2, &f64s(&[0.0])),
        ]),
    );

    let leaf = RawDefinition::default();
    let mut parent_def = RawDefinition::default();
    parent_def.builder.instances.push(RawInstance {
        persistent_id: hex_upper(&inner_id),
        ref_idx: Some(2),
        matrix: vec![
            1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 100.0, 0.0, 0.0, 1.0,
        ],
        ..Default::default()
    });
    let mut root = RawDefinition::default();
    root.builder.instances.push(RawInstance {
        persistent_id: hex_upper(&outer_id),
        ref_idx: Some(1),
        matrix: vec![
            1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 10.0, 0.0, 0.0, 1.0,
        ],
        ..Default::default()
    });
    let mut defs = BTreeMap::new();
    defs.insert(1, parent_def);
    defs.insert(2, leaf);

    let mut world = BTreeMap::new();
    compose_instance_world(&root, &defs, &mut world);
    let id2pos: BTreeMap<String, Vec3> = [(hex_upper(&vid), [0.0, 0.0, 0.0])].into();
    let dims = parse_dimensions(&blob, &id2pos, &world);
    assert_eq!(dims.len(), 1);
    assert_eq!(dims[0].a, [110.0, 0.0, 0.0]);
}

#[test]
fn sdk_annotation_tags_retain_owner_and_visibility() {
    fn check_tagged<'a>(
        items: impl Iterator<Item = (&'a str, bool)>,
        tag: &str,
        count: usize,
        hidden: usize,
        name: &str,
    ) {
        let collected: Vec<_> = items.collect();
        assert_eq!(collected.len(), count, "{name}");
        for &(layer, _) in &collected {
            assert_eq!(layer, tag, "{name}");
        }
        let actual_hidden = collected.iter().filter(|&&(_, h)| h).count();
        assert_eq!(actual_hidden, hidden, "{name}");
    }

    fn verify(owner: &Definition, nested: bool, name: &str) {
        check_tagged(
            owner
                .dimensions
                .iter()
                .map(|e| (e.layer.as_str(), e.hidden)),
            "尺寸标注",
            2,
            1,
            name,
        );
        check_tagged(
            owner
                .construction_points
                .iter()
                .map(|e| (e.layer.as_str(), e.hidden)),
            "参考元素",
            2,
            1,
            name,
        );
        check_tagged(
            owner
                .construction_lines
                .iter()
                .map(|e| (e.layer.as_str(), e.hidden)),
            "参考元素",
            2,
            1,
            name,
        );
        check_tagged(
            owner.texts.iter().map(|e| (e.layer.as_str(), e.hidden)),
            "引注文字",
            if nested { 1 } else { 3 },
            if nested { 0 } else { 1 },
            name,
        );
        for text in &owner.texts {
            assert_eq!(text.display_leader, text.text != "Dot", "{name}");
            if nested {
                assert_eq!(text.leader_type, 0, "{name}");
                assert_eq!(text.screen_position, Some([0.23, 0.67]), "{name}");
            } else {
                let pt = text.point.expect("point");
                let lp = text.label_point.expect("label_point");
                assert!((lp[0] - pt[0]).abs() < 1e-9, "{name}");
                assert!((lp[1] - pt[1]).abs() < 1e-9, "{name}");
                assert!((lp[2] - pt[2] - 10.0).abs() < 1e-9, "{name}");
            }
        }
    }

    for name in ["annotation_layers_legacy.skp", "annotation_layers_vff.skp"] {
        let model = SkpFile::open(common::fixture(name))
            .unwrap()
            .parse(&ParseOptions::default())
            .unwrap();
        verify(model.root(), false, name);
        let mut annotated = 0usize;
        for def in model.definitions.values() {
            if def.texts.is_empty() {
                continue;
            }
            verify(def, true, name);
            annotated += 1;
        }
        assert_eq!(annotated, 1, "{name}");
        if name == "annotation_layers_vff.skp" {
            assert_eq!(model.dimensions.len(), 4);
            for dim in &model.dimensions {
                assert_eq!(dim.layer, "尺寸标注");
            }
        }
    }
}

#[test]
fn drawing_properties_come_from_dimension_not_its_connections() {
    let free_point = |x: f64, y: f64, z: f64| {
        tlv(
            0x5208,
            &concat(&[
                &tlv(0x5209, &[1, 0, 0, 0]),
                &tlv(0x520a, &vec3(x, y, z)),
                &tlv(0x07d0, &tlv(0x07d2, &[99])),
            ]),
        )
    };
    let body = concat(&[
        &tlv(
            0x59d8,
            &tlv(0x07d0, &concat(&[&tlv(0x07d2, &[12]), &tlv(0x07d3, &[7])])),
        ),
        &tlv(0x5bcd, &free_point(0.0, 0.0, 0.0)),
        &tlv(0x5bce, &free_point(10.0, 0.0, 0.0)),
        &tlv(0x5bd2, &f64s(&[3.0])),
    ]);
    let dims = parse_dimensions(&tlv(0x5bcc, &body), &BTreeMap::new(), &BTreeMap::new());
    assert_eq!(dims.len(), 1);
    assert_eq!(dims[0].layer, "12");
    assert!(dims[0].hidden);
}

#[test]
fn vff_doubling_chain_composes_only_referenced_path_and_caps_visits() {
    let chain_model = SkpFile::open(common::fixture("vff_doubling_chain.skp"))
        .unwrap()
        .parse(&ParseOptions::default())
        .unwrap();
    assert_eq!(chain_model.definitions.len(), 22);
    assert_eq!(chain_model.root().instances.len(), 2);

    const LEVELS: usize = 22;
    let make_parsed = || {
        let mut p = RawParsed::default();
        p.definitions.insert(0, RawDefinition::default());
        for i in 1..LEVELS {
            let mut d = RawDefinition::default();
            for j in 0..2usize {
                let tx = if j == 0 { 0.0 } else { 10.0 };
                d.builder.instances.push(RawInstance {
                    persistent_id: hex_upper(&[i as u8, (j + 1) as u8]),
                    ref_idx: Some((i - 1) as i64),
                    matrix: vec![
                        1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, tx, 0.0, 0.0, 1.0,
                    ],
                    ..Default::default()
                });
            }
            p.definitions.insert(i as i64, d);
        }
        for j in 0..2usize {
            let tx = if j == 0 { 0.0 } else { 10.0 };
            p.root.builder.instances.push(RawInstance {
                persistent_id: hex_upper(&[LEVELS as u8, (j + 1) as u8]),
                ref_idx: Some((LEVELS - 1) as i64),
                matrix: vec![
                    1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, tx, 0.0, 0.0, 1.0,
                ],
                ..Default::default()
            });
        }
        p
    };

    let mut path_bytes = Vec::new();
    for i in (1..=LEVELS).rev() {
        path_bytes.extend_from_slice(&[2u8, i as u8, 2]);
    }
    let vid_zero = [0xaau8, 0xbb, 0x00];
    let vid = [0xaau8, 0xbb, 0x01];
    let vid_b = [0xaau8, 0xbb, 0x02];
    let id2pos: BTreeMap<String, Vec3> = [
        (hex_upper(&vid_zero), [0.0, 0.0, 0.0]),
        (hex_upper(&vid), [4.0, 0.0, 0.0]),
        (hex_upper(&vid_b), [5.0, 0.0, 0.0]),
    ]
    .into();
    let dim_blob = |raw_53fe: &[u8]| {
        let ref_tlv = tlv(
            0x53fc,
            &concat(&[&tlv(0x53fd, &vid), &tlv(0x53fe, raw_53fe)]),
        );
        let conn_a = tlv(
            0x5bcd,
            &tlv(
                0x5208,
                &concat(&[&tlv(0x5209, &[2, 0, 0, 0]), &tlv(0x520b, &ref_tlv)]),
            ),
        );
        let conn_b = tlv(
            0x5bce,
            &tlv(
                0x5208,
                &concat(&[
                    &tlv(0x5209, &[1, 0, 0, 0]),
                    &tlv(0x520a, &vec3(0.0, 0.0, 0.0)),
                ]),
            ),
        );
        tlv(
            0x5bcc,
            &concat(&[&conn_a, &conn_b, &tlv(0x5bd2, &f64s(&[0.0]))]),
        )
    };

    let mut p0 = make_parsed();
    assert_eq!(
        assign_vff_annotations_with_limit(&dim_blob(&[]), &id2pos, &mut p0, 5),
        0
    );
    assert_eq!(p0.root.builder.dimensions[0].a, Some([4.0, 0.0, 0.0]));

    let mut p_ok = make_parsed();
    assert_eq!(
        assign_vff_annotations_with_limit(&dim_blob(&path_bytes), &id2pos, &mut p_ok, 64),
        LEVELS as u64
    );
    assert_eq!(p_ok.root.builder.dimensions[0].a, Some([224.0, 0.0, 0.0]));
    assert_eq!(p_ok.dimensions[0].a, [224.0, 0.0, 0.0]);

    let mut p_cap = make_parsed();
    assert_eq!(
        assign_vff_annotations_with_limit(&dim_blob(&path_bytes), &id2pos, &mut p_cap, 10),
        10
    );
    assert_eq!(p_cap.root.builder.dimensions[0].a, Some([4.0, 0.0, 0.0]));

    let mut p_miss = make_parsed();
    let bogus = [4u8, 0xde, 0xad, 0xbe, 0xef];
    assert_eq!(
        assign_vff_annotations_with_limit(&dim_blob(&bogus), &id2pos, &mut p_miss, 25),
        0
    );

    let make_after_chain = || {
        let mut p = make_parsed();
        p.definitions.insert(101, RawDefinition::default());
        let mut after_def = RawDefinition::default();
        after_def.builder.instances.push(RawInstance {
            persistent_id: "6501".to_owned(),
            ref_idx: Some(101),
            matrix: vec![
                1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 3.0, 0.0, 0.0, 1.0,
            ],
            ..Default::default()
        });
        p.definitions.insert(100, after_def);
        p.root.builder.instances.push(RawInstance {
            persistent_id: "6401".to_owned(),
            ref_idx: Some(100),
            matrix: vec![
                1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 5.0, 0.0, 0.0, 1.0,
            ],
            ..Default::default()
        });
        p
    };

    let sub_path = [2u8, 0x65, 0x01];
    let ref_a = tlv(
        0x53fc,
        &concat(&[&tlv(0x53fd, &vid_zero), &tlv(0x53fe, &sub_path)]),
    );
    let ref_b = tlv(
        0x53fc,
        &concat(&[&tlv(0x53fd, &vid_b), &tlv(0x53fe, &sub_path)]),
    );
    let after_dim = tlv(
        0x5bcc,
        &concat(&[
            &tlv(
                0x5bcd,
                &tlv(
                    0x5208,
                    &concat(&[&tlv(0x5209, &[2, 0, 0, 0]), &tlv(0x520b, &ref_a)]),
                ),
            ),
            &tlv(
                0x5bce,
                &tlv(
                    0x5208,
                    &concat(&[&tlv(0x5209, &[2, 0, 0, 0]), &tlv(0x520b, &ref_b)]),
                ),
            ),
            &tlv(0x5bd2, &f64s(&[0.0])),
        ]),
    );
    let def_100_span = tlv(
        0x01f9,
        &tlv(
            0x1770,
            &tlv(
                0x1771,
                &tlv(
                    0x157c,
                    &concat(&[&tlv(0x05dc, &tlv(0x05de, &[100])), &after_dim]),
                ),
            ),
        ),
    );

    let mut p_after = make_after_chain();
    let visits_after = assign_vff_annotations_with_limit(&def_100_span, &id2pos, &mut p_after, 16);
    assert!(visits_after <= 10);
    assert_eq!(
        p_after.definitions[&100].builder.dimensions[0].a,
        Some([3.0, 0.0, 0.0])
    );
    assert_eq!(
        p_after.definitions[&100].builder.dimensions[0].b,
        Some([8.0, 0.0, 0.0])
    );
    assert_eq!(p_after.dimensions[0].a, [8.0, 0.0, 0.0]);
    assert_eq!(p_after.dimensions[0].b, [13.0, 0.0, 0.0]);

    let dangling_mid = [2u8, 10, 2, 4, 0xde, 0xad, 0xbe, 0xef];
    let one_dangling = dim_blob(&dangling_mid);
    let mut repeated = Vec::new();
    for _ in 0..20 {
        repeated.extend_from_slice(&one_dangling);
    }
    let mut p_repeat = make_parsed();
    assert!(assign_vff_annotations_with_limit(&repeated, &id2pos, &mut p_repeat, 16) <= 5);

    let mut sixty_dangling = Vec::new();
    for k in 0..60u8 {
        sixty_dangling.extend_from_slice(&dim_blob(&[2, 10, 2, 2, 0xee, k]));
    }
    sixty_dangling.extend_from_slice(&def_100_span);
    let mut p_starve = make_after_chain();
    assign_vff_annotations_with_limit(&sixty_dangling, &id2pos, &mut p_starve, 16);
    let last_dim = p_starve.dimensions.last().unwrap();
    assert_eq!(last_dim.a, [8.0, 0.0, 0.0]);
    assert_eq!(last_dim.b, [13.0, 0.0, 0.0]);
}
