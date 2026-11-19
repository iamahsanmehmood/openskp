//! The VFF (2021+) reader: ZIP container, material/style XML, and the
//! model.dat record walk (`core.cpp`).

use std::collections::BTreeMap;
use std::sync::Arc;

use super::bytes::{parse_flat, parse_tlv_recursive, tag_at, u32_at};
use super::geometry::{
    collect_definitions, collect_geometry, collect_layers, collect_material_ids,
    collect_model_attribute_dictionaries,
};
use super::pages_dimensions::{
    find_page_node, parse_camera, parse_dimensions, parse_pages, scan_instance_transforms,
    scan_vertex_positions,
};
use super::strconv::{c_stod, c_stoi, c_stoll, c_stoul_digits};
use super::vff::{extract_version, is_legacy, read_meta_units, valid_header};
use super::xml::{attr, attributes, find_element, find_open_tag, find_screenimage};
use super::{text, RawMaterial, RawParsed, RawStyle, RawTexture, TlvNode};
use crate::errors::SkpParseError;
use crate::legacy::parse_legacy;
use crate::model::{StyleItem, StyleWatermark, Vec3, ViewCamera};
use crate::observability::{
    emit_log, emit_progress, LogLevel, ParseOptions, ParseStage, PROGRESS_INTERVAL,
};
use crate::zip::{ZipArchive, ZipFileStat};

// ── ZIP access ──────────────────────────────────────────────────────────

struct Zip<'a> {
    z: ZipArchive<'a>,
    /// Entry names (C strings, as miniz reports them), directories excluded.
    names: Vec<Vec<u8>>,
    archive_len: u64,
}

// A ZIP entry's declared uncompressed size is untrusted central-directory
// metadata - it can be set independently of what the compressed stream
// actually decompresses to, and even when genuine, DEFLATE can expand highly
// compressible data by three orders of magnitude. Real production model.dat
// entries are observed at ~10x compression, so both limits below leave
// generous headroom for legitimate files while rejecting the kind of
// declared-size lie or extreme ratio a genuine file would never need.
const MAX_UNCOMPRESSED_ENTRY_BYTES: u64 = 16 * 1024 * 1024 * 1024; // 16 GB
const MAX_COMPRESSION_RATIO: u64 = 1000;
const RATIO_CHECK_THRESHOLD_BYTES: u64 = 1024 * 1024; // 1 MB

fn validate_entry_size(s: &ZipFileStat, archive_len: u64) -> Result<(), SkpParseError> {
    let declared = s.uncomp_size;
    if declared == 0 {
        return Ok(());
    }
    let name = s.filename_str();
    if declared > usize::MAX as u64 {
        return Err(SkpParseError::at_stage(
            format!(
                "ZIP entry '{name}' declares {declared} bytes, exceeding addressable memory limits"
            ),
            ParseStage::ZipExtract,
        ));
    }
    if declared > MAX_UNCOMPRESSED_ENTRY_BYTES {
        return Err(SkpParseError::at_stage(
            format!(
                "ZIP entry '{name}' declares {declared} bytes uncompressed, exceeding the \
                 {MAX_UNCOMPRESSED_ENTRY_BYTES}-byte safety ceiling"
            ),
            ParseStage::ZipExtract,
        ));
    }
    if declared >= RATIO_CHECK_THRESHOLD_BYTES {
        let compressed = s.comp_size.min(archive_len);
        if compressed == 0 || declared / compressed > MAX_COMPRESSION_RATIO {
            return Err(SkpParseError::at_stage(
                format!(
                    "ZIP entry '{name}' declares an implausible compression ratio ({declared} bytes from \
                     {} bytes compressed) - likely a decompression bomb",
                    s.comp_size
                ),
                ParseStage::ZipExtract,
            ));
        }
    }
    Ok(())
}

impl<'a> Zip<'a> {
    fn new(d: &'a [u8]) -> Result<Self, SkpParseError> {
        let z = ZipArchive::new(d).map_err(|e| {
            SkpParseError::at_stage("Invalid ZIP container", ParseStage::ZipExtract).with_cause(e)
        })?;
        let mut names = Vec::new();
        for i in 0..z.len() as u32 {
            if let Ok(s) = z.stat(i) {
                if !s.is_directory {
                    names.push(s.filename);
                }
            }
        }
        Ok(Zip {
            z,
            names,
            archive_len: d.len() as u64,
        })
    }

    /// The entry's bytes, `None` when it is missing or fails to extract; an
    /// error when its declared size fails the safety checks.
    fn get(&self, name: &[u8]) -> Result<Option<Vec<u8>>, SkpParseError> {
        let Some(i) = self.z.locate(name) else {
            return Ok(None);
        };
        let Ok(s) = self.z.stat(i) else {
            return Ok(None);
        };
        validate_entry_size(&s, self.archive_len)?;
        Ok(self.z.extract(i).ok())
    }
}

fn zip_offset(d: &[u8]) -> Option<usize> {
    let mut i = 0;
    while i + 4 <= d.len() && i < 4096 {
        if d[i] == b'P' && d[i + 1] == b'K' && d[i + 2] == 3 && d[i + 3] == 4 {
            return Some(i);
        }
        i += 1;
    }
    None
}

#[derive(Clone, Copy)]
struct Header {
    offset: usize,
    size: usize,
}

fn headers(d: &[u8], start: usize, end: usize) -> Vec<Header> {
    let mut h = Vec::new();
    let mut p = start;
    while p + 6 <= end {
        let n = u32_at(d, p + 2) as usize;
        if n > end - p - 6 {
            break;
        }
        h.push(Header { offset: p, size: n });
        p += 6 + n;
    }
    h
}

fn integer(s: &str, fallback: i32) -> i32 {
    if s.is_empty() {
        fallback
    } else {
        c_stoi(s).unwrap_or(fallback)
    }
}

fn number(s: &str, fallback: f64) -> f64 {
    if s.is_empty() {
        fallback
    } else {
        c_stod(s).unwrap_or(fallback)
    }
}

fn basename(s: &[u8]) -> Vec<u8> {
    match s.iter().rposition(|&c| c == b'/') {
        Some(p) => s[p + 1..].to_vec(),
        None => s.to_vec(),
    }
}

fn concat(parts: &[&[u8]]) -> Vec<u8> {
    parts.concat()
}

// ── XML entities ────────────────────────────────────────────────────────

/// Decodes the 5 predefined XML entities plus numeric character references
/// (`&#39;`, `&#x27;`) in a raw attribute value extracted without a real XML
/// parser. A real material name legitimately contains "<"/">" - SketchUp's own
/// "&lt;auto&gt;" default-material naming - so without this such names come
/// through still escaped. A single left-to-right pass, not five sequential
/// replacements: sequential replacement would double-decode "&amp;lt;" into
/// "<" instead of the correct "&lt;".
pub fn decode_xml_entities(value: &str) -> String {
    text(&decode_xml_entities_bytes(value.as_bytes()))
}

/// [`decode_xml_entities`] on raw bytes. Numeric references are UTF-8 encoded
/// with the C++'s byte arithmetic (a 64-bit `unsigned long`); one that
/// overflows is left as-is.
pub fn decode_xml_entities_bytes(v: &[u8]) -> Vec<u8> {
    let mut out = Vec::with_capacity(v.len());
    let mut i = 0;
    while i < v.len() {
        if v[i] != b'&' {
            out.push(v[i]);
            i += 1;
            continue;
        }
        // &(lt|gt|amp|apos|quot|#[0-9]+|#[xX][0-9a-fA-F]+);
        let rest = &v[i + 1..];
        let named: [(&[u8], u8); 5] = [
            (b"lt", b'<'),
            (b"gt", b'>'),
            (b"amp", b'&'),
            (b"apos", b'\''),
            (b"quot", b'"'),
        ];
        if let Some(&(name, ch)) = named
            .iter()
            .find(|(n, _)| rest.starts_with(n) && rest.get(n.len()) == Some(&b';'))
        {
            out.push(ch);
            i += 1 + name.len() + 1;
            continue;
        }
        if rest.first() == Some(&b'#') {
            let (digits_at, hex) = match rest.get(1) {
                Some(b'x' | b'X') => (2, true),
                _ => (1, false),
            };
            let mut j = digits_at;
            while j < rest.len()
                && if hex {
                    rest[j].is_ascii_hexdigit()
                } else {
                    rest[j].is_ascii_digit()
                }
            {
                j += 1;
            }
            if j > digits_at && rest.get(j) == Some(&b';') {
                let whole = &v[i..i + 1 + j + 1];
                match c_stoul_digits(&rest[digits_at..j], if hex { 16 } else { 10 }) {
                    Some(cp) => encode_utf8_like_cpp(cp, &mut out),
                    None => out.extend_from_slice(whole), // malformed numeric reference - left as-is
                }
                i += 1 + j + 1;
                continue;
            }
        }
        out.push(b'&');
        i += 1;
    }
    out
}

fn encode_utf8_like_cpp(cp: u64, out: &mut Vec<u8>) {
    if cp <= 0x7F {
        out.push(cp as u8);
    } else if cp <= 0x7FF {
        out.push((0xC0 | (cp >> 6)) as u8);
        out.push((0x80 | (cp & 0x3F)) as u8);
    } else if cp <= 0xFFFF {
        out.push((0xE0 | (cp >> 12)) as u8);
        out.push((0x80 | ((cp >> 6) & 0x3F)) as u8);
        out.push((0x80 | (cp & 0x3F)) as u8);
    } else {
        out.push((0xF0 | (cp >> 18)) as u8);
        out.push((0x80 | ((cp >> 12) & 0x3F)) as u8);
        out.push((0x80 | ((cp >> 6) & 0x3F)) as u8);
        out.push((0x80 | (cp & 0x3F)) as u8);
    }
}

// ── materials ───────────────────────────────────────────────────────────

fn material_xml(zip: &Zip, path: &[u8], xml: &[u8]) -> Result<Option<RawMaterial>, SkpParseError> {
    let Some(tag) = find_open_tag(xml, 0, "material", true) else {
        return Ok(None);
    };
    let a = &xml[tag.attrs];
    let mut out = RawMaterial {
        name: attr(a, "name"),
        ..Default::default()
    };
    if out.name.is_empty() {
        out.name = "unknown".into();
    }
    out.r = integer(&attr(a, "colorRed"), 128);
    out.g = integer(&attr(a, "colorGreen"), 128);
    out.b = integer(&attr(a, "colorBlue"), 128);
    if attr(a, "useTrans") == "1" {
        out.transparency = (1.0 - number(&attr(a, "trans"), 0.0)).clamp(0.0, 1.0);
    }
    out.colorized = attr(a, "type") == "2";
    out.colorize_type = integer(&attr(a, "colorizeType"), 0);
    if let Some(tt) = find_open_tag(xml, 0, "texture", true) {
        let ta = &xml[tt.attrs];
        let mut t = RawTexture {
            filename: attr(ta, "textureFilename"),
            x_scale: number(&attr(ta, "xScale"), 0.0),
            y_scale: number(&attr(ta, "yScale"), 0.0),
            data: None,
        };
        let folder: &[u8] = match path.iter().rposition(|&c| c == b'/') {
            Some(s) => &path[..s],
            None => b"",
        };
        let candidate = concat(&[folder, b"/", t.filename.as_bytes()]);
        if !t.filename.is_empty() {
            t.data = zip.get(&candidate)?;
        }
        if t.data.is_none() {
            let prefix = concat(&[folder, b"/"]);
            for n in &zip.names {
                if n.starts_with(&prefix)
                    && n.as_slice() != path
                    && n.len() > 4
                    && &n[n.len() - 4..] != b".xml"
                {
                    t.data = zip.get(n)?;
                    if t.filename.is_empty() {
                        t.filename = text(&basename(n));
                    }
                    break;
                }
            }
        }
        if t.data.is_none() {
            if let Some(im) = find_open_tag(xml, 0, "image", false) {
                let ip = attr(&xml[im.attrs], "path");
                let ip = ip.trim_start_matches(['.', '/']).as_bytes();
                for c in [ip.to_vec(), concat(&[folder, b"/", ip])] {
                    if !c.is_empty() {
                        t.data = zip.get(&c)?;
                        if t.data.is_some() {
                            if t.filename.is_empty() {
                                t.filename = text(&basename(&c));
                            }
                            break;
                        }
                    }
                }
            }
        }
        out.texture = Some(t);
    }
    Ok(Some(out))
}

// ── styles ──────────────────────────────────────────────────────────────

/// Watermarks of style.xml item 5001: `<screenimage>` entries, each naming its
/// image inside the SKP ZIP. Image bytes are filled in by the caller.
fn style_watermarks(wmlist: &[u8]) -> Vec<StyleWatermark> {
    let mut out = Vec::new();
    let mut from = 0;
    while let Some(m) = find_screenimage(wmlist, from) {
        from = m.end;
        let a = &wmlist[m.attrs.clone()];
        let mut w = StyleWatermark {
            attributes: attributes(a),
            ..Default::default()
        };
        w.attributes.remove("name");
        w.name = attr(a, "name");
        if w.name == "<MODEL SPACE>" {
            continue; // separator between under- and overlays
        }
        let body = m.body.map(|b| &wmlist[b]).unwrap_or(&[]);
        if let Some(im) = find_open_tag(body, 0, "image", true) {
            let ia = &body[im.attrs];
            w.image_path = attr(ia, "path");
            w.file_name = attr(ia, "file_name");
        }
        out.push(w);
    }
    out
}

fn style_xml(xml: &[u8]) -> Option<RawStyle> {
    let st = find_open_tag(xml, 0, "style", true)?;
    let sa = &xml[st.attrs];
    let mut o = RawStyle {
        name: attr(sa, "name"),
        description: attr(sa, "desc"),
        ..Default::default()
    };
    let mut from = 0;
    while let Some(item) = find_element(xml, from, "item") {
        from = item.end;
        let id = integer(&attr(&xml[item.attrs.clone()], "id"), -1);
        if id < 0 {
            continue;
        }
        let body = item.body.map(|b| &xml[b]).unwrap_or(&[]);
        let Some(v) = find_element(body, 0, "variant") else {
            continue;
        };
        let raw = v.body.map(|b| &body[b]).unwrap_or(&[]);
        let trimmed = trim_ws(raw);
        let it = StyleItem {
            r#type: integer(&attr(&body[v.attrs], "type"), 0),
            value: text(trimmed),
        };
        // Faces: 2002 front, 2003 back, as signed-int32 ABGR (R in the low
        // byte); variant type 4 in current SketchUp, 5 in older files. The
        // 4000-series items are background/sky/ground, not face colors.
        if id == 2002 || id == 2003 {
            if let Some(n) = c_stoll(&it.value) {
                let n = n as u32;
                let c = [n as u8, (n >> 8) as u8, (n >> 16) as u8];
                if id == 2002 {
                    o.front_color = Some(c);
                } else {
                    o.back_color = Some(c);
                }
            }
        }
        o.items.insert(id, it);
    }
    if let Some(wm) = o.items.get(&5001) {
        o.watermarks = style_watermarks(wm.value.as_bytes());
    }
    Some(o)
}

fn trim_ws(s: &[u8]) -> &[u8] {
    let ws = |c: &u8| matches!(c, b' ' | b'\t' | b'\r' | b'\n');
    let Some(first) = s.iter().position(|c| !ws(c)) else {
        return &[];
    };
    let last = s.iter().rposition(|c| !ws(c)).unwrap_or(first);
    &s[first..=last]
}

/// Style catalog (model.dat record 0602 > 7869). 7969 lists the model's
/// styles as 6C6B entries (DC05 > DE05 entity id, 6F6B name = the style's
/// folder under styles/), 7A69 holds the current style's id, 7B69 the
/// current style's working copy (its own 6C6B, folder "<name>_1"), and 7C69
/// is 1 when the current style was edited without updating it.
#[derive(Default)]
struct CurrentStyle {
    folder: String,
    working_copy: String,
    modified: bool,
}

fn current_style(catalog: &[u8]) -> CurrentStyle {
    fn field<'a>(entry: &'a [u8], tag: &str) -> Option<&'a [u8]> {
        parse_flat(entry)
            .into_iter()
            .find(|(t, _)| *t == tag)
            .map(|(_, v)| v)
    }
    let mut listed: Vec<(&[u8], String)> = Vec::new();
    let mut current_id: Option<&[u8]> = None;
    let mut out = CurrentStyle::default();
    for (tag, value) in parse_flat(catalog) {
        if tag == "7969" {
            for (t, entry) in parse_flat(value) {
                if t != "6C6B" {
                    continue;
                }
                let name = field(entry, "6F6B");
                let id = field(entry, "DC05").and_then(|ids| field(ids, "DE05"));
                if let (Some(name), Some(id)) = (name, id) {
                    listed.push((id, text(name)));
                }
            }
        } else if tag == "7A69" {
            current_id = Some(value);
        } else if tag == "7C69" {
            out.modified = !value.is_empty() && value[0] != 0;
        } else if tag == "7B69" {
            for (t, entry) in parse_flat(value) {
                if t == "6C6B" {
                    if let Some(name) = field(entry, "6F6B") {
                        out.working_copy = text(name);
                    }
                }
            }
        }
    }
    for (id, name) in listed {
        if current_id == Some(id) {
            out.folder = name;
        }
    }
    out
}

/// The model's current view (FA01 > 34BC); unset without eye and target.
fn view_camera(record: &[u8]) -> Option<ViewCamera> {
    let raw = parse_camera(record);
    let (eye, target) = (raw.eye?, raw.target?);
    let mut cam = ViewCamera {
        eye,
        target,
        ..Default::default()
    };
    if let Some(up) = raw.up {
        cam.up = up;
    }
    cam.fov = raw.fov;
    cam.parallel = raw.parallel;
    cam.ortho_height = raw.ortho_height;
    Some(cam)
}

/// VFF model.dat wraps the file's definition list inside container tags
/// F901 -> 7017 -> 7117 -> 7C15. Unwrapping it into individual 7C15 headers
/// upfront bounds memory to one definition at a time (Python's
/// `_unwrap_definitions_container`, Issue #264).
fn unwrap_definitions_container(data: &[u8], offset: usize, size: usize) -> Vec<Header> {
    let mut level = headers(data, offset + 6, offset + 6 + size);
    for expected in ["7017", "7117"] {
        let Some(h) = level
            .iter()
            .find(|h| tag_is(data, h.offset, expected))
            .copied()
        else {
            return Vec::new();
        };
        level = headers(data, h.offset + 6, h.offset + 6 + h.size);
    }
    level
        .into_iter()
        .filter(|h| tag_is(data, h.offset, "7C15"))
        .collect()
}

fn tag_is(data: &[u8], offset: usize, tag: &str) -> bool {
    tag_at(data, offset).is_ok_and(|t| t == tag)
}

// ── the parse ───────────────────────────────────────────────────────────

/// Reads a whole .skp buffer: legacy (pre-2021) files go to the legacy reader,
/// VFF files are read here.
pub fn full_parse(data: &[u8], o: &ParseOptions) -> Result<RawParsed, SkpParseError> {
    emit_log(
        o,
        LogLevel::Information,
        &format!("Parsing buffer ({} bytes)", data.len()),
    );
    if !valid_header(data) {
        return Err(SkpParseError::at_stage(
            "Not a valid SketchUp file (bad header magic)",
            ParseStage::Header,
        ));
    }
    if is_legacy(data) {
        emit_log(
            o,
            LogLevel::Debug,
            "Detected legacy MFC container; routing to legacy walker",
        );
        return parse_legacy(data, o);
    }
    let mut p = RawParsed {
        version: extract_version(data),
        ..Default::default()
    };
    let Some(off) = zip_offset(data) else {
        return Err(SkpParseError::at_stage(
            "No ZIP container found",
            ParseStage::ZipExtract,
        ));
    };
    let zip = Zip::new(&data[off..])?;

    for n in &zip.names {
        if !(n.starts_with(b"materials/") && n.len() >= 12 && n.ends_with(b"material.xml")) {
            continue;
        }
        let Some(b) = zip.get(n)? else { continue };
        let Some(m) = material_xml(&zip, n, &b)? else {
            continue;
        };
        let folder_end = n[10..]
            .iter()
            .position(|&c| c == b'/')
            .map(|k| 10 + k)
            .unwrap_or(n.len());
        let folder = text(&n[10..folder_end]);
        let layer_name = m.name.strip_prefix("Layer_").map(str::to_owned);
        let color = [m.r as u8, m.g as u8, m.b as u8];
        let m = Arc::new(m);
        p.materials.insert(m.name.clone(), Arc::clone(&m));
        p.materials_by_folder.insert(folder, m);
        if let Some(layer_name) = layer_name {
            if !p.layer_colors.contains_key(&layer_name) {
                p.layer_order.push(layer_name.clone());
            }
            p.layer_colors.insert(layer_name.clone(), color);
            p.layer_hidden.insert(layer_name, false);
        }
    }

    for n in &zip.names {
        if !(n.starts_with(b"styles/") && n.len() >= 9 && n.ends_with(b"style.xml")) {
            continue;
        }
        let Some(b) = zip.get(n)? else { continue };
        let Some(mut s) = style_xml(&b) else { continue };
        if n.len() > 17 {
            s.folder = text(&n[7..n.len() - 10]); // styles/<folder>/style.xml
        }
        // A style's own images sit in its folder ("./2.jpg"); the current
        // style's watermark images are at the ZIP root
        // ("watermarks/Watermark1.jpg").
        for w in &mut s.watermarks {
            let mut path = w.image_path.as_str();
            while let Some(rest) = path.strip_prefix("./") {
                path = rest;
            }
            let path = path.trim_start_matches('/');
            if path.is_empty() {
                continue;
            }
            for candidate in [format!("styles/{}/{}", s.folder, path), path.to_owned()] {
                w.image = zip.get(candidate.as_bytes())?;
                if w.image.is_some() {
                    break;
                }
            }
        }
        p.styles.push(s);
    }

    let Some(model) = zip.get(b"model.dat")? else {
        return Err(SkpParseError::at_stage(
            "model.dat not found in ZIP container",
            ParseStage::ZipExtract,
        ));
    };
    let mut hs = headers(&model, 0, model.len());
    if hs.len() == 1 && model[hs[0].offset] == 0xf4 && model[hs[0].offset + 1] == 1 {
        hs = headers(&model, hs[0].offset + 6, hs[0].offset + 6 + hs[0].size);
    }

    let mut expanded = Vec::with_capacity(hs.len());
    for h in hs {
        if tag_is(&model, h.offset, "F901") {
            let defs = unwrap_definitions_container(&model, h.offset, h.size);
            if !defs.is_empty() {
                expanded.extend(defs);
                continue;
            }
        }
        expanded.push(h);
    }
    let hs = expanded;

    let mut vertex_positions: BTreeMap<String, Vec3> = BTreeMap::new();
    let mut instance_world: BTreeMap<String, Vec<f64>> = BTreeMap::new();
    let mut page_node: Option<TlvNode> = None;
    let mut current = CurrentStyle::default();
    let total = hs.len() as u64;

    for (i, h) in hs.iter().enumerate() {
        let i = i as u64;
        let mut tag = String::new();
        let step = (|| -> Result<bool, super::Error> {
            let one = parse_tlv_recursive(&model, h.offset, h.offset + 6 + h.size)?;
            let Some(first) = one.first() else {
                return Ok(false);
            };
            tag = first.tag.as_str().to_owned();
            // The model's current view: FA01 > 34BC.
            if first.tag == "FA01" {
                for (t, record) in parse_flat(&first.payload) {
                    if t == "BC34" {
                        p.camera = view_camera(record);
                    }
                }
            }
            if first.tag == "0602" {
                for (t, catalog) in parse_flat(&first.payload) {
                    if t == "7869" {
                        current = current_style(catalog);
                    }
                }
            }
            collect_layers(&one, &mut p.layer_id_to_name, &mut p.layer_hidden)?;
            collect_material_ids(&one, &mut p.material_id_to_name)?;
            collect_definitions(&one, &mut p.definitions)?;
            scan_vertex_positions(first, &mut vertex_positions);
            scan_instance_transforms(first, &mut instance_world);
            if page_node.is_none() {
                page_node = find_page_node(first).cloned();
            }
            if first.tag == "F601" {
                collect_geometry(&first.children, &mut p.root.builder)?;
                collect_model_attribute_dictionaries(first, &mut p.model_attribute_dicts);
            }
            Ok(true)
        })();
        match step {
            Ok(true) => {}
            Ok(false) => {
                emit_log(
                    o,
                    LogLevel::Debug,
                    &format!("Failed to parse record at offset {}", h.offset),
                );
                continue;
            }
            Err(e) => {
                return Err(SkpParseError::with_context(
                    "Failed while processing record",
                    Some(ParseStage::TlvWalk),
                    Some(i),
                    Some(total),
                    Some(tag),
                    Some(h.offset as u64),
                    None,
                    Some(Arc::new(e)),
                ));
            }
        }
        if i.is_multiple_of(PROGRESS_INTERVAL) || i + 1 == total {
            emit_progress(o, ParseStage::TlvWalk, i + 1, total);
        }
    }
    for s in &mut p.styles {
        s.active = !current.folder.is_empty() && s.folder == current.folder;
        s.modified = s.active && current.modified;
        s.working_copy = !current.working_copy.is_empty() && s.folder == current.working_copy;
    }
    // Units (meta/meta.dat) - VFF-only; legacy files carry no equivalent.
    match zip.get(b"meta/meta.dat") {
        Ok(Some(meta)) => p.units = read_meta_units(&meta),
        Ok(None) => {}
        Err(_) => {
            p.units = None;
            emit_log(
                o,
                LogLevel::Debug,
                "Failed to read units from meta/meta.dat",
            );
        }
    }
    match parse_pages(page_node.as_ref()) {
        Ok(pages) => p.pages = pages,
        Err(_) => emit_log(o, LogLevel::Debug, "Failed to parse pages"),
    }
    if !vertex_positions.is_empty() {
        p.dimensions = parse_dimensions(&model, &vertex_positions, &instance_world);
    }
    drop(model);
    p.layer_id_to_name
        .entry(1)
        .or_insert_with(|| "Layer0".into());
    if !p.layer_colors.contains_key("Layer0") {
        p.layer_order.push("Layer0".into());
        p.layer_colors.insert("Layer0".into(), [136, 136, 136]);
    }
    p.layer_hidden.entry("Layer0".into()).or_insert(false);
    emit_log(
        o,
        LogLevel::Information,
        &format!("Parse complete: {} defs", p.definitions.len()),
    );
    Ok(p)
}
