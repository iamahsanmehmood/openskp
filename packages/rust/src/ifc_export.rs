//! IFC4 (ISO-10303-21 STEP ASCII) export (`ifc_export.hpp` /
//! `ifc_export.cpp`).
//!
//! Ported from the C++ exporter, plus what only the Python port
//! (`openskp/export/ifc.py`, the reference) has so far:
//!
//! * loose-edge curve sets ([`Scene::curve_sets`]) written as `IfcAnnotation`
//!   products in an `Annotation` sub-context - a polyline of the run's own
//!   chords, or an `IfcIndexedPolyCurve` with inline `IfcArcIndex` segments
//!   when the scene proved the run is one whole circular arc;
//! * the four IFC4 STEP-conformance fixes: non-ASCII text escaped as
//!   `\X2\`/`\X4\` instead of raw UTF-8; IFC4 enumeration spellings (`.T.`/
//!   `.F.`, `.NOCHANGE.`, `.SOLIDANGLEUNIT.`); each product written with its
//!   own type's attribute count; and `IfcTriangulatedFaceSet.Closed` computed
//!   from the mesh rather than hardcoded.

use std::collections::hash_map::RandomState;
use std::collections::{BTreeMap, HashMap};
use std::fmt::{self, Write as _};
use std::hash::{BuildHasher, Hasher};
use std::io;
use std::path::Path;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::OnceLock;
use std::time::{SystemTime, UNIX_EPOCH};

use crate::scene::{GlbPrimitive, Scene};

/// Metres to millimetres - the default `scale` for [`to_ifc`], since the file
/// always declares its length unit as millimetres (see the `IFCSIUNIT` it
/// writes). [`METRES_TO_INCHES`](crate::METRES_TO_INCHES) stays available for
/// callers that want inch-scaled coordinates explicitly, but with it every
/// coordinate reads back ~25.4x too small in any IFC consumer that respects
/// the unit declaration.
pub const METRES_TO_MM: f64 = 1000.0;

/// A custom classifier for [`to_ifc`]: called with an element's (escaped)
/// display name and layer name, returns `(STEP_ENTITY_TYPE, IFC_CLASS_NAME)`,
/// e.g. `("IFCCOLUMN", "IfcColumn")`.
pub type IfcClassifier<'a> = dyn Fn(&str, &str) -> (String, String) + 'a;

const IFC_BASE64: &[u8; 64] = b"0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_$";

fn is_trim_char(c: char) -> bool {
    matches!(c, ' ' | '\t' | '\r' | '\n')
}

/// Escapes a string for a STEP (ISO 10303-21) string literal.
///
/// The default character set of a STEP file is ISO 8859-1, so non-ASCII
/// text cannot go out as raw UTF-8 (ifcopenshell 0.8.5 silently drops every
/// such character). Maximal runs of non-ASCII are wrapped in `\X2\` (UTF-16BE,
/// 4 upper-case hex digits per character), or `\X4\` (8 per character) once a
/// run holds a character outside the BMP - the forms ifcopenshell's own
/// writer emits. As in the Python reference, `\X4\` stays in effect for every
/// later run of the same string. ASCII is written as is, with `'` doubled
/// and `\` doubled.
fn step_text(text: &str) -> String {
    fn flush(out: &mut String, run: &mut Vec<char>, wide: bool) {
        if run.is_empty() {
            return;
        }
        out.push_str(if wide { "\\X4\\" } else { "\\X2\\" });
        for c in run.drain(..) {
            let _ = if wide {
                write!(out, "{:08X}", u32::from(c))
            } else {
                write!(out, "{:04X}", u32::from(c))
            };
        }
        out.push_str("\\X0\\");
    }

    let mut out = String::with_capacity(text.len());
    let mut run: Vec<char> = Vec::new();
    let mut wide = false;
    for ch in text.chars() {
        if ch.is_ascii() {
            flush(&mut out, &mut run, wide);
            match ch {
                '\'' => out.push_str("''"),
                '\\' => out.push_str("\\\\"),
                _ => out.push(ch),
            }
        } else {
            run.push(ch);
            if u32::from(ch) > 0xFFFF {
                wide = true;
            }
        }
    }
    flush(&mut out, &mut run, wide);
    out
}

/// Trims a name and escapes it as STEP string content; empty or
/// whitespace-only names become `Unnamed`.
fn sanitize_name(name: &str) -> String {
    let clean = name.trim_matches(is_trim_char);
    if clean.is_empty() {
        return "Unnamed".to_string();
    }
    step_text(clean)
}

fn get_prim_rgb(scene: &Scene, prim_mat_idx: usize) -> (f64, f64, f64, f64) {
    match scene.gltf_materials.get(prim_mat_idx) {
        Some(mat) => {
            let col = &mat.pbr_metallic_roughness.base_color_factor;
            (
                col[0].clamp(0.0, 1.0),
                col[1].clamp(0.0, 1.0),
                col[2].clamp(0.0, 1.0),
                col[3].clamp(0.0, 1.0),
            )
        }
        None => (0.8, 0.8, 0.8, 1.0),
    }
}

/// `std::fixed << std::setprecision(prec)`; Rust's `{:.N}` matches it digit
/// for digit except for NaN, spelled the glibc way here.
fn fixed(v: f64, prec: usize) -> String {
    if v.is_nan() {
        return if v.is_sign_negative() { "-nan" } else { "nan" }.to_string();
    }
    format!("{v:.prec$}")
}

/// One point as `(x,y,z)`, converted from glTF's Y-up convention (glTF.y =
/// SketchUp Z/height, glTF.z = -SketchUp Y/depth) back to IFC's - and
/// SketchUp's own - Z-up, or the building comes out rotated ~90 degrees and
/// mirrored.
fn ifc_coord(x: f64, y: f64, z: f64, scale: f64) -> String {
    format!(
        "({},{},{})",
        fixed(x * scale, 6),
        fixed(-z * scale, 6),
        fixed(y * scale, 6)
    )
}

// ---------------------------------------------------------------------------
// GUIDs and timestamps
// ---------------------------------------------------------------------------

/// SplitMix64 over a per-process random seed: 64 random bits per call.
fn random_u64() -> u64 {
    static SEED: OnceLock<u64> = OnceLock::new();
    static COUNTER: AtomicU64 = AtomicU64::new(0);
    let seed = *SEED.get_or_init(|| {
        // std's RandomState is seeded from the OS's random source.
        let mut h = RandomState::new().build_hasher();
        h.write_u128(
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .map_or(0, |d| d.as_nanos()),
        );
        h.write_u32(std::process::id());
        h.finish()
    });
    let n = COUNTER.fetch_add(1, Ordering::Relaxed);
    let mut z = seed.wrapping_add(n.wrapping_add(1).wrapping_mul(0x9E37_79B9_7F4A_7C15));
    z = (z ^ (z >> 30)).wrapping_mul(0xBF58_476D_1CE4_E5B9);
    z = (z ^ (z >> 27)).wrapping_mul(0x94D0_49BB_1331_11EB);
    z ^ (z >> 31)
}

/// Generates a standard 22-character IFC base64 compressed GUID.
///
/// Encodes a random (version 4) UUID the way the Python reference does, so
/// the first character is always `0`-`3` like any compressed 128-bit GUID.
pub fn generate_ifc_guid() -> String {
    let mut u = (u128::from(random_u64()) << 64) | u128::from(random_u64());
    u = (u & !(0xF_u128 << 76)) | (0x4_u128 << 76); // version 4
    u = (u & !(0x3_u128 << 62)) | (0x2_u128 << 62); // RFC 4122 variant
    let mut chars = [0_u8; 22];
    for c in chars.iter_mut().rev() {
        *c = IFC_BASE64[(u % 64) as usize];
        u /= 64;
    }
    chars.iter().map(|&c| char::from(c)).collect()
}

/// Days since 1970-01-01 to a proleptic Gregorian (year, month, day).
fn civil_from_days(days: i64) -> (i64, u32, u32) {
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let day = (doy - (153 * mp + 2) / 5 + 1) as u32;
    let month = if mp < 10 { mp + 3 } else { mp - 9 } as u32;
    let year = yoe + era * 400 + i64::from(month <= 2);
    (year, month, day)
}

/// `%Y-%m-%dT%H:%M:%S` of a Unix time, in UTC.
fn iso_utc(epoch_secs: i64) -> String {
    let (year, month, day) = civil_from_days(epoch_secs.div_euclid(86_400));
    let secs = epoch_secs.rem_euclid(86_400);
    format!(
        "{year:04}-{month:02}-{day:02}T{:02}:{:02}:{:02}",
        secs / 3600,
        secs / 60 % 60,
        secs % 60
    )
}

// ---------------------------------------------------------------------------
// Classification
// ---------------------------------------------------------------------------

fn classify_by_keyword(name: &str) -> Option<(&'static str, &'static str)> {
    let l = name.to_ascii_lowercase();
    if l.contains("wall") {
        return Some(("IFCWALL", "IfcWall"));
    }
    if l.contains("door") {
        return Some(("IFCDOOR", "IfcDoor"));
    }
    if l.contains("window") {
        return Some(("IFCWINDOW", "IfcWindow"));
    }
    if l.contains("slab") || l.contains("floor") {
        return Some(("IFCSLAB", "IfcSlab"));
    }
    if l.contains("column") || l.contains("pillar") {
        return Some(("IFCCOLUMN", "IfcColumn"));
    }
    if l.contains("beam") || l.contains("joist") {
        return Some(("IFCBEAM", "IfcBeam"));
    }
    if l.contains("roof") {
        return Some(("IFCROOF", "IfcRoof"));
    }
    None
}

/// Classifies a geometry/component name to an IFC entity type, returning
/// `(STEP_ENTITY_TYPE, IFC_CLASS_NAME)`.
///
/// Tries `geom_name` first, then falls back to `layer_name` (many
/// SketchUp-for-BIM workflows organize by tag/layer - "Walls", "Doors" -
/// even when individual components are never renamed away from SketchUp's
/// own defaults like "Component#109415").
///
/// If neither matches and `classify_using_full_path` is set, falls back
/// further to keyword-matching the component's full ancestor hierarchy path
/// (e.g. "ROOT / Wall Frame / Stud 12") - a broader, noisier signal than the
/// component's own name/layer, since it can match on an ancestor's name
/// rather than the part itself. Off by default: matching keywords against a
/// mangled internal path unconditionally was a past bug here (it also
/// corrupted the element's displayed Name), so that broader matching is an
/// explicit opt-in. Only if nothing at all matches does this fall back to a
/// generic, untyped element.
pub fn classify_element(
    geom_name: &str,
    layer_name: &str,
    path_name: &str,
    classify_using_full_path: bool,
) -> (String, String) {
    let found = classify_by_keyword(geom_name)
        .or_else(|| {
            if layer_name.is_empty() {
                None
            } else {
                classify_by_keyword(layer_name)
            }
        })
        .or_else(|| {
            if classify_using_full_path && !path_name.is_empty() {
                classify_by_keyword(path_name)
            } else {
                None
            }
        });
    let (step, class) = found.unwrap_or(("IFCBUILDINGELEMENTPROXY", "IfcBuildingElementProxy"));
    (step.to_string(), class.to_string())
}

/// The total attribute count of a STEP entity type (upper-case name) in
/// `schema`, from the schema-generated table the Python port ships as
/// `ifc_attr_counts.json`. Unknown schemas use IFC4's table.
fn attr_count(schema: &str, step_type: &str) -> Option<usize> {
    let table = if schema == "IFC2X3" {
        IFC2X3_ATTR_COUNTS
    } else {
        IFC4_ATTR_COUNTS
    };
    table
        .binary_search_by(|(name, _)| (*name).cmp(step_type))
        .ok()
        .map(|i| usize::from(table[i].1))
}

// ---------------------------------------------------------------------------
// Writer
// ---------------------------------------------------------------------------

/// The STEP text being assembled, and the entity-id counter.
struct StepWriter {
    out: String,
    next: usize,
}

impl StepWriter {
    fn id(&mut self) -> usize {
        let id = self.next;
        self.next += 1;
        id
    }

    fn line(&mut self, args: fmt::Arguments) {
        let _ = self.out.write_fmt(args);
        self.out.push_str("\r\n");
    }
}

fn id_refs(ids: &[usize]) -> String {
    let mut s = String::new();
    for (i, id) in ids.iter().enumerate() {
        if i > 0 {
            s.push(',');
        }
        let _ = write!(s, "#{id}");
    }
    s
}

/// One IFCPROPERTYSET (`props`) attached to `product_id` via
/// IFCRELDEFINESBYPROPERTIES; nothing at all for an empty `props`.
fn write_pset(
    w: &mut StepWriter,
    owner_hist_id: usize,
    product_id: usize,
    pset_name: &str,
    props: &BTreeMap<String, String>,
) {
    if props.is_empty() {
        return;
    }
    let mut prop_val_ids = Vec::with_capacity(props.len());
    for (pk, pv) in props {
        let clean_k = sanitize_name(pk);
        let clean_v = sanitize_name(pv);
        let prop_id = w.id();
        w.line(format_args!(
            "#{prop_id}=IFCPROPERTYSINGLEVALUE('{clean_k}',$,IFCTEXT('{clean_v}'),$);"
        ));
        prop_val_ids.push(prop_id);
    }
    let pset_id = w.id();
    w.line(format_args!(
        "#{pset_id}=IFCPROPERTYSET('{}',#{owner_hist_id},'{}',$,({}));",
        generate_ifc_guid(),
        sanitize_name(pset_name),
        id_refs(&prop_val_ids)
    ));
    let rel_id = w.id();
    w.line(format_args!(
        "#{rel_id}=IFCRELDEFINESBYPROPERTIES('{}',#{owner_hist_id},$,$,(#{product_id}),#{pset_id});",
        generate_ifc_guid()
    ));
}

/// Python's `round(v, 6)` as a hashable key: the double nearest the
/// correctly rounded 6-dp decimal, with -0.0 folded into 0.0.
fn weld_key(v: f32) -> u64 {
    let rounded: f64 = format!("{:.6}", f64::from(v)).parse().unwrap_or(f64::NAN);
    (rounded + 0.0).to_bits()
}

/// Whether a triangle mesh is closed: after welding vertices by position
/// (rounded to 6 dp - the pipeline's vertices are per-face, never shared, so
/// the raw index buffer alone would call every mesh open), every undirected
/// edge has to be used by exactly two triangles. A boundary edge, or an edge
/// shared by three or more triangles, makes it open. An index out of range
/// makes it open too.
fn mesh_is_closed(prim: &GlbPrimitive) -> bool {
    let mut weld: HashMap<[u64; 3], u32> = HashMap::new();
    let remap: Vec<u32> = prim
        .positions
        .as_chunks::<3>()
        .0
        .iter()
        .map(|p| {
            let next = weld.len() as u32;
            *weld
                .entry([weld_key(p[0]), weld_key(p[1]), weld_key(p[2])])
                .or_insert(next)
        })
        .collect();
    let mut edge_use: HashMap<(u32, u32), u32> = HashMap::new();
    for tri in prim.indices.as_chunks::<3>().0 {
        let (Some(&a), Some(&b), Some(&c)) = (
            remap.get(tri[0] as usize),
            remap.get(tri[1] as usize),
            remap.get(tri[2] as usize),
        ) else {
            return false;
        };
        for (u, v) in [(a, b), (b, c), (c, a)] {
            *edge_use
                .entry(if u < v { (u, v) } else { (v, u) })
                .or_insert(0) += 1;
        }
    }
    !edge_use.is_empty() && edge_use.values().all(|&n| n == 2)
}

/// Representation items grouped by (escaped) layer name, with the first raw
/// name seen for the `Scene::layer_hidden` lookup.
#[derive(Default)]
struct LayerItems(BTreeMap<String, (String, Vec<usize>)>);

impl LayerItems {
    fn add(&mut self, raw_layer: &str, item_id: usize) {
        let (raw, key) = if raw_layer.is_empty() {
            ("Layer0".to_string(), "Layer0".to_string())
        } else {
            (raw_layer.to_string(), sanitize_name(raw_layer))
        };
        self.0
            .entry(key)
            .or_insert_with(|| (raw, Vec::new()))
            .1
            .push(item_id);
    }
}

/// Serializes a baked [`Scene`] into ISO-10303-21 STEP ASCII IFC4 text.
///
/// * `scale` multiplies every coordinate; the C++ default is
///   [`METRES_TO_MM`], matching the millimetre length unit this exporter
///   always declares.
/// * `schema` names the IFC schema (the C++ default is `"IFC4"`; empty means
///   `"IFC4"`); it is written upper-cased.
/// * `classifier` overrides [`classify_element`] - supply your own naming
///   convention or metadata-driven typing instead of the built-in
///   keyword/layer heuristic. When set, `classify_using_full_path` is ignored,
///   since that flag only affects the built-in classifier.
/// * `classify_using_full_path`: when the built-in classifier can't type an
///   element from its own name or layer, also try keyword-matching its full
///   ancestor hierarchy path. Off by default - see [`classify_element`].
///
/// Each loose-edge run in [`Scene::curve_sets`] becomes an `IfcAnnotation`;
/// each triangulated primitive becomes a typed product with an
/// `IfcTriangulatedFaceSet` body, surface style and property sets.
pub fn to_ifc(
    scene: &Scene,
    scale: f64,
    schema: &str,
    classifier: Option<&IfcClassifier<'_>>,
    classify_using_full_path: bool,
) -> String {
    // A custom classifier keeps its 2-arg (name, layer) signature; only the
    // built-in classify_element() takes the path/full-path parameters.
    let classify = |name: &str, layer: &str, path: &str| -> (String, String) {
        match classifier {
            Some(c) => c(name, layer),
            None => classify_element(name, layer, path, classify_using_full_path),
        }
    };
    let schema_str = if schema.is_empty() {
        "IFC4".to_string()
    } else {
        schema.to_ascii_uppercase()
    };

    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0, |d| d.as_secs() as i64);
    let time_str = iso_utc(now);

    let mut w = StepWriter {
        out: String::new(),
        next: 1,
    };
    w.line(format_args!("ISO-10303-21;"));
    w.line(format_args!("HEADER;"));
    w.line(format_args!(
        "FILE_DESCRIPTION(('ViewDefinition [CoordinationView]'),'2;1');"
    ));
    w.line(format_args!(
        "FILE_NAME('model.ifc','{time_str}',('OpenSKP Author'),('OpenSKP Organization'),\
         'OpenSKP IFC Exporter','OpenSKP','');"
    ));
    w.line(format_args!("FILE_SCHEMA(('{schema_str}'));"));
    w.line(format_args!("ENDSEC;"));
    w.line(format_args!("DATA;"));

    let person_id = w.id();
    w.line(format_args!(
        "#{person_id}=IFCPERSON($,$,'OpenSKP User',$,$,$,$,$);"
    ));
    let org_id = w.id();
    w.line(format_args!(
        "#{org_id}=IFCORGANIZATION($,'OpenSKP',$,$,$);"
    ));
    let person_org_id = w.id();
    w.line(format_args!(
        "#{person_org_id}=IFCPERSONANDORGANIZATION(#{person_id},#{org_id},$);"
    ));
    let app_id = w.id();
    w.line(format_args!(
        "#{app_id}=IFCAPPLICATION(#{org_id},'0.3.1','OpenSKP Exporter','OpenSKP');"
    ));

    // IfcChangeActionEnum in IFC4 is exactly (NOCHANGE, MODIFIED, ADDED,
    // DELETED, NOTDEFINED): the C++ port's `.READWRITE.` only ever existed in
    // IFC2x3. NOCHANGE is what an untouched, just-created entity is.
    let owner_hist_id = w.id();
    w.line(format_args!(
        "#{owner_hist_id}=IFCOWNERHISTORY(#{person_org_id},#{app_id},$,.NOCHANGE.,$,$,$,{now});"
    ));

    let length_unit_id = w.id();
    w.line(format_args!(
        "#{length_unit_id}=IFCSIUNIT(*,.LENGTHUNIT.,.MILLI.,.METRE.);"
    ));
    let angle_unit_id = w.id();
    w.line(format_args!(
        "#{angle_unit_id}=IFCSIUNIT(*,.PLANEANGLEUNIT.,$,.RADIAN.);"
    ));
    // `.STERADIANUNIT.` is not an IfcUnitEnum value (`.SOLIDANGLEUNIT.` is);
    // `.STERADIAN.` is a valid IfcSIUnitName, so only the type slot changes.
    let solid_unit_id = w.id();
    w.line(format_args!(
        "#{solid_unit_id}=IFCSIUNIT(*,.SOLIDANGLEUNIT.,$,.STERADIAN.);"
    ));
    let unit_assign_id = w.id();
    w.line(format_args!("#{unit_assign_id}=IFCUNITASSIGNMENT((#{length_unit_id},#{angle_unit_id},#{solid_unit_id}));"));

    let pt_zero_id = w.id();
    w.line(format_args!(
        "#{pt_zero_id}=IFCCARTESIANPOINT((0.0,0.0,0.0));"
    ));
    let axis_placement_id = w.id();
    w.line(format_args!(
        "#{axis_placement_id}=IFCAXIS2PLACEMENT3D(#{pt_zero_id},$,$);"
    ));
    // The C++ port writes `# <id>` here; an entity reference is a single
    // STEP token, so it is written without the space, as Python does.
    let geom_ctx_id = w.id();
    w.line(format_args!(
        "#{geom_ctx_id}=IFCGEOMETRICREPRESENTATIONCONTEXT($,'Model',3,1.0E-5,#{axis_placement_id},$);"
    ));

    // An Annotation sub-context for the loose-edge curves: a body belongs to
    // the Model context, line work to a sub-context, so a consumer can tell a
    // solid from an annotation without guessing from the representation
    // identifier. The four derived attributes (dimension, precision, world
    // coordinate system, true north) are `*` because IFC4 declares them
    // DERIVE FROM ParentContext. The parent is the 3D Model context: these
    // runs are 3D polylines and a sub-context cannot change
    // CoordinateSpaceDimension.
    let ann_ctx_id = w.id();
    w.line(format_args!(
        "#{ann_ctx_id}=IFCGEOMETRICREPRESENTATIONSUBCONTEXT('Annotation','Model',*,*,*,*,#{geom_ctx_id},$,\
         .MODEL_VIEW.,$);"
    ));

    let proj_id = w.id();
    w.line(format_args!(
        "#{proj_id}=IFCPROJECT('{}',#{owner_hist_id},'OpenSKP Project',$,$,$,$,(#{geom_ctx_id}),#{unit_assign_id});",
        generate_ifc_guid()
    ));
    let site_placement_id = w.id();
    w.line(format_args!(
        "#{site_placement_id}=IFCLOCALPLACEMENT($,#{axis_placement_id});"
    ));
    let site_id = w.id();
    w.line(format_args!(
        "#{site_id}=IFCSITE('{}',#{owner_hist_id},'Site',$,$,#{site_placement_id},$,$,.ELEMENT.,$,$,$,$,$);",
        generate_ifc_guid()
    ));
    let bldg_placement_id = w.id();
    w.line(format_args!(
        "#{bldg_placement_id}=IFCLOCALPLACEMENT(#{site_placement_id},#{axis_placement_id});"
    ));
    let bldg_id = w.id();
    w.line(format_args!(
        "#{bldg_id}=IFCBUILDING('{}',#{owner_hist_id},'Building',$,$,#{bldg_placement_id},$,$,.ELEMENT.,$,$,$);",
        generate_ifc_guid()
    ));
    let storey_placement_id = w.id();
    w.line(format_args!(
        "#{storey_placement_id}=IFCLOCALPLACEMENT(#{bldg_placement_id},#{axis_placement_id});"
    ));
    let storey_id = w.id();
    w.line(format_args!(
        "#{storey_id}=IFCBUILDINGSTOREY('{}',#{owner_hist_id},'Level 0',$,$,#{storey_placement_id},$,$,.ELEMENT.,0.0);",
        generate_ifc_guid()
    ));

    for (relating, related) in [(proj_id, site_id), (site_id, bldg_id), (bldg_id, storey_id)] {
        let rel_id = w.id();
        w.line(format_args!(
            "#{rel_id}=IFCRELAGGREGATES('{}',#{owner_hist_id},$,$,#{relating},(#{related}));",
            generate_ifc_guid()
        ));
    }

    let mut product_ids: Vec<usize> = Vec::new();
    let mut layer_items = LayerItems::default();
    let mut mat_style_cache: HashMap<String, usize> = HashMap::new();

    // Loose-edge curve sets -> IfcAnnotation (Python port extension).
    //
    // The only geometry a drawing-style model has: a facade elevation is
    // edges and no faces, so without this the export is a spatial skeleton
    // with no elements at all. IfcAnnotation + IfcGeometricCurveSet is IFC4's
    // shape for zero-thickness line work; deliberately not extruded into a
    // solid, which would invent a thickness the file does not contain.
    for cs in &scene.curve_sets {
        if cs.points_m.len() < 2 {
            continue;
        }

        let curve_id = match cs.arc.as_ref().filter(|arc| arc.points.len() >= 3) {
            Some(arc) => {
                // A run that provably IS one whole circular arc stays an arc:
                // an IfcIndexedPolyCurve over an IfcCartesianPointList3D
                // (1-based), with IfcArcIndex (start, a point on the arc,
                // end) segments. IfcArcIndex is a DEFINED TYPE, not an
                // entity, so it is written inline inside Segments - a
                // standalone `#n=IFCARCINDEX(...)` line is a syntax error
                // that ifcopenshell answers by silently truncating the file.
                let segs: Vec<String> = arc
                    .segments
                    .iter()
                    .map(|s| format!("IFCARCINDEX(({},{},{}))", s[0] + 1, s[1] + 1, s[2] + 1))
                    .collect();
                let coords: Vec<String> = arc
                    .points
                    .iter()
                    .map(|p| ifc_coord(p[0], p[1], p[2], scale))
                    .collect();
                let pt_list_id = w.id();
                w.line(format_args!(
                    "#{pt_list_id}=IFCCARTESIANPOINTLIST3D(({}));",
                    coords.join(",")
                ));
                let curve_id = w.id();
                w.line(format_args!(
                    "#{curve_id}=IFCINDEXEDPOLYCURVE(#{pt_list_id},({}),.F.);",
                    segs.join(",")
                ));
                curve_id
            }
            None => {
                let mut coords: Vec<String> = cs
                    .points_m
                    .iter()
                    .map(|p| ifc_coord(p[0], p[1], p[2], scale))
                    .collect();
                // IfcPolyline has no implicit closing segment - a closed run
                // must repeat its first point or the last gap renders open.
                if cs.closed {
                    coords.push(coords[0].clone());
                }
                let mut pt_ids = Vec::with_capacity(coords.len());
                for c in &coords {
                    let pt_id = w.id();
                    w.line(format_args!("#{pt_id}=IFCCARTESIANPOINT({c});"));
                    pt_ids.push(pt_id);
                }
                let curve_id = w.id();
                w.line(format_args!(
                    "#{curve_id}=IFCPOLYLINE(({}));",
                    id_refs(&pt_ids)
                ));
                curve_id
            }
        };

        let curve_set_id = w.id();
        w.line(format_args!(
            "#{curve_set_id}=IFCGEOMETRICCURVESET((#{curve_id}));"
        ));

        // The layer assignment takes representation items, so the curve goes
        // in - that is what carries the loose edge's own layer to the IFC.
        layer_items.add(&cs.layer, curve_id);

        let ann_name = sanitize_name(if cs.name.is_empty() {
            "Curve"
        } else {
            &cs.name
        });
        let ann_rep_id = w.id();
        w.line(format_args!(
            "#{ann_rep_id}=IFCSHAPEREPRESENTATION(#{ann_ctx_id},'Annotation','GeometricCurveSet',(#{curve_set_id}));"
        ));
        let ann_shape_id = w.id();
        w.line(format_args!(
            "#{ann_shape_id}=IFCPRODUCTDEFINITIONSHAPE($,$,(#{ann_rep_id}));"
        ));
        let ann_placement_id = w.id();
        w.line(format_args!(
            "#{ann_placement_id}=IFCLOCALPLACEMENT(#{storey_placement_id},#{axis_placement_id});"
        ));
        let ann_id = w.id();
        w.line(format_args!(
            "#{ann_id}=IFCANNOTATION('{}',#{owner_hist_id},'{ann_name}',$,$,#{ann_placement_id},#{ann_shape_id});",
            generate_ifc_guid()
        ));
        // Same property-set treatment the mesh branch gives its primitives:
        // each attribute dictionary becomes its own named Pset.
        for (dict_name, entries) in &cs.attribute_dictionaries {
            write_pset(
                &mut w,
                owner_hist_id,
                ann_id,
                &format!("Pset_{dict_name}"),
                entries,
            );
        }
        product_ids.push(ann_id);
    }

    for prim in &scene.glb_primitives {
        let tri_count = prim.indices.len() / 3;
        let v_count = prim.positions.len() / 3;
        if tri_count == 0 || v_count == 0 {
            continue;
        }

        let meta = scene.mesh_index.get(&prim.geom_name);
        // prim.geom_name is an internal lookup key (mesh index + hierarchy
        // path + layer, e.g. "mesh_3115_ROOT__Component_6205261_Layer0") -
        // never the element's real name. meta.name is the actual SketchUp
        // instance name (or SketchUp's own "Component_<id>" default when
        // nobody renamed it) - use that for both classification and the
        // element's IFC Name, falling back to the internal key only if a
        // primitive somehow has no mesh_index entry.
        let display_name = match meta {
            Some(m) if !m.name.is_empty() => sanitize_name(&m.name),
            _ => sanitize_name(&prim.geom_name),
        };
        let raw_layer = meta.map_or("", |m| m.layer.as_str());
        let layer_name = if raw_layer.is_empty() {
            "Layer0".to_string()
        } else {
            sanitize_name(raw_layer)
        };
        let path_name = meta.map_or("", |m| m.path.as_str());

        let (step_type, _) = classify(&display_name, &layer_name, path_name);

        let mut pt_list = String::new();
        for (i, p) in prim.positions.as_chunks::<3>().0.iter().enumerate() {
            if i > 0 {
                pt_list.push(',');
            }
            pt_list.push_str(&ifc_coord(
                f64::from(p[0]),
                f64::from(p[1]),
                f64::from(p[2]),
                scale,
            ));
        }
        let pt_list_id = w.id();
        w.line(format_args!(
            "#{pt_list_id}=IFCCARTESIANPOINTLIST3D(({pt_list}));"
        ));

        let mut faces = String::new();
        for (i, tri) in prim.indices.as_chunks::<3>().0.iter().enumerate() {
            if i > 0 {
                faces.push(',');
            }
            let _ = write!(
                faces,
                "({},{},{})",
                tri[0].wrapping_add(1),
                tri[1].wrapping_add(1),
                tri[2].wrapping_add(1)
            );
        }

        // `Closed` is an IfcBoolean (`.T.`/`.F.` in IFC4, not IFC2x's
        // `.TRUE.`), and it is computed rather than claimed.
        let closed = if mesh_is_closed(prim) { ".T." } else { ".F." };
        let face_set_id = w.id();
        w.line(format_args!(
            "#{face_set_id}=IFCTRIANGULATEDFACESET(#{pt_list_id},$,{closed},({faces}),$);"
        ));

        layer_items.add(raw_layer, face_set_id);

        let (r, g, b, a) = get_prim_rgb(scene, prim.material_index);
        let rgba_key = format!("{r:.4},{g:.4},{b:.4},{a:.4}");
        let style_assign_id = match mat_style_cache.get(&rgba_key) {
            Some(&id) => id,
            None => {
                let col_id = w.id();
                w.line(format_args!(
                    "#{col_id}=IFCCOLOURRGB($, {},{},{});",
                    fixed(r, 4),
                    fixed(g, 4),
                    fixed(b, 4)
                ));
                let rendering_id = w.id();
                w.line(format_args!(
                    "#{rendering_id}=IFCSURFACESTYLERENDERING(#{col_id},{},$,$,$,$,$,$,.FLAT.);",
                    fixed(1.0 - a, 4)
                ));
                let style_id = w.id();
                w.line(format_args!(
                    "#{style_id}=IFCSURFACESTYLE('{display_name}_Material',.BOTH.,(#{rendering_id}));"
                ));
                let style_assign_id = w.id();
                w.line(format_args!(
                    "#{style_assign_id}=IFCPRESENTATIONSTYLEASSIGNMENT((#{style_id}));"
                ));
                mat_style_cache.insert(rgba_key, style_assign_id);
                style_assign_id
            }
        };

        let styled_item_id = w.id();
        w.line(format_args!(
            "#{styled_item_id}=IFCSTYLEDITEM(#{face_set_id},(#{style_assign_id}),$);"
        ));
        let shape_rep_id = w.id();
        w.line(format_args!(
            "#{shape_rep_id}=IFCSHAPEREPRESENTATION(#{geom_ctx_id},'Body','Tessellation',(#{face_set_id}));"
        ));
        let prod_shape_id = w.id();
        w.line(format_args!(
            "#{prod_shape_id}=IFCPRODUCTDEFINITIONSHAPE($,$,(#{shape_rep_id}));"
        ));
        let prod_placement_id = w.id();
        w.line(format_args!(
            "#{prod_placement_id}=IFCLOCALPLACEMENT(#{storey_placement_id},#{axis_placement_id});"
        ));

        // IFC entity types do not share one attribute count (IFC4 IfcDoor
        // declares 13, IfcWall 9), so the product line is padded with `$` to
        // its own type's count instead of a hardcoded 9.
        let product_id = w.id();
        let mut attrs: Vec<String> = vec![
            format!("'{}'", generate_ifc_guid()), // GlobalId
            format!("#{owner_hist_id}"),          // OwnerHistory
            format!("'{display_name}'"),          // Name
            "$".to_string(),                      // Description
            "$".to_string(),                      // ObjectType
            format!("#{prod_placement_id}"),      // ObjectPlacement
            format!("#{prod_shape_id}"),          // Representation
            "$".to_string(),                      // Tag
        ];
        let total = attr_count(&schema_str, &step_type)
            .filter(|&n| n > 0)
            .unwrap_or(9)
            .max(attrs.len());
        attrs.resize(total, "$".to_string());
        // IfcBuildingElementProxy's 9th attribute is its PredefinedType.
        if step_type == "IFCBUILDINGELEMENTPROXY" && total == 9 {
            attrs[8] = ".NOTDEFINED.".to_string();
        }
        w.line(format_args!(
            "#{product_id}={step_type}({});",
            attrs.join(",")
        ));
        product_ids.push(product_id);

        if let Some(meta) = meta {
            write_pset(
                &mut w,
                owner_hist_id,
                product_id,
                "Pset_CustomProperties",
                &meta.properties,
            );
            // Any OTHER attribute dictionaries the instance carries
            // (third-party BIM/steel-detailing plugins, etc.) - each becomes
            // its own named property set instead of being merged into
            // Pset_CustomProperties, since these come from a distinct source
            // and commonly share key names with each other.
            for (dict_name, entries) in &meta.attribute_dictionaries {
                write_pset(
                    &mut w,
                    owner_hist_id,
                    product_id,
                    &format!("Pset_{dict_name}"),
                    entries,
                );
            }
        }
    }

    // Presentation layer assignments, preserving each layer's on/off state.
    // IFCPRESENTATIONLAYERWITHSTYLE is the only IFC4 entity that can carry a
    // layer's visibility at all (LayerOn); every other attribute besides Name
    // and LayerOn is left unset/false since this project doesn't track them.
    for (l_name, (raw_name, item_ids)) in &layer_items.0 {
        let hidden = scene.layer_hidden.get(raw_name).copied().unwrap_or(false);
        let layer_on = if hidden { ".F." } else { ".T." };
        let layer_id = w.id();
        w.line(format_args!(
            "#{layer_id}=IFCPRESENTATIONLAYERWITHSTYLE('{l_name}',$,({}),$,{layer_on},.F.,.F.,());",
            id_refs(item_ids)
        ));
    }

    if !product_ids.is_empty() {
        let rel_id = w.id();
        w.line(format_args!(
            "#{rel_id}=IFCRELCONTAINEDINSPATIALSTRUCTURE('{}',#{owner_hist_id},$,$,({}),#{storey_id});",
            generate_ifc_guid(),
            id_refs(&product_ids)
        ));
    }

    w.line(format_args!("ENDSEC;"));
    w.line(format_args!("END-ISO-10303-21;"));
    w.out
}

/// Exports a baked [`Scene`] directly to an ISO-10303-21 STEP ASCII IFC4
/// file (see [`to_ifc`] for the parameters), creating the parent directory
/// if needed.
pub fn export_ifc(
    scene: &Scene,
    path: impl AsRef<Path>,
    scale: f64,
    schema: &str,
    classifier: Option<&IfcClassifier<'_>>,
    classify_using_full_path: bool,
) -> io::Result<()> {
    let path = path.as_ref();
    if let Some(parent) = path.parent().filter(|p| !p.as_os_str().is_empty()) {
        std::fs::create_dir_all(parent)?;
    }
    let text = to_ifc(scene, scale, schema, classifier, classify_using_full_path);
    std::fs::write(path, text.as_bytes()).map_err(|e| {
        io::Error::new(
            e.kind(),
            format!("Failed to open file for writing: {}: {e}", path.display()),
        )
    })
}

// Total attribute count per STEP entity type, generated mechanically from
// the Python port's `export/ifc_attr_counts.json` (itself generated from the
// schemas by ifcopenshell and cross-checked against the official ifcXML
// schema), keys upper-cased and sorted for binary search; do not edit by hand.

#[rustfmt::skip]
static IFC2X3_ATTR_COUNTS: &[(&str, u8)] = &[
    ("IFC2DCOMPOSITECURVE", 2), ("IFCACTIONREQUEST", 6), ("IFCACTOR", 6), ("IFCACTORROLE", 3),
    ("IFCACTUATORTYPE", 10), ("IFCADDRESS", 3), ("IFCAIRTERMINALBOXTYPE", 10), ("IFCAIRTERMINALTYPE", 10),
    ("IFCAIRTOAIRHEATRECOVERYTYPE", 10), ("IFCALARMTYPE", 10), ("IFCANGULARDIMENSION", 1), ("IFCANNOTATION", 7),
    ("IFCANNOTATIONCURVEOCCURRENCE", 3), ("IFCANNOTATIONFILLAREA", 2), ("IFCANNOTATIONFILLAREAOCCURRENCE", 5),
    ("IFCANNOTATIONOCCURRENCE", 3), ("IFCANNOTATIONSURFACE", 2), ("IFCANNOTATIONSURFACEOCCURRENCE", 3),
    ("IFCANNOTATIONSYMBOLOCCURRENCE", 3), ("IFCANNOTATIONTEXTOCCURRENCE", 3), ("IFCAPPLICATION", 4),
    ("IFCAPPLIEDVALUE", 6), ("IFCAPPLIEDVALUERELATIONSHIP", 5), ("IFCAPPROVAL", 7),
    ("IFCAPPROVALACTORRELATIONSHIP", 3), ("IFCAPPROVALPROPERTYRELATIONSHIP", 2), ("IFCAPPROVALRELATIONSHIP", 4),
    ("IFCARBITRARYCLOSEDPROFILEDEF", 3), ("IFCARBITRARYOPENPROFILEDEF", 3), ("IFCARBITRARYPROFILEDEFWITHVOIDS", 4),
    ("IFCASSET", 14), ("IFCASYMMETRICISHAPEPROFILEDEF", 12), ("IFCAXIS1PLACEMENT", 2), ("IFCAXIS2PLACEMENT2D", 2),
    ("IFCAXIS2PLACEMENT3D", 3), ("IFCBEAM", 8), ("IFCBEAMTYPE", 10), ("IFCBEZIERCURVE", 5), ("IFCBLOBTEXTURE", 6),
    ("IFCBLOCK", 4), ("IFCBOILERTYPE", 10), ("IFCBOOLEANCLIPPINGRESULT", 3), ("IFCBOOLEANRESULT", 3),
    ("IFCBOUNDARYCONDITION", 1), ("IFCBOUNDARYEDGECONDITION", 7), ("IFCBOUNDARYFACECONDITION", 4),
    ("IFCBOUNDARYNODECONDITION", 7), ("IFCBOUNDARYNODECONDITIONWARPING", 8), ("IFCBOUNDEDCURVE", 0),
    ("IFCBOUNDEDSURFACE", 0), ("IFCBOUNDINGBOX", 4), ("IFCBOXEDHALFSPACE", 3), ("IFCBSPLINECURVE", 5),
    ("IFCBUILDING", 12), ("IFCBUILDINGELEMENT", 8), ("IFCBUILDINGELEMENTCOMPONENT", 8), ("IFCBUILDINGELEMENTPART", 8),
    ("IFCBUILDINGELEMENTPROXY", 9), ("IFCBUILDINGELEMENTPROXYTYPE", 10), ("IFCBUILDINGELEMENTTYPE", 9),
    ("IFCBUILDINGSTOREY", 10), ("IFCCABLECARRIERFITTINGTYPE", 10), ("IFCCABLECARRIERSEGMENTTYPE", 10),
    ("IFCCABLESEGMENTTYPE", 10), ("IFCCALENDARDATE", 3), ("IFCCARTESIANPOINT", 1),
    ("IFCCARTESIANTRANSFORMATIONOPERATOR", 4), ("IFCCARTESIANTRANSFORMATIONOPERATOR2D", 4),
    ("IFCCARTESIANTRANSFORMATIONOPERATOR2DNONUNIFORM", 5), ("IFCCARTESIANTRANSFORMATIONOPERATOR3D", 5),
    ("IFCCARTESIANTRANSFORMATIONOPERATOR3DNONUNIFORM", 7), ("IFCCENTERLINEPROFILEDEF", 4),
    ("IFCCHAMFEREDGEFEATURE", 11), ("IFCCHILLERTYPE", 10), ("IFCCIRCLE", 2), ("IFCCIRCLEHOLLOWPROFILEDEF", 5),
    ("IFCCIRCLEPROFILEDEF", 4), ("IFCCLASSIFICATION", 4), ("IFCCLASSIFICATIONITEM", 3),
    ("IFCCLASSIFICATIONITEMRELATIONSHIP", 2), ("IFCCLASSIFICATIONNOTATION", 1), ("IFCCLASSIFICATIONNOTATIONFACET", 1),
    ("IFCCLASSIFICATIONREFERENCE", 4), ("IFCCLOSEDSHELL", 1), ("IFCCOILTYPE", 10), ("IFCCOLOURRGB", 4),
    ("IFCCOLOURSPECIFICATION", 1), ("IFCCOLUMN", 8), ("IFCCOLUMNTYPE", 10), ("IFCCOMPLEXPROPERTY", 4),
    ("IFCCOMPOSITECURVE", 2), ("IFCCOMPOSITECURVESEGMENT", 3), ("IFCCOMPOSITEPROFILEDEF", 4),
    ("IFCCOMPRESSORTYPE", 10), ("IFCCONDENSERTYPE", 10), ("IFCCONDITION", 5), ("IFCCONDITIONCRITERION", 7),
    ("IFCCONIC", 1), ("IFCCONNECTEDFACESET", 1), ("IFCCONNECTIONCURVEGEOMETRY", 2), ("IFCCONNECTIONGEOMETRY", 0),
    ("IFCCONNECTIONPOINTECCENTRICITY", 5), ("IFCCONNECTIONPOINTGEOMETRY", 2), ("IFCCONNECTIONPORTGEOMETRY", 3),
    ("IFCCONNECTIONSURFACEGEOMETRY", 2), ("IFCCONSTRAINT", 7), ("IFCCONSTRAINTAGGREGATIONRELATIONSHIP", 5),
    ("IFCCONSTRAINTCLASSIFICATIONRELATIONSHIP", 2), ("IFCCONSTRAINTRELATIONSHIP", 4),
    ("IFCCONSTRUCTIONEQUIPMENTRESOURCE", 9), ("IFCCONSTRUCTIONMATERIALRESOURCE", 11),
    ("IFCCONSTRUCTIONPRODUCTRESOURCE", 9), ("IFCCONSTRUCTIONRESOURCE", 9), ("IFCCONTEXTDEPENDENTUNIT", 3),
    ("IFCCONTROL", 5), ("IFCCONTROLLERTYPE", 10), ("IFCCONVERSIONBASEDUNIT", 4), ("IFCCOOLEDBEAMTYPE", 10),
    ("IFCCOOLINGTOWERTYPE", 10), ("IFCCOORDINATEDUNIVERSALTIMEOFFSET", 3), ("IFCCOSTITEM", 5),
    ("IFCCOSTSCHEDULE", 13), ("IFCCOSTVALUE", 8), ("IFCCOVERING", 9), ("IFCCOVERINGTYPE", 10),
    ("IFCCRANERAILASHAPEPROFILEDEF", 15), ("IFCCRANERAILFSHAPEPROFILEDEF", 12), ("IFCCREWRESOURCE", 9),
    ("IFCCSGPRIMITIVE3D", 1), ("IFCCSGSOLID", 1), ("IFCCSHAPEPROFILEDEF", 9), ("IFCCURRENCYRELATIONSHIP", 5),
    ("IFCCURTAINWALL", 8), ("IFCCURTAINWALLTYPE", 10), ("IFCCURVE", 0), ("IFCCURVEBOUNDEDPLANE", 3),
    ("IFCCURVESTYLE", 4), ("IFCCURVESTYLEFONT", 2), ("IFCCURVESTYLEFONTANDSCALING", 3),
    ("IFCCURVESTYLEFONTPATTERN", 2), ("IFCDAMPERTYPE", 10), ("IFCDATEANDTIME", 2), ("IFCDEFINEDSYMBOL", 2),
    ("IFCDERIVEDPROFILEDEF", 5), ("IFCDERIVEDUNIT", 3), ("IFCDERIVEDUNITELEMENT", 2), ("IFCDIAMETERDIMENSION", 1),
    ("IFCDIMENSIONALEXPONENTS", 7), ("IFCDIMENSIONCALLOUTRELATIONSHIP", 4), ("IFCDIMENSIONCURVE", 3),
    ("IFCDIMENSIONCURVEDIRECTEDCALLOUT", 1), ("IFCDIMENSIONCURVETERMINATOR", 5), ("IFCDIMENSIONPAIR", 4),
    ("IFCDIRECTION", 1), ("IFCDISCRETEACCESSORY", 8), ("IFCDISCRETEACCESSORYTYPE", 9),
    ("IFCDISTRIBUTIONCHAMBERELEMENT", 8), ("IFCDISTRIBUTIONCHAMBERELEMENTTYPE", 10),
    ("IFCDISTRIBUTIONCONTROLELEMENT", 9), ("IFCDISTRIBUTIONCONTROLELEMENTTYPE", 9), ("IFCDISTRIBUTIONELEMENT", 8),
    ("IFCDISTRIBUTIONELEMENTTYPE", 9), ("IFCDISTRIBUTIONFLOWELEMENT", 8), ("IFCDISTRIBUTIONFLOWELEMENTTYPE", 9),
    ("IFCDISTRIBUTIONPORT", 8), ("IFCDOCUMENTELECTRONICFORMAT", 3), ("IFCDOCUMENTINFORMATION", 17),
    ("IFCDOCUMENTINFORMATIONRELATIONSHIP", 3), ("IFCDOCUMENTREFERENCE", 3), ("IFCDOOR", 10),
    ("IFCDOORLININGPROPERTIES", 15), ("IFCDOORPANELPROPERTIES", 9), ("IFCDOORSTYLE", 12), ("IFCDRAUGHTINGCALLOUT", 1),
    ("IFCDRAUGHTINGCALLOUTRELATIONSHIP", 4), ("IFCDRAUGHTINGPREDEFINEDCOLOUR", 1),
    ("IFCDRAUGHTINGPREDEFINEDCURVEFONT", 1), ("IFCDRAUGHTINGPREDEFINEDTEXTFONT", 1), ("IFCDUCTFITTINGTYPE", 10),
    ("IFCDUCTSEGMENTTYPE", 10), ("IFCDUCTSILENCERTYPE", 10), ("IFCEDGE", 2), ("IFCEDGECURVE", 4),
    ("IFCEDGEFEATURE", 9), ("IFCEDGELOOP", 1), ("IFCELECTRICALBASEPROPERTIES", 14), ("IFCELECTRICALCIRCUIT", 5),
    ("IFCELECTRICALELEMENT", 8), ("IFCELECTRICAPPLIANCETYPE", 10), ("IFCELECTRICDISTRIBUTIONPOINT", 10),
    ("IFCELECTRICFLOWSTORAGEDEVICETYPE", 10), ("IFCELECTRICGENERATORTYPE", 10), ("IFCELECTRICHEATERTYPE", 10),
    ("IFCELECTRICMOTORTYPE", 10), ("IFCELECTRICTIMECONTROLTYPE", 10), ("IFCELEMENT", 8), ("IFCELEMENTARYSURFACE", 1),
    ("IFCELEMENTASSEMBLY", 10), ("IFCELEMENTCOMPONENT", 8), ("IFCELEMENTCOMPONENTTYPE", 9), ("IFCELEMENTQUANTITY", 6),
    ("IFCELEMENTTYPE", 9), ("IFCELLIPSE", 3), ("IFCELLIPSEPROFILEDEF", 5), ("IFCENERGYCONVERSIONDEVICE", 8),
    ("IFCENERGYCONVERSIONDEVICETYPE", 9), ("IFCENERGYPROPERTIES", 6), ("IFCENVIRONMENTALIMPACTVALUE", 9),
    ("IFCEQUIPMENTELEMENT", 8), ("IFCEQUIPMENTSTANDARD", 5), ("IFCEVAPORATIVECOOLERTYPE", 10),
    ("IFCEVAPORATORTYPE", 10), ("IFCEXTENDEDMATERIALPROPERTIES", 4), ("IFCEXTERNALLYDEFINEDHATCHSTYLE", 3),
    ("IFCEXTERNALLYDEFINEDSURFACESTYLE", 3), ("IFCEXTERNALLYDEFINEDSYMBOL", 3), ("IFCEXTERNALLYDEFINEDTEXTFONT", 3),
    ("IFCEXTERNALREFERENCE", 3), ("IFCEXTRUDEDAREASOLID", 4), ("IFCFACE", 1), ("IFCFACEBASEDSURFACEMODEL", 1),
    ("IFCFACEBOUND", 2), ("IFCFACEOUTERBOUND", 2), ("IFCFACESURFACE", 3), ("IFCFACETEDBREP", 1),
    ("IFCFACETEDBREPWITHVOIDS", 2), ("IFCFAILURECONNECTIONCONDITION", 7), ("IFCFANTYPE", 10), ("IFCFASTENER", 8),
    ("IFCFASTENERTYPE", 9), ("IFCFEATUREELEMENT", 8), ("IFCFEATUREELEMENTADDITION", 8),
    ("IFCFEATUREELEMENTSUBTRACTION", 8), ("IFCFILLAREASTYLE", 2), ("IFCFILLAREASTYLEHATCHING", 5),
    ("IFCFILLAREASTYLETILES", 3), ("IFCFILLAREASTYLETILESYMBOLWITHSTYLE", 1), ("IFCFILTERTYPE", 10),
    ("IFCFIRESUPPRESSIONTERMINALTYPE", 10), ("IFCFLOWCONTROLLER", 8), ("IFCFLOWCONTROLLERTYPE", 9),
    ("IFCFLOWFITTING", 8), ("IFCFLOWFITTINGTYPE", 9), ("IFCFLOWINSTRUMENTTYPE", 10), ("IFCFLOWMETERTYPE", 10),
    ("IFCFLOWMOVINGDEVICE", 8), ("IFCFLOWMOVINGDEVICETYPE", 9), ("IFCFLOWSEGMENT", 8), ("IFCFLOWSEGMENTTYPE", 9),
    ("IFCFLOWSTORAGEDEVICE", 8), ("IFCFLOWSTORAGEDEVICETYPE", 9), ("IFCFLOWTERMINAL", 8), ("IFCFLOWTERMINALTYPE", 9),
    ("IFCFLOWTREATMENTDEVICE", 8), ("IFCFLOWTREATMENTDEVICETYPE", 9), ("IFCFLUIDFLOWPROPERTIES", 19),
    ("IFCFOOTING", 9), ("IFCFUELPROPERTIES", 5), ("IFCFURNISHINGELEMENT", 8), ("IFCFURNISHINGELEMENTTYPE", 9),
    ("IFCFURNITURESTANDARD", 5), ("IFCFURNITURETYPE", 10), ("IFCGASTERMINALTYPE", 10),
    ("IFCGENERALMATERIALPROPERTIES", 4), ("IFCGENERALPROFILEPROPERTIES", 7), ("IFCGEOMETRICCURVESET", 1),
    ("IFCGEOMETRICREPRESENTATIONCONTEXT", 6), ("IFCGEOMETRICREPRESENTATIONITEM", 0),
    ("IFCGEOMETRICREPRESENTATIONSUBCONTEXT", 10), ("IFCGEOMETRICSET", 1), ("IFCGRID", 10), ("IFCGRIDAXIS", 3),
    ("IFCGRIDPLACEMENT", 2), ("IFCGROUP", 5), ("IFCHALFSPACESOLID", 2), ("IFCHEATEXCHANGERTYPE", 10),
    ("IFCHUMIDIFIERTYPE", 10), ("IFCHYGROSCOPICMATERIALPROPERTIES", 6), ("IFCIMAGETEXTURE", 5), ("IFCINVENTORY", 11),
    ("IFCIRREGULARTIMESERIES", 9), ("IFCIRREGULARTIMESERIESVALUE", 2), ("IFCISHAPEPROFILEDEF", 8),
    ("IFCJUNCTIONBOXTYPE", 10), ("IFCLABORRESOURCE", 10), ("IFCLAMPTYPE", 10), ("IFCLIBRARYINFORMATION", 5),
    ("IFCLIBRARYREFERENCE", 3), ("IFCLIGHTDISTRIBUTIONDATA", 3), ("IFCLIGHTFIXTURETYPE", 10),
    ("IFCLIGHTINTENSITYDISTRIBUTION", 2), ("IFCLIGHTSOURCE", 4), ("IFCLIGHTSOURCEAMBIENT", 4),
    ("IFCLIGHTSOURCEDIRECTIONAL", 5), ("IFCLIGHTSOURCEGONIOMETRIC", 10), ("IFCLIGHTSOURCEPOSITIONAL", 9),
    ("IFCLIGHTSOURCESPOT", 13), ("IFCLINE", 2), ("IFCLINEARDIMENSION", 1), ("IFCLOCALPLACEMENT", 2),
    ("IFCLOCALTIME", 5), ("IFCLOOP", 0), ("IFCLSHAPEPROFILEDEF", 11), ("IFCMANIFOLDSOLIDBREP", 1),
    ("IFCMAPPEDITEM", 2), ("IFCMATERIAL", 1), ("IFCMATERIALCLASSIFICATIONRELATIONSHIP", 2),
    ("IFCMATERIALDEFINITIONREPRESENTATION", 4), ("IFCMATERIALLAYER", 3), ("IFCMATERIALLAYERSET", 2),
    ("IFCMATERIALLAYERSETUSAGE", 4), ("IFCMATERIALLIST", 1), ("IFCMATERIALPROPERTIES", 1), ("IFCMEASUREWITHUNIT", 2),
    ("IFCMECHANICALCONCRETEMATERIALPROPERTIES", 12), ("IFCMECHANICALFASTENER", 10), ("IFCMECHANICALFASTENERTYPE", 9),
    ("IFCMECHANICALMATERIALPROPERTIES", 6), ("IFCMECHANICALSTEELMATERIALPROPERTIES", 13), ("IFCMEMBER", 8),
    ("IFCMEMBERTYPE", 10), ("IFCMETRIC", 10), ("IFCMONETARYUNIT", 1), ("IFCMOTORCONNECTIONTYPE", 10), ("IFCMOVE", 13),
    ("IFCNAMEDUNIT", 2), ("IFCOBJECT", 5), ("IFCOBJECTDEFINITION", 4), ("IFCOBJECTIVE", 11),
    ("IFCOBJECTPLACEMENT", 0), ("IFCOCCUPANT", 7), ("IFCOFFSETCURVE2D", 3), ("IFCOFFSETCURVE3D", 4),
    ("IFCONEDIRECTIONREPEATFACTOR", 1), ("IFCOPENINGELEMENT", 8), ("IFCOPENSHELL", 1),
    ("IFCOPTICALMATERIALPROPERTIES", 10), ("IFCORDERACTION", 11), ("IFCORGANIZATION", 5),
    ("IFCORGANIZATIONRELATIONSHIP", 4), ("IFCORIENTEDEDGE", 4), ("IFCOUTLETTYPE", 10), ("IFCOWNERHISTORY", 8),
    ("IFCPARAMETERIZEDPROFILEDEF", 3), ("IFCPATH", 1), ("IFCPERFORMANCEHISTORY", 6),
    ("IFCPERMEABLECOVERINGPROPERTIES", 9), ("IFCPERMIT", 6), ("IFCPERSON", 8), ("IFCPERSONANDORGANIZATION", 3),
    ("IFCPHYSICALCOMPLEXQUANTITY", 6), ("IFCPHYSICALQUANTITY", 2), ("IFCPHYSICALSIMPLEQUANTITY", 3), ("IFCPILE", 10),
    ("IFCPIPEFITTINGTYPE", 10), ("IFCPIPESEGMENTTYPE", 10), ("IFCPIXELTEXTURE", 8), ("IFCPLACEMENT", 1),
    ("IFCPLANARBOX", 3), ("IFCPLANAREXTENT", 2), ("IFCPLANE", 1), ("IFCPLATE", 8), ("IFCPLATETYPE", 10),
    ("IFCPOINT", 0), ("IFCPOINTONCURVE", 2), ("IFCPOINTONSURFACE", 3), ("IFCPOLYGONALBOUNDEDHALFSPACE", 4),
    ("IFCPOLYLINE", 1), ("IFCPOLYLOOP", 1), ("IFCPORT", 7), ("IFCPOSTALADDRESS", 10), ("IFCPREDEFINEDCOLOUR", 1),
    ("IFCPREDEFINEDCURVEFONT", 1), ("IFCPREDEFINEDDIMENSIONSYMBOL", 1), ("IFCPREDEFINEDITEM", 1),
    ("IFCPREDEFINEDPOINTMARKERSYMBOL", 1), ("IFCPREDEFINEDSYMBOL", 1), ("IFCPREDEFINEDTERMINATORSYMBOL", 1),
    ("IFCPREDEFINEDTEXTFONT", 1), ("IFCPRESENTATIONLAYERASSIGNMENT", 4), ("IFCPRESENTATIONLAYERWITHSTYLE", 8),
    ("IFCPRESENTATIONSTYLE", 1), ("IFCPRESENTATIONSTYLEASSIGNMENT", 1), ("IFCPROCEDURE", 8), ("IFCPROCESS", 5),
    ("IFCPRODUCT", 7), ("IFCPRODUCTDEFINITIONSHAPE", 3), ("IFCPRODUCTREPRESENTATION", 3),
    ("IFCPRODUCTSOFCOMBUSTIONPROPERTIES", 5), ("IFCPROFILEDEF", 2), ("IFCPROFILEPROPERTIES", 2), ("IFCPROJECT", 9),
    ("IFCPROJECTIONCURVE", 3), ("IFCPROJECTIONELEMENT", 8), ("IFCPROJECTORDER", 8), ("IFCPROJECTORDERRECORD", 7),
    ("IFCPROPERTY", 2), ("IFCPROPERTYBOUNDEDVALUE", 5), ("IFCPROPERTYCONSTRAINTRELATIONSHIP", 4),
    ("IFCPROPERTYDEFINITION", 4), ("IFCPROPERTYDEPENDENCYRELATIONSHIP", 5), ("IFCPROPERTYENUMERATEDVALUE", 4),
    ("IFCPROPERTYENUMERATION", 3), ("IFCPROPERTYLISTVALUE", 4), ("IFCPROPERTYREFERENCEVALUE", 4),
    ("IFCPROPERTYSET", 5), ("IFCPROPERTYSETDEFINITION", 4), ("IFCPROPERTYSINGLEVALUE", 4),
    ("IFCPROPERTYTABLEVALUE", 7), ("IFCPROTECTIVEDEVICETYPE", 10), ("IFCPROXY", 9), ("IFCPUMPTYPE", 10),
    ("IFCQUANTITYAREA", 4), ("IFCQUANTITYCOUNT", 4), ("IFCQUANTITYLENGTH", 4), ("IFCQUANTITYTIME", 4),
    ("IFCQUANTITYVOLUME", 4), ("IFCQUANTITYWEIGHT", 4), ("IFCRADIUSDIMENSION", 1), ("IFCRAILING", 9),
    ("IFCRAILINGTYPE", 10), ("IFCRAMP", 9), ("IFCRAMPFLIGHT", 8), ("IFCRAMPFLIGHTTYPE", 10),
    ("IFCRATIONALBEZIERCURVE", 6), ("IFCRECTANGLEHOLLOWPROFILEDEF", 8), ("IFCRECTANGLEPROFILEDEF", 5),
    ("IFCRECTANGULARPYRAMID", 4), ("IFCRECTANGULARTRIMMEDSURFACE", 7), ("IFCREFERENCESVALUEDOCUMENT", 4),
    ("IFCREGULARTIMESERIES", 10), ("IFCREINFORCEMENTBARPROPERTIES", 6), ("IFCREINFORCEMENTDEFINITIONPROPERTIES", 6),
    ("IFCREINFORCINGBAR", 14), ("IFCREINFORCINGELEMENT", 9), ("IFCREINFORCINGMESH", 17), ("IFCRELAGGREGATES", 6),
    ("IFCRELASSIGNS", 6), ("IFCRELASSIGNSTASKS", 8), ("IFCRELASSIGNSTOACTOR", 8), ("IFCRELASSIGNSTOCONTROL", 7),
    ("IFCRELASSIGNSTOGROUP", 7), ("IFCRELASSIGNSTOPROCESS", 8), ("IFCRELASSIGNSTOPRODUCT", 7),
    ("IFCRELASSIGNSTOPROJECTORDER", 7), ("IFCRELASSIGNSTORESOURCE", 7), ("IFCRELASSOCIATES", 5),
    ("IFCRELASSOCIATESAPPLIEDVALUE", 6), ("IFCRELASSOCIATESAPPROVAL", 6), ("IFCRELASSOCIATESCLASSIFICATION", 6),
    ("IFCRELASSOCIATESCONSTRAINT", 7), ("IFCRELASSOCIATESDOCUMENT", 6), ("IFCRELASSOCIATESLIBRARY", 6),
    ("IFCRELASSOCIATESMATERIAL", 6), ("IFCRELASSOCIATESPROFILEPROPERTIES", 8), ("IFCRELATIONSHIP", 4),
    ("IFCRELAXATION", 2), ("IFCRELCONNECTS", 4), ("IFCRELCONNECTSELEMENTS", 7), ("IFCRELCONNECTSPATHELEMENTS", 11),
    ("IFCRELCONNECTSPORTS", 7), ("IFCRELCONNECTSPORTTOELEMENT", 6), ("IFCRELCONNECTSSTRUCTURALACTIVITY", 6),
    ("IFCRELCONNECTSSTRUCTURALELEMENT", 6), ("IFCRELCONNECTSSTRUCTURALMEMBER", 10),
    ("IFCRELCONNECTSWITHECCENTRICITY", 11), ("IFCRELCONNECTSWITHREALIZINGELEMENTS", 9),
    ("IFCRELCONTAINEDINSPATIALSTRUCTURE", 6), ("IFCRELCOVERSBLDGELEMENTS", 6), ("IFCRELCOVERSSPACES", 6),
    ("IFCRELDECOMPOSES", 6), ("IFCRELDEFINES", 5), ("IFCRELDEFINESBYPROPERTIES", 6), ("IFCRELDEFINESBYTYPE", 6),
    ("IFCRELFILLSELEMENT", 6), ("IFCRELFLOWCONTROLELEMENTS", 6), ("IFCRELINTERACTIONREQUIREMENTS", 9),
    ("IFCRELNESTS", 6), ("IFCRELOCCUPIESSPACES", 8), ("IFCRELOVERRIDESPROPERTIES", 7), ("IFCRELPROJECTSELEMENT", 6),
    ("IFCRELREFERENCEDINSPATIALSTRUCTURE", 6), ("IFCRELSCHEDULESCOSTITEMS", 7), ("IFCRELSEQUENCE", 8),
    ("IFCRELSERVICESBUILDINGS", 6), ("IFCRELSPACEBOUNDARY", 9), ("IFCRELVOIDSELEMENT", 6), ("IFCREPRESENTATION", 4),
    ("IFCREPRESENTATIONCONTEXT", 2), ("IFCREPRESENTATIONITEM", 0), ("IFCREPRESENTATIONMAP", 2), ("IFCRESOURCE", 5),
    ("IFCREVOLVEDAREASOLID", 4), ("IFCRIBPLATEPROFILEPROPERTIES", 7), ("IFCRIGHTCIRCULARCONE", 3),
    ("IFCRIGHTCIRCULARCYLINDER", 3), ("IFCROOF", 9), ("IFCROOT", 4), ("IFCROUNDEDEDGEFEATURE", 10),
    ("IFCROUNDEDRECTANGLEPROFILEDEF", 6), ("IFCSANITARYTERMINALTYPE", 10), ("IFCSCHEDULETIMECONTROL", 23),
    ("IFCSECTIONEDSPINE", 3), ("IFCSECTIONPROPERTIES", 3), ("IFCSECTIONREINFORCEMENTPROPERTIES", 6),
    ("IFCSENSORTYPE", 10), ("IFCSERVICELIFE", 7), ("IFCSERVICELIFEFACTOR", 8), ("IFCSHAPEASPECT", 5),
    ("IFCSHAPEMODEL", 4), ("IFCSHAPEREPRESENTATION", 4), ("IFCSHELLBASEDSURFACEMODEL", 1), ("IFCSIMPLEPROPERTY", 2),
    ("IFCSITE", 14), ("IFCSIUNIT", 4), ("IFCSLAB", 9), ("IFCSLABTYPE", 10), ("IFCSLIPPAGECONNECTIONCONDITION", 4),
    ("IFCSOLIDMODEL", 0), ("IFCSOUNDPROPERTIES", 7), ("IFCSOUNDVALUE", 7), ("IFCSPACE", 11),
    ("IFCSPACEHEATERTYPE", 10), ("IFCSPACEPROGRAM", 10), ("IFCSPACETHERMALLOADPROPERTIES", 14), ("IFCSPACETYPE", 10),
    ("IFCSPATIALSTRUCTUREELEMENT", 9), ("IFCSPATIALSTRUCTUREELEMENTTYPE", 9), ("IFCSPHERE", 2),
    ("IFCSTACKTERMINALTYPE", 10), ("IFCSTAIR", 9), ("IFCSTAIRFLIGHT", 12), ("IFCSTAIRFLIGHTTYPE", 10),
    ("IFCSTRUCTURALACTION", 11), ("IFCSTRUCTURALACTIVITY", 9), ("IFCSTRUCTURALANALYSISMODEL", 9),
    ("IFCSTRUCTURALCONNECTION", 8), ("IFCSTRUCTURALCONNECTIONCONDITION", 1), ("IFCSTRUCTURALCURVECONNECTION", 8),
    ("IFCSTRUCTURALCURVEMEMBER", 8), ("IFCSTRUCTURALCURVEMEMBERVARYING", 8), ("IFCSTRUCTURALITEM", 7),
    ("IFCSTRUCTURALLINEARACTION", 12), ("IFCSTRUCTURALLINEARACTIONVARYING", 14), ("IFCSTRUCTURALLOAD", 1),
    ("IFCSTRUCTURALLOADGROUP", 10), ("IFCSTRUCTURALLOADLINEARFORCE", 7), ("IFCSTRUCTURALLOADPLANARFORCE", 4),
    ("IFCSTRUCTURALLOADSINGLEDISPLACEMENT", 7), ("IFCSTRUCTURALLOADSINGLEDISPLACEMENTDISTORTION", 8),
    ("IFCSTRUCTURALLOADSINGLEFORCE", 7), ("IFCSTRUCTURALLOADSINGLEFORCEWARPING", 8), ("IFCSTRUCTURALLOADSTATIC", 1),
    ("IFCSTRUCTURALLOADTEMPERATURE", 4), ("IFCSTRUCTURALMEMBER", 7), ("IFCSTRUCTURALPLANARACTION", 12),
    ("IFCSTRUCTURALPLANARACTIONVARYING", 14), ("IFCSTRUCTURALPOINTACTION", 11), ("IFCSTRUCTURALPOINTCONNECTION", 8),
    ("IFCSTRUCTURALPOINTREACTION", 9), ("IFCSTRUCTURALPROFILEPROPERTIES", 23), ("IFCSTRUCTURALREACTION", 9),
    ("IFCSTRUCTURALRESULTGROUP", 8), ("IFCSTRUCTURALSTEELPROFILEPROPERTIES", 27),
    ("IFCSTRUCTURALSURFACECONNECTION", 8), ("IFCSTRUCTURALSURFACEMEMBER", 9),
    ("IFCSTRUCTURALSURFACEMEMBERVARYING", 11), ("IFCSTRUCTUREDDIMENSIONCALLOUT", 1), ("IFCSTYLEDITEM", 3),
    ("IFCSTYLEDREPRESENTATION", 4), ("IFCSTYLEMODEL", 4), ("IFCSUBCONTRACTRESOURCE", 11), ("IFCSUBEDGE", 3),
    ("IFCSURFACE", 0), ("IFCSURFACECURVESWEPTAREASOLID", 6), ("IFCSURFACEOFLINEAREXTRUSION", 4),
    ("IFCSURFACEOFREVOLUTION", 3), ("IFCSURFACESTYLE", 3), ("IFCSURFACESTYLELIGHTING", 4),
    ("IFCSURFACESTYLEREFRACTION", 2), ("IFCSURFACESTYLERENDERING", 9), ("IFCSURFACESTYLESHADING", 1),
    ("IFCSURFACESTYLEWITHTEXTURES", 1), ("IFCSURFACETEXTURE", 4), ("IFCSWEPTAREASOLID", 2), ("IFCSWEPTDISKSOLID", 5),
    ("IFCSWEPTSURFACE", 2), ("IFCSWITCHINGDEVICETYPE", 10), ("IFCSYMBOLSTYLE", 2), ("IFCSYSTEM", 5),
    ("IFCSYSTEMFURNITUREELEMENTTYPE", 9), ("IFCTABLE", 2), ("IFCTABLEROW", 2), ("IFCTANKTYPE", 10), ("IFCTASK", 10),
    ("IFCTELECOMADDRESS", 8), ("IFCTENDON", 17), ("IFCTENDONANCHOR", 9), ("IFCTERMINATORSYMBOL", 4),
    ("IFCTEXTLITERAL", 3), ("IFCTEXTLITERALWITHEXTENT", 5), ("IFCTEXTSTYLE", 4), ("IFCTEXTSTYLEFONTMODEL", 6),
    ("IFCTEXTSTYLEFORDEFINEDFONT", 2), ("IFCTEXTSTYLETEXTMODEL", 7), ("IFCTEXTSTYLEWITHBOXCHARACTERISTICS", 5),
    ("IFCTEXTURECOORDINATE", 0), ("IFCTEXTURECOORDINATEGENERATOR", 2), ("IFCTEXTUREMAP", 1), ("IFCTEXTUREVERTEX", 1),
    ("IFCTHERMALMATERIALPROPERTIES", 5), ("IFCTIMESERIES", 8), ("IFCTIMESERIESREFERENCERELATIONSHIP", 2),
    ("IFCTIMESERIESSCHEDULE", 8), ("IFCTIMESERIESVALUE", 1), ("IFCTOPOLOGICALREPRESENTATIONITEM", 0),
    ("IFCTOPOLOGYREPRESENTATION", 4), ("IFCTRANSFORMERTYPE", 10), ("IFCTRANSPORTELEMENT", 11),
    ("IFCTRANSPORTELEMENTTYPE", 10), ("IFCTRAPEZIUMPROFILEDEF", 7), ("IFCTRIMMEDCURVE", 5),
    ("IFCTSHAPEPROFILEDEF", 13), ("IFCTUBEBUNDLETYPE", 10), ("IFCTWODIRECTIONREPEATFACTOR", 2), ("IFCTYPEOBJECT", 6),
    ("IFCTYPEPRODUCT", 8), ("IFCUNITARYEQUIPMENTTYPE", 10), ("IFCUNITASSIGNMENT", 1), ("IFCUSHAPEPROFILEDEF", 11),
    ("IFCVALVETYPE", 10), ("IFCVECTOR", 2), ("IFCVERTEX", 0), ("IFCVERTEXBASEDTEXTUREMAP", 2), ("IFCVERTEXLOOP", 1),
    ("IFCVERTEXPOINT", 1), ("IFCVIBRATIONISOLATORTYPE", 10), ("IFCVIRTUALELEMENT", 8),
    ("IFCVIRTUALGRIDINTERSECTION", 2), ("IFCWALL", 8), ("IFCWALLSTANDARDCASE", 8), ("IFCWALLTYPE", 10),
    ("IFCWASTETERMINALTYPE", 10), ("IFCWATERPROPERTIES", 8), ("IFCWINDOW", 10), ("IFCWINDOWLININGPROPERTIES", 13),
    ("IFCWINDOWPANELPROPERTIES", 9), ("IFCWINDOWSTYLE", 12), ("IFCWORKCONTROL", 15), ("IFCWORKPLAN", 15),
    ("IFCWORKSCHEDULE", 15), ("IFCZONE", 5), ("IFCZSHAPEPROFILEDEF", 9),
];

#[rustfmt::skip]
static IFC4_ATTR_COUNTS: &[(&str, u8)] = &[
    ("IFCACTIONREQUEST", 9), ("IFCACTOR", 6), ("IFCACTORROLE", 3), ("IFCACTUATOR", 9), ("IFCACTUATORTYPE", 10),
    ("IFCADDRESS", 3), ("IFCADVANCEDBREP", 1), ("IFCADVANCEDBREPWITHVOIDS", 2), ("IFCADVANCEDFACE", 3),
    ("IFCAIRTERMINAL", 9), ("IFCAIRTERMINALBOX", 9), ("IFCAIRTERMINALBOXTYPE", 10), ("IFCAIRTERMINALTYPE", 10),
    ("IFCAIRTOAIRHEATRECOVERY", 9), ("IFCAIRTOAIRHEATRECOVERYTYPE", 10), ("IFCALARM", 9), ("IFCALARMTYPE", 10),
    ("IFCANNOTATION", 7), ("IFCANNOTATIONFILLAREA", 2), ("IFCAPPLICATION", 4), ("IFCAPPLIEDVALUE", 10),
    ("IFCAPPROVAL", 9), ("IFCAPPROVALRELATIONSHIP", 4), ("IFCARBITRARYCLOSEDPROFILEDEF", 3),
    ("IFCARBITRARYOPENPROFILEDEF", 3), ("IFCARBITRARYPROFILEDEFWITHVOIDS", 4), ("IFCASSET", 14),
    ("IFCASYMMETRICISHAPEPROFILEDEF", 15), ("IFCAUDIOVISUALAPPLIANCE", 9), ("IFCAUDIOVISUALAPPLIANCETYPE", 10),
    ("IFCAXIS1PLACEMENT", 2), ("IFCAXIS2PLACEMENT2D", 2), ("IFCAXIS2PLACEMENT3D", 3), ("IFCBEAM", 9),
    ("IFCBEAMSTANDARDCASE", 9), ("IFCBEAMTYPE", 10), ("IFCBLOBTEXTURE", 7), ("IFCBLOCK", 4), ("IFCBOILER", 9),
    ("IFCBOILERTYPE", 10), ("IFCBOOLEANCLIPPINGRESULT", 3), ("IFCBOOLEANRESULT", 3), ("IFCBOUNDARYCONDITION", 1),
    ("IFCBOUNDARYCURVE", 2), ("IFCBOUNDARYEDGECONDITION", 7), ("IFCBOUNDARYFACECONDITION", 4),
    ("IFCBOUNDARYNODECONDITION", 7), ("IFCBOUNDARYNODECONDITIONWARPING", 8), ("IFCBOUNDEDCURVE", 0),
    ("IFCBOUNDEDSURFACE", 0), ("IFCBOUNDINGBOX", 4), ("IFCBOXEDHALFSPACE", 3), ("IFCBSPLINECURVE", 5),
    ("IFCBSPLINECURVEWITHKNOTS", 8), ("IFCBSPLINESURFACE", 7), ("IFCBSPLINESURFACEWITHKNOTS", 12),
    ("IFCBUILDING", 12), ("IFCBUILDINGELEMENT", 8), ("IFCBUILDINGELEMENTPART", 9), ("IFCBUILDINGELEMENTPARTTYPE", 10),
    ("IFCBUILDINGELEMENTPROXY", 9), ("IFCBUILDINGELEMENTPROXYTYPE", 10), ("IFCBUILDINGELEMENTTYPE", 9),
    ("IFCBUILDINGSTOREY", 10), ("IFCBUILDINGSYSTEM", 7), ("IFCBURNER", 9), ("IFCBURNERTYPE", 10),
    ("IFCCABLECARRIERFITTING", 9), ("IFCCABLECARRIERFITTINGTYPE", 10), ("IFCCABLECARRIERSEGMENT", 9),
    ("IFCCABLECARRIERSEGMENTTYPE", 10), ("IFCCABLEFITTING", 9), ("IFCCABLEFITTINGTYPE", 10), ("IFCCABLESEGMENT", 9),
    ("IFCCABLESEGMENTTYPE", 10), ("IFCCARTESIANPOINT", 1), ("IFCCARTESIANPOINTLIST", 0),
    ("IFCCARTESIANPOINTLIST2D", 1), ("IFCCARTESIANPOINTLIST3D", 1), ("IFCCARTESIANTRANSFORMATIONOPERATOR", 4),
    ("IFCCARTESIANTRANSFORMATIONOPERATOR2D", 4), ("IFCCARTESIANTRANSFORMATIONOPERATOR2DNONUNIFORM", 5),
    ("IFCCARTESIANTRANSFORMATIONOPERATOR3D", 5), ("IFCCARTESIANTRANSFORMATIONOPERATOR3DNONUNIFORM", 7),
    ("IFCCENTERLINEPROFILEDEF", 4), ("IFCCHILLER", 9), ("IFCCHILLERTYPE", 10), ("IFCCHIMNEY", 9),
    ("IFCCHIMNEYTYPE", 10), ("IFCCIRCLE", 2), ("IFCCIRCLEHOLLOWPROFILEDEF", 5), ("IFCCIRCLEPROFILEDEF", 4),
    ("IFCCIVILELEMENT", 8), ("IFCCIVILELEMENTTYPE", 9), ("IFCCLASSIFICATION", 7), ("IFCCLASSIFICATIONREFERENCE", 6),
    ("IFCCLOSEDSHELL", 1), ("IFCCOIL", 9), ("IFCCOILTYPE", 10), ("IFCCOLOURRGB", 4), ("IFCCOLOURRGBLIST", 1),
    ("IFCCOLOURSPECIFICATION", 1), ("IFCCOLUMN", 9), ("IFCCOLUMNSTANDARDCASE", 9), ("IFCCOLUMNTYPE", 10),
    ("IFCCOMMUNICATIONSAPPLIANCE", 9), ("IFCCOMMUNICATIONSAPPLIANCETYPE", 10), ("IFCCOMPLEXPROPERTY", 4),
    ("IFCCOMPLEXPROPERTYTEMPLATE", 7), ("IFCCOMPOSITECURVE", 2), ("IFCCOMPOSITECURVEONSURFACE", 2),
    ("IFCCOMPOSITECURVESEGMENT", 3), ("IFCCOMPOSITEPROFILEDEF", 4), ("IFCCOMPRESSOR", 9), ("IFCCOMPRESSORTYPE", 10),
    ("IFCCONDENSER", 9), ("IFCCONDENSERTYPE", 10), ("IFCCONIC", 1), ("IFCCONNECTEDFACESET", 1),
    ("IFCCONNECTIONCURVEGEOMETRY", 2), ("IFCCONNECTIONGEOMETRY", 0), ("IFCCONNECTIONPOINTECCENTRICITY", 5),
    ("IFCCONNECTIONPOINTGEOMETRY", 2), ("IFCCONNECTIONSURFACEGEOMETRY", 2), ("IFCCONNECTIONVOLUMEGEOMETRY", 2),
    ("IFCCONSTRAINT", 7), ("IFCCONSTRUCTIONEQUIPMENTRESOURCE", 11), ("IFCCONSTRUCTIONEQUIPMENTRESOURCETYPE", 12),
    ("IFCCONSTRUCTIONMATERIALRESOURCE", 11), ("IFCCONSTRUCTIONMATERIALRESOURCETYPE", 12),
    ("IFCCONSTRUCTIONPRODUCTRESOURCE", 11), ("IFCCONSTRUCTIONPRODUCTRESOURCETYPE", 12),
    ("IFCCONSTRUCTIONRESOURCE", 10), ("IFCCONSTRUCTIONRESOURCETYPE", 11), ("IFCCONTEXT", 9),
    ("IFCCONTEXTDEPENDENTUNIT", 3), ("IFCCONTROL", 6), ("IFCCONTROLLER", 9), ("IFCCONTROLLERTYPE", 10),
    ("IFCCONVERSIONBASEDUNIT", 4), ("IFCCONVERSIONBASEDUNITWITHOFFSET", 5), ("IFCCOOLEDBEAM", 9),
    ("IFCCOOLEDBEAMTYPE", 10), ("IFCCOOLINGTOWER", 9), ("IFCCOOLINGTOWERTYPE", 10), ("IFCCOORDINATEOPERATION", 2),
    ("IFCCOORDINATEREFERENCESYSTEM", 4), ("IFCCOSTITEM", 9), ("IFCCOSTSCHEDULE", 10), ("IFCCOSTVALUE", 10),
    ("IFCCOVERING", 9), ("IFCCOVERINGTYPE", 10), ("IFCCREWRESOURCE", 11), ("IFCCREWRESOURCETYPE", 12),
    ("IFCCSGPRIMITIVE3D", 1), ("IFCCSGSOLID", 1), ("IFCCSHAPEPROFILEDEF", 8), ("IFCCURRENCYRELATIONSHIP", 7),
    ("IFCCURTAINWALL", 9), ("IFCCURTAINWALLTYPE", 10), ("IFCCURVE", 0), ("IFCCURVEBOUNDEDPLANE", 3),
    ("IFCCURVEBOUNDEDSURFACE", 3), ("IFCCURVESTYLE", 5), ("IFCCURVESTYLEFONT", 2), ("IFCCURVESTYLEFONTANDSCALING", 3),
    ("IFCCURVESTYLEFONTPATTERN", 2), ("IFCCYLINDRICALSURFACE", 2), ("IFCDAMPER", 9), ("IFCDAMPERTYPE", 10),
    ("IFCDERIVEDPROFILEDEF", 5), ("IFCDERIVEDUNIT", 3), ("IFCDERIVEDUNITELEMENT", 2), ("IFCDIMENSIONALEXPONENTS", 7),
    ("IFCDIRECTION", 1), ("IFCDISCRETEACCESSORY", 9), ("IFCDISCRETEACCESSORYTYPE", 10),
    ("IFCDISTRIBUTIONCHAMBERELEMENT", 9), ("IFCDISTRIBUTIONCHAMBERELEMENTTYPE", 10), ("IFCDISTRIBUTIONCIRCUIT", 7),
    ("IFCDISTRIBUTIONCONTROLELEMENT", 8), ("IFCDISTRIBUTIONCONTROLELEMENTTYPE", 9), ("IFCDISTRIBUTIONELEMENT", 8),
    ("IFCDISTRIBUTIONELEMENTTYPE", 9), ("IFCDISTRIBUTIONFLOWELEMENT", 8), ("IFCDISTRIBUTIONFLOWELEMENTTYPE", 9),
    ("IFCDISTRIBUTIONPORT", 10), ("IFCDISTRIBUTIONSYSTEM", 7), ("IFCDOCUMENTINFORMATION", 17),
    ("IFCDOCUMENTINFORMATIONRELATIONSHIP", 5), ("IFCDOCUMENTREFERENCE", 5), ("IFCDOOR", 13),
    ("IFCDOORLININGPROPERTIES", 17), ("IFCDOORPANELPROPERTIES", 9), ("IFCDOORSTANDARDCASE", 13), ("IFCDOORSTYLE", 12),
    ("IFCDOORTYPE", 13), ("IFCDRAUGHTINGPREDEFINEDCOLOUR", 1), ("IFCDRAUGHTINGPREDEFINEDCURVEFONT", 1),
    ("IFCDUCTFITTING", 9), ("IFCDUCTFITTINGTYPE", 10), ("IFCDUCTSEGMENT", 9), ("IFCDUCTSEGMENTTYPE", 10),
    ("IFCDUCTSILENCER", 9), ("IFCDUCTSILENCERTYPE", 10), ("IFCEDGE", 2), ("IFCEDGECURVE", 4), ("IFCEDGELOOP", 1),
    ("IFCELECTRICAPPLIANCE", 9), ("IFCELECTRICAPPLIANCETYPE", 10), ("IFCELECTRICDISTRIBUTIONBOARD", 9),
    ("IFCELECTRICDISTRIBUTIONBOARDTYPE", 10), ("IFCELECTRICFLOWSTORAGEDEVICE", 9),
    ("IFCELECTRICFLOWSTORAGEDEVICETYPE", 10), ("IFCELECTRICGENERATOR", 9), ("IFCELECTRICGENERATORTYPE", 10),
    ("IFCELECTRICMOTOR", 9), ("IFCELECTRICMOTORTYPE", 10), ("IFCELECTRICTIMECONTROL", 9),
    ("IFCELECTRICTIMECONTROLTYPE", 10), ("IFCELEMENT", 8), ("IFCELEMENTARYSURFACE", 1), ("IFCELEMENTASSEMBLY", 10),
    ("IFCELEMENTASSEMBLYTYPE", 10), ("IFCELEMENTCOMPONENT", 8), ("IFCELEMENTCOMPONENTTYPE", 9),
    ("IFCELEMENTQUANTITY", 6), ("IFCELEMENTTYPE", 9), ("IFCELLIPSE", 3), ("IFCELLIPSEPROFILEDEF", 5),
    ("IFCENERGYCONVERSIONDEVICE", 8), ("IFCENERGYCONVERSIONDEVICETYPE", 9), ("IFCENGINE", 9), ("IFCENGINETYPE", 10),
    ("IFCEVAPORATIVECOOLER", 9), ("IFCEVAPORATIVECOOLERTYPE", 10), ("IFCEVAPORATOR", 9), ("IFCEVAPORATORTYPE", 10),
    ("IFCEVENT", 11), ("IFCEVENTTIME", 7), ("IFCEVENTTYPE", 12), ("IFCEXTENDEDPROPERTIES", 3),
    ("IFCEXTERNALINFORMATION", 0), ("IFCEXTERNALLYDEFINEDHATCHSTYLE", 3), ("IFCEXTERNALLYDEFINEDSURFACESTYLE", 3),
    ("IFCEXTERNALLYDEFINEDTEXTFONT", 3), ("IFCEXTERNALREFERENCE", 3), ("IFCEXTERNALREFERENCERELATIONSHIP", 4),
    ("IFCEXTERNALSPATIALELEMENT", 9), ("IFCEXTERNALSPATIALSTRUCTUREELEMENT", 8), ("IFCEXTRUDEDAREASOLID", 4),
    ("IFCEXTRUDEDAREASOLIDTAPERED", 5), ("IFCFACE", 1), ("IFCFACEBASEDSURFACEMODEL", 1), ("IFCFACEBOUND", 2),
    ("IFCFACEOUTERBOUND", 2), ("IFCFACESURFACE", 3), ("IFCFACETEDBREP", 1), ("IFCFACETEDBREPWITHVOIDS", 2),
    ("IFCFAILURECONNECTIONCONDITION", 7), ("IFCFAN", 9), ("IFCFANTYPE", 10), ("IFCFASTENER", 9),
    ("IFCFASTENERTYPE", 10), ("IFCFEATUREELEMENT", 8), ("IFCFEATUREELEMENTADDITION", 8),
    ("IFCFEATUREELEMENTSUBTRACTION", 8), ("IFCFILLAREASTYLE", 3), ("IFCFILLAREASTYLEHATCHING", 5),
    ("IFCFILLAREASTYLETILES", 3), ("IFCFILTER", 9), ("IFCFILTERTYPE", 10), ("IFCFIRESUPPRESSIONTERMINAL", 9),
    ("IFCFIRESUPPRESSIONTERMINALTYPE", 10), ("IFCFIXEDREFERENCESWEPTAREASOLID", 6), ("IFCFLOWCONTROLLER", 8),
    ("IFCFLOWCONTROLLERTYPE", 9), ("IFCFLOWFITTING", 8), ("IFCFLOWFITTINGTYPE", 9), ("IFCFLOWINSTRUMENT", 9),
    ("IFCFLOWINSTRUMENTTYPE", 10), ("IFCFLOWMETER", 9), ("IFCFLOWMETERTYPE", 10), ("IFCFLOWMOVINGDEVICE", 8),
    ("IFCFLOWMOVINGDEVICETYPE", 9), ("IFCFLOWSEGMENT", 8), ("IFCFLOWSEGMENTTYPE", 9), ("IFCFLOWSTORAGEDEVICE", 8),
    ("IFCFLOWSTORAGEDEVICETYPE", 9), ("IFCFLOWTERMINAL", 8), ("IFCFLOWTERMINALTYPE", 9),
    ("IFCFLOWTREATMENTDEVICE", 8), ("IFCFLOWTREATMENTDEVICETYPE", 9), ("IFCFOOTING", 9), ("IFCFOOTINGTYPE", 10),
    ("IFCFURNISHINGELEMENT", 8), ("IFCFURNISHINGELEMENTTYPE", 9), ("IFCFURNITURE", 9), ("IFCFURNITURETYPE", 11),
    ("IFCGEOGRAPHICELEMENT", 9), ("IFCGEOGRAPHICELEMENTTYPE", 10), ("IFCGEOMETRICCURVESET", 1),
    ("IFCGEOMETRICREPRESENTATIONCONTEXT", 6), ("IFCGEOMETRICREPRESENTATIONITEM", 0),
    ("IFCGEOMETRICREPRESENTATIONSUBCONTEXT", 10), ("IFCGEOMETRICSET", 1), ("IFCGRID", 11), ("IFCGRIDAXIS", 3),
    ("IFCGRIDPLACEMENT", 2), ("IFCGROUP", 5), ("IFCHALFSPACESOLID", 2), ("IFCHEATEXCHANGER", 9),
    ("IFCHEATEXCHANGERTYPE", 10), ("IFCHUMIDIFIER", 9), ("IFCHUMIDIFIERTYPE", 10), ("IFCIMAGETEXTURE", 6),
    ("IFCINDEXEDCOLOURMAP", 4), ("IFCINDEXEDPOLYCURVE", 3), ("IFCINDEXEDPOLYGONALFACE", 1),
    ("IFCINDEXEDPOLYGONALFACEWITHVOIDS", 2), ("IFCINDEXEDTEXTUREMAP", 3), ("IFCINDEXEDTRIANGLETEXTUREMAP", 4),
    ("IFCINTERCEPTOR", 9), ("IFCINTERCEPTORTYPE", 10), ("IFCINTERSECTIONCURVE", 3), ("IFCINVENTORY", 11),
    ("IFCIRREGULARTIMESERIES", 9), ("IFCIRREGULARTIMESERIESVALUE", 2), ("IFCISHAPEPROFILEDEF", 10),
    ("IFCJUNCTIONBOX", 9), ("IFCJUNCTIONBOXTYPE", 10), ("IFCLABORRESOURCE", 11), ("IFCLABORRESOURCETYPE", 12),
    ("IFCLAGTIME", 5), ("IFCLAMP", 9), ("IFCLAMPTYPE", 10), ("IFCLIBRARYINFORMATION", 6), ("IFCLIBRARYREFERENCE", 6),
    ("IFCLIGHTDISTRIBUTIONDATA", 3), ("IFCLIGHTFIXTURE", 9), ("IFCLIGHTFIXTURETYPE", 10),
    ("IFCLIGHTINTENSITYDISTRIBUTION", 2), ("IFCLIGHTSOURCE", 4), ("IFCLIGHTSOURCEAMBIENT", 4),
    ("IFCLIGHTSOURCEDIRECTIONAL", 5), ("IFCLIGHTSOURCEGONIOMETRIC", 10), ("IFCLIGHTSOURCEPOSITIONAL", 9),
    ("IFCLIGHTSOURCESPOT", 13), ("IFCLINE", 2), ("IFCLOCALPLACEMENT", 2), ("IFCLOOP", 0), ("IFCLSHAPEPROFILEDEF", 9),
    ("IFCMANIFOLDSOLIDBREP", 1), ("IFCMAPCONVERSION", 8), ("IFCMAPPEDITEM", 2), ("IFCMATERIAL", 3),
    ("IFCMATERIALCLASSIFICATIONRELATIONSHIP", 2), ("IFCMATERIALCONSTITUENT", 5), ("IFCMATERIALCONSTITUENTSET", 3),
    ("IFCMATERIALDEFINITION", 0), ("IFCMATERIALDEFINITIONREPRESENTATION", 4), ("IFCMATERIALLAYER", 7),
    ("IFCMATERIALLAYERSET", 3), ("IFCMATERIALLAYERSETUSAGE", 5), ("IFCMATERIALLAYERWITHOFFSETS", 9),
    ("IFCMATERIALLIST", 1), ("IFCMATERIALPROFILE", 6), ("IFCMATERIALPROFILESET", 4),
    ("IFCMATERIALPROFILESETUSAGE", 3), ("IFCMATERIALPROFILESETUSAGETAPERING", 5),
    ("IFCMATERIALPROFILEWITHOFFSETS", 7), ("IFCMATERIALPROPERTIES", 4), ("IFCMATERIALRELATIONSHIP", 5),
    ("IFCMATERIALUSAGEDEFINITION", 0), ("IFCMEASUREWITHUNIT", 2), ("IFCMECHANICALFASTENER", 11),
    ("IFCMECHANICALFASTENERTYPE", 12), ("IFCMEDICALDEVICE", 9), ("IFCMEDICALDEVICETYPE", 10), ("IFCMEMBER", 9),
    ("IFCMEMBERSTANDARDCASE", 9), ("IFCMEMBERTYPE", 10), ("IFCMETRIC", 11), ("IFCMIRROREDPROFILEDEF", 5),
    ("IFCMONETARYUNIT", 1), ("IFCMOTORCONNECTION", 9), ("IFCMOTORCONNECTIONTYPE", 10), ("IFCNAMEDUNIT", 2),
    ("IFCOBJECT", 5), ("IFCOBJECTDEFINITION", 4), ("IFCOBJECTIVE", 11), ("IFCOBJECTPLACEMENT", 0), ("IFCOCCUPANT", 7),
    ("IFCOFFSETCURVE2D", 3), ("IFCOFFSETCURVE3D", 4), ("IFCOPENINGELEMENT", 9), ("IFCOPENINGSTANDARDCASE", 9),
    ("IFCOPENSHELL", 1), ("IFCORGANIZATION", 5), ("IFCORGANIZATIONRELATIONSHIP", 4), ("IFCORIENTEDEDGE", 4),
    ("IFCOUTERBOUNDARYCURVE", 2), ("IFCOUTLET", 9), ("IFCOUTLETTYPE", 10), ("IFCOWNERHISTORY", 8),
    ("IFCPARAMETERIZEDPROFILEDEF", 3), ("IFCPATH", 1), ("IFCPCURVE", 2), ("IFCPERFORMANCEHISTORY", 8),
    ("IFCPERMEABLECOVERINGPROPERTIES", 9), ("IFCPERMIT", 9), ("IFCPERSON", 8), ("IFCPERSONANDORGANIZATION", 3),
    ("IFCPHYSICALCOMPLEXQUANTITY", 6), ("IFCPHYSICALQUANTITY", 2), ("IFCPHYSICALSIMPLEQUANTITY", 3), ("IFCPILE", 10),
    ("IFCPILETYPE", 10), ("IFCPIPEFITTING", 9), ("IFCPIPEFITTINGTYPE", 10), ("IFCPIPESEGMENT", 9),
    ("IFCPIPESEGMENTTYPE", 10), ("IFCPIXELTEXTURE", 9), ("IFCPLACEMENT", 1), ("IFCPLANARBOX", 3),
    ("IFCPLANAREXTENT", 2), ("IFCPLANE", 1), ("IFCPLATE", 9), ("IFCPLATESTANDARDCASE", 9), ("IFCPLATETYPE", 10),
    ("IFCPOINT", 0), ("IFCPOINTONCURVE", 2), ("IFCPOINTONSURFACE", 3), ("IFCPOLYGONALBOUNDEDHALFSPACE", 4),
    ("IFCPOLYGONALFACESET", 4), ("IFCPOLYLINE", 1), ("IFCPOLYLOOP", 1), ("IFCPORT", 7), ("IFCPOSTALADDRESS", 10),
    ("IFCPREDEFINEDCOLOUR", 1), ("IFCPREDEFINEDCURVEFONT", 1), ("IFCPREDEFINEDITEM", 1),
    ("IFCPREDEFINEDPROPERTIES", 0), ("IFCPREDEFINEDPROPERTYSET", 4), ("IFCPREDEFINEDTEXTFONT", 1),
    ("IFCPRESENTATIONITEM", 0), ("IFCPRESENTATIONLAYERASSIGNMENT", 4), ("IFCPRESENTATIONLAYERWITHSTYLE", 8),
    ("IFCPRESENTATIONSTYLE", 1), ("IFCPRESENTATIONSTYLEASSIGNMENT", 1), ("IFCPROCEDURE", 8), ("IFCPROCEDURETYPE", 10),
    ("IFCPROCESS", 7), ("IFCPRODUCT", 7), ("IFCPRODUCTDEFINITIONSHAPE", 3), ("IFCPRODUCTREPRESENTATION", 3),
    ("IFCPROFILEDEF", 2), ("IFCPROFILEPROPERTIES", 4), ("IFCPROJECT", 9), ("IFCPROJECTEDCRS", 7),
    ("IFCPROJECTIONELEMENT", 9), ("IFCPROJECTLIBRARY", 9), ("IFCPROJECTORDER", 9), ("IFCPROPERTY", 2),
    ("IFCPROPERTYABSTRACTION", 0), ("IFCPROPERTYBOUNDEDVALUE", 6), ("IFCPROPERTYDEFINITION", 4),
    ("IFCPROPERTYDEPENDENCYRELATIONSHIP", 5), ("IFCPROPERTYENUMERATEDVALUE", 4), ("IFCPROPERTYENUMERATION", 3),
    ("IFCPROPERTYLISTVALUE", 4), ("IFCPROPERTYREFERENCEVALUE", 4), ("IFCPROPERTYSET", 5),
    ("IFCPROPERTYSETDEFINITION", 4), ("IFCPROPERTYSETTEMPLATE", 7), ("IFCPROPERTYSINGLEVALUE", 4),
    ("IFCPROPERTYTABLEVALUE", 8), ("IFCPROPERTYTEMPLATE", 4), ("IFCPROPERTYTEMPLATEDEFINITION", 4),
    ("IFCPROTECTIVEDEVICE", 9), ("IFCPROTECTIVEDEVICETRIPPINGUNIT", 9), ("IFCPROTECTIVEDEVICETRIPPINGUNITTYPE", 10),
    ("IFCPROTECTIVEDEVICETYPE", 10), ("IFCPROXY", 9), ("IFCPUMP", 9), ("IFCPUMPTYPE", 10), ("IFCQUANTITYAREA", 5),
    ("IFCQUANTITYCOUNT", 5), ("IFCQUANTITYLENGTH", 5), ("IFCQUANTITYSET", 4), ("IFCQUANTITYTIME", 5),
    ("IFCQUANTITYVOLUME", 5), ("IFCQUANTITYWEIGHT", 5), ("IFCRAILING", 9), ("IFCRAILINGTYPE", 10), ("IFCRAMP", 9),
    ("IFCRAMPFLIGHT", 9), ("IFCRAMPFLIGHTTYPE", 10), ("IFCRAMPTYPE", 10), ("IFCRATIONALBSPLINECURVEWITHKNOTS", 9),
    ("IFCRATIONALBSPLINESURFACEWITHKNOTS", 13), ("IFCRECTANGLEHOLLOWPROFILEDEF", 8), ("IFCRECTANGLEPROFILEDEF", 5),
    ("IFCRECTANGULARPYRAMID", 4), ("IFCRECTANGULARTRIMMEDSURFACE", 7), ("IFCRECURRENCEPATTERN", 8),
    ("IFCREFERENCE", 5), ("IFCREGULARTIMESERIES", 10), ("IFCREINFORCEMENTBARPROPERTIES", 6),
    ("IFCREINFORCEMENTDEFINITIONPROPERTIES", 6), ("IFCREINFORCINGBAR", 14), ("IFCREINFORCINGBARTYPE", 16),
    ("IFCREINFORCINGELEMENT", 9), ("IFCREINFORCINGELEMENTTYPE", 9), ("IFCREINFORCINGMESH", 18),
    ("IFCREINFORCINGMESHTYPE", 20), ("IFCRELAGGREGATES", 6), ("IFCRELASSIGNS", 6), ("IFCRELASSIGNSTOACTOR", 8),
    ("IFCRELASSIGNSTOCONTROL", 7), ("IFCRELASSIGNSTOGROUP", 7), ("IFCRELASSIGNSTOGROUPBYFACTOR", 8),
    ("IFCRELASSIGNSTOPROCESS", 8), ("IFCRELASSIGNSTOPRODUCT", 7), ("IFCRELASSIGNSTORESOURCE", 7),
    ("IFCRELASSOCIATES", 5), ("IFCRELASSOCIATESAPPROVAL", 6), ("IFCRELASSOCIATESCLASSIFICATION", 6),
    ("IFCRELASSOCIATESCONSTRAINT", 7), ("IFCRELASSOCIATESDOCUMENT", 6), ("IFCRELASSOCIATESLIBRARY", 6),
    ("IFCRELASSOCIATESMATERIAL", 6), ("IFCRELATIONSHIP", 4), ("IFCRELCONNECTS", 4), ("IFCRELCONNECTSELEMENTS", 7),
    ("IFCRELCONNECTSPATHELEMENTS", 11), ("IFCRELCONNECTSPORTS", 7), ("IFCRELCONNECTSPORTTOELEMENT", 6),
    ("IFCRELCONNECTSSTRUCTURALACTIVITY", 6), ("IFCRELCONNECTSSTRUCTURALMEMBER", 10),
    ("IFCRELCONNECTSWITHECCENTRICITY", 11), ("IFCRELCONNECTSWITHREALIZINGELEMENTS", 9),
    ("IFCRELCONTAINEDINSPATIALSTRUCTURE", 6), ("IFCRELCOVERSBLDGELEMENTS", 6), ("IFCRELCOVERSSPACES", 6),
    ("IFCRELDECLARES", 6), ("IFCRELDECOMPOSES", 4), ("IFCRELDEFINES", 4), ("IFCRELDEFINESBYOBJECT", 6),
    ("IFCRELDEFINESBYPROPERTIES", 6), ("IFCRELDEFINESBYTEMPLATE", 6), ("IFCRELDEFINESBYTYPE", 6),
    ("IFCRELFILLSELEMENT", 6), ("IFCRELFLOWCONTROLELEMENTS", 6), ("IFCRELINTERFERESELEMENTS", 9), ("IFCRELNESTS", 6),
    ("IFCRELPROJECTSELEMENT", 6), ("IFCRELREFERENCEDINSPATIALSTRUCTURE", 6), ("IFCRELSEQUENCE", 9),
    ("IFCRELSERVICESBUILDINGS", 6), ("IFCRELSPACEBOUNDARY", 9), ("IFCRELSPACEBOUNDARY1STLEVEL", 10),
    ("IFCRELSPACEBOUNDARY2NDLEVEL", 11), ("IFCRELVOIDSELEMENT", 6), ("IFCREPARAMETRISEDCOMPOSITECURVESEGMENT", 4),
    ("IFCREPRESENTATION", 4), ("IFCREPRESENTATIONCONTEXT", 2), ("IFCREPRESENTATIONITEM", 0),
    ("IFCREPRESENTATIONMAP", 2), ("IFCRESOURCE", 7), ("IFCRESOURCEAPPROVALRELATIONSHIP", 4),
    ("IFCRESOURCECONSTRAINTRELATIONSHIP", 4), ("IFCRESOURCELEVELRELATIONSHIP", 2), ("IFCRESOURCETIME", 18),
    ("IFCREVOLVEDAREASOLID", 4), ("IFCREVOLVEDAREASOLIDTAPERED", 5), ("IFCRIGHTCIRCULARCONE", 3),
    ("IFCRIGHTCIRCULARCYLINDER", 3), ("IFCROOF", 9), ("IFCROOFTYPE", 10), ("IFCROOT", 4),
    ("IFCROUNDEDRECTANGLEPROFILEDEF", 6), ("IFCSANITARYTERMINAL", 9), ("IFCSANITARYTERMINALTYPE", 10),
    ("IFCSCHEDULINGTIME", 3), ("IFCSEAMCURVE", 3), ("IFCSECTIONEDSPINE", 3), ("IFCSECTIONPROPERTIES", 3),
    ("IFCSECTIONREINFORCEMENTPROPERTIES", 6), ("IFCSENSOR", 9), ("IFCSENSORTYPE", 10), ("IFCSHADINGDEVICE", 9),
    ("IFCSHADINGDEVICETYPE", 10), ("IFCSHAPEASPECT", 5), ("IFCSHAPEMODEL", 4), ("IFCSHAPEREPRESENTATION", 4),
    ("IFCSHELLBASEDSURFACEMODEL", 1), ("IFCSIMPLEPROPERTY", 2), ("IFCSIMPLEPROPERTYTEMPLATE", 12), ("IFCSITE", 14),
    ("IFCSIUNIT", 4), ("IFCSLAB", 9), ("IFCSLABELEMENTEDCASE", 9), ("IFCSLABSTANDARDCASE", 9), ("IFCSLABTYPE", 10),
    ("IFCSLIPPAGECONNECTIONCONDITION", 4), ("IFCSOLARDEVICE", 9), ("IFCSOLARDEVICETYPE", 10), ("IFCSOLIDMODEL", 0),
    ("IFCSPACE", 11), ("IFCSPACEHEATER", 9), ("IFCSPACEHEATERTYPE", 10), ("IFCSPACETYPE", 11),
    ("IFCSPATIALELEMENT", 8), ("IFCSPATIALELEMENTTYPE", 9), ("IFCSPATIALSTRUCTUREELEMENT", 9),
    ("IFCSPATIALSTRUCTUREELEMENTTYPE", 9), ("IFCSPATIALZONE", 9), ("IFCSPATIALZONETYPE", 11), ("IFCSPHERE", 2),
    ("IFCSPHERICALSURFACE", 2), ("IFCSTACKTERMINAL", 9), ("IFCSTACKTERMINALTYPE", 10), ("IFCSTAIR", 9),
    ("IFCSTAIRFLIGHT", 13), ("IFCSTAIRFLIGHTTYPE", 10), ("IFCSTAIRTYPE", 10), ("IFCSTRUCTURALACTION", 10),
    ("IFCSTRUCTURALACTIVITY", 9), ("IFCSTRUCTURALANALYSISMODEL", 10), ("IFCSTRUCTURALCONNECTION", 8),
    ("IFCSTRUCTURALCONNECTIONCONDITION", 1), ("IFCSTRUCTURALCURVEACTION", 12), ("IFCSTRUCTURALCURVECONNECTION", 9),
    ("IFCSTRUCTURALCURVEMEMBER", 9), ("IFCSTRUCTURALCURVEMEMBERVARYING", 9), ("IFCSTRUCTURALCURVEREACTION", 10),
    ("IFCSTRUCTURALITEM", 7), ("IFCSTRUCTURALLINEARACTION", 12), ("IFCSTRUCTURALLOAD", 1),
    ("IFCSTRUCTURALLOADCASE", 11), ("IFCSTRUCTURALLOADCONFIGURATION", 3), ("IFCSTRUCTURALLOADGROUP", 10),
    ("IFCSTRUCTURALLOADLINEARFORCE", 7), ("IFCSTRUCTURALLOADORRESULT", 1), ("IFCSTRUCTURALLOADPLANARFORCE", 4),
    ("IFCSTRUCTURALLOADSINGLEDISPLACEMENT", 7), ("IFCSTRUCTURALLOADSINGLEDISPLACEMENTDISTORTION", 8),
    ("IFCSTRUCTURALLOADSINGLEFORCE", 7), ("IFCSTRUCTURALLOADSINGLEFORCEWARPING", 8), ("IFCSTRUCTURALLOADSTATIC", 1),
    ("IFCSTRUCTURALLOADTEMPERATURE", 4), ("IFCSTRUCTURALMEMBER", 7), ("IFCSTRUCTURALPLANARACTION", 12),
    ("IFCSTRUCTURALPOINTACTION", 10), ("IFCSTRUCTURALPOINTCONNECTION", 9), ("IFCSTRUCTURALPOINTREACTION", 9),
    ("IFCSTRUCTURALREACTION", 9), ("IFCSTRUCTURALRESULTGROUP", 8), ("IFCSTRUCTURALSURFACEACTION", 12),
    ("IFCSTRUCTURALSURFACECONNECTION", 8), ("IFCSTRUCTURALSURFACEMEMBER", 9),
    ("IFCSTRUCTURALSURFACEMEMBERVARYING", 9), ("IFCSTRUCTURALSURFACEREACTION", 10), ("IFCSTYLEDITEM", 3),
    ("IFCSTYLEDREPRESENTATION", 4), ("IFCSTYLEMODEL", 4), ("IFCSUBCONTRACTRESOURCE", 11),
    ("IFCSUBCONTRACTRESOURCETYPE", 12), ("IFCSUBEDGE", 3), ("IFCSURFACE", 0), ("IFCSURFACECURVE", 3),
    ("IFCSURFACECURVESWEPTAREASOLID", 6), ("IFCSURFACEFEATURE", 9), ("IFCSURFACEOFLINEAREXTRUSION", 4),
    ("IFCSURFACEOFREVOLUTION", 3), ("IFCSURFACEREINFORCEMENTAREA", 4), ("IFCSURFACESTYLE", 3),
    ("IFCSURFACESTYLELIGHTING", 4), ("IFCSURFACESTYLEREFRACTION", 2), ("IFCSURFACESTYLERENDERING", 9),
    ("IFCSURFACESTYLESHADING", 2), ("IFCSURFACESTYLEWITHTEXTURES", 1), ("IFCSURFACETEXTURE", 5),
    ("IFCSWEPTAREASOLID", 2), ("IFCSWEPTDISKSOLID", 5), ("IFCSWEPTDISKSOLIDPOLYGONAL", 6), ("IFCSWEPTSURFACE", 2),
    ("IFCSWITCHINGDEVICE", 9), ("IFCSWITCHINGDEVICETYPE", 10), ("IFCSYSTEM", 5), ("IFCSYSTEMFURNITUREELEMENT", 9),
    ("IFCSYSTEMFURNITUREELEMENTTYPE", 10), ("IFCTABLE", 3), ("IFCTABLECOLUMN", 5), ("IFCTABLEROW", 2), ("IFCTANK", 9),
    ("IFCTANKTYPE", 10), ("IFCTASK", 13), ("IFCTASKTIME", 20), ("IFCTASKTIMERECURRING", 21), ("IFCTASKTYPE", 11),
    ("IFCTELECOMADDRESS", 9), ("IFCTENDON", 17), ("IFCTENDONANCHOR", 10), ("IFCTENDONANCHORTYPE", 10),
    ("IFCTENDONTYPE", 13), ("IFCTESSELLATEDFACESET", 1), ("IFCTESSELLATEDITEM", 0), ("IFCTEXTLITERAL", 3),
    ("IFCTEXTLITERALWITHEXTENT", 5), ("IFCTEXTSTYLE", 5), ("IFCTEXTSTYLEFONTMODEL", 6),
    ("IFCTEXTSTYLEFORDEFINEDFONT", 2), ("IFCTEXTSTYLETEXTMODEL", 7), ("IFCTEXTURECOORDINATE", 1),
    ("IFCTEXTURECOORDINATEGENERATOR", 3), ("IFCTEXTUREMAP", 3), ("IFCTEXTUREVERTEX", 1), ("IFCTEXTUREVERTEXLIST", 1),
    ("IFCTIMEPERIOD", 2), ("IFCTIMESERIES", 8), ("IFCTIMESERIESVALUE", 1), ("IFCTOPOLOGICALREPRESENTATIONITEM", 0),
    ("IFCTOPOLOGYREPRESENTATION", 4), ("IFCTOROIDALSURFACE", 3), ("IFCTRANSFORMER", 9), ("IFCTRANSFORMERTYPE", 10),
    ("IFCTRANSPORTELEMENT", 9), ("IFCTRANSPORTELEMENTTYPE", 10), ("IFCTRAPEZIUMPROFILEDEF", 7),
    ("IFCTRIANGULATEDFACESET", 5), ("IFCTRIMMEDCURVE", 5), ("IFCTSHAPEPROFILEDEF", 12), ("IFCTUBEBUNDLE", 9),
    ("IFCTUBEBUNDLETYPE", 10), ("IFCTYPEOBJECT", 6), ("IFCTYPEPROCESS", 9), ("IFCTYPEPRODUCT", 8),
    ("IFCTYPERESOURCE", 9), ("IFCUNITARYCONTROLELEMENT", 9), ("IFCUNITARYCONTROLELEMENTTYPE", 10),
    ("IFCUNITARYEQUIPMENT", 9), ("IFCUNITARYEQUIPMENTTYPE", 10), ("IFCUNITASSIGNMENT", 1),
    ("IFCUSHAPEPROFILEDEF", 10), ("IFCVALVE", 9), ("IFCVALVETYPE", 10), ("IFCVECTOR", 2), ("IFCVERTEX", 0),
    ("IFCVERTEXLOOP", 1), ("IFCVERTEXPOINT", 1), ("IFCVIBRATIONISOLATOR", 9), ("IFCVIBRATIONISOLATORTYPE", 10),
    ("IFCVIRTUALELEMENT", 8), ("IFCVIRTUALGRIDINTERSECTION", 2), ("IFCVOIDINGFEATURE", 9), ("IFCWALL", 9),
    ("IFCWALLELEMENTEDCASE", 9), ("IFCWALLSTANDARDCASE", 9), ("IFCWALLTYPE", 10), ("IFCWASTETERMINAL", 9),
    ("IFCWASTETERMINALTYPE", 10), ("IFCWINDOW", 13), ("IFCWINDOWLININGPROPERTIES", 16),
    ("IFCWINDOWPANELPROPERTIES", 9), ("IFCWINDOWSTANDARDCASE", 13), ("IFCWINDOWSTYLE", 12), ("IFCWINDOWTYPE", 13),
    ("IFCWORKCALENDAR", 9), ("IFCWORKCONTROL", 13), ("IFCWORKPLAN", 14), ("IFCWORKSCHEDULE", 14), ("IFCWORKTIME", 6),
    ("IFCZONE", 6), ("IFCZSHAPEPROFILEDEF", 9),
];

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn step_text_escapes_quotes_backslashes_and_non_ascii() {
        assert_eq!(step_text("it's a\\b"), "it''s a\\\\b");
        // The reference forms, as ifcopenshell's own writer emits them.
        assert_eq!(step_text("测试项目"), "\\X2\\6D4B8BD5987976EE\\X0\\");
        assert_eq!(
            step_text("emoji\u{1F600}墙"),
            "emoji\\X4\\0001F60000005899\\X0\\"
        );
        assert_eq!(step_text("剪力墙 JLQ-1"), "\\X2\\526A529B5899\\X0\\ JLQ-1");
        // \X4\ stays in effect for later runs, as in the Python reference.
        assert_eq!(
            step_text("\u{1F600}a墙"),
            "\\X4\\0001F600\\X0\\a\\X4\\00005899\\X0\\"
        );
    }

    #[test]
    fn sanitize_name_trims_and_defaults() {
        assert_eq!(sanitize_name(""), "Unnamed");
        assert_eq!(sanitize_name(" \t\r\n"), "Unnamed");
        assert_eq!(sanitize_name("  O'Brien  "), "O''Brien");
    }

    #[test]
    fn guids_are_22_compressed_base64_characters_and_distinct() {
        let a = generate_ifc_guid();
        let b = generate_ifc_guid();
        assert_eq!(a.len(), 22);
        assert!(a.bytes().all(|c| IFC_BASE64.contains(&c)));
        assert!(matches!(a.as_bytes()[0], b'0'..=b'3'));
        assert_ne!(a, b);
    }

    #[test]
    fn utc_timestamps_format_like_strftime() {
        assert_eq!(iso_utc(0), "1970-01-01T00:00:00");
        assert_eq!(iso_utc(1_700_000_000), "2023-11-14T22:13:20");
        assert_eq!(iso_utc(951_782_400), "2000-02-29T00:00:00");
        assert_eq!(iso_utc(4_102_444_799), "2099-12-31T23:59:59");
    }

    #[test]
    fn attr_count_tables_are_sorted_and_anchored() {
        for table in [IFC2X3_ATTR_COUNTS, IFC4_ATTR_COUNTS] {
            assert!(table.windows(2).all(|w| w[0].0 < w[1].0));
        }
        assert_eq!(IFC2X3_ATTR_COUNTS.len(), 653);
        assert_eq!(IFC4_ATTR_COUNTS.len(), 776);
        assert_eq!(attr_count("IFC4", "IFCDOOR"), Some(13));
        assert_eq!(attr_count("IFC4", "IFCWALL"), Some(9));
        assert_eq!(attr_count("IFC4", "IFCBUILDINGELEMENTPROXY"), Some(9));
        assert_eq!(attr_count("IFC2X3", "IFCDOOR"), Some(10));
        assert_eq!(attr_count("IFC2X3", "IFCWALL"), Some(8));
        assert_eq!(attr_count("IFC4X3", "IFCDOOR"), Some(13));
        assert_eq!(attr_count("IFC4", "IFCNOSUCHTHING"), None);
    }

    #[test]
    fn weld_keys_follow_pythons_round() {
        assert_eq!(weld_key(0.0), weld_key(-0.0));
        assert_eq!(weld_key(-1e-9), weld_key(0.0));
        assert_eq!(weld_key(1.000_000_1), weld_key(1.0));
        assert_ne!(weld_key(1.000_002), weld_key(1.0));
    }
}
