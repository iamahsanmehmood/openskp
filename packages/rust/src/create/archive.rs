//! `ArchiveWriter` - the write-side mirror of the legacy reader's MFC `CArchive` slot/class-ref
//! bookkeeping, plus every entity record encoder the builder uses.

use std::collections::hash_map::RandomState;
use std::collections::HashMap;
use std::hash::{BuildHasher, Hasher};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};

use super::geometry::{
    cross3, normalize3, plane_from_polygon, plane_tolerance, uv_matrix_for_face,
};
use super::scaffold_blank_v17::{ATTRIBUTE_NAMED_SLOT, ATTR_CONTAINER_SLOT, CCAMERA_SLOT};
use super::{
    AttributeDict, AttributeValue, Color4, GuideOptions, Matrix3x3, Point3, SkpWriteError, UvPoint,
};

// ---------------------------------------------------------------------------------------------
// Class schema versions (ground truth from real SketchUp files unless noted).
// ---------------------------------------------------------------------------------------------

const LAYER_SCHEMA: u16 = 3;
const MATERIAL_SCHEMA: u16 = 12;
const DIB_SCHEMA: u16 = 3;
const DEFINITION_SCHEMA: u16 = 11;
const INSTANCE_SCHEMA: u16 = 6;
const GROUP_SCHEMA: u16 = 1;
/// UNVERIFIED - no real SketchUp-authored CImage sample was available. The reader never branches
/// on it, so this only affects whether real SketchUp accepts the file; it matches
/// `INSTANCE_SCHEMA` because CImage's read path always expects the trailing GUID, the same
/// "always present" shape CComponentInstance has.
const IMAGE_SCHEMA: u16 = 6;
const THUMBNAIL_SCHEMA: u16 = 1;
const FTC_SCHEMA: u16 = 4;
const ARC_CURVE_SCHEMA: u16 = 3;
const CCURVE_SCHEMA: u16 = 4;
const SECTION_PLANE_SCHEMA: u16 = 3;
const DIMENSION_LINEAR_SCHEMA: u16 = 6;
const SK_FONT_SCHEMA: u16 = 1;
const TEXT_SCHEMA: u16 = 9;
const CONSTRUCTION_LINE_SCHEMA: u16 = 1;
const CONSTRUCTION_POINT_SCHEMA: u16 = 0;

/// The start/end distance parameter of a construction line unbounded on that side - real
/// SketchUp's own sentinel (`ConstructionLine#start`/`#end` return nil for it).
const CLINE_INFINITE: f64 = 1e30;

const ATTR_TYPE_INT32: u8 = 0x04;
const ATTR_TYPE_DOUBLE: u8 = 0x06;
const ATTR_TYPE_BOOL: u8 = 0x07;
const ATTR_TYPE_STRING: u8 = 0x0A;
const ATTR_TYPE_LENGTH: u8 = 0x0C;

/// A definition record's 22-byte "base block" (after its preamble, before the embedded layer
/// list): all zero except offsets 3-4, the same 1,1 padding convention drawbase uses.
const DEFINITION_BASE_BLOCK: [u8; 22] = [
    0, 0, 0, 1, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
];

/// The 176 bytes (after CCamera's 2-byte class-ref tag) real SketchUp writes for a definition's
/// default thumbnail camera - copied verbatim, not decoded.
const CAMERA_TEMPLATE: [u8; 176] = [
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 240, 63, 0, 0, //
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 64, 0, 0, //
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 240, 63, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, //
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 62, 64, 0, 0, 0, 0, 0, 0, 240, //
    63, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, //
    0, 0, 0, 0, 0, 0, 0, 1, 0, 255, 254, 255, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, //
    0, 0, 240, 63, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
];

/// CSkFont payload harvested from a real SketchUp 2017 file (see
/// docs/dimension-record-notes.md): null attrs + pid mask 0, "Tahoma", flags, f64 height.
const DIM_FONT_PAYLOAD: [u8; 34] = [
    0x00, 0x00, 0x00, // preamble: null attrs + pid mask 0
    0xff, 0xfe, 0xff, 0x06, b'T', 0, b'a', 0, b'h', 0, b'o', 0, b'm', 0, b'a', 0, // "Tahoma"
    0x00, 0x00, //
    0x08, 0x00, 0x00, 0x00, //
    0x00, //
    0xec, 0xf5, 0x7a, 0xbd, 0x5e, 0xaf, 0x23, 0x40, // height f64
];

/// Leader-text delimiter block: [u32 1][u8 flag=1][u8 0][u32 ARROW=3 closed][u8 1].
const TEXT_DELIM: [u8; 11] = [
    0x01, 0x00, 0x00, 0x00, 0x01, 0x00, 0x03, 0x00, 0x00, 0x00, 0x01,
];

/// A face with no explicit texture positioning stores no CFaceTextureCoords at all, so this is
/// only used to fill the *other* side's slot when just one side is positioned.
const IDENTITY_UV_MATRIX: Matrix3x3 = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0];
const IDENTITY_MATRIX: Matrix3x3 = IDENTITY_UV_MATRIX;

// ---------------------------------------------------------------------------------------------
// Byte helpers.
// ---------------------------------------------------------------------------------------------

/// Little-endian appends onto a byte buffer.
pub(super) trait PutLe {
    fn put_u16(&mut self, v: u16);
    fn put_u32(&mut self, v: u32);
    fn put_f64(&mut self, v: f64);
    fn put_zeros(&mut self, n: usize);
}

impl PutLe for Vec<u8> {
    fn put_u16(&mut self, v: u16) {
        self.extend_from_slice(&v.to_le_bytes());
    }
    fn put_u32(&mut self, v: u32) {
        self.extend_from_slice(&v.to_le_bytes());
    }
    fn put_f64(&mut self, v: f64) {
        self.extend_from_slice(&v.to_le_bytes());
    }
    fn put_zeros(&mut self, n: usize) {
        self.resize(self.len() + n, 0);
    }
}

pub(super) fn read_u16_at(buf: &[u8], pos: usize) -> u16 {
    u16::from_le_bytes([buf[pos], buf[pos + 1]])
}

pub(super) fn write_u16_at(buf: &mut [u8], pos: usize, v: u16) {
    buf[pos..pos + 2].copy_from_slice(&v.to_le_bytes());
}

pub(super) fn write_u32_at(buf: &mut [u8], pos: usize, v: u32) {
    buf[pos..pos + 4].copy_from_slice(&v.to_le_bytes());
}

/// Renumber the u16 slot reference at `pos` by `shift`, keeping its 0x8000 class-ref tag bit.
/// Widens to the 6-byte escape form if the new slot lands at or past 0x7FFF: 0x7FFF is the
/// archive's big-tag escape marker and 0x8000|0x7FFF == 0xFFFF its new-class marker, so neither
/// can be an ordinary slot number. Returns how many bytes the field grew by (0 or 4).
pub(super) fn shift_ref(buf: &mut Vec<u8>, pos: usize, shift: i32) -> usize {
    let raw = read_u16_at(buf, pos);
    let tag_bit = raw & 0x8000;
    let new_slot = i32::from(raw & 0x7FFF) + shift;
    if new_slot < 0x7FFF {
        write_u16_at(buf, pos, tag_bit | new_slot as u16);
        return 0;
    }
    let val = if tag_bit != 0 {
        0x8000_0000 | new_slot as u32
    } else {
        new_slot as u32
    };
    let mut replacement = vec![0xFF, 0x7F];
    replacement.extend_from_slice(&val.to_le_bytes());
    buf.splice(pos..pos + 2, replacement);
    4
}

/// CDib's image format tag from the file's own magic bytes: 4 for PNG, 1 for JPEG.
pub(super) fn detect_image_subtype(image_bytes: &[u8]) -> Result<u32, SkpWriteError> {
    const PNG_SIG: [u8; 8] = [0x89, b'P', b'N', b'G', b'\r', b'\n', 0x1a, b'\n'];
    if image_bytes.starts_with(&PNG_SIG) {
        Ok(4)
    } else if image_bytes.starts_with(&[0xFF, 0xD8, 0xFF]) {
        Ok(1)
    } else {
        Err(SkpWriteError::new(
            "unrecognized image format - only PNG and JPEG textures are supported for now \
             (detected from the file's own magic bytes, not its extension)",
        ))
    }
}

// ---------------------------------------------------------------------------------------------
// GUIDs and timestamps.
// ---------------------------------------------------------------------------------------------

/// Where definition/instance GUIDs and definition timestamps come from.
#[derive(Clone, Debug)]
pub(super) enum IdSource {
    /// Random v4 GUIDs and the current time - what real files get.
    Random,
    /// Reproducible output for byte-exact tests: GUID `n` is bytes `n*16 + i` (with the v4
    /// version/variant bits applied) and every timestamp is fixed.
    Deterministic { next: u32, timestamp: u32 },
}

impl IdSource {
    pub fn guid(&mut self) -> [u8; 16] {
        let mut b = [0u8; 16];
        match self {
            IdSource::Random => {
                b[..8].copy_from_slice(&random_u64().to_le_bytes());
                b[8..].copy_from_slice(&random_u64().to_le_bytes());
            }
            IdSource::Deterministic { next, .. } => {
                let n = *next;
                *next = next.wrapping_add(1);
                for (i, byte) in (0u32..).zip(b.iter_mut()) {
                    *byte = (n.wrapping_mul(16).wrapping_add(i) & 0xFF) as u8;
                }
            }
        }
        b[6] = (b[6] & 0x0F) | 0x40; // RFC 4122 version 4
        b[8] = (b[8] & 0x3F) | 0x80; // RFC 4122 variant 10
        b
    }

    pub fn timestamp(&self) -> u32 {
        match self {
            IdSource::Random => SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .map_or(0, |d| d.as_secs() as u32),
            IdSource::Deterministic { timestamp, .. } => *timestamp,
        }
    }
}

/// 64 random bits from std alone: SipHash keyed by `RandomState`'s per-process random keys, over
/// a process-wide counter and the current time.
fn random_u64() -> u64 {
    static COUNTER: AtomicU64 = AtomicU64::new(0);
    let mut hasher = RandomState::new().build_hasher();
    hasher.write_u64(COUNTER.fetch_add(1, Ordering::Relaxed));
    if let Ok(now) = SystemTime::now().duration_since(UNIX_EPOCH) {
        hasher.write_u128(now.as_nanos());
    }
    hasher.finish()
}

// ---------------------------------------------------------------------------------------------
// Shared write-side state and parameter bundles.
// ---------------------------------------------------------------------------------------------

/// A named attribute dictionary, in write order.
pub(super) type AttributeDictList = Vec<(String, AttributeDict)>;

/// Exact-coordinate key for vertex sharing. `-0.0` and `0.0` are one key, matching the C++
/// `std::map<Point3, int>` (which compares with `<`).
fn point_key(p: Point3) -> [u64; 3] {
    p.map(|v| if v == 0.0 { 0 } else { v.to_bits() })
}

/// The vertex/edge sharing state of one entity list (the root model or one definition).
#[derive(Debug, Default)]
pub(super) struct Topology {
    vertex_slots: HashMap<[u64; 3], i32>,
    /// (min vertex slot, max vertex slot) -> (edge slot, the edge's own first vertex slot).
    edges: HashMap<(i32, i32), (i32, i32)>,
}

#[derive(Clone, Copy, Debug, Default)]
pub(super) struct EdgeFlags {
    pub hidden: bool,
    pub soft: bool,
    pub smooth: bool,
}

/// Curve parameters shared by every edge of a circle/arc - see `write_arc_curve`.
#[derive(Clone, Copy, Debug)]
pub(super) struct ArcCurveParams {
    pub center: Point3,
    pub normal: Point3,
    /// The arc's fixed 0-angle reference direction (unit vector times radius).
    pub xaxis: Point3,
    pub start_angle: f64,
    pub end_angle: f64,
    pub radius: f64,
    pub num_segments: u32,
}

/// The curve object an edge chain's edges share, declared inline by its first new edge.
#[derive(Clone, Copy, Debug)]
pub(super) enum ChainCurve<'a> {
    None,
    Arc(&'a ArcCurveParams),
    Polyline { num_edges: u32 },
}

#[derive(Debug, Default)]
struct EdgeChain {
    edge_slots: Vec<i32>,
    edge_senses: Vec<u8>,
    new_entities: i32,
}

/// Everything about one face except its outer boundary points.
#[derive(Clone, Copy, Debug)]
pub(super) struct FaceSpec<'a> {
    pub material: i32,
    pub layer: i32,
    pub back_material: i32,
    pub hidden: bool,
    pub edges: EdgeFlags,
    pub front_uv: Option<&'a [UvPoint]>,
    pub back_uv: Option<&'a [UvPoint]>,
    pub attribute_dicts: &'a [(String, AttributeDict)],
    pub curve: Option<&'a ArcCurveParams>,
    pub holes: &'a [Vec<Point3>],
}

/// A texture image to embed: its bytes, the path string stored with it, and its CDib subtype.
#[derive(Clone, Copy, Debug)]
pub(super) struct TextureSource<'a> {
    pub bytes: &'a [u8],
    pub path: &'a str,
    pub subtype: u32,
}

/// One placement of a definition (component instance, group or image).
#[derive(Clone, Copy, Debug)]
pub(super) struct Placement<'a> {
    pub definition_slot: i32,
    pub name: &'a str,
    pub translation: Point3,
    pub matrix3x3: Option<Matrix3x3>,
    pub material: i32,
    pub layer: i32,
    pub attribute_dicts: &'a [(String, AttributeDict)],
    pub hidden: bool,
}

// ---------------------------------------------------------------------------------------------
// ArchiveWriter.
// ---------------------------------------------------------------------------------------------

#[derive(Clone, Debug)]
pub(super) struct ArchiveWriter {
    pub next_slot: i32,
    pub class_slot: HashMap<&'static str, i32>,
    pub next_pid: u64,
    pub buf: Vec<u8>,
    /// One CSkFont per file: serialized inline at the first dimension/text's font field (exactly
    /// as SketchUp writes it) and back-referenced afterwards.
    dim_font_slot: Option<i32>,
}

impl ArchiveWriter {
    pub fn new(next_slot: i32, class_slot: HashMap<&'static str, i32>, next_pid: u64) -> Self {
        ArchiveWriter {
            next_slot,
            class_slot,
            next_pid,
            buf: Vec::new(),
            dim_font_slot: None,
        }
    }

    fn alloc(&mut self) -> i32 {
        let slot = self.next_slot;
        self.next_slot += 1;
        slot
    }

    pub fn alloc_pid(&mut self) -> u64 {
        let pid = self.next_pid;
        self.next_pid += 1;
        pid
    }

    /// Declares `class_name` on first use (or short-class-refs it afterwards) and always
    /// allocates a fresh object slot for the value that follows. Slot 0x7FFF is excluded from the
    /// short form (0x8000 | 0x7FFF == 0xFFFF is the new-class-declaration marker).
    fn new_of_known_class(&mut self, class_name: &'static str, schema: u16) -> i32 {
        match self.class_slot.get(class_name) {
            None => {
                self.buf.put_u16(0xFFFF);
                self.buf.put_u16(schema);
                self.buf.put_u16(class_name.len() as u16);
                self.buf.extend_from_slice(class_name.as_bytes());
                let class_slot = self.alloc();
                self.class_slot.insert(class_name, class_slot);
            }
            Some(&slot) if slot < 0x7FFF => self.buf.put_u16(0x8000 | slot as u16),
            Some(&slot) => {
                self.buf.put_u16(0x7FFF);
                self.buf.put_u32(0x8000_0000 | slot as u32);
            }
        }
        self.alloc()
    }

    /// A short class-ref to a class the scaffold prefix already declares.
    fn scaffold_class_ref(&mut self, slot: i32) {
        self.buf.put_u16(0x8000 | slot as u16);
        self.alloc(); // a class-ref always allocates an object slot, even a bookkeeping-only one
    }

    fn write_null(&mut self) {
        self.buf.put_u16(0);
    }

    /// Same 0x7FFF exclusion as `new_of_known_class`, for the plain (no class bit) case.
    fn backref(&mut self, slot: i32) {
        if slot < 0x7FFF {
            self.buf.put_u16(slot as u16);
        } else {
            self.buf.put_u16(0x7FFF);
            self.buf.put_u32(slot as u32);
        }
    }

    /// A pid as a byte mask of its non-zero bytes followed by those bytes.
    fn write_pid(&mut self, pid: u64) {
        let bytes = pid.to_le_bytes();
        let mask = bytes
            .iter()
            .enumerate()
            .filter(|(_, b)| **b != 0)
            .fold(0u8, |m, (bit, _)| m | (1 << bit));
        self.buf.push(mask);
        self.buf.extend(bytes.iter().filter(|b| **b != 0));
    }

    /// The entity preamble: attribute-container pointer, then pid (a fresh one unless given).
    fn preamble(&mut self, pid: Option<u64>, real_attrs: bool) {
        if real_attrs {
            // Ground truth: CComponentDefinition and CComponentInstance reference a real (but
            // childless) CAttributeContainer here instead of the null every other entity uses.
            self.scaffold_class_ref(ATTR_CONTAINER_SLOT);
            self.buf.put_zeros(3);
            self.buf.put_u16(0); // empty children-list terminator
        } else {
            self.write_null(); // no CAttributeContainer
        }
        let pid = pid.unwrap_or_else(|| self.alloc_pid());
        self.write_pid(pid);
    }

    /// Like `preamble(real_attrs = true)`, but the container's children list holds an optional
    /// CFaceTextureCoords followed by zero or more CAttributeNamed dictionaries.
    fn preamble_with_real_attrs(
        &mut self,
        front_matrix: Option<Matrix3x3>,
        back_matrix: Option<Matrix3x3>,
        attribute_dicts: &[(String, AttributeDict)],
        pid: Option<u64>,
    ) -> Result<(), SkpWriteError> {
        self.scaffold_class_ref(ATTR_CONTAINER_SLOT);
        self.buf.put_zeros(3);
        if front_matrix.is_some() || back_matrix.is_some() {
            self.write_face_texture_coords(front_matrix, back_matrix);
        }
        for (dict_name, entries) in attribute_dicts {
            self.write_attribute_dict(dict_name, entries)?;
        }
        self.write_null(); // children-list terminator
        let pid = pid.unwrap_or_else(|| self.alloc_pid());
        self.write_pid(pid);
        Ok(())
    }

    fn write_face_texture_coords(&mut self, front: Option<Matrix3x3>, back: Option<Matrix3x3>) {
        self.new_of_known_class("CFaceTextureCoords", FTC_SCHEMA);
        self.preamble(Some(0), false);
        self.buf.put_u32(0);
        let mut ks = [0.0; 24];
        ks[..9].copy_from_slice(&front.unwrap_or(IDENTITY_UV_MATRIX));
        ks[12..21].copy_from_slice(&back.unwrap_or(IDENTITY_UV_MATRIX));
        for v in ks {
            self.buf.put_f64(v);
        }
        self.buf.put_u32(0); // front pin count - this writer always emits a solved matrix
        self.buf.put_u32(0); // back pin count
        self.buf.put_u32(u32::from(front.is_some())); // fflags bit 0: front positioned
        self.buf.put_u32(u32::from(back.is_some())); // bflags bit 0: back positioned
    }

    fn write_attribute_dict(
        &mut self,
        dict_name: &str,
        entries: &AttributeDict,
    ) -> Result<(), SkpWriteError> {
        // CAttributeNamed is pre-declared in the scaffold prefix: always a short class-ref.
        self.scaffold_class_ref(ATTRIBUTE_NAMED_SLOT);
        self.buf.put_zeros(3); // null attrs (2) + mask=0 (1), pid=0
        self.buf.put_u32(0); // ground truth: read and discarded by the reader too
        self.write_str(dict_name)?;
        for (key, value) in entries {
            self.write_str(key)?;
            match value {
                AttributeValue::String(s) => {
                    self.buf.push(ATTR_TYPE_STRING);
                    self.write_str(s)?;
                }
                AttributeValue::Int32(i) => {
                    self.buf.push(ATTR_TYPE_INT32);
                    self.buf.extend_from_slice(&i.to_le_bytes());
                }
                AttributeValue::Bool(b) => {
                    self.buf.push(ATTR_TYPE_BOOL);
                    self.buf.push(u8::from(*b));
                }
                AttributeValue::Length(l) => {
                    self.buf.push(ATTR_TYPE_LENGTH);
                    self.buf.put_f64(*l);
                }
                AttributeValue::Double(d) => {
                    self.buf.push(ATTR_TYPE_DOUBLE);
                    self.buf.put_f64(*d);
                }
            }
        }
        self.write_str("")?; // empty-key terminator
        self.buf.put_u32(0);
        Ok(())
    }

    fn drawbase(&mut self, mat: i32, layer: i32, hidden: bool, edges: EdgeFlags) {
        let mut b = [0u8; 10];
        write_u16_at(&mut b, 0, mat as u16);
        b[2] = u8::from(hidden);
        // Offsets 3-4 are unused padding per the reader, but real SketchUp silently drops any
        // entity whose drawbase has them zeroed - they must be 1, 1.
        b[3] = 1;
        b[4] = 1;
        b[5] = u8::from(edges.soft);
        b[6] = u8::from(edges.smooth);
        write_u16_at(&mut b, 8, layer as u16);
        self.buf.extend_from_slice(&b);
    }

    /// A drawbase with default material/layer and no flags.
    fn plain_drawbase(&mut self) {
        self.drawbase(0, 0, false, EdgeFlags::default());
    }

    /// A guide's drawbase: default material, the guide's layer and hidden flag.
    fn guide_drawbase(&mut self, options: &GuideOptions) {
        self.drawbase(
            0,
            options.layer.unwrap_or(0),
            options.hidden,
            EdgeFlags::default(),
        );
    }

    fn write_vertex(&mut self, point: Point3) -> i32 {
        let slot = self.new_of_known_class("CVertex", 0);
        self.preamble(None, false);
        for v in point {
            self.buf.put_f64(v);
        }
        slot
    }

    /// One CArcCurve record: the shared geometric-parameter object every straight CEdge of a
    /// circle/arc back-references. Two of the 14 stored values (offsets 11 and 13) were 0 in
    /// every sample tested and are written as 0.
    fn write_arc_curve(&mut self, p: &ArcCurveParams) -> i32 {
        // Callers validate 3..=255 first; the format itself stores a single byte.
        debug_assert!(p.num_segments <= 0xFF);
        let slot = self.new_of_known_class("CArcCurve", ARC_CURVE_SCHEMA);
        self.preamble(None, false);
        self.buf.push(0);
        self.buf.push(p.num_segments as u8);
        self.buf.put_zeros(3);
        let vals = [
            p.center[0],
            p.center[1],
            p.center[2],
            p.normal[0],
            p.normal[1],
            p.normal[2],
            p.xaxis[0],
            p.xaxis[1],
            p.xaxis[2],
            p.start_angle,
            p.end_angle,
            0.0,
            p.radius,
            0.0,
        ];
        for v in vals {
            self.buf.put_f64(v);
        }
        slot
    }

    /// One CCurve record (a freeform polyline grouping): a constant 1-byte type tag, then the
    /// edge count as a u32.
    fn write_curve(&mut self, num_edges: u32) -> i32 {
        let slot = self.new_of_known_class("CCurve", CCURVE_SCHEMA);
        self.preamble(None, false);
        self.buf.push(1);
        self.buf.put_u32(num_edges);
        slot
    }

    /// An MFC CString: `FF FE FF`, a u8 UTF-16 code-unit count, then UTF-16LE code units.
    pub fn write_str(&mut self, s: &str) -> Result<(), SkpWriteError> {
        let n = s.encode_utf16().count();
        if n >= 0xFF {
            return Err(SkpWriteError::new(
                "string too long to encode (255 char limit)",
            ));
        }
        self.buf.extend_from_slice(&[0xFF, 0xFE, 0xFF, n as u8]);
        for unit in s.encode_utf16() {
            self.buf.put_u16(unit);
        }
        Ok(())
    }

    fn write_dim_font_ref(&mut self) {
        match self.dim_font_slot {
            Some(slot) => self.backref(slot),
            None => {
                self.dim_font_slot = Some(self.new_of_known_class("CSkFont", SK_FONT_SCHEMA));
                self.buf.extend_from_slice(&DIM_FONT_PAYLOAD);
            }
        }
    }

    /// A FREE linear dimension between two explicit points. The layout is the byte-exact one the
    /// real SDK writes for free dimensions (docs/dimension-record-notes.md): connection type 1
    /// with the point stored inline in each connection block and null object refs.
    pub fn write_dimension(
        &mut self,
        p1: Point3,
        p2: Point3,
        offset: f64,
        offset_vector: Option<Point3>,
    ) -> Result<(), SkpWriteError> {
        if p1 == p2 {
            return Err(SkpWriteError::new("add_dimension endpoints coincide"));
        }
        let mut offset = offset;
        let mut normal = [0.0, 0.0, 1.0];
        let mut xaxis = [1.0, 0.0, 0.0];
        if let Some(v) = offset_vector {
            if p1.iter().chain(&p2).chain(&v).any(|c| !c.is_finite()) {
                return Err(SkpWriteError::new(
                    "add_dimension coordinates must be finite",
                ));
            }
            xaxis = [p2[0] - p1[0], p2[1] - p1[1], p2[2] - p1[2]];
            let length = xaxis[0].hypot(xaxis[1]).hypot(xaxis[2]);
            if !length.is_finite() || length == 0.0 {
                return Err(SkpWriteError::new(
                    "add_dimension segment length must be finite and nonzero",
                ));
            }
            xaxis = [xaxis[0] / length, xaxis[1] / length, xaxis[2] / length];
            // Only the component perpendicular to the measured segment places the line.
            normal = cross3(xaxis, v);
            offset = normal[0].hypot(normal[1]).hypot(normal[2]);
            if !offset.is_finite() {
                return Err(SkpWriteError::new("add_dimension offset must be finite"));
            }
            if offset > 0.0 {
                normal = [normal[0] / offset, normal[1] / offset, normal[2] / offset];
            } else {
                // A stable plane is still needed when the dimension line has zero offset.
                let seed = if xaxis[2].abs() < 0.9 {
                    [0.0, 0.0, 1.0]
                } else {
                    [0.0, 1.0, 0.0]
                };
                normal = normalize3(cross3(xaxis, seed))?;
            }
        }
        self.new_of_known_class("CDimensionLinear", DIMENSION_LINEAR_SCHEMA);
        self.preamble(None, false);
        self.plain_drawbase();
        self.write_str("")?; // auto-computed measurement text
        self.write_dim_font_ref();
        // connection 1: [u8 0][u32 0][u32 type=1][u32 4][point A], null ref
        self.buf.put_zeros(5);
        self.buf.put_u32(1);
        self.buf.put_u32(4);
        for v in p1 {
            self.buf.put_f64(v);
        }
        self.buf.put_u16(0);
        // connection 2: [u16 0][f64 0][u32 type=1][u32 4][point B], null ref
        self.buf.put_zeros(10);
        self.buf.put_u32(1);
        self.buf.put_u32(4);
        for v in p2 {
            self.buf.put_f64(v);
        }
        self.buf.put_u16(0);
        // placement block: the plane normal and X axis (SDK free-dimension defaults unless an
        // offset vector placed the line), the constrained axis, and the offset
        self.buf.put_zeros(2);
        self.buf.put_f64(0.0);
        for v in normal.into_iter().chain(xaxis) {
            self.buf.put_f64(v);
        }
        // Constrain to the plane X axis: the signed side is encoded by its normal.
        self.buf.put_u32(u32::from(offset_vector.is_some()));
        self.buf.put_f64(offset);
        self.buf.put_f64(0.0);
        self.buf.put_u32(1);
        Ok(())
    }

    /// A leader text anchored at `point`, label at `point + leader`. Mirrors human-drawn leader
    /// texts from real files (the SDK's own create only makes SCREEN texts): screen slot zeroed,
    /// the free-connection block dimensions use, the label's world position in the placement
    /// tail, and leader type 2 (pushpin) before the arrow delimiter.
    pub fn write_text(
        &mut self,
        text: &str,
        point: Point3,
        leader: Point3,
    ) -> Result<(), SkpWriteError> {
        let label = [
            point[0] + leader[0],
            point[1] + leader[1],
            point[2] + leader[2],
        ];
        self.new_of_known_class("CText", TEXT_SCHEMA);
        self.preamble(None, false);
        self.plain_drawbase();
        self.write_dim_font_ref();
        self.buf.put_f64(0.0);
        self.buf.put_f64(0.0); // screen-fraction slot (unused)
        self.buf.put_u32(1);
        self.buf.put_u32(4); // free connection + constant
        for v in point {
            self.buf.put_f64(v);
        }
        self.buf.put_zeros(12);
        for v in label {
            self.buf.put_f64(v);
        }
        self.buf.put_zeros(16);
        self.buf.put_f64(1.0);
        self.buf.put_u32(2); // leader type: pushpin
        self.buf.extend_from_slice(&TEXT_DELIM);
        self.write_str(text)?;
        self.buf.put_zeros(5);
        Ok(())
    }

    /// A construction line: point + unit direction + signed start/end distance parameters along
    /// it (ground truth: SketchUp 2025, SDK/Ruby cross-checked). Exactly one of `point2` (bounded
    /// segment) or `direction` (unbounded, written with the real +/-1e30 sentinel) is required.
    pub fn write_construction_line(
        &mut self,
        point: Point3,
        point2: Option<Point3>,
        direction: Option<Point3>,
        options: &GuideOptions,
    ) -> Result<(), SkpWriteError> {
        let (dir, start_param, end_param) = match (point2, direction) {
            (Some(p2), None) => {
                let d = [p2[0] - point[0], p2[1] - point[1], p2[2] - point[2]];
                let length = (d[0] * d[0] + d[1] * d[1] + d[2] * d[2]).sqrt();
                if length == 0.0 {
                    return Err(SkpWriteError::new(
                        "add_construction_line: point and point2 coincide",
                    ));
                }
                ([d[0] / length, d[1] / length, d[2] / length], 0.0, length)
            }
            (None, Some(d)) => {
                let dlen = (d[0] * d[0] + d[1] * d[1] + d[2] * d[2]).sqrt();
                if dlen == 0.0 {
                    return Err(SkpWriteError::new(
                        "add_construction_line: direction must be nonzero",
                    ));
                }
                (
                    [d[0] / dlen, d[1] / dlen, d[2] / dlen],
                    -CLINE_INFINITE,
                    CLINE_INFINITE,
                )
            }
            _ => {
                return Err(SkpWriteError::new(
                    "add_construction_line: pass exactly one of point2 or direction",
                ))
            }
        };
        self.new_of_known_class("CConstructionLine", CONSTRUCTION_LINE_SCHEMA);
        self.preamble(None, false);
        self.guide_drawbase(options);
        for v in [
            point[0],
            point[1],
            point[2],
            dir[0],
            dir[1],
            dir[2],
            start_param,
            end_param,
        ] {
            self.buf.put_f64(v);
        }
        self.buf.put_zeros(4); // trailer
        Ok(())
    }

    /// A construction point. A second, always-zero 3-double block and a trailing zero byte follow
    /// the position - reserved, written as zero to match every real sample.
    pub fn write_construction_point(&mut self, position: Point3, options: &GuideOptions) {
        self.new_of_known_class("CConstructionPoint", CONSTRUCTION_POINT_SCHEMA);
        self.preamble(None, false);
        self.guide_drawbase(options);
        for v in [position[0], position[1], position[2], 0.0, 0.0, 0.0] {
            self.buf.put_f64(v);
        }
        self.buf.put_zeros(1);
    }

    /// A section plane: preamble + drawbase + (a, b, c, d) with a*x + b*y + c*z + d = 0 (unit
    /// normal, d = -(normal . point)), the form `SectionPlane#get_plane` returns. The v18+
    /// name/label pair is omitted: this writer only produces v17 files.
    pub fn write_section_plane(
        &mut self,
        point: Point3,
        normal: Point3,
    ) -> Result<(), SkpWriteError> {
        let nlen = (normal[0] * normal[0] + normal[1] * normal[1] + normal[2] * normal[2]).sqrt();
        if nlen == 0.0 {
            return Err(SkpWriteError::new(
                "add_section_plane: normal must be nonzero",
            ));
        }
        let (a, b, c) = (normal[0] / nlen, normal[1] / nlen, normal[2] / nlen);
        let d = -(a * point[0] + b * point[1] + c * point[2]);
        self.new_of_known_class("CSectionPlane", SECTION_PLANE_SCHEMA);
        self.preamble(None, false);
        self.plain_drawbase();
        for v in [a, b, c, d] {
            self.buf.put_f64(v);
        }
        Ok(())
    }

    /// A solid-color material. The stored f64 is TRANSPARENCY (0 = opaque), gated by the u8
    /// after it - see `write_textured_material`.
    pub fn write_material(
        &mut self,
        name: &str,
        rgba: Color4,
        opacity: Option<f64>,
    ) -> Result<i32, SkpWriteError> {
        let slot = self.new_of_known_class("CMaterial", MATERIAL_SCHEMA);
        self.preamble(None, false);
        self.write_str(name)?;
        self.buf.put_u16(0); // texflag: solid color, no texture
        self.buf.extend_from_slice(&rgba);
        self.write_str("")?; // texture path (empty - no texture)
        self.buf.put_zeros(8);
        self.buf.put_f64(opacity.map_or(1.0, |o| 1.0 - o));
        self.buf.push(u8::from(opacity.is_some())); // use_opacity gates it
        Ok(slot)
    }

    /// An image-textured material. `subtype` is CDib's format tag (see `detect_image_subtype`).
    ///
    /// The applied size (both default 1.0) is how much model space one tile covers, in inches.
    /// A face textured without positioning carries no per-face UV record, so for it this pair IS
    /// the mapping - and the reader divides a positioned face's UV by it too. (It once defaulted
    /// to a garbage sentinel pattern copied from one SDK sample, which real SketchUp renders as a
    /// vertically-smeared texture.)
    pub fn write_textured_material(
        &mut self,
        name: &str,
        image: &TextureSource<'_>,
        applied_height: Option<f64>,
        applied_width: Option<f64>,
        opacity: Option<f64>,
    ) -> Result<i32, SkpWriteError> {
        let slot = self.new_of_known_class("CMaterial", MATERIAL_SCHEMA);
        self.preamble(None, false);
        self.write_str(name)?;
        self.buf.put_u16(1); // texflag: textured
        self.buf.put_zeros(2);
        self.new_of_known_class("CDib", DIB_SCHEMA);
        self.buf.put_u32(image.subtype);
        self.buf.put_u32(image.bytes.len() as u32);
        self.buf.extend_from_slice(image.bytes);
        if image.subtype == 1 {
            // JPEG only: ground truth is a constant 90 whatever the source's own quality.
            self.buf.put_u32(90);
        }
        self.buf.put_f64(applied_width.unwrap_or(1.0));
        self.buf.put_f64(applied_height.unwrap_or(1.0));
        self.write_str(image.path)?;
        // Image average and paint colour are separate fields. Neither alpha is a colorize flag.
        self.buf
            .extend_from_slice(&[255, 255, 255, 255, 0, 255, 255, 255, 255]);
        self.write_str("")?;
        self.buf.put_u32(1); // material type: plain texture (2 = colorized texture)
        self.buf.put_u32(0); // colorize mode: shift (1 = tint)
                             // Opacity and the u8 that GATES it. The stored f64 is TRANSPARENCY (0 = opaque); the
                             // reader exposes `1.0 - stored` only when the flag is set, so an `opacity` is written
                             // inverted and round-trips as itself.
        self.buf.put_f64(opacity.map_or(1.0, |o| 1.0 - o));
        self.buf.push(u8::from(opacity.is_some()));
        Ok(slot)
    }

    /// A CLayer record. Ground truth shows each top-level layer embeds a second pid after its
    /// name, so it consumes 2 pids. `with_pids = false` (only for the Layer0 copy a definition
    /// embeds) omits both.
    pub fn write_layer(
        &mut self,
        name: &str,
        with_pids: bool,
        hidden: bool,
        rgba: Option<Color4>,
        dicts: &[(String, AttributeDict)],
    ) -> Result<i32, SkpWriteError> {
        let slot = self.new_of_known_class("CLayer", LAYER_SCHEMA);
        let pid = if with_pids { None } else { Some(0) };
        if dicts.is_empty() {
            self.preamble(pid, false);
        } else {
            self.preamble_with_real_attrs(None, None, dicts, pid)?;
        }
        self.write_str(name)?;
        let pid2 = if with_pids { self.alloc_pid() } else { 0 };
        self.buf.extend_from_slice(&[u8::from(hidden), 0, 0]);
        self.write_pid(pid2);
        self.write_str(&format!("Layer_{name}"))?;
        self.buf.put_u16(256); // ground truth is a constant 256 here
        self.buf.extend_from_slice(&rgba.unwrap_or([0; 4]));
        self.write_str("")?; // second name field - empty in ground truth
        self.buf.put_zeros(8);
        self.buf.put_f64(0.5); // 21-byte tail, opacity-like f64=0.5
        self.buf.put_zeros(5);
        Ok(slot)
    }

    fn write_thumbnail(&mut self) {
        self.new_of_known_class("CThumbnail", THUMBNAIL_SCHEMA);
        self.preamble(Some(0), false); // structural container: ground truth carries no pid
        self.scaffold_class_ref(CCAMERA_SLOT);
        self.buf.extend_from_slice(&CAMERA_TEMPLATE);
        self.write_null(); // no thumbnail image
    }

    /// Begin a CComponentDefinition record, up to (not including) its entity list. Returns
    /// (definition slot, offset of the u32 entity count the caller patches on close).
    pub fn write_definition_header(
        &mut self,
        attribute_dicts: &[(String, AttributeDict)],
    ) -> Result<(i32, usize), SkpWriteError> {
        let slot = self.new_of_known_class("CComponentDefinition", DEFINITION_SCHEMA);
        if attribute_dicts.is_empty() {
            self.preamble(None, true); // ground truth: a real pid and a real (empty) container
        } else {
            self.preamble_with_real_attrs(None, None, attribute_dicts, None)?;
        }
        self.buf.extend_from_slice(&DEFINITION_BASE_BLOCK);
        self.buf.put_u32(1); // nlayers: always 1, an embedded copy of Layer0
        let embedded_layer_slot = self.write_layer("Layer0", false, false, None, &[])?;
        self.backref(embedded_layer_slot); // this definition's own active layer
                                           // Counts CComponentDefinition classes declared inline in this header (not nested
                                           // instances, which are ordinary entities) - always 0, definitions are top-level here.
        self.buf.put_u32(0);
        let count_patch_pos = self.buf.len();
        self.buf.put_u32(0); // placeholder entity count
        Ok((slot, count_patch_pos))
    }

    /// Close a CComponentDefinition record: relationship count, GUID, name, timestamp, behavior
    /// flags and a default thumbnail. Returns the offset of the u32 group flag, so a definition
    /// first closed as a component can be flagged later when it is placed as a group.
    pub fn write_definition_tail(
        &mut self,
        name: &str,
        is_group: bool,
        ids: &mut IdSource,
    ) -> Result<usize, SkpWriteError> {
        self.buf.put_u32(0); // nrel: CRelationship count - always 0, not supported
        self.buf.put_u16(0);
        self.buf.extend_from_slice(&ids.guid());
        self.write_str(name)?;
        self.write_str("")?; // description - empty in ground truth
        self.write_str("")?; // second name field - empty in ground truth
        self.buf.put_u32(ids.timestamp());
        // 43-byte gap; byte -9 carries the always-faces-camera/shadows-face-sun flags (both off,
        // not exposed yet). The last u32 is 1 for a group's definition and 0 for a component's
        // (SketchUp API's SUComponentType_Group).
        let group_flag_pos = self.buf.len() + 39;
        self.buf.put_zeros(39);
        self.buf.put_u32(u32::from(is_group));
        self.write_thumbnail();
        Ok(group_flag_pos)
    }

    fn write_instance_like(
        &mut self,
        class_name: &'static str,
        schema: u16,
        real_attrs: bool,
        placement: &Placement<'_>,
        ids: &mut IdSource,
    ) -> Result<(), SkpWriteError> {
        self.new_of_known_class(class_name, schema);
        if real_attrs && !placement.attribute_dicts.is_empty() {
            self.preamble_with_real_attrs(None, None, placement.attribute_dicts, None)?;
        } else {
            self.preamble(None, real_attrs);
        }
        self.drawbase(
            placement.material,
            placement.layer,
            placement.hidden,
            EdgeFlags::default(),
        );
        self.backref(placement.definition_slot);
        for v in placement.matrix3x3.unwrap_or(IDENTITY_MATRIX) {
            self.buf.put_f64(v);
        }
        for v in placement.translation {
            self.buf.put_f64(v);
        }
        self.buf.put_f64(1.0);
        self.write_str(placement.name)?;
        self.buf.extend_from_slice(&ids.guid());
        Ok(())
    }

    /// A component instance - which, unlike a group, always carries a real (if empty)
    /// attribute container.
    pub fn write_instance(
        &mut self,
        placement: &Placement<'_>,
        ids: &mut IdSource,
    ) -> Result<(), SkpWriteError> {
        self.write_instance_like("CComponentInstance", INSTANCE_SCHEMA, true, placement, ids)
    }

    /// A group: structurally a component instance except for its class/schema and attribute
    /// pointer - a group only gets a real container when it actually has attributes (a real
    /// SketchUp 2020 Group with attributes carries one at this exact schema; openskp#261).
    pub fn write_group(
        &mut self,
        placement: &Placement<'_>,
        ids: &mut IdSource,
    ) -> Result<(), SkpWriteError> {
        let real_attrs = !placement.attribute_dicts.is_empty();
        self.write_instance_like("CGroup", GROUP_SCHEMA, real_attrs, placement, ids)
    }

    /// A CImage, which the reader treats as instance-shaped: field-for-field the same as
    /// `write_instance` (13 f64s, name, GUID), with an always-empty source-path string as the
    /// name (real SketchUp writes it empty too) and no material - an Image's look comes entirely
    /// from its definition's textured face.
    pub fn write_image(
        &mut self,
        definition_slot: i32,
        translation: Point3,
        matrix3x3: Option<Matrix3x3>,
        layer: i32,
        hidden: bool,
        ids: &mut IdSource,
    ) -> Result<(), SkpWriteError> {
        let placement = Placement {
            definition_slot,
            name: "",
            translation,
            matrix3x3,
            material: 0,
            layer,
            attribute_dicts: &[],
            hidden,
        };
        self.write_instance_like("CImage", IMAGE_SCHEMA, false, &placement, ids)
    }

    /// A chain of straight CEdge records connecting `points` in order (and back to the first if
    /// `closed`), sharing vertices/edges through `topo`. A shared curve object, if any, is
    /// declared inline as the FIRST newly-declared edge's own "curve" field.
    fn write_edge_chain(
        &mut self,
        points: &[Point3],
        topo: &mut Topology,
        closed: bool,
        flags: EdgeFlags,
        curve: ChainCurve<'_>,
    ) -> EdgeChain {
        let n = points.len();
        let pair_count = if closed { n } else { n.saturating_sub(1) };
        // Looked up once up front: a coordinate repeated within this chain is only shared once
        // its own index has been written, exactly like the C++ writer.
        let mut point_slots: Vec<Option<i32>> = points
            .iter()
            .map(|p| topo.vertex_slots.get(&point_key(*p)).copied())
            .collect();
        let mut chain = EdgeChain::default();
        let mut curve_slot: Option<i32> = None;

        for i in 0..pair_count {
            let (v1, v2) = (i, (i + 1) % n);
            if let (Some(a), Some(b)) = (point_slots[v1], point_slots[v2]) {
                if let Some(&(edge_slot, fwd_v1)) = topo.edges.get(&(a.min(b), a.max(b))) {
                    chain.edge_slots.push(edge_slot);
                    chain.edge_senses.push(u8::from(fwd_v1 != a));
                    continue;
                }
            }

            let edge_slot = self.new_of_known_class("CEdge", 2);
            self.preamble(None, false);
            self.drawbase(0, 0, flags.hidden, flags);
            for idx in [v1, v2] {
                match point_slots[idx] {
                    Some(slot) => self.backref(slot),
                    None => {
                        let slot = self.write_vertex(points[idx]);
                        point_slots[idx] = Some(slot);
                        topo.vertex_slots.insert(point_key(points[idx]), slot);
                    }
                }
            }
            match (curve_slot, curve) {
                (Some(slot), _) => self.backref(slot),
                (None, ChainCurve::Arc(params)) => curve_slot = Some(self.write_arc_curve(params)),
                (None, ChainCurve::Polyline { num_edges }) => {
                    curve_slot = Some(self.write_curve(num_edges))
                }
                (None, ChainCurve::None) => self.write_null(),
            }
            chain.edge_slots.push(edge_slot);
            chain.edge_senses.push(0);
            chain.new_entities += 1;
            let (a, b) = (
                point_slots[v1].unwrap_or_default(),
                point_slots[v2].unwrap_or_default(),
            );
            topo.edges.insert((a.min(b), a.max(b)), (edge_slot, a));
        }
        chain
    }

    /// A partial (open) arc as a chain of straight edges - no face. Returns the number of new
    /// entity-list entries.
    pub fn write_arc(
        &mut self,
        points: &[Point3],
        topo: &mut Topology,
        params: &ArcCurveParams,
        flags: EdgeFlags,
    ) -> i32 {
        self.write_edge_chain(points, topo, false, flags, ChainCurve::Arc(params))
            .new_entities
    }

    /// A freeform polyline: straight edges sharing one CCurve, no face. Returns the number of new
    /// entity-list entries.
    pub fn write_polyline(
        &mut self,
        points: &[Point3],
        topo: &mut Topology,
        closed: bool,
        flags: EdgeFlags,
    ) -> i32 {
        let n = points.len();
        let num_edges = if closed { n } else { n - 1 };
        self.write_edge_chain(
            points,
            topo,
            closed,
            flags,
            ChainCurve::Polyline {
                num_edges: num_edges as u32,
            },
        )
        .new_entities
    }

    /// One planar face. Returns the number of new entity-list entries (newly declared edges of
    /// the boundary and every hole, plus the face itself).
    pub fn write_face(
        &mut self,
        points: &[Point3],
        topo: &mut Topology,
        spec: &FaceSpec<'_>,
    ) -> Result<i32, SkpWriteError> {
        // Validate everything that CAN fail before writing a byte: write_edge_chain mutates both
        // this buffer and the shared topology as it goes, with no rollback.
        let plane = plane_from_polygon(points)?;
        let front_matrix = spec
            .front_uv
            .map(|uv| uv_matrix_for_face(uv, plane.normal()))
            .transpose()?;
        let back_matrix = spec
            .back_uv
            .map(|uv| uv_matrix_for_face(uv, plane.normal()))
            .transpose()?;
        let tol = plane_tolerance(points);
        for hole in spec.holes {
            if hole.len() < 3 {
                return Err(SkpWriteError::new("a hole needs at least 3 points"));
            }
            if hole.iter().any(|&p| plane.distance(p).abs() > tol) {
                return Err(SkpWriteError::new(
                    "a hole point is off the face's own plane - a hole must lie on the same plane as \
                     the outer boundary",
                ));
            }
        }

        let curve = spec.curve.map_or(ChainCurve::None, ChainCurve::Arc);
        let outer = self.write_edge_chain(points, topo, true, spec.edges, curve);
        let mut new_entities = outer.new_entities;
        let mut hole_loops = Vec::with_capacity(spec.holes.len());
        for hole in spec.holes {
            let chain = self.write_edge_chain(hole, topo, true, spec.edges, ChainCurve::None);
            new_entities += chain.new_entities;
            hole_loops.push(chain);
        }

        self.new_of_known_class("CFace", 3);
        if spec.front_uv.is_some() || spec.back_uv.is_some() || !spec.attribute_dicts.is_empty() {
            self.preamble_with_real_attrs(front_matrix, back_matrix, spec.attribute_dicts, None)?;
        } else {
            self.preamble(None, false);
        }
        self.drawbase(spec.material, spec.layer, spec.hidden, EdgeFlags::default());
        // The fit uses n.p = d; SketchUp's CFace stores ax + by + cz + d = 0.
        for v in [plane.nx, plane.ny, plane.nz, -plane.d] {
            self.buf.put_f64(v);
        }
        self.buf.put_u32(1 + spec.holes.len() as u32); // nloops

        // 1 marks the boundary loop, 0 a hole loop (ground truth).
        self.write_loop(&outer, 1);
        for hole in &hole_loops {
            self.write_loop(hole, 0);
        }

        self.buf.put_u16(spec.back_material as u16);
        Ok(new_entities + 1) // + the face itself
    }

    fn write_loop(&mut self, chain: &EdgeChain, boundary_flag: u8) {
        let loop_slot = self.new_of_known_class("CLoop", 1);
        self.preamble(Some(0), false); // structural object: ground truth uses pid 0
        self.buf.push(boundary_flag);
        self.buf.push(1);
        for (&edge_slot, &sense) in chain.edge_slots.iter().zip(&chain.edge_senses) {
            self.new_of_known_class("CEdgeUse", 1);
            self.preamble(Some(0), false);
            self.backref(edge_slot);
            self.buf.push(sense);
            self.backref(loop_slot);
        }
        self.write_null(); // loop terminator
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn shift_ref_keeps_the_class_bit_and_widens_past_0x7ffe() {
        let mut buf = vec![0xAA, 0x05, 0x80, 0xBB];
        assert_eq!(shift_ref(&mut buf, 1, 3), 0);
        assert_eq!(buf, [0xAA, 0x08, 0x80, 0xBB]);

        // 0x7FFE is the last short slot; 0x7FFF needs the 6-byte escape form.
        let mut buf = vec![0xFE, 0x7F];
        assert_eq!(shift_ref(&mut buf, 0, 0), 0);
        assert_eq!(shift_ref(&mut buf, 0, 1), 4);
        assert_eq!(buf, [0xFF, 0x7F, 0xFF, 0x7F, 0x00, 0x00]);

        let mut buf = vec![0x00, 0x00, 0x00, 0xFE, 0xFF, 0x01];
        assert_eq!(shift_ref(&mut buf, 3, 2), 4); // 0x8000 | 0x7FFE, shifted by 2
        assert_eq!(
            buf,
            [0x00, 0x00, 0x00, 0xFF, 0x7F, 0x00, 0x80, 0x00, 0x80, 0x01]
        );
    }

    #[test]
    fn pids_are_written_as_a_mask_of_their_nonzero_bytes() {
        let mut w = ArchiveWriter::new(0, HashMap::new(), 1);
        w.write_pid(0);
        w.write_pid(0x0102);
        w.write_pid(0x03_0000_0000);
        assert_eq!(w.buf, [0x00, 0x03, 0x02, 0x01, 0x10, 0x03]);
    }

    #[test]
    fn class_refs_and_backrefs_use_the_escape_form_from_0x7fff() {
        let mut w = ArchiveWriter::new(0x7FFE, HashMap::new(), 1);
        assert_eq!(w.new_of_known_class("CX", 2), 0x7FFF); // the class itself takes 0x7FFE
        w.buf.clear();
        w.new_of_known_class("CX", 2);
        assert_eq!(w.buf, [0xFE, 0xFF]); // 0x8000 | 0x7FFE
        w.buf.clear();
        w.class_slot.insert("CY", 0x7FFF);
        w.new_of_known_class("CY", 0);
        assert_eq!(w.buf, [0xFF, 0x7F, 0xFF, 0x7F, 0x00, 0x80]);
        w.buf.clear();
        w.backref(0x7FFE);
        w.backref(0x7FFF);
        assert_eq!(w.buf, [0xFE, 0x7F, 0xFF, 0x7F, 0xFF, 0x7F, 0x00, 0x00]);
    }

    #[test]
    fn strings_are_mfc_cstrings_in_utf16() {
        let mut w = ArchiveWriter::new(0, HashMap::new(), 1);
        w.write_str("A\u{e9}\u{1F600}").unwrap();
        assert_eq!(
            w.buf,
            [0xFF, 0xFE, 0xFF, 4, 0x41, 0, 0xE9, 0, 0x3D, 0xD8, 0x00, 0xDE]
        );
    }

    #[test]
    fn image_subtype_comes_from_magic_bytes() {
        assert_eq!(detect_image_subtype(b"\x89PNG\r\n\x1a\nrest").unwrap(), 4);
        assert_eq!(detect_image_subtype(&[0xFF, 0xD8, 0xFF, 0xE0]).unwrap(), 1);
        assert!(detect_image_subtype(b"GIF89a").is_err());
        assert!(detect_image_subtype(&[0x89, b'P']).is_err());
    }

    #[test]
    fn deterministic_guids_follow_the_oracle_scheme() {
        let mut ids = IdSource::Deterministic {
            next: 0,
            timestamp: 7,
        };
        assert_eq!(
            ids.guid(),
            [0, 1, 2, 3, 4, 5, 0x46, 7, 0x88, 9, 10, 11, 12, 13, 14, 15]
        );
        assert_eq!(ids.guid()[0], 16);
        assert_eq!(ids.timestamp(), 7);
    }
}
