//! VFF geometry, layer, material-id and definition collection
//! (`geometry.cpp`).

use std::collections::BTreeMap;

use super::bytes::{parse_flat, parse_varint, read_f64, read_i32, read_u32, u32_at};
use super::{
    hex_upper, text, GeometryBuilder, RawArcFrame, RawDefinition, RawFace, RawInstance, Result,
    Tag, TlvNode,
};
use crate::model::{
    stringify_attr_dict, CoEdge, ConstructionLine, ConstructionPoint, EntityId, ParsedAttrDict,
    ParsedAttrDictionaries, ParsedAttribute,
};

/// Depth-first search for the first node tagged `t`.
pub(crate) fn find_node<'a>(ns: &'a [TlvNode], t: &str) -> Option<&'a TlvNode> {
    for n in ns {
        if n.tag == t {
            return Some(n);
        }
        if let Some(r) = find_node(&n.children, t) {
            return Some(r);
        }
    }
    None
}

fn find_all<'a>(ns: &'a [TlvNode], t: &str, out: &mut Vec<&'a TlvNode>) {
    for n in ns {
        if n.tag == t {
            out.push(n);
        }
        find_all(&n.children, t, out);
    }
}

fn varint_all(p: &[u8]) -> Result<u64> {
    parse_varint(p, 0, p.len())
}

/// A guide's layer id (as text, `""` when absent) and hidden flag. Guides
/// (6942 / 6C42) wrap the usual drawing-element base in 6842: its D007
/// container holds D307 (display flags, base 0x06, +0x01 hidden) and D207
/// (layer entity id), the same leaves edges/faces/instances carry.
fn guide_drawbase(e: &TlvNode) -> Result<(String, bool)> {
    let hidden = find_node(&e.children, "D307")
        .and_then(|f| f.payload.first())
        .is_some_and(|&flags| flags & 0x01 != 0);
    let layer = match find_node(&e.children, "D207") {
        Some(l) if !l.payload.is_empty() => varint_all(&l.payload)?.to_string(),
        _ => String::new(),
    };
    Ok((layer, hidden))
}

/// The node's entity id: a direct DE05 child, a DC05 child wrapping a DE05,
/// or (failing both) the first one found deeper down.
pub(crate) fn entity_id(n: &TlvNode) -> Result<Option<EntityId>> {
    for c in &n.children {
        if c.tag == "DE05" && !c.payload.is_empty() {
            return Ok(Some(varint_all(&c.payload)? as EntityId));
        }
        if c.tag == "DC05" && c.payload.len() >= 6 && c.payload[0] == 0xde && c.payload[1] == 5 {
            let z = read_u32(&c.payload, 2)? as usize;
            if z <= c.payload.len() - 6 {
                return Ok(Some(parse_varint(&c.payload, 6, z)? as EntityId));
            }
        }
    }
    for c in &n.children {
        if let Some(v) = entity_id(c)? {
            return Ok(Some(v));
        }
    }
    Ok(None)
}

fn dc_id(p: &[u8]) -> Result<EntityId> {
    if p.len() >= 6 && p[0] == 0xde && p[1] == 5 {
        let z = read_u32(p, 2)? as usize;
        if z <= p.len() - 6 {
            return Ok(parse_varint(p, 6, z)? as EntityId);
        }
    }
    Ok(varint_all(p)? as EntityId)
}

fn flat_find<'a>(s: &[(Tag, &'a [u8])], t: &str) -> Option<&'a [u8]> {
    s.iter().find(|(tag, _)| *tag == t).map(|(_, p)| *p)
}

type UvPair = (Option<[f64; 9]>, Option<[f64; 9]>);

/// Front/back UV transforms from a face's DC05 payload:
/// DD05 > B136 > B236 > 1027 > (1127 front | 1227 back) > 1327 > 1527 (9 f64).
fn uv(p: &[u8]) -> UvPair {
    let chain = || -> Option<&[u8]> {
        let mut q = flat_find(&parse_flat(p), "DD05")?;
        for t in ["B136", "B236", "1027"] {
            q = flat_find(&parse_flat(q), t)?;
        }
        Some(q)
    };
    let Some(sides) = chain() else {
        return (None, None);
    };
    let sides = parse_flat(sides);
    let side = |t: &str| -> Option<[f64; 9]> {
        let x = flat_find(&sides, t)?;
        let x = flat_find(&parse_flat(x), "1327")?;
        let x = flat_find(&parse_flat(x), "1527")?;
        if x.len() != 72 {
            return None;
        }
        let mut m = [0.0; 9];
        for (i, v) in m.iter_mut().enumerate() {
            *v = read_f64(x, i * 8).ok()?;
        }
        Some(m)
    };
    (side("1127"), side("1227"))
}

/// (tag, payload start, payload end) for one flat level of `p[a..z]`, without
/// recursing - used to walk a dictionary's siblings (its entries container is
/// a SIBLING of its B436 name node, not a child).
fn parse_flat_spans(p: &[u8], mut a: usize, z: usize) -> Vec<(Tag, usize, usize)> {
    let mut out = Vec::new();
    while a + 6 <= z {
        let n = u32_at(p, a + 2) as usize;
        if n > z - a - 6 {
            break;
        }
        out.push((Tag::from_bytes(p[a], p[a + 1]), a + 6, a + 6 + n));
        a += 6 + n;
    }
    out
}

// Tags worth descending into when walking attribute data (mirrors Python's
// _PROP_CONTAINER_TAGS exactly) - anything else's payload is raw data, never
// more nested TLV structure.
fn is_prop_container_tag(t: &Tag) -> bool {
    [
        "DD05", "B536", "B136", "B236", "B336", "B036", "A438", "AE38",
    ]
    .iter()
    .any(|c| t == c)
}

// A438 wraps exactly one attribute value: its payload holds a single nested
// span whose OWN tag says the real type (AD38 string, A938 Float / AF38
// Length double, A738 int32, B438/B538 a 3xf64 point/vector, AE38 a nested
// array, AA38 a one-byte boolean - what a SketchUp 2024 file stores for
// `set_attribute(..., true)`). An A438 with no children or an unrecognized
// value tag is Null - a real, present key, not a missing key.
fn decode_a438_value(p: &[u8], a: usize, z: usize, depth: usize) -> ParsedAttribute {
    if depth > super::bytes::MAX_TLV_DEPTH {
        return ParsedAttribute::Null;
    }
    let spans = parse_flat_spans(p, a, z);
    let Some(&(tag, sa, sz)) = spans.first() else {
        return ParsedAttribute::Null;
    };
    let n = sz - sa;
    let f = |o: usize| read_f64(p, o).unwrap_or(0.0);
    if tag == "AD38" {
        return ParsedAttribute::String(text(&p[sa..sz]));
    }
    if tag == "AA38" && n == 1 {
        return ParsedAttribute::Boolean(p[sa] != 0);
    }
    if tag == "AF38" && n == 8 {
        return ParsedAttribute::Length(f(sa));
    }
    if tag == "A938" && n == 8 {
        return ParsedAttribute::Float(f(sa));
    }
    if tag == "A738" && n == 4 {
        return ParsedAttribute::Integer(i64::from(read_i32(p, sa).unwrap_or(0)));
    }
    if tag == "B438" && n == 24 {
        return ParsedAttribute::Point3d([f(sa), f(sa + 8), f(sa + 16)]);
    }
    if tag == "B538" && n == 24 {
        return ParsedAttribute::Vector3d([f(sa), f(sa + 8), f(sa + 16)]);
    }
    if tag == "AE38" {
        let items = parse_flat_spans(p, sa, sz)
            .into_iter()
            .filter(|(t, _, _)| *t == "A438")
            .map(|(_, ca, cz)| decode_a438_value(p, ca, cz, depth + 1))
            .collect();
        return ParsedAttribute::Array(items);
    }
    ParsedAttribute::Null
}

fn extract_entries(p: &[u8], a: usize, z: usize, entries: &mut ParsedAttrDict, depth: usize) {
    if depth > super::bytes::MAX_TLV_DEPTH {
        return;
    }
    let mut key = String::new();
    for (tag, sa, sz) in parse_flat_spans(p, a, z) {
        if tag == "B636" {
            key = text(&p[sa..sz]);
        } else if tag == "A438" && !key.is_empty() {
            entries.insert(
                std::mem::take(&mut key),
                decode_a438_value(p, sa, sz, depth + 1),
            );
        } else if is_prop_container_tag(&tag) {
            extract_entries(p, sa, sz, entries, depth + 1);
        }
    }
}

fn walk_dicts(p: &[u8], a: usize, z: usize, out: &mut ParsedAttrDictionaries, depth: usize) {
    if depth > super::bytes::MAX_TLV_DEPTH {
        return;
    }
    let here = parse_flat_spans(p, a, z);
    for (i, &(tag, sa, sz)) in here.iter().enumerate() {
        if tag == "B436" {
            let name = text(&p[sa..sz]);
            let mut entries = ParsedAttrDict::new();
            if let Some(&(_, ea, ez)) = here.get(i + 1) {
                extract_entries(p, ea, ez, &mut entries, depth + 1);
            }
            out.insert(name, entries);
        } else if is_prop_container_tag(&tag) {
            walk_dicts(p, sa, sz, out, depth + 1);
        }
    }
}

/// Every attribute dictionary in a DC05 payload, keyed by the dictionary's own
/// declared name (B436). Each dictionary is a B436 name node immediately
/// followed by a sibling entries container (typically B536) holding B636 key /
/// A438 value pairs; B436 can occur at any depth reachable through container
/// tags, so both walks recurse into every container-tagged node.
pub fn extract_attribute_dictionaries(p: &[u8], out: &mut ParsedAttrDictionaries) {
    walk_dicts(p, 0, p.len(), out, 0);
}

fn vec3_at(p: &[u8], o: usize) -> Result<[f64; 3]> {
    Ok([read_f64(p, o)?, read_f64(p, o + 8)?, read_f64(p, o + 16)?])
}

/// Collects vertices, edges, curves, faces, instances and construction
/// geometry from a list of nodes into `b`, descending into any other node with
/// children.
pub fn collect_geometry(es: &[TlvNode], b: &mut GeometryBuilder) -> Result<()> {
    for e in es {
        let tag = e.tag;
        if tag == "C409" {
            let id = entity_id(e)?;
            if let (Some(id), Some(p)) = (id, find_node(&e.children, "C509")) {
                if p.payload.len() >= 24 {
                    b.vertices.insert(id, vec3_at(&p.payload, 0)?);
                }
            }
        } else if tag == "B80B" {
            let Some(id) = entity_id(e)? else { continue };
            let x = find_node(&e.children, "B90B");
            let y = find_node(&e.children, "BA0B");
            let mut a = None;
            let mut c = None;
            if let Some(x) = x.filter(|x| !x.payload.is_empty()) {
                a = Some(varint_all(&x.payload)? as EntityId);
            }
            if let Some(y) = y.filter(|y| !y.payload.is_empty()) {
                c = Some(varint_all(&y.payload)? as EntityId);
            }
            b.edges.insert(id, (a, c));
            for z in e.children.iter().filter(|z| z.tag == "D007") {
                for w in &z.children {
                    if w.tag == "D307" && !w.payload.is_empty() {
                        b.edge_flags.insert(id, i32::from(w.payload[0]));
                    } else if w.tag == "D207" && !w.payload.is_empty() {
                        // D207 = layer id, the same tag faces and instances
                        // carry (Python port's edge_layers).
                        b.edge_layers
                            .insert(id, varint_all(&w.payload)? as EntityId);
                    }
                }
            }
            // BB0B = the parent Curve's entity id (Edge#curve).
            if let Some(bb) = find_node(&e.children, "BB0B") {
                if !bb.payload.is_empty() {
                    let curve_id = varint_all(&bb.payload)?;
                    if curve_id != 0 {
                        b.edge_curves.insert(id, curve_id as EntityId);
                    }
                }
            }
        } else if tag == "2C4C" {
            // A Curve (384A: its entity id and edge count) and, when the curve
            // is an arc, 2D4C: 16 doubles - center, normal, the plane
            // constant, x axis, y axis, radius, start angle, end angle.
            let curve = find_node(&e.children, "384A");
            let arc = find_node(&e.children, "2D4C");
            if let (Some(curve), Some(arc)) = (curve, arc) {
                if arc.payload.len() == 128 {
                    if let Some(id) = entity_id(curve)? {
                        let f = |i: usize| read_f64(&arc.payload, i * 8).unwrap_or(0.0);
                        b.arc_curves.insert(
                            id,
                            RawArcFrame {
                                center: [f(0), f(1), f(2)],
                                normal: [f(3), f(4), f(5)],
                                x_axis: [f(7), f(8), f(9)],
                                y_axis: [f(10), f(11), f(12)],
                                start_angle: f(14),
                                end_angle: f(15),
                            },
                        );
                    }
                }
            }
        } else if tag == "AC0D" {
            let Some(id) = entity_id(e)? else { continue };
            let mut f = RawFace::default();
            if let Some(n) = find_node(&e.children, "AD0D") {
                if n.payload.len() >= 24 {
                    f.normal = vec3_at(&n.payload, 0)?;
                }
            }
            if let Some(l) = find_node(&e.children, "AE0D") {
                let mut loops = Vec::new();
                find_all(&l.children, "9411", &mut loops);
                for ln in loops {
                    let mut co = Vec::new();
                    let mut cs = Vec::new();
                    find_all(&ln.children, "A00F", &mut cs);
                    for cn in cs {
                        let mut eid = None;
                        let mut ori = None;
                        for (t, v) in parse_flat(&cn.payload) {
                            if t == "A10F" {
                                eid = Some(varint_all(v)? as EntityId);
                            } else if t == "A20F" {
                                let raw_orientation = varint_all(v)?;
                                ori = Some(if raw_orientation == 0 { 1 } else { -1 });
                            }
                        }
                        if let (Some(edge_id), Some(orientation)) = (eid, ori) {
                            co.push(CoEdge {
                                edge_id,
                                orientation,
                            });
                        }
                    }
                    if !co.is_empty() {
                        f.loops.push(co);
                    }
                }
            }
            for d in e.children.iter().filter(|d| d.tag == "D007") {
                for x in &d.children {
                    if x.tag == "D107" && !x.payload.is_empty() {
                        f.material_id = Some(varint_all(&x.payload)? as EntityId);
                    } else if x.tag == "DC05" {
                        (f.uv_transform, f.uv_transform_back) = uv(&x.payload);
                    } else if x.tag == "D307" && !x.payload.is_empty() {
                        // D307 = display flags, same record edges read (base
                        // 0x06, +0x01 hidden).
                        f.hidden = x.payload[0] & 0x01 != 0;
                    } else if x.tag == "D207" && !x.payload.is_empty() {
                        let layer = varint_all(&x.payload)?;
                        f.layer = layer.to_string();
                        f.layer_id = Some(layer as EntityId);
                    }
                }
            }
            for x in e
                .children
                .iter()
                .filter(|x| x.tag == "AF0D" && !x.payload.is_empty())
            {
                f.back_material_id = Some(varint_all(&x.payload)? as EntityId);
            }
            b.faces.insert(id, f);
        } else if tag == "6419" {
            let mut i = RawInstance {
                offset: e.offset,
                id: entity_id(e)?,
                ..Default::default()
            };
            if let Some(g) = find_node(&e.children, "6819") {
                if g.payload.len() == 16 {
                    i.ref_guid = hex_upper(&g.payload);
                }
            }
            if let Some(r) = find_node(&e.children, "6719") {
                if !r.payload.is_empty() {
                    i.ref_idx = Some(varint_all(&r.payload)? as EntityId);
                }
            }
            if let Some(n) = find_node(&e.children, "6519") {
                i.name = text(&n.payload);
            }
            if let Some(m) = find_node(&e.children, "6619") {
                if m.payload.len() >= 104 {
                    for q in 0..13 {
                        i.matrix.push(read_f64(&m.payload, q * 8)?);
                    }
                }
            }
            for d in e.children.iter().filter(|d| d.tag == "D007") {
                for x in &d.children {
                    if x.tag == "D107" && !x.payload.is_empty() {
                        i.material_id = Some(varint_all(&x.payload)? as EntityId);
                    } else if x.tag == "D207" && !x.payload.is_empty() {
                        i.layer = varint_all(&x.payload)?.to_string();
                    } else if x.tag == "DC05" {
                        let mut all_dicts = ParsedAttrDictionaries::new();
                        extract_attribute_dictionaries(&x.payload, &mut all_dicts);
                        for (dict_name, entries) in all_dicts {
                            if dict_name == "dynamic_attributes" {
                                i.properties = stringify_attr_dict(&entries);
                            }
                            i.attribute_dicts.insert(dict_name, entries);
                        }
                    } else if x.tag == "D307" && !x.payload.is_empty() {
                        // D307 = display flags (base 0x06, +0x01 hidden).
                        i.hidden = x.payload[0] & 0x01 != 0;
                    }
                }
            }
            b.instances.push(i);
        } else if tag == "6942" {
            // VFF CConstructionLine (list 9113). Same 8-double body as legacy:
            // point + unit direction + start/end params; |param| >= 1e20 is
            // unbounded.
            if let Some(p) = find_node(&e.children, "6A42") {
                if p.payload.len() >= 64 {
                    let point = vec3_at(&p.payload, 0)?;
                    let direction = vec3_at(&p.payload, 24)?;
                    const HUGE_PARAM: f64 = 1e20;
                    let start_param = read_f64(&p.payload, 48)?;
                    let end_param = read_f64(&p.payload, 56)?;
                    let at = |t: f64| {
                        [
                            point[0] + direction[0] * t,
                            point[1] + direction[1] * t,
                            point[2] + direction[2] * t,
                        ]
                    };
                    let (layer, hidden) = guide_drawbase(e)?;
                    b.construction_lines.push(ConstructionLine {
                        point,
                        direction,
                        start: (start_param.abs() < HUGE_PARAM).then(|| at(start_param)),
                        end: (end_param.abs() < HUGE_PARAM).then(|| at(end_param)),
                        layer,
                        hidden,
                    });
                }
            }
        } else if tag == "6C42" {
            // VFF CConstructionPoint (list 9213): 6D42 position, 6E42 a second
            // 3xf64 block (often zeros, not decoded), 6F42 u8.
            if let Some(p) = find_node(&e.children, "6D42") {
                if p.payload.len() >= 24 {
                    let (layer, hidden) = guide_drawbase(e)?;
                    b.construction_points.push(ConstructionPoint {
                        position: vec3_at(&p.payload, 0)?,
                        layer,
                        hidden,
                    });
                }
            }
        } else if !e.children.is_empty() {
            collect_geometry(&e.children, b)?;
        }
    }
    Ok(())
}

/// Layer ids -> names and visibility from every 993A layer-manager list.
pub fn collect_layers(
    ns: &[TlvNode],
    out: &mut BTreeMap<EntityId, String>,
    hidden: &mut BTreeMap<String, bool>,
) -> Result<()> {
    for e in ns {
        if e.tag == "993A" {
            for c in e.children.iter().filter(|c| c.tag == "8C3C") {
                let d = find_node(&c.children, "DC05");
                let n = find_node(&c.children, "8D3C");
                if let (Some(d), Some(n)) = (d, n) {
                    if !d.payload.is_empty() {
                        out.insert(dc_id(&d.payload)?, text(&n.payload));
                    }
                }
                // 8E3C: a single byte, 1 = hidden / 0 = visible - the VFF
                // counterpart of the legacy layer-hidden flag, confirmed
                // against a real file's Tags panel.
                if let Some(n) = n {
                    if let Some(h) = find_node(&c.children, "8E3C") {
                        if !h.payload.is_empty() {
                            hidden.insert(text(&n.payload), h.payload[0] == 1);
                        }
                    }
                }
            }
        }
        collect_layers(&e.children, out, hidden)?;
    }
    Ok(())
}

/// The model record (F601) carries the model entity's own attributes the same
/// way an instance does: an 8813 entity base holding a D007 container whose
/// DC05 payload lists the named dictionaries.
pub fn collect_model_attribute_dictionaries(model: &TlvNode, out: &mut ParsedAttrDictionaries) {
    let Some(entity) = find_node(&model.children, "8813") else {
        return;
    };
    for d in entity.children.iter().filter(|d| d.tag == "D007") {
        for x in d.children.iter().filter(|x| x.tag == "DC05") {
            extract_attribute_dictionaries(&x.payload, out);
        }
    }
}

/// Material ids -> names (C832 records: DC05 id, CC32 name).
pub fn collect_material_ids(ns: &[TlvNode], out: &mut BTreeMap<EntityId, String>) -> Result<()> {
    for e in ns {
        if e.tag == "C832" {
            let d = find_node(&e.children, "DC05");
            let n = find_node(&e.children, "CC32");
            if let (Some(d), Some(n)) = (d, n) {
                if !d.payload.is_empty() {
                    out.insert(dc_id(&d.payload)?, text(&n.payload));
                }
            }
        }
        collect_material_ids(&e.children, out)?;
    }
    Ok(())
}

/// Component/group definitions (7C15 records) with their geometry.
pub fn collect_definitions(
    ns: &[TlvNode],
    out: &mut BTreeMap<EntityId, RawDefinition>,
) -> Result<()> {
    for e in ns {
        if e.tag == "7C15" {
            let mut d = RawDefinition::default();
            for c in &e.children {
                if c.tag == "7D15" && c.payload.len() == 16 {
                    d.guid = hex_upper(&c.payload);
                } else if c.tag == "7E15" {
                    d.name = text(&c.payload);
                } else if c.tag == "8315" && !c.payload.is_empty() {
                    // Definition kind: 0 component, 1 group (what SketchUp's
                    // own API reports too), 2 the quad backing an Image.
                    let kind = varint_all(&c.payload)?;
                    d.is_image = kind == 2;
                    d.is_group = kind == 1;
                } else if c.tag == "581B" {
                    for (t, v) in parse_flat(&c.payload) {
                        if t == "5D1B" && !v.is_empty() {
                            d.always_faces_camera = varint_all(v)? == 1;
                        } else if t == "5E1B" && !v.is_empty() {
                            d.shadows_face_sun = varint_all(v)? == 1;
                        }
                    }
                }
            }
            collect_geometry(&e.children, &mut d.builder)?;
            if let Some(id) = entity_id(e)? {
                out.insert(id, d);
            }
        }
        collect_definitions(&e.children, out)?;
    }
    Ok(())
}
