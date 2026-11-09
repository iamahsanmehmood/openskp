//! Generate Rust source that rebuilds a parsed [`SkpModel`] from scratch via the
//! [`create`](mod@crate::create) API (`codegen.hpp` / `codegen.cpp`) - a faithful, human-readable,
//! re-runnable transcript of the model as writer API calls, not a serialized dump. The C++ port
//! emits C++ against its own `create.hpp`; this one emits Rust against this crate.
//!
//! The output is one self-contained file: `use openskp::create::*;`, a
//! `pub fn build() -> Result<Vec<u8>, SkpWriteError>` returning the rebuilt file's bytes, and (only
//! when a textured material was emitted) two private helpers that decode the embedded base64
//! image data and stage it in a temporary file for `add_texture_material`.
//!
//! Handles: materials (solid and textured, including default-projection and explicitly-pinned
//! UVs), layers, component/group definitions (built in dependency order), faces (front/back
//! material, holes), instances (transform, instance-level paint, instance-level name).
//!
//! Found and fixed via diffing a real, large file (jeff.skp: 2713 definitions, 113643 faces)
//! against its own regenerated output - the TypeScript port this mirrors (`toTypeScriptCode`)
//! found that an earlier prototype silently dropped instance-level paint (95% of that file's
//! instances) and every instance's own name entirely, and never emitted textured materials at all.
//! The UV/hole math is shared with [`edit`](crate::edit)'s replay.
//!
//! Only reproduces geometry reachable by walking faces (`Definition::faces`) - a real file's
//! standalone/construction edges and curves that don't bound any face are NOT reproduced. This
//! does not affect materials, textures, instance paint, or any face/surface geometry - only
//! invisible construction/reference lines.
//!
//! Also not yet handled: colorized material tint, per-face hidden/soft/smooth edge flags, section
//! planes, text/dimension entities. A model using any of these round-trips its geometry/
//! materials/instances correctly; those specific facts are silently dropped.
//!
//! A face a few millionths of an inch off its own fitted plane (common in real files) is
//! auto-triangulated rather than rejected, mirroring real SketchUp's own tolerance.
//!
//! Differences from the C++ output beyond the language: every number is written as the
//! 4-decimal-rounded value in its shortest exact form (the C++ iostream default keeps only 6
//! significant digits, so a coordinate like `1234.5678` came out as `1234.57`); line breaks in a
//! definition name are replaced in its `//` header comment so they can't end the comment early;
//! a staged texture file is removed even when `add_texture_material` fails; and a large model's
//! statements are wrapped in closure chunks so rustc can compile them (see `CHUNK_ITEMS`).
//!
//! ```no_run
//! use openskp::{to_rust_code, ParseOptions, SkpFile};
//!
//! let model = SkpFile::open("building.skp")?.parse(&ParseOptions::default())?;
//! std::fs::write("rebuild_building.rs", to_rust_code(&model))?;
//! # Ok::<(), Box<dyn std::error::Error>>(())
//! ```

use std::collections::{BTreeMap, BTreeSet};

use crate::create::Point3;
use crate::edit::{
    build_edge_map, face_uv_basis, non_collinear_triple, reconstruct_loop_vertices, texture_suffix,
    EdgeMap,
};
use crate::model::{CoEdge, Definition, EntityId, Face, Instance, Material, SkpModel, Vec3};

fn loop_points(lp: &[CoEdge], edges: &EdgeMap, defn: &Definition) -> Option<Vec<Point3>> {
    let vert_ids = reconstruct_loop_vertices(lp, edges);
    if vert_ids.len() < 3 {
        return None;
    }
    let points: Vec<Point3> = vert_ids
        .iter()
        .filter_map(|vid| defn.vertices.get(vid))
        .map(|v| [v.x, v.y, v.z])
        .collect();
    (points.len() >= 3).then_some(points)
}

/// Unlike `edit`'s inverse, a singular matrix inverts to all zeros here (as in the C++ port).
fn invert3x3(m: &[f64; 9]) -> [f64; 9] {
    let [a, b, c, d, e, f, g, h, i] = *m;
    let det = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g);
    let inv_det = if det.abs() < 1e-15 { 0.0 } else { 1.0 / det };
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
    let inv = invert3x3(transform);
    let u = px * inv[0] + py * inv[3] + inv[6];
    let v = px * inv[1] + py * inv[4] + inv[7];
    let mut q = px * inv[2] + py * inv[5] + inv[8];
    if q.abs() < 1e-12 {
        q = 1.0;
    }
    (u / q / tile_w, v / q / tile_h)
}

fn to_base64(bytes: &[u8]) -> String {
    const CHARS: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut out = String::with_capacity(bytes.len().div_ceil(3) * 4);
    for chunk in bytes.chunks(3) {
        let b0 = u32::from(chunk[0]);
        let b1 = chunk.get(1).map_or(0, |&b| u32::from(b));
        let b2 = chunk.get(2).map_or(0, |&b| u32::from(b));
        out.push(CHARS[(b0 >> 2) as usize] as char);
        out.push(CHARS[(((b0 & 0x03) << 4) | (b1 >> 4)) as usize] as char);
        out.push(if chunk.len() > 1 {
            CHARS[(((b1 & 0x0f) << 2) | (b2 >> 6)) as usize] as char
        } else {
            '='
        });
        out.push(if chunk.len() > 2 {
            CHARS[(b2 & 0x3f) as usize] as char
        } else {
            '='
        });
    }
    out
}

fn round4(n: f64) -> f64 {
    let r = (n * 10000.0).round() / 10000.0;
    if r == 0.0 {
        0.0
    } else {
        r
    }
}

/// `n` rounded to 4 decimals, as an `f64` literal (`12.0`, `-0.75`, `1e20`).
fn num(n: f64) -> String {
    let r = round4(n);
    if r.is_nan() {
        "f64::NAN".to_owned()
    } else if r.is_infinite() {
        if r > 0.0 {
            "f64::INFINITY"
        } else {
            "f64::NEG_INFINITY"
        }
        .to_owned()
    } else {
        // Debug keeps the ".0" on whole numbers and switches to exponent form only for very
        // large magnitudes - both valid float literals.
        format!("{r:?}")
    }
}

fn point_str(p: Point3) -> String {
    format!("[{}, {}, {}]", num(p[0]), num(p[1]), num(p[2]))
}

fn points_str(points: &[Point3]) -> String {
    let parts: Vec<String> = points.iter().map(|&p| point_str(p)).collect();
    format!("[{}]", parts.join(", "))
}

fn matrix3x3_str(m9: &[f64; 9]) -> String {
    let parts: Vec<String> = m9.iter().map(|&v| num(v)).collect();
    format!("[{}]", parts.join(", "))
}

/// A Rust string literal for `s` (`Debug` escapes exactly what a literal needs escaped).
fn rust_string(s: &str) -> String {
    format!("{s:?}")
}

/// Splits `s` into string-literal chunks of at most 16000 chars - the C++ port's limit for MSVC's
/// ~16380-char single literal. Rust has no such limit, but it keeps a large texture's base64 blob
/// from becoming one multi-megabyte source line.
fn string_literal_lines(s: &str) -> Vec<String> {
    const CHUNK: usize = 16000;
    if s.is_empty() {
        return vec![rust_string(s)];
    }
    // base64 is pure ASCII, so byte chunks are char chunks.
    s.as_bytes()
        .chunks(CHUNK)
        .map(|c| rust_string(std::str::from_utf8(c).unwrap_or_default()))
        .collect()
}

/// The instance's (translation, row-major 3x3): `matrix[9..12]` and `matrix[..9]`, zeros if
/// the stored matrix is shorter.
fn instance_transform(inst: &Instance) -> (Point3, [f64; 9]) {
    let mut m9 = [0.0; 9];
    let mut t = [0.0; 3];
    if inst.matrix.len() >= 9 {
        m9.copy_from_slice(&inst.matrix[..9]);
    }
    if inst.matrix.len() >= 12 {
        t.copy_from_slice(&inst.matrix[9..12]);
    }
    (t, m9)
}

fn texture_tile(m: &Material) -> (f64, f64) {
    let texture = m.texture.as_ref();
    let tw = texture.map_or(1.0, |t| if t.width != 0.0 { t.width } else { 1.0 });
    let th = texture.map_or(1.0, |t| if t.height != 0.0 { t.height } else { 1.0 });
    (tw, th)
}

fn has_texture_data(m: &Material) -> bool {
    m.texture
        .as_ref()
        .and_then(|t| t.data.as_ref())
        .is_some_and(|d| !d.is_empty())
}

/// Faces/placements per immediately-invoked closure in a large model's generated code.
///
/// Rust-only: the C++ port emits one flat `build()` regardless of size, which C++ compilers
/// accept. rustc's borrow checker needs memory roughly proportional to locals x basic blocks of
/// one function body, so a real 43k-face model as one flat body exhausted it (rustc aborted with
/// an out-of-memory error). Each closure is borrow-checked as its own body, so a model with more
/// than this many faces and placements in total has every definition's (and the root's)
/// statements wrapped `CHUNK_ITEMS` at a time in `(|| -> Result<(), SkpWriteError> { ... })()?;`.
/// Smaller models come out as the plain flat transcript.
const CHUNK_ITEMS: usize = 500;

/// Every face and placement the generated code could emit.
fn item_count(model: &SkpModel) -> usize {
    model
        .definitions
        .values()
        .chain(std::iter::once(model.root()))
        .map(|d| d.faces.len() + d.instances.len())
        .sum()
}

/// The code generator's running state (the C++ port's lambdas and their captures).
struct Gen<'a> {
    model: &'a SkpModel,
    lines: Vec<String>,
    mat_var: BTreeMap<String, String>,
    textured_mats: BTreeSet<String>,
    def_var: BTreeMap<EntityId, String>,
    faces_skipped_degenerate: usize,
    /// The first definition found nested deeper than
    /// [`MAX_COMPONENT_DEPTH`](crate::scene_build::MAX_COMPONENT_DEPTH). The generated `build()`
    /// then fails with the error the writer would raise for that model.
    too_deep: Option<EntityId>,
    face_opts_counter: usize,
    def_counter: usize,
    inst_opts_counter: usize,
    /// Whether bodies are emitted in closure chunks (see [`CHUNK_ITEMS`]).
    chunked: bool,
}

impl Gen<'_> {
    fn push(&mut self, line: impl Into<String>) {
        self.lines.push(line.into());
    }

    fn material(&self, id: Option<EntityId>) -> Option<&Material> {
        self.model.material_by_id(id?)
    }

    fn uv_triple_str(
        points: &[Point3],
        normal: Option<Vec3>,
        uv_transform: Option<&[f64; 9]>,
        tile_w: f64,
        tile_h: f64,
    ) -> Option<String> {
        let n = normal?;
        if points.len() < 3 {
            return None;
        }
        let sample = non_collinear_triple(points)?;
        let (xr, yr) = face_uv_basis(n);
        let pairs: Vec<String> = sample
            .iter()
            .map(|&p| {
                let (u, v) = compute_face_uv(p, xr, yr, uv_transform, tile_w, tile_h);
                format!("({}, [{}, {}])", point_str(p), num(u), num(v))
            })
            .collect();
        Some(format!("vec![{}]", pairs.join(", ")))
    }

    /// Statement lines assigning `opts_var`'s material/back_material/front_uv/back_uv fields,
    /// and whether any UV was pinned.
    fn material_opts_lines(
        &self,
        face: &Face,
        points: &[Point3],
        opts_var: &str,
    ) -> (Vec<String>, bool) {
        let mut parts = Vec::new();
        let mut has_uv = false;
        let sides = [
            (
                "material",
                "front_uv",
                face.material_id,
                face.uv_transform.as_ref(),
            ),
            (
                "back_material",
                "back_uv",
                face.back_material_id,
                face.uv_transform_back.as_ref(),
            ),
        ];
        for (field, uv_field, material_id, uv_transform) in sides {
            let Some(m) = self.material(material_id) else {
                continue;
            };
            parts.push(format!(
                "{opts_var}.{field} = Some({});",
                self.mat_var.get(&m.name).map_or("", String::as_str)
            ));
            if self.textured_mats.contains(&m.name) {
                let (tw, th) = texture_tile(m);
                if let Some(triple) = Self::uv_triple_str(points, face.normal, uv_transform, tw, th)
                {
                    parts.push(format!("{opts_var}.{uv_field} = Some({triple});"));
                    has_uv = true;
                }
            }
        }
        (parts, has_uv)
    }

    /// Writes one body's statement groups (one per face/placement), each at body indent - or,
    /// for a large model, `CHUNK_ITEMS` at a time inside an immediately-invoked closure (see
    /// [`CHUNK_ITEMS`]).
    fn flush_items(&mut self, items: Vec<Vec<String>>) {
        if !self.chunked {
            for line in items.into_iter().flatten() {
                self.push(format!("    {line}"));
            }
            return;
        }
        for chunk in items.chunks(CHUNK_ITEMS) {
            self.push("    (|| -> Result<(), SkpWriteError> {");
            for line in chunk.iter().flatten() {
                self.push(format!("        {line}"));
            }
            self.push("        Ok(())");
            self.push("    })()?;");
        }
    }

    /// One statement group per face of `defn`, added through `target_var`.
    fn face_items(&mut self, defn: &Definition, target_var: &str) -> Vec<Vec<String>> {
        let mut items = Vec::new();
        let edges = build_edge_map(defn);
        for face in defn.faces.values() {
            let Some(outer) = face.loops.first() else {
                continue;
            };
            let Some(points) = loop_points(outer, &edges, defn) else {
                self.faces_skipped_degenerate += 1;
                continue;
            };
            let holes: Vec<Vec<Point3>> = face.loops[1..]
                .iter()
                .filter_map(|hole| loop_points(hole, &edges, defn))
                .collect();

            let opts_var = format!("face_opts{}", self.face_opts_counter);
            self.face_opts_counter += 1;
            let (opt_lines, has_uv) = self.material_opts_lines(face, &points, &opts_var);
            let points_literal = points_str(&points);

            let mut extra = Vec::new();
            // auto_triangulate - mirrors real SketchUp's own tolerance for a not-quite-flat
            // polygon; incompatible with front_uv/back_uv, so only set when this face has
            // neither. Harmless alongside holes - the writer takes the direct (non-triangulated)
            // path whenever holes are present either way.
            if !has_uv {
                extra.push(format!("{opts_var}.auto_triangulate = true;"));
            }
            if !holes.is_empty() {
                let holes_literal: Vec<String> = holes
                    .iter()
                    .map(|h| {
                        let pts: Vec<String> = h.iter().map(|&p| point_str(p)).collect();
                        format!("vec![{}]", pts.join(", "))
                    })
                    .collect();
                extra.push(format!(
                    "{opts_var}.holes = vec![{}];",
                    holes_literal.join(", ")
                ));
            }

            if opt_lines.is_empty() && extra.is_empty() {
                items.push(vec![format!(
                    "{target_var}.add_face(&{points_literal}, &FaceOptions::default())?;"
                )]);
            } else {
                let mut item = vec![format!("let mut {opts_var} = FaceOptions::default();")];
                item.extend(opt_lines);
                item.extend(extra);
                item.push(format!(
                    "{target_var}.add_face(&{points_literal}, &{opts_var})?;"
                ));
                items.push(item);
            }
        }
        items
    }

    /// Statement lines assigning `opts_var`'s material/name fields.
    fn instance_opts_lines(&self, inst: &Instance, def_name: &str, opts_var: &str) -> Vec<String> {
        let mut parts = Vec::new();
        if let Some(m) = self.material(inst.material_id) {
            parts.push(format!(
                "{opts_var}.material = Some({});",
                self.mat_var.get(&m.name).map_or("", String::as_str)
            ));
        }
        // Explicit even when inst.name is empty: add_instance defaults an OMITTED name to the
        // definition's own name, so a source instance with a genuinely empty name would otherwise
        // come out with that name baked in for real.
        if inst.name != def_name {
            parts.push(format!(
                "{opts_var}.name = Some({}.to_owned());",
                rust_string(&inst.name)
            ));
        }
        parts
    }

    /// The statement group placing `inst` (definition `child_ref`) through `target_var`.
    fn instance_item(
        &mut self,
        inst: &Instance,
        def_name: &str,
        target_var: &str,
        child_ref: &str,
    ) -> Vec<String> {
        let (t, m9) = instance_transform(inst);
        let opts_var = format!("inst_opts{}", self.inst_opts_counter);
        self.inst_opts_counter += 1;
        let mut item = vec![
            format!("let mut {opts_var} = InstanceOptions::default();"),
            format!("{opts_var}.translation = {};", point_str(t)),
            format!("{opts_var}.matrix3x3 = Some({});", matrix3x3_str(&m9)),
        ];
        item.extend(self.instance_opts_lines(inst, def_name, &opts_var));
        item.push(format!(
            "{target_var}.add_instance({child_ref}, &{opts_var})?;"
        ));
        item
    }

    fn get_or_build_def(
        &mut self,
        def_id: EntityId,
        visiting: &mut BTreeSet<EntityId>,
        depth: usize,
    ) -> Option<String> {
        if let Some(existing) = self.def_var.get(&def_id) {
            return Some(existing.clone());
        }
        // `visiting` only grows (it is the cycle guard), so it cannot measure nesting depth:
        // `depth` counts the definitions on the current path, as `edit.rs` does.
        if depth >= crate::scene_build::MAX_COMPONENT_DEPTH {
            self.too_deep.get_or_insert(def_id);
            return None;
        }
        if !visiting.insert(def_id) {
            return None;
        }
        let model = self.model;
        let defn = model.definitions.get(&def_id)?;
        if defn.faces.is_empty() && defn.instances.is_empty() {
            return None;
        }

        for inst in &defn.instances {
            if let Some(child) = inst.ref_idx {
                self.get_or_build_def(child, visiting, depth + 1);
            }
        }

        let var_name = format!("def{}", self.def_counter);
        self.def_counter += 1;
        // defn.name unconditionally, not a fabricated fallback for an empty one - an explicit
        // empty string is a real, valid definition name, and this same value also feeds
        // instance_opts_lines's comparison, which needs the TRUE definition name to decide
        // whether an instance's own name differs from it. var_name (the emitted identifier, e.g.
        // "def0") is unrelated and always safe.
        let def_name = defn.name.as_str();
        self.def_var.insert(def_id, var_name.clone());

        self.push("");
        self.push(format!(
            "    // {} - {} faces, {} nested instances",
            def_name.replace(['\r', '\n'], " "),
            defn.faces.len(),
            defn.instances.len()
        ));
        self.push(format!(
            "    let mut {var_name} = builder.add_component_definition({}, &DefinitionOptions::default())?;",
            rust_string(def_name)
        ));
        let mut items = self.face_items(defn, &var_name);
        for inst in &defn.instances {
            let Some(child) = inst.ref_idx else {
                continue;
            };
            let Some(child_var) = self.def_var.get(&child).cloned() else {
                continue;
            };
            // A definition placing itself (never seen in a real file) still compiles - and then
            // fails at run time, like the C++ output - through its own handle.
            let child_ref = if child_var == var_name {
                format!("{var_name}.handle()")
            } else {
                child_var
            };
            // Compared against the ENCLOSING definition's name, not the placed one's - as in the
            // C++, TypeScript and Python ports (root placements use the placed definition's).
            items.push(self.instance_item(inst, def_name, &var_name, &child_ref));
        }
        self.flush_items(items);
        self.push(format!("    let {var_name} = {var_name}.close()?;"));
        Some(var_name)
    }

    fn emit_materials(&mut self) {
        let model = self.model;
        self.push(format!(
            "    // --- Materials ({}) ---",
            model.materials.len()
        ));
        for (i, mat) in model.materials.iter().enumerate() {
            let var_name = format!("mat{i}");
            self.mat_var.insert(mat.name.clone(), var_name.clone());
            let texture = mat.texture.as_ref().filter(|_| has_texture_data(mat));
            if let Some(texture) = texture {
                self.textured_mats.insert(mat.name.clone());
                let b64 = to_base64(texture.data.as_deref().unwrap_or_default());
                let suffix = texture_suffix(&texture.filename);
                // The real applied size: the generated add_face pins are in tiles and the writer
                // scales them by the material's size, so the material keeps its size and the
                // faces their mapping. The block scopes the temp file staging.
                self.push(format!("    let {var_name} = {{"));
                self.push(format!("        let b64_{i} = concat!("));
                for l in string_literal_lines(&b64) {
                    self.push(format!("            {l},"));
                }
                self.push("        );");
                self.push(format!(
                    "        let tex_bytes_{i} = openskp_codegen_base64_decode(b64_{i});"
                ));
                self.push(format!(
                    "        let tex_path_{i} = openskp_codegen_write_temp_file(&tex_bytes_{i}, {});",
                    rust_string(suffix)
                ));
                let app_h = if texture.height > 1e-9 {
                    texture.height
                } else {
                    1.0
                };
                let app_w = if texture.width > 1e-9 {
                    texture.width
                } else {
                    1.0
                };
                let opacity = if mat.transparency < 1.0 - 1e-6 {
                    format!("Some({})", num(mat.transparency))
                } else {
                    "None".to_owned()
                };
                self.push(format!(
                    "        let result = builder.add_texture_material({}, &tex_path_{i}, Some({}), Some({}), {opacity});",
                    rust_string(&mat.name),
                    num(app_h),
                    num(app_w)
                ));
                self.push(format!(
                    "        let _ = std::fs::remove_file(&tex_path_{i});"
                ));
                self.push("        result?");
                self.push("    };");
            } else {
                let c = mat.color;
                self.push(format!(
                    "    let {var_name} = builder.add_material({}, [{}, {}, {}, {}], None)?;",
                    rust_string(&mat.name),
                    c[0],
                    c[1],
                    c[2],
                    c[3]
                ));
            }
        }
    }

    fn emit_layers(&mut self) {
        let model = self.model;
        self.push("");
        self.push(format!("    // --- Layers ({}) ---", model.layers.len()));
        for (i, layer) in model.layers.iter().enumerate() {
            let opts_var = format!("layer_opts{i}");
            self.push(format!("    let mut {opts_var} = LayerOptions::default();"));
            self.push(format!(
                "    {opts_var}.color = Some([{}, {}, {}, 255]);",
                layer.color[0], layer.color[1], layer.color[2]
            ));
            self.push(format!("    {opts_var}.hidden = {};", layer.hidden));
            self.push(format!(
                "    let layer{i} = builder.add_layer({}, &{opts_var})?;",
                rust_string(&layer.name)
            ));
        }
    }

    fn emit_root(&mut self) {
        let model = self.model;
        let root = model.root();
        self.push("");
        self.push(format!(
            "    // --- Root instances ({}) ---",
            root.instances.len()
        ));
        let mut items = Vec::new();
        for inst in &root.instances {
            let Some(child) = inst.ref_idx else {
                continue;
            };
            let Some(child_var) = self.def_var.get(&child).cloned() else {
                continue;
            };
            let child_def_name = model
                .definitions
                .get(&child)
                .map_or("", |d| d.name.as_str());
            items.push(self.instance_item(inst, child_def_name, "builder", &child_var));
        }
        items.extend(self.face_items(root, "builder"));
        self.flush_items(items);
    }

    fn emit_helpers(&mut self) {
        const HELPERS: &str = r#"
fn openskp_codegen_base64_decode(s: &str) -> Vec<u8> {
    const CHARS: &[u8] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut out = Vec::with_capacity(s.len() / 4 * 3);
    let (mut val, mut bits) = (0u32, -8i32);
    for c in s.bytes() {
        if c == b'=' {
            break;
        }
        let Some(pos) = CHARS.iter().position(|&x| x == c) else {
            continue;
        };
        val = (val << 6) | pos as u32;
        bits += 6;
        if bits >= 0 {
            out.push((val >> bits) as u8);
            bits -= 8;
        }
    }
    out
}

fn openskp_codegen_write_temp_file(bytes: &[u8], suffix: &str) -> std::path::PathBuf {
    use std::sync::atomic::{AtomicU64, Ordering};
    static COUNTER: AtomicU64 = AtomicU64::new(0);
    let n = COUNTER.fetch_add(1, Ordering::Relaxed);
    let name = format!("openskp_codegen_{}_{}{}", std::process::id(), n, suffix);
    let path = std::env::temp_dir().join(name);
    // A failed write surfaces as add_texture_material's own "cannot open" error.
    let _ = std::fs::write(&path, bytes);
    path
}"#;
        for line in HELPERS.lines() {
            self.push(line);
        }
    }
}

/// Generate Rust source that, when its `build()` function is called, rebuilds `model` from
/// scratch via [`create`](mod@crate::create) and returns the new file's bytes. See the module docs
/// for exactly what is and isn't reproduced.
pub fn to_rust_code(model: &SkpModel) -> String {
    let mut g = Gen {
        model,
        lines: Vec::new(),
        mat_var: BTreeMap::new(),
        textured_mats: BTreeSet::new(),
        def_var: BTreeMap::new(),
        faces_skipped_degenerate: 0,
        too_deep: None,
        face_opts_counter: 0,
        def_counter: 0,
        inst_opts_counter: 0,
        chunked: item_count(model) > CHUNK_ITEMS,
    };

    g.push("use openskp::create::*;");
    g.push("");
    g.push("#[allow(unused_variables)]");
    g.push("pub fn build() -> Result<Vec<u8>, SkpWriteError> {");
    g.push("    let mut builder = create();");
    g.push("");
    g.emit_materials();
    g.emit_layers();
    for &def_id in model.definitions.keys() {
        let mut visiting = BTreeSet::new();
        g.get_or_build_def(def_id, &mut visiting, 0);
    }
    if let Some(def_id) = g.too_deep {
        // Same message as the writer's `definition_order`, which would reject this model.
        let msg = format!(
            "component-definition nesting depth exceeds maximum allowed limit at definition \
             {def_id}"
        );
        let at = g
            .lines
            .iter()
            .position(|l| l.starts_with("pub fn build()"))
            .expect("build() header emitted above");
        g.lines.insert(at, "#[allow(unreachable_code)]".to_owned());
        g.lines.insert(
            at + 2,
            format!("    return Err(SkpWriteError::new({}));", rust_string(&msg)),
        );
    }
    g.emit_root();
    g.push("");
    g.push("    builder.to_bytes()");
    g.push("}");
    if !g.textured_mats.is_empty() {
        g.emit_helpers();
    }

    if g.faces_skipped_degenerate > 0 {
        g.lines.insert(
            0,
            format!(
                "// {} degenerate face(s) (fewer than 3 resolvable vertices) were skipped during \
                 generation.",
                g.faces_skipped_degenerate
            ),
        );
    }

    let mut result = String::new();
    for l in &g.lines {
        result.push_str(l);
        result.push('\n');
    }
    result
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn base64_matches_the_standard_alphabet_and_padding() {
        assert_eq!(to_base64(b""), "");
        assert_eq!(to_base64(b"f"), "Zg==");
        assert_eq!(to_base64(b"fo"), "Zm8=");
        assert_eq!(to_base64(b"foo"), "Zm9v");
        assert_eq!(to_base64(b"foobar"), "Zm9vYmFy");
        assert_eq!(to_base64(&[0xff, 0xfe, 0x00]), "//4A");
    }

    #[test]
    fn numbers_are_rounded_float_literals() {
        assert_eq!(num(12.0), "12.0");
        assert_eq!(num(0.75), "0.75");
        assert_eq!(num(-0.00001), "0.0");
        assert_eq!(num(1234.56789), "1234.5679");
        assert_eq!(num(-3.0), "-3.0");
        assert_eq!(num(f64::NAN), "f64::NAN");
        assert_eq!(point_str([20.0, 20.0, 0.0]), "[20.0, 20.0, 0.0]");
    }

    #[test]
    fn strings_are_escaped_rust_literals() {
        assert_eq!(rust_string("a\"b\\c\nd"), r#""a\"b\\c\nd""#);
        assert_eq!(rust_string(""), r#""""#);
    }

    #[test]
    fn long_literals_are_chunked() {
        let s = "A".repeat(40_000);
        let lines = string_literal_lines(&s);
        assert_eq!(lines.len(), 3);
        assert!(lines.iter().all(|l| l.len() <= 16_002));
    }
}
