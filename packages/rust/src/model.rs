//! The parsed SketchUp model (`model.hpp` / `model.cpp`).

use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::fmt;
use std::io;
use std::path::Path;

/// Unique entity id from the file.
pub type EntityId = i64;
/// Raw bytes.
pub type ByteBuffer = Vec<u8>;
/// `(x, y, z)`.
pub type Vec3 = [f64; 3];
/// RGB 0..255.
pub type Color3 = [u8; 3];
/// RGBA 0..255.
pub type Color4 = [u8; 4];

/// 1 metre = 39.37007874015748 inches (SketchUp native unit). Shared by every
/// exporter that takes a coordinate scale factor.
pub const METRES_TO_INCHES: f64 = 39.37007874015748;

// ── attribute values ─────────────────────────────────────────────────────

/// The C++ `ParsedAttribute::Kind` an attribute value maps to. The Rust enum
/// [`ParsedAttribute`] distinguishes a few more SketchUp types (Point3d vs
/// Vector3d, Time) that the C++ port folds into `Vec` / `Integer`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum AttributeKind {
    Null,
    String,
    Integer,
    Float,
    Vec,
    Array,
    Boolean,
    Length,
}

/// One attribute-dictionary value, covering every value type SketchUp stores
/// (legacy `CAttributeNamed` types 0/4/6/7/9/10/11/12/17/18 and the VFF `A438`
/// wrapper's AD38/A738/A938/AF38/AA38/B438/B538/AE38 tags):
///
/// * `Null` - an explicitly present key with no value (and any value tag this
///   reader does not recognise, rather than a wrong guess).
/// * `Length` keeps its own kind (VFF `AF38`, legacy type 12) so it can be
///   written back as a Length; it stringifies exactly like `Float`.
/// * `Boolean` (VFF `AA38`, legacy type 7) stringifies as `1` / `0`.
/// * `Time` (legacy type 9, a `time_t`) stringifies like `Integer`.
///
/// `Instance::properties` stays a `map<string,string>` view of a dictionary
/// named `dynamic_attributes`; scene / JSON / IFC stringify every named
/// dictionary with [`ParsedAttribute::to_string`]. The model keeps types.
#[derive(Debug, Clone, PartialEq, Default)]
pub enum ParsedAttribute {
    #[default]
    Null,
    String(String),
    Integer(i64),
    Float(f64),
    /// SketchUp `Length` (inches).
    Length(f64),
    Boolean(bool),
    /// `time_t` seconds.
    Time(i64),
    Point3d(Vec3),
    Vector3d(Vec3),
    Array(Vec<ParsedAttribute>),
}

impl ParsedAttribute {
    /// The C++ kind this value maps to.
    pub fn kind(&self) -> AttributeKind {
        match self {
            ParsedAttribute::Null => AttributeKind::Null,
            ParsedAttribute::String(_) => AttributeKind::String,
            ParsedAttribute::Integer(_) | ParsedAttribute::Time(_) => AttributeKind::Integer,
            ParsedAttribute::Float(_) => AttributeKind::Float,
            ParsedAttribute::Length(_) => AttributeKind::Length,
            ParsedAttribute::Boolean(_) => AttributeKind::Boolean,
            ParsedAttribute::Point3d(_) | ParsedAttribute::Vector3d(_) => AttributeKind::Vec,
            ParsedAttribute::Array(_) => AttributeKind::Array,
        }
    }

    /// The string, or `""` (C++ `text`).
    pub fn text(&self) -> &str {
        match self {
            ParsedAttribute::String(s) => s,
            _ => "",
        }
    }

    /// Integer / Time / Boolean (1/0) value, or 0 (C++ `integer`).
    pub fn integer(&self) -> i64 {
        match self {
            ParsedAttribute::Integer(n) | ParsedAttribute::Time(n) => *n,
            ParsedAttribute::Boolean(b) => i64::from(*b),
            _ => 0,
        }
    }

    /// Float / Length value, or 0 (C++ `number`).
    pub fn number(&self) -> f64 {
        match self {
            ParsedAttribute::Float(n) | ParsedAttribute::Length(n) => *n,
            _ => 0.0,
        }
    }

    /// Point3d / Vector3d value, or zeros (C++ `vec`).
    pub fn vec(&self) -> Vec3 {
        match self {
            ParsedAttribute::Point3d(v) | ParsedAttribute::Vector3d(v) => *v,
            _ => [0.0; 3],
        }
    }

    /// Array items, or an empty slice (C++ `array`).
    pub fn array(&self) -> &[ParsedAttribute] {
        match self {
            ParsedAttribute::Array(a) => a,
            _ => &[],
        }
    }

    pub fn is_null(&self) -> bool {
        matches!(self, ParsedAttribute::Null)
    }
}

/// Shortest round-trip text of a double, as C++ `std::to_chars(double)` writes
/// it: the shortest digits, in fixed or scientific notation, whichever is
/// shorter (fixed on a tie); scientific uses a signed, two-digit-minimum
/// exponent (`1e+20`, `1e-07`).
pub fn format_double(v: f64) -> String {
    if v.is_nan() {
        return if v.is_sign_negative() {
            "-nan".into()
        } else {
            "nan".into()
        };
    }
    if v.is_infinite() {
        return if v < 0.0 { "-inf".into() } else { "inf".into() };
    }
    let neg = v.is_sign_negative();
    let sci = format!("{:e}", v.abs());
    let (mantissa, exp) = sci.split_once('e').unwrap_or((sci.as_str(), "0"));
    let exp: i32 = exp.parse().unwrap_or(0);
    let digits: String = mantissa.chars().filter(|c| *c != '.').collect();
    let n = digits.len() as i32;

    let mut scientific = String::new();
    scientific.push_str(&digits[..1]);
    if n > 1 {
        scientific.push('.');
        scientific.push_str(&digits[1..]);
    }
    scientific.push('e');
    scientific.push(if exp < 0 { '-' } else { '+' });
    let e = exp.unsigned_abs();
    if e < 10 {
        scientific.push('0');
    }
    scientific.push_str(&e.to_string());

    let fixed = if exp >= 0 {
        if n <= exp + 1 {
            let mut s = digits.clone();
            for _ in 0..(exp + 1 - n) {
                s.push('0');
            }
            s
        } else {
            let split = (exp + 1) as usize;
            format!("{}.{}", &digits[..split], &digits[split..])
        }
    } else {
        let mut s = String::from("0.");
        for _ in 0..(-exp - 1) {
            s.push('0');
        }
        s.push_str(&digits);
        s
    };

    let body = if fixed.len() <= scientific.len() {
        fixed
    } else {
        scientific
    };
    if neg {
        format!("-{body}")
    } else {
        body
    }
}

impl fmt::Display for ParsedAttribute {
    /// Same formatting as Python's `_stringify_vff_attr_value` / C++
    /// `ParsedAttribute::to_string`.
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            ParsedAttribute::Null => Ok(()),
            ParsedAttribute::String(s) => f.write_str(s),
            ParsedAttribute::Integer(n) | ParsedAttribute::Time(n) => write!(f, "{n}"),
            ParsedAttribute::Boolean(b) => write!(f, "{}", i64::from(*b)),
            ParsedAttribute::Float(n) | ParsedAttribute::Length(n) => {
                f.write_str(&format_double(*n))
            }
            ParsedAttribute::Point3d(v) | ParsedAttribute::Vector3d(v) => write!(
                f,
                "{},{},{}",
                format_double(v[0]),
                format_double(v[1]),
                format_double(v[2])
            ),
            ParsedAttribute::Array(items) => {
                let mut joined = String::new();
                for item in items {
                    if !joined.is_empty() {
                        joined.push(',');
                    }
                    joined.push_str(&item.to_string());
                }
                f.write_str(&joined)
            }
        }
    }
}

impl PartialEq<str> for ParsedAttribute {
    fn eq(&self, other: &str) -> bool {
        matches!(self, ParsedAttribute::String(s) if s == other)
    }
}

impl PartialEq<&str> for ParsedAttribute {
    fn eq(&self, other: &&str) -> bool {
        *self == **other
    }
}

impl PartialEq<String> for ParsedAttribute {
    fn eq(&self, other: &String) -> bool {
        *self == **other
    }
}

impl From<&str> for ParsedAttribute {
    fn from(s: &str) -> Self {
        ParsedAttribute::String(s.to_owned())
    }
}

impl From<String> for ParsedAttribute {
    fn from(s: String) -> Self {
        ParsedAttribute::String(s)
    }
}

/// One named dictionary's entries.
pub type ParsedAttrDict = BTreeMap<String, ParsedAttribute>;
/// Dictionaries keyed by their declared name.
pub type ParsedAttrDictionaries = BTreeMap<String, ParsedAttrDict>;
/// A stringified dictionary set (scene / JSON / IFC view).
pub type StringDictionaries = BTreeMap<String, BTreeMap<String, String>>;

/// Stringifies every value of one dictionary.
pub fn stringify_attr_dict(src: &ParsedAttrDict) -> BTreeMap<String, String> {
    src.iter()
        .map(|(k, v)| (k.clone(), v.to_string()))
        .collect()
}

/// Stringifies every value of every dictionary.
pub fn stringify_attr_dictionaries(src: &ParsedAttrDictionaries) -> StringDictionaries {
    src.iter()
        .map(|(k, d)| (k.clone(), stringify_attr_dict(d)))
        .collect()
}

// ── geometry ─────────────────────────────────────────────────────────────

/// 3D point in raw SketchUp space (Z-up, inches).
#[derive(Debug, Clone, Copy, PartialEq, Default)]
pub struct Vertex {
    pub id: EntityId,
    pub x: f64,
    pub y: f64,
    pub z: f64,
}

/// Straight line segment connecting two vertices.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct Edge {
    pub id: EntityId,
    pub v1_id: EntityId,
    pub v2_id: EntityId,
    /// Soft edge flag (suppresses edge rendering across coplanar faces).
    pub soft: bool,
    /// Smooth edge flag (enables vertex normal interpolation).
    pub smooth: bool,
    /// Hidden edge flag (explicit "Hide" bit on this edge).
    pub hidden: bool,
    /// SketchUp's own `Edge#curve` grouping: the id of the Curve this edge
    /// belongs to, or `None`. Edges sharing a curve_id were drawn as one
    /// continuous multi-segment line or arc; if the curve is an arc,
    /// [`Definition::arcs`] has it under the same id.
    pub curve_id: Option<EntityId>,
    /// This edge's own layer, resolved to a name (Python port's `Edge.layer`,
    /// VFF `D007 > D207`, the legacy drawbase's layer field); `""` when the
    /// file carries no explicit override (legacy: the default id 0), the raw id
    /// as text when it names no known layer. Only meaningful for a *loose*
    /// edge - a face-bounded edge's layer is cosmetic.
    pub layer: String,
    /// The raw layer id behind [`Edge::layer`], or `None`.
    pub layer_id: Option<EntityId>,
}

/// The analytic arc behind a curve: SketchUp's own ArcCurve.
///
/// The arc is the set of points `center + cos(t) * x_axis + sin(t) * y_axis`
/// for t from `start_angle` to `end_angle`. For a circle `x_axis` and `y_axis`
/// are perpendicular and both of length [`ArcCurve::radius`]; an arc scaled
/// non-uniformly stores an affine image of a circle. Lengths are in inches, in
/// the definition's local space.
#[derive(Debug, Clone, Copy, PartialEq, Default)]
pub struct ArcCurve {
    pub center: Vec3,
    /// Unit normal of the arc's plane.
    pub normal: Vec3,
    /// Vector from the centre to the arc's start (length = radius).
    pub x_axis: Vec3,
    /// normal x x_axis.
    pub y_axis: Vec3,
    /// Start parameter in radians.
    pub start_angle: f64,
    /// End parameter in radians. A full circle has `end_angle == start_angle`
    /// in a classic (pre-2021) file and `start_angle + 2 pi` in a 2021+ file;
    /// [`ArcCurve::is_full_circle`] covers both.
    pub end_angle: f64,
}

impl ArcCurve {
    /// Length of `x_axis`.
    pub fn radius(&self) -> f64 {
        let a = self.x_axis;
        (a[0] * a[0] + a[1] * a[1] + a[2] * a[2]).sqrt()
    }

    pub fn is_full_circle(&self) -> bool {
        let two_pi = 2.0 * (-1.0f64).acos();
        let mut sweep = (self.end_angle - self.start_angle) % two_pi;
        if sweep < 0.0 {
            sweep += two_pi;
        }
        sweep.min(two_pi - sweep) < 1e-9
    }
}

/// Oriented edge reference within a face loop.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct CoEdge {
    pub edge_id: EntityId,
    /// +1 = same direction as the edge, -1 = reversed.
    pub orientation: i64,
}

/// Planar polygon bounded by outer and inner (hole) edge loops.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct Face {
    pub id: EntityId,
    /// `loops[0]` = outer boundary, `loops[1..]` = holes.
    pub loops: Vec<Vec<CoEdge>>,
    pub normal: Option<Vec3>,
    /// Front-face material id (references [`SkpModel::materials`]).
    pub material_id: Option<EntityId>,
    /// Back-face material id.
    pub back_material_id: Option<EntityId>,
    /// Front-face 3x3 row-major UV transform.
    pub uv_transform: Option<[f64; 9]>,
    /// Back-face 3x3 row-major UV transform.
    pub uv_transform_back: Option<[f64; 9]>,
    /// The texture is PROJECTED (e.g. the Add Location terrain drape): its UVs
    /// run in the projection plane's frame, not the face frame.
    pub uv_projected: bool,
    pub uv_projected_back: bool,
    /// SketchUp's "Hide" on this face (not a layer visibility toggle).
    pub hidden: bool,
    /// Name of the face's layer. `""` for faces on the default layer, which
    /// VFF files don't record per face, and for every legacy face.
    pub layer: String,
    /// The raw layer id behind [`Face::layer`] (Python port's `Face.layer`), or
    /// `None`. A legacy face always carries one (its drawbase layer field, `0`
    /// for the default layer), even though its `layer` name stays `""`.
    pub layer_id: Option<EntityId>,
}

// ── layers, materials, styles ───────────────────────────────────────────

/// Organization layer (tag).
#[derive(Debug, Clone, PartialEq)]
pub struct Layer {
    pub name: String,
    pub color: Color3,
    /// Whether the layer's visibility is switched off.
    pub hidden: bool,
    /// Named `CAttributeNamed` dictionaries on this `CLayer` (legacy only).
    pub attribute_dictionaries: StringDictionaries,
}

impl Default for Layer {
    fn default() -> Self {
        Self {
            name: String::new(),
            color: [200, 200, 200],
            hidden: false,
            attribute_dictionaries: BTreeMap::new(),
        }
    }
}

/// Embedded texture image data and metadata.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct Texture {
    /// Original image filename inside the SKP archive.
    pub filename: String,
    /// Horizontal tile size (inches).
    pub width: f64,
    /// Vertical tile size (inches).
    pub height: f64,
    /// Raw image file bytes (PNG/JPG/BMP).
    pub data: Option<ByteBuffer>,
}

impl Texture {
    /// Saves the image bytes to `path`.
    pub fn save(&self, path: impl AsRef<Path>) -> io::Result<()> {
        let path = path.as_ref();
        let Some(data) = &self.data else {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                format!("Texture '{}' has no image data", self.filename),
            ));
        };
        std::fs::write(path, data).map_err(|e| {
            io::Error::new(
                e.kind(),
                format!("Failed to write texture: {}", path.display()),
            )
        })
    }
}

/// Surface material properties.
#[derive(Debug, Clone, PartialEq)]
pub struct Material {
    pub name: String,
    pub color: Color4,
    /// Opacity factor [0 = fully transparent, 1 = fully opaque].
    pub transparency: f64,
    /// First TLV entity id that names this material.
    pub id: Option<EntityId>,
    pub texture: Option<Texture>,
    pub colorized: bool,
    pub colorize_type: i32,
}

impl Default for Material {
    fn default() -> Self {
        Self {
            name: String::new(),
            color: [200, 200, 200, 255],
            transparency: 1.0,
            id: None,
            texture: None,
            colorized: false,
            colorize_type: 0,
        }
    }
}

/// One `<sty:item>` of a style.xml: the raw `<t:variant>` of a style setting.
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct StyleItem {
    /// Variant type attribute: 1 bool, 3/4 int32, 5 ABGR color in older files,
    /// 6 float, 7 double, 13 nested XML such as the watermark list.
    pub r#type: i32,
    /// Variant text, trimmed; nested XML is kept verbatim.
    pub value: String,
}

/// One watermark of a style (style.xml item 5001, a `<screenimage>`), in file
/// order. The "<MODEL SPACE>" separator entry is not included.
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct StyleWatermark {
    pub name: String,
    /// Raw `<screenimage>` attributes, entity-decoded (`name` excluded).
    pub attributes: BTreeMap<String, String>,
    /// Image path as the style names it.
    pub image_path: String,
    /// Original image file name.
    pub file_name: String,
    /// Raw image bytes, when the ZIP has the entry.
    pub image: Option<ByteBuffer>,
}

/// Bundled rendering style settings.
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct Style {
    pub name: String,
    pub description: String,
    /// Default front face color (item 2002).
    pub front_color: Option<Color3>,
    /// Default back face color (item 2003).
    pub back_color: Option<Color3>,
    /// Every item of the style.xml, keyed by SketchUp's item id.
    pub items: BTreeMap<i32, StyleItem>,
    pub watermarks: Vec<StyleWatermark>,
    /// Folder of this style in the SKP ZIP ("styles/<folder>/style.xml").
    pub folder: String,
    /// The model's current style.
    pub active: bool,
    /// Working copy of the current style (folder "<folder>_1").
    pub working_copy: bool,
    /// On the current style: edited without updating the style.
    pub modified: bool,
}

// ── placements and annotations ──────────────────────────────────────────

/// Component or group placement within a definition.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct Instance {
    pub name: String,
    /// Id of the placed definition.
    pub ref_idx: Option<EntityId>,
    /// GUID of the placed definition.
    pub guid: String,
    /// 13-value transform: 0-8 the row-major 3x3, 9-11 translation, 12 the
    /// scale term (`1/w`); empty when the file stores none.
    pub matrix: Vec<f64>,
    /// This instance's own explicit layer override, or `""`.
    pub layer: String,
    /// View of `attribute_dictionaries["dynamic_attributes"]`, stringified.
    pub properties: BTreeMap<String, String>,
    /// Every attribute dictionary on this instance, values typed.
    pub attribute_dictionaries: ParsedAttrDictionaries,
    /// Instance material override id.
    pub material_id: Option<EntityId>,
    /// SketchUp's "Hide" on this placement.
    pub hidden: bool,
    /// The placement's own persistent entity id (VFF only).
    pub id: Option<EntityId>,
}

/// Section plane entity.
#[derive(Debug, Clone, PartialEq)]
pub struct SectionPlane {
    pub plane: [f64; 4],
    pub name: String,
    pub label: String,
    pub hidden: bool,
}

impl Default for SectionPlane {
    fn default() -> Self {
        Self {
            plane: [0.0, 0.0, 1.0, 0.0],
            name: String::new(),
            label: String::new(),
            hidden: false,
        }
    }
}

/// Text annotation entity.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct TextEntity {
    pub text: String,
    pub hidden: bool,
    /// Anchor point in inches (Python port extension), or `None` when the
    /// record anchors to geometry or the reader does not recover it.
    pub point: Option<Vec3>,
    /// Where the label floats (Python port extension), or `None`.
    pub label_point: Option<Vec3>,
}

/// A linear dimension (SketchUp's Dimension tool).
///
/// The legacy reader recovers free endpoints and vertex attachments in the
/// owning entities; unresolved attachments remain unset. See
/// [`SkpModel::dimensions`] for the VFF model-level, world-space list.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct Dimension {
    /// Displayed text; empty when it shows the auto-computed value.
    pub text: String,
    pub hidden: bool,
    /// First measured point in inches in the owning entities' space
    /// (definition-local, or world at the model root), or unset when
    /// unresolved.
    pub a: Option<Vec3>,
    /// Second measured point.
    pub b: Option<Vec3>,
    /// Offset distance (inches) of the dimension line from the a-b segment.
    pub offset: f64,
    /// Historical frame field: VFF `5BCF` (plane normal), legacy X axis, or
    /// unset.
    pub plane_x: Option<Vec3>,
    /// Historical frame field: VFF `5BD0` (X axis), legacy plane normal, or
    /// unset.
    pub normal: Option<Vec3>,
    /// Explicit offset for legacy or VFF dimensions constrained to a plane
    /// axis. Preserves the side of the line independently of endpoint order.
    pub offset_vector: Option<Vec3>,
}

/// The offset vector of a dimension constrained to a plane axis (`axis` 1 or
/// 2), from its plane `normal`, X axis `x` and signed `offset`; `None` for
/// any other mode, which keeps only the scalar offset.
pub(crate) fn constrained_offset_vector(
    axis: u32,
    normal: Vec3,
    x: Vec3,
    offset: f64,
) -> Option<Vec3> {
    match axis {
        1 => Some([
            (normal[1] * x[2] - normal[2] * x[1]) * offset,
            (normal[2] * x[0] - normal[0] * x[2]) * offset,
            (normal[0] * x[1] - normal[1] * x[0]) * offset,
        ]),
        2 => Some([-x[0] * offset, -x[1] * offset, -x[2] * offset]),
        _ => None,
    }
}

/// A construction/guide line: point + normalized direction; `start`/`end` are
/// unset when the line is unbounded in that direction.
#[derive(Debug, Clone, PartialEq)]
pub struct ConstructionLine {
    pub point: Vec3,
    pub direction: Vec3,
    pub start: Option<Vec3>,
    pub end: Option<Vec3>,
    /// Name of the layer (tag) the guide is assigned to, or `""` for the
    /// default layer (Layer0).
    pub layer: String,
    /// Whether the guide is hidden (SketchUp's "Hide" on this specific guide,
    /// not a layer/tag visibility toggle).
    pub hidden: bool,
}

impl Default for ConstructionLine {
    fn default() -> Self {
        Self {
            point: [0.0; 3],
            direction: [1.0, 0.0, 0.0],
            start: None,
            end: None,
            layer: String::new(),
            hidden: false,
        }
    }
}

/// A construction/guide point.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct ConstructionPoint {
    pub position: Vec3,
    /// Name of the layer (tag) the guide is assigned to, or `""` for the
    /// default layer (Layer0).
    pub layer: String,
    /// Whether the guide is hidden (SketchUp's "Hide" on this specific guide,
    /// not a layer/tag visibility toggle).
    pub hidden: bool,
}

/// A saved scene ("page").
#[derive(Debug, Clone, PartialEq)]
pub struct Page {
    pub name: String,
    pub eye: Option<Vec3>,
    pub target: Option<Vec3>,
    pub up: Option<Vec3>,
    /// Field of view in degrees.
    pub fov: f64,
    /// Parallel (orthographic) projection; `fov` still holds the stored angle.
    pub parallel: bool,
    /// Visible height in inches when `parallel`.
    pub ortho_height: f64,
    /// Names of the layers this scene hides.
    pub hidden_layers: Vec<String>,
    /// The scene selected when the model was saved.
    pub selected: bool,
}

impl Default for Page {
    fn default() -> Self {
        Self {
            name: String::new(),
            eye: None,
            target: None,
            up: None,
            fov: 35.0,
            parallel: false,
            ortho_height: 0.0,
            hidden_layers: Vec::new(),
            selected: false,
        }
    }
}

/// A view camera (VFF `34BC` record).
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct ViewCamera {
    pub eye: Vec3,
    pub target: Vec3,
    pub up: Vec3,
    pub fov: f64,
    pub parallel: bool,
    pub ortho_height: f64,
}

impl Default for ViewCamera {
    fn default() -> Self {
        Self {
            eye: [0.0, 0.0, 1.0],
            target: [0.0; 3],
            up: [0.0, 0.0, 1.0],
            fov: 35.0,
            parallel: false,
            ortho_height: 0.0,
        }
    }
}

// ── definitions ─────────────────────────────────────────────────────────

/// One ordered run of loose edges (see [`Definition::loose_edge_runs`]).
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct LooseEdgeRun {
    pub edge_ids: Vec<EntityId>,
    /// Vertex ids in walk order; a closed run does not repeat its first vertex.
    pub vertex_ids: Vec<EntityId>,
    pub closed: bool,
}

/// Reusable geometry container (component definition or group).
#[derive(Debug, Clone, PartialEq, Default)]
pub struct Definition {
    pub id: EntityId,
    pub guid: String,
    pub name: String,
    pub vertices: BTreeMap<EntityId, Vertex>,
    pub edges: BTreeMap<EntityId, Edge>,
    pub faces: BTreeMap<EntityId, Face>,
    pub instances: Vec<Instance>,
    pub section_planes: Vec<SectionPlane>,
    pub texts: Vec<TextEntity>,
    pub dimensions: Vec<Dimension>,
    pub construction_lines: Vec<ConstructionLine>,
    pub construction_points: Vec<ConstructionPoint>,
    pub always_faces_camera: bool,
    pub shadows_face_sun: bool,
    /// The analytic arc of every curve that is one, keyed by curve id.
    pub arcs: BTreeMap<EntityId, ArcCurve>,
    /// Backs an Image entity.
    pub is_image: bool,
    /// SketchUp made this definition for a group rather than a component.
    pub is_group: bool,
}

impl Definition {
    /// Groups this definition's LOOSE edges (edges no face uses) into ordered
    /// polyline runs (Python's `loose_edge_runs`).
    ///
    /// Grouping comes from the file's own `Edge#curve` ([`Edge::curve_id`]),
    /// not a vertex-adjacency guess. An edge with no curve id is its own
    /// two-point run - joining it to neighbours by adjacency alone would
    /// invent a curve the author never drew. Each curve's edges are walked in
    /// order (a SketchUp Curve is a path or a single cycle); a branched group
    /// emits its longest walk and orders the remainder again.
    pub fn loose_edge_runs(&self) -> Vec<LooseEdgeRun> {
        let mut used = BTreeSet::new();
        for face in self.faces.values() {
            for lp in &face.loops {
                for co in lp {
                    used.insert(co.edge_id);
                }
            }
        }
        let edges: BTreeMap<EntityId, (EntityId, EntityId)> = self
            .edges
            .values()
            .map(|e| (e.id, (e.v1_id, e.v2_id)))
            .collect();
        let mut group_order: Vec<EntityId> = Vec::new();
        let mut groups: HashMap<EntityId, Vec<EntityId>> = HashMap::new();
        let mut runs = Vec::new();
        for (&eid, &(v1, v2)) in &edges {
            if used.contains(&eid) {
                continue;
            }
            match self.edges.get(&eid).and_then(|e| e.curve_id) {
                Some(cid) if cid != 0 => {
                    groups
                        .entry(cid)
                        .or_insert_with(|| {
                            group_order.push(cid);
                            Vec::new()
                        })
                        .push(eid);
                }
                _ => runs.push(LooseEdgeRun {
                    edge_ids: vec![eid],
                    vertex_ids: vec![v1, v2],
                    closed: false,
                }),
            }
        }
        for cid in group_order {
            runs.extend(order_curve(&edges, &groups[&cid]));
        }
        runs
    }
}

/// Orders ONE curve's edges into runs (Python `_curves._order_curve`).
fn order_curve(
    edges: &BTreeMap<EntityId, (EntityId, EntityId)>,
    eids: &[EntityId],
) -> Vec<LooseEdgeRun> {
    let mut remaining: BTreeSet<EntityId> = eids.iter().copied().collect();
    let mut runs = Vec::new();
    while !remaining.is_empty() {
        let mut adj_order: Vec<EntityId> = Vec::new();
        let mut adj: HashMap<EntityId, Vec<(EntityId, EntityId)>> = HashMap::new();
        for &e in &remaining {
            let (a, b) = edges[&e];
            for (v, other) in [(a, b), (b, a)] {
                adj.entry(v)
                    .or_insert_with(|| {
                        adj_order.push(v);
                        Vec::new()
                    })
                    .push((e, other));
            }
        }
        // Start at a degree-1 vertex when there is one, so an open path comes
        // out end-to-end. A cycle has none, so any vertex will do.
        let start = adj_order
            .iter()
            .copied()
            .find(|v| adj[v].len() == 1)
            .unwrap_or(adj_order[0]);
        let (eid, mut nxt) = adj[&start][0];
        remaining.remove(&eid);
        let mut chain_eids = vec![eid];
        let mut chain = vec![start, nxt];
        let mut closed = false;
        while let Some(&(e2, v2)) = adj
            .get(&nxt)
            .and_then(|o| o.iter().find(|t| remaining.contains(&t.0)))
        {
            remaining.remove(&e2);
            chain_eids.push(e2);
            if v2 == chain[0] {
                // Back where it started: closed, and the first point is not
                // appended again.
                closed = true;
                break;
            }
            chain.push(v2);
            nxt = v2;
        }
        runs.push(LooseEdgeRun {
            edge_ids: chain_eids,
            vertex_ids: chain,
            closed,
        });
    }
    runs
}

/// Free-function form of [`Definition::loose_edge_runs`] (Python API name).
pub fn loose_edge_runs(definition: &Definition) -> Vec<LooseEdgeRun> {
    definition.loose_edge_runs()
}

// ── the model ───────────────────────────────────────────────────────────

/// Top-level model: definitions, layers, materials, styles and scenes.
#[derive(Debug, Clone, PartialEq)]
pub struct SkpModel {
    /// SketchUp version string (e.g. "{25.0.575}").
    pub version: String,
    /// Unit-system string from meta/meta.dat (VFF only).
    pub units: Option<String>,
    /// Component/group definitions keyed by entity id.
    pub definitions: BTreeMap<EntityId, Definition>,
    /// Layers in file order.
    pub layers: Vec<Layer>,
    /// Saved scenes.
    pub pages: Vec<Page>,
    /// The view the model was saved with (VFF only).
    pub camera: Option<ViewCamera>,
    /// Model-level linear dimensions with world-space endpoints (VFF).
    pub dimensions: Vec<Dimension>,
    pub materials: Vec<Material>,
    pub styles: Vec<Style>,
    /// Dictionaries attached to the model itself (VFF only), values typed.
    pub attribute_dictionaries: ParsedAttrDictionaries,
    pub(crate) root: Definition,
    pub(crate) material_indices: HashMap<EntityId, usize>,
}

impl Default for SkpModel {
    fn default() -> Self {
        Self {
            version: "unknown".into(),
            units: None,
            definitions: BTreeMap::new(),
            layers: Vec::new(),
            pages: Vec::new(),
            camera: None,
            dimensions: Vec::new(),
            materials: Vec::new(),
            styles: Vec::new(),
            attribute_dictionaries: BTreeMap::new(),
            root: Definition {
                id: 0,
                guid: "ROOT".into(),
                name: "ROOT_MODEL".into(),
                ..Default::default()
            },
            material_indices: HashMap::new(),
        }
    }
}

impl SkpModel {
    /// The implicit root model definition.
    pub fn root(&self) -> &Definition {
        &self.root
    }

    pub fn root_mut(&mut self) -> &mut Definition {
        &mut self.root
    }

    /// Looks up a material by any TLV entity id that names it.
    pub fn material_by_id(&self, id: EntityId) -> Option<&Material> {
        self.material_indices.get(&id).map(|&i| &self.materials[i])
    }

    pub fn material_by_id_mut(&mut self, id: EntityId) -> Option<&mut Material> {
        let i = *self.material_indices.get(&id)?;
        self.materials.get_mut(i)
    }

    /// Index into [`SkpModel::materials`] of the material an id names.
    pub fn material_index(&self, id: EntityId) -> Option<usize> {
        self.material_indices.get(&id).copied()
    }

    /// Every material keyed by each TLV entity id that names it (several ids
    /// may alias one material).
    pub fn materials_by_id(&self) -> BTreeMap<EntityId, &Material> {
        self.material_indices
            .iter()
            .map(|(&id, &i)| (id, &self.materials[i]))
            .collect()
    }
}
