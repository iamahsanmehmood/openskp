//! VFF (2021+) scenes ("pages") and linear dimensions (`pages_dimensions.cpp`,
//! itself a port of Python's `_core.py`).

use std::collections::BTreeMap;

use super::bytes::{parse_varint, read_f64, u32_at};
use super::transforms::{multiply_matrices, transform_point};
use super::{hex_upper, text, RawCamera, RawDimension, RawPage, Result, TlvNode};
use crate::model::{constrained_offset_vector, EntityId, Vec3};

// A second, deliberately separate flat-TLV reader from parse_flat: the
// sub-records read here (5208, 520A, 53FC, 5BCD, ...) use Python's own
// INTEGER tag convention (a true little-endian u16), so Python's numeric
// constants are copied byte-for-byte rather than hand-converted to the
// swapped string form.

type FlatTlvItem<'a> = (u16, &'a [u8]);

/// The whole buffer as flat records; any malformed record (or a zero tag)
/// yields an empty list.
fn tlv_items_int(buf: &[u8]) -> Vec<FlatTlvItem<'_>> {
    let mut items = Vec::new();
    let mut off = 0usize;
    let n = buf.len();
    while off < n {
        if off + 6 > n {
            return Vec::new();
        }
        let tag = u16::from_le_bytes([buf[off], buf[off + 1]]);
        let ln = u32_at(buf, off + 2) as usize;
        if tag == 0 || off + 6 + ln > n {
            return Vec::new();
        }
        items.push((tag, &buf[off + 6..off + 6 + ln]));
        off += 6 + ln;
    }
    items
}

fn tlv_find_int<'a>(items: &[FlatTlvItem<'a>], tag: u16) -> Option<&'a [u8]> {
    items.iter().find(|(t, _)| *t == tag).map(|(_, p)| *p)
}

fn strip_de05(p: &[u8]) -> &[u8] {
    if p.len() >= 6 && p[0] == 0xde && p[1] == 0x05 {
        let idlen = u32_at(p, 2) as usize;
        if idlen <= p.len() - 6 {
            return &p[6..6 + idlen];
        }
    }
    p
}

fn find_child_tag<'a>(nodes: &'a [TlvNode], target: &str) -> Option<&'a TlvNode> {
    for n in nodes {
        if n.tag == target {
            return Some(n);
        }
        if let Some(r) = find_child_tag(&n.children, target) {
            return Some(r);
        }
    }
    None
}

fn vec3_of(p: Option<&[u8]>) -> Option<Vec3> {
    let p = p?;
    if p.len() != 24 {
        return None;
    }
    Some([
        read_f64(p, 0).ok()?,
        read_f64(p, 8).ok()?,
        read_f64(p, 16).ok()?,
    ])
}

/// Accumulates every vertex's persistent id (hex) -> (x, y, z) inches. A vertex
/// is a C409 record: DC05 holds its persistent id (the DE05 var-int payload),
/// C509 its 3xf64 position. Dimension connection points reference geometry by
/// this id. Called once per top-level record.
pub fn scan_vertex_positions(top: &TlvNode, id2pos: &mut BTreeMap<String, Vec3>) {
    fn visit(el: &TlvNode, id2pos: &mut BTreeMap<String, Vec3>) {
        if el.tag == "C409" {
            let dc05 = find_child_tag(&el.children, "DC05");
            let c509 = find_child_tag(&el.children, "C509");
            if let (Some(dc05), Some(c509)) = (dc05, c509) {
                if let Some(pos) = vec3_of(Some(&c509.payload)) {
                    id2pos.insert(hex_upper(strip_de05(&dc05.payload)), pos);
                }
            }
        }
        for c in &el.children {
            visit(c, id2pos);
        }
    }
    visit(top, id2pos);
}

/// Accumulates each instance's persistent id (hex) -> its WORLD transform (a
/// 13-double matrix, or empty for the identity), composing parent x local at
/// every 6419. A dimension connects to geometry INSIDE a placed component; the
/// vertex position is definition-local, so it must be lifted to world by the
/// instance's transform.
pub fn scan_instance_transforms(top: &TlvNode, world: &mut BTreeMap<String, Vec<f64>>) {
    fn visit(el: &TlvNode, parent: &[f64], world: &mut BTreeMap<String, Vec<f64>>) {
        if el.tag == "6419" {
            let d007 = find_child_tag(&el.children, "D007");
            let dc05 = d007.and_then(|d| find_child_tag(&d.children, "DC05"));
            let iid = dc05.map(|d| hex_upper(strip_de05(&d.payload)));
            let mat = find_child_tag(&el.children, "6619")
                .filter(|m| m.payload.len() == 104)
                .map(|m| {
                    (0..13)
                        .map(|i| read_f64(&m.payload, i * 8).unwrap_or(0.0))
                        .collect::<Vec<f64>>()
                });
            let here = match &mat {
                Some(m) => multiply_matrices(parent, m),
                None => parent.to_vec(),
            };
            if let Some(iid) = iid {
                world.insert(iid, here.clone());
            }
            for c in &el.children {
                visit(c, &here, world);
            }
        } else {
            for c in &el.children {
                visit(c, parent, world);
            }
        }
    }
    visit(top, &[], world);
}

/// Linear dimensions (SketchUp's Dimension tool).
///
/// A dimension entity is a 5BCC record (raw bytes `cc 5b`) holding:
///
/// * 5BCD / 5BCE - the two connection points. Each wraps a 5208 whose 5209 is
///   the connection TYPE (1 = a free explicit point in 520A, already world
///   space; 2 = connected to geometry: 520B > 53FC names the target, 53FD the
///   vertex by persistent id, 53FE a length-prefixed persistent id of the
///   INSTANCE holding it - lifted to world by that instance's transform).
/// * 5BCF - the dimension plane normal; 5BD0 - its X axis; 5BD1 - the
///   constrained axis.
/// * 5BD2 - the offset distance (inches).
///
/// Endpoints come out in WORLD space (inches). A connection point that cannot
/// be resolved drops the whole dimension (fail-safe).
pub fn parse_dimensions(
    model_dat: &[u8],
    id2pos: &BTreeMap<String, Vec3>,
    inst_world: &BTreeMap<String, Vec<f64>>,
) -> Vec<RawDimension> {
    let mut dims = Vec::new();
    let n = model_dat.len();

    let point = |block_payload: Option<&[u8]>| -> Option<Vec3> {
        let blk_items = tlv_items_int(block_payload?);
        let blk = tlv_find_int(&blk_items, 0x5208)?;
        let sub = tlv_items_int(blk);
        let typ = tlv_find_int(&sub, 0x5209)
            .filter(|t| t.len() == 4)
            .map(|t| u32_at(t, 0));
        if typ == Some(1) {
            return vec3_of(tlv_find_int(&sub, 0x520a));
        }
        // type 2: resolve the geometry reference (vertex + instance).
        let f53fc =
            tlv_find_int(&sub, 0x520b).and_then(|r| tlv_find_int(&tlv_items_int(r), 0x53fc));
        let fi = f53fc.map(tlv_items_int).unwrap_or_default();
        let vid = tlv_find_int(&fi, 0x53fd)?;
        let iid = tlv_find_int(&fi, 0x53fe);
        let local = *id2pos.get(&hex_upper(vid))?;
        if let Some(iid) = iid {
            if !iid.is_empty() && iid[0] > 0 && (iid[0] as usize) < iid.len() {
                let id_bytes = &iid[1..1 + iid[0] as usize];
                if let Some(w) = inst_world.get(&hex_upper(id_bytes)) {
                    if !w.is_empty() {
                        return Some(transform_point(w, local));
                    }
                }
            }
        }
        Some(local) // model-root vertex - already world
    };

    let mut i = 0usize;
    while let Some(k) = model_dat
        .get(i..)
        .and_then(|s| s.windows(2).position(|w| w == [0xcc, 0x5b]))
    {
        let j = i + k;
        i = j + 1;
        if j + 6 > n {
            continue;
        }
        let ln = u32_at(model_dat, j + 2) as usize;
        if ln < 40 || j + 6 + ln > n {
            continue;
        }
        let body = tlv_items_int(&model_dat[j + 6..j + 6 + ln]);
        if body.is_empty() {
            continue;
        }
        let has_5bcd = body.iter().any(|(t, _)| *t == 0x5bcd);
        let has_5bce = body.iter().any(|(t, _)| *t == 0x5bce);
        if !has_5bcd || !has_5bce {
            continue;
        }
        let (Some(a), Some(b)) = (
            point(tlv_find_int(&body, 0x5bcd)),
            point(tlv_find_int(&body, 0x5bce)),
        ) else {
            continue;
        };
        let off_b = tlv_find_int(&body, 0x5bd2);
        let plane_x = vec3_of(tlv_find_int(&body, 0x5bcf));
        let normal = vec3_of(tlv_find_int(&body, 0x5bd0));
        let offset = off_b
            .filter(|o| o.len() == 8)
            .and_then(|o| read_f64(o, 0).ok())
            .unwrap_or(0.0);
        // VFF 5BCF is the plane normal, 5BD0 its X axis, and 5BD1 the
        // constrained axis. Retain the historical public frame fields, but
        // resolve placement explicitly so clients need not guess the side
        // from the endpoints.
        let offset_vector = match (tlv_find_int(&body, 0x5bd1), plane_x, normal) {
            (Some(axis), Some(plane_normal), Some(x)) if axis.len() == 4 => {
                constrained_offset_vector(u32_at(axis, 0), plane_normal, x, offset)
            }
            _ => None,
        };
        dims.push(RawDimension {
            a,
            b,
            plane_x,
            normal,
            offset,
            offset_vector,
            text: String::new(),
        });
    }
    dims
}

/// The 0702 scenes node inside `top`'s subtree (including `top` itself).
pub fn find_page_node(top: &TlvNode) -> Option<&TlvNode> {
    if top.tag == "0702" {
        return Some(top);
    }
    find_child_tag(&top.children, "0702")
}

/// A view camera (34BC): 34BD eye, 34BE target, 34BF up (3xf64, inches), 34C4
/// field of view (degrees), 34C2 u8 = PERSPECTIVE flag (00 = parallel
/// projection - calibrated against the bundled scene thumbnails), 34C3 f64 =
/// visible height when parallel (inches). Scenes and the model's current view
/// (FA01) use the same record.
pub fn parse_camera(record: &[u8]) -> RawCamera {
    let cam = tlv_items_int(record);
    let mut out = RawCamera {
        eye: vec3_of(tlv_find_int(&cam, 0x34bd)),
        target: vec3_of(tlv_find_int(&cam, 0x34be)),
        up: vec3_of(tlv_find_int(&cam, 0x34bf)),
        ..Default::default()
    };
    if let Some(fov) = tlv_find_int(&cam, 0x34c4).filter(|f| f.len() == 8) {
        out.fov = read_f64(fov, 0).unwrap_or(out.fov);
    }
    out.parallel = tlv_find_int(&cam, 0x34c2).is_some_and(|f| !f.is_empty() && f[0] == 0);
    if let Some(h) = tlv_find_int(&cam, 0x34c3).filter(|h| h.len() == 8) {
        out.ortho_height = read_f64(h, 0).unwrap_or(out.ortho_height);
    }
    out
}

/// Scenes ("pages"). The 0702 node's payload nests 6D60 > 6D61 > one 7148
/// record per page:
///
/// * 6F54 > 6F55 - page name (UTF-8)
/// * 714A > 34BC - camera (see [`parse_camera`])
/// * 7150 - layers hidden in this page: (u8 length, var-int layer id) runs
///
/// Each page's 6F54 also carries its entity id (DC05 > DE05), and 6D62 next to
/// 6D61 holds the id of the scene selected when the model was saved.
pub fn parse_pages(node: Option<&TlvNode>) -> Result<Vec<RawPage>> {
    let mut pages = Vec::new();
    let Some(node) = node else { return Ok(pages) };

    for (t60, p60) in tlv_items_int(&node.payload) {
        if t60 != 0x6d60 {
            continue;
        }
        let t61_items = tlv_items_int(p60);
        let selected_id = tlv_find_int(&t61_items, 0x6d62);
        for &(t61, p61) in &t61_items {
            if t61 != 0x6d61 {
                continue;
            }
            for (t48, p48) in tlv_items_int(p61) {
                if t48 != 0x7148 {
                    continue;
                }
                let items = tlv_items_int(p48);
                if items.is_empty() {
                    continue;
                }
                let mut page = RawPage::default();
                let head = tlv_find_int(&items, 0x6f54)
                    .map(tlv_items_int)
                    .unwrap_or_default();
                if let Some(name) = tlv_find_int(&head, 0x6f55) {
                    if !name.is_empty() {
                        page.name = text(name);
                    }
                }
                if let Some(selected_id) = selected_id {
                    let id = tlv_find_int(&head, 0x05dc)
                        .and_then(|ids| tlv_find_int(&tlv_items_int(ids), 0x05de));
                    page.selected = id == Some(selected_id);
                }
                let cam_wrap = tlv_find_int(&items, 0x714a)
                    .map(tlv_items_int)
                    .unwrap_or_default();
                if let Some(cam_payload) = tlv_find_int(&cam_wrap, 0x34bc) {
                    let cam = parse_camera(cam_payload);
                    page.eye = cam.eye;
                    page.target = cam.target;
                    page.up = cam.up;
                    page.fov = cam.fov;
                    page.parallel = cam.parallel;
                    page.ortho_height = cam.ortho_height;
                }
                if let Some(hidden) = tlv_find_int(&items, 0x7150) {
                    let mut off = 0usize;
                    while off < hidden.len() {
                        let ln = hidden[off] as usize;
                        if ln == 0 || off + 1 + ln > hidden.len() {
                            break;
                        }
                        page.hidden_layer_ids
                            .push(parse_varint(hidden, off + 1, ln)? as EntityId);
                        off += 1 + ln;
                    }
                }
                // A scene that doesn't save its camera is still a scene: it
                // keeps its name and hidden layers, eye/target/up unset.
                pages.push(page);
            }
        }
    }
    Ok(pages)
}
