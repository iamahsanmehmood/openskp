//! ThatOpen Fragments (`.frag`) export and import (`fragments_export.hpp`).
//!
//! [`to_fragments`] writes an [`InstancedScene`] against ThatOpen's real
//! Fragments FlatBuffers schema (see [`schema`]); [`from_fragments`] reads
//! a `.frag` buffer back into an [`InstancedScene`]. Both are direct ports
//! of the C++ `fragments_export.cpp` (itself a port of Python's
//! `openskp.export.fragments`), with the flatbuffers library replaced by
//! the crate's own [`crate::flatbuffers`] and miniz by
//! [`crate::deflate`]/[`crate::inflate`]. The raw (uncompressed) output
//! follows the C++ builder's algorithm and call order, so it should match
//! the C++ port's bytes; the zlib stream differs (different compressor),
//! but inflates to the same bytes.

mod json;
pub mod schema;

use std::collections::{BTreeMap, BTreeSet, HashMap, HashSet};
use std::fmt;
use std::io;
use std::path::{Path, PathBuf};

use crate::flatbuffers::{Builder, InvalidFlatbuffer, Offset, ReadResult};
use crate::instanced_scene::{
    InstancedMeshResource, InstancedNode, InstancedScene, LocalPrimitive, IDENTITY_GLTF,
};
use crate::model::EntityId;
use crate::scene::GltfMaterial;
use schema::{
    BoundingBox, DoubleVector, FloatVector, Material, MeshesArgs, ModelArgs, RenderedFaces,
    Representation, RepresentationClass, Sample, ShellArgs, ShellType, Stroke, Transform,
};

/// Fragments' `Shell` uses `ushort` point indices (and `ushort` face ids)
/// by default; past this many points a shell switches to the BigShell
/// encoding, and past this many triangles it is split.
const USHORT_MAX: usize = 65535;

/// Decimal places scale factors are rounded to for the shell cache key.
const SCALE_ROUND_NDIGITS: i32 = 6;

/// miniz's `MZ_DEFAULT_LEVEL`.
const ZLIB_LEVEL: u32 = 6;

/// The model-level GUID every export carries.
const MODEL_GUID: &str = "00000000-0000-0000-0000-000000000000";

/// Spatial structures nested deeper than this are rejected on read (the
/// C++ recurses without a limit; a hostile buffer could exhaust the stack).
const MAX_SPATIAL_DEPTH: usize = 256;

/// Maximum total spatial structure nodes read. Every node must also be its
/// own table (see `FragReader::build_node`), so a buffer cannot alias one
/// subtree from many parents to expand exponentially.
const MAX_SPATIAL_NODES: usize = 10_000_000;

/// Children slots reserved up front, whatever length the buffer claims: a
/// node is far larger than the 4-byte offset that names it, so trusting the
/// length would let a small buffer request a huge allocation. (No test can
/// see the difference: 64-bit allocators commit such a reservation lazily.)
const MAX_CHILDREN_RESERVE: usize = 1024;

/// Maximum decompressed `.frag` payload size (2 GiB).
const MAX_FRAGMENTS_DECOMPRESSED_BYTES: usize = 2 * 1024 * 1024 * 1024;

type Mat4 = [f64; 16];

/// Column-major 4x4 multiply, `a * b`.
fn mat4_mul(a: &Mat4, b: &Mat4) -> Mat4 {
    let mut out = [0.0; 16];
    for col in 0..4 {
        for row in 0..4 {
            let mut s = 0.0;
            for k in 0..4 {
                s += a[k * 4 + row] * b[col * 4 + k];
            }
            out[col * 4 + row] = s;
        }
    }
    out
}

struct Leaf<'a> {
    node: &'a InstancedNode,
    world: Mat4,
}

/// Every node worth tracking as its own item, with its world transform:
/// one that carries a mesh, or a real (not generated) named wrapper other
/// than the root (e.g. a group "W-2" holding separately-meshed parts).
/// Pre-order, like the C++ `collect_leaves`.
fn collect_leaves<'a>(
    node: &'a InstancedNode,
    is_root: bool,
    parent: &Mat4,
    out: &mut Vec<Leaf<'a>>,
) {
    let world = mat4_mul(parent, &node.matrix);
    let is_named_wrapper = !is_root && !node.name_is_generated;
    if node.mesh_resource_id.is_some() || is_named_wrapper {
        out.push(Leaf { node, world });
    }
    for child in &node.children {
        collect_leaves(child, false, &world, out);
    }
}

#[derive(Debug, Clone, Copy)]
struct Trs {
    position: [f64; 3],
    x_dir: [f64; 3],
    y_dir: [f64; 3],
    scale: [f64; 3],
    mirrored: bool,
}

/// Splits a column-major transform into a rigid frame (position, X and Y
/// unit directions) plus per-axis scale and a mirror flag. A mirrored
/// transform flips the X direction so the frame stays right-handed; the
/// flip is baked into the geometry instead (see [`bake_primitive`]).
fn decompose_trs(m: &Mat4) -> Trs {
    let x_axis = [m[0], m[1], m[2]];
    let y_axis = [m[4], m[5], m[6]];
    let z_axis = [m[8], m[9], m[10]];
    let pos = [m[12], m[13], m[14]];

    let norm = |v: &[f64; 3]| {
        let s = (v[0] * v[0] + v[1] * v[1] + v[2] * v[2]).sqrt();
        if s > 0.0 {
            s
        } else {
            1.0
        }
    };
    let (sx, sy, sz) = (norm(&x_axis), norm(&y_axis), norm(&z_axis));

    let det = x_axis[0] * (y_axis[1] * z_axis[2] - y_axis[2] * z_axis[1])
        - x_axis[1] * (y_axis[0] * z_axis[2] - y_axis[2] * z_axis[0])
        + x_axis[2] * (y_axis[0] * z_axis[1] - y_axis[1] * z_axis[0]);
    let mirrored = det < 0.0;

    let mut x_dir = [x_axis[0] / sx, x_axis[1] / sx, x_axis[2] / sx];
    if mirrored {
        x_dir = [-x_dir[0], -x_dir[1], -x_dir[2]];
    }
    let y_dir = [y_axis[0] / sy, y_axis[1] / sy, y_axis[2] / sy];

    Trs {
        position: pos,
        x_dir,
        y_dir,
        scale: [sx, sy, sz],
        mirrored,
    }
}

struct BakedGeometry {
    points: Vec<[f32; 3]>,
    triangles: Vec<[u32; 3]>,
}

/// A copy of `prim`'s local points with the instance's scale (and mirror,
/// as a negated X plus reversed winding) applied, so a rigid transform
/// places it correctly.
fn bake_primitive(prim: &LocalPrimitive, scale: [f64; 3], mirrored: bool) -> BakedGeometry {
    let sx = if mirrored { -scale[0] } else { scale[0] };
    let (sy, sz) = (scale[1], scale[2]);

    let points = prim
        .positions
        .as_chunks::<3>()
        .0
        .iter()
        .map(|p| {
            [
                (f64::from(p[0]) * sx) as f32,
                (f64::from(p[1]) * sy) as f32,
                (f64::from(p[2]) * sz) as f32,
            ]
        })
        .collect();
    let triangles = prim
        .indices
        .as_chunks::<3>()
        .0
        .iter()
        .map(|t| if mirrored { [t[0], t[2], t[1]] } else { *t })
        .collect();
    BakedGeometry { points, triangles }
}

/// A (mirrored, scale) pair rounded to a stable cache key: the mirror flag
/// folds into the X component's sign. Keyed by bit pattern, with `-0.0`
/// folded into `0.0` the way `std::map`'s `<` treats them as equal.
fn scale_cache_key(mirrored: bool, scale: [f64; 3]) -> [u64; 3] {
    let mult = 10f64.powi(SCALE_ROUND_NDIGITS);
    let rnd = |v: f64| ((v * mult).round() / mult + 0.0).to_bits();
    [
        rnd(if mirrored { -scale[0] } else { scale[0] }),
        rnd(scale[1]),
        rnd(scale[2]),
    ]
}

/// `std::lround(v * 255)` narrowed to a byte (wrapping, like the C++
/// `static_cast<std::uint8_t>`).
fn color_byte(v: f64) -> u8 {
    (v * 255.0).round() as i64 as u8
}

/// The lazily built geometry tables of one export.
struct GeometryWriter<'a> {
    scene: &'a InstancedScene,
    fbb: Builder,
    /// (resource id, primitive index, scale key) -> shell indices.
    shell_key_to_index: HashMap<(&'a str, usize, [u64; 3]), Vec<usize>>,
    shell_offsets: Vec<Offset>,
    representation_bounds: Vec<([f32; 3], [f32; 3])>,
    material_key_to_index: HashMap<usize, usize>,
    material_rgba: Vec<[u8; 4]>,
}

impl<'a> GeometryWriter<'a> {
    fn material_index(&mut self, material_index: usize) -> usize {
        if let Some(&idx) = self.material_key_to_index.get(&material_index) {
            return idx;
        }
        let base = self
            .scene
            .gltf_materials
            .get(material_index)
            .map_or([1.0; 4], |gm| gm.pbr_metallic_roughness.base_color_factor);
        let idx = self.material_rgba.len();
        self.material_rgba.push([
            color_byte(base[0]),
            color_byte(base[1]),
            color_byte(base[2]),
            color_byte(base[3]),
        ]);
        self.material_key_to_index.insert(material_index, idx);
        idx
    }

    fn bake_one_shell(&mut self, points: &[[f32; 3]], triangles: &[[u32; 3]]) -> usize {
        let fbb = &mut self.fbb;
        let is_big = points.len() > USHORT_MAX;

        let mut profile_offsets = Vec::new();
        let mut big_profile_offsets = Vec::new();
        for tri in triangles {
            if is_big {
                big_profile_offsets.push(schema::create_big_shell_profile_direct(fbb, tri));
            } else {
                let idx = [tri[0] as u16, tri[1] as u16, tri[2] as u16];
                profile_offsets.push(schema::create_shell_profile_direct(fbb, &idx));
            }
        }

        let points_struct: Vec<FloatVector> = points
            .iter()
            .map(|p| FloatVector::new(p[0], p[1], p[2]))
            .collect();
        // Sequential per-shell face ids; the caller keeps every shell
        // within the ushort limit.
        let face_ids: Vec<u16> = (0..triangles.len()).map(|i| i as u16).collect();

        let shell = schema::create_shell_direct(
            fbb,
            &ShellArgs {
                profiles: &profile_offsets,
                holes: &[],
                points: &points_struct,
                big_profiles: &big_profile_offsets,
                big_holes: &[],
                shell_type: if is_big {
                    ShellType::BIG
                } else {
                    ShellType::NONE
                },
                profiles_face_ids: &face_ids,
            },
        );
        let index = self.shell_offsets.len();
        self.shell_offsets.push(shell);

        let mut lo = [f32::INFINITY; 3];
        let mut hi = [f32::NEG_INFINITY; 3];
        for p in points {
            for k in 0..3 {
                if p[k] < lo[k] {
                    lo[k] = p[k];
                }
                if p[k] > hi[k] {
                    hi[k] = p[k];
                }
            }
        }
        self.representation_bounds.push((lo, hi));
        index
    }

    /// The shell(s) for one primitive at one scale/mirror state, baked on
    /// first use. More than one only when the primitive has more triangles
    /// than a shell's `ushort` face ids can number: it is then split into
    /// chunks, each carrying the full point array.
    fn shells_for(
        &mut self,
        resource_id: &'a str,
        prim_idx: usize,
        prim: &LocalPrimitive,
        scale: [f64; 3],
        mirrored: bool,
    ) -> Vec<usize> {
        let key = (resource_id, prim_idx, scale_cache_key(mirrored, scale));
        if let Some(found) = self.shell_key_to_index.get(&key) {
            return found.clone();
        }
        let baked = bake_primitive(prim, scale, mirrored);
        let indices: Vec<usize> = if baked.triangles.len() > USHORT_MAX {
            baked
                .triangles
                .chunks(USHORT_MAX)
                .map(|chunk| self.bake_one_shell(&baked.points, chunk))
                .collect()
        } else {
            vec![self.bake_one_shell(&baked.points, &baked.triangles)]
        };
        self.shell_key_to_index.insert(key, indices.clone());
        indices
    }
}

/// One `SpatialStructure` per node of the ORIGINAL tree (not just the
/// tracked items), children first.
fn build_spatial_node(
    fbb: &mut Builder,
    node: &InstancedNode,
    item_index_by_node: &HashMap<*const InstancedNode, u32>,
) -> Offset {
    let child_offsets: Vec<Offset> = node
        .children
        .iter()
        .map(|child| build_spatial_node(fbb, child, item_index_by_node))
        .collect();
    let children = fbb.create_vector_of_offsets(&child_offsets);
    let category = if node.layer.is_empty() {
        Offset::NULL
    } else {
        fbb.create_string(&node.layer)
    };
    let local_id = item_index_by_node
        .get(&(node as *const InstancedNode))
        .copied();
    schema::create_spatial_structure(fbb, local_id, category, children)
}

/// Builds a `.frag` (FlatBuffers) buffer from an instanced scene.
///
/// `raw = false` (ThatOpen's own IfcImporter convention) zlib-compresses
/// the output (RFC 1950); the real Fragments loader auto-detects either
/// form. `raw = true` returns the uncompressed FlatBuffers bytes.
pub fn to_fragments(scene: &InstancedScene, raw: bool) -> Vec<u8> {
    let mut leaves = Vec::new();
    collect_leaves(&scene.scene_hierarchy, true, &IDENTITY_GLTF, &mut leaves);

    let mut resource_by_id: HashMap<&str, &InstancedMeshResource> = HashMap::new();
    for r in &scene.mesh_resources {
        resource_by_id.insert(&r.id, r);
    }

    let mut w = GeometryWriter {
        scene,
        fbb: Builder::with_capacity(64 * 1024),
        shell_key_to_index: HashMap::new(),
        shell_offsets: Vec::new(),
        representation_bounds: Vec::new(),
        material_key_to_index: HashMap::new(),
        material_rgba: Vec::new(),
    };

    // ---- Model-level items + geometry samples ----
    let mut local_ids: Vec<u32> = Vec::new();
    let mut categories: Vec<&str> = Vec::new();
    let mut names: Vec<&str> = Vec::new();
    let mut guids: Vec<String> = Vec::new();
    let mut generated_name_guids: Vec<String> = Vec::new();
    let mut sample_material: Vec<usize> = Vec::new();
    let mut sample_representation: Vec<usize> = Vec::new();
    let mut meshes_items: Vec<u32> = Vec::new();
    let mut global_transform_data: Vec<Trs> = Vec::new();
    let mut item_index_by_node: HashMap<*const InstancedNode, u32> = HashMap::new();
    // A real file can carry the same per-instance GUID on several copies
    // (SketchUp duplicates attribute dictionaries verbatim): the first
    // claimant keeps it, later ones fall back to a synthetic GUID.
    let mut seen_guids: HashSet<String> = HashSet::new();

    for (item_index, leaf) in leaves.iter().enumerate() {
        let node = leaf.node;
        let res = match &node.mesh_resource_id {
            Some(id) => match resource_by_id.get(id.as_str()) {
                Some(r) => Some((id.as_str(), *r)),
                // The leaf names a resource that never baked.
                None => continue,
            },
            None => None,
        };
        let item_index_u32 = item_index as u32;

        local_ids.push(item_index_u32);
        categories.push(if node.layer.is_empty() {
            "Layer0"
        } else {
            node.layer.as_str()
        });
        names.push(node.name.as_str());

        let item_guid = if !node.guid.is_empty() && !seen_guids.contains(&node.guid) {
            node.guid.clone()
        } else {
            format!("openskp-{item_index}")
        };
        seen_guids.insert(item_guid.clone());
        if node.name_is_generated {
            generated_name_guids.push(item_guid.clone());
        }
        guids.push(item_guid);
        item_index_by_node.insert(node as *const InstancedNode, item_index_u32);

        if let Some((resource_id, res)) = res {
            let trs = decompose_trs(&leaf.world);
            for (prim_idx, prim) in res.primitives.iter().enumerate() {
                let material_index = w.material_index(prim.material_index);
                // Normally one shell; more only for an oversized primitive,
                // each extra shell becoming its own sample of the same item.
                for shell_index in
                    w.shells_for(resource_id, prim_idx, prim, trs.scale, trs.mirrored)
                {
                    sample_material.push(material_index);
                    sample_representation.push(shell_index);
                    meshes_items.push(item_index_u32);
                    global_transform_data.push(trs);
                }
            }
        }
    }

    let GeometryWriter {
        fbb: mut builder,
        shell_offsets,
        representation_bounds,
        material_rgba,
        ..
    } = w;
    let fbb = &mut builder;

    let shells_vec = fbb.create_vector_of_offsets(&shell_offsets);

    let material_structs: Vec<Material> = material_rgba
        .iter()
        .map(|c| Material {
            r: c[0],
            g: c[1],
            b: c[2],
            a: c[3],
            rendered_faces: RenderedFaces::ONE,
            stroke: Stroke::DEFAULT,
        })
        .collect();
    let materials_vec = fbb.create_vector_of_structs(&material_structs);

    let representation_structs: Vec<Representation> = representation_bounds
        .iter()
        .enumerate()
        .map(|(i, (lo, hi))| Representation {
            id: i as u32,
            bbox: BoundingBox {
                min: FloatVector::new(lo[0], lo[1], lo[2]),
                max: FloatVector::new(hi[0], hi[1], hi[2]),
            },
            representation_class: RepresentationClass::SHELL,
        })
        .collect();
    let representations_vec = fbb.create_vector_of_structs(&representation_structs);

    let sample_structs: Vec<Sample> = (0..sample_material.len())
        .map(|i| Sample {
            item: i as u32,
            material: sample_material[i] as u32,
            representation: sample_representation[i] as u32,
            local_transform: 0,
        })
        .collect();
    let samples_vec = fbb.create_vector_of_structs(&sample_structs);

    let meshes_items_vec = fbb.create_vector(&meshes_items);

    let global_transform_structs: Vec<Transform> = global_transform_data
        .iter()
        .map(|trs| Transform {
            position: DoubleVector::new(trs.position[0], trs.position[1], trs.position[2]),
            x_direction: FloatVector::new(
                trs.x_dir[0] as f32,
                trs.x_dir[1] as f32,
                trs.x_dir[2] as f32,
            ),
            y_direction: FloatVector::new(
                trs.y_dir[0] as f32,
                trs.y_dir[1] as f32,
                trs.y_dir[2] as f32,
            ),
        })
        .collect();
    let global_transforms_vec = fbb.create_vector_of_structs(&global_transform_structs);

    // One shared identity local transform: every primitive's points are
    // already in their resource's local space, scale/mirror baked in.
    let local_transforms_vec = fbb.create_vector_of_structs(&[Transform::IDENTITY]);
    let circle_extrusions_vec = fbb.create_vector_of_offsets(&[]);

    let meshes = schema::create_meshes(
        fbb,
        &MeshesArgs {
            coordinates: Some(Transform::IDENTITY),
            meshes_items: meshes_items_vec,
            samples: samples_vec,
            representations: representations_vec,
            materials: materials_vec,
            circle_extrusions: circle_extrusions_vec,
            shells: shells_vec,
            local_transforms: local_transforms_vec,
            global_transforms: global_transforms_vec,
            ..MeshesArgs::default()
        },
    );

    let categories_vec = fbb.create_vector_of_strings(&categories);
    let local_ids_vec = fbb.create_vector(&local_ids);
    let guid_str = fbb.create_string(MODEL_GUID);
    let guids_vec = fbb.create_vector_of_strings(&guids);
    let guids_items_vec = fbb.create_vector(&local_ids);

    // One Attribute per tracked item, carrying its display name as a
    // `["Name", value, "STRING"]` JSON triple (the IfcImporter convention).
    let attribute_offsets: Vec<Offset> = names
        .iter()
        .map(|name| {
            let data = if name.is_empty() {
                Vec::new()
            } else {
                vec![fbb.create_string(&json::name_attribute(name))]
            };
            schema::create_attribute_direct(fbb, &data)
        })
        .collect();
    let attributes_vec = fbb.create_vector_of_offsets(&attribute_offsets);

    // Per-layer visibility has no field in the schema; `metadata` is its
    // general-purpose JSON sidecar.
    let metadata = fbb.create_string(&json::metadata(&scene.layer_hidden, &generated_name_guids));

    let root_spatial = build_spatial_node(fbb, &scene.scene_hierarchy, &item_index_by_node);

    let model = schema::create_model(
        fbb,
        &ModelArgs {
            metadata,
            guids: guids_vec,
            guids_items: guids_items_vec,
            max_local_id: local_ids.len() as u32,
            local_ids: local_ids_vec,
            categories: categories_vec,
            meshes,
            attributes: attributes_vec,
            guid: guid_str,
            spatial_structure: root_spatial,
            ..ModelArgs::default()
        },
    );
    schema::finish_model_buffer(fbb, model);

    let data = builder.into_finished_data();
    if raw {
        data
    } else {
        crate::deflate::zlib_compress(&data, ZLIB_LEVEL)
    }
}

/// Exports an instanced scene to a `.frag` file, creating its parent
/// directories.
pub fn export_fragments(
    scene: &InstancedScene,
    output_path: impl AsRef<Path>,
    raw: bool,
) -> io::Result<()> {
    let output_path = output_path.as_ref();
    if let Some(parent) = output_path.parent() {
        if !parent.as_os_str().is_empty() {
            std::fs::create_dir_all(parent)?;
        }
    }
    std::fs::write(output_path, to_fragments(scene, raw))
}

// ---------------------------------------------------------------------------
// Read side
// ---------------------------------------------------------------------------

/// Why a `.frag` file or buffer could not be read.
#[derive(Debug)]
pub enum FragmentsError {
    /// The file could not be read.
    Io { path: PathBuf, source: io::Error },
    /// The bytes are not a well-formed Fragments FlatBuffer (after zlib
    /// inflation, when they were compressed).
    InvalidBuffer(InvalidFlatbuffer),
}

impl fmt::Display for FragmentsError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            FragmentsError::Io { path, source } => {
                write!(
                    f,
                    "Fragments import: could not open {}: {source}",
                    path.display()
                )
            }
            FragmentsError::InvalidBuffer(e) => write!(f, "Fragments import: {e}"),
        }
    }
}

impl std::error::Error for FragmentsError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            FragmentsError::Io { source, .. } => Some(source),
            FragmentsError::InvalidBuffer(e) => Some(e),
        }
    }
}

impl From<InvalidFlatbuffer> for FragmentsError {
    fn from(e: InvalidFlatbuffer) -> Self {
        FragmentsError::InvalidBuffer(e)
    }
}

fn cross3(a: [f64; 3], b: [f64; 3]) -> [f64; 3] {
    [
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    ]
}

/// One flat-shaded normal per vertex: each triangle's face normal, written
/// to its 3 vertices (later triangles win) - all a Shell can give back.
fn compute_flat_normals(points: &[[f64; 3]], triangles: &[[u32; 3]]) -> Vec<[f64; 3]> {
    let mut normals = vec![[0.0, 0.0, 1.0]; points.len()];
    for tri in triangles {
        let (pa, pb, pc) = (
            points[tri[0] as usize],
            points[tri[1] as usize],
            points[tri[2] as usize],
        );
        let u = [pb[0] - pa[0], pb[1] - pa[1], pb[2] - pa[2]];
        let v = [pc[0] - pa[0], pc[1] - pa[1], pc[2] - pa[2]];
        let mut n = cross3(u, v);
        let length = (n[0] * n[0] + n[1] * n[1] + n[2] * n[2]).sqrt();
        if length > 1e-12 {
            n = [n[0] / length, n[1] / length, n[2] / length];
        }
        for &i in tri {
            normals[i as usize] = n;
        }
    }
    normals
}

/// A glTF column-major matrix from a Fragments `Transform` (Z = X x Y).
fn transform_to_matrix16(t: &Transform) -> Mat4 {
    let x = [
        f64::from(t.x_direction.x),
        f64::from(t.x_direction.y),
        f64::from(t.x_direction.z),
    ];
    let y = [
        f64::from(t.y_direction.x),
        f64::from(t.y_direction.y),
        f64::from(t.y_direction.z),
    ];
    let z = cross3(x, y);
    let p = t.position;
    [
        x[0], x[1], x[2], 0.0, //
        y[0], y[1], y[2], 0.0, //
        z[0], z[1], z[2], 0.0, //
        p.x, p.y, p.z, 1.0,
    ]
}

/// The `["Name", value, ...]` entry of an item's attribute strings, or "".
fn extract_name_attribute(attribute: Option<schema::Attribute<'_>>) -> ReadResult<String> {
    let Some(data) = attribute.map(|a| a.data()).transpose()?.flatten() else {
        return Ok(String::new());
    };
    for raw in data.iter() {
        if let Ok(json::Value::Array(arr)) = json::parse(raw?.as_bytes()) {
            if let [json::Value::String(key), json::Value::String(value), ..] = arr.as_slice() {
                if key == "Name" {
                    return Ok(value.clone());
                }
            }
        }
    }
    Ok(String::new())
}

type ShellGeometry = (Vec<[f64; 3]>, Vec<[u32; 3]>);

fn decode_shell(shell: schema::Shell<'_>) -> ReadResult<ShellGeometry> {
    let mut points = Vec::new();
    if let Some(ps) = shell.points()? {
        points.reserve(ps.len());
        for p in ps.iter() {
            let p = p?.0;
            points.push([f64::from(p.x), f64::from(p.y), f64::from(p.z)]);
        }
    }
    let mut triangles = Vec::new();
    if shell.shell_type()? == ShellType::BIG {
        if let Some(profiles) = shell.big_profiles()? {
            for profile in profiles.iter() {
                if let Some(idx) = profile?.indices()? {
                    if idx.len() >= 3 {
                        triangles.push([idx.get(0)?, idx.get(1)?, idx.get(2)?]);
                    }
                }
            }
        }
    } else if let Some(profiles) = shell.profiles()? {
        for profile in profiles.iter() {
            if let Some(idx) = profile?.indices()? {
                if idx.len() >= 3 {
                    triangles.push([
                        u32::from(idx.get(0)?),
                        u32::from(idx.get(1)?),
                        u32::from(idx.get(2)?),
                    ]);
                }
            }
        }
    }
    if let Some(bad) = triangles
        .iter()
        .flatten()
        .find(|&&i| i as usize >= points.len())
    {
        return Err(InvalidFlatbuffer::new(format!(
            "shell triangle index {bad} out of range ({} points)",
            points.len()
        )));
    }
    Ok((points, triangles))
}

/// State shared by one [`from_fragments`] call.
struct FragReader<'a> {
    model: schema::Model<'a>,
    meshes: Option<schema::Meshes<'a>>,
    shells: Option<crate::flatbuffers::Vector<'a, schema::Shell<'a>>>,
    shell_cache: Vec<Option<ShellGeometry>>,
    material_count: usize,
    samples_by_item: BTreeMap<u32, Vec<usize>>,
    local_id_to_item_index: HashMap<u32, usize>,
    guid_by_item: HashMap<usize, String>,
    generated_name_guids: BTreeSet<String>,
    mesh_resources: Vec<InstancedMeshResource>,
    resource_by_signature: HashMap<String, String>,
    /// The resource each item resolved to, so an item named by several
    /// spatial nodes is built once.
    resource_by_item: HashMap<usize, String>,
    warned_unsupported: bool,
    visited_nodes: usize,
    /// Buffer positions of the spatial nodes read so far.
    visited_tables: HashSet<usize>,
}

impl<'a> FragReader<'a> {
    fn shell_geometry(&mut self, shell_idx: usize) -> ReadResult<&ShellGeometry> {
        if self.shell_cache[shell_idx].is_none() {
            let shells = self
                .shells
                .as_ref()
                .expect("shell_idx < n_shells implies shells");
            self.shell_cache[shell_idx] = Some(decode_shell(shells.get(shell_idx)?)?);
        }
        Ok(self.shell_cache[shell_idx].as_ref().expect("just decoded"))
    }

    /// The mesh resource for one item's samples; items whose samples
    /// reference the same (representation, material) pairs share one.
    fn build_resource_for_item(&mut self, item_idx: usize, item_name: &str) -> ReadResult<String> {
        if let Some(found) = self.resource_by_item.get(&item_idx) {
            return Ok(found.clone());
        }
        let resource_id = self.build_resource_uncached(item_idx, item_name)?;
        self.resource_by_item.insert(item_idx, resource_id.clone());
        Ok(resource_id)
    }

    fn build_resource_uncached(&mut self, item_idx: usize, item_name: &str) -> ReadResult<String> {
        let sample_indices = self
            .samples_by_item
            .get(&(item_idx as u32))
            .cloned()
            .unwrap_or_default();
        let meshes = self.meshes.expect("samples imply meshes");
        let samples = meshes.samples()?.expect("samples imply a samples vector");
        let representations = meshes.representations()?;
        let mut sig_key = String::new();
        let mut primitives = Vec::new();
        for k in sample_indices {
            let sample = samples.get(k)?.0;
            let (rep_idx, mat_idx) = (sample.representation, sample.material);
            let representation = representations
                .as_ref()
                .filter(|reps| (rep_idx as usize) < reps.len())
                .map(|reps| reps.get(rep_idx as usize))
                .transpose()?
                .map(|r| r.0)
                .ok_or_else(|| {
                    InvalidFlatbuffer::new(format!(
                        "sample {k} references missing representation {rep_idx}"
                    ))
                })?;
            let rep_class = representation.representation_class;
            if rep_class != RepresentationClass::SHELL {
                if !self.warned_unsupported {
                    eprintln!(
                        "openskp from_fragments: skipping a sample with unsupported RepresentationClass={} (only SHELL is read today).",
                        rep_class.0
                    );
                    self.warned_unsupported = true;
                }
                continue;
            }
            sig_key.push_str(&format!("{rep_idx}:{mat_idx},"));
            // Representation::id is the index into Meshes::shells - not the
            // representation's own position.
            let shell_idx = representation.id as usize;
            if self.shells.as_ref().is_none_or(|s| shell_idx >= s.len()) {
                continue;
            }
            let material_index = if (mat_idx as usize) < self.material_count {
                mat_idx as usize
            } else {
                0
            };
            let (points, triangles) = self.shell_geometry(shell_idx)?;
            let normals = compute_flat_normals(points, triangles);
            let prim = LocalPrimitive {
                positions: points.iter().flat_map(|p| p.map(|c| c as f32)).collect(),
                normals: normals.iter().flat_map(|n| n.map(|c| c as f32)).collect(),
                uvs: vec![0.0; points.len() * 2],
                indices: triangles.iter().flatten().copied().collect(),
                material_index,
            };
            primitives.push(prim);
        }

        if !sig_key.is_empty() {
            if let Some(found) = self.resource_by_signature.get(&sig_key) {
                return Ok(found.clone());
            }
        }

        let resource_id = format!("frag-{item_idx}");
        self.mesh_resources.push(InstancedMeshResource {
            id: resource_id.clone(),
            definition_id: Some(item_idx as EntityId),
            definition_name: if item_name.is_empty() {
                resource_id.clone()
            } else {
                item_name.to_string()
            },
            variant_key: "default".to_string(),
            primitives,
        });
        if !sig_key.is_empty() {
            self.resource_by_signature
                .insert(sig_key, resource_id.clone());
        }
        Ok(resource_id)
    }

    fn build_node(
        &mut self,
        spatial: schema::SpatialStructure<'a>,
        depth: usize,
    ) -> ReadResult<InstancedNode> {
        if depth > MAX_SPATIAL_DEPTH {
            return Err(InvalidFlatbuffer::new(format!(
                "spatial structure nested deeper than {MAX_SPATIAL_DEPTH} levels"
            )));
        }
        self.visited_nodes += 1;
        if self.visited_nodes > MAX_SPATIAL_NODES {
            return Err(InvalidFlatbuffer::new(format!(
                "spatial structure exceeds maximum of {MAX_SPATIAL_NODES} nodes"
            )));
        }
        // A spatial structure is a tree. Offsets come from the buffer, so a
        // hostile one can point many parents at one subtree (a DAG that
        // expands exponentially within the depth limit); every writer gives
        // each node its own table, so a second visit is rejected.
        if !self.visited_tables.insert(spatial.0.pos()) {
            return Err(InvalidFlatbuffer::new(format!(
                "spatial structure node at {} has more than one parent",
                spatial.0.pos()
            )));
        }
        let category = spatial
            .category()?
            .map(|s| s.to_string_lossy())
            .unwrap_or_default();
        let mut node = InstancedNode {
            definition_name: category.clone(),
            layer: category.clone(),
            ..InstancedNode::default()
        };

        if let Some(local_id) = spatial.local_id()? {
            if let Some(&item_idx) = self.local_id_to_item_index.get(&local_id) {
                let attribute = match self.model.attributes()? {
                    Some(attrs) if item_idx < attrs.len() => Some(attrs.get(item_idx)?),
                    _ => None,
                };
                node.name = extract_name_attribute(attribute)?;
                node.guid = self
                    .guid_by_item
                    .get(&item_idx)
                    .cloned()
                    .unwrap_or_default();
                node.name_is_generated =
                    !node.guid.is_empty() && self.generated_name_guids.contains(&node.guid);
                if let Some(first_sample) =
                    self.samples_by_item.get(&(item_idx as u32)).map(|s| s[0])
                {
                    let item_name = if node.name.is_empty() {
                        category.clone()
                    } else {
                        node.name.clone()
                    };
                    node.mesh_resource_id =
                        Some(self.build_resource_for_item(item_idx, &item_name)?);
                    let meshes = self.meshes.expect("samples imply meshes");
                    let samples = meshes.samples()?.expect("samples imply a samples vector");
                    let transforms = meshes.global_transforms()?.ok_or_else(|| {
                        InvalidFlatbuffer::new("Meshes.global_transforms is missing")
                    })?;
                    // Indexed like meshes_items (see read_model).
                    let mesh_item = samples.get(first_sample)?.0.item as usize;
                    node.matrix = transform_to_matrix16(&transforms.get(mesh_item)?.0);
                }
            }
        }

        if let Some(children) = spatial.children()? {
            node.children
                .reserve(children.len().min(MAX_CHILDREN_RESERVE));
            for child in children.iter() {
                node.children.push(self.build_node(child?, depth + 1)?);
            }
        }
        Ok(node)
    }
}

fn read_materials(meshes: Option<schema::Meshes<'_>>) -> ReadResult<Vec<GltfMaterial>> {
    let mut out = Vec::new();
    if let Some(materials) = meshes.map(|m| m.materials()).transpose()?.flatten() {
        for mat in materials.iter() {
            let mat = mat?.0;
            let mut gm = GltfMaterial::default();
            gm.pbr_metallic_roughness.base_color_factor = [
                f64::from(mat.r) / 255.0,
                f64::from(mat.g) / 255.0,
                f64::from(mat.b) / 255.0,
                f64::from(mat.a) / 255.0,
            ];
            gm.pbr_metallic_roughness.metallic_factor = 0.0;
            gm.pbr_metallic_roughness.roughness_factor = 1.0;
            gm.double_sided = mat.rendered_faces != RenderedFaces::ONE;
            out.push(gm);
        }
    }
    if out.is_empty() {
        let mut gm = GltfMaterial::default();
        gm.pbr_metallic_roughness.base_color_factor = [0.8, 0.8, 0.8, 1.0];
        gm.pbr_metallic_roughness.metallic_factor = 0.0;
        gm.pbr_metallic_roughness.roughness_factor = 1.0;
        out.push(gm);
    }
    Ok(out)
}

/// `layer_hidden` and `generated_name_guids` from the model's metadata
/// JSON; both stay empty when it is missing or unparseable.
fn read_metadata(
    metadata: Option<crate::flatbuffers::Str<'_>>,
) -> (BTreeMap<String, bool>, BTreeSet<String>) {
    let mut layer_hidden = BTreeMap::new();
    let mut generated = BTreeSet::new();
    if let Some(Ok(json::Value::Object(obj))) = metadata.map(|m| json::parse(m.as_bytes())) {
        if let Some(json::Value::Object(lh)) = obj.get("layer_hidden") {
            for (key, value) in lh {
                if let json::Value::Bool(b) = value {
                    layer_hidden.insert(key.clone(), *b);
                }
            }
        }
        if let Some(json::Value::Array(items)) = obj.get("generated_name_guids") {
            for item in items {
                if let json::Value::String(s) = item {
                    generated.insert(s.clone());
                }
            }
        }
    }
    (layer_hidden, generated)
}

fn read_model(raw: &[u8]) -> ReadResult<InstancedScene> {
    let model = schema::root_as_model(raw)?;
    let meshes = model.meshes()?;
    let gltf_materials = read_materials(meshes)?;
    let shells = meshes.map(|m| m.shells()).transpose()?.flatten();
    let n_shells = shells.as_ref().map_or(0, |s| s.len());

    // Samples grouped by item: `meshes_items[sample.item]` is the item index
    // (Sample::item itself is NOT - it indexes meshes_items and
    // global_transforms). The C++ reads `meshes_items[k]` (k = the sample's
    // position), which is the same for every OpenSKP-written file (they
    // always write `item = k`), but reads past the vector on real ThatOpen
    // files, which carry fewer meshes_items than samples.
    let mut samples_by_item: BTreeMap<u32, Vec<usize>> = BTreeMap::new();
    if let Some(meshes) = meshes {
        if let Some(samples) = meshes.samples()?.filter(|s| !s.is_empty()) {
            let items = meshes
                .meshes_items()?
                .ok_or_else(|| InvalidFlatbuffer::new("Meshes.meshes_items is missing"))?;
            for k in 0..samples.len() {
                let mesh_item = samples.get(k)?.0.item as usize;
                samples_by_item
                    .entry(items.get(mesh_item)?)
                    .or_default()
                    .push(k);
            }
        }
    }

    // local_ids[i] IS the item index space.
    let mut local_id_to_item_index = HashMap::new();
    if let Some(local_ids) = model.local_ids()? {
        for (i, lid) in local_ids.iter().enumerate() {
            local_id_to_item_index.insert(lid?, i);
        }
    }

    let mut guid_by_item = HashMap::new();
    if let Some(guids_items) = model.guids_items()? {
        let guids = model.guids()?;
        for (i, lid) in guids_items.iter().enumerate() {
            if let (Some(&item), Some(guids)) = (local_id_to_item_index.get(&lid?), guids.as_ref())
            {
                if i < guids.len() {
                    guid_by_item.insert(item, guids.get(i)?.to_string_lossy());
                }
            }
        }
    }

    let (layer_hidden, generated_name_guids) = read_metadata(model.metadata()?);

    let mut reader = FragReader {
        model,
        meshes,
        shells,
        shell_cache: vec![None; n_shells],
        material_count: gltf_materials.len(),
        samples_by_item,
        local_id_to_item_index,
        guid_by_item,
        generated_name_guids,
        mesh_resources: Vec::new(),
        resource_by_signature: HashMap::new(),
        resource_by_item: HashMap::new(),
        warned_unsupported: false,
        visited_nodes: 0,
        visited_tables: HashSet::new(),
    };

    let scene_hierarchy = match model.spatial_structure()? {
        Some(spatial) => reader.build_node(spatial, 0)?,
        None => InstancedNode {
            name: "ROOT".into(),
            definition_name: "ROOT".into(),
            ..InstancedNode::default()
        },
    };

    Ok(InstancedScene {
        bounds: None,
        scene_hierarchy,
        mesh_resources: reader.mesh_resources,
        gltf_materials,
        textures: Vec::new(),
        layer_hidden,
        curve_resources: Vec::new(),
    })
}

/// Parses a `.frag` buffer into an instanced scene - the mirror of
/// [`to_fragments`]. Accepts the zlib-compressed wire format or raw
/// FlatBuffers bytes, detected the way the real loader does: by trying
/// zlib inflation first.
///
/// Known gaps (shared with every other port): `InstancedNode::position_mm`,
/// `properties` and `attribute_dictionaries` stay at their defaults - the
/// format only carries the `Name` attribute this reads. Geometry comes back
/// flat-shaded with zero UVs; samples of a non-SHELL representation (e.g.
/// CIRCLE_EXTRUSION) are skipped with a one-time warning on stderr.
pub fn from_fragments(data: &[u8]) -> Result<InstancedScene, FragmentsError> {
    from_fragments_capped(data, MAX_FRAGMENTS_DECOMPRESSED_BYTES)
}

/// [`from_fragments`] with the decompressed size capped at `max_inflated`.
fn from_fragments_capped(
    data: &[u8],
    max_inflated: usize,
) -> Result<InstancedScene, FragmentsError> {
    let inflated = match crate::inflate::zlib_decompress_capped(data, max_inflated) {
        Ok(buf) => Some(buf),
        Err(crate::inflate::InflateError::OutputLimit) => {
            return Err(FragmentsError::InvalidBuffer(InvalidFlatbuffer::new(
                "decompressed fragments payload exceeds safety limit",
            )));
        }
        Err(_) => None,
    };
    let raw = inflated.as_deref().unwrap_or(data);
    Ok(read_model(raw)?)
}

/// Reads a `.frag` file into an instanced scene. See [`from_fragments`].
pub fn read_fragments(path: impl AsRef<Path>) -> Result<InstancedScene, FragmentsError> {
    let path = path.as_ref();
    let data = std::fs::read(path).map_err(|source| FragmentsError::Io {
        path: path.to_path_buf(),
        source,
    })?;
    from_fragments(&data)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn decompose_trs_recovers_rotation_scale_and_mirror() {
        // 90 degrees about Y, scaled (2, 3, 4), translated.
        let m: Mat4 = [
            0.0, 0.0, -2.0, 0.0, 0.0, 3.0, 0.0, 0.0, 4.0, 0.0, 0.0, 0.0, 1.0, 2.0, 3.0, 1.0,
        ];
        let trs = decompose_trs(&m);
        assert_eq!(trs.position, [1.0, 2.0, 3.0]);
        assert_eq!(trs.scale, [2.0, 3.0, 4.0]);
        assert!(!trs.mirrored);
        assert_eq!(trs.x_dir, [0.0, 0.0, -1.0]);

        let mut mirrored = IDENTITY_GLTF;
        mirrored[0] = -1.0;
        let trs = decompose_trs(&mirrored);
        assert!(trs.mirrored);
        assert_eq!(trs.x_dir, [1.0, 0.0, 0.0]);
        assert_eq!(scale_cache_key(true, trs.scale)[0], (-1.0f64).to_bits());
    }

    #[test]
    fn bake_primitive_mirrors_x_and_reverses_winding() {
        let prim = LocalPrimitive {
            positions: vec![1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0],
            indices: vec![0, 1, 2],
            ..LocalPrimitive::default()
        };
        let baked = bake_primitive(&prim, [2.0, 1.0, 1.0], true);
        assert_eq!(baked.points[0], [-2.0, 2.0, 3.0]);
        assert_eq!(baked.triangles, vec![[0, 2, 1]]);
    }

    #[test]
    fn transform_round_trips_through_matrix16() {
        let m: Mat4 = [
            0.0, 0.0, -1.0, 0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 5.0, 6.0, 7.0, 1.0,
        ];
        let trs = decompose_trs(&m);
        let t = Transform {
            position: DoubleVector::new(trs.position[0], trs.position[1], trs.position[2]),
            x_direction: FloatVector::new(
                trs.x_dir[0] as f32,
                trs.x_dir[1] as f32,
                trs.x_dir[2] as f32,
            ),
            y_direction: FloatVector::new(
                trs.y_dir[0] as f32,
                trs.y_dir[1] as f32,
                trs.y_dir[2] as f32,
            ),
        };
        assert_eq!(transform_to_matrix16(&t), m);
    }

    #[test]
    fn color_bytes_round_and_wrap_like_lround() {
        assert_eq!(color_byte(1.0), 255);
        assert_eq!(color_byte(0.5), 128); // 127.5 rounds away from zero
        assert_eq!(color_byte(0.0), 0);
    }

    #[test]
    fn garbage_is_an_error_not_a_panic() {
        assert!(matches!(
            from_fragments(&[]),
            Err(FragmentsError::InvalidBuffer(_))
        ));
        assert!(matches!(
            from_fragments(&[1, 2, 3, 4, 5, 6, 7, 8]),
            Err(FragmentsError::InvalidBuffer(_))
        ));
        let e = read_fragments("definitely/not/here.frag").unwrap_err();
        assert!(matches!(e, FragmentsError::Io { .. }));
        assert!(e.to_string().contains("could not open"));
    }

    #[test]
    fn a_payload_that_inflates_past_the_cap_is_rejected() {
        // The real cap is 2 GiB (the largest FlatBuffer); the same path at 1 MiB.
        let bomb = crate::deflate::zlib_compress(&vec![0u8; 4 << 20], ZLIB_LEVEL);
        assert!(bomb.len() < 64 << 10);
        let err = from_fragments_capped(&bomb, 1 << 20).unwrap_err();
        assert!(err.to_string().contains("exceeds safety limit"), "{err}");
        // Under the cap the zeros inflate, and are then not a FlatBuffer.
        assert!(matches!(
            from_fragments_capped(&bomb, 8 << 20),
            Err(FragmentsError::InvalidBuffer(e)) if !e.message().contains("safety limit")
        ));
    }
}
