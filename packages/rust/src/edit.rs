//! Load an existing legacy-format `.skp` file and rebuild it as a new, independent
//! [`SkpBuilder`] (`edit.hpp` / `edit.cpp`, itself a port of Python's `openskp.edit`).
//!
//! [`create`](mod@crate::create) only ever builds a brand-new file by splicing new geometry into its
//! own bundled blank scaffold - there is no way to append to or patch an arbitrary existing file's
//! bytes in place, because real SketchUp itself doesn't do that either: it fully re-serializes the
//! whole document on every save, so there is no stable "original bytes + appended bytes"
//! structure to target for a file this project didn't create.
//!
//! This module takes the other viable approach instead: fully parse the existing file with this
//! crate's own reader, then *replay* everything it understood back through the
//! [`create`](mod@crate::create) public API (materials, layers, every component definition, every
//! face/instance, construction points and lines) to produce a brand-new file - not a byte-patched
//! copy of the original, but a freshly-built one with equivalent content, to which the caller can
//! add more geometry before saving.
//!
//! **Adding more geometry after the fact.** The returned builder can take more `add_face`/
//! `add_circle`/`add_instance`/etc. calls, and every material/layer the source had is already
//! reachable via [`SkpBuilder::materials_by_name`]/[`SkpBuilder::layers_by_name`];
//! [`OpenExistingResult::definitions`] maps each component definition's name to its handle, for
//! placing more instances of something the source already defined. What the returned builder can
//! no longer do is register a genuinely NEW material, layer, or component definition/group - the
//! writer's own file-format ordering requirement (materials/layers/definitions must all be
//! finalized before any geometry is written) is already satisfied by the time replay finishes
//! writing the source's own root-level geometry (which happens for any source file with root-level
//! content - in practice, almost always), so `add_material`/`add_layer`/
//! `add_component_definition`/`add_group` all fail with [`SkpWriteError`] on the returned builder
//! in that case. Build anything new into a separate [`create()`](crate::create::create) call
//! instead.
//!
//! **Scope and known fidelity gaps** (every gap here is a genuine, deliberately-scoped limitation
//! carried over from the Python port, not an oversight):
//!
//! * Only a **legacy-format** (SketchUp 2013-2020) source file is accepted - the writer never
//!   writes any other format, so a modern VFF (2021+) source can't be faithfully round-tripped
//!   through it.
//! * Per-edge `hidden`/`soft`/`smooth` flags are applied per-FACE, not per-edge (an "any edge in
//!   this boundary has the flag" approximation) - `add_face` can only set these uniformly for
//!   every edge it newly declares in one call, the same limitation any user of that API has.
//! * A positioned texture is replayed via 3 sample-point correspondences fitted to an affine map
//!   (see [`FaceOptions::front_uv`]/[`FaceOptions::back_uv`]) - exact at those 3 points, but a
//!   genuinely projective (4-pin/distorted) source mapping won't interpolate identically between
//!   them. A *projected* (draped) texture has no equivalent at all and falls back to the default
//!   projection.
//! * A colorized (tinted) material variant is replayed as its plain source texture, losing the
//!   tint.
//! * Per-face material/layer painting: only a face's front/back *material* is replayed - the
//!   reader doesn't expose a per-face layer assignment for legacy files (only instances carry an
//!   explicit layer).
//! * Every placed thing (originally a group or a component instance alike) is replayed as a plain
//!   component instance - structurally simpler, and visually identical, but no longer shows as a
//!   "Group" in SketchUp's Outliner afterward.
//! * Section planes, text entities, and dimensions aren't carried over at all.
//! * A circle/arc/polyline's original `CArcCurve`/`CCurve` grouping is lost - a round-tripped
//!   circle becomes an ordinary straight-edged face.
//! * Definition-level and face-level custom attributes aren't reproduced - only an instance's own
//!   `properties` are.
//!
//! Every one of these gaps is surfaced in the returned `warnings` list wherever it actually
//! affected the specific source file being replayed, not just documented here.

use std::collections::hash_map::RandomState;
use std::collections::{BTreeMap, BTreeSet};
use std::error::Error;
use std::fmt;
use std::hash::{BuildHasher, Hasher};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};

use crate::create::{
    create, AttributeDict, AttributeValue, ComponentDefinitionBuilder, DefinitionHandle,
    DefinitionOptions, FaceOptions, GuideOptions, InstanceOptions, LayerOptions, Matrix3x3, Point3,
    SkpBuilder, SkpWriteError, UvCorrespondence,
};
use crate::errors::SkpParseError;
use crate::internal::is_legacy;
use crate::model::{CoEdge, Definition, EntityId, Face, Instance, SkpModel, Vec3};
use crate::observability::ParseOptions;
use crate::parser::parse_skp;

/// Result of [`open_existing`].
#[derive(Debug)]
pub struct OpenExistingResult {
    /// Ready for more `add_face`/`add_circle`/`add_instance`/etc. calls before
    /// [`SkpBuilder::save`]. Every material and layer the source file had is already reachable via
    /// [`SkpBuilder::materials_by_name`]/[`SkpBuilder::layers_by_name`].
    pub builder: SkpBuilder,
    /// Anything from the source file that couldn't be faithfully reproduced - see the module docs
    /// for the exact, deliberately-scoped gaps this draws from.
    pub warnings: Vec<String>,
    /// Maps each replayed component definition's own name to its (already-closed) definition, so
    /// the caller can place additional instances of something the source file already defined via
    /// `builder.add_instance(definitions["Wheel"], &options)`. If two source definitions share a
    /// name, the later one (by entity id) wins - real SketchUp allows duplicate component names;
    /// only this convenience lookup needs them unique. Definitions with an empty name are not
    /// reachable through this map (but are still replayed and instantiable through their
    /// instances).
    pub definitions: BTreeMap<String, DefinitionHandle>,
}

/// Why [`open_existing`] failed. The C++ port throws one of two exception types; this keeps them
/// apart.
#[derive(Debug)]
pub enum OpenExistingError {
    /// The file can't be read, isn't a legacy-format file, or replaying it through the writer
    /// failed (C++ `SkpWriteError`).
    Write(SkpWriteError),
    /// The reader rejected the file (C++ `SkpParseError`).
    Parse(SkpParseError),
}

impl fmt::Display for OpenExistingError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            OpenExistingError::Write(e) => e.fmt(f),
            OpenExistingError::Parse(e) => e.fmt(f),
        }
    }
}

impl Error for OpenExistingError {
    fn source(&self) -> Option<&(dyn Error + 'static)> {
        match self {
            OpenExistingError::Write(e) => Some(e),
            OpenExistingError::Parse(e) => Some(e),
        }
    }
}

impl From<SkpWriteError> for OpenExistingError {
    fn from(e: SkpWriteError) -> Self {
        OpenExistingError::Write(e)
    }
}

impl From<SkpParseError> for OpenExistingError {
    fn from(e: SkpParseError) -> Self {
        OpenExistingError::Parse(e)
    }
}

// ---------------------------------------------------------------------------------------------
// Loop/edge reconstruction - mirrors Python scene.py's _reconstruct_loop_vertices exactly (the
// same algorithm the glTF scene baker uses to turn a face's CoEdge loop into an ordered vertex
// list). Shared with codegen.rs, which the C++ port duplicates per translation unit.
// ---------------------------------------------------------------------------------------------

pub(crate) type EdgeMap = BTreeMap<EntityId, (EntityId, EntityId)>;

pub(crate) fn build_edge_map(defn: &Definition) -> EdgeMap {
    defn.edges
        .iter()
        .map(|(&id, e)| (id, (e.v1_id, e.v2_id)))
        .collect()
}

pub(crate) fn reconstruct_loop_vertices(lp: &[CoEdge], edges: &EdgeMap) -> Vec<EntityId> {
    let mut loop_verts: Vec<EntityId> = Vec::new();
    for c in lp {
        let Some(&(v1, v2)) = edges.get(&c.edge_id) else {
            continue;
        };
        let v_start = if c.orientation == 1 { v1 } else { v2 };
        if loop_verts.last() != Some(&v_start) {
            loop_verts.push(v_start);
        }
    }
    if loop_verts.len() > 1 && loop_verts.first() == loop_verts.last() {
        loop_verts.pop();
    }
    loop_verts
}

// ---------------------------------------------------------------------------------------------
// UV replay helpers - mirrors Python scene.py's _face_uv_basis / _compute_face_uv (the same
// formulas the glTF scene baker uses).
// ---------------------------------------------------------------------------------------------

fn invert_3x3(m: &[f64; 9]) -> [f64; 9] {
    let [a, b, c, d, e, f, g, h, i] = *m;
    let det = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g);
    if det.abs() < 1e-12 {
        return [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0];
    }
    let inv_det = 1.0 / det;
    [
        (e * i - f * h) * inv_det,
        (c * h - b * i) * inv_det,
        (b * f - c * e) * inv_det,
        (f * g - d * i) * inv_det,
        (a * i - c * g) * inv_det,
        (c * d - a * f) * inv_det,
        (d * h - e * g) * inv_det,
        (b * g - a * h) * inv_det,
        (a * e - b * d) * inv_det,
    ]
}

const UV_VERTICAL_TOLERANCE: f64 = 1e-3;

pub(crate) fn face_uv_basis(n: Point3) -> (Point3, Point3) {
    let (cx, cy) = (-n[1], n[0]);
    let clen = (cx * cx + cy * cy).sqrt();
    if clen < UV_VERTICAL_TOLERANCE {
        let xr = if n[2] >= 0.0 {
            [1.0, 0.0, 0.0]
        } else {
            [-1.0, 0.0, 0.0]
        };
        return (xr, [0.0, 1.0, 0.0]);
    }
    let xr = [cx / clen, cy / clen, 0.0];
    let yr = [
        n[1] * xr[2] - n[2] * xr[1],
        n[2] * xr[0] - n[0] * xr[2],
        n[0] * xr[1] - n[1] * xr[0],
    ];
    (xr, yr)
}

fn compute_face_uv(
    p: Point3,
    xr: Point3,
    yr: Point3,
    uv_transform: Option<&[f64; 9]>,
    tile_w: f64,
    tile_h: f64,
) -> (f64, f64) {
    let px = p[0] * xr[0] + p[1] * xr[1] + p[2] * xr[2];
    let py = p[0] * yr[0] + p[1] * yr[1] + p[2] * yr[2];
    let Some(transform) = uv_transform else {
        return (px / tile_w, py / tile_h);
    };
    let inv = invert_3x3(transform);
    let u = px * inv[0] + py * inv[3] + inv[6];
    let v = px * inv[1] + py * inv[4] + inv[7];
    let mut q = px * inv[2] + py * inv[5] + inv[8];
    if q.abs() < 1e-12 {
        q = 1.0;
    }
    ((u / q) / tile_w, (v / q) / tile_h)
}

// ---------------------------------------------------------------------------------------------
// Material/layer replay.
// ---------------------------------------------------------------------------------------------

/// A process-unique random number for temp file names (the C++ port seeds an `mt19937_64` from
/// `std::random_device`; std's `RandomState` keys are the zero-dependency equivalent).
fn random_u64() -> u64 {
    static COUNTER: AtomicU64 = AtomicU64::new(0);
    let mut h = RandomState::new().build_hasher();
    h.write_u64(COUNTER.fetch_add(1, Ordering::Relaxed));
    h.write_u32(std::process::id());
    h.finish()
}

/// The image's file-name suffix (from the last `.`), `.png` if there is none.
pub(crate) fn texture_suffix(filename: &str) -> &str {
    filename.rfind('.').map_or(".png", |dot| &filename[dot..])
}

/// Every source material's writer handle, indexed like `model.materials`.
fn replay_materials(
    builder: &mut SkpBuilder,
    model: &SkpModel,
    warnings: &mut Vec<String>,
) -> Result<Vec<i32>, SkpWriteError> {
    let mut slots = Vec::with_capacity(model.materials.len());
    for mat in &model.materials {
        let texture_data = mat.texture.as_ref().and_then(|t| t.data.as_deref());
        let slot = if let (Some(texture), Some(data)) = (&mat.texture, texture_data) {
            let suffix = texture_suffix(&texture.filename);
            let tmp_path: PathBuf =
                std::env::temp_dir().join(format!("openskp_edit_tex_{}{suffix}", random_u64()));
            std::fs::write(&tmp_path, data).map_err(|_| {
                SkpWriteError::new(format!(
                    "cannot create temporary texture file: {}",
                    tmp_path.display()
                ))
            })?;
            // applied_height: 1.0 - every textured face is replayed with an explicit
            // front_uv/back_uv (see replay_uv), whose pins already bake in the SOURCE's real tile
            // size via compute_face_uv - the material's own stored applied height must be a no-op
            // divisor (1.0) or the read-side UV formula divides by it a second time. This happens
            // to match add_texture_material's own default too, but is kept explicit since it's a
            // hard requirement of this call site specifically, not just a safe default.
            let result = builder.add_texture_material(&mat.name, &tmp_path, Some(1.0), None, None);
            let _ = std::fs::remove_file(&tmp_path);
            let slot = result?;
            if mat.colorized {
                warnings.push(format!(
                    "material '{}': colorized tint not reproduced (base texture only)",
                    mat.name
                ));
            }
            slot
        } else {
            if mat.texture.is_some() {
                warnings.push(format!(
                    "material '{}': texture image data missing - replayed as solid color",
                    mat.name
                ));
            }
            builder.add_material(&mat.name, mat.color, None)?
        };
        slots.push(slot);
    }
    Ok(slots)
}

fn replay_layers(
    builder: &mut SkpBuilder,
    model: &SkpModel,
) -> Result<BTreeMap<String, i32>, SkpWriteError> {
    let mut slots = BTreeMap::new();
    for layer in &model.layers {
        let extra_dictionaries = layer
            .attribute_dictionaries
            .iter()
            .map(|(name, dict)| {
                let bag: AttributeDict = dict
                    .iter()
                    .map(|(k, v)| (k.clone(), AttributeValue::String(v.clone())))
                    .collect();
                (name.clone(), bag)
            })
            .collect();
        let options = LayerOptions {
            color: Some([layer.color[0], layer.color[1], layer.color[2], 255]),
            hidden: layer.hidden,
            extra_dictionaries,
        };
        slots.insert(
            layer.name.clone(),
            builder.add_layer(&layer.name, &options)?,
        );
    }
    Ok(slots)
}

// ---------------------------------------------------------------------------------------------
// Definition ordering - mirrors edit.py's own _definition_order: dependencies (nested-instance
// targets) before dependents, the ordering constraint ComponentDefinitionBuilder::add_instance
// documents, via a standard DFS post-order topological sort.
// ---------------------------------------------------------------------------------------------

fn definition_order(model: &SkpModel) -> Result<Vec<EntityId>, SkpWriteError> {
    fn visit(
        model: &SkpModel,
        def_id: EntityId,
        visited: &mut BTreeSet<EntityId>,
        in_progress: &mut BTreeSet<EntityId>,
        order: &mut Vec<EntityId>,
    ) -> Result<(), SkpWriteError> {
        if visited.contains(&def_id) {
            return Ok(());
        }
        if in_progress.contains(&def_id) {
            return Err(SkpWriteError::new(format!(
                "circular component-definition reference involving definition {def_id}"
            )));
        }
        if in_progress.len() >= crate::scene_build::MAX_COMPONENT_DEPTH {
            return Err(SkpWriteError::new(format!(
                "component-definition nesting depth exceeds maximum allowed limit at definition {def_id}"
            )));
        }
        in_progress.insert(def_id);
        if let Some(defn) = model.definitions.get(&def_id) {
            for inst in &defn.instances {
                if let Some(child) = inst.ref_idx {
                    if model.definitions.contains_key(&child) {
                        visit(model, child, visited, in_progress, order)?;
                    }
                }
            }
        }
        in_progress.remove(&def_id);
        visited.insert(def_id);
        order.push(def_id);
        Ok(())
    }

    let mut visited = BTreeSet::new();
    let mut in_progress = BTreeSet::new();
    let mut order = Vec::new();
    for &def_id in model.definitions.keys() {
        visit(model, def_id, &mut visited, &mut in_progress, &mut order)?;
    }
    Ok(order)
}

fn definition_has_content(
    defn: &Definition,
    def_builders: &BTreeMap<EntityId, DefinitionHandle>,
) -> bool {
    let edges = build_edge_map(defn);
    for face in defn.faces.values() {
        let Some(outer) = face.loops.first() else {
            continue;
        };
        if reconstruct_loop_vertices(outer, &edges).len() >= 3 {
            return true;
        }
    }
    if defn
        .instances
        .iter()
        .any(|inst| inst.ref_idx.is_some_and(|r| def_builders.contains_key(&r)))
    {
        return true;
    }
    !defn.construction_points.is_empty() || !defn.construction_lines.is_empty()
}

// ---------------------------------------------------------------------------------------------
// Body replay (faces + instances + construction) - generic over the target, since SkpBuilder
// and ComponentDefinitionBuilder expose the identical add_face/add_instance/add_construction_*
// shape this needs (the same duck typing edit.py relies on for its `target` parameter).
// ---------------------------------------------------------------------------------------------

trait ReplayTarget {
    fn add_face(&mut self, points: &[Point3], options: &FaceOptions) -> Result<(), SkpWriteError>;
    fn add_instance(
        &mut self,
        definition: DefinitionHandle,
        options: &InstanceOptions,
    ) -> Result<(), SkpWriteError>;
    fn add_construction_point(
        &mut self,
        position: Point3,
        options: &GuideOptions,
    ) -> Result<(), SkpWriteError>;
    fn add_construction_line(
        &mut self,
        point: Point3,
        point2: Option<Point3>,
        direction: Option<Point3>,
        options: &GuideOptions,
    ) -> Result<(), SkpWriteError>;
}

impl ReplayTarget for SkpBuilder {
    fn add_face(&mut self, points: &[Point3], options: &FaceOptions) -> Result<(), SkpWriteError> {
        SkpBuilder::add_face(self, points, options)
    }
    fn add_instance(
        &mut self,
        definition: DefinitionHandle,
        options: &InstanceOptions,
    ) -> Result<(), SkpWriteError> {
        SkpBuilder::add_instance(self, definition, options)
    }
    fn add_construction_point(
        &mut self,
        position: Point3,
        options: &GuideOptions,
    ) -> Result<(), SkpWriteError> {
        SkpBuilder::add_construction_point(self, position, options)
    }
    fn add_construction_line(
        &mut self,
        point: Point3,
        point2: Option<Point3>,
        direction: Option<Point3>,
        options: &GuideOptions,
    ) -> Result<(), SkpWriteError> {
        SkpBuilder::add_construction_line(self, point, point2, direction, options)
    }
}

impl ReplayTarget for ComponentDefinitionBuilder<'_> {
    fn add_face(&mut self, points: &[Point3], options: &FaceOptions) -> Result<(), SkpWriteError> {
        ComponentDefinitionBuilder::add_face(self, points, options)
    }
    fn add_instance(
        &mut self,
        definition: DefinitionHandle,
        options: &InstanceOptions,
    ) -> Result<(), SkpWriteError> {
        ComponentDefinitionBuilder::add_instance(self, definition, options)
    }
    fn add_construction_point(
        &mut self,
        position: Point3,
        options: &GuideOptions,
    ) -> Result<(), SkpWriteError> {
        ComponentDefinitionBuilder::add_construction_point(self, position, options)
    }
    fn add_construction_line(
        &mut self,
        point: Point3,
        point2: Option<Point3>,
        direction: Option<Point3>,
        options: &GuideOptions,
    ) -> Result<(), SkpWriteError> {
        ComponentDefinitionBuilder::add_construction_line(self, point, point2, direction, options)
    }
}

/// `front_uv`/`back_uv` need exactly 3 correspondences whose (u, v) values are NOT collinear (an
/// affine fit is impossible otherwise) - real faces can have a "flat" vertex (three consecutive
/// vertices genuinely collinear in 3D), which `points[0..2]` alone isn't guaranteed to avoid.
/// Search for the first non-collinear triple - an affine map preserves collinearity, so a
/// non-collinear triple in 3D is also non-collinear in (u, v).
pub(crate) fn non_collinear_triple(points: &[Point3]) -> Option<[Point3; 3]> {
    let is_non_collinear = |a: Point3, b: Point3, c: Point3| {
        let e1 = [b[0] - a[0], b[1] - a[1], b[2] - a[2]];
        let e2 = [c[0] - a[0], c[1] - a[1], c[2] - a[2]];
        let cx = e1[1] * e2[2] - e1[2] * e2[1];
        let cy = e1[2] * e2[0] - e1[0] * e2[2];
        let cz = e1[0] * e2[1] - e1[1] * e2[0];
        cx * cx + cy * cy + cz * cz > 1e-9
    };
    let cap = points.len().min(64);
    for i in 0..cap {
        for j in i + 1..cap {
            for k in j + 1..cap {
                if is_non_collinear(points[i], points[j], points[k]) {
                    return Some([points[i], points[j], points[k]]);
                }
            }
        }
    }
    if points.len() > cap {
        let a = points[0];
        let b = *points[1..].iter().max_by(|p, q| {
            let dp = (p[0] - a[0]).powi(2) + (p[1] - a[1]).powi(2) + (p[2] - a[2]).powi(2);
            let dq = (q[0] - a[0]).powi(2) + (q[1] - a[1]).powi(2) + (q[2] - a[2]).powi(2);
            dp.total_cmp(&dq)
        })?;
        for &c in &points[cap..] {
            if is_non_collinear(a, b, c) {
                return Some([a, b, c]);
            }
        }
    }
    None
}

/// The model plus the writer handles every replay step resolves against.
struct ReplayContext<'a> {
    model: &'a SkpModel,
    material_slots: &'a [i32],
    layer_slots: &'a BTreeMap<String, i32>,
    def_builders: &'a BTreeMap<EntityId, DefinitionHandle>,
}

impl ReplayContext<'_> {
    fn material_slot(&self, material_id: Option<EntityId>) -> Option<i32> {
        let index = self.model.material_index(material_id?)?;
        self.material_slots.get(index).copied()
    }

    /// Explicit `front_uv`/`back_uv` for EVERY textured face, not just already-positioned ones -
    /// `compute_face_uv` already computes the correct final UV for the untouched-projection case
    /// too (`uv_transform` is unset) using the source's real tile size, so this reproduces a
    /// default-projected face's true rendering exactly without needing the material's own applied
    /// width/height to match (which, post-replay, is intentionally 1.0 - see replay_materials).
    #[allow(clippy::too_many_arguments)] // the C++ signature
    fn replay_uv(
        &self,
        material_id: Option<EntityId>,
        uv_transform: Option<&[f64; 9]>,
        projected: bool,
        points: &[Point3],
        normal: Option<Vec3>,
        warnings: &mut Vec<String>,
        context: &str,
        side: &str,
    ) -> Option<UvCorrespondence> {
        let mat = self.model.material_by_id(material_id?)?;
        let texture = mat.texture.as_ref()?; // solid color - no UV to replay
        if projected {
            warnings.push(format!(
                "{context}: {side} texture is projected/draped - falls back to default projection"
            ));
            return None;
        }
        let n = normal?;
        let tile_w = if texture.width != 0.0 {
            texture.width
        } else {
            1.0
        };
        let tile_h = if texture.height != 0.0 {
            texture.height
        } else {
            1.0
        };
        let (xr, yr) = face_uv_basis(n);
        let sample = non_collinear_triple(points)?; // every vertex triple collinear - a sliver
        Some(
            sample
                .iter()
                .map(|&p| {
                    let (u, v) = compute_face_uv(p, xr, yr, uv_transform, tile_w, tile_h);
                    (p, [u, v])
                })
                .collect(),
        )
    }

    fn replay_face<T: ReplayTarget>(
        &self,
        target: &mut T,
        face: &Face,
        defn: &Definition,
        edges: &EdgeMap,
        warnings: &mut Vec<String>,
        context: &str,
    ) {
        let Some(outer) = face.loops.first() else {
            warnings.push(format!(
                "{context}: face {} has no loops - skipped",
                face.id
            ));
            return;
        };
        let vert_ids = reconstruct_loop_vertices(outer, edges);
        if vert_ids.len() < 3 {
            warnings.push(format!(
                "{context}: face {} has fewer than 3 usable points - skipped",
                face.id
            ));
            return;
        }
        let mut points = Vec::with_capacity(vert_ids.len());
        for vid in &vert_ids {
            let Some(v) = defn.vertices.get(vid) else {
                warnings.push(format!(
                    "{context}: face {} references a missing vertex - skipped",
                    face.id
                ));
                return;
            };
            points.push([v.x, v.y, v.z]);
        }

        let mut holes = Vec::new();
        for hole_loop in &face.loops[1..] {
            let hole_vert_ids = reconstruct_loop_vertices(hole_loop, edges);
            if hole_vert_ids.len() < 3 {
                warnings.push(format!(
                    "{context}: face {} has a hole with fewer than 3 usable points - skipped",
                    face.id
                ));
                return;
            }
            let mut hole_points = Vec::with_capacity(hole_vert_ids.len());
            for vid in &hole_vert_ids {
                let Some(v) = defn.vertices.get(vid) else {
                    warnings.push(format!(
                        "{context}: face {} has a hole referencing a missing vertex - skipped",
                        face.id
                    ));
                    return;
                };
                hole_points.push([v.x, v.y, v.z]);
            }
            holes.push(hole_points);
        }

        let (mut hidden_edges, mut soft_edges, mut smooth_edges) = (false, false, false);
        for coedge in outer {
            let Some(edge) = defn.edges.get(&coedge.edge_id) else {
                continue;
            };
            hidden_edges |= edge.hidden;
            soft_edges |= edge.soft;
            smooth_edges |= edge.smooth;
        }

        let options = FaceOptions {
            material: self.material_slot(face.material_id),
            back_material: self.material_slot(face.back_material_id),
            hidden: face.hidden,
            soft_edges,
            smooth_edges,
            hidden_edges,
            front_uv: self.replay_uv(
                face.material_id,
                face.uv_transform.as_ref(),
                face.uv_projected,
                &points,
                face.normal,
                warnings,
                context,
                "front",
            ),
            back_uv: self.replay_uv(
                face.back_material_id,
                face.uv_transform_back.as_ref(),
                face.uv_projected_back,
                &points,
                face.normal,
                warnings,
                context,
                "back",
            ),
            holes,
            ..FaceOptions::default()
        };

        if let Err(exc) = target.add_face(&points, &options) {
            warnings.push(format!("{context}: face {} skipped ({exc})", face.id));
        }
    }

    fn replay_instance<T: ReplayTarget>(
        &self,
        target: &mut T,
        inst: &Instance,
        warnings: &mut Vec<String>,
        context: &str,
    ) {
        let Some(&definition) = inst.ref_idx.and_then(|r| self.def_builders.get(&r)) else {
            warnings.push(format!(
                "{context}: instance '{}' references unavailable definition - skipped",
                inst.name
            ));
            return;
        };

        // Ground truth (the legacy CComponentInstance/CGroup reader): the stored transform is 9
        // f64 (row-major 3x3) + 3 f64 (translation) + a trailing 1.0 that isn't stored here -
        // matching the writer's own instance layout exactly, hence the same [0:9]/[9:12] slicing
        // edit.py's own _replay_instance uses.
        let matrix3x3: Option<Matrix3x3> =
            inst.matrix.get(..9).map(|m| std::array::from_fn(|i| m[i]));
        let translation: Point3 = inst
            .matrix
            .get(9..12)
            .map_or([0.0; 3], |t| [t[0], t[1], t[2]]);

        let mut options = InstanceOptions {
            // inst.name unconditionally, even when empty - an explicit empty string is a real,
            // valid instance name (SketchUp stores it that way when a placement was never
            // renamed); leaving `name` unset would make add_instance fall back to the
            // definition's own name instead.
            name: Some(inst.name.clone()),
            translation,
            matrix3x3,
            material: self.material_slot(inst.material_id),
            hidden: inst.hidden,
            ..InstanceOptions::default()
        };
        if !inst.layer.is_empty() {
            if let Some(&slot) = self.layer_slots.get(&inst.layer) {
                options.layer = Some(slot);
            }
        }
        if !inst.properties.is_empty() {
            options.attributes = inst
                .properties
                .iter()
                .map(|(k, v)| (k.clone(), AttributeValue::String(v.clone())))
                .collect();
            options.attribute_dict_name = "dynamic_attributes".to_owned();
        }

        if let Err(exc) = target.add_instance(definition, &options) {
            warnings.push(format!(
                "{context}: instance '{}' skipped ({exc})",
                inst.name
            ));
        }
    }

    fn replay_body<T: ReplayTarget>(
        &self,
        target: &mut T,
        defn: &Definition,
        warnings: &mut Vec<String>,
        context: &str,
    ) {
        let edges = build_edge_map(defn);
        for face in defn.faces.values() {
            self.replay_face(target, face, defn, &edges, warnings, context);
        }
        for inst in &defn.instances {
            self.replay_instance(target, inst, warnings, context);
        }
        let guide_options = |layer: &str, hidden: bool| GuideOptions {
            layer: (!layer.is_empty())
                .then(|| self.layer_slots.get(layer).copied())
                .flatten(),
            hidden,
        };
        for cp in &defn.construction_points {
            let options = guide_options(&cp.layer, cp.hidden);
            if let Err(exc) = target.add_construction_point(cp.position, &options) {
                warnings.push(format!("{context}: construction point skipped ({exc})"));
            }
        }
        for cl in &defn.construction_lines {
            let options = guide_options(&cl.layer, cl.hidden);
            let result = match (cl.start, cl.end) {
                (Some(start), Some(end)) => {
                    target.add_construction_line(start, Some(end), None, &options)
                }
                (None, None) => {
                    target.add_construction_line(cl.point, None, Some(cl.direction), &options)
                }
                _ => {
                    warnings.push(format!("{context}: half-bounded construction line skipped"));
                    continue;
                }
            };
            if let Err(exc) = result {
                warnings.push(format!("{context}: construction line skipped ({exc})"));
            }
        }
    }
}

/// Rebuild an already-parsed model through the writer - [`open_existing`] minus the file I/O
/// and the legacy-format gate.
fn replay_model(model: &SkpModel) -> Result<OpenExistingResult, SkpWriteError> {
    let mut warnings = Vec::new();
    let mut builder = create();

    let material_slots = replay_materials(&mut builder, model, &mut warnings)?;
    let layer_slots = replay_layers(&mut builder, model)?;

    let mut def_builders: BTreeMap<EntityId, DefinitionHandle> = BTreeMap::new();
    for def_id in definition_order(model)? {
        let Some(defn) = model.definitions.get(&def_id) else {
            continue;
        };
        let context_name = if defn.name.is_empty() {
            format!("Definition{def_id}")
        } else {
            defn.name.clone()
        };
        let context = format!("definition '{context_name}'");
        if !definition_has_content(defn, &def_builders) {
            warnings.push(format!("{context}: skipped (no replayable geometry)"));
            continue;
        }
        // defn.name unconditionally, not the context_name fallback above - an explicit empty
        // string is a real, valid definition name. SketchUp Groups are internally just unnamed
        // component definitions (unlike Components, which SketchUp auto-names), so an empty name
        // is common in real files. context_name stays fallback-safe since it's only ever used
        // for a human-readable warning message.
        let ctx = ReplayContext {
            model,
            material_slots: &material_slots,
            layer_slots: &layer_slots,
            def_builders: &def_builders,
        };
        let mut db = builder.add_component_definition(&defn.name, &DefinitionOptions::default())?;
        ctx.replay_body(&mut db, defn, &mut warnings, &context);
        let handle = db.close()?;
        def_builders.insert(def_id, handle);
    }

    let ctx = ReplayContext {
        model,
        material_slots: &material_slots,
        layer_slots: &layer_slots,
        def_builders: &def_builders,
    };
    ctx.replay_body(&mut builder, model.root(), &mut warnings, "root");

    let mut definitions = BTreeMap::new();
    for (def_id, &handle) in &def_builders {
        let name = &model.definitions[def_id].name;
        if !name.is_empty() {
            // If two source definitions share a name, the later one wins.
            definitions.insert(name.clone(), handle);
        }
    }
    Ok(OpenExistingResult {
        builder,
        warnings,
        definitions,
    })
}

/// Parse `path` (a legacy-format `.skp` file) and rebuild it as a new [`SkpBuilder`], replaying
/// materials, layers, every component definition, and all root-level geometry/instances. See the
/// module docs for exactly what is and isn't reproduced.
///
/// Fails with [`OpenExistingError::Write`] if `path` can't be read or isn't a legacy-format file
/// (or replay fails), and with [`OpenExistingError::Parse`] if the reader rejects it.
pub fn open_existing(path: impl AsRef<Path>) -> Result<OpenExistingResult, OpenExistingError> {
    let path = path.as_ref();
    let data = std::fs::read(path)
        .map_err(|_| SkpWriteError::new(format!("cannot open file: {}", path.display())))?;

    if !is_legacy(&data) {
        return Err(SkpWriteError::new(format!(
            "'{}' is not a legacy-format (SketchUp 2013-2020) .skp file - openskp::create only ever \
             writes that format, so only a legacy-format source file can be rebuilt through it (see \
             openskp::edit's own docs for why an arbitrary existing file can't simply be patched)",
            path.display()
        ))
        .into());
    }

    let model = parse_skp(&data, &ParseOptions::default())?;
    Ok(replay_model(&model)?)
}

#[cfg(test)]
mod tests {
    //! The legacy reader isn't ported yet, so `open_existing` can't reach the replay on any real
    //! input; these drive `replay_model` with models from the VFF reader instead.

    use super::*;
    use crate::parser::SkpFile;

    fn fixture(name: &str) -> SkpModel {
        let path = Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("tests")
            .join("fixtures")
            .join(name);
        SkpFile::open(path)
            .unwrap()
            .parse(&ParseOptions::default())
            .unwrap()
    }

    #[test]
    fn reconstructs_loops_by_coedge_orientation() {
        let edges: EdgeMap = BTreeMap::from([(1, (10, 11)), (2, (12, 11)), (3, (12, 10))]);
        let lp = [
            CoEdge {
                edge_id: 1,
                orientation: 1,
            },
            CoEdge {
                edge_id: 2,
                orientation: 0,
            },
            CoEdge {
                edge_id: 3,
                orientation: 1,
            },
            CoEdge {
                edge_id: 99,
                orientation: 1,
            }, // unknown edge - skipped
        ];
        assert_eq!(reconstruct_loop_vertices(&lp, &edges), vec![10, 11, 12]);
    }

    #[test]
    fn non_collinear_triple_skips_a_flat_vertex() {
        let pts = [
            [0.0, 0.0, 0.0],
            [1.0, 0.0, 0.0],
            [2.0, 0.0, 0.0],
            [2.0, 1.0, 0.0],
        ];
        assert_eq!(
            non_collinear_triple(&pts),
            Some([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [2.0, 1.0, 0.0]])
        );
        assert_eq!(non_collinear_triple(&pts[..3]), None);
    }

    #[test]
    fn texture_suffix_falls_back_to_png() {
        assert_eq!(texture_suffix("brick.jpg"), ".jpg");
        assert_eq!(texture_suffix("noext"), ".png");
    }

    #[test]
    fn replays_vff_models_through_the_writer() {
        for name in [
            "nesting_2021.skp",
            "Untitled.skp",
            "arcs_2021.skp",
            "SU_File.skp",
            "instance_scale_term_2021.skp",
        ] {
            let model = fixture(name);
            let mut result = replay_model(&model).unwrap_or_else(|e| panic!("{name}: {e}"));
            for mat in &model.materials {
                assert!(
                    result.builder.materials_by_name().contains_key(&mat.name),
                    "{name}: material {} not replayed",
                    mat.name
                );
            }
            for layer in &model.layers {
                assert!(result.builder.layers_by_name().contains_key(&layer.name));
            }
            let named = model
                .definitions
                .values()
                .filter(|d| !d.name.is_empty())
                .count();
            assert!(result.definitions.len() <= named, "{name}");
            let bytes = result
                .builder
                .to_bytes()
                .unwrap_or_else(|e| panic!("{name}: {e}"));
            assert!(is_legacy(&bytes), "{name}");
            // Every replayed instance is placeable again through the returned map.
            if let Some(&handle) = result.definitions.values().next() {
                result
                    .builder
                    .add_instance(handle, &InstanceOptions::default())
                    .unwrap_or_else(|e| panic!("{name}: {e}"));
            }
        }
    }

    #[test]
    fn rejects_cyclic_definitions() {
        let mut model = SkpModel::default();
        for (id, child) in [(1, 2), (2, 1)] {
            let defn = Definition {
                id,
                instances: vec![Instance {
                    ref_idx: Some(child),
                    ..Instance::default()
                }],
                ..Definition::default()
            };
            model.definitions.insert(id, defn);
        }
        assert!(definition_order(&model).is_err());
    }
}
