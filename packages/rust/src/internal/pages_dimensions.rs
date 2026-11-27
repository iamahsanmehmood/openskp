//! VFF (2021+) scenes ("pages") and linear dimensions (`pages_dimensions.cpp`,
//! itself a port of Python's `_core.py`).

use std::collections::{BTreeMap, HashMap, HashSet};

use super::bytes::{parse_varint, read_f64, u32_at};
use super::transforms::{multiply_matrices, transform_point};
use super::{
    hex_upper, text, GeometryBuilder, RawCamera, RawDefinition, RawDimension, RawPage, RawParsed,
    Result, Tag, TlvNode,
};
use crate::model::{constrained_offset_vector, Dimension, EntityId, TextEntity, Vec3};
use crate::scene_build::MAX_EXPANDED_INSTANCES;

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

// 53FE stores SketchUp's instance path as repeated (u8 length, id bytes),
// outer placement first. A root-level dimension on nested groups names the
// cabinet AND each inner instance; the vertex is in the innermost definition.
fn length_prefixed_ids(p: &[u8]) -> Vec<String> {
    let mut ids = Vec::new();
    let mut off = 0usize;
    while off < p.len() {
        let ln = p[off] as usize;
        if ln == 0 || off + 1 + ln > p.len() {
            break;
        }
        ids.push(hex_upper(&p[off + 1..off + 1 + ln]));
        off += 1 + ln;
    }
    ids
}

fn vff_face_uv_point(
    builder: Option<&GeometryBuilder>,
    face_id: EntityId,
    c0: f64,
    c1: f64,
    c2: f64,
) -> Option<Vec3> {
    let builder = builder?;
    if !c0.is_finite() || !c1.is_finite() || !c2.is_finite() {
        return None;
    }
    let face = builder.faces.get(&face_id)?;
    let outer = face.loops.first()?;
    if outer.len() < 3 {
        return None;
    }
    let [nx, ny, nz] = face.normal;
    if !nx.is_finite() || !ny.is_finite() || !nz.is_finite() {
        return None;
    }
    let n_len = (nx * nx + ny * ny + nz * nz).sqrt();
    if !n_len.is_finite() || n_len < 1e-12 {
        return None;
    }
    let (nhx, nhy, nhz) = (nx / n_len, ny / n_len, nz / n_len);
    let mut pts = Vec::with_capacity(outer.len());
    for co in outer {
        let &(v1, v2) = builder.edges.get(&co.edge_id)?;
        let vid = if co.orientation >= 0 { v1? } else { v2? };
        let &vpt = builder.vertices.get(&vid)?;
        if !vpt[0].is_finite() || !vpt[1].is_finite() || !vpt[2].is_finite() {
            return None;
        }
        pts.push(vpt);
    }
    if pts.len() < 3 {
        return None;
    }
    let [p0x, p0y, p0z] = pts[0];
    let mut uh = None;
    for p in &pts[1..] {
        let (dx, dy, dz) = (p[0] - p0x, p[1] - p0y, p[2] - p0z);
        let u_len = (dx * dx + dy * dy + dz * dz).sqrt();
        if u_len.is_finite() && u_len >= 1e-12 {
            uh = Some([dx / u_len, dy / u_len, dz / u_len]);
            break;
        }
    }
    let [uhx, uhy, uhz] = uh?;
    let vhx = nhy * uhz - nhz * uhy;
    let vhy = nhz * uhx - nhx * uhz;
    let vhz = nhx * uhy - nhy * uhx;
    let (mut u_min, mut u_max) = (0.0_f64, 0.0_f64);
    let (mut v_min, mut v_max) = (0.0_f64, 0.0_f64);
    for p in &pts[1..] {
        let (dx, dy, dz) = (p[0] - p0x, p[1] - p0y, p[2] - p0z);
        let du = dx * uhx + dy * uhy + dz * uhz;
        let dv = dx * vhx + dy * vhy + dz * vhz;
        if du < u_min {
            u_min = du;
        }
        if du > u_max {
            u_max = du;
        }
        if dv < v_min {
            v_min = dv;
        }
        if dv > v_max {
            v_max = dv;
        }
    }
    let su = c0 * (u_max - u_min);
    let sv = c1 * (v_max - v_min);
    let rx = p0x + su * uhx + sv * vhx + c2 * nhx;
    let ry = p0y + su * uhy + sv * vhy + c2 * nhy;
    let rz = p0z + su * uhz + sv * vhz + c2 * nhz;
    if !rx.is_finite() || !ry.is_finite() || !rz.is_finite() {
        return None;
    }
    Some([rx, ry, rz])
}

fn text_connection_point(
    block_payload: Option<&[u8]>,
    id2pos: &BTreeMap<String, Vec3>,
    resolver: &mut InstancePathResolver,
    root: &RawDefinition,
    definitions: &BTreeMap<EntityId, RawDefinition>,
    owner: Option<EntityId>,
) -> Option<Vec3> {
    let blk_items = tlv_items_int(block_payload?);
    let blk = tlv_find_int(&blk_items, 0x5208)?;
    let sub = tlv_items_int(blk);
    let typ = tlv_find_int(&sub, 0x5209)
        .filter(|t| t.len() == 4)
        .map(|t| u32_at(t, 0));
    if typ == Some(7) {
        let pos = tlv_find_int(&sub, 0x520a).filter(|p| p.len() == 24)?;
        let f53fc =
            tlv_find_int(&sub, 0x520b).and_then(|r| tlv_find_int(&tlv_items_int(r), 0x53fc));
        let fi = f53fc.map(tlv_items_int).unwrap_or_default();
        let vid = tlv_find_int(&fi, 0x53fd).filter(|v| !v.is_empty() && v.len() <= 8)?;
        let iid = tlv_find_int(&fi, 0x53fe);
        let face_id = parse_varint(vid, 0, vid.len()).ok()? as EntityId;
        let c0 = read_f64(pos, 0).ok()?;
        let c1 = read_f64(pos, 8).ok()?;
        let c2 = read_f64(pos, 16).ok()?;
        let mut path = Vec::new();
        if let Some(iid) = iid {
            if !iid.is_empty() {
                path = length_prefixed_ids(iid);
                if path.is_empty() {
                    path.push(hex_upper(iid));
                }
            }
        }
        return resolver.resolve_face_point(root, definitions, owner, face_id, c0, c1, c2, &path);
    }
    dimension_connection_point(block_payload, id2pos, |p| {
        resolver.resolve_path(root, definitions, owner, p, None)
    })
}

fn dimension_connection_point<F>(
    block_payload: Option<&[u8]>,
    id2pos: &BTreeMap<String, Vec3>,
    mut resolve_path: F,
) -> Option<Vec3>
where
    F: FnMut(&[String]) -> Option<Vec<f64>>,
{
    let blk_items = tlv_items_int(block_payload?);
    let blk = tlv_find_int(&blk_items, 0x5208)?;
    let sub = tlv_items_int(blk);
    let typ = tlv_find_int(&sub, 0x5209)
        .filter(|t| t.len() == 4)
        .map(|t| u32_at(t, 0));
    if typ == Some(1) {
        return vec3_of(tlv_find_int(&sub, 0x520a));
    }
    let f53fc = tlv_find_int(&sub, 0x520b).and_then(|r| tlv_find_int(&tlv_items_int(r), 0x53fc));
    let fi = f53fc.map(tlv_items_int).unwrap_or_default();
    let vid = tlv_find_int(&fi, 0x53fd)?;
    let iid = tlv_find_int(&fi, 0x53fe);
    let local = *id2pos.get(&hex_upper(vid))?;
    if let Some(iid) = iid {
        if !iid.is_empty() {
            let mut path = length_prefixed_ids(iid);
            if path.is_empty() {
                path.push(hex_upper(iid));
            }
            if let Some(w) = resolve_path(&path) {
                if !w.is_empty() {
                    return Some(transform_point(&w, local));
                }
            }
        }
    }
    Some(local)
}

// Header-only check that buf[begin..end] is a well-formed flat TLV carrying
// both required fields. Raw byte searches hit mesh/float data far more often
// than real annotation records; this rejects false hits before copying.
fn flat_tlv_has_fields(buf: &[u8], begin: usize, end: usize, first: u16, second: u16) -> bool {
    let mut has_first = false;
    let mut has_second = false;
    let mut off = begin;
    while off < end {
        if off + 6 > end {
            return false;
        }
        let tag = u16::from_le_bytes([buf[off], buf[off + 1]]);
        let ln = u32_at(buf, off + 2) as usize;
        if tag == 0 || ln > end - off - 6 {
            return false;
        }
        if tag == first {
            has_first = true;
        }
        if tag == second {
            has_second = true;
        }
        off += 6 + ln;
    }
    has_first && has_second
}

fn parse_one_5bcc<F>(
    model_dat: &[u8],
    j: usize,
    id2pos: &BTreeMap<String, Vec3>,
    mut resolve_path: F,
) -> Option<RawDimension>
where
    F: FnMut(&[String]) -> Option<Vec<f64>>,
{
    let n = model_dat.len();
    if j + 6 > n {
        return None;
    }
    let ln = u32_at(model_dat, j + 2) as usize;
    if ln < 40 || j + 6 + ln > n {
        return None;
    }
    if !flat_tlv_has_fields(model_dat, j + 6, j + 6 + ln, 0x5bcd, 0x5bce) {
        return None;
    }
    let body = tlv_items_int(&model_dat[j + 6..j + 6 + ln]);
    if body.is_empty() {
        return None;
    }
    let a = dimension_connection_point(tlv_find_int(&body, 0x5bcd), id2pos, &mut resolve_path)?;
    let b = dimension_connection_point(tlv_find_int(&body, 0x5bce), id2pos, &mut resolve_path)?;
    let mut layer = String::new();
    let mut hidden = false;
    // Dimension base (59D8) owns the drawing properties. Connection targets may
    // contain other entities: never search those for the dimension's tag/flags.
    if let Some(base) = tlv_find_int(&body, 0x59d8) {
        if let Some(draw) = tlv_find_int(&tlv_items_int(base), 0x07d0) {
            let props = tlv_items_int(draw);
            if let Some(l) = tlv_find_int(&props, 0x07d2).filter(|l| !l.is_empty()) {
                if let Ok(lid) = parse_varint(l, 0, l.len()) {
                    layer = lid.to_string();
                }
            }
            if let Some(flags) = tlv_find_int(&props, 0x07d3).filter(|f| !f.is_empty()) {
                hidden = flags[0] & 0x01 != 0;
            }
        }
    }
    let plane_x = vec3_of(tlv_find_int(&body, 0x5bcf));
    let normal = vec3_of(tlv_find_int(&body, 0x5bd0));
    let offset = tlv_find_int(&body, 0x5bd2)
        .filter(|o| o.len() == 8)
        .and_then(|o| read_f64(o, 0).ok())
        .unwrap_or(0.0);
    // VFF 5BCF is the plane normal, 5BD0 its X axis, and 5BD1 the constrained
    // axis. Retain the historical public frame fields, but resolve placement
    // explicitly so clients need not guess the side from the endpoints.
    let offset_vector = match (tlv_find_int(&body, 0x5bd1), plane_x, normal) {
        (Some(axis), Some(plane_normal), Some(x)) if axis.len() == 4 => {
            constrained_offset_vector(u32_at(axis, 0), plane_normal, x, offset)
        }
        _ => None,
    };
    Some(RawDimension {
        a,
        b,
        offset,
        plane_x,
        normal,
        offset_vector,
        layer,
        hidden,
        text: String::new(),
    })
}

fn parse_text(
    model_dat: &[u8],
    j: usize,
    id2pos: &BTreeMap<String, Vec3>,
    resolver: &mut InstancePathResolver,
    root: &RawDefinition,
    definitions: &BTreeMap<EntityId, RawDefinition>,
    owner: Option<EntityId>,
) -> Option<TextEntity> {
    let size = u32_at(model_dat, j + 2) as usize;
    let fields = tlv_items_int(&model_dat[j + 6..j + 6 + size]);
    let value = tlv_find_int(&fields, 0x55f1)?;
    let connection = tlv_find_int(&fields, 0x55f4)?;
    let mut out = TextEntity {
        text: text(value),
        point: text_connection_point(Some(connection), id2pos, resolver, root, definitions, owner),
        label_point: vec3_of(tlv_find_int(&fields, 0x55f5)),
        ..Default::default()
    };
    if let (Some(x), Some(y)) = (tlv_find_int(&fields, 0x55f2), tlv_find_int(&fields, 0x55f3)) {
        if x.len() == 8 && y.len() == 8 {
            if let (Ok(fx), Ok(fy)) = (read_f64(x, 0), read_f64(y, 0)) {
                out.screen_position = Some([fx, fy]);
            }
        }
    }
    if let Some(t) = tlv_find_int(&fields, 0x55fd).filter(|t| t.len() == 4) {
        out.leader_type = u32_at(t, 0) as i32;
    }
    if let Some(a) = tlv_find_int(&fields, 0x55fa).filter(|a| a.len() == 4) {
        out.arrow_type = u32_at(a, 0) as i32;
    }
    if let Some(d) = tlv_find_int(&fields, 0x55fc).filter(|d| d.len() == 1) {
        out.display_leader = d[0] != 0;
    }
    if let Some(draw) = tlv_find_int(&fields, 0x07d0) {
        let props = tlv_items_int(draw);
        if let Some(l) = tlv_find_int(&props, 0x07d2).filter(|l| !l.is_empty()) {
            if let Ok(lid) = parse_varint(l, 0, l.len()) {
                out.layer = lid.to_string();
            }
        }
        if let Some(flags) = tlv_find_int(&props, 0x07d3).filter(|f| !f.is_empty()) {
            out.hidden = flags[0] & 0x01 != 0;
        }
    }
    Some(out)
}

fn public_dimension(d: &RawDimension) -> Dimension {
    Dimension {
        text: d.text.clone(),
        hidden: d.hidden,
        a: Some(d.a),
        b: Some(d.b),
        offset: d.offset,
        plane_x: d.plane_x,
        normal: d.normal,
        offset_vector: d.offset_vector,
        layer: d.layer.clone(),
    }
}

struct DefSpan {
    begin: usize,
    end: usize,
    id: EntityId,
}

struct TlvHeader {
    offset: usize,
    size: usize,
}

fn tlv_headers(md: &[u8], start: usize, end: usize) -> Vec<TlvHeader> {
    let mut out = Vec::new();
    let mut p = start;
    while p + 6 <= end {
        let n = u32_at(md, p + 2) as usize;
        if n > end - p - 6 {
            break;
        }
        out.push(TlvHeader { offset: p, size: n });
        p += 6 + n;
    }
    out
}

// Same first-id rule as collect_definitions: direct DE05 or DC05, then recurse
// only dictionary / entity-header containers. Walking C409/6419 would steal a
// vertex or instance id.
fn entity_id_from_tlv(md: &[u8], rec: usize, rec_end: usize, depth: usize) -> Option<EntityId> {
    if depth > 24 || rec + 6 > rec_end {
        return None;
    }
    let kids = tlv_headers(md, rec + 6, rec_end);
    for k in &kids {
        let t = Tag::from_bytes(md[k.offset], md[k.offset + 1]);
        if t == "DE05" && k.size > 0 && k.size <= 8 {
            if let Ok(v) = parse_varint(md, k.offset + 6, k.size) {
                return Some(v as EntityId);
            }
        }
        if t == "DC05" && k.size >= 6 && md[k.offset + 6] == 0xde && md[k.offset + 7] == 5 {
            let z = u32_at(md, k.offset + 8) as usize;
            if z > 0 && z <= 8 && z <= k.size - 6 {
                if let Ok(v) = parse_varint(md, k.offset + 12, z) {
                    return Some(v as EntityId);
                }
            }
        }
    }
    for k in &kids {
        let t = Tag::from_bytes(md[k.offset], md[k.offset + 1]);
        if !matches!(
            t.as_str(),
            "8813" | "D007" | "8913" | "8A13" | "8B13" | "8C13" | "8D13"
        ) {
            continue;
        }
        if let Some(id) = entity_id_from_tlv(md, k.offset, k.offset + 6 + k.size, depth + 1) {
            return Some(id);
        }
    }
    None
}

// Only the F901 -> 7017 -> 7117 definition list. A raw `7c 15` hunt inside
// F601/geometry false-matches and parks model-root (or in-group) 5BCC on the
// wrong definition.
fn scan_definition_spans(md: &[u8], defs: &BTreeMap<EntityId, RawDefinition>) -> Vec<DefSpan> {
    let mut spans = Vec::new();
    if md.len() < 6 {
        return spans;
    }
    let mut hs = tlv_headers(md, 0, md.len());
    if hs.len() == 1 && md[hs[0].offset] == 0xf4 && md[hs[0].offset + 1] == 1 {
        hs = tlv_headers(md, hs[0].offset + 6, hs[0].offset + 6 + hs[0].size);
    }
    for h in &hs {
        if Tag::from_bytes(md[h.offset], md[h.offset + 1]) != "F901" {
            continue;
        }
        let mut level = tlv_headers(md, h.offset + 6, h.offset + 6 + h.size);
        let mut ok = true;
        for expected in ["7017", "7117"] {
            let found = level
                .iter()
                .find(|rec| Tag::from_bytes(md[rec.offset], md[rec.offset + 1]) == expected);
            if let Some(rec) = found {
                level = tlv_headers(md, rec.offset + 6, rec.offset + 6 + rec.size);
            } else {
                ok = false;
                break;
            }
        }
        if !ok {
            continue;
        }
        for rec in &level {
            if Tag::from_bytes(md[rec.offset], md[rec.offset + 1]) != "7C15" {
                continue;
            }
            if let Some(id) = entity_id_from_tlv(md, rec.offset, rec.offset + 6 + rec.size, 0) {
                if defs.contains_key(&id) {
                    spans.push(DefSpan {
                        begin: rec.offset,
                        end: rec.offset + 6 + rec.size,
                        id,
                    });
                }
            }
        }
    }
    spans
}

fn owning_definition(spans: &[DefSpan], off: usize) -> Option<EntityId> {
    let mut best = None;
    let mut best_len = usize::MAX;
    for s in spans {
        if off < s.begin || off >= s.end {
            continue;
        }
        let len = s.end - s.begin;
        if len < best_len {
            best_len = len;
            best = Some(s.id);
        }
    }
    best
}

// Recursive non-empty tag search over buf[begin..end], in place.
fn tlv_has_nonempty_tag(buf: &[u8], begin: usize, end: usize, tag: u16, depth: usize) -> bool {
    if depth > 64 {
        return false;
    }
    let mut off = begin;
    while off < end {
        if off + 6 > end {
            return false;
        }
        let t = u16::from_le_bytes([buf[off], buf[off + 1]]);
        let ln = u32_at(buf, off + 2) as usize;
        if t == 0 || ln > end - off - 6 {
            return false;
        }
        off += 6 + ln;
    }
    off = begin;
    while off < end {
        let t = u16::from_le_bytes([buf[off], buf[off + 1]]);
        let ln = u32_at(buf, off + 2) as usize;
        let hit = t == tag && ln > 0;
        if hit || tlv_has_nonempty_tag(buf, off + 6, off + 6 + ln, tag, depth + 1) {
            return true;
        }
        off += 6 + ln;
    }
    false
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

/// Composes parent x local transforms through the definition graph starting at
/// `root`, populating `world` by each instance's `persistent_id`.
pub fn compose_instance_world(
    root: &RawDefinition,
    definitions: &BTreeMap<EntityId, RawDefinition>,
    world: &mut BTreeMap<String, Vec<f64>>,
) -> u64 {
    compose_instance_world_with_limit(root, definitions, world, MAX_EXPANDED_INSTANCES)
}

/// Bounded variant of [`compose_instance_world`] capping visited placements at
/// `max_visits`.
pub fn compose_instance_world_with_limit(
    root: &RawDefinition,
    definitions: &BTreeMap<EntityId, RawDefinition>,
    world: &mut BTreeMap<String, Vec<f64>>,
    max_visits: u64,
) -> u64 {
    fn walk(
        def: &RawDefinition,
        definitions: &BTreeMap<EntityId, RawDefinition>,
        parent: &[f64],
        depth: usize,
        max_visits: u64,
        visits: &mut u64,
        world: &mut BTreeMap<String, Vec<f64>>,
    ) {
        if depth > 64 || *visits >= max_visits {
            return;
        }
        for inst in &def.builder.instances {
            if *visits >= max_visits {
                return;
            }
            *visits += 1;
            let here = if inst.matrix.is_empty() {
                parent.to_vec()
            } else {
                multiply_matrices(parent, &inst.matrix)
            };
            if !inst.persistent_id.is_empty() {
                world.insert(inst.persistent_id.clone(), here.clone());
            }
            if let Some(ref_idx) = inst.ref_idx {
                if let Some(child) = definitions.get(&ref_idx) {
                    walk(
                        child,
                        definitions,
                        &here,
                        depth + 1,
                        max_visits,
                        visits,
                        world,
                    );
                }
            }
        }
    }
    let mut visits = 0u64;
    walk(root, definitions, &[], 0, max_visits, &mut visits, world);
    visits
}

type PathCacheKey = (Option<EntityId>, Option<EntityId>, Vec<String>);
type PathCacheVal = Option<(Vec<f64>, Option<EntityId>)>;

struct InstancePathResolver {
    max_visits: u64,
    visits: u64,
    indexed: bool,
    by_pid: HashMap<Option<EntityId>, HashMap<String, usize>>,
    holders_of_pid: HashMap<String, Vec<Option<EntityId>>>,
    parents_of_def: HashMap<EntityId, Vec<Option<EntityId>>>,
    resolved: HashMap<PathCacheKey, PathCacheVal>,
}

impl InstancePathResolver {
    fn new(max_visits: u64) -> Self {
        Self {
            max_visits,
            visits: 0,
            indexed: false,
            by_pid: HashMap::new(),
            holders_of_pid: HashMap::new(),
            parents_of_def: HashMap::new(),
            resolved: HashMap::new(),
        }
    }

    fn builder_for<'a>(
        root: &'a RawDefinition,
        definitions: &'a BTreeMap<EntityId, RawDefinition>,
        owner: Option<EntityId>,
    ) -> Option<&'a GeometryBuilder> {
        match owner {
            Some(id) => definitions.get(&id).map(|d| &d.builder),
            None => Some(&root.builder),
        }
    }

    fn inst_index(
        &mut self,
        owner: Option<EntityId>,
        builder: &GeometryBuilder,
        pid: &str,
    ) -> Option<usize> {
        let map = self.by_pid.entry(owner).or_insert_with(|| {
            let mut m = HashMap::new();
            for (idx, inst) in builder.instances.iter().enumerate() {
                if !inst.persistent_id.is_empty() {
                    m.insert(inst.persistent_id.to_ascii_uppercase(), idx);
                }
            }
            m
        });
        map.get(pid).copied()
    }

    fn ensure_index(
        &mut self,
        root: &RawDefinition,
        definitions: &BTreeMap<EntityId, RawDefinition>,
    ) {
        if self.indexed {
            return;
        }
        self.indexed = true;
        let mut owners = Vec::with_capacity(1 + definitions.len());
        owners.push(None);
        owners.extend(definitions.keys().copied().map(Some));
        for def_owner in owners {
            let Some(builder) = Self::builder_for(root, definitions, def_owner) else {
                continue;
            };
            let mut seen_pids = HashSet::new();
            let mut seen_refs = HashSet::new();
            for inst in &builder.instances {
                if !inst.persistent_id.is_empty() {
                    let norm_pid = inst.persistent_id.to_ascii_uppercase();
                    if seen_pids.insert(norm_pid.clone()) {
                        self.holders_of_pid
                            .entry(norm_pid)
                            .or_default()
                            .push(def_owner);
                    }
                }
                if let Some(ref_idx) = inst.ref_idx {
                    if definitions.contains_key(&ref_idx) && seen_refs.insert(ref_idx) {
                        self.parents_of_def
                            .entry(ref_idx)
                            .or_default()
                            .push(def_owner);
                    }
                }
            }
        }
    }

    #[allow(clippy::too_many_arguments)]
    fn walk_exact(
        &mut self,
        root: &RawDefinition,
        definitions: &BTreeMap<EntityId, RawDefinition>,
        owner: Option<EntityId>,
        builder: Option<&GeometryBuilder>,
        path: &[String],
        idx: usize,
        parent_xf: &[f64],
        depth: usize,
        query_visits: &mut u64,
    ) -> PathCacheVal {
        if depth > 64 || *query_visits >= self.max_visits {
            return None;
        }
        let builder = builder?;
        let inst_idx = self.inst_index(owner, builder, &path[idx])?;
        self.visits += 1;
        *query_visits += 1;
        let inst = &builder.instances[inst_idx];
        let here = if inst.matrix.is_empty() {
            parent_xf.to_vec()
        } else {
            multiply_matrices(parent_xf, &inst.matrix)
        };
        if idx + 1 == path.len() {
            return Some((here, inst.ref_idx));
        }
        let ref_idx = inst.ref_idx?;
        let child = Self::builder_for(root, definitions, Some(ref_idx));
        self.walk_exact(
            root,
            definitions,
            Some(ref_idx),
            child,
            path,
            idx + 1,
            &here,
            depth + 1,
            query_visits,
        )
    }

    #[allow(clippy::too_many_arguments)]
    fn walk_to_holder(
        &mut self,
        root: &RawDefinition,
        definitions: &BTreeMap<EntityId, RawDefinition>,
        cur_owner: Option<EntityId>,
        cur_builder: Option<&GeometryBuilder>,
        path: &[String],
        parent_xf: &[f64],
        depth: usize,
        valid_holders: &HashSet<Option<EntityId>>,
        can_reach: &HashSet<Option<EntityId>>,
        active_defs: &mut HashSet<EntityId>,
        failed_min_depth: &mut HashMap<EntityId, usize>,
        query_visits: &mut u64,
    ) -> PathCacheVal {
        if depth > 64 || *query_visits >= self.max_visits {
            return None;
        }
        let cur_builder = cur_builder?;
        if depth > 0 && valid_holders.contains(&cur_owner) && depth + path.len() <= 65 {
            let tail = self.walk_exact(
                root,
                definitions,
                cur_owner,
                Some(cur_builder),
                path,
                0,
                parent_xf,
                depth,
                query_visits,
            );
            if tail.is_some() || *query_visits >= self.max_visits {
                return tail;
            }
        }
        for inst in &cur_builder.instances {
            if *query_visits >= self.max_visits {
                return None;
            }
            let Some(ref_idx) = inst.ref_idx else {
                continue;
            };
            if !can_reach.contains(&Some(ref_idx)) || active_defs.contains(&ref_idx) {
                continue;
            }
            if let Some(&prev_fail) = failed_min_depth.get(&ref_idx) {
                if depth + 1 >= prev_fail {
                    continue;
                }
            }
            let Some(child) = Self::builder_for(root, definitions, Some(ref_idx)) else {
                continue;
            };
            self.visits += 1;
            *query_visits += 1;
            let here = if inst.matrix.is_empty() {
                parent_xf.to_vec()
            } else {
                multiply_matrices(parent_xf, &inst.matrix)
            };
            active_defs.insert(ref_idx);
            let found = self.walk_to_holder(
                root,
                definitions,
                Some(ref_idx),
                Some(child),
                path,
                &here,
                depth + 1,
                valid_holders,
                can_reach,
                active_defs,
                failed_min_depth,
                query_visits,
            );
            active_defs.remove(&ref_idx);
            if found.is_some() {
                return found;
            }
            if *query_visits >= self.max_visits {
                return None;
            }
        }
        if let Some(id) = cur_owner {
            let entry = failed_min_depth.entry(id).or_insert(depth);
            if depth < *entry {
                *entry = depth;
            }
        }
        None
    }

    fn resolve_path_with_leaf(
        &mut self,
        root: &RawDefinition,
        definitions: &BTreeMap<EntityId, RawDefinition>,
        owner: Option<EntityId>,
        raw_path: &[String],
        fallback_owner: Option<EntityId>,
    ) -> PathCacheVal {
        if raw_path.is_empty() {
            return None;
        }
        let norm: Vec<String> = raw_path.iter().map(|s| s.to_ascii_uppercase()).collect();
        let key = (owner, fallback_owner, norm);
        if let Some(cached) = self.resolved.get(&key) {
            return cached.clone();
        }
        let mut query_visits = 0u64;
        let start = Self::builder_for(root, definitions, owner);
        let mut res = self.walk_exact(
            root,
            definitions,
            owner,
            start,
            &key.2,
            0,
            &[],
            0,
            &mut query_visits,
        );
        if res.is_some() {
            self.resolved.insert(key, res.clone());
            return res;
        }
        if query_visits >= self.max_visits {
            return None;
        }

        self.ensure_index(root, definitions);
        let holders = self
            .holders_of_pid
            .get(&key.2[0])
            .cloned()
            .unwrap_or_default();
        let mut valid_holders = HashSet::new();
        let mut fb_result: PathCacheVal = None;
        for holder in holders {
            if holder == owner {
                continue;
            }
            if query_visits >= self.max_visits {
                break;
            }
            let h_builder = Self::builder_for(root, definitions, holder);
            if let Some(h_res) = self.walk_exact(
                root,
                definitions,
                holder,
                h_builder,
                &key.2,
                0,
                &[],
                0,
                &mut query_visits,
            ) {
                valid_holders.insert(holder);
                if fallback_owner.is_some() && holder == fallback_owner {
                    fb_result = Some(h_res);
                }
            }
        }

        if !valid_holders.is_empty() && query_visits < self.max_visits {
            let mut can_reach: HashSet<Option<EntityId>> = valid_holders.iter().copied().collect();
            let mut queue: Vec<EntityId> = valid_holders.iter().filter_map(|&h| h).collect();
            let mut qi = 0usize;
            while qi < queue.len() {
                let cur = queue[qi];
                qi += 1;
                if let Some(parents) = self.parents_of_def.get(&cur) {
                    for &parent_owner in parents {
                        if can_reach.insert(parent_owner) {
                            if let Some(pid) = parent_owner {
                                queue.push(pid);
                            }
                        }
                    }
                }
            }

            if can_reach.contains(&owner) {
                let mut active_defs = HashSet::new();
                if let Some(id) = owner {
                    active_defs.insert(id);
                }
                let mut failed_min_depth = HashMap::new();
                res = self.walk_to_holder(
                    root,
                    definitions,
                    owner,
                    start,
                    &key.2,
                    &[],
                    0,
                    &valid_holders,
                    &can_reach,
                    &mut active_defs,
                    &mut failed_min_depth,
                    &mut query_visits,
                );
            }
        }

        if res.is_none() && fallback_owner.is_some() && fallback_owner != owner {
            if fb_result.is_some() {
                res = fb_result;
            } else if query_visits < self.max_visits && valid_holders.contains(&fallback_owner) {
                let fb_builder = Self::builder_for(root, definitions, fallback_owner);
                res = self.walk_exact(
                    root,
                    definitions,
                    fallback_owner,
                    fb_builder,
                    &key.2,
                    0,
                    &[],
                    0,
                    &mut query_visits,
                );
            }
        }

        if query_visits < self.max_visits {
            self.resolved.insert(key, res.clone());
        }
        res
    }

    fn resolve_path(
        &mut self,
        root: &RawDefinition,
        definitions: &BTreeMap<EntityId, RawDefinition>,
        owner: Option<EntityId>,
        raw_path: &[String],
        fallback_owner: Option<EntityId>,
    ) -> Option<Vec<f64>> {
        self.resolve_path_with_leaf(root, definitions, owner, raw_path, fallback_owner)
            .map(|(xf, _)| xf)
    }

    #[allow(clippy::too_many_arguments)]
    fn resolve_face_point(
        &mut self,
        root: &RawDefinition,
        definitions: &BTreeMap<EntityId, RawDefinition>,
        owner: Option<EntityId>,
        face_id: EntityId,
        c0: f64,
        c1: f64,
        c2: f64,
        raw_path: &[String],
    ) -> Option<Vec3> {
        if raw_path.is_empty() {
            let builder = Self::builder_for(root, definitions, owner);
            return vff_face_uv_point(builder, face_id, c0, c1, c2);
        }
        let (xf, leaf_def) =
            self.resolve_path_with_leaf(root, definitions, owner, raw_path, None)?;
        let leaf_def = leaf_def?;
        let local = vff_face_uv_point(
            Self::builder_for(root, definitions, Some(leaf_def)),
            face_id,
            c0,
            c1,
            c2,
        )?;
        if !xf.is_empty() {
            let pt = transform_point(&xf, local);
            if !pt[0].is_finite() || !pt[1].is_finite() || !pt[2].is_finite() {
                return None;
            }
            return Some(pt);
        }
        Some(local)
    }
}

/// Linear dimensions (SketchUp's Dimension tool). 5BCC records inside a 7C15
/// component/group belong to that definition (local space); records outside any
/// definition are model-root (world space). See [`assign_vff_annotations`].
/// `parse_dimensions` keeps the older "every 5BCC against one xf map" behaviour
/// for unit tests and `SkpModel::dimensions`.
pub fn parse_dimensions(
    model_dat: &[u8],
    id2pos: &BTreeMap<String, Vec3>,
    inst_world: &BTreeMap<String, Vec<f64>>,
) -> Vec<RawDimension> {
    let mut dims = Vec::new();
    let mut i = 0usize;
    while let Some(k) = model_dat
        .get(i..)
        .and_then(|s| s.windows(2).position(|w| w == [0xcc, 0x5b]))
    {
        let j = i + k;
        i = j + 1;
        let resolve = |path: &[String]| {
            path.iter()
                .rev()
                .find_map(|id| inst_world.get(id).filter(|w| !w.is_empty()).cloned())
        };
        if let Some(d) = parse_one_5bcc(model_dat, j, id2pos, resolve) {
            dims.push(d);
        }
    }
    dims
}

/// Assigns VFF dimensions (`5BCC`) and texts (`55F0`) to their owning
/// definition (`7C15`) or to `parsed.root`, and populates `parsed.dimensions`.
pub fn assign_vff_annotations(
    model_dat: &[u8],
    id2pos: &BTreeMap<String, Vec3>,
    parsed: &mut RawParsed,
) -> u64 {
    assign_vff_annotations_with_limit(model_dat, id2pos, parsed, MAX_EXPANDED_INSTANCES)
}

/// Bounded variant of [`assign_vff_annotations`] capping visited placements at
/// `max_visits`.
pub fn assign_vff_annotations_with_limit(
    model_dat: &[u8],
    id2pos: &BTreeMap<String, Vec3>,
    parsed: &mut RawParsed,
    max_visits: u64,
) -> u64 {
    let mut spans: Option<Vec<DefSpan>> = None;
    let mut resolver = InstancePathResolver::new(max_visits);
    let mut i = 0usize;
    while let Some(k) = model_dat.get(i..).and_then(|s| {
        s.windows(2)
            .position(|w| w == [0xcc, 0x5b] || w == [0xf0, 0x55])
    }) {
        let j = i + k;
        i = j + 1;
        if j + 6 > model_dat.len() {
            continue;
        }
        let ln = u32_at(model_dat, j + 2) as usize;
        if ln < 40 || j + 6 + ln > model_dat.len() {
            continue;
        }
        let is_text = model_dat[j] == 0xf0;
        let (first, second) = if is_text {
            (0x55f1, 0x55f4)
        } else {
            (0x5bcd, 0x5bce)
        };
        let end = j + 6 + ln;
        if !flat_tlv_has_fields(model_dat, j + 6, end, first, second) {
            continue;
        }
        if spans.is_none() {
            spans = Some(scan_definition_spans(model_dat, &parsed.definitions));
        }
        let owner = owning_definition(spans.as_deref().unwrap_or(&[]), j);
        if is_text {
            let text_opt = parse_text(
                model_dat,
                j,
                id2pos,
                &mut resolver,
                &parsed.root,
                &parsed.definitions,
                owner,
            );
            if let Some(t) = text_opt {
                let builder = match owner.and_then(|oid| parsed.definitions.get_mut(&oid)) {
                    Some(def) => &mut def.builder,
                    None => &mut parsed.root.builder,
                };
                builder.texts.push(t);
            }
            continue;
        }
        let dim_opt = {
            let root = &parsed.root;
            let defs = &parsed.definitions;
            let resolve = |p: &[String]| resolver.resolve_path(root, defs, owner, p, None);
            parse_one_5bcc(model_dat, j, id2pos, resolve)
        };
        let has_53fe = owner.is_some() && tlv_has_nonempty_tag(model_dat, j + 6, end, 0x53fe, 0);
        let world_dim_opt = if has_53fe {
            let root = &parsed.root;
            let defs = &parsed.definitions;
            let resolve = |p: &[String]| resolver.resolve_path(root, defs, None, p, owner);
            parse_one_5bcc(model_dat, j, id2pos, resolve)
        } else {
            None
        };
        if let Some(d) = dim_opt {
            let pub_dim = public_dimension(&d);
            let builder = match owner.and_then(|oid| parsed.definitions.get_mut(&oid)) {
                Some(def) => &mut def.builder,
                None => &mut parsed.root.builder,
            };
            builder.dimensions.push(pub_dim);
            if !has_53fe {
                parsed.dimensions.push(d);
            }
        }
        if let Some(wd) = world_dim_opt {
            parsed.dimensions.push(wd);
        }
    }
    resolver.visits
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

fn parse_id_runs(runs: Option<&[u8]>) -> Result<Vec<EntityId>> {
    let mut out = Vec::new();
    let Some(runs) = runs else { return Ok(out) };
    let mut off = 0usize;
    while off < runs.len() {
        let ln = runs[off] as usize;
        if ln == 0 || off + 1 + ln > runs.len() {
            break;
        }
        let id = parse_varint(runs, off + 1, ln)?;
        out.push(id as EntityId);
        off += 1 + ln;
    }
    Ok(out)
}

/// Scenes ("pages"). The 0702 node's payload nests 6D60 > 6D61 > one 7148
/// record per page:
///
/// * 6F54 > 6F55 - page name (UTF-8)
/// * 714A > 34BC - camera (see [`parse_camera`])
/// * 714B - entities hidden in this page: (u8 length, var-int entity id) runs
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
                if let Some(flags) = tlv_find_int(&items, 0x7149).filter(|b| b.len() == 4) {
                    page.capture_flags = Some(u32_at(flags, 0));
                }
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
                page.hidden_layer_ids = parse_id_runs(tlv_find_int(&items, 0x7150))?;
                page.hidden_entity_ids = parse_id_runs(tlv_find_int(&items, 0x714b))?;
                // A scene that doesn't save its camera is still a scene: it
                // keeps its name and hidden layers, eye/target/up unset.
                pages.push(page);
            }
        }
    }
    Ok(pages)
}
