//! Legacy (pre-2021 MFC `CArchive`) reader: SketchUp 3 through 2020
//! (`legacy.cpp`).
//!
//! A classic .skp is one MFC object graph: every object is written in full
//! the first time anything points at it and back-referenced by its store-map
//! slot afterwards. The walk starts at the material manager (or, with no
//! materials, at the layer list), recovers the archive's absolute slot base,
//! then reads materials, layers, component definitions and the root entity
//! list in file order. Scenes are read from the document tail afterwards.
//!
//! Beyond the C++ reader this port also surfaces what the Python reader does
//! (`legacy.py`): each edge's own layer ([`GeometryBuilder::edge_layers`]),
//! each face's raw layer id ([`RawFace::layer_id`]) and attribute values with
//! their full SketchUp type (`Time`, `Length`, `Point3d`, `Vector3d`, nested
//! `Array`).

mod archive;
mod pages;
mod reader;

use std::collections::{HashMap, HashSet};
use std::rc::Rc;
use std::sync::Arc;

use crate::errors::SkpParseError;
use crate::internal::{
    extract_version, read_u16, read_u32, Error, GeometryBuilder, RawArcFrame, RawDefinition,
    RawFace, RawInstance, RawMaterial, RawParsed, RawTexture, Result,
};
use crate::model::{
    stringify_attr_dict, stringify_attr_dictionaries, CoEdge, ConstructionLine, ConstructionPoint,
    Dimension, EntityId, ParsedAttrDict, ParsedAttrDictionaries, ParsedAttribute, SectionPlane,
    TextEntity, Vec3,
};
use crate::observability::{emit_log, emit_progress, LogLevel, ParseOptions, ParseStage};

pub use self::pages::scan_pages_for_layers;

use self::archive::{
    bootstrap_two_materials, probe_layer_anchor_bases, walk_with_bases, Archive, WalkResult,
};
use self::reader::{header_major, R};

// The trailing GUID looked plausible as a class-schema gate at first
// (CComponentInstance schema >= 5, CGroup schema >= 1), but it doesn't
// generalize: a v7 file's CComponentInstance reports schema 6, well above
// that threshold, yet has no GUID; CGroup reports schema 1 on every version
// tested (v3 through 2025), so a schema gate is always true for it and would
// force the read even on pre-2014 files. Forcing the 16-byte read anyway
// silently eats bytes belonging to the next sibling's own tag - the exact bug
// openskp#284/#310 root-caused in the Python reader. The file's own version
// number is the only signal that actually holds (matches Python's legacy.py
// `_read_instance`; openskp#391).
pub fn legacy_instance_has_guid(ver: i32) -> bool {
    ver >= 14
}

// SketchUp 3 (header "{3.0.x}") predates several fields every later file
// carries. The differences are gated on `ver < FIRST_V4` and were calibrated
// byte-by-byte on one real SketchUp-written V3 file (a synthetic model saved
// down through SketchUp's own version export) - not on a corpus - so they are
// exactly what that file proves and no more (openskp#284, #407; matches
// Python's legacy.py `_FIRST_V4`). SketchUp 4 and 5 have layout differences of
// their own (openskp#284): see FIRST_DIB_OBJECT, FIRST_NAMED_INSTANCE and the
// version checks in draw(), the CCamera/CThumbnail/CDimensionLinear/
// CComponentDefinition readers.
pub(crate) const FIRST_V4: i32 = 4;

// The texture image became a first-class CDib object (store-map slot, entity
// preamble) in SketchUp 6. SketchUp 3, 4 and 5 write it as a bare inline
// subtype/length/bytes record straight after the material's flag word.
pub(crate) const FIRST_DIB_OBJECT: i32 = 6;

// First version whose instances and groups carry a name string (SketchUp 3
// and 4 files have none; where the name is optional the reader checks for
// one).
pub(crate) const FIRST_NAMED_INSTANCE: i32 = 5;

// ── decoded objects ──────────────────────────────────────────────────────

/// Which entity a decoded [`LegacyValue`] is (C++ `V::k`).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Default)]
pub enum LegacyKind {
    /// An object this port reads only to stay aligned (camera, thumbnail,
    /// font).
    #[default]
    None,
    Vertex,
    Edge,
    Curve,
    ArcCurve,
    EdgeUse,
    Loop,
    Face,
    Attrs,
    Dict,
    Layer,
    Material,
    Dib,
    Ftc,
    Image,
    Relationship,
    ConstructionLine,
    ConstructionPoint,
    SectionPlane,
    Dimension,
    Text,
    Definition,
    Instance,
}

/// One decoded entity of the MFC object graph (C++ `V`): material, layer,
/// component, edge, face, ... - `k` says which, and only the fields that kind
/// uses are filled.
/// A legacy dimension's decoded geometry, and the raw refs of its connection
/// points, resolved to vertex positions once the whole archive is read.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct LegacyDimension {
    pub geometry: Dimension,
    pub connect1: Option<u64>,
    pub connect2: Option<u64>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct LegacyValue {
    pub k: LegacyKind,
    pub name: String,
    pub label: String,
    pub text: String,
    /// Dimension only (boxed, so mesh vertices do not carry it).
    pub dimension: Option<Box<LegacyDimension>>,
    pub guid: String,
    pub xyz: Vec3,
    /// Construction line only: its normalized direction, and its bounded
    /// segment's start/end (unset when unbounded in that direction).
    pub direction: Vec3,
    pub start: Option<Vec3>,
    pub end: Option<Vec3>,
    pub plane: Vec<f64>,
    pub xf: Vec<f64>,
    pub uvf: Vec<f64>,
    pub uvb: Vec<f64>,
    pub front_projected: bool,
    pub back_projected: bool,
    pub v1: u64,
    pub v2: u64,
    /// Edge only: the slot of its parent Curve, and the curve itself on the
    /// edge that first points at it (a back-ref to a curve already read
    /// carries none).
    pub curve: Option<u64>,
    pub curve_value: Option<Rc<LegacyValue>>,
    /// Arc curve only.
    pub arc: RawArcFrame,
    pub edge: u64,
    pub def: u64,
    /// Slot of this entity's CAttributeContainer, or `None` when it has none
    /// (slot 0 is a legitimate slot, so it cannot double as "absent").
    pub attrs: Option<u64>,
    /// Dictionary (CAttributeNamed) only: its own typed key/value pairs.
    pub entries: ParsedAttrDict,
    pub tex_dib: u64,
    /// SketchUp 3-5: the texture image sits inline (bytes in `blob`), no
    /// store slot.
    pub inline_tex: bool,
    pub sense: bool,
    pub faces_camera: bool,
    pub shadows_face_sun: bool,
    pub is_group: bool,
    pub colorized: bool,
    pub colorize_type: i32,
    pub mat: i32,
    pub back_mat: i32,
    pub layer: i32,
    pub hidden: i32,
    pub soft: i32,
    pub smooth: i32,
    pub r: i32,
    pub g: i32,
    pub b: i32,
    pub a: i32,
    pub opacity: f64,
    pub tw: f64,
    pub th: f64,
    pub tex_file: String,
    pub blob: Vec<u8>,
    pub loops: Vec<Rc<LegacyValue>>,
    pub uses: Vec<Rc<LegacyValue>>,
    pub ents: Vec<LegacyObject>,
}

impl Default for LegacyValue {
    fn default() -> Self {
        Self {
            k: LegacyKind::None,
            name: String::new(),
            label: String::new(),
            text: String::new(),
            dimension: None,
            guid: String::new(),
            xyz: [0.0; 3],
            direction: [0.0; 3],
            start: None,
            end: None,
            plane: Vec::new(),
            xf: Vec::new(),
            uvf: Vec::new(),
            uvb: Vec::new(),
            front_projected: false,
            back_projected: false,
            v1: 0,
            v2: 0,
            curve: None,
            curve_value: None,
            arc: RawArcFrame::default(),
            edge: 0,
            def: 0,
            attrs: None,
            entries: ParsedAttrDict::new(),
            tex_dib: 0,
            inline_tex: false,
            sense: false,
            faces_camera: false,
            shadows_face_sun: false,
            is_group: false,
            colorized: false,
            colorize_type: 0,
            mat: 0,
            back_mat: 0,
            layer: 0,
            hidden: 0,
            soft: 0,
            smooth: 0,
            r: 128,
            g: 128,
            b: 128,
            a: 255,
            opacity: 0.0,
            tw: 0.0,
            th: 0.0,
            tex_file: String::new(),
            blob: Vec::new(),
            loops: Vec::new(),
            uses: Vec::new(),
            ents: Vec::new(),
        }
    }
}

/// One object read off the archive (C++ `tuple<slot, class name, V>`): a null
/// reference has an empty `name`; a back-ref into the unwalked pre-model
/// region is named `"premodel"`, one into a burned connection-point band
/// `"reserved"`.
#[derive(Debug, Clone, PartialEq)]
pub struct LegacyObject {
    pub slot: u64,
    pub name: Rc<str>,
    pub v: Option<Rc<LegacyValue>>,
}

impl LegacyObject {
    pub(crate) fn null() -> Self {
        LegacyObject {
            slot: 0,
            name: Rc::from(""),
            v: None,
        }
    }
}

/// One legacy MFC archive slot-table entry: either a class declaration
/// (`cls`, `name`/`schema` identify the class, `v` unused) or a read object
/// (`v` holds its decoded value, possibly `None` for object kinds this port
/// doesn't decode). Public so [`scan_pages_for_layers`] is unit-testable
/// without the full archive type.
#[derive(Debug, Clone, PartialEq)]
pub struct LegacySlotEntry {
    pub cls: bool,
    pub name: Rc<str>,
    pub schema: i32,
    pub v: Option<Rc<LegacyValue>>,
}

impl Default for LegacySlotEntry {
    fn default() -> Self {
        LegacySlotEntry {
            cls: false,
            name: Rc::from(""),
            schema: 0,
            v: None,
        }
    }
}

/// A minimal view into an archive's slot-table bookkeeping - the only pieces
/// the page (scene) scanner needs. The real reader wraps its own archive's
/// tables; a test can hand-build just the entries a case needs.
pub struct LegacySlotTable<'a> {
    pub slots: &'a mut HashMap<u64, LegacySlotEntry>,
    pub class_slot: &'a mut HashMap<String, u64>,
    pub next: &'a mut u64,
}

impl LegacySlotTable<'_> {
    pub fn alloc(&mut self, e: LegacySlotEntry) -> u64 {
        let s = *self.next;
        *self.next += 1;
        self.slots.insert(s, e);
        s
    }
}

// ── dimension connection paths ───────────────────────────────────────────

// One entity-reference tag at `q`, advancing it: null, a plain 2-byte
// back-reference, or the 0x7fff big-tag escape. A "new object" form is an
// error here.
fn read_plain_entity_ref(d: &[u8], q: &mut usize) -> Result<Option<u64>> {
    if *q + 2 > d.len() {
        return Err(Error::runtime("legacy archive truncated"));
    }
    let tag = read_u16(d, *q)?;
    *q += 2;
    if tag == 0 {
        return Ok(None);
    }
    if tag == 0x7fff {
        if *q + 4 > d.len() {
            return Err(Error::runtime("legacy archive truncated"));
        }
        let big = read_u32(d, *q)?;
        *q += 4;
        if big & 0x8000_0000 != 0 {
            return Err(Error::runtime("entity ref is a new object"));
        }
        return Ok(Some(u64::from(big)));
    }
    if tag == 0xffff || tag & 0x8000 != 0 {
        return Err(Error::runtime("entity ref is a new object"));
    }
    Ok(Some(u64::from(tag)))
}

/// What follows a legacy linear dimension's connection ref (openskp#384 /
/// #412): `[entity ref][u32 n1][n1 refs][u32 n2][n2 refs]` - the instance
/// paths of the anchored entity.
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct LegacyPathResult {
    pub extra: Option<u64>,
    pub first: Vec<Option<u64>>,
    pub second: Vec<Option<u64>>,
    /// Whether each entry of `first` / `second` was an object written in full.
    pub first_new: Vec<bool>,
    pub second_new: Vec<bool>,
    pub next: usize,
}

/// Whether the text whose font ends at `pos` is anchored to an entity: after
/// the two screen-fraction f64s comes the connection block, `[u32 type][u32 4]
/// [point3d]`, and a type other than 0 or 1 (2, 4, 5 and 7 seen; see
/// docs/dimension-record-notes.md) anchors the label. Matches Python's
/// `_anchored_connection`.
pub(crate) fn anchored_text_connection(d: &[u8], pos: usize) -> bool {
    if pos + 24 > d.len() || read_u32(d, pos + 20).ok() != Some(4) {
        return false;
    }
    read_u32(d, pos + 16).is_ok_and(|t| (2..=15).contains(&t))
}

/// A legacy definition's entity list, read by [`read_legacy_entity_list`]:
/// each item's slot and class name, and the offset just past the list.
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct LegacyEntityListResult {
    pub items: Vec<(u64, String)>,
    pub next: usize,
}

/// Reads a legacy definition's entity list of `count` items at `pos`, with an
/// archive of SketchUp version `ver` over `slots`. Exposed, like
/// [`scan_pages_for_layers`], so the list's bookkeeping is unit-testable: an
/// instance a text label's connection path writes in full is one of the
/// list's items whether or not the list names it again.
pub fn read_legacy_entity_list(
    data: &[u8],
    pos: usize,
    ver: i32,
    count: u32,
    table: LegacySlotTable,
) -> Result<LegacyEntityListResult> {
    let mut ar = Archive::new(data, ver);
    ar.r.p = pos;
    ar.slots = std::mem::take(table.slots);
    ar.class_slot = std::mem::take(table.class_slot);
    ar.next = *table.next;
    let res = ar.entity_list(count, false);
    *table.slots = std::mem::take(&mut ar.slots);
    *table.class_slot = std::mem::take(&mut ar.class_slot);
    *table.next = ar.next;
    let items = res?
        .into_iter()
        .map(|o| (o.slot, o.name.to_string()))
        .collect();
    Ok(LegacyEntityListResult {
        items,
        next: ar.r.p,
    })
}

/// Reads a dimension connection's instance paths from `pos`.
/// `read_new_object(at)` is called for a path entry MFC wrote in full (the
/// first time anything pointed at it) and returns `(slot, position just past
/// the object)`.
///
/// A dimension on loose geometry has a null ref and two empty lists (ten zero
/// bytes, which the old fixed 42/82-byte blocks skipped); one anchored to a
/// vertex inside nested components carries a ref per component, and a
/// fixed-size read slid off the record by exactly those refs - silently
/// truncating the ROOT entity list after it. Matches Python's
/// `_connection_paths` (openskp#384; found again on a real SketchUp 2017
/// cabinet, #412).
pub fn legacy_connection_paths<F>(
    data: &[u8],
    pos: usize,
    mut read_new_object: F,
) -> Result<LegacyPathResult>
where
    F: FnMut(usize) -> Result<(u64, usize)>,
{
    let mut out = LegacyPathResult::default();
    let mut q = pos;
    out.extra = read_plain_entity_ref(data, &mut q)?;
    for list in 0..2 {
        if q + 4 > data.len() {
            return Err(Error::runtime("legacy archive truncated"));
        }
        let count = read_u32(data, q)?;
        q += 4;
        if count > 1000 {
            return Err(Error::runtime("implausible dimension path length"));
        }
        let (refs, written) = if list == 0 {
            (&mut out.first, &mut out.first_new)
        } else {
            (&mut out.second, &mut out.second_new)
        };
        for _ in 0..count {
            if q + 2 > data.len() {
                return Err(Error::runtime("legacy archive truncated"));
            }
            // MFC writes an object IN FULL the first time anything points at
            // it, so a path entry can be a whole NEW object (a dimension
            // serialized before the group it is anchored in). Read it like any
            // object so it is registered and the stream stays aligned.
            let tag = read_u16(data, q)?;
            let mut is_new = tag == 0xffff || (tag != 0x7fff && tag & 0x8000 != 0);
            if tag == 0x7fff {
                if q + 6 > data.len() {
                    return Err(Error::runtime("legacy archive truncated"));
                }
                is_new = read_u32(data, q + 2)? & 0x8000_0000 != 0;
            }
            if is_new {
                let (slot, next) = read_new_object(q)?;
                refs.push(Some(slot));
                q = next;
            } else {
                refs.push(read_plain_entity_ref(data, &mut q)?);
            }
            written.push(is_new);
        }
    }
    out.next = q;
    Ok(out)
}

// ── the SketchUp 2020 filler ─────────────────────────────────────────────

// Widest zero padding seen between the v20 filler's empty string and the
// count that follows it (9 and 13 bytes occur in real files; the ceiling
// leaves room without letting the probe wander into unrelated records).
pub(crate) const MAX_V20_FILLER_PAD: usize = 29;

/// Result of [`find_count_after_v20_filler`]: the recovered count and the
/// offset just past it.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct V20FillerHit {
    pub count: u32,
    pub next: usize,
}

/// Locates the count that follows a v20 filler record, given the offset the
/// bad count was read from. Pure byte logic, exposed for tests.
///
/// SketchUp 2020 (v20) writes an extra, undocumented record ahead of some
/// counts that v17 does not have, which leaves the reader a few bytes early
/// and makes it read garbage as the count. The filler is an empty UTF-16
/// string record followed by zero padding:
///
/// ```text
///   <ff fe ff> <u8 0>        empty string
///   <zero padding>           runs up to the real count
/// ```
///
/// Rather than hard-code an offset (the number of bytes before the marker
/// differs per call site), locate the marker in the short window ahead, then
/// take the first plausible u32 that follows the padding. Only the EMPTY-
/// string form counts as filler: a real string here would mean genuine data,
/// and moving the cursor past it would corrupt the parse.
///
/// Returns `None` when the bytes do not match the filler layout.
pub fn find_count_after_v20_filler(d: &[u8], count_pos: usize, limit: u32) -> Option<V20FillerHit> {
    let marker_at = find_v20_filler_marker(d, count_pos)?;
    // The count sits past a run of zero padding whose length varies per call
    // site (9 and 13 bytes both occur in real files), but always lands at
    // marker_at + 4 + pad with pad % 4 == 1. Step through those candidate
    // offsets and take the first plausible u32.
    //
    // Deliberately NOT "scan forward to the first non-zero byte": a count that
    // is an exact multiple of 256 has a 0x00 low byte, which such a scan
    // cannot tell apart from padding, so it would skip into the count and
    // misalign every later read. Probing whole u32s at 4-byte strides never
    // inspects an individual byte, so those counts round-trip correctly.
    for pad in (1..=MAX_V20_FILLER_PAD).step_by(4) {
        let at = marker_at + 4 + pad;
        if at + 4 > d.len() {
            break;
        }
        let count = u32::from_le_bytes([d[at], d[at + 1], d[at + 2], d[at + 3]]);
        if count > 0 && count <= limit {
            return Some(V20FillerHit {
                count,
                next: at + 4,
            });
        }
    }
    None
}

/// The SketchUp 2020 layout of a model with NO component definitions AND no
/// root entities. [`find_count_after_v20_filler`] cannot see it: behind the
/// filler (an empty string record, then 13 bytes of padding) the only count is
/// the ROOT entity count, and it is 0, which that search skips as "no count".
///
/// Returns the offset of that root count (so the caller can put the cursor
/// there and read the zero), or `None` when the bytes are not that layout: the
/// empty string record has to sit within 12 bytes of `count_pos`, and the 13
/// padding bytes and the count behind it have to be all zero.
pub fn find_empty_root_after_v20_filler(d: &[u8], count_pos: usize) -> Option<usize> {
    let marker_at = find_v20_filler_marker(d, count_pos)?;
    let root_count_at = marker_at + 4 + 13;
    if root_count_at + 4 > d.len() {
        return None;
    }
    d[marker_at + 3..root_count_at + 4]
        .iter()
        .all(|&b| b == 0)
        .then_some(root_count_at)
}

/// The empty-string filler marker in the 12 bytes from `count_pos`, or `None`
/// (also when the string there is not empty: that is real data).
pub(crate) fn find_v20_filler_marker(d: &[u8], count_pos: usize) -> Option<usize> {
    let mut i = count_pos;
    while i + 4 <= d.len() && i < count_pos + 12 {
        if d[i] == 255 && d[i + 1] == 254 && d[i + 2] == 255 {
            return if d[i + 3] != 0 { None } else { Some(i) };
        }
        i += 1;
    }
    None
}

// ── attribute values ─────────────────────────────────────────────────────

/// Decodes one typed `CAttributeNamed` value of type `type_tag` starting at
/// `pos`; returns the value and the offset just past it. Exposed for tests.
pub fn read_legacy_attribute_value(
    data: &[u8],
    pos: usize,
    type_tag: u8,
) -> Result<(ParsedAttribute, usize)> {
    let mut r = R::new(data, pos);
    let v = r.attr_value(type_tag)?;
    Ok((v, r.p))
}

// ── RawParsed assembly ───────────────────────────────────────────────────

fn add_edge(
    b: &mut GeometryBuilder,
    s: u64,
    e: &LegacyValue,
    slots: &HashMap<u64, LegacySlotEntry>,
) {
    let id = s as EntityId;
    if b.edges.contains_key(&id) {
        return;
    }
    for vid in [e.v1, e.v2] {
        if let Some(LegacySlotEntry { v: Some(v), .. }) = slots.get(&vid) {
            if v.k == LegacyKind::Vertex {
                b.vertices.insert(vid as EntityId, v.xyz);
            }
        }
    }
    b.edges
        .insert(id, (Some(e.v1 as EntityId), Some(e.v2 as EntityId)));
    let f = (if e.soft != 0 { 8 } else { 0 })
        | (if e.smooth != 0 { 16 } else { 0 })
        | i32::from(e.hidden != 0);
    if f != 0 {
        b.edge_flags.insert(id, f);
    }
    // The edge's own layer, from the same drawbase a face's layer comes from
    // (Python's `_add_edge`): only a non-default id is stored, so "no layered
    // edge in this file" stays distinguishable from "not read".
    if e.layer != 0 {
        b.edge_layers.insert(id, EntityId::from(e.layer));
    }
    // The edge's parent Curve (the CCurve/CArcCurve slot) and, if that curve is
    // an analytic arc, its frame: it arrives on the edge that first references
    // the CArcCurve; every later edge on the arc back-refs it.
    if let Some(curve) = e.curve {
        b.edge_curves.insert(id, curve as EntityId);
        if let Some(cv) = &e.curve_value {
            if cv.k == LegacyKind::ArcCurve {
                b.arc_curves.insert(curve as EntityId, cv.arc);
            }
        }
    }
}

// SketchUp's Dynamic Components extension stores its data in an attribute
// dictionary literally named "dynamic_attributes"; every named dictionary of
// the entity's CAttributeContainer is kept, keyed by its declared name.
fn extract_legacy_attribute_dictionaries(
    attrs_slot: Option<u64>,
    slots: &HashMap<u64, LegacySlotEntry>,
) -> ParsedAttrDictionaries {
    let mut out = ParsedAttrDictionaries::new();
    let Some(slot) = attrs_slot else { return out };
    let Some(LegacySlotEntry {
        v: Some(container), ..
    }) = slots.get(&slot)
    else {
        return out;
    };
    for ent in &container.ents {
        let Some(ev) = &ent.v else { continue };
        if ev.k != LegacyKind::Dict || ev.name.is_empty() {
            continue;
        }
        out.insert(ev.name.clone(), ev.entries.clone());
    }
    out
}

/// A guide's layer slot as text, resolved to the name later like a face's;
/// `""` for the default layer (slot 0).
fn guide_layer(layer: i32) -> String {
    if layer == 0 {
        String::new()
    } else {
        layer.to_string()
    }
}

fn fill(b: &mut GeometryBuilder, ents: &[LegacyObject], slots: &HashMap<u64, LegacySlotEntry>) {
    for x in ents {
        let s = x.slot;
        let Some(v) = &x.v else { continue };
        match v.k {
            LegacyKind::Edge => add_edge(b, s, v, slots),
            LegacyKind::ArcCurve => {
                b.arc_curves.insert(s as EntityId, v.arc);
            }
            LegacyKind::Face => {
                let mut f = RawFace {
                    normal: [v.plane[0], v.plane[1], v.plane[2]],
                    material_id: (v.mat != 0).then_some(EntityId::from(v.mat)),
                    back_material_id: (v.back_mat != 0).then_some(EntityId::from(v.back_mat)),
                    hidden: v.hidden != 0,
                    // The drawbase's own layer id (Python's `face['layer']`):
                    // always present in a classic record, 0 for the default.
                    layer_id: Some(EntityId::from(v.layer)),
                    ..Default::default()
                };
                for lp in &v.loops {
                    let mut co = Vec::new();
                    for u in &lp.uses {
                        if let Some(LegacySlotEntry { v: Some(ev), .. }) = slots.get(&u.edge) {
                            add_edge(b, u.edge, ev, slots);
                            co.push(CoEdge {
                                edge_id: u.edge as EntityId,
                                orientation: if u.sense { -1 } else { 1 },
                            });
                        }
                    }
                    f.loops.push(co);
                }
                // A positioned/photo-fitted texture mapping
                // (CFaceTextureCoords) lives as a child of this face's
                // attribute container, alongside any CAttributeNamed
                // dictionaries.
                if let Some(LegacySlotEntry {
                    v: Some(container), ..
                }) = v.attrs.and_then(|a| slots.get(&a))
                {
                    for ent in &container.ents {
                        let Some(ev) = &ent.v else { continue };
                        if ev.k != LegacyKind::Ftc {
                            continue;
                        }
                        if let Ok(m) = <[f64; 9]>::try_from(ev.uvf.as_slice()) {
                            f.uv_transform = Some(m);
                        }
                        if let Ok(m) = <[f64; 9]>::try_from(ev.uvb.as_slice()) {
                            f.uv_transform_back = Some(m);
                        }
                        f.uv_projected = ev.front_projected;
                        f.uv_projected_back = ev.back_projected;
                    }
                }
                b.faces.insert(s as EntityId, f);
            }
            LegacyKind::Instance => {
                let dicts = extract_legacy_attribute_dictionaries(v.attrs, slots);
                let properties = dicts
                    .get("dynamic_attributes")
                    .map(stringify_attr_dict)
                    .unwrap_or_default();
                b.instances.push(RawInstance {
                    name: v.name.clone(),
                    ref_idx: Some(v.def as EntityId),
                    matrix: v.xf.clone(),
                    material_id: (v.mat != 0).then_some(EntityId::from(v.mat)),
                    layer: if v.layer != 0 {
                        v.layer.to_string()
                    } else {
                        String::new()
                    },
                    hidden: v.hidden != 0,
                    properties,
                    attribute_dicts: dicts,
                    ..Default::default()
                });
            }
            LegacyKind::Image => {
                // Placed exactly like an ordinary component instance; the
                // definition it points at is flagged is_image.
                b.instances.push(RawInstance {
                    ref_idx: Some(v.def as EntityId),
                    ref_guid: v.guid.clone(),
                    matrix: v.xf.clone(),
                    material_id: (v.mat != 0).then_some(EntityId::from(v.mat)),
                    layer: if v.layer != 0 {
                        v.layer.to_string()
                    } else {
                        String::new()
                    },
                    hidden: v.hidden != 0,
                    ..Default::default()
                });
            }
            LegacyKind::SectionPlane => {
                let mut sp = SectionPlane::default();
                if let Ok(p) = <[f64; 4]>::try_from(v.plane.as_slice()) {
                    sp.plane = p;
                }
                sp.name = v.name.clone();
                sp.label = v.label.clone();
                sp.hidden = v.hidden != 0;
                b.section_planes.push(sp);
            }
            LegacyKind::Text => {
                b.texts.push(TextEntity {
                    text: v.text.clone(),
                    hidden: v.hidden != 0,
                    ..Default::default()
                });
            }
            LegacyKind::Dimension => {
                let decoded = v.dimension.as_deref().cloned().unwrap_or_default();
                let mut dim = decoded.geometry;
                // A connection point stored as a vertex ref (possibly forward)
                // resolves now that the whole archive is read.
                let resolve = |point: &mut Option<Vec3>, r: Option<u64>| {
                    if point.is_some() {
                        return;
                    }
                    if let Some(LegacySlotEntry { v: Some(pv), .. }) = r.and_then(|r| slots.get(&r))
                    {
                        if pv.k == LegacyKind::Vertex {
                            *point = Some(pv.xyz);
                        }
                    }
                };
                resolve(&mut dim.a, decoded.connect1);
                resolve(&mut dim.b, decoded.connect2);
                dim.text = v.text.clone();
                dim.hidden = v.hidden != 0;
                b.dimensions.push(dim);
            }
            LegacyKind::ConstructionLine => {
                b.construction_lines.push(ConstructionLine {
                    point: v.xyz,
                    direction: v.direction,
                    start: v.start,
                    end: v.end,
                    layer: guide_layer(v.layer),
                    hidden: v.hidden != 0,
                });
            }
            LegacyKind::ConstructionPoint => {
                b.construction_points.push(ConstructionPoint {
                    position: v.xyz,
                    layer: guide_layer(v.layer),
                    hidden: v.hidden != 0,
                });
            }
            _ => {}
        }
    }
}

fn material(v: &LegacyValue, slots: &HashMap<u64, LegacySlotEntry>) -> RawMaterial {
    let mut x = RawMaterial {
        name: v.name.clone(),
        r: v.r,
        g: v.g,
        b: v.b,
        a: v.a,
        transparency: (1.0 - v.opacity).clamp(0.0, 1.0),
        colorized: v.colorized,
        colorize_type: v.colorize_type,
        texture: None,
    };
    if v.tex_dib != 0 || v.inline_tex {
        let mut t = RawTexture {
            filename: v.tex_file.clone(),
            x_scale: v.tw,
            y_scale: v.th,
            data: None,
        };
        if v.inline_tex {
            t.data = Some(v.blob.clone());
        } else if let Some(LegacySlotEntry { v: Some(dib), .. }) = slots.get(&v.tex_dib) {
            t.data = Some(dib.blob.clone());
        }
        if t.filename.is_empty() {
            let png = t
                .data
                .as_ref()
                .is_some_and(|d| d.len() >= 4 && d[0] == 0x89);
            t.filename = format!("{}{}", v.name, if png { ".png" } else { ".jpg" });
        }
        x.texture = Some(t);
    }
    x
}

fn walk(data: &[u8]) -> Result<WalkResult<'_>> {
    let ver = match header_major(data) {
        Some(Ok(v)) => v,
        Some(Err(())) => return Err(Error::out_of_range("stoi")),
        None => return Err(Error::invalid_argument("stoi")),
    };

    // anchor: the material manager (u32 count right before the first
    // CMaterial new-class record); zero-material files have no CMaterial
    // record anywhere, so fall back to the first CLayer class record and
    // start at the layer-list marker just before it
    let mh = (0..data.len().saturating_sub(15)).find(|&i| {
        data[i] == 255
            && data[i + 1] == 255
            && read_u16(data, i + 4).ok() == Some(9)
            && &data[i + 6..i + 15] == b"CMaterial"
    });
    let (start, mc) = match mh {
        Some(mh) if mh >= 4 => {
            let mc = read_u32(data, mh - 4)?;
            if mc > 100000 {
                return Err(Error::runtime("implausible material count"));
            }
            (mh, mc)
        }
        _ => {
            let lh = (0..data.len().saturating_sub(12))
                .find(|&i| {
                    data[i] == 255
                        && data[i + 1] == 255
                        && read_u16(data, i + 4).ok() == Some(6)
                        && &data[i + 6..i + 12] == b"CLayer"
                })
                .ok_or_else(|| Error::runtime("no CMaterial or CLayer class record found"))?;
            // C++ size_t arithmetic wraps here, and the first read from the
            // wrapped offset throws out_of_range; fail the same way up front.
            let start = lh
                .checked_sub(if ver >= 17 { 9 } else { 8 })
                .ok_or_else(|| Error::out_of_range("legacy archive truncated"))?;
            (start, 0)
        }
    };

    let bases = if mc >= 2 {
        vec![bootstrap_two_materials(data, ver, start)?]
    } else {
        probe_layer_anchor_bases(data, ver, start, mc)?
    };
    walk_with_bases(data, ver, start, mc, &bases)
}

/// Reads a classic (pre-2021) MFC-archive .skp buffer.
pub fn parse_legacy(data: &[u8], o: &ParseOptions) -> Result<RawParsed, SkpParseError> {
    emit_log(
        o,
        LogLevel::Information,
        &format!("Parsing legacy MFC container ({} bytes)", data.len()),
    );
    let mut out = RawParsed {
        version: extract_version(data),
        ..Default::default()
    };
    // Legacy (pre-2021 MFC) files carry no meta/meta.dat container - that's a
    // VFF/ZIP-only construct - so there is no known source for the model's
    // unit-system string here (out.units stays unset).
    let mut walked = walk(data).map_err(|e| {
        SkpParseError::at_stage("legacy .skp parse failed", ParseStage::LegacyWalk).with_cause(e)
    })?;

    {
        let slots = &walked.ar.slots;
        for (slot, v) in &walked.materials {
            // C++ dereferences the value unconditionally; a material slot read
            // as a back-ref (never seen in a real file) is skipped here.
            let Some(v) = v else { continue };
            let x = Arc::new(material(v, slots));
            out.material_id_to_name
                .insert(*slot as EntityId, x.name.clone());
            out.materials.insert(x.name.clone(), x);
        }
        for (slot, l) in &walked.layers {
            out.layer_id_to_name
                .insert(*slot as EntityId, l.name.clone());
            if !out.layer_colors.contains_key(&l.name) {
                out.layer_order.push(l.name.clone());
            }
            out.layer_colors
                .insert(l.name.clone(), [l.r as u8, l.g as u8, l.b as u8]);
            out.layer_hidden.insert(l.name.clone(), l.hidden != 0);
            let dicts = extract_legacy_attribute_dictionaries(l.attrs, slots);
            // Layer::attribute_dictionaries stays string-typed by design.
            if !dicts.is_empty() {
                out.layer_attribute_dictionaries
                    .insert(l.name.clone(), stringify_attr_dictionaries(&dicts));
            }
        }
        if !out.layer_colors.contains_key("Layer0") {
            out.layer_order.push("Layer0".into());
            out.layer_colors.insert("Layer0".into(), [136, 136, 136]);
        }
        out.layer_hidden.entry("Layer0".into()).or_insert(false);

        // A classic file says "this definition is an Image" only by which kind
        // of entity places it (CImage), so collect those first.
        let image_def_ids: HashSet<u64> = slots
            .values()
            .filter(|s| !s.cls && &*s.name == "CImage")
            .filter_map(|s| s.v.as_ref().map(|v| v.def))
            .collect();
        for (&slot, s) in slots {
            if s.cls || &*s.name != "CComponentDefinition" {
                continue;
            }
            let Some(v) = &s.v else { continue };
            let mut d = RawDefinition {
                name: v.name.clone(),
                guid: v.guid.clone(),
                always_faces_camera: v.faces_camera,
                shadows_face_sun: v.shadows_face_sun,
                is_image: image_def_ids.contains(&slot),
                is_group: v.is_group,
                ..Default::default()
            };
            fill(&mut d.builder, &v.ents, slots);
            out.definitions.insert(slot as EntityId, d);
        }
        fill(&mut out.root.builder, &walked.root, slots);
    }

    // whole scene records first; the narrow scan only when the full reader
    // gives up on one
    out.pages = match pages::read_page_list(data, &walked.ar) {
        Some(pages) => pages,
        None => {
            let ar = &mut walked.ar;
            let table = LegacySlotTable {
                slots: &mut ar.slots,
                class_slot: &mut ar.class_slot,
                next: &mut ar.next,
            };
            scan_pages_for_layers(data, ar.r.p, table).unwrap_or_default()
        }
    };

    let n = out.definitions.len() as u64;
    emit_progress(o, ParseStage::LegacyDefs, n, n);
    emit_log(
        o,
        LogLevel::Information,
        &format!("Parse complete: {n} defs"),
    );
    Ok(out)
}
