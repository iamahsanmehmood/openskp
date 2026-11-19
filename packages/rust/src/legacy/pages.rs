//! Scenes (CViewPage records) in a classic file's document tail
//! (`legacy.cpp`'s `scan_pages_for_layers` and `read_page_list`).

use std::collections::BTreeMap;

use super::archive::Archive;
use super::reader::{decode_utf16le, header_major, R};
use super::{LegacySlotEntry, LegacySlotTable};
use crate::internal::{read_u32, Error, RawPage, Result};
use crate::model::EntityId;

// ── narrow scan ──────────────────────────────────────────────────────────
//
// CViewPage's full record embeds a camera, an optional thumbnail image, a
// font, and - whenever any of its several independent "use_*" capture flags
// beyond hidden-layers is set - an entire Style sub-object with no documented
// fixed size. The scan supports only the narrow case: a page whose captured
// flags are exactly the baseline (nothing captured) or baseline +
// hidden-layers. Ground truth for both was captured from real v17-native
// SketchUp saves - mirrors Python's legacy._scan_pages exactly.
const PAGE_ALLOWED_FLAGS: [u32; 2] = [0xc00, 0xc20];
const PAGE_HIDDEN_LAYERS_BIT: u32 = 0x20;

// Consume one reference to an object of `cls_name` - a null tag, a back-ref
// (value unneeded here, so left unresolved), a class-ref to an
// already-declared class, or (rarely, if this is the class's first use in the
// file) a fresh declaration - without relying on the referenced object's slot
// already being known the way Archive::object() requires. The page scan runs
// over a region of the file the main walk never visits, so back-refs into
// that region can't be resolved. Mirrors Python's legacy._skip_typed_ref.
fn skip_typed_ref(
    slots: &mut LegacySlotTable,
    r: &mut R,
    cls_name: &str,
    read_body: &dyn Fn(&mut R) -> Result<()>,
) -> Result<()> {
    let tag = r.peek_u16()?;
    if tag == 0 {
        r.u16()?;
        return Ok(());
    }
    if tag == 0xffff {
        r.u16()?;
        let schema = r.u16()?;
        let namelen = r.u16()?;
        if namelen > 40 {
            return Err(Error::runtime("implausible class name length"));
        }
        let raw_name = r.raw(usize::from(namelen))?;
        let name = String::from_utf8_lossy(raw_name);
        if name != cls_name {
            return Err(Error::runtime(format!(
                "expected {cls_name} decl, got {name}"
            )));
        }
        if !slots.class_slot.contains_key(cls_name) {
            let s = slots.alloc(LegacySlotEntry {
                cls: true,
                name: cls_name.into(),
                schema: i32::from(schema),
                v: None,
            });
            slots.class_slot.insert(cls_name.to_owned(), s);
        }
        read_body(r)?;
        return Ok(());
    }
    if tag & 0x8000 != 0 {
        let cslot = u64::from(tag & 0x7fff);
        match slots.slots.get(&cslot) {
            Some(e) if e.cls && &*e.name == cls_name => {}
            _ => return Err(Error::runtime(format!("unexpected {cls_name} class-ref"))),
        }
        r.u16()?;
        read_body(r)?;
        return Ok(());
    }
    r.u16()?; // plain back-ref - the referenced value isn't needed here
    Ok(())
}

// Same byte layout as the CCamera/CDib branches of Archive::read(),
// duplicated as standalone bodies since skip_typed_ref can't route through
// the full object() dispatch.
fn read_camera_body(r: &mut R, ver: i32) -> Result<()> {
    r.skip(137)?;
    r.u16()?;
    r.utf16()?;
    r.skip(if ver >= 6 { 33 } else { 10 })
}

fn read_dib_body(r: &mut R) -> Result<()> {
    r.u32()?;
    let z = r.u32()?;
    if z as usize > r.d.len() {
        return Err(Error::runtime("implausible dib length"));
    }
    r.skip(z as usize)
}

/// Best-effort scan for CViewPage (scene) records in a legacy (pre-2021 MFC)
/// file: name + hidden-layer slot ids only, for the narrow capture-flag
/// combinations this reader understands. `start_pos` is the byte offset to
/// begin scanning from (where the main entity walk stopped, in a real parse).
///
/// Runs as an independent scan over the file tail rather than a sequential
/// archive read: each candidate is validated by its own local structure (a
/// name string immediately followed by an empty second string and a
/// recognized flags word), and any candidate that doesn't fully validate is
/// silently skipped, so a page this reader can't fully understand is just
/// absent instead of wrong. Mirrors Python's `legacy._scan_pages`.
///
/// Errors only where the C++ throws out of the scan (a flags word cut off by
/// the end of the buffer, an unreadable header version).
pub fn scan_pages_for_layers(
    data: &[u8],
    start_pos: usize,
    mut slots: LegacySlotTable,
) -> Result<Vec<RawPage>> {
    let mut pages = Vec::new();
    let mut pos = start_pos;
    let n = data.len();
    // The header version decides the camera layout below; a buffer with no
    // header (unit tests feed bare record runs) is read as the current layout.
    let scan_ver = match header_major(data) {
        None => 99,
        Some(Ok(v)) => v,
        Some(Err(())) => return Err(Error::out_of_range("stoi")),
    };
    loop {
        let mut idx = None;
        let mut i = pos;
        while i + 3 <= n {
            if data[i] == 255 && data[i + 1] == 254 && data[i + 2] == 255 {
                idx = Some(i);
                break;
            }
            i += 1;
        }
        let Some(idx) = idx else { break };
        pos = idx + 1;
        let nlen_pos = idx + 3;
        if nlen_pos >= n {
            break;
        }
        let nlen = data[nlen_pos];
        if nlen == 0 || nlen > 40 {
            continue;
        }
        let name_start = nlen_pos + 1;
        let name_end = name_start + 2 * usize::from(nlen);
        if name_end + 7 > n {
            continue;
        }
        if data[name_end..name_end + 4] != [255, 254, 255, 0] {
            continue; // second name must be empty
        }
        let flags_pos = name_end + 4;
        let flags = read_u32(data, flags_pos)?;
        if !PAGE_ALLOWED_FLAGS.contains(&flags) {
            continue;
        }
        let name = decode_utf16le(&data[name_start..name_end]);
        let candidate = (|| -> Result<RawPage> {
            let mut r = R::new(data, flags_pos + 4);
            skip_typed_ref(&mut slots, &mut r, "CCamera", &|rr| {
                read_camera_body(rr, scan_ver)
            })?;
            skip_typed_ref(&mut slots, &mut r, "CDib", &read_dib_body)?;
            let mut hidden_ids = Vec::new();
            if flags & PAGE_HIDDEN_LAYERS_BIT != 0 {
                loop {
                    let save = r.p;
                    let v = r.u16()?;
                    match slots.slots.get(&u64::from(v)) {
                        Some(e) if !e.cls && &*e.name == "CLayer" => {
                            hidden_ids.push(EntityId::from(v))
                        }
                        _ => {
                            r.p = save;
                            break;
                        }
                    }
                }
                if r.u32()? != 1 {
                    return Err(Error::runtime("unexpected hidden-layers marker"));
                }
            }
            Ok(RawPage {
                name: name.clone(),
                hidden_layer_ids: hidden_ids,
                ..Default::default()
            })
        })();
        if let Ok(page) = candidate {
            pages.push(page);
        }
    }
    Ok(pages)
}

// ── full scene records ───────────────────────────────────────────────────
//
// Ground truth, all read byte-exactly: the gondola_v20.skp fixture (SketchUp
// 2020, one scene, flags 0x1FF); SketchUp's own 3D Warehouse model "Skill
// Builder: Creating Section Animations with Scenes" in its SketchUp 2017
// format (eight scenes, 0x7F) and SketchUp 2015 saves (nine scenes, 0x7F);
// files written by openskp.create; and files built and saved through
// SketchUp's C API as 2017 to 2020 (schema 15).
//
// The scene list sits in the document tail as a u32 count right before the
// first record (which declares the CViewPage class), then one record per
// scene, then a ref to the selected scene. A record is: the class tag | a
// null attribute ref | (schema 13+) a pid | name | description | u32 capture
// flags | the camera (CCamera, or null) | the thumbnail (CDib, or null) |
// (schema 13) a byte | then one section per capture flag that is set:
//
//   0x02 style       the rendering options (length by file version) + a
//                    CSkpStyle ref or object
//   0x04 shadows     2 bytes (schema 13+: 3) + the shadow info
//   0x08 axes        (schema 13+: a flag byte +) 110 bytes
//   0x10 hidden      a byte, u32 n, n entity refs
//   0x20 layers      u32 n, n refs to the layers (tags) the scene hides
//   0x40 sections    u32 n, n section-plane refs
//
// | 22 bytes (transition and delay time, flags) | u32 n + an n-byte PNG.
//
// Schema 15 (a file saved through the C API as 2017 to 2020) differs: flag
// 0x01 is the camera, which is only there when the flag is set, with no CDib
// tag, no byte after it and no thumbnail; the rendering options are 3 bytes
// longer; the style options have two more value types (3: 2 bytes, 5: 4
// bytes); and the trailer is 21 bytes with no PNG after it.
//
// A record this reader can't fully read, or flags outside 0x1FF, make
// read_page_list give up (None) and parse_legacy falls back to
// scan_pages_for_layers - the same order as Python.

const PAGE_KNOWN_FLAGS: u32 = 0x1FF;
const PAGE_AXES_LEN: usize = 110;
const PAGE_TRAILER_LEN: usize = 22;
const SHADOW_INFO_FIXED_LEN: usize = 60; // after the city and country strings

// The rendering options' length by SketchUp major version, measured on the
// model-level copy of the same block in every fixture from 2014 to 2020;
// other versions aren't known, so give up there.
fn rendering_options_len(ver: i32) -> Option<usize> {
    match ver {
        14..=17 => Some(206),
        18 => Some(211),
        19 => Some(212),
        20 => Some(213),
        _ => None,
    }
}

// CSkpStyle option values: type tag -> byte size (None when unknown)
fn style_value_size(t: u32) -> Option<usize> {
    match t {
        1 => Some(1),
        3 => Some(2),
        4..=6 => Some(4),
        7 => Some(8),
        _ => None,
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum PageTag {
    Null,
    Decl,
    ClassRef,
    Obj,
}

// One archive tag, with the 0x7FFF escape to 32 bits resolved.
fn read_page_tag(r: &mut R) -> Result<(PageTag, u64)> {
    let v = r.u16()?;
    if v == 0 {
        return Ok((PageTag::Null, 0));
    }
    if v == 0xFFFF {
        return Ok((PageTag::Decl, 0));
    }
    if v == 0x7FFF {
        let w = r.u32()?;
        return Ok(if w & 0x8000_0000 != 0 {
            (PageTag::ClassRef, u64::from(w & 0x7FFF_FFFF))
        } else {
            (PageTag::Obj, u64::from(w))
        });
    }
    Ok(if v & 0x8000 != 0 {
        (PageTag::ClassRef, u64::from(v & 0x7FFF))
    } else {
        (PageTag::Obj, u64::from(v))
    })
}

fn skip_page_pid(r: &mut R) -> Result<()> {
    let mask = r.u8()?;
    r.skip(mask.count_ones() as usize)
}

fn page_error(msg: impl Into<std::borrow::Cow<'static, str>>) -> Error {
    Error::runtime(msg)
}

// The classes the scene records declare or refer to: the schema each was
// declared with and, once seen, the slot its class-refs use - every class-ref
// to one class must carry the same slot, which catches a record read from the
// wrong offset early.
struct PageClasses {
    // CCamera and CDib are declared in the document prefix; the scene records
    // only refer to them, at the schemas every v6+ file uses.
    schema: BTreeMap<String, i32>,
    slot: BTreeMap<String, u64>,
}

impl Default for PageClasses {
    fn default() -> Self {
        PageClasses {
            schema: BTreeMap::from([("CCamera".to_owned(), 5), ("CDib".to_owned(), 3)]),
            slot: BTreeMap::new(),
        }
    }
}

impl PageClasses {
    // After a decl or class tag: the declaration's header (if any); returns
    // the schema the object was written with.
    fn object(&mut self, r: &mut R, kind: PageTag, s: u64, name: &str) -> Result<i32> {
        if kind == PageTag::Decl {
            let sch = i32::from(r.u16()?);
            let n = usize::from(r.u16()?);
            let bytes = r.raw(n)?;
            if n != name.len() || bytes != name.as_bytes() {
                return Err(page_error(format!("expected a {name} declaration")));
            }
            self.schema.insert(name.to_owned(), sch);
        } else {
            match self.slot.get(name) {
                None => {
                    self.slot.insert(name.to_owned(), s);
                }
                Some(&have) if have != s => {
                    return Err(page_error(format!("{name} class-ref to another slot")))
                }
                Some(_) => {}
            }
        }
        self.schema
            .get(name)
            .copied()
            .ok_or_else(|| page_error(format!("{name} object of an unknown schema")))
    }
}

fn read_page_camera(r: &mut R, page: &mut RawPage) -> Result<()> {
    let eye = r.vec3()?;
    let target = r.vec3()?;
    let up = r.vec3()?;
    r.f64s(2)?; // constants 1.0, 1000.0
    let perspective = r.u8()?;
    let fov = r.f64()?;
    let ortho_height = r.f64()?;
    r.skip(137 - 105)?;
    r.u16()?;
    r.utf16()?;
    r.skip(33)?;
    page.eye = Some(eye);
    page.target = Some(target);
    page.up = Some(up);
    page.fov = fov;
    page.parallel = perspective == 0;
    page.ortho_height = ortho_height;
    Ok(())
}

// A CSkpStyle object's body: no attributes, a pid (schema 2+), GUID, a
// string, u32, name, description, then its options - each a u32 key and a u32
// count of (u32 type, value) pairs.
fn skip_page_style(r: &mut R, schema: i32) -> Result<()> {
    if r.u16()? != 0 {
        return Err(page_error("style with attributes"));
    }
    if schema >= 2 {
        skip_page_pid(r)?;
    }
    r.skip(16)?;
    r.utf16()?;
    r.u32()?;
    r.utf16()?;
    r.utf16()?;
    let n = r.u32()?;
    for _ in 0..n {
        r.u32()?;
        let m = r.u32()?;
        for _ in 0..m {
            let size =
                style_value_size(r.u32()?).ok_or_else(|| page_error("unknown style value type"))?;
            r.skip(size)?;
        }
    }
    Ok(())
}

fn read_page_refs(r: &mut R, n: u32) -> Result<Vec<EntityId>> {
    if n as usize > r.d.len().saturating_sub(r.p) / 2 {
        return Err(page_error("implausible ref count"));
    }
    let mut refs = Vec::with_capacity(n as usize);
    for _ in 0..n {
        let (kind, v) = read_page_tag(r)?;
        if kind != PageTag::Obj {
            return Err(page_error("expected a back-ref"));
        }
        refs.push(v as EntityId);
    }
    Ok(refs)
}

fn read_page_record(ver: i32, r: &mut R, classes: &mut PageClasses) -> Result<RawPage> {
    let (kind, slot) = read_page_tag(r)?;
    if kind != PageTag::Decl && kind != PageTag::ClassRef {
        return Err(page_error("expected a CViewPage record"));
    }
    let schema = classes.object(r, kind, slot, "CViewPage")?;
    if r.u16()? != 0 {
        return Err(page_error("scene with attributes"));
    }
    if schema >= 13 {
        skip_page_pid(r)?;
    }
    let mut page = RawPage {
        name: r.utf16()?,
        ..Default::default()
    };
    r.utf16()?; // description
    let flags = r.u32()?;
    if flags & !PAGE_KNOWN_FLAGS != 0 {
        return Err(page_error("scene capture flags"));
    }
    if schema >= 15 {
        // saved through SketchUp's C API: the camera is there when its flag
        // is, and there is no thumbnail
        if flags & 0x01 != 0 {
            let (kind, slot) = read_page_tag(r)?;
            if kind != PageTag::Decl && kind != PageTag::ClassRef {
                return Err(page_error("expected a camera"));
            }
            classes.object(r, kind, slot, "CCamera")?;
            read_page_camera(r, &mut page)?;
        }
    } else {
        let (kind, slot) = read_page_tag(r)?;
        if kind == PageTag::Decl || kind == PageTag::ClassRef {
            classes.object(r, kind, slot, "CCamera")?;
            read_page_camera(r, &mut page)?;
        }
        let (kind, slot) = read_page_tag(r)?;
        if kind == PageTag::Decl || kind == PageTag::ClassRef {
            classes.object(r, kind, slot, "CDib")?;
            read_dib_body(r)?;
        }
        if schema >= 13 {
            r.u8()?;
        }
    }
    if flags & 0x02 != 0 {
        let base = rendering_options_len(ver)
            .ok_or_else(|| page_error("rendering options of an unknown version"))?;
        r.skip(base + if schema >= 15 { 3 } else { 0 })?;
        let (kind, slot) = read_page_tag(r)?;
        if kind == PageTag::Decl || kind == PageTag::ClassRef {
            let style_schema = classes.object(r, kind, slot, "CSkpStyle")?;
            skip_page_style(r, style_schema)?;
        }
    }
    if flags & 0x04 != 0 {
        r.skip(if schema >= 13 { 3 } else { 2 })?;
        r.u32()?; // time
        r.u8()?;
        r.utf16()?; // city
        r.utf16()?; // country
        r.skip(SHADOW_INFO_FIXED_LEN)?;
    }
    if flags & 0x08 != 0 {
        r.skip(PAGE_AXES_LEN + usize::from(schema >= 13))?;
    }
    if flags & 0x10 != 0 {
        r.u8()?;
        let n = r.u32()?;
        read_page_refs(r, n)?;
    }
    if flags & 0x20 != 0 {
        let n = r.u32()?;
        page.hidden_layer_ids = read_page_refs(r, n)?;
    }
    if flags & 0x40 != 0 {
        let n = r.u32()?;
        read_page_refs(r, n)?;
    }
    if schema >= 15 {
        r.skip(PAGE_TRAILER_LEN - 1)?;
        return Ok(page);
    }
    r.skip(PAGE_TRAILER_LEN)?;
    let png = r.u32()?;
    if png as usize > r.d.len().saturating_sub(r.p) {
        return Err(page_error("implausible thumbnail length"));
    }
    r.skip(png as usize)?;
    Ok(page)
}

/// Every scene, with its camera and hidden layers, read as a sequence from
/// the scene list in the file tail. `None` when there is a scene list this
/// reader can't fully read; an empty list when there is none.
pub(crate) fn read_page_list(data: &[u8], ar: &Archive) -> Option<Vec<RawPage>> {
    const CLASS: &[u8] = b"CViewPage";
    let mut start = None;
    let mut i = ar.r.p;
    while i + 6 + CLASS.len() <= data.len() {
        let hit = data[i] == 0xFF
            && data[i + 1] == 0xFF
            && matches!(data[i + 2], 0x0C | 0x0D | 0x0F)
            && data[i + 3] == 0x00
            && data[i + 4] == 0x09
            && data[i + 5] == 0x00
            && &data[i + 6..i + 6 + CLASS.len()] == CLASS;
        if hit {
            start = Some(i);
            break;
        }
        i += 1;
    }
    let Some(start) = start else {
        // a scene list in a schema this reader doesn't know
        let from = ar.r.p.min(data.len());
        if data[from..].windows(CLASS.len()).any(|w| w == CLASS) {
            return None;
        }
        return Some(Vec::new());
    };
    if start < 4 {
        return None;
    }
    let count = read_u32(data, start - 4).ok()?;
    if !(1..=10000).contains(&count) {
        return None; // implausible scene count
    }
    let mut r = R::new(data, start);
    let mut classes = PageClasses::default();
    let mut pages = Vec::new();
    for _ in 0..count {
        pages.push(read_page_record(ar.ver, &mut r, &mut classes).ok()?);
    }
    // the list ends with a ref to the selected scene (or a null ref)
    let (kind, _) = read_page_tag(&mut r).ok()?;
    if kind != PageTag::Obj && kind != PageTag::Null {
        return None; // no selected-scene ref
    }
    for page in &mut pages {
        page.hidden_layer_ids.retain(|id| {
            ar.slots
                .get(&(*id as u64))
                .is_some_and(|e| !e.cls && &*e.name == "CLayer")
        });
    }
    Some(pages)
}
