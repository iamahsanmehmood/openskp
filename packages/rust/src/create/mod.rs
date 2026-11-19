//! Create new legacy-format (v17) `.skp` files from scratch.
//!
//! A from-scratch binary writer for the same MFC `CArchive` object-stream format the legacy
//! reader decodes, built by inverting that reader's own proven logic (the class-ref/back-ref
//! protocol, entity preambles, drawbase records). Ported from OpenSKP's C++ `create.cpp` (itself a
//! port of the SketchUp-SDK-cross-checked Python writer) and kept byte-for-byte identical to it.
//! No SketchUp SDK is involved at any point.
//!
//! **Scope:** planar faces from vertex coordinates (vertices/edges shared automatically wherever
//! coordinates coincide exactly), faces with holes, `auto_triangulate` fan-splitting of
//! non-planar polygons; solid-color and PNG/JPEG-textured materials with optional explicit UV
//! positioning; named layers; reusable component definitions with positioned instances; groups;
//! nested definitions and nested group instances; per-instance rotation and hidden state; custom
//! attribute dictionaries; true `CArcCurve` circles/arcs and `CCurve` polylines; Image entities;
//! free linear dimensions, leader texts, construction lines/points and section planes.
//!
//! Coordinates are in **inches**, SketchUp's native internal unit. Every file opens to the
//! standard "Iso" view (parallel projection, from the (1, -1, 1) octant).
//!
//! **The blank scaffold, and why it's there.** Every legacy `.skp` carries a header/material-
//! manager/style-and-font-manager region that is only understood well enough to preserve it
//! byte-for-byte and renumber the few internal references that shift when geometry is inserted.
//! So rather than synthesize it, new files are built by splicing genuinely-written records into
//! a bundled minimal empty document (see `scaffold_blank_v17`). Those template bytes came from
//! Trimble's own SDK (`SUModelCreate` + a bare save) - SketchUp's built-in empty-document
//! boilerplate (default style, Layer0, system font references), the same bytes any new
//! SketchUp document contains. Everything else - the entity encoding, the object-graph protocol,
//! the flag bytes real SketchUp silently requires, the tail renumbering - is independently
//! reverse-engineered.
//!
//! ```no_run
//! use openskp::create::{create, FaceOptions};
//!
//! let mut builder = create();
//! let red = builder.add_material("Red", [255, 0, 0], None)?;
//! let roof = builder.add_layer("Roof", &Default::default())?;
//! let options = FaceOptions { material: Some(red), layer: Some(roof), ..Default::default() };
//! builder.add_face(&[[0.0, 0.0, 0.0], [100.0, 0.0, 0.0], [100.0, 100.0, 0.0], [0.0, 100.0, 0.0]], &options)?;
//! builder.save("output.skp")?;
//! # Ok::<(), openskp::create::SkpWriteError>(())
//! ```

mod archive;
mod geometry;
mod scaffold_blank_v17;

use std::borrow::Cow;
use std::collections::{BTreeMap, HashMap};
use std::f64::consts::PI;
use std::fmt;
use std::fs::{self, File};
use std::io::Write;
use std::path::Path;
use std::sync::atomic::{AtomicU64, Ordering};

use archive::{
    detect_image_subtype, shift_ref, write_u32_at, ArcCurveParams, ArchiveWriter,
    AttributeDictList, EdgeFlags, FaceSpec, IdSource, Placement, PutLe, TextureSource, Topology,
};
use geometry::{
    arc_points, circle_basis, circle_points, is_coplanar, newell_plane, normalize3,
    plane_from_polygon, resolve_matrix3x3,
};
use scaffold_blank_v17 as scaffold;

/// A 3D point (x, y, z) in inches.
pub type Point3 = [f64; 3];
/// Row-major 3x3 rotation/scale matrix.
pub type Matrix3x3 = [f64; 9];
/// A rotation as (axis, angle in radians), right-hand rule; the axis need not be unit length.
pub type Rotation = (Point3, f64);
/// One (world point, (u, v)) correspondence for explicit texture positioning.
pub type UvPoint = (Point3, [f64; 2]);
/// Exactly 3 [`UvPoint`]s - the minimum that fully determines an affine UV mapping.
pub type UvCorrespondence = Vec<UvPoint>;
/// The reader's own color types: RGB / RGBA, 0-255 per channel.
pub use crate::model::{Color3, Color4};

/// The C++ default for [`SkpBuilder::add_dimension`]'s `offset`, in inches.
pub const DEFAULT_DIMENSION_OFFSET: f64 = 10.0;
/// The C++ default for [`SkpBuilder::add_text`]'s `leader`, in inches.
pub const DEFAULT_TEXT_LEADER: Point3 = [15.0, 15.0, 15.0];

/// Raised when a `.skp` file cannot be constructed.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct SkpWriteError {
    message: String,
}

impl SkpWriteError {
    pub(crate) fn new(message: impl Into<String>) -> Self {
        SkpWriteError {
            message: message.into(),
        }
    }

    /// The human-readable reason.
    pub fn message(&self) -> &str {
        &self.message
    }
}

impl fmt::Display for SkpWriteError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.message)
    }
}

impl std::error::Error for SkpWriteError {}

/// A custom attribute value, one variant per SketchUp attribute type this format supports.
#[derive(Clone, Debug, PartialEq)]
pub enum AttributeValue {
    /// A string (type 10).
    String(String),
    /// An int32 (type 4).
    Int32(i32),
    /// A plain double (type 6, "Float").
    Double(f64),
    /// A boolean (type 7).
    Bool(bool),
    /// A SketchUp `Length` in inches (type 12).
    Length(f64),
}

impl From<&str> for AttributeValue {
    fn from(v: &str) -> Self {
        AttributeValue::String(v.to_owned())
    }
}

impl From<String> for AttributeValue {
    fn from(v: String) -> Self {
        AttributeValue::String(v)
    }
}

impl From<i32> for AttributeValue {
    fn from(v: i32) -> Self {
        AttributeValue::Int32(v)
    }
}

impl From<f64> for AttributeValue {
    fn from(v: f64) -> Self {
        AttributeValue::Double(v)
    }
}

impl From<bool> for AttributeValue {
    fn from(v: bool) -> Self {
        AttributeValue::Bool(v)
    }
}

/// A named dictionary of custom key/value metadata - the same mechanism SketchUp's own "dynamic
/// component" attributes use. Keys are written in sorted order.
pub type AttributeDict = BTreeMap<String, AttributeValue>;

/// A material color: an opaque [`Color3`] (alpha 255) or a full [`Color4`].
pub trait MaterialColor {
    /// The color as RGBA.
    fn to_rgba(self) -> Color4;
}

impl MaterialColor for Color3 {
    fn to_rgba(self) -> Color4 {
        [self[0], self[1], self[2], 255]
    }
}

impl MaterialColor for Color4 {
    fn to_rgba(self) -> Color4 {
        self
    }
}

const DEFAULT_ATTRIBUTE_DICT_NAME: &str = "attributes";

/// Display settings supported by the legacy document scaffold, for
/// [`SkpBuilder::set_document_options`].
#[derive(Clone, Debug, PartialEq)]
pub struct DocumentOptions {
    /// Native UnitsOptions values: inches=0, feet=1, mm=2, cm=3, metres=4, yards=5.
    pub length_unit: i32,
    pub length_precision: i32,
    /// Native style item ids and scalar values (colours are packed ABGR u32 values). Only items
    /// present in the bundled legacy style are updated; newer items are ignored.
    pub style_items: BTreeMap<i32, f64>,
}

impl Default for DocumentOptions {
    fn default() -> Self {
        DocumentOptions {
            length_unit: 0,
            length_precision: 3,
            style_items: BTreeMap::new(),
        }
    }
}

fn read_u32_le(buf: &[u8], pos: usize) -> u32 {
    u32::from_le_bytes([buf[pos], buf[pos + 1], buf[pos + 2], buf[pos + 3]])
}

/// The scalar a style item or rendering option stores as an integer: the value truncated toward
/// zero to 64 bits, then to 32 (as the C++ writer's casts do).
fn style_u32(v: f64) -> u32 {
    v as i64 as u32
}

/// Patches the scaffold's style item table at `pos` (one of the two: working and saved style).
/// Patched before archive references are widened, so every offset still refers to the scaffold.
fn patch_style_items(tail: &mut [u8], mut pos: usize, options: &DocumentOptions) {
    for _ in 0..56 {
        let id = read_u32_le(tail, pos) as i32;
        let valid = read_u32_le(tail, pos + 4) != 0;
        pos += 8;
        if !valid {
            continue; // e.g. the empty watermark list has no variant payload
        }
        let kind = read_u32_le(tail, pos);
        pos += 4;
        let size = match kind {
            1 => 1,
            7 => 8,
            _ => 4,
        };
        if let Some(&v) = options.style_items.get(&id) {
            match kind {
                1 => tail[pos] = u8::from(v != 0.0),
                4 => write_u32_at(tail, pos, style_u32(v)),
                6 => write_u32_at(tail, pos, (v as f32).to_bits()),
                7 => tail[pos..pos + 8].copy_from_slice(&v.to_le_bytes()),
                _ => {}
            }
        }
        pos += size;
    }
}

/// Current rendering options, also stored outside the style manager. These scaffold offsets
/// were cross-checked (upstream) by changing one option at a time with the SketchUp 2026 C API,
/// saving as v17, and reading the result with that API: (style item id, offset, is boolean).
const RENDERING_OPTION_FIELDS: [(i32, usize, bool); 41] = [
    (1000, 3173, false),
    (1001, 3279, false),
    (1002, 3197, false),
    (1004, 3201, true),
    (1005, 3202, false),
    (1006, 3206, true),
    (1007, 3207, false),
    (1008, 3211, true),
    (1009, 3212, false),
    (1010, 3216, true),
    (1011, 3217, false),
    (1012, 3172, true),
    (1014, 3181, false),
    (1015, 3353, true),
    (2001, 3166, false),
    (2002, 3227, false),
    (2003, 3231, false),
    (2004, 3170, true),
    (2005, 3171, true),
    (2006, 3326, false),
    (2007, 3196, true),
    (4000, 3177, false),
    (4001, 3287, false),
    (4002, 3299, true),
    (4003, 3295, false),
    (4004, 3300, true),
    (4005, 3302, false),
    (4006, 3301, true),
    (4007, 3291, false),
    (5000, 3344, true),
    (7000, 3185, false),
    (7001, 3340, false),
    (7002, 3189, false),
    (7003, 3306, false),
    (7004, 3310, false),
    (7005, 3314, false),
    (7008, 3283, true),
    (7010, 3222, true),
    (7011, 3195, true),
    (7012, 3286, true),
    (7014, 3318, false),
];

/// Applies `options` to the scaffold prefix: the native named UnitsOptions and the current
/// rendering options, at fixed offsets in the immutable v17 scaffold.
fn patch_document_prefix(prefix: &mut [u8], options: &DocumentOptions) {
    write_u32_at(prefix, 2327, options.length_precision as u32);
    write_u32_at(prefix, 2360, 0); // LengthFormat: decimal
    write_u32_at(prefix, 2389, options.length_unit as u32);
    write_u32_at(prefix, 2689, options.length_unit as u32); // AreaUnit
    write_u32_at(prefix, 2718, options.length_unit as u32); // VolumeUnit
    for (id, offset, boolean) in RENDERING_OPTION_FIELDS {
        let Some(&v) = options.style_items.get(&id) else {
            continue;
        };
        if boolean {
            prefix[offset] = u8::from(v != 0.0);
        } else {
            write_u32_at(prefix, offset, style_u32(v));
        }
    }
}

/// Options for `add_construction_line` / `add_construction_point` on [`SkpBuilder`] and
/// [`ComponentDefinitionBuilder`].
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct GuideOptions {
    /// Layer handle (from `add_layer`); `None` = default (Layer0).
    pub layer: Option<i32>,
    /// Hides the guide itself (SketchUp's "Hide" on this specific guide).
    pub hidden: bool,
}

/// Options for `add_face`.
#[derive(Clone, Debug, PartialEq)]
pub struct FaceOptions {
    /// Front-side material handle (from `add_material`/`add_texture_material`); `None` = default.
    pub material: Option<i32>,
    /// Layer handle (from `add_layer`); `None` = Layer0.
    pub layer: Option<i32>,
    /// Back-side material handle; `None` = default.
    pub back_material: Option<i32>,
    /// Hides the face itself.
    pub hidden: bool,
    /// Edge flags, applied only to edges NEWLY declared by this call (a shared edge keeps the
    /// flags it was first declared with).
    pub soft_edges: bool,
    pub smooth_edges: bool,
    pub hidden_edges: bool,
    /// Explicit front-side texture positioning: exactly 3 (point, (u, v)) pairs, (u, v) in tiles
    /// of the image. Works on a face of any orientation.
    pub front_uv: Option<UvCorrespondence>,
    pub back_uv: Option<UvCorrespondence>,
    /// Custom metadata, written under a dictionary named `attribute_dict_name`.
    pub attributes: AttributeDict,
    pub attribute_dict_name: String,
    /// Fan-triangulate a non-coplanar polygon from `points[0]` into real triangles instead of
    /// failing. Already-planar input is still written as one face. Not compatible with
    /// `front_uv`/`back_uv`; ignored when `holes` is non-empty.
    pub auto_triangulate: bool,
    /// Closed polygons cut out of the face, on the outer boundary's plane. Winding is irrelevant.
    pub holes: Vec<Vec<Point3>>,
}

impl Default for FaceOptions {
    fn default() -> Self {
        FaceOptions {
            material: None,
            layer: None,
            back_material: None,
            hidden: false,
            soft_edges: false,
            smooth_edges: false,
            hidden_edges: false,
            front_uv: None,
            back_uv: None,
            attributes: AttributeDict::new(),
            attribute_dict_name: DEFAULT_ATTRIBUTE_DICT_NAME.to_owned(),
            auto_triangulate: false,
            holes: Vec::new(),
        }
    }
}

/// Options for `add_circle`.
#[derive(Clone, Debug, PartialEq)]
pub struct CircleOptions {
    /// Tessellation segment count, 3-255 (SketchUp's own circle tool defaults to 24).
    pub num_segments: u32,
    pub material: Option<i32>,
    pub layer: Option<i32>,
    pub back_material: Option<i32>,
    pub hidden: bool,
    /// Explicit texture positioning. Unlike `FaceOptions`, these pins are NOT scaled by the
    /// material's applied size (matching the C++ writer).
    pub front_uv: Option<UvCorrespondence>,
    pub back_uv: Option<UvCorrespondence>,
    pub attributes: AttributeDict,
    pub attribute_dict_name: String,
}

impl Default for CircleOptions {
    fn default() -> Self {
        CircleOptions {
            num_segments: 24,
            material: None,
            layer: None,
            back_material: None,
            hidden: false,
            front_uv: None,
            back_uv: None,
            attributes: AttributeDict::new(),
            attribute_dict_name: DEFAULT_ATTRIBUTE_DICT_NAME.to_owned(),
        }
    }
}

/// Options for `add_arc`.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct ArcOptions {
    /// Segment count, 3-255.
    pub num_segments: u32,
    pub hidden_edges: bool,
    pub soft_edges: bool,
    pub smooth_edges: bool,
}

impl Default for ArcOptions {
    fn default() -> Self {
        ArcOptions {
            num_segments: 24,
            hidden_edges: false,
            soft_edges: false,
            smooth_edges: false,
        }
    }
}

/// Options for `add_polyline`.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct PolylineOptions {
    /// Also connects the last point back to the first.
    pub closed: bool,
    pub hidden_edges: bool,
    pub soft_edges: bool,
    pub smooth_edges: bool,
}

/// Options for [`SkpBuilder::add_component_definition`].
#[derive(Clone, Debug, PartialEq)]
pub struct DefinitionOptions {
    /// Custom metadata on the definition itself, under `attribute_dict_name`.
    pub attributes: AttributeDict,
    pub attribute_dict_name: String,
}

impl Default for DefinitionOptions {
    fn default() -> Self {
        DefinitionOptions {
            attributes: AttributeDict::new(),
            attribute_dict_name: DEFAULT_ATTRIBUTE_DICT_NAME.to_owned(),
        }
    }
}

/// Options for [`SkpBuilder::add_layer`].
#[derive(Clone, Debug, Default, PartialEq)]
pub struct LayerOptions {
    /// The layer's display color; `None` writes all-zero bytes.
    pub color: Option<Color4>,
    /// The layer's own visibility (the layer panel checkbox).
    pub hidden: bool,
    /// Named attribute dictionaries written onto the layer; the writer reserves no names.
    pub extra_dictionaries: BTreeMap<String, AttributeDict>,
}

/// Options for `add_instance`.
#[derive(Clone, Debug, PartialEq)]
pub struct InstanceOptions {
    /// Defaults to the definition's name.
    pub name: Option<String>,
    /// Applied after the matrix, in inches.
    pub translation: Point3,
    /// Rotation/scale (identity if unset). At most one of `matrix3x3`/`rotation`.
    pub matrix3x3: Option<Matrix3x3>,
    pub rotation: Option<Rotation>,
    /// Applied to the instance itself, not its contents.
    pub material: Option<i32>,
    pub layer: Option<i32>,
    /// Custom metadata on this placement (not its definition), under `attribute_dict_name`.
    pub attributes: AttributeDict,
    pub attribute_dict_name: String,
    /// Hides this placement; its contents still exist in the file.
    pub hidden: bool,
}

impl Default for InstanceOptions {
    fn default() -> Self {
        InstanceOptions {
            name: None,
            translation: [0.0; 3],
            matrix3x3: None,
            rotation: None,
            material: None,
            layer: None,
            attributes: AttributeDict::new(),
            attribute_dict_name: DEFAULT_ATTRIBUTE_DICT_NAME.to_owned(),
            hidden: false,
        }
    }
}

/// Options for [`SkpBuilder::add_group`].
#[derive(Clone, Debug, PartialEq)]
pub struct GroupOptions {
    /// Defaults to "Group".
    pub name: Option<String>,
    pub translation: Point3,
    pub matrix3x3: Option<Matrix3x3>,
    pub rotation: Option<Rotation>,
    pub material: Option<i32>,
    pub layer: Option<i32>,
    /// Custom metadata; a group only gets a real attribute container when this is non-empty
    /// (openskp#261).
    pub attributes: AttributeDict,
    pub attribute_dict_name: String,
    pub hidden: bool,
}

impl Default for GroupOptions {
    fn default() -> Self {
        GroupOptions {
            name: None,
            translation: [0.0; 3],
            matrix3x3: None,
            rotation: None,
            material: None,
            layer: None,
            attributes: AttributeDict::new(),
            attribute_dict_name: DEFAULT_ATTRIBUTE_DICT_NAME.to_owned(),
            hidden: false,
        }
    }
}

/// Options for [`ComponentDefinitionBuilder::add_group_instance`]; same shape as
/// [`GroupOptions`], but the name defaults to the placed definition's name.
#[derive(Clone, Debug, PartialEq)]
pub struct GroupInstanceOptions {
    pub name: Option<String>,
    pub translation: Point3,
    pub matrix3x3: Option<Matrix3x3>,
    pub rotation: Option<Rotation>,
    pub material: Option<i32>,
    pub layer: Option<i32>,
    pub attributes: AttributeDict,
    pub attribute_dict_name: String,
    pub hidden: bool,
}

impl Default for GroupInstanceOptions {
    fn default() -> Self {
        GroupInstanceOptions {
            name: None,
            translation: [0.0; 3],
            matrix3x3: None,
            rotation: None,
            material: None,
            layer: None,
            attributes: AttributeDict::new(),
            attribute_dict_name: DEFAULT_ATTRIBUTE_DICT_NAME.to_owned(),
            hidden: false,
        }
    }
}

/// Options for [`SkpBuilder::add_image`].
#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct ImageOptions {
    pub translation: Point3,
    /// Rotation/scale (identity if unset). At most one of `matrix3x3`/`rotation`.
    pub matrix3x3: Option<Matrix3x3>,
    pub rotation: Option<Rotation>,
    pub layer: Option<i32>,
    pub hidden: bool,
}

/// A reference to a component/group definition of one [`SkpBuilder`], used to place it
/// (`add_instance`/`add_group_instance`) or to reopen its [`ComponentDefinitionBuilder`].
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub struct DefinitionHandle {
    builder: u64,
    index: usize,
}

/// Where a group places itself when its definition closes.
#[derive(Clone, Debug)]
struct GroupPlacement {
    translation: Point3,
    matrix3x3: Option<Matrix3x3>,
    material: i32,
    layer: i32,
    attribute_dicts: AttributeDictList,
    hidden: bool,
}

#[derive(Debug)]
struct DefinitionState {
    slot: i32,
    name: String,
    count_patch_pos: usize,
    topology: Topology,
    new_entity_count: i32,
    closed: bool,
    /// Offset of the definition's group flag in the definition buffer, set when it closes.
    group_flag_pos: Option<usize>,
    /// Set only for definitions started via `add_group`: a group places itself on close.
    group_placement: Option<GroupPlacement>,
}

/// The placement fields shared by nested instances and nested group instances.
struct NestedPlacement<'a> {
    name: Option<&'a str>,
    translation: Point3,
    matrix3x3: Option<Matrix3x3>,
    material: Option<i32>,
    layer: Option<i32>,
    attribute_dicts: &'a [(String, AttributeDict)],
    hidden: bool,
}

static NEXT_BUILDER_ID: AtomicU64 = AtomicU64::new(1);

/// Accumulates geometry and writes it into a new legacy-format (v17) `.skp` file.
///
/// Sections are spliced into the scaffold in a fixed order, so calls must follow it: materials,
/// then layers, then component definitions/groups, then root-level geometry and placements.
/// Each step's archive slot numbering depends on the final size of every earlier one.
#[derive(Debug)]
pub struct SkpBuilder {
    id: u64,
    materials_by_name: BTreeMap<String, i32>,
    layers_by_name: BTreeMap<String, i32>,
    ids: IdSource,

    material_writer: ArchiveWriter,
    material_count: i32,
    /// Applied size (width, height) in inches of each TEXTURED material slot; `add_face` scales
    /// its UV pins by it (see `scale_pins`).
    applied_sizes: HashMap<i32, (f64, f64)>,

    layer_writer: Option<ArchiveWriter>,
    layer_writer_start: i32,
    layer_count: i32,

    definition_writer: Option<ArchiveWriter>,
    definition_writer_start: i32,
    definition_count: i32,
    definitions: Vec<DefinitionState>,
    open_definition: Option<usize>,
    pending_groups: Vec<(usize, GroupPlacement)>,

    geometry_writer: Option<ArchiveWriter>,
    root_topology: Topology,
    new_entity_count: i32,
    /// Root entities added so far (faces, placements, curves, annotations...) - to_bytes refuses
    /// an empty model.
    face_count: i32,
    /// Display units and style values from `set_document_options`, applied in `to_bytes`.
    document_options: Option<DocumentOptions>,
}

impl Default for SkpBuilder {
    fn default() -> Self {
        Self::new()
    }
}

/// Start building a new legacy-format (v17) `.skp` file. Same as [`SkpBuilder::new`].
pub fn create() -> SkpBuilder {
    SkpBuilder::new()
}

fn single_dict(name: &str, attributes: &AttributeDict) -> AttributeDictList {
    if attributes.is_empty() {
        Vec::new()
    } else {
        vec![(name.to_owned(), attributes.clone())]
    }
}

fn check_segments(num_segments: u32) -> Result<(), SkpWriteError> {
    if (3..=255).contains(&num_segments) {
        Ok(())
    } else {
        Err(SkpWriteError::new(format!(
            "num_segments must be between 3 and 255, got {num_segments}"
        )))
    }
}

/// A caller's UV pins are in TILES of the image, but the matrix SketchUp stores is in INCHES of
/// texture space (the reader divides by the material's applied size), so pins are scaled up by
/// that size before the fit. Without this a 2 m (78.74 in) tile came out 78.74x too big.
fn scale_pins<'a>(
    pins: Option<&'a [UvPoint]>,
    sizes: &HashMap<i32, (f64, f64)>,
    slot: i32,
) -> Option<Cow<'a, [UvPoint]>> {
    let pins = pins?;
    match sizes.get(&slot) {
        Some(&(w, h)) if !(w == 1.0 && h == 1.0) => Some(Cow::Owned(
            pins.iter()
                .map(|&(p, uv)| (p, [uv[0] * w, uv[1] * h]))
                .collect(),
        )),
        _ => Some(Cow::Borrowed(pins)),
    }
}

/// Write one face - or, for a non-planar polygon with `auto_triangulate`, its fan triangles.
fn write_face_or_triangulate(
    writer: &mut ArchiveWriter,
    points: &[Point3],
    topo: &mut Topology,
    spec: &FaceSpec<'_>,
    auto_triangulate: bool,
) -> Result<i32, SkpWriteError> {
    if !spec.holes.is_empty() || !auto_triangulate || points.len() == 3 || is_coplanar(points)? {
        return writer.write_face(points, topo, spec);
    }
    if spec.front_uv.is_some() || spec.back_uv.is_some() {
        return Err(SkpWriteError::new(
            "auto_triangulate cannot be combined with front_uv/back_uv positioning",
        ));
    }
    let triangle = FaceSpec {
        front_uv: None,
        back_uv: None,
        curve: None,
        holes: &[],
        ..*spec
    };
    // Collinear fan wedges have no surface to emit. Validate all remaining wedges before
    // writing any: a later failure must not leave earlier triangles serialized without their
    // entity counts (SketchUp rejects the whole document).
    check_finite_points(points, "face")?;
    let mut triangles = Vec::new();
    for pair in points[1..].windows(2) {
        let wedge = [points[0], pair[0], pair[1]];
        if newell_plane(&wedge).is_err() {
            continue;
        }
        plane_from_polygon(&wedge)?;
        triangles.push(wedge);
    }
    if triangles.is_empty() {
        return Err(SkpWriteError::new(
            "triangulation has no non-degenerate faces",
        ));
    }
    let mut total = 0;
    for wedge in &triangles {
        total += writer.write_face(wedge, topo, &triangle)?;
    }
    Ok(total)
}

fn check_finite_point(p: Point3, what: &str) -> Result<(), SkpWriteError> {
    if !p[0].is_finite() || !p[1].is_finite() || !p[2].is_finite() {
        return Err(SkpWriteError::new(format!(
            "{what} coordinates must be finite (got NaN or Infinity)"
        )));
    }
    Ok(())
}

fn check_finite_points(points: &[Point3], what: &str) -> Result<(), SkpWriteError> {
    for &p in points {
        check_finite_point(p, what)?;
    }
    Ok(())
}

fn add_face_to(
    writer: &mut ArchiveWriter,
    topo: &mut Topology,
    applied_sizes: &HashMap<i32, (f64, f64)>,
    points: &[Point3],
    options: &FaceOptions,
) -> Result<i32, SkpWriteError> {
    check_finite_points(points, "face")?;
    for hole in options.holes.iter() {
        check_finite_points(hole, "hole")?;
    }
    let dicts = single_dict(&options.attribute_dict_name, &options.attributes);
    let front_uv = scale_pins(
        options.front_uv.as_deref(),
        applied_sizes,
        options.material.unwrap_or(0),
    );
    let back_uv = scale_pins(
        options.back_uv.as_deref(),
        applied_sizes,
        options.back_material.unwrap_or(0),
    );
    let spec = FaceSpec {
        material: options.material.unwrap_or(0),
        layer: options.layer.unwrap_or(0),
        back_material: options.back_material.unwrap_or(0),
        hidden: options.hidden,
        edges: EdgeFlags {
            hidden: options.hidden_edges,
            soft: options.soft_edges,
            smooth: options.smooth_edges,
        },
        front_uv: front_uv.as_deref(),
        back_uv: back_uv.as_deref(),
        attribute_dicts: &dicts,
        curve: None,
        holes: &options.holes,
    };
    write_face_or_triangulate(writer, points, topo, &spec, options.auto_triangulate)
}

fn add_circle_to(
    writer: &mut ArchiveWriter,
    topo: &mut Topology,
    center: Point3,
    normal: Point3,
    radius: f64,
    options: &CircleOptions,
) -> Result<i32, SkpWriteError> {
    check_finite_point(center, "circle center")?;
    check_finite_point(normal, "circle normal")?;
    if !radius.is_finite() {
        return Err(SkpWriteError::new("circle radius must be finite"));
    }
    check_segments(options.num_segments)?;
    let n = normalize3(normal)?;
    let (u, w) = circle_basis(n)?;
    let params = ArcCurveParams {
        center,
        normal: n,
        xaxis: [radius * u[0], radius * u[1], radius * u[2]],
        start_angle: 0.0,
        end_angle: 2.0 * PI,
        radius,
        num_segments: options.num_segments,
    };
    let points = circle_points(center, radius, options.num_segments, u, w);
    let dicts = single_dict(&options.attribute_dict_name, &options.attributes);
    let spec = FaceSpec {
        material: options.material.unwrap_or(0),
        layer: options.layer.unwrap_or(0),
        back_material: options.back_material.unwrap_or(0),
        hidden: options.hidden,
        edges: EdgeFlags::default(),
        front_uv: options.front_uv.as_deref(),
        back_uv: options.back_uv.as_deref(),
        attribute_dicts: &dicts,
        curve: Some(&params),
        holes: &[],
    };
    writer.write_face(&points, topo, &spec)
}

#[allow(clippy::too_many_arguments)] // the C++ signature: geometry plus the shared state
fn add_arc_to(
    writer: &mut ArchiveWriter,
    topo: &mut Topology,
    center: Point3,
    normal: Point3,
    radius: f64,
    start_angle: f64,
    end_angle: f64,
    options: &ArcOptions,
) -> Result<i32, SkpWriteError> {
    check_finite_point(center, "arc center")?;
    check_finite_point(normal, "arc normal")?;
    if !radius.is_finite() {
        return Err(SkpWriteError::new("arc radius must be finite"));
    }
    if !start_angle.is_finite() || !end_angle.is_finite() {
        return Err(SkpWriteError::new("arc angles must be finite"));
    }
    check_segments(options.num_segments)?;
    if end_angle == start_angle {
        return Err(SkpWriteError::new(
            "start_angle and end_angle must differ - use add_circle for a full circle",
        ));
    }
    let n = normalize3(normal)?;
    let basis = circle_basis(n)?;
    let u = basis.0;
    let params = ArcCurveParams {
        center,
        normal: n,
        xaxis: [radius * u[0], radius * u[1], radius * u[2]],
        start_angle,
        end_angle,
        radius,
        num_segments: options.num_segments,
    };
    let points = arc_points(
        center,
        radius,
        options.num_segments,
        basis,
        start_angle,
        end_angle,
    );
    let flags = EdgeFlags {
        hidden: options.hidden_edges,
        soft: options.soft_edges,
        smooth: options.smooth_edges,
    };
    Ok(writer.write_arc(&points, topo, &params, flags))
}

fn add_polyline_to(
    writer: &mut ArchiveWriter,
    topo: &mut Topology,
    points: &[Point3],
    options: &PolylineOptions,
) -> Result<i32, SkpWriteError> {
    check_finite_points(points, "polyline")?;
    if points.len() < 2 {
        return Err(SkpWriteError::new("a polyline needs at least 2 points"));
    }
    let flags = EdgeFlags {
        hidden: options.hidden_edges,
        soft: options.soft_edges,
        smooth: options.smooth_edges,
    };
    Ok(writer.write_polyline(points, topo, options.closed, flags))
}

impl SkpBuilder {
    /// An empty builder. See also [`create`].
    pub fn new() -> Self {
        SkpBuilder {
            id: NEXT_BUILDER_ID.fetch_add(1, Ordering::Relaxed),
            materials_by_name: BTreeMap::new(),
            layers_by_name: BTreeMap::new(),
            ids: IdSource::Random,
            material_writer: ArchiveWriter::new(
                scaffold::BASE,
                HashMap::new(),
                scaffold::FIRST_NEW_PID,
            ),
            material_count: 0,
            applied_sizes: HashMap::new(),
            layer_writer: None,
            layer_writer_start: 0,
            layer_count: 0,
            definition_writer: None,
            definition_writer_start: 0,
            definition_count: 0,
            definitions: Vec::new(),
            open_definition: None,
            pending_groups: Vec::new(),
            geometry_writer: None,
            root_topology: Topology::default(),
            new_entity_count: 0,
            face_count: 0,
            document_options: None,
        }
    }

    /// Every material registered so far, by name, with its handle.
    pub fn materials_by_name(&self) -> &BTreeMap<String, i32> {
        &self.materials_by_name
    }

    /// Every layer registered so far, by name, with its handle.
    pub fn layers_by_name(&self) -> &BTreeMap<String, i32> {
        &self.layers_by_name
    }

    /// Replace random GUIDs and the current time with a fixed, reproducible sequence (GUID `n`
    /// is bytes `n*16 + i` with the v4 bits applied; every timestamp is `timestamp`). Only for
    /// byte-exact tests: real files need unique GUIDs.
    #[doc(hidden)]
    pub fn use_deterministic_ids(&mut self, timestamp: u32) {
        self.ids = IdSource::Deterministic { next: 0, timestamp };
    }

    /// Set decimal display units and basic working/saved style values, without scaling geometry.
    pub fn set_document_options(&mut self, options: &DocumentOptions) -> Result<(), SkpWriteError> {
        if !(0..=5).contains(&options.length_unit) || !(0..=6).contains(&options.length_precision) {
            return Err(SkpWriteError::new(
                "invalid decimal display units or precision",
            ));
        }
        if options
            .style_items
            .values()
            .any(|&v| !v.is_finite() || !(-2_147_483_648.0..=4_294_967_295.0).contains(&v))
        {
            return Err(SkpWriteError::new("style scalar is out of range"));
        }
        self.document_options = Some(options.clone());
        Ok(())
    }

    /// The root geometry writer's next archive slot, once root geometry exists (test hook).
    #[doc(hidden)]
    pub fn debug_geometry_next_slot(&self) -> Option<i32> {
        self.geometry_writer.as_ref().map(|w| w.next_slot)
    }

    fn check_material_handle(&self, value: Option<i32>, param: &str) -> Result<(), SkpWriteError> {
        // Without this a stray value (most commonly a layer handle passed to the wrong option)
        // is written as a dangling material reference that real SketchUp rejects on open, with
        // no hint of which call caused it.
        match value {
            None | Some(0) => Ok(()),
            Some(v) if self.materials_by_name.values().any(|&slot| slot == v) => Ok(()),
            Some(v) => Err(SkpWriteError::new(format!(
                "{param}={v} is not a handle this builder's add_material()/add_texture_material() returned - \
                 passing an unrelated value (e.g. a layer handle by mistake) would silently write an \
                 invalid material reference that real SketchUp rejects on open"
            ))),
        }
    }

    fn check_layer_handle(&self, value: Option<i32>) -> Result<(), SkpWriteError> {
        match value {
            None | Some(0) => Ok(()),
            Some(v) if self.layers_by_name.values().any(|&slot| slot == v) => Ok(()),
            Some(v) => Err(SkpWriteError::new(format!(
                "layer={v} is not a handle this builder's add_layer() returned - passing an unrelated \
                 value (e.g. a material handle by mistake) would silently write an invalid layer \
                 reference that real SketchUp rejects on open"
            ))),
        }
    }

    /// The definition index behind `handle`, if it belongs to this builder.
    fn resolve(&self, handle: DefinitionHandle) -> Result<usize, SkpWriteError> {
        if handle.builder == self.id {
            Ok(handle.index)
        } else {
            Err(SkpWriteError::new(
                "component definition belongs to a different builder (a different create() call) - \
                 its slot number is meaningless here",
            ))
        }
    }

    fn handle(&self, index: usize) -> DefinitionHandle {
        DefinitionHandle {
            builder: self.id,
            index,
        }
    }

    fn material_shift(&self) -> i32 {
        self.material_writer.next_slot - scaffold::BASE
    }

    fn material_shifted_class_slot(&self) -> HashMap<&'static str, i32> {
        // CLayer is the scaffold's only pre-declared class that new sections reuse.
        HashMap::from([("CLayer", scaffold::BASE + self.material_shift())])
    }

    fn layer_shift(&self) -> i32 {
        self.layer_writer
            .as_ref()
            .map_or(0, |w| w.next_slot - self.layer_writer_start)
    }

    fn post_layer_class_slot(&self) -> HashMap<&'static str, i32> {
        match &self.layer_writer {
            Some(w) => w.class_slot.clone(),
            None => self.material_shifted_class_slot(),
        }
    }

    fn definition_shift(&self) -> i32 {
        self.definition_writer
            .as_ref()
            .map_or(0, |w| w.next_slot - self.definition_writer_start)
    }

    fn post_definition_class_slot(&self) -> HashMap<&'static str, i32> {
        match &self.definition_writer {
            Some(w) => w.class_slot.clone(),
            None => self.post_layer_class_slot(),
        }
    }

    fn check_material_order(&self, method: &str) -> Result<(), SkpWriteError> {
        if self.geometry_writer.is_some() {
            Err(SkpWriteError::new(format!(
                "{method} must be called before any add_face calls"
            )))
        } else if self.layer_writer.is_some() {
            Err(SkpWriteError::new(format!(
                "{method} must be called before any add_layer calls"
            )))
        } else if self.definition_writer.is_some() {
            Err(SkpWriteError::new(format!(
                "{method} must be called before any add_component_definition calls"
            )))
        } else {
            Ok(())
        }
    }

    /// Create the root geometry writer on first use. Its starting slot locks in every earlier
    /// section's size, so no definition may still be open.
    fn ensure_geometry_writer(&mut self) -> Result<(), SkpWriteError> {
        if self.geometry_writer.is_some() {
            return Ok(());
        }
        if let Some(open) = self.open_definition {
            return Err(SkpWriteError::new(format!(
                "component definition '{}' is still open - call close() on it before adding root-level geometry",
                self.definitions[open].name
            )));
        }
        let start = scaffold::SCAFFOLD_NEXT_SLOT
            + self.material_shift()
            + self.layer_shift()
            + self.definition_shift();
        let class_slot = self.post_definition_class_slot();
        // Persistent ids continue from the last section written before root geometry.
        let next_pid = self
            .definition_writer
            .as_ref()
            .or(self.layer_writer.as_ref())
            .map_or(self.material_writer.next_pid, |w| w.next_pid);
        let writer = self
            .geometry_writer
            .insert(ArchiveWriter::new(start, class_slot, next_pid));
        // Flush groups that closed earlier, in creation order - deferred until now so closing one
        // group doesn't lock in root slot numbering before a later add_group/
        // add_component_definition call has run.
        for (index, gp) in &self.pending_groups {
            let def = &self.definitions[*index];
            let placement = Placement {
                definition_slot: def.slot,
                name: &def.name,
                translation: gp.translation,
                matrix3x3: gp.matrix3x3,
                material: gp.material,
                layer: gp.layer,
                attribute_dicts: &gp.attribute_dicts,
                hidden: gp.hidden,
            };
            writer.write_group(&placement, &mut self.ids)?;
            self.new_entity_count += 1;
            self.face_count += 1;
        }
        self.pending_groups.clear();
        Ok(())
    }

    /// Register a solid-color material and return its handle for `material` options. `color` is
    /// a [`Color3`] (opaque) or [`Color4`]. Registering an existing name returns its handle.
    ///
    /// Materials must all come before any layer, definition or geometry.
    pub fn add_material(
        &mut self,
        name: &str,
        color: impl MaterialColor,
        opacity: Option<f64>,
    ) -> Result<i32, SkpWriteError> {
        self.check_material_order("add_material")?;
        if let Some(&slot) = self.materials_by_name.get(name) {
            return Ok(slot);
        }
        let slot = self
            .material_writer
            .write_material(name, color.to_rgba(), opacity)?;
        self.materials_by_name.insert(name.to_owned(), slot);
        self.material_count += 1;
        Ok(slot)
    }

    /// Register an image-textured material from a PNG or JPEG file (detected from its magic
    /// bytes) and return its handle. Same ordering rules as [`add_material`](Self::add_material).
    ///
    /// `applied_height`/`applied_width` (default 1.0) are the inches of model space one tile of
    /// the image covers. A face textured without positioning carries no UV record, so this pair
    /// IS its mapping. The path string itself is embedded in the file.
    pub fn add_texture_material(
        &mut self,
        name: &str,
        image_path: impl AsRef<Path>,
        applied_height: Option<f64>,
        applied_width: Option<f64>,
        opacity: Option<f64>,
    ) -> Result<i32, SkpWriteError> {
        self.check_material_order("add_texture_material")?;
        if let Some(&slot) = self.materials_by_name.get(name) {
            return Ok(slot);
        }
        let path = image_path.as_ref();
        let path_str = path.to_string_lossy();
        let bytes = fs::read(path).map_err(|e| {
            SkpWriteError::new(format!("cannot open texture image file: {path_str}: {e}"))
        })?;
        let image = TextureSource {
            bytes: &bytes,
            path: &path_str,
            subtype: detect_image_subtype(&bytes)?,
        };
        let slot = self.material_writer.write_textured_material(
            name,
            &image,
            applied_height,
            applied_width,
            opacity,
        )?;
        self.materials_by_name.insert(name.to_owned(), slot);
        self.applied_sizes.insert(
            slot,
            (applied_width.unwrap_or(1.0), applied_height.unwrap_or(1.0)),
        );
        self.material_count += 1;
        Ok(slot)
    }

    /// Register a layer and return its handle for `layer` options. Registering an existing name
    /// returns its handle (and ignores `options`). Must come before any definition or geometry.
    pub fn add_layer(&mut self, name: &str, options: &LayerOptions) -> Result<i32, SkpWriteError> {
        if self.geometry_writer.is_some() {
            return Err(SkpWriteError::new(
                "add_layer must be called before any add_face calls",
            ));
        }
        if self.definition_writer.is_some() {
            return Err(SkpWriteError::new(
                "add_layer must be called before any add_component_definition calls",
            ));
        }
        if let Some(&slot) = self.layers_by_name.get(name) {
            return Ok(slot);
        }
        if self.layer_writer.is_none() {
            self.layer_writer_start = scaffold::LAYER_WRITER_BASE + self.material_shift();
            self.layer_writer = Some(ArchiveWriter::new(
                self.layer_writer_start,
                self.material_shifted_class_slot(),
                self.material_writer.next_pid,
            ));
        }
        let dicts: AttributeDictList = options
            .extra_dictionaries
            .iter()
            .map(|(k, v)| (k.clone(), v.clone()))
            .collect();
        let writer = self.layer_writer.as_mut().expect("created above");
        let slot = writer.write_layer(name, true, options.hidden, options.color, &dicts)?;
        self.layers_by_name.insert(name.to_owned(), slot);
        self.layer_count += 1;
        Ok(slot)
    }

    fn begin_definition_header(
        &mut self,
        caller: &str,
        attribute_dicts: &[(String, AttributeDict)],
    ) -> Result<(i32, usize), SkpWriteError> {
        if self.geometry_writer.is_some() {
            return Err(SkpWriteError::new(format!(
                "{caller} must be called before any add_face/add_instance calls"
            )));
        }
        if let Some(open) = self.open_definition {
            return Err(SkpWriteError::new(format!(
                "component definition '{}' is still open - call close() on it before starting another",
                self.definitions[open].name
            )));
        }
        if self.definition_writer.is_none() {
            self.definition_writer_start =
                scaffold::SCAFFOLD_NEXT_SLOT + self.material_shift() + self.layer_shift();
            let next_pid = self
                .layer_writer
                .as_ref()
                .map_or(self.material_writer.next_pid, |w| w.next_pid);
            self.definition_writer = Some(ArchiveWriter::new(
                self.definition_writer_start,
                self.post_layer_class_slot(),
                next_pid,
            ));
        }
        let writer = self.definition_writer.as_mut().expect("created above");
        let header = writer.write_definition_header(attribute_dicts)?;
        self.definition_count += 1;
        Ok(header)
    }

    fn push_definition(
        &mut self,
        (slot, count_patch_pos): (i32, usize),
        name: String,
        group_placement: Option<GroupPlacement>,
    ) -> ComponentDefinitionBuilder<'_> {
        self.definitions.push(DefinitionState {
            slot,
            name,
            count_patch_pos,
            topology: Topology::default(),
            new_entity_count: 0,
            closed: false,
            group_flag_pos: None,
            group_placement,
        });
        let index = self.definitions.len() - 1;
        self.open_definition = Some(index);
        ComponentDefinitionBuilder { skp: self, index }
    }

    /// Start a reusable component definition: add its geometry through the returned builder,
    /// `close()` it, then place it with [`add_instance`](Self::add_instance). Must come before
    /// any root-level geometry, and only one definition may be open at a time.
    pub fn add_component_definition(
        &mut self,
        name: &str,
        options: &DefinitionOptions,
    ) -> Result<ComponentDefinitionBuilder<'_>, SkpWriteError> {
        let dicts = single_dict(&options.attribute_dict_name, &options.attributes);
        let header = self.begin_definition_header("add_component_definition", &dicts)?;
        Ok(self.push_definition(header, name.to_owned(), None))
    }

    /// Start a group: add its geometry through the returned builder; `close()` places it at
    /// `options.translation`/`matrix3x3` automatically. Same ordering rule as
    /// [`add_component_definition`](Self::add_component_definition).
    pub fn add_group(
        &mut self,
        options: &GroupOptions,
    ) -> Result<ComponentDefinitionBuilder<'_>, SkpWriteError> {
        self.check_material_handle(options.material, "material")?;
        self.check_layer_handle(options.layer)?;
        let matrix3x3 = resolve_matrix3x3(options.matrix3x3, options.rotation)?;
        let header = self.begin_definition_header("add_group", &[])?;
        let placement = GroupPlacement {
            translation: options.translation,
            matrix3x3,
            material: options.material.unwrap_or(0),
            layer: options.layer.unwrap_or(0),
            attribute_dicts: single_dict(&options.attribute_dict_name, &options.attributes),
            hidden: options.hidden,
        };
        let name = options.name.clone().unwrap_or_else(|| "Group".to_owned());
        Ok(self.push_definition(header, name, Some(placement)))
    }

    /// Reopen the builder of one of this builder's definitions (e.g. to `close()` it later).
    pub fn definition(
        &mut self,
        handle: DefinitionHandle,
    ) -> Result<ComponentDefinitionBuilder<'_>, SkpWriteError> {
        let index = self.resolve(handle)?;
        Ok(ComponentDefinitionBuilder { skp: self, index })
    }

    /// Place one instance of a closed definition in the model.
    pub fn add_instance(
        &mut self,
        definition: DefinitionHandle,
        options: &InstanceOptions,
    ) -> Result<(), SkpWriteError> {
        self.check_material_handle(options.material, "material")?;
        self.check_layer_handle(options.layer)?;
        let index = self.resolve(definition)?;
        if !self.definitions[index].closed {
            return Err(SkpWriteError::new(format!(
                "component definition '{}' is still open - call close() on it before calling add_instance",
                self.definitions[index].name
            )));
        }
        let matrix3x3 = resolve_matrix3x3(options.matrix3x3, options.rotation)?;
        self.ensure_geometry_writer()?;
        let dicts = single_dict(&options.attribute_dict_name, &options.attributes);
        let def = &self.definitions[index];
        let placement = Placement {
            definition_slot: def.slot,
            name: options.name.as_deref().unwrap_or(&def.name),
            translation: options.translation,
            matrix3x3,
            material: options.material.unwrap_or(0),
            layer: options.layer.unwrap_or(0),
            attribute_dicts: &dicts,
            hidden: options.hidden,
        };
        let writer = self.geometry_writer.as_mut().expect("ensured above");
        writer.write_instance(&placement, &mut self.ids)?;
        self.new_entity_count += 1;
        self.face_count += 1;
        Ok(())
    }

    /// Place a SketchUp Image entity (File > Import > Image): a picture as its own object, with
    /// its own Outliner classification and explode behavior, unlike a textured face.
    ///
    /// `width`/`height` size its quad in inches (in the XY plane before placement); the image
    /// covers it edge to edge regardless of the file's own pixel aspect ratio. It registers a
    /// texture material internally, so it must come before any layer, definition or geometry.
    ///
    /// The quad's UVs are pinned explicitly with the default 1.0 applied size, which makes the
    /// reader's divide-by-applied-height a no-op. CImage's schema version is an unverified guess
    /// (no real SketchUp-authored Image sample was available).
    pub fn add_image(
        &mut self,
        image_path: impl AsRef<Path>,
        width: f64,
        height: f64,
        options: &ImageOptions,
    ) -> Result<(), SkpWriteError> {
        self.check_layer_handle(options.layer)?;
        let material_name = format!("__openskp_image_{}", self.material_count);
        let mat = self.add_texture_material(&material_name, image_path, None, None, None)?;
        let definition_name = format!("Image{}", self.definition_count);
        let image_def = {
            let mut def =
                self.add_component_definition(&definition_name, &DefinitionOptions::default())?;
            // Standard (0,0)-at-bottom-left, V increasing upward. Unlike every other UV fact here
            // this sense is NOT calibrated against real SketchUp (no ground truth available).
            let face_options = FaceOptions {
                material: Some(mat),
                front_uv: Some(vec![
                    ([0.0, 0.0, 0.0], [0.0, 0.0]),
                    ([width, 0.0, 0.0], [1.0, 0.0]),
                    ([0.0, height, 0.0], [0.0, 1.0]),
                ]),
                ..FaceOptions::default()
            };
            def.add_face(
                &[
                    [0.0, 0.0, 0.0],
                    [width, 0.0, 0.0],
                    [width, height, 0.0],
                    [0.0, height, 0.0],
                ],
                &face_options,
            )?;
            def.close()?
        };
        let matrix3x3 = resolve_matrix3x3(options.matrix3x3, options.rotation)?;
        self.ensure_geometry_writer()?;
        let slot = self.definitions[image_def.index].slot;
        let writer = self.geometry_writer.as_mut().expect("ensured above");
        writer.write_image(
            slot,
            options.translation,
            matrix3x3,
            options.layer.unwrap_or(0),
            options.hidden,
            &mut self.ids,
        )?;
        self.new_entity_count += 1;
        self.face_count += 1;
        Ok(())
    }

    /// Add one planar face: 3+ coplanar points (inches) in order, without repeating the first.
    /// Vertices/edges are shared with earlier faces wherever coordinates match exactly.
    pub fn add_face(
        &mut self,
        points: &[Point3],
        options: &FaceOptions,
    ) -> Result<(), SkpWriteError> {
        self.check_material_handle(options.material, "material")?;
        self.check_material_handle(options.back_material, "back_material")?;
        self.check_layer_handle(options.layer)?;
        if points.len() < 3 {
            return Err(SkpWriteError::new("a face needs at least 3 points"));
        }
        self.ensure_geometry_writer()?;
        let writer = self.geometry_writer.as_mut().expect("ensured above");
        self.new_entity_count += add_face_to(
            writer,
            &mut self.root_topology,
            &self.applied_sizes,
            points,
            options,
        )?;
        self.face_count += 1;
        Ok(())
    }

    /// Add one circular face - a true SketchUp circle (editable by radius, one "Curve" entity),
    /// not loose edges tracing it. `normal` need not be unit length.
    pub fn add_circle(
        &mut self,
        center: Point3,
        normal: Point3,
        radius: f64,
        options: &CircleOptions,
    ) -> Result<(), SkpWriteError> {
        self.check_material_handle(options.material, "material")?;
        self.check_material_handle(options.back_material, "back_material")?;
        self.check_layer_handle(options.layer)?;
        self.ensure_geometry_writer()?;
        let writer = self.geometry_writer.as_mut().expect("ensured above");
        self.new_entity_count += add_circle_to(
            writer,
            &mut self.root_topology,
            center,
            normal,
            radius,
            options,
        )?;
        self.face_count += 1;
        Ok(())
    }

    /// Add one partial (open) arc - a genuine arc entity, edges only, no face. Angles (radians)
    /// sweep from a fixed 0-angle reference direction in the arc's plane chosen from `normal`;
    /// either direction and sweeps beyond a full turn are valid.
    pub fn add_arc(
        &mut self,
        center: Point3,
        normal: Point3,
        radius: f64,
        start_angle: f64,
        end_angle: f64,
        options: &ArcOptions,
    ) -> Result<(), SkpWriteError> {
        self.ensure_geometry_writer()?;
        let writer = self.geometry_writer.as_mut().expect("ensured above");
        self.new_entity_count += add_arc_to(
            writer,
            &mut self.root_topology,
            center,
            normal,
            radius,
            start_angle,
            end_angle,
            options,
        )?;
        self.face_count += 1;
        Ok(())
    }

    /// Add one freeform polyline (at least 2 points) - a chain of edges grouped into one genuine
    /// "Curve" entity. No face.
    pub fn add_polyline(
        &mut self,
        points: &[Point3],
        options: &PolylineOptions,
    ) -> Result<(), SkpWriteError> {
        self.ensure_geometry_writer()?;
        let writer = self.geometry_writer.as_mut().expect("ensured above");
        self.new_entity_count += add_polyline_to(writer, &mut self.root_topology, points, options)?;
        self.face_count += 1;
        Ok(())
    }

    /// Add a FREE linear dimension between two world-space points. `offset` is the dimension
    /// line's signed offset from the measured segment, in inches (C++ default:
    /// [`DEFAULT_DIMENSION_OFFSET`]).
    pub fn add_dimension(
        &mut self,
        p1: Point3,
        p2: Point3,
        offset: f64,
    ) -> Result<(), SkpWriteError> {
        check_finite_point(p1, "dimension point")?;
        check_finite_point(p2, "dimension point")?;
        if !offset.is_finite() {
            return Err(SkpWriteError::new("dimension offset must be finite"));
        }
        self.ensure_geometry_writer()?;
        let writer = self.geometry_writer.as_mut().expect("ensured above");
        writer.write_dimension(p1, p2, offset, None)?;
        self.new_entity_count += 1;
        self.face_count += 1;
        Ok(())
    }

    /// Add a linear dimension placed by an offset vector (inches, in the same space as the
    /// endpoints): its component perpendicular to `p1`->`p2` sets the plane and side of the
    /// dimension line. Coordinates must be finite and the endpoints distinct; a zero offset is
    /// supported. (C++: the `add_dimension` overload taking a `Point3` offset.)
    pub fn add_dimension_with_offset_vector(
        &mut self,
        p1: Point3,
        p2: Point3,
        offset_vector: Point3,
    ) -> Result<(), SkpWriteError> {
        self.ensure_geometry_writer()?;
        let writer = self.geometry_writer.as_mut().expect("ensured above");
        writer.write_dimension(p1, p2, 0.0, Some(offset_vector))?;
        self.new_entity_count += 1;
        self.face_count += 1;
        Ok(())
    }

    /// Add a leader text anchored at `point`, its label at `point + leader` (C++ default:
    /// [`DEFAULT_TEXT_LEADER`]).
    pub fn add_text(
        &mut self,
        text: &str,
        point: Point3,
        leader: Point3,
    ) -> Result<(), SkpWriteError> {
        check_finite_point(point, "text point")?;
        check_finite_point(leader, "text leader")?;
        self.ensure_geometry_writer()?;
        let writer = self.geometry_writer.as_mut().expect("ensured above");
        writer.write_text(text, point, leader)?;
        self.new_entity_count += 1;
        self.face_count += 1;
        Ok(())
    }

    /// Add a construction (guide) line through `point`: pass exactly one of `point2` (a bounded
    /// segment, like `Entities#add_cline(p1, p2)`) or `direction` (unbounded, like
    /// `Entities#add_cline(point, vector)`). `options` places the guide on a layer and/or hides
    /// it.
    pub fn add_construction_line(
        &mut self,
        point: Point3,
        point2: Option<Point3>,
        direction: Option<Point3>,
        options: &GuideOptions,
    ) -> Result<(), SkpWriteError> {
        check_finite_point(point, "construction line point")?;
        if let Some(p2) = point2 {
            check_finite_point(p2, "construction line point2")?;
        }
        if let Some(d) = direction {
            check_finite_point(d, "construction line direction")?;
        }
        self.check_layer_handle(options.layer)?;
        self.ensure_geometry_writer()?;
        let writer = self.geometry_writer.as_mut().expect("ensured above");
        writer.write_construction_line(point, point2, direction, options)?;
        self.new_entity_count += 1;
        self.face_count += 1;
        Ok(())
    }

    /// Add a construction (guide) point. `options` places the guide on a layer and/or hides it.
    pub fn add_construction_point(
        &mut self,
        position: Point3,
        options: &GuideOptions,
    ) -> Result<(), SkpWriteError> {
        check_finite_point(position, "construction point")?;
        self.check_layer_handle(options.layer)?;
        self.ensure_geometry_writer()?;
        let writer = self.geometry_writer.as_mut().expect("ensured above");
        writer.write_construction_point(position, options);
        self.new_entity_count += 1;
        self.face_count += 1;
        Ok(())
    }

    /// Add a section plane through `point` with `normal` (any length), like
    /// `Entities#add_section_plane([point, normal])`.
    pub fn add_section_plane(
        &mut self,
        point: Point3,
        normal: Point3,
    ) -> Result<(), SkpWriteError> {
        check_finite_point(point, "section plane point")?;
        check_finite_point(normal, "section plane normal")?;
        self.ensure_geometry_writer()?;
        let writer = self.geometry_writer.as_mut().expect("ensured above");
        writer.write_section_plane(point, normal)?;
        self.new_entity_count += 1;
        self.face_count += 1;
        Ok(())
    }

    /// The finished file's bytes. Fails if nothing was added at the root level (a closed group
    /// counts) or a definition is still open while groups wait to be placed.
    pub fn to_bytes(&mut self) -> Result<Vec<u8>, SkpWriteError> {
        if !self.pending_groups.is_empty() {
            // A file with only groups would otherwise never flush them.
            self.ensure_geometry_writer()?;
        }
        if self.face_count == 0 {
            return Err(SkpWriteError::new(
                "no geometry added - call add_face at least once before saving",
            ));
        }
        let geometry = self.geometry_writer.as_ref().expect("root geometry exists");

        let mshift = self.material_shift();
        let lshift = self.layer_shift();
        let dshift = self.definition_shift();
        let geometry_shift =
            geometry.next_slot - (scaffold::SCAFFOLD_NEXT_SLOT + mshift + lshift + dshift);
        let data: &[u8] = scaffold::SCAFFOLD_BLANK_V17;

        let mut out =
            Vec::with_capacity(data.len() + self.material_writer.buf.len() + geometry.buf.len());
        out.extend_from_slice(&data[..scaffold::MATERIAL_INSERT_POS - 4]);
        if let Some(options) = &self.document_options {
            patch_document_prefix(&mut out, options);
        }
        let pid_at = scaffold::PID_COUNTER_POS;
        out[pid_at..pid_at + 8].copy_from_slice(&geometry.next_pid.to_le_bytes());
        let iso = scaffold::ISO_CAMERA_PREFIX_OFFSET;
        out[iso..iso + scaffold::ISO_CAMERA_PREFIX_PATCH.len()]
            .copy_from_slice(&scaffold::ISO_CAMERA_PREFIX_PATCH);
        out.put_u32(self.material_count as u32);
        out.extend_from_slice(&self.material_writer.buf);

        // material_insert_pos..layer_insert_pos: Layer0 plus the layer-count field; only the
        // count changes.
        let layer_count_pos =
            out.len() + (scaffold::LAYER_COUNT_POS - scaffold::MATERIAL_INSERT_POS);
        out.extend_from_slice(&data[scaffold::MATERIAL_INSERT_POS..scaffold::LAYER_INSERT_POS]);
        write_u32_at(
            &mut out,
            layer_count_pos,
            (scaffold::ORIG_LAYER_COUNT + self.layer_count) as u32,
        );
        if let Some(w) = &self.layer_writer {
            out.extend_from_slice(&w.buf);
        }

        // layer_insert_pos..def_count_pos: just the active-layer anchor, shifted by the material
        // shift only - Layer0 never moves because more layers are appended after it.
        let mut anchor = data[scaffold::LAYER_INSERT_POS..scaffold::DEF_COUNT_POS].to_vec();
        if mshift != 0 {
            shift_ref(&mut anchor, scaffold::ACTIVE_LAYER_ANCHOR_REL, mshift);
        }
        out.extend_from_slice(&anchor);

        out.put_u32((scaffold::ORIG_DEF_COUNT + self.definition_count) as u32);
        if let Some(w) = &self.definition_writer {
            out.extend_from_slice(&w.buf);
        }
        out.extend_from_slice(&data[scaffold::DEF_COUNT_POS + 4..scaffold::ROOT_COUNT_POS]);
        out.put_u32((scaffold::ORIG_ROOT_COUNT + self.new_entity_count) as u32);
        out.extend_from_slice(&data[scaffold::ROOT_COUNT_POS + 4..scaffold::TAIL_POS]);
        out.extend_from_slice(&geometry.buf);

        // The tail-ref shifts and iso-camera patches both index this tail. A shift that widens
        // to the 6-byte escape form grows it, pushing every later position forward, so all are
        // applied in ascending original offset while tracking that growth.
        let mut tail = data[scaffold::TAIL_POS..].to_vec();
        if let Some(options) = &self.document_options {
            patch_style_items(&mut tail, 4070 - scaffold::TAIL_POS, options);
            patch_style_items(&mut tail, 4951 - scaffold::TAIL_POS, options);
        }
        let total_tail_shift = mshift + lshift + dshift + geometry_shift;
        let mut actions: Vec<(usize, Option<&[u8]>)> = scaffold::TAIL_REF_POSITIONS
            .iter()
            .map(|&pos| (pos, None))
            .chain(
                scaffold::ISO_CAMERA_TAIL_PATCHES
                    .iter()
                    .map(|&(pos, bytes)| (pos, Some(bytes))),
            )
            .collect();
        actions.sort_by_key(|&(pos, _)| pos);
        let mut growth = 0;
        for (pos, patch) in actions {
            let here = pos + growth;
            match patch {
                None => growth += shift_ref(&mut tail, here, total_tail_shift),
                Some(bytes) => tail[here..here + bytes.len()].copy_from_slice(bytes),
            }
        }
        out.extend_from_slice(&tail);
        Ok(out)
    }

    /// Write the finished file to `path`.
    pub fn save(&mut self, path: impl AsRef<Path>) -> Result<(), SkpWriteError> {
        let bytes = self.to_bytes()?;
        let path = path.as_ref();
        let mut file = File::create(path).map_err(|e| {
            SkpWriteError::new(format!(
                "cannot open output file for writing: {}: {e}",
                path.display()
            ))
        })?;
        file.write_all(&bytes).map_err(|e| {
            SkpWriteError::new(format!(
                "failed writing output file: {}: {e}",
                path.display()
            ))
        })
    }
}

/// Accumulates one component/group definition's geometry. Obtained from
/// [`SkpBuilder::add_component_definition`], [`SkpBuilder::add_group`] or
/// [`SkpBuilder::definition`]; it borrows the builder, so finish the definition (`close()`)
/// before going back to the builder - only one definition can be open at a time anyway.
///
/// ```no_run
/// # use openskp::create::*;
/// let mut builder = create();
/// let mut chair = builder.add_component_definition("Chair", &DefinitionOptions::default())?;
/// chair.add_face(&[[0.0, 0.0, 0.0], [20.0, 0.0, 0.0], [20.0, 20.0, 0.0], [0.0, 20.0, 0.0]], &FaceOptions::default())?;
/// let chair = chair.close()?;
/// builder.add_instance(chair, &InstanceOptions { translation: [100.0, 0.0, 0.0], ..Default::default() })?;
/// # Ok::<(), SkpWriteError>(())
/// ```
#[derive(Debug)]
pub struct ComponentDefinitionBuilder<'a> {
    skp: &'a mut SkpBuilder,
    index: usize,
}

impl ComponentDefinitionBuilder<'_> {
    fn state(&self) -> &DefinitionState {
        &self.skp.definitions[self.index]
    }

    /// This definition's name.
    pub fn name(&self) -> &str {
        &self.state().name
    }

    /// True once `close()` has succeeded; no more geometry may be added.
    pub fn closed(&self) -> bool {
        self.state().closed
    }

    /// This definition's archive slot number.
    pub fn slot(&self) -> i32 {
        self.state().slot
    }

    /// A handle for placing this definition (valid even before it closes).
    pub fn handle(&self) -> DefinitionHandle {
        self.skp.handle(self.index)
    }

    fn check_writable(&self, action: &str) -> Result<(), SkpWriteError> {
        if self.closed() {
            return Err(SkpWriteError::new(format!(
                "component definition '{}' has already closed (close() was already called) - cannot add more {action} to it",
                self.name()
            )));
        }
        Ok(())
    }

    /// The shared definition writer and this definition's state.
    fn parts(&mut self) -> (&mut ArchiveWriter, &mut DefinitionState) {
        let skp = &mut *self.skp;
        let writer = skp
            .definition_writer
            .as_mut()
            .expect("a definition always has a writer");
        (writer, &mut skp.definitions[self.index])
    }

    /// Add one planar face - like [`SkpBuilder::add_face`], but vertices/edges are shared only
    /// within this definition.
    pub fn add_face(
        &mut self,
        points: &[Point3],
        options: &FaceOptions,
    ) -> Result<(), SkpWriteError> {
        self.check_writable("faces")?;
        self.skp
            .check_material_handle(options.material, "material")?;
        self.skp
            .check_material_handle(options.back_material, "back_material")?;
        self.skp.check_layer_handle(options.layer)?;
        if points.len() < 3 {
            return Err(SkpWriteError::new("a face needs at least 3 points"));
        }
        let skp = &mut *self.skp;
        let writer = skp
            .definition_writer
            .as_mut()
            .expect("a definition always has a writer");
        let def = &mut skp.definitions[self.index];
        def.new_entity_count += add_face_to(
            writer,
            &mut def.topology,
            &skp.applied_sizes,
            points,
            options,
        )?;
        Ok(())
    }

    /// Add one circular face - like [`SkpBuilder::add_circle`].
    pub fn add_circle(
        &mut self,
        center: Point3,
        normal: Point3,
        radius: f64,
        options: &CircleOptions,
    ) -> Result<(), SkpWriteError> {
        self.check_writable("faces")?;
        self.skp
            .check_material_handle(options.material, "material")?;
        self.skp
            .check_material_handle(options.back_material, "back_material")?;
        self.skp.check_layer_handle(options.layer)?;
        let (writer, def) = self.parts();
        def.new_entity_count +=
            add_circle_to(writer, &mut def.topology, center, normal, radius, options)?;
        Ok(())
    }

    /// Add one partial (open) arc - like [`SkpBuilder::add_arc`].
    pub fn add_arc(
        &mut self,
        center: Point3,
        normal: Point3,
        radius: f64,
        start_angle: f64,
        end_angle: f64,
        options: &ArcOptions,
    ) -> Result<(), SkpWriteError> {
        self.check_writable("arcs")?;
        let (writer, def) = self.parts();
        def.new_entity_count += add_arc_to(
            writer,
            &mut def.topology,
            center,
            normal,
            radius,
            start_angle,
            end_angle,
            options,
        )?;
        Ok(())
    }

    /// Add one freeform polyline - like [`SkpBuilder::add_polyline`].
    pub fn add_polyline(
        &mut self,
        points: &[Point3],
        options: &PolylineOptions,
    ) -> Result<(), SkpWriteError> {
        self.check_writable("polylines")?;
        let (writer, def) = self.parts();
        def.new_entity_count += add_polyline_to(writer, &mut def.topology, points, options)?;
        Ok(())
    }

    /// Add a free linear dimension - like [`SkpBuilder::add_dimension`], in definition-local
    /// inches.
    pub fn add_dimension(
        &mut self,
        p1: Point3,
        p2: Point3,
        offset: f64,
    ) -> Result<(), SkpWriteError> {
        check_finite_point(p1, "dimension point")?;
        check_finite_point(p2, "dimension point")?;
        if !offset.is_finite() {
            return Err(SkpWriteError::new("dimension offset must be finite"));
        }
        self.check_writable("dimensions")?;
        let (writer, def) = self.parts();
        writer.write_dimension(p1, p2, offset, None)?;
        def.new_entity_count += 1;
        Ok(())
    }

    /// Add a linear dimension placed by an offset vector - like
    /// [`SkpBuilder::add_dimension_with_offset_vector`], in definition-local inches.
    pub fn add_dimension_with_offset_vector(
        &mut self,
        p1: Point3,
        p2: Point3,
        offset_vector: Point3,
    ) -> Result<(), SkpWriteError> {
        self.check_writable("dimensions")?;
        let (writer, def) = self.parts();
        writer.write_dimension(p1, p2, 0.0, Some(offset_vector))?;
        def.new_entity_count += 1;
        Ok(())
    }

    /// Add a construction line - like [`SkpBuilder::add_construction_line`], in
    /// definition-local inches.
    pub fn add_construction_line(
        &mut self,
        point: Point3,
        point2: Option<Point3>,
        direction: Option<Point3>,
        options: &GuideOptions,
    ) -> Result<(), SkpWriteError> {
        check_finite_point(point, "construction line point")?;
        if let Some(p2) = point2 {
            check_finite_point(p2, "construction line point2")?;
        }
        if let Some(d) = direction {
            check_finite_point(d, "construction line direction")?;
        }
        self.check_writable("construction lines")?;
        self.skp.check_layer_handle(options.layer)?;
        let (writer, def) = self.parts();
        writer.write_construction_line(point, point2, direction, options)?;
        def.new_entity_count += 1;
        Ok(())
    }

    /// Add a construction point - like [`SkpBuilder::add_construction_point`], in
    /// definition-local inches.
    pub fn add_construction_point(
        &mut self,
        position: Point3,
        options: &GuideOptions,
    ) -> Result<(), SkpWriteError> {
        check_finite_point(position, "construction point")?;
        self.check_writable("construction points")?;
        self.skp.check_layer_handle(options.layer)?;
        let (writer, def) = self.parts();
        writer.write_construction_point(position, options);
        def.new_entity_count += 1;
        Ok(())
    }

    /// The index of `definition` for nesting inside this one: same builder, not this one.
    /// (Definitions are strictly ordered, and only one is open at a time, so any other
    /// definition of this builder is already closed - which also rules out cycles.)
    fn nested_target(
        &self,
        definition: DefinitionHandle,
        what: &str,
    ) -> Result<usize, SkpWriteError> {
        let target = self.skp.resolve(definition)?;
        if target == self.index {
            return Err(SkpWriteError::new(format!(
                "component definition '{}' cannot nest {what} of itself",
                self.name()
            )));
        }
        Ok(target)
    }

    fn write_nested(
        &mut self,
        target: usize,
        as_group: bool,
        nested: NestedPlacement<'_>,
    ) -> Result<(), SkpWriteError> {
        let skp = &mut *self.skp;
        let writer = skp
            .definition_writer
            .as_mut()
            .expect("a definition always has a writer");
        let placed = &skp.definitions[target];
        let placement = Placement {
            definition_slot: placed.slot,
            name: nested.name.unwrap_or(&placed.name),
            translation: nested.translation,
            matrix3x3: nested.matrix3x3,
            material: nested.material.unwrap_or(0),
            layer: nested.layer.unwrap_or(0),
            attribute_dicts: nested.attribute_dicts,
            hidden: nested.hidden,
        };
        if as_group {
            writer.write_group(&placement, &mut skp.ids)?;
            // A definition placed as a group is a group's definition to SketchUp: flag it (it
            // closed earlier in this same buffer). One placed both ways reads as a group.
            if let Some(pos) = placed.group_flag_pos {
                write_u32_at(&mut writer.buf, pos, 1);
            }
        } else {
            writer.write_instance(&placement, &mut skp.ids)?;
        }
        skp.definitions[self.index].new_entity_count += 1;
        Ok(())
    }

    /// Place an instance of another (closed) definition of the same builder inside this one.
    pub fn add_instance(
        &mut self,
        definition: DefinitionHandle,
        options: &InstanceOptions,
    ) -> Result<(), SkpWriteError> {
        self.check_writable("instances")?;
        self.skp
            .check_material_handle(options.material, "material")?;
        self.skp.check_layer_handle(options.layer)?;
        let target = self.nested_target(definition, "an instance")?;
        let matrix3x3 = resolve_matrix3x3(options.matrix3x3, options.rotation)?;
        let dicts = single_dict(&options.attribute_dict_name, &options.attributes);
        let nested = NestedPlacement {
            name: options.name.as_deref(),
            translation: options.translation,
            matrix3x3,
            material: options.material,
            layer: options.layer,
            attribute_dicts: &dicts,
            hidden: options.hidden,
        };
        self.write_nested(target, false, nested)
    }

    /// Place another definition inside this one as a *group* (`CGroup`) rather than a component
    /// instance. A nested group can't be declared inline (this format can't embed one
    /// definition's declaration inside another's): build it with `add_component_definition`
    /// first, then place it here.
    pub fn add_group_instance(
        &mut self,
        definition: DefinitionHandle,
        options: &GroupInstanceOptions,
    ) -> Result<(), SkpWriteError> {
        self.check_writable("groups")?;
        self.skp
            .check_material_handle(options.material, "material")?;
        self.skp.check_layer_handle(options.layer)?;
        let target = self.nested_target(definition, "a group instance")?;
        let matrix3x3 = resolve_matrix3x3(options.matrix3x3, options.rotation)?;
        let dicts = single_dict(&options.attribute_dict_name, &options.attributes);
        let nested = NestedPlacement {
            name: options.name.as_deref(),
            translation: options.translation,
            matrix3x3,
            material: options.material,
            layer: options.layer,
            attribute_dicts: &dicts,
            hidden: options.hidden,
        };
        self.write_nested(target, true, nested)
    }

    /// Finalize this definition: patch its entity count, write its tail (GUID/name/thumbnail)
    /// and, for a group, queue its placement. Fails if it has no geometry or is already closed.
    /// Returns the definition's handle.
    pub fn close(&mut self) -> Result<DefinitionHandle, SkpWriteError> {
        let handle = self.handle();
        let skp = &mut *self.skp;
        let def = &mut skp.definitions[self.index];
        if def.closed {
            return Err(SkpWriteError::new(format!(
                "component definition '{}' has already closed",
                def.name
            )));
        }
        if def.new_entity_count == 0 {
            return Err(SkpWriteError::new(format!(
                "component definition '{}' has no geometry - add at least one face",
                def.name
            )));
        }
        let writer = skp
            .definition_writer
            .as_mut()
            .expect("a definition always has a writer");
        write_u32_at(
            &mut writer.buf,
            def.count_patch_pos,
            def.new_entity_count as u32,
        );
        let is_group = def.group_placement.is_some();
        def.group_flag_pos =
            Some(writer.write_definition_tail(&def.name, is_group, &mut skp.ids)?);
        def.closed = true;
        skp.open_definition = None;
        if let Some(placement) = &def.group_placement {
            skp.pending_groups.push((self.index, placement.clone()));
        }
        Ok(handle)
    }
}
