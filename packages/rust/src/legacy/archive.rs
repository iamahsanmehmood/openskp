//! The MFC object-graph walk (`legacy.cpp`'s `struct Archive`, the per-class
//! readers and `walk_model`).

use std::collections::hash_map::Entry;
use std::collections::HashMap;
use std::rc::Rc;

use super::reader::R;
use super::{
    anchored_text_connection, find_v20_filler_marker, legacy_connection_paths,
    legacy_instance_has_guid, LegacyDimension, LegacyKind, LegacyObject, LegacySlotEntry,
    LegacyValue, FIRST_DIB_OBJECT, FIRST_NAMED_INSTANCE, FIRST_V4, MAX_V20_FILLER_PAD,
};
use crate::internal::{read_f64, read_u16, read_u32, Error, Result};
use crate::model::constrained_offset_vector;

/// A decoded value (always present for an object read in full).
pub(crate) type Value = Rc<LegacyValue>;

// True when the bytes at p are an MFC class-ref to class `slot`. Mirrors both
// encodings Archive::object() decodes: the short 16-bit form (0x8000|slot)
// and, for slots past 0x7fff, the big-tag escape (0x7fff followed by a u32 of
// 0x80000000|slot).
pub(crate) fn is_class_ref(d: &[u8], p: usize, slot: u64) -> bool {
    if slot <= 0x7fff {
        return p + 2 <= d.len()
            && u64::from(u16::from_le_bytes([d[p], d[p + 1]])) == (0x8000 | slot);
    }
    p + 6 <= d.len()
        && u16::from_le_bytes([d[p], d[p + 1]]) == 0x7fff
        && u32::from_le_bytes([d[p + 2], d[p + 3], d[p + 4], d[p + 5]])
            == (0x8000_0000u32 | slot as u32)
}

fn hex_guid(g: &[u8]) -> String {
    const H: &[u8; 16] = b"0123456789ABCDEF";
    let mut s = String::with_capacity(g.len() * 2);
    for &x in g {
        s.push(H[(x >> 4) as usize] as char);
        s.push(H[(x & 15) as usize] as char);
    }
    s
}

pub(crate) struct Archive<'a> {
    pub r: R<'a>,
    pub ver: i32,
    pid: bool,
    pub next: u64,
    pub base: u64,
    current_loop: u64,
    pub slots: HashMap<u64, LegacySlotEntry>,
    pub class_slot: HashMap<String, u64>,

    // Burned store-map indices (see the CEdgeUse branch of read()): the
    // writer maps an annotation's connection points into the store map
    // WITHOUT writing bytes, so file back-references beyond each burn run
    // ahead of the walker's numbering. Registrations always stay at WALKER
    // indices - no captured slot ever goes stale - and back() translates file
    // references through the burn bands instead. `burns` holds
    // (file_band_start, width) per event; `cum_delta` their total;
    // `annot_watermark` the walker slot right after the last annotation
    // record - the only place a band can start.
    burns: Vec<(u64, u64)>,
    cum_delta: u64,
    annot_watermark: Option<u64>,
    burn_stack: Vec<i32>, // per-entity-list burned-item credits
    // per-entity-list instances an annotation's path wrote in full (see
    // connection_paths) that the list has not listed yet, in order
    inline_stack: Vec<Vec<u64>>,
    cline_tail: Option<usize>,
    depth: usize,
}

impl<'a> Archive<'a> {
    pub fn new(d: &'a [u8], ver: i32) -> Self {
        Archive {
            r: R::new(d, 0),
            ver,
            pid: ver >= 17,
            next: 0,
            base: 0,
            current_loop: 0,
            slots: HashMap::new(),
            class_slot: HashMap::new(),
            burns: Vec::new(),
            cum_delta: 0,
            annot_watermark: None,
            burn_stack: Vec::new(),
            inline_stack: Vec::new(),
            cline_tail: None,
            depth: 0,
        }
    }

    fn alloc(&mut self, e: LegacySlotEntry) -> u64 {
        let s = self.next;
        self.next += 1;
        self.slots.insert(s, e);
        s
    }

    pub fn object(&mut self, expect: Option<&str>) -> Result<LegacyObject> {
        // Measured on Windows with a hostile chain of nested class refs: an optimized build
        // reaches 256 within a 1 MiB stack (the Windows main thread) but not 512. Debug builds
        // use far bigger frames and overflow even 2 MiB before reaching 256, so the limit only
        // keeps release builds safe on default stacks.
        const MAX_OBJECT_DEPTH: usize = 256;
        if self.depth >= MAX_OBJECT_DEPTH {
            return Err(Error::runtime(
                "legacy archive object nesting exceeds maximum depth",
            ));
        }
        let tag = self.r.u16()?;
        if tag == 0 {
            return Ok(LegacyObject::null());
        }
        if tag == 0x7fff {
            let big = self.r.u32()?;
            if big & 0x8000_0000 != 0 {
                return self.new_class_ref(u64::from(big & 0x7fff_ffff), expect);
            }
            return self.back(u64::from(big));
        }
        if tag == 0xffff {
            let schema = self.r.u16()?;
            let n = self.r.u16()?;
            if n > 40 {
                return Err(Error::runtime("implausible legacy class name"));
            }
            let b = self.r.raw(usize::from(n))?;
            let name: Rc<str> = Rc::from(String::from_utf8_lossy(b).as_ref());
            let cs = self.alloc(LegacySlotEntry {
                cls: true,
                name: name.clone(),
                schema: i32::from(schema),
                v: None,
            });
            self.class_slot.insert(name.to_string(), cs);
            return self.new_obj(name);
        }
        if tag & 0x8000 != 0 {
            return self.new_class_ref(u64::from(tag & 0x7fff), expect);
        }
        self.back(u64::from(tag))
    }

    fn new_class_ref(&mut self, s: u64, expect: Option<&str>) -> Result<LegacyObject> {
        if let Entry::Vacant(slot) = self.slots.entry(s) {
            let Some(expect) = expect else {
                return Err(Error::runtime("unknown legacy class slot"));
            };
            slot.insert(LegacySlotEntry {
                cls: true,
                name: Rc::from(expect),
                schema: 0,
                v: None,
            });
            self.class_slot.insert(expect.to_owned(), s);
        }
        let e = &self.slots[&s];
        if !e.cls {
            return Err(Error::runtime("class ref points to object"));
        }
        let name = e.name.clone();
        self.new_obj(name)
    }

    // Maps a FILE store-map index to the walker's numbering through the burn
    // bands. Returns the walker slot, or None when the reference points INTO a
    // band (a phantom, never-serialized connection point).
    fn translate_ref(&self, slot: u64) -> Option<u64> {
        let mut offset = 0;
        for &(start, width) in &self.burns {
            if slot < start {
                break;
            }
            if slot < start + width {
                return None;
            }
            offset += width;
        }
        Some(slot - offset)
    }

    fn back(&mut self, s: u64) -> Result<LegacyObject> {
        let mut s = s;
        if !self.burns.is_empty() && s >= self.burns[0].0 {
            match self.translate_ref(s) {
                // a phantom (burned) connection-point index - annotation
                // metadata only; nothing was ever serialized for it
                None => {
                    return Ok(LegacyObject {
                        slot: s,
                        name: Rc::from("reserved"),
                        v: None,
                    })
                }
                Some(w) => s = w,
            }
        }
        match self.slots.get(&s) {
            None => {
                if s < self.base {
                    return Ok(LegacyObject {
                        slot: s,
                        name: Rc::from("premodel"),
                        v: None,
                    });
                }
                Err(Error::runtime("legacy backref to unknown slot"))
            }
            Some(e) if e.cls => Err(Error::runtime("legacy backref to class")),
            Some(e) => Ok(LegacyObject {
                slot: s,
                name: e.name.clone(),
                v: e.v.clone(),
            }),
        }
    }

    // Records that the writer burned `delta` store-map indices without
    // serializing any bytes for them.
    fn register_burn(&mut self, delta: u64) {
        let start = self.annot_watermark.unwrap_or(0) + self.cum_delta;
        self.burns.push((start, delta));
        self.cum_delta += delta;
        self.annot_watermark = None;
        // each burn event corresponds to ONE phantom top-level entity that the
        // entity list's declared count includes but the stream never carries -
        // credit it so the list doesn't run past its real end
        if let Some(last) = self.burn_stack.last_mut() {
            *last += 1;
        }
    }

    // A reference-to-entity tag: dimension connection points and text leader
    // attachments. Unlike object()'s back-ref path, this tolerates a slot the
    // walk has not reached yet - SketchUp serializes a label/dimension BEFORE
    // the entity it anchors to when both live in the same entity list, so the
    // reference can legitimately point forward.
    //
    // With `allow_inline`, an object MFC wrote in full here (the first
    // reference to it) is read and registered, and its slot returned; without
    // it, that is an error.
    fn entity_ref(&mut self, allow_inline: bool) -> Result<Option<u64>> {
        let start = self.r.p;
        let tag = self.r.u16()?;
        if tag == 0 {
            return Ok(None);
        }
        if tag == 0x7fff {
            let big = self.r.u32()?;
            if big & 0x8000_0000 == 0 {
                return Ok(Some(u64::from(big)));
            }
        } else if tag != 0xffff && tag & 0x8000 == 0 {
            return Ok(Some(u64::from(tag)));
        }
        if !allow_inline {
            return Err(Error::runtime("entity ref is a new object"));
        }
        self.r.p = start;
        Ok(Some(self.object(None)?.slot))
    }

    // The instance paths that follow each connection ref (see
    // legacy_connection_paths): consumes them, reading a path entry that MFC
    // wrote in full as a real object. A path runs from the outermost instance
    // in, so its first one is an entity of the list the annotation is in; one
    // written here in full is noted for that list (see entity_list). Returns
    // false when the connection carries any instance path.
    fn connection_paths(&mut self) -> Result<bool> {
        let d = self.r.d;
        let pos = self.r.p;
        let res = legacy_connection_paths(d, pos, |at| {
            self.r.p = at;
            let q = self.object(None)?;
            Ok((q.slot, self.r.p))
        })?;
        self.r.p = res.next;
        if let Some(pending) = self.inline_stack.last_mut() {
            for (refs, written) in [(&res.first, &res.first_new), (&res.second, &res.second_new)] {
                if let (Some(Some(slot)), Some(true)) = (refs.first(), written.first()) {
                    pending.push(*slot);
                }
            }
        }
        Ok(res.extra.is_none() && res.first.is_empty() && res.second.is_empty())
    }

    // The walker slot of the back-reference at the cursor, without moving it;
    // None when the next tag is anything else.
    fn peek_backref(&self) -> Option<u64> {
        let d = self.r.d;
        let p = self.r.p;
        if p + 2 > d.len() {
            return None;
        }
        let mut slot = u64::from(u16::from_le_bytes([d[p], d[p + 1]]));
        if slot == 0x7fff {
            if p + 6 > d.len() {
                return None;
            }
            slot = u64::from(u32::from_le_bytes([d[p + 2], d[p + 3], d[p + 4], d[p + 5]]));
            if slot & 0x8000_0000 != 0 {
                return None;
            }
        } else if slot == 0 || slot & 0x8000 != 0 {
            return None;
        }
        if !self.burns.is_empty() && slot >= self.burns[0].0 {
            return self.translate_ref(slot);
        }
        Some(slot)
    }

    // True when the u16 at `at` starts an object read in one of the
    // UNAMBIGUOUS forms: null, escape, class definition, or a class-ref to a
    // class already known. Plain object back-refs are excluded on purpose -
    // any 2-byte junk below 0x8000 would qualify.
    fn strict_next_tag(&self, at: usize, allow_null: bool) -> bool {
        let d = self.r.d;
        if at + 2 > d.len() {
            return false;
        }
        let t = u16::from_le_bytes([d[at], d[at + 1]]);
        if t == 0 {
            return allow_null;
        }
        if t == 0x7fff || t == 0xffff {
            return true;
        }
        if t & 0x8000 != 0 {
            return self
                .slots
                .get(&u64::from(t & 0x7fff))
                .is_some_and(|e| e.cls);
        }
        false
    }

    // The textured-material payload: an embedded CDib plus applied size,
    // source file name, average colour, and opacity. Shared verbatim between
    // a CMaterial with a texture and a colour-by-layer CLayer that carries a
    // textured material.
    //
    // The texture is an entity in its own right, so right after the u8 "has
    // texture" flag it opens with the standard entity preamble: an
    // attribute-container ref and, from v17 on, the persistent-id mask.
    // Callers read the flag as a u16, so step back over its high byte - the
    // first byte of that preamble.
    //
    // SketchUp 3-5 have neither: the image follows the flag directly as a
    // bare subtype/length/bytes record - no preamble, no object tag, and so no
    // store-map slot (the bytes ride along in `v.blob`, flagged by
    // `v.inline_tex`).
    fn texture_block(&mut self, v: &mut LegacyValue) -> Result<()> {
        if self.ver < FIRST_DIB_OBJECT {
            self.r.u32()?;
            let z = self.r.u32()?;
            if z as usize > self.r.d.len() {
                return Err(Error::runtime("implausible dib length"));
            }
            v.blob = self.r.raw(z as usize)?.to_vec();
            v.inline_tex = true;
        } else {
            self.r.p -= 1;
            self.preamble()?;
            let q = self.object(Some("CDib"))?;
            v.tex_dib = q.slot;
        }
        let d = self.r.d;
        let begin = self.r.p;
        let limit = d.len().min(self.r.p + 28);
        let mut marker = begin;
        while marker + 3 <= limit {
            if d[marker] == 255 && d[marker + 1] == 254 && d[marker + 2] == 255 {
                break;
            }
            marker += 1;
        }
        if marker - begin == 20 {
            self.r.u32()?;
        } else if marker - begin != 16 {
            return Err(Error::runtime("texture size block misaligned"));
        }
        v.tw = self.r.f64()?;
        v.th = self.r.f64()?;
        v.tex_file = self.r.utf16()?;
        let c = self.r.raw(9)?; // image average RGBA + 00 + material paint RGBA
        v.r = i32::from(c[5]);
        v.g = i32::from(c[6]);
        v.b = i32::from(c[7]);
        // c[3] is the image average alpha, not the material opacity. Textured
        // materials/layers use the separate opacity field; v.a stays 255.
        self.r.utf16()?;
        let blob = self.r.raw(8)?; // u32 material type + u32 colorize mode
        v.opacity = self.r.f64()?;
        if self.r.u8()? == 0 {
            v.opacity = 0.0;
        }
        v.colorized = blob[0] == 2;
        v.colorize_type = if blob[4] == 1 { 1 } else { 0 };
        Ok(())
    }

    // Returns the CAttributeContainer's slot (None when this entity has none:
    // a null object reference) and the class name the leading pointer
    // resolved to.
    fn preamble_cls(&mut self) -> Result<(Option<u64>, Rc<str>)> {
        let attrs = self.object(Some("CAttributeContainer"))?;
        if self.pid {
            let mask = self.r.u8()?;
            for i in 0..8 {
                if mask & (1 << i) != 0 {
                    self.r.u8()?;
                }
            }
        }
        let slot = if attrs.name.is_empty() {
            None
        } else {
            Some(attrs.slot)
        };
        Ok((slot, attrs.name))
    }

    fn preamble(&mut self) -> Result<Option<u64>> {
        Ok(self.preamble_cls()?.0)
    }

    // `preamble` for the entity types that carry NO attribute-container
    // pointer in a SketchUp 3 file: vertex, loop, edge-use, material, layer.
    // (Edge, face, definition, instance and thumbnail still open with one.)
    fn early_preamble(&mut self) -> Result<Option<u64>> {
        if self.ver < FIRST_V4 {
            return Ok(None);
        }
        self.preamble()
    }

    fn draw(&mut self, v: &mut LegacyValue) -> Result<()> {
        // SketchUp 3's draw block is 5 bytes, identical for every entity in
        // the calibration file, so nothing in them is decodable: report the
        // fields as unset rather than what the 8-byte layout would read.
        let v3 = self.ver < FIRST_V4;
        // 5 bytes in V3, 7 in V4, 8 from V5.
        let b = self.r.raw(if v3 {
            5
        } else if self.ver < 5 {
            7
        } else {
            8
        })?;
        if !v3 {
            v.mat = i32::from(b[0]) | i32::from(b[1]) << 8;
            v.hidden = i32::from(b[2]);
            v.soft = i32::from(b[5]);
            v.smooth = i32::from(b[6]);
        }
        // The layer field is normally a u16 id, but an entity can carry the
        // layer BY OBJECT instead (seen on real 2018 instances): a full inline
        // CLayer record on first use, an escaped back-ref to it on later
        // siblings. Layer ids never have the 0x8000 bit and never equal
        // 0x7fff, so both object forms are unambiguous.
        let lay_cs = self.class_slot.get("CLayer").copied();
        let tag = read_u16(self.r.d, self.r.p)?;
        if lay_cs.is_some_and(|cs| u64::from(tag) == (0x8000 | cs)) {
            self.object(Some("CLayer"))?;
            v.layer = 0; // by-object layer: keep the default id
        } else if tag == 0x7fff {
            self.r.u16()?;
            let big = self.r.u32()?;
            if big & 0x8000_0000 != 0 {
                return Err(Error::runtime("drawbase layer: unexpected class"));
            }
            v.layer = 0; // by-object layer (back-ref)
        } else {
            v.layer = i32::from(self.r.u16()?);
        }
        Ok(())
    }

    // 0xffff CLayer, a class-ref to a CLayer already in class_slot, or - when
    // `unmatched_class_ref` - any short 0x8000 class-ref. The last form is
    // only for the throwaway slot-base probe, which numbers from 1<<20 so the
    // file's 0x8000|real_slot will not match class_slot.
    fn clayer_record_at(&self, at: usize, unmatched_class_ref: bool) -> bool {
        let d = self.r.d;
        if at + 2 > d.len() {
            return false;
        }
        let tag = u16::from_le_bytes([d[at], d[at + 1]]);
        if tag == 0xffff
            && at + 12 <= d.len()
            && u16::from_le_bytes([d[at + 4], d[at + 5]]) == 6
            && &d[at + 6..at + 12] == b"CLayer"
        {
            return true;
        }
        if let Some(&cs) = self.class_slot.get("CLayer") {
            if is_class_ref(d, at, cs) {
                return true;
            }
        }
        unmatched_class_ref && tag & 0x8000 != 0 && tag != 0xffff
    }

    // Colour CLayer ends in a 21-byte tail. Some v18 zero-material files
    // (custom tag listed first, then Layer0) write 12 zero bytes + u32 after
    // that, then another CLayer. Skip those 16 bytes only when they precede a
    // CLayer; leave a definition-list back-ref alone.
    fn skip_clayer_colour_ext(&mut self) {
        const PAD: usize = 12;
        const EXT: usize = 16;
        let d = self.r.d;
        if self.r.p + EXT + 2 > d.len() {
            return;
        }
        if d[self.r.p..self.r.p + PAD].iter().any(|&b| b != 0) {
            return;
        }
        if self.clayer_record_at(self.r.p + EXT, true) {
            self.r.p += EXT;
        }
    }

    // v18 template Layer0 after a custom tag: <parent u16><active u16><dc u32>.
    // Consume parent only when both u16s are already CLayer objects. A lone
    // back-ref then a definition count (usual layout) stays put.
    fn skip_clayer_parent_ref(&mut self, this: u64) {
        let d = self.r.d;
        let p = self.r.p;
        if p + 4 > d.len() {
            return;
        }
        let a = u16::from_le_bytes([d[p], d[p + 1]]);
        let b = u16::from_le_bytes([d[p + 2], d[p + 3]]);
        if a == 0 || a & 0x8000 != 0 || a == 0x7fff || u64::from(a) == this {
            return;
        }
        if b == 0 || b & 0x8000 != 0 || b == 0x7fff {
            return;
        }
        let (Some(ea), Some(eb)) = (self.slots.get(&u64::from(a)), self.slots.get(&u64::from(b)))
        else {
            return;
        };
        if ea.cls || eb.cls {
            return;
        }
        if &*ea.name != "CLayer" || &*eb.name != "CLayer" {
            return;
        }
        self.r.p += 2;
    }

    // Extra CLayer records past declared layer_count (v18: count=1 custom
    // tag, then Layer0). Skip null separators; stop at the definition-list
    // anchor. `unmatched_class_ref` only on the throwaway probe.
    pub fn collect_trailing_layers(
        &mut self,
        mut slots_out: Option<&mut Vec<u64>>,
        mut layers_out: Option<&mut Vec<(u64, Value)>>,
        unmatched_class_ref: bool,
    ) -> Result<()> {
        while self.r.p + 2 <= self.r.d.len() {
            let tag = read_u16(self.r.d, self.r.p)?;
            if tag == 0 {
                self.r.p += 2;
                continue;
            }
            if !self.clayer_record_at(self.r.p, unmatched_class_ref) {
                break;
            }
            let q = self.object(Some("CLayer"))?;
            if let Some(v) = q.v {
                if let Some(out) = slots_out.as_deref_mut() {
                    out.push(q.slot);
                }
                if let Some(out) = layers_out.as_deref_mut() {
                    out.push((q.slot, v));
                }
            }
        }
        Ok(())
    }

    fn new_obj(&mut self, n: Rc<str>) -> Result<LegacyObject> {
        let slot = self.alloc(LegacySlotEntry {
            cls: false,
            name: n.clone(),
            schema: 0,
            v: None,
        });
        self.depth += 1;
        let read_res = self.read(&n, slot);
        self.depth -= 1;
        let v = Rc::new(read_res?);
        self.slots.entry(slot).or_default().v = Some(v.clone());
        if &*n == "CDimensionLinear" || &*n == "CText" {
            self.annot_watermark = Some(self.next);
        }
        Ok(LegacyObject {
            slot,
            name: n,
            v: Some(v),
        })
    }

    fn read(&mut self, n: &str, this: u64) -> Result<LegacyValue> {
        let mut v = LegacyValue::default();
        match n {
            "CVertex" => {
                self.early_preamble()?;
                v.k = LegacyKind::Vertex;
                v.xyz = self.r.vec3()?;
            }
            "CEdge" => {
                self.preamble()?;
                v.k = LegacyKind::Edge;
                self.draw(&mut v)?;
                v.v1 = self.object(Some("CVertex"))?.slot;
                v.v2 = self.object(Some("CVertex"))?.slot;
                let curve = self.object(None)?;
                if !curve.name.is_empty() {
                    v.curve = Some(curve.slot);
                    v.curve_value = curve.v;
                }
            }
            "CCurve" => {
                self.preamble()?;
                v.k = LegacyKind::Curve;
                self.r.u8()?;
                self.r.u32()?;
            }
            "CArcCurve" => {
                self.preamble()?;
                v.k = LegacyKind::ArcCurve;
                self.r.skip(5)?;
                // center[3], normal[3], x_axis[3], start_angle, end_angle, y_axis[3]
                v.arc.center = self.r.vec3()?;
                v.arc.normal = self.r.vec3()?;
                v.arc.x_axis = self.r.vec3()?;
                v.arc.start_angle = self.r.f64()?;
                v.arc.end_angle = self.r.f64()?;
                v.arc.y_axis = self.r.vec3()?;
            }
            "CEdgeUse" => {
                self.early_preamble()?;
                v.k = LegacyKind::EdgeUse;
                v.edge = self.object(Some("CEdge"))?.slot;
                v.sense = self.r.u8()? != 0;
                // parent-loop back-ref: the alignment oracle. Read as a RAW
                // file index - after annotations the claimed index can sit
                // AHEAD of the walker's numbering (burned MapObject indices,
                // see register_burn()), which is a correction signal, not a
                // mis-parse.
                let p0 = self.r.p;
                let tag = self.r.u16()?;
                let parent = if tag == 0x7fff {
                    let big = self.r.u32()?;
                    if big & 0x8000_0000 != 0 {
                        return Err(Error::runtime("edge-use parent is a new object"));
                    }
                    Some(u64::from(big))
                } else if tag == 0xffff || tag & 0x8000 != 0 {
                    return Err(Error::runtime("edge-use parent is a new object"));
                } else if tag != 0 {
                    Some(u64::from(tag))
                } else {
                    None
                };
                let expected = self.current_loop + self.cum_delta;
                if parent != Some(expected) {
                    let delta = parent.map_or(0, |p| p as i64 - expected as i64);
                    if delta > 0 && delta <= 4096 && self.annot_watermark.is_some() {
                        self.register_burn(delta as u64);
                    } else {
                        self.r.p = p0;
                        return Err(Error::runtime("edge-use parent mismatch"));
                    }
                }
            }
            "CLoop" => {
                let old = self.current_loop;
                self.current_loop = this;
                self.early_preamble()?;
                self.r.skip(2)?;
                v.k = LegacyKind::Loop;
                while self.r.p + 2 <= self.r.d.len() && read_u16(self.r.d, self.r.p)? != 0 {
                    if let Some(q) = self.object(Some("CEdgeUse"))?.v {
                        v.uses.push(q);
                    }
                }
                self.r.u16()?;
                self.current_loop = old;
            }
            "CFace" => {
                let (attrs, lead_cls) = self.preamble_cls()?;
                v.attrs = attrs;
                v.k = LegacyKind::Face;
                self.draw(&mut v)?;
                if self.ver < FIRST_V4 && &*lead_cls == "CMaterial" {
                    if let Some(a) = v.attrs {
                        // SketchUp 3: the pointer that opens a face is its
                        // FRONT MATERIAL (null for an unpainted face), not an
                        // attribute container.
                        v.mat = a as i32;
                        v.attrs = None;
                    }
                }
                v.plane = self.r.f64s(4)?;
                let count = self.r.u32()?;
                if count > 10000 {
                    return Err(Error::runtime("implausible loop count"));
                }
                for _ in 0..count {
                    if let Some(q) = self.object(Some("CLoop"))?.v {
                        v.loops.push(q);
                    }
                }
                v.back_mat = i32::from(self.r.u16()?);
            }
            "CAttributeContainer" => {
                self.preamble()?;
                v.k = LegacyKind::Attrs;
                while self.r.p + 2 <= self.r.d.len() && read_u16(self.r.d, self.r.p)? != 0 {
                    let q = self.object(Some("CAttributeNamed"))?;
                    v.ents.push(q);
                }
                self.r.u16()?;
            }
            "CAttributeNamed" => {
                self.preamble()?;
                v.k = LegacyKind::Dict;
                self.r.skip(4)?;
                v.name = self.r.utf16()?;
                loop {
                    let key = self.r.utf16()?;
                    if key.is_empty() {
                        break;
                    }
                    let t = self.r.u8()?;
                    let value = self.r.attr_value(t)?;
                    v.entries.insert(key, value);
                }
                // Trailing u32 is a v7+ addition - a real SketchUp 6 file ends
                // the record at the empty-key terminator with nothing after
                // it (openskp#284's V6 signature; openskp#385). The class is
                // always learned from the unwalked pre-model region, so its
                // schema can't gate this - the file's version is the only
                // signal available.
                if self.ver >= 7 {
                    self.r.u32()?;
                }
            }
            "CLayer" => {
                v.attrs = self.early_preamble()?;
                v.k = LegacyKind::Layer;
                v.name = self.r.utf16()?;
                let mut mid = Vec::new();
                while mid.len() < 8 && !self.r.marker() {
                    mid.push(self.r.u8()?);
                }
                v.hidden = mid.first().map_or(0, |&b| i32::from(b));
                self.r.utf16()?;
                let flags = self.r.u16()?;
                if flags & 0x00ff != 0 {
                    // Colour-by-layer with a TEXTURED material: the layer
                    // embeds the same texture block a CMaterial carries. Low
                    // byte of the flag word set = textured.
                    self.texture_block(&mut v)?;
                    self.r.skip(4)?; // trailing u32
                } else {
                    let c = self.r.raw(4)?;
                    v.r = i32::from(c[0]);
                    v.g = i32::from(c[1]);
                    v.b = i32::from(c[2]);
                    self.r.utf16()?;
                    // SketchUp 3's tail is 4 bytes shorter
                    self.r.skip(if self.ver < FIRST_V4 { 17 } else { 21 })?;
                    self.skip_clayer_colour_ext();
                    self.skip_clayer_parent_ref(this);
                }
            }
            "CMaterial" => {
                self.early_preamble()?;
                v.k = LegacyKind::Material;
                v.name = self.r.utf16()?;
                let flag = self.r.u16()?;
                if flag == 0 {
                    let c = self.r.raw(4)?;
                    v.r = i32::from(c[0]);
                    v.g = i32::from(c[1]);
                    v.b = i32::from(c[2]);
                    v.a = i32::from(c[3]);
                    self.r.utf16()?;
                    self.r.skip(8)?;
                    v.opacity = self.r.f64()?;
                    if self.r.u8()? == 0 {
                        v.opacity = 0.0;
                    }
                } else {
                    self.texture_block(&mut v)?;
                }
            }
            "CDib" => {
                v.k = LegacyKind::Dib;
                self.r.u32()?;
                let z = self.r.u32()?;
                if z as usize > self.r.d.len() {
                    return Err(Error::runtime("implausible dib length"));
                }
                v.blob = self.r.raw(z as usize)?.to_vec();
            }
            "CFaceTextureCoords" => {
                self.preamble()?;
                v.k = LegacyKind::Ftc;
                self.r.u32()?;
                let a = self.r.f64s(24)?;
                v.uvf = a[0..9].to_vec();
                v.uvb = a[12..21].to_vec();
                for _ in 0..2 {
                    let mut z = self.r.u32()?;
                    while z > 0 {
                        z -= 1;
                        self.r.f64s(4)?;
                    }
                }
                let fflags = self.r.u32()?;
                let bflags = self.r.u32()?;
                v.front_projected = fflags & 2 != 0;
                v.back_projected = bflags & 2 != 0;
            }
            "CCamera" => {
                self.r.skip(137)?;
                self.r.u16()?;
                self.r.utf16()?;
                self.r.skip(if self.ver >= 6 { 33 } else { 10 })?;
            }
            "CThumbnail" => {
                self.preamble()?;
                if self.ver < FIRST_V4 {
                    // SketchUp 3: a fixed 129-byte camera block, no name, no
                    // image.
                    self.r.skip(129)?;
                } else {
                    self.object(Some("CCamera"))?;
                    // SketchUp 4 and 5 end the thumbnail with its camera: no
                    // trailing image pointer (not even a null one).
                    if self.ver >= 6 {
                        self.object(Some("CDib"))?;
                    }
                }
            }
            "CImage" => {
                // An Image entity - instance-shaped: a back-ref to the
                // (already walked) CComponentDefinition holding the image's
                // face and texture, a 3x4 placement, a constant 1.0, the
                // source path string, and a 16-byte GUID.
                self.preamble()?;
                v.k = LegacyKind::Image;
                self.draw(&mut v)?;
                v.def = self.object(None)?.slot;
                v.xf = self.r.f64s(12)?;
                self.r.f64()?; // constant 1.0
                self.r.utf16()?; // source path
                v.guid = hex_guid(self.r.raw(16)?);
            }
            "CRelationship" => {
                // two object pointers binding an annotation to the entity it
                // labels; they can point forward, past the walk cursor
                // (entity_ref() tolerates that).
                self.preamble()?;
                v.k = LegacyKind::Relationship;
                self.entity_ref(false)?;
                self.entity_ref(false)?;
            }
            "CConstructionLine" => {
                self.preamble()?;
                v.k = LegacyKind::ConstructionLine;
                self.draw(&mut v)?;
                // point(3) + direction(3) + two signed distance params along
                // direction marking where the visible segment starts/ends. A
                // parameter magnitude past HUGE_PARAM means unbounded in that
                // direction (matches the real API returning nil).
                let lp = self.r.f64s(8)?;
                v.xyz = [lp[0], lp[1], lp[2]];
                v.direction = [lp[3], lp[4], lp[5]];
                const HUGE_PARAM: f64 = 1e20;
                let (start_param, end_param) = (lp[6], lp[7]);
                let at = |t: f64| -> [f64; 3] {
                    [lp[0] + lp[3] * t, lp[1] + lp[4] * t, lp[2] + lp[5] * t]
                };
                if start_param.abs() < HUGE_PARAM {
                    v.start = Some(at(start_param));
                }
                if end_param.abs() < HUGE_PARAM {
                    v.end = Some(at(end_param));
                }
                // The trailing block varies by the WRITING BUILD, not cleanly
                // by version: 7 bytes on the v17 calibration corpus, 4 on v16
                // and on a real v18, 0 on another real v17. Self-calibrate on
                // the first guide line of the file - the length that lands on
                // a legitimate next tag (strict forms only) - and cache it for
                // the rest of the file.
                if self.cline_tail.is_none() {
                    let dflt: usize = if self.ver == 17 { 7 } else { 4 };
                    let mut order = vec![dflt];
                    for c in [0usize, 4, 7] {
                        if c != dflt {
                            order.push(c);
                        }
                    }
                    // Strong tags first (escape / known class / class
                    // definition). The weak (null) pass is the last-in-list
                    // case: a construction line that is the last entity in a
                    // CComponentDefinition is followed by nrel=0, which looks
                    // like a null tag at every candidate - prefer 4 there.
                    let p = self.r.p;
                    let k = order
                        .iter()
                        .copied()
                        .find(|&c| self.strict_next_tag(p + c, false))
                        .or_else(|| {
                            [4usize, 0, 7]
                                .into_iter()
                                .find(|&c| self.strict_next_tag(p + c, true))
                        });
                    self.cline_tail = Some(k.unwrap_or(4));
                }
                let tail = self.cline_tail.unwrap_or(4);
                self.r.skip(tail)?;
            }
            "CConstructionPoint" => {
                self.preamble()?;
                v.k = LegacyKind::ConstructionPoint;
                self.draw(&mut v)?;
                v.xyz = self.r.vec3()?;
                self.r.vec3()?; // reserved/unused (observed all-zero)
                self.r.u8()?; // reserved/unused (observed 0)
            }
            "CSectionPlane" => {
                self.preamble()?;
                v.k = LegacyKind::SectionPlane;
                self.draw(&mut v)?;
                let first = read_f64(self.r.d, self.r.p)?;
                if first.abs() > 1.0001 {
                    self.object(None)?;
                }
                v.plane = self.r.f64s(4)?;
                if self.r.marker() {
                    v.name = self.r.utf16()?;
                    v.label = self.r.utf16()?;
                }
            }
            "CSkFont" => {
                self.object(Some("CAttributeContainer"))?;
                if self.pid {
                    // the mask byte says how many pid bytes follow (SketchUp
                    // 2017+ writes two or more here)
                    let mask = self.r.u8()?;
                    self.r.skip(mask.count_ones() as usize)?;
                }
                self.r.utf16()?;
                self.r.skip(15)?;
            }
            "CDimensionLinear" => {
                self.preamble()?;
                v.k = LegacyKind::Dimension;
                self.draw(&mut v)?;
                v.text = self.r.utf16()?;
                if self.ver >= 6 {
                    self.object(Some("CSkFont"))?;
                }
                // The tail embeds two connection points into the geometry,
                // each a normal MFC tag (2 bytes, or 6 once the big-tag escape
                // kicks in) followed by its instance paths, so a fixed-size
                // skip walks off the rails on large models. SketchUp 4 and 5
                // write neither the font above nor the 5 bytes in front of the
                // first connection block, nor the trailing u32 of the last.
                let mut decoded = LegacyDimension::default();
                if self.ver >= 6 {
                    self.r.skip(5)?;
                }
                for second in [false, true] {
                    // [u32 type][u32 4][point3d]: type 1 stores the point
                    // inline.
                    let block = self.r.raw(32)?;
                    let mut point = (read_u32(block, 0)? == 1).then_some([
                        read_f64(block, 8)?,
                        read_f64(block, 16)?,
                        read_f64(block, 24)?,
                    ]);
                    // The first reference to an anchored vertex can serialize
                    // it in full. Keep forward references unresolved until the
                    // complete archive is read (see fill).
                    let mut r = self.entity_ref(true)?;
                    if !self.connection_paths()? {
                        // Do not expose definition-local vertex coordinates as
                        // owner/world points until the nested instance path
                        // can also be resolved.
                        point = None;
                        r = None;
                    }
                    if second {
                        decoded.geometry.b = point;
                        decoded.connect2 = r;
                    } else {
                        decoded.geometry.a = point;
                        decoded.connect1 = r;
                    }
                }
                let placement = self.r.raw(if self.ver >= 6 { 72 } else { 68 })?;
                let f = |at| read_f64(placement, at);
                let normal = [f(0)?, f(8)?, f(16)?];
                let x = [f(24)?, f(32)?, f(40)?];
                let axis = read_u32(placement, 48)?;
                let g = &mut decoded.geometry;
                g.normal = Some(normal);
                g.plane_x = Some(x);
                g.offset = f(52)?;
                // X/Y-constrained dimensions use their plane frame,
                // independent of the order of the measured endpoints.
                // Preserve other modes without guessing.
                g.offset_vector = constrained_offset_vector(axis, normal, x, g.offset);
                v.dimension = Some(Box::new(decoded));
            }
            "CText" => {
                self.preamble()?;
                v.k = LegacyKind::Text;
                self.draw(&mut v)?;
                self.object(Some("CSkFont"))?;
                if anchored_text_connection(self.r.d, self.r.p) {
                    // An anchored connection block is followed, as a
                    // dimension's is, by the anchored entity's ref and its
                    // instance paths - and MFC writes a path instance IN FULL
                    // there when nothing has pointed at it before (a label
                    // anchored to a component instance serialized after it,
                    // its class declaration included when it is the file's
                    // first instance). Skipped as bytes, that instance never
                    // gets its store-map slots and every later reference is
                    // off by them; read it instead.
                    self.r.skip(16 + 32)?;
                    self.entity_ref(false)?;
                    self.connection_paths()?;
                }
                let d = self.r.d;
                let lim = d.len().min(self.r.p + 512);
                let mut found = None;
                let mut q = self.r.p;
                while q + 14 <= lim {
                    if d[q] == 1
                        && d[q + 1] == 0
                        && d[q + 2] == 0
                        && d[q + 3] == 0
                        && d[q + 6] <= 8
                        && d[q + 7] == 0
                        && d[q + 8] == 0
                        && d[q + 9] == 0
                        && d[q + 10] == 1
                        && d[q + 11] == 255
                        && d[q + 12] == 254
                        && d[q + 13] == 255
                    {
                        found = Some(q + 11);
                        break;
                    }
                    q += 1;
                }
                let Some(found) = found else {
                    return Err(Error::runtime("text delimiter not found"));
                };
                self.r.skip(found - self.r.p)?;
                v.text = self.r.utf16()?;
                self.r.skip(5)?;
                // Optional leader-attachment refs follow the fixed tail. Only
                // the escaped 6-byte form is recognisable without risk: a
                // 2-byte back-ref here would be indistinguishable from the
                // next list item's tag.
                while self.r.p + 2 <= d.len() && d[self.r.p] == 255 && d[self.r.p + 1] == 127 {
                    if self.r.p + 6 > d.len() {
                        break;
                    }
                    let val = read_u32(d, self.r.p + 2)?;
                    if val & 0x8000_0000 != 0 {
                        break; // new-object tag - the next entity
                    }
                    self.r.skip(6)?;
                }
            }
            "CComponentDefinition" => self.read_definition(&mut v)?,
            "CComponentInstance" | "CGroup" => {
                v.attrs = self.preamble()?;
                v.k = LegacyKind::Instance;
                self.draw(&mut v)?;
                v.def = self.object(Some("CComponentDefinition"))?.slot;
                v.xf = self.r.f64s(13)?;
                // SketchUp 3 and 4 instances carry no name string (5 is the
                // first to write one); where it is optional, take it only if a
                // string is there.
                if self.ver >= FIRST_NAMED_INSTANCE || self.r.marker() {
                    v.name = self.r.utf16()?;
                }
                if legacy_instance_has_guid(self.ver) {
                    self.r.skip(16)?;
                }
            }
            _ => return Err(Error::runtime(format!("no legacy reader for {n}"))),
        }
        Ok(v)
    }

    fn read_definition(&mut self, v: &mut LegacyValue) -> Result<()> {
        self.preamble()?;
        v.k = LegacyKind::Definition;
        // undecoded base block: 13 bytes in SketchUp 3, 19 in 4, 20 from 5,
        // 22 from 2017
        let ver = self.ver;
        self.r.skip(if ver >= 17 {
            22
        } else if ver >= 5 {
            20
        } else if ver >= FIRST_V4 {
            19
        } else {
            13
        })?;
        let nl = self.r.u32()?;
        if nl > 10000 {
            return Err(Error::runtime("implausible def layers"));
        }
        // like the model-level layer list, the count is REAL layers (new
        // records or back-refs); SketchUp 2020 interleaves null separators
        // between them
        let mut got = 0;
        while got < nl {
            if self.r.p + 2 <= self.r.d.len() && read_u16(self.r.d, self.r.p)? == 0 {
                self.r.p += 2;
                continue;
            }
            self.object(Some("CLayer"))?;
            got += 1;
        }
        if ver == 19
            && nl > 0
            && self.r.p + 2 <= self.r.d.len()
            && read_u16(self.r.d, self.r.p)? == 0
        {
            // SketchUp 2019 follows the LAST layer with a separator null too
            // (2020 does as well, but its count recovery already absorbs it).
            self.r.p += 2;
        }
        let decl = self.r.u16()?;
        if decl == 0x7fff {
            self.r.u32()?;
        }
        // v20 can drop its undocumented filler right here, swallowing the u32
        // field (and, behind a layer-separator null, even the decl itself):
        // if the empty-string marker sits in the next few bytes, the real
        // count is the first non-zero u32 after its padding.
        let mut count = 0u32;
        let mut filled = false;
        if ver >= 20 {
            if let Some(c) = self.retry_count_after_v20_filler(self.r.p, 5_000_000) {
                count = c;
                filled = true;
            }
        }
        if !filled {
            self.r.u32()?;
            count = self.r.u32()?;
        }
        // A zero count is as much a symptom of the v20 filler as an
        // implausibly large one: the reader lands on the leading zero bytes of
        // the filler instead of the count. A genuinely empty definition reads
        // zero with no filler ahead, and the retry leaves those alone.
        if count > 5_000_000 || count == 0 {
            if let Some(retry) = self.retry_count_after_v20_filler(self.r.p - 4, 5_000_000) {
                count = retry;
            }
        }
        if count > 5_000_000 {
            return Err(Error::runtime("implausible def entities"));
        }
        v.ents = self.entity_list(count, false)?;
        let mut nr = self.r.u32()?;
        if nr > 100_000 {
            if let Some(retry) = self.retry_count_after_v20_filler(self.r.p - 4, 100_000) {
                nr = retry;
            }
        }
        if nr > 100_000 {
            return Err(Error::runtime("definition list misaligned"));
        }
        for _ in 0..nr {
            self.object(Some("CRelationship"))?;
        }
        self.r.u16()?;
        // The GUID is followed immediately by the name string. Some files
        // (SketchUp 2020) carry two extra bytes ahead of the GUID; anchor on
        // the string marker that must follow the 16 GUID bytes instead of
        // trusting the fixed prefix width.
        let d = self.r.d;
        let marker_after_guid = |at: usize| {
            at + 19 <= d.len() && d[at + 16] == 255 && d[at + 17] == 254 && d[at + 18] == 255
        };
        if !marker_after_guid(self.r.p) {
            if let Some(skip) = (1..=4).find(|&skip| marker_after_guid(self.r.p + skip)) {
                self.r.p += skip;
            }
        }
        v.guid = hex_guid(self.r.raw(16)?);
        v.name = self.r.utf16()?;
        self.r.utf16()?;
        self.r.utf16()?;
        self.r.u32()?;
        let thumb_cs = self.class_slot.get("CThumbnail").copied();
        let mut tpos = None;
        let mut off = 0;
        while off < 96 && self.r.p + off + 26 <= d.len() {
            let p = self.r.p + off;
            if d[p] == 255
                && d[p + 1] == 255
                && d[p + 4] == 10
                && d[p + 5] == 0
                && &d[p + 6..p + 16] == b"CThumbnail"
            {
                tpos = Some(p);
                break;
            }
            if thumb_cs.is_some_and(|cs| is_class_ref(d, p, cs)) {
                tpos = Some(p);
                break;
            }
            off += 1;
        }
        let Some(tpos) = tpos else {
            return Err(Error::runtime("definition thumbnail not found"));
        };
        let gap = self.r.raw(tpos - self.r.p)?;
        let n = gap.len();
        v.faces_camera = n >= 9 && gap[n - 9] & 1 != 0;
        v.shadows_face_sun = n >= 9 && gap[n - 9] & 2 != 0;
        // the last u32 before the thumbnail is 1 for the definition of a group
        // and 0 for a component's (what SketchUp's
        // SUComponentDefinitionGetType reports)
        v.is_group = n >= 4 && gap[n - 4..] == [1, 0, 0, 0];
        self.object(Some("CThumbnail"))?;
        Ok(())
    }

    pub fn entity_list(&mut self, n: u32, root: bool) -> Result<Vec<LegacyObject>> {
        self.burn_stack.push(0);
        self.inline_stack.push(Vec::new());
        let res = self.entity_list_inner(n, root);
        self.burn_stack.pop();
        self.inline_stack.pop();
        res
    }

    fn entity_list_inner(&mut self, n: u32, root: bool) -> Result<Vec<LegacyObject>> {
        // indexed, not held by reference: nested lists grow the stack
        let depth = self.inline_stack.len() - 1;
        let mut v: Vec<LegacyObject> = Vec::new();
        while v.len() < n as usize {
            let save = self.r.p;
            let d = self.r.d;
            let pending = &self.inline_stack[depth];
            if !pending.is_empty() && v.len() + pending.len() >= n as usize {
                // every item left is an instance an annotation already wrote
                // in full and the list does not list again (see below)
                match self.peek_backref() {
                    Some(next_ref) if pending.contains(&next_ref) => {}
                    _ => break,
                }
            }
            let has_burn_credit = !root && self.burn_stack.last().is_some_and(|&c| c > 0);
            if has_burn_credit
                && save + 25 <= d.len()
                && read_u32(d, save)? == 0
                && d[save + 22] == 255
                && d[save + 23] == 254
                && d[save + 24] == 255
            {
                // burned MapObject indices (see register_burn()) mean the
                // declared count includes phantom entities the stream never
                // carries; the definition tail signature (nrel=0 + pad +
                // 16-byte GUID + name marker at +22) marks the list's REAL end
                break;
            }
            match self.object(None) {
                Ok(o) => {
                    self.inline_stack[depth].retain(|&s| s != o.slot);
                    v.push(o);
                }
                Err(e) => {
                    if root {
                        // over-declared root counts run into the document
                        // tail - stop
                        self.r.p = save;
                        break;
                    }
                    if has_burn_credit {
                        // this list had burned MapObject indices: the phantom
                        // connection points were also counted as items, so the
                        // declared count overshoots the real records. Stop at
                        // the failed item - the definition tail that follows
                        // validates the cut.
                        self.r.p = save;
                        break;
                    }
                    return Err(e);
                }
            }
        }
        // An instance an annotation wrote in full is one of this list's items,
        // and the declared count holds it - but SketchUp does not always list
        // it again with a back-reference (labels anchored to the loudspeakers
        // of an auditorium: of five instances written in a definition's
        // labels, three were listed again, two not). Keep the ones never
        // listed, as the list's own entities.
        for &slot in &self.inline_stack[depth] {
            if v.len() >= n as usize {
                break;
            }
            let e = self
                .slots
                .get(&slot)
                .ok_or_else(|| Error::runtime("legacy backref to unknown slot"))?;
            v.push(LegacyObject {
                slot,
                name: e.name.clone(),
                v: e.v.clone(),
            });
        }
        Ok(v)
    }

    // True when the u16 at `at` can legally start an object read: a null, an
    // escape, a class definition, a class-ref to a KNOWN class, or an object
    // back-ref within the allocated range.
    fn plausible_list_tag(&self, at: usize) -> bool {
        let d = self.r.d;
        if at + 2 > d.len() {
            return false;
        }
        let t = u16::from_le_bytes([d[at], d[at + 1]]);
        if t == 0 || t == 0x7fff || t == 0xffff {
            return true;
        }
        if t & 0x8000 != 0 {
            return self
                .slots
                .get(&u64::from(t & 0x7fff))
                .is_some_and(|e| e.cls);
        }
        u64::from(t) < self.next
    }

    // SketchUp 2020's filler can appear at MORE call sites than the
    // count-only read this originally guarded - the same byte-stride search
    // as find_count_after_v20_filler, but each candidate must ALSO look like a
    // legal object-read start (plausible_list_tag) to reduce false positives.
    // On a hit the cursor moves just past the count.
    //
    // This only ever runs after a count came back implausible (or zero), so
    // files that were already parsing never reach it. `count_pos` is the
    // offset the count was read FROM.
    fn retry_count_after_v20_filler(&mut self, count_pos: usize, limit: u32) -> Option<u32> {
        let d = self.r.d;
        let marker_at = find_v20_filler_marker(d, count_pos)?;
        for pad in (1..=MAX_V20_FILLER_PAD).step_by(4) {
            let at = marker_at + 4 + pad;
            if at + 4 > d.len() {
                break;
            }
            let count = u32::from_le_bytes([d[at], d[at + 1], d[at + 2], d[at + 3]]);
            if count > 0 && count <= limit && self.plausible_list_tag(at + 4) {
                self.r.p = at + 4;
                return Some(count);
            }
        }
        None
    }
}

/// The finished walk: the archive (its slot table holds every object), the
/// root entity list, and the layers and materials in file order.
pub(crate) struct WalkResult<'a> {
    pub ar: Archive<'a>,
    pub root: Vec<LegacyObject>,
    pub layers: Vec<(u64, Value)>,
    pub materials: Vec<(u64, Option<Value>)>,
}

// Bootstrap the absolute slot base: parse material 1 with a throwaway
// archive; material 2's class-ref tag names CMaterial's true slot.
pub(crate) fn bootstrap_two_materials(data: &[u8], ver: i32, mat_hdr: usize) -> Result<u64> {
    let mut boot = Archive::new(data, ver);
    boot.next = 1 << 20;
    boot.base = 1 << 20;
    boot.r.p = mat_hdr;
    boot.object(Some("CMaterial"))?;
    let tag = read_u16(data, boot.r.p)?;
    if tag == 0xffff || tag & 0x8000 == 0 {
        return Err(Error::runtime("cannot bootstrap the slot base"));
    }
    Ok(u64::from(tag & 0x7fff))
}

// Slot-base candidates for files where the two-material trick is unavailable
// (0 or 1 materials).
//
// Parse the model prefix (materials, layer list) with a throwaway base; the
// object right after the layer list is the definition-list anchor - an
// ABSOLUTE back-ref to the active layer, an object we just allocated
// relatively. Each walked layer yields one candidate base; with a single
// layer (the common case) the answer is exact.
pub(crate) fn probe_layer_anchor_bases(
    data: &[u8],
    ver: i32,
    start: usize,
    mat_count: u32,
) -> Result<Vec<u64>> {
    let mut boot = Archive::new(data, ver);
    const B0: u64 = 1 << 20;
    boot.next = B0;
    boot.base = B0;
    boot.r.p = start;
    for _ in 0..mat_count {
        boot.object(Some("CMaterial"))?;
    }
    boot.r.u32()?;
    if ver >= 17 {
        boot.r.u8()?;
    }
    // SketchUp 3 writes no layer count: the layer records simply run until
    // the definition-list anchor, which collect_trailing_layers (below)
    // already stops at. Reading a count here would consume the first layer
    // record and reject every single-material V3 file.
    let mut layer_count = 0;
    if ver >= FIRST_V4 {
        layer_count = boot.r.u32()?;
        if !(1..=100_000).contains(&layer_count) {
            return Err(Error::runtime("implausible layer count in base probe"));
        }
    }
    let mut layer_slots = Vec::new();
    for _ in 0..layer_count {
        layer_slots.push(boot.object(Some("CLayer"))?.slot);
    }
    boot.collect_trailing_layers(Some(&mut layer_slots), None, true)?;
    let anchor = boot.object(None)?;
    if &*anchor.name != "premodel" {
        // under the throwaway base every absolute back-ref classifies as
        // premodel; anything else means the prefix did not parse
        return Err(Error::runtime(format!(
            "base probe: anchor resolved to {}",
            anchor.name
        )));
    }
    let s = anchor.slot as i64;
    Ok(layer_slots
        .into_iter()
        .map(|rel| s - (rel as i64 - B0 as i64))
        .filter(|&c| c > 0 && (c as u64) < B0)
        .map(|c| c as u64)
        .collect())
}

// Tries each candidate base in turn, returning the first one that walks
// cleanly. Every candidate but the right one dies almost immediately (the
// slot numbers it hands out disagree with the file's own class-refs), while
// the right one only fails where the file really differs from what we know.
// So when none parses, return the failure that got FURTHEST into the file -
// not the last candidate's, which is the earliest to fail and says nothing
// useful.
pub(crate) fn walk_with_bases<'a>(
    data: &'a [u8],
    ver: i32,
    start: usize,
    mat_count: u32,
    bases: &[u64],
) -> Result<WalkResult<'a>> {
    const MAX_BASE_CANDIDATES: usize = 1024;
    let mut best: Option<(Error, usize)> = None;
    for &base in bases.iter().take(MAX_BASE_CANDIDATES) {
        let mut ar = Archive::new(data, ver);
        match walk_model_on(&mut ar, data, ver, start, mat_count, base) {
            Ok((root, layers, materials)) => {
                return Ok(WalkResult {
                    ar,
                    root,
                    layers,
                    materials,
                })
            }
            Err(e) => {
                let at = ar.r.p;
                if best.as_ref().is_none_or(|(_, best_at)| at > *best_at) {
                    best = Some((e, at));
                }
            }
        }
    }
    Err(best.map_or_else(
        || Error::runtime("no viable slot base candidate"),
        |(e, _)| e,
    ))
}

// True when the bytes at `at` begin a CComponentDefinition record: its
// first-use class declaration, or a class-ref to its known slot.
fn definition_starts_at(ar: &Archive, at: usize) -> bool {
    let d = ar.r.d;
    if at + 26 <= d.len()
        && d[at] == 0xff
        && d[at + 1] == 0xff
        && &d[at + 6..at + 26] == b"CComponentDefinition"
    {
        return true;
    }
    ar.class_slot
        .get("CComponentDefinition")
        .is_some_and(|&cs| is_class_ref(d, at, cs))
}

type Walked = (
    Vec<LegacyObject>,
    Vec<(u64, Value)>,
    Vec<(u64, Option<Value>)>,
);

// The walk proper, on a caller-owned archive so the caller can report how far
// it got when it fails.
fn walk_model_on(
    ar: &mut Archive,
    data: &[u8],
    ver: i32,
    start: usize,
    mat_count: u32,
    base: u64,
) -> Result<Walked> {
    ar.next = base;
    ar.base = base;
    ar.r.p = start;
    let mut mats = Vec::new();
    let mut layers: Vec<(u64, Value)> = Vec::new();
    for _ in 0..mat_count {
        let q = ar.object(Some("CMaterial"))?;
        mats.push((q.slot, q.v));
    }
    ar.r.u32()?;
    if ver >= 17 {
        ar.r.u8()?;
    }
    // SketchUp 3 writes no layer count: the layer records simply run until the
    // definition-list anchor, which the loop below already stops at (a
    // back-ref, not a layer record).
    let lc: u32 = if ver < FIRST_V4 { 1 << 30 } else { ar.r.u32()? };
    if lc > 100_000 && ver >= FIRST_V4 {
        return Err(Error::runtime("invalid layer count"));
    }
    // lc counts REAL layers. SketchUp 2020 interleaves a null object-ref after
    // each layer record (a separator, not a layer), so count parsed layers
    // instead, skip the separators, and stop early if the next tag is a
    // back-ref (the definition-list anchor) - a v20 variant where the count
    // over-includes separators.
    while layers.len() < lc as usize {
        if ar.r.p + 2 > data.len() {
            break;
        }
        let tag = read_u16(data, ar.r.p)?;
        if tag == 0 {
            ar.r.p += 2;
            continue;
        }
        if tag != 0xffff && tag & 0x8000 == 0 {
            break;
        }
        let q = ar.object(Some("CLayer"))?;
        let Some(v) = q.v else { continue };
        layers.push((q.slot, v));
    }
    // trailing separators (and any layer records past the declared count)
    ar.collect_trailing_layers(None, Some(&mut layers), false)?;
    let anchor = ar.object(None)?;
    if &*anchor.name != "CLayer" {
        return Err(Error::runtime("definition anchor is not a layer"));
    }
    let mut dc = ar.r.u32()?;
    if dc > 1_000_000 {
        if let Some(retry) = ar.retry_count_after_v20_filler(ar.r.p - 4, 1_000_000) {
            dc = retry;
            if !definition_starts_at(ar, ar.r.p) {
                // A model with NO component definitions has no definition
                // count to find: SketchUp 2020 writes its filler where the
                // count would be, and the first count behind it is the ROOT
                // entity count. A real definition count is always followed by
                // a definition record; anything else means this is the root
                // count, so put it back.
                dc = 0;
                ar.r.p -= 4;
            }
        } else if let Some(root_count_at) =
            super::find_empty_root_after_v20_filler(data, ar.r.p - 4)
        {
            // No count behind the filler at all: an entirely empty model,
            // whose root count is 0 (the retry only accepts counts > 0).
            dc = 0;
            ar.r.p = root_count_at;
        }
    }
    if dc > 1_000_000 {
        return Err(Error::runtime("invalid definition count"));
    }
    for _ in 0..dc {
        ar.object(Some("CComponentDefinition"))?;
    }
    let cs = ar.class_slot.get("CComponentDefinition").copied();
    while ar.r.p + 2 <= data.len() {
        let p = ar.r.p;
        let t = read_u16(data, p)?;
        let mut yes = cs.is_some_and(|cs| u64::from(t) == (0x8000 | cs));
        if !yes && t == 0xffff && p + 26 <= data.len() {
            yes = &data[p + 6..p + 26] == b"CComponentDefinition";
        }
        if !yes {
            break;
        }
        ar.object(None)?;
    }
    let mut root_count = ar.r.u32()?;
    if root_count > 5_000_000 {
        if let Some(retry) = ar.retry_count_after_v20_filler(ar.r.p - 4, 5_000_000) {
            root_count = retry;
        }
    }
    if root_count > 5_000_000 {
        return Err(Error::runtime("implausible root entity count"));
    }
    let root = ar.entity_list(root_count, true)?;
    Ok((root, layers, mats))
}
