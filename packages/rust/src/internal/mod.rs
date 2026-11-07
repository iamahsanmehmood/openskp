//! Reader infrastructure shared by the VFF reader, the legacy reader and the
//! scene builders (`internal.hpp`).
//!
//! Public (but `#[doc(hidden)]` from the crate root) so integration tests and
//! sibling modules can reach the same internals the C++ tests include.

use std::borrow::Cow;
use std::collections::BTreeMap;
use std::fmt;
use std::sync::Arc;

use crate::model::{
    CoEdge, Color3, ConstructionLine, ConstructionPoint, Dimension, EntityId,
    ParsedAttrDictionaries, SectionPlane, StringDictionaries, StyleItem, StyleWatermark,
    TextEntity, Vec3, ViewCamera,
};

pub mod build_model;
pub mod bytes;
pub mod core;
pub mod geometry;
pub mod pages_dimensions;
pub mod strconv;
pub mod transforms;
pub mod vff;
pub mod xml;

pub use self::build_model::build_model;
pub use self::bytes::{
    parse_flat, parse_tlv_recursive, parse_varint, read_f64, read_i32, read_u16, read_u32, tag_at,
};
pub use self::core::{decode_xml_entities, full_parse};
pub use self::geometry::{
    collect_definitions, collect_geometry, collect_layers, collect_material_ids,
    collect_model_attribute_dictionaries, extract_attribute_dictionaries,
};
pub use self::pages_dimensions::{
    find_page_node, parse_camera, parse_dimensions, parse_pages, scan_instance_transforms,
    scan_vertex_positions,
};
pub use self::transforms::{
    fold_scale, multiply_matrices, transform_determinant, transform_normal, transform_point,
};
pub use self::vff::{extract_version, is_legacy, read_meta_units, valid_header};
pub use crate::legacy::{
    find_count_after_v20_filler, find_empty_root_after_v20_filler, legacy_connection_paths,
    legacy_instance_has_guid, parse_legacy, read_legacy_attribute_value, read_legacy_entity_list,
    scan_pages_for_layers, LegacyEntityListResult, LegacyKind, LegacyObject, LegacyPathResult,
    LegacySlotEntry, LegacySlotTable, LegacyValue, V20FillerHit,
};
pub use crate::observability::{emit_log, emit_progress};

// ── internal error (the C++ std::exception family) ──────────────────────

/// Which standard exception the C++ code would have thrown.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum ErrorKind {
    /// `std::out_of_range`
    OutOfRange,
    /// `std::overflow_error`
    Overflow,
    /// `std::invalid_argument`
    InvalidArgument,
    /// `std::runtime_error` (and anything else)
    Runtime,
}

/// An internal failure: a bounds-checked read past the end, a varint wider
/// than 64 bits, or a reader giving up (`std::runtime_error`). Readers return
/// it; the parse entry points wrap it into an `SkpParseError` cause.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Error {
    pub kind: ErrorKind,
    pub message: Cow<'static, str>,
}

impl Error {
    pub fn new(kind: ErrorKind, message: impl Into<Cow<'static, str>>) -> Self {
        Self {
            kind,
            message: message.into(),
        }
    }

    pub fn out_of_range(message: impl Into<Cow<'static, str>>) -> Self {
        Self::new(ErrorKind::OutOfRange, message)
    }

    pub fn overflow(message: impl Into<Cow<'static, str>>) -> Self {
        Self::new(ErrorKind::Overflow, message)
    }

    pub fn invalid_argument(message: impl Into<Cow<'static, str>>) -> Self {
        Self::new(ErrorKind::InvalidArgument, message)
    }

    pub fn runtime(message: impl Into<Cow<'static, str>>) -> Self {
        Self::new(ErrorKind::Runtime, message)
    }
}

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.message)
    }
}

impl std::error::Error for Error {}

/// Result of an internal reader step.
pub type Result<T, E = Error> = std::result::Result<T, E>;

// ── TLV tree ────────────────────────────────────────────────────────────

/// A TLV tag as its 4 upper-case hex digits in byte order (bytes `F4 01` are
/// `"F401"`), stored inline. Compares directly against `&str`.
#[derive(Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord, Default)]
pub struct Tag([u8; 4]);

const HEX: &[u8; 16] = b"0123456789ABCDEF";

impl Tag {
    /// The tag of the two raw bytes `b0 b1`.
    pub const fn from_bytes(b0: u8, b1: u8) -> Self {
        Tag([
            HEX[(b0 >> 4) as usize],
            HEX[(b0 & 15) as usize],
            HEX[(b1 >> 4) as usize],
            HEX[(b1 & 15) as usize],
        ])
    }

    /// The tag text (`""` for a default tag).
    pub fn as_str(&self) -> &str {
        let n = self.0.iter().position(|&b| b == 0).unwrap_or(4);
        std::str::from_utf8(&self.0[..n]).unwrap_or("")
    }
}

impl From<&str> for Tag {
    fn from(s: &str) -> Self {
        let mut t = [0u8; 4];
        for (d, b) in t.iter_mut().zip(s.bytes()) {
            *d = b;
        }
        Tag(t)
    }
}

impl PartialEq<str> for Tag {
    fn eq(&self, other: &str) -> bool {
        self.as_str() == other
    }
}

impl PartialEq<&str> for Tag {
    fn eq(&self, other: &&str) -> bool {
        self.as_str() == *other
    }
}

impl fmt::Display for Tag {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.as_str())
    }
}

impl fmt::Debug for Tag {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{:?}", self.as_str())
    }
}

/// One TLV record: container tags carry `children`, everything else (and a
/// container whose payload does not parse) carries `payload`.
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct TlvNode {
    pub offset: u64,
    pub size: u64,
    pub tag: Tag,
    pub children: Vec<TlvNode>,
    pub payload: Vec<u8>,
}

impl TlvNode {
    /// A leaf with a payload (test / builder convenience).
    pub fn leaf(tag: &str, payload: Vec<u8>) -> Self {
        TlvNode {
            tag: Tag::from(tag),
            payload,
            ..Default::default()
        }
    }

    /// A container with children (test / builder convenience).
    pub fn container(tag: &str, children: Vec<TlvNode>) -> Self {
        TlvNode {
            tag: Tag::from(tag),
            children,
            ..Default::default()
        }
    }
}

// ── raw parse results ───────────────────────────────────────────────────

#[derive(Debug, Clone, PartialEq)]
pub struct RawFace {
    pub loops: Vec<Vec<CoEdge>>,
    pub normal: Vec3,
    pub material_id: Option<EntityId>,
    pub back_material_id: Option<EntityId>,
    pub uv_transform: Option<[f64; 9]>,
    pub uv_transform_back: Option<[f64; 9]>,
    pub uv_projected: bool,
    pub uv_projected_back: bool,
    pub hidden: bool,
    /// Layer entity id as text until build_model resolves it to a name, same
    /// as `RawInstance::layer`.
    pub layer: String,
    /// The raw layer id (Python port's `Face.layer`); `None` when the record
    /// carries none.
    pub layer_id: Option<EntityId>,
}

impl Default for RawFace {
    fn default() -> Self {
        Self {
            loops: Vec::new(),
            normal: [0.0, 0.0, 1.0],
            material_id: None,
            back_material_id: None,
            uv_transform: None,
            uv_transform_back: None,
            uv_projected: false,
            uv_projected_back: false,
            hidden: false,
            layer: String::new(),
            layer_id: None,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Default)]
pub struct RawInstance {
    pub offset: u64,
    pub id: Option<EntityId>,
    pub ref_guid: String,
    pub name: String,
    pub ref_idx: Option<EntityId>,
    pub material_id: Option<EntityId>,
    pub matrix: Vec<f64>,
    pub layer: String,
    /// The "dynamic_attributes" dictionary only, stringified.
    pub properties: BTreeMap<String, String>,
    /// Every named dictionary, values typed.
    pub attribute_dicts: ParsedAttrDictionaries,
    pub hidden: bool,
}

/// The analytic frame of a Curve that is one arc.
#[derive(Debug, Clone, Copy, PartialEq, Default)]
pub struct RawArcFrame {
    pub center: Vec3,
    pub normal: Vec3,
    pub x_axis: Vec3,
    pub y_axis: Vec3,
    pub start_angle: f64,
    pub end_angle: f64,
}

#[derive(Debug, Clone, PartialEq, Default)]
pub struct GeometryBuilder {
    pub vertices: BTreeMap<EntityId, Vec3>,
    pub edges: BTreeMap<EntityId, (Option<EntityId>, Option<EntityId>)>,
    pub edge_flags: BTreeMap<EntityId, i32>,
    /// Edge id -> entity id of the Curve the edge belongs to (Edge#curve).
    pub edge_curves: BTreeMap<EntityId, EntityId>,
    /// Edge id -> raw layer id (VFF `D007 > D207`; Python's `edge_layers`).
    pub edge_layers: BTreeMap<EntityId, EntityId>,
    /// Curve id -> its analytic arc, for the curves that are arcs.
    pub arc_curves: BTreeMap<EntityId, RawArcFrame>,
    pub faces: BTreeMap<EntityId, RawFace>,
    pub instances: Vec<RawInstance>,
    pub section_planes: Vec<SectionPlane>,
    pub texts: Vec<TextEntity>,
    pub dimensions: Vec<Dimension>,
    pub construction_lines: Vec<ConstructionLine>,
    pub construction_points: Vec<ConstructionPoint>,
}

#[derive(Debug, Clone, PartialEq, Default)]
pub struct RawDefinition {
    pub guid: String,
    pub name: String,
    pub always_faces_camera: bool,
    pub shadows_face_sun: bool,
    pub is_image: bool,
    pub is_group: bool,
    pub builder: GeometryBuilder,
}

impl RawDefinition {
    /// The implicit root definition (`{"ROOT", "ROOT_MODEL"}`).
    pub fn root() -> Self {
        Self {
            guid: "ROOT".into(),
            name: "ROOT_MODEL".into(),
            ..Default::default()
        }
    }
}

#[derive(Debug, Clone, PartialEq, Default)]
pub struct RawTexture {
    pub filename: String,
    pub x_scale: f64,
    pub y_scale: f64,
    pub data: Option<Vec<u8>>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct RawMaterial {
    pub name: String,
    pub r: i32,
    pub g: i32,
    pub b: i32,
    pub a: i32,
    pub transparency: f64,
    pub colorized: bool,
    pub colorize_type: i32,
    pub texture: Option<RawTexture>,
}

impl Default for RawMaterial {
    fn default() -> Self {
        Self {
            name: String::new(),
            r: 128,
            g: 128,
            b: 128,
            a: 255,
            transparency: 1.0,
            colorized: false,
            colorize_type: 0,
            texture: None,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct RawStyle {
    pub name: String,
    pub description: String,
    pub front_color: Option<Color3>,
    pub back_color: Option<Color3>,
    pub items: BTreeMap<i32, StyleItem>,
    pub watermarks: Vec<StyleWatermark>,
    pub folder: String,
    pub active: bool,
    pub working_copy: bool,
    pub modified: bool,
}

#[derive(Debug, Clone, PartialEq)]
pub struct RawPage {
    pub name: String,
    pub eye: Option<Vec3>,
    pub target: Option<Vec3>,
    pub up: Option<Vec3>,
    pub fov: f64,
    pub parallel: bool,
    pub ortho_height: f64,
    pub hidden_layer_ids: Vec<EntityId>,
    pub selected: bool,
}

impl Default for RawPage {
    fn default() -> Self {
        Self {
            name: String::new(),
            eye: None,
            target: None,
            up: None,
            fov: 35.0,
            parallel: false,
            ortho_height: 0.0,
            hidden_layer_ids: Vec::new(),
            selected: false,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Default)]
pub struct RawDimension {
    pub a: Vec3,
    pub b: Vec3,
    pub offset: f64,
    pub plane_x: Option<Vec3>,
    pub normal: Option<Vec3>,
    pub offset_vector: Option<Vec3>,
    pub text: String,
}

/// A view camera record (34BC), as scenes and the model's current view store
/// it.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct RawCamera {
    pub eye: Option<Vec3>,
    pub target: Option<Vec3>,
    pub up: Option<Vec3>,
    pub fov: f64,
    pub parallel: bool,
    pub ortho_height: f64,
}

impl Default for RawCamera {
    fn default() -> Self {
        Self {
            eye: None,
            target: None,
            up: None,
            fov: 35.0,
            parallel: false,
            ortho_height: 0.0,
        }
    }
}

/// Everything a reader (VFF or legacy) extracts, before build_model /
/// build_scene_raw / build_instanced_scene_raw shape it.
#[derive(Debug, Clone, PartialEq)]
pub struct RawParsed {
    pub version: String,
    /// The model's unit-system string from meta/meta.dat (VFF only).
    pub units: Option<String>,
    pub layer_colors: BTreeMap<String, Color3>,
    /// Layer names in the order first encountered in the file -
    /// `layer_colors` is sorted by name, so this is the only place file order
    /// survives.
    pub layer_order: Vec<String>,
    /// Layer visibility: VFF from the layer manager's 8E3C byte, legacy from
    /// the CLayer record.
    pub layer_hidden: BTreeMap<String, bool>,
    pub layer_attribute_dictionaries: BTreeMap<String, StringDictionaries>,
    pub layer_id_to_name: BTreeMap<EntityId, String>,
    pub pages: Vec<RawPage>,
    pub camera: Option<ViewCamera>,
    pub dimensions: Vec<RawDimension>,
    pub material_id_to_name: BTreeMap<EntityId, String>,
    /// Materials by name. The same `Arc` may also sit in
    /// `materials_by_folder`; identity (`Arc::ptr_eq`) matters to build_model.
    pub materials: BTreeMap<String, Arc<RawMaterial>>,
    pub materials_by_folder: BTreeMap<String, Arc<RawMaterial>>,
    pub styles: Vec<RawStyle>,
    pub definitions: BTreeMap<EntityId, RawDefinition>,
    pub root: RawDefinition,
    /// Dictionaries attached to the model entity itself (F601 > 8813 > D007 >
    /// DC05).
    pub model_attribute_dicts: ParsedAttrDictionaries,
}

impl Default for RawParsed {
    fn default() -> Self {
        Self {
            version: "unknown".into(),
            units: None,
            layer_colors: BTreeMap::new(),
            layer_order: Vec::new(),
            layer_hidden: BTreeMap::new(),
            layer_attribute_dictionaries: BTreeMap::new(),
            layer_id_to_name: BTreeMap::new(),
            pages: Vec::new(),
            camera: None,
            dimensions: Vec::new(),
            material_id_to_name: BTreeMap::new(),
            materials: BTreeMap::new(),
            materials_by_folder: BTreeMap::new(),
            styles: Vec::new(),
            definitions: BTreeMap::new(),
            root: RawDefinition::root(),
            model_attribute_dicts: BTreeMap::new(),
        }
    }
}

/// Lossy UTF-8 text of raw bytes (the C++ keeps them as a byte string).
pub fn text(p: &[u8]) -> String {
    String::from_utf8_lossy(p).into_owned()
}

/// Upper-case hex of raw bytes.
pub fn hex_upper(p: &[u8]) -> String {
    let mut s = String::with_capacity(p.len() * 2);
    for &b in p {
        s.push(HEX[(b >> 4) as usize] as char);
        s.push(HEX[(b & 15) as usize] as char);
    }
    s
}
