//! The baked, world-space scene (`scene.hpp`). Types only; the builder lives
//! in `scene_build.rs`.

use std::collections::BTreeMap;

use crate::model::{ByteBuffer, StringDictionaries};

/// One node of the baked instance tree.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct InstanceNode {
    pub name: String,
    /// True when `name` is a fallback this project generated rather than a
    /// real name from the file (Python port extension; see
    /// `InstancedNode::name_is_generated`).
    pub name_is_generated: bool,
    pub definition_name: String,
    pub layer: String,
    pub position_mm: [f64; 3],
    pub properties: BTreeMap<String, String>,
    pub children: Vec<InstanceNode>,
    /// Every attribute dictionary other than SketchUp's own
    /// `dynamic_attributes` (already on `properties`) and `SU_InstanceSet`
    /// (always-present Owner/Status boilerplate). Values are stringified.
    pub attribute_dictionaries: StringDictionaries,
    /// The full-path string this node's meshes carry as `MeshMetadata::path`
    /// (Python port extension), or `""`.
    pub path: String,
}

/// Metadata for one baked mesh, keyed like its primitive's `geom_name`.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct MeshMetadata {
    pub name: String,
    pub definition_name: String,
    pub layer: String,
    pub position_mm: [f64; 3],
    pub properties: BTreeMap<String, String>,
    pub path: String,
    /// Same as [`InstanceNode::attribute_dictionaries`], backfilled from the
    /// instance that placed this mesh's definition.
    pub attribute_dictionaries: StringDictionaries,
}

/// One triangulated, world-space mesh (metres, glTF Y-up).
#[derive(Debug, Clone, PartialEq, Default)]
pub struct GlbPrimitive {
    pub positions: Vec<f32>,
    pub normals: Vec<f32>,
    /// Flat `[u, v, ...]` texture coordinates matching positions 1:1.
    pub uvs: Vec<f32>,
    pub indices: Vec<u32>,
    pub material_index: usize,
    pub geom_name: String,
}

/// glTF `pbrMetallicRoughness`.
#[derive(Debug, Clone, PartialEq)]
pub struct PbrMetallicRoughness {
    pub base_color_factor: [f64; 4],
    pub metallic_factor: f64,
    pub roughness_factor: f64,
    /// Index into `Scene::textures`, or `None` for an untextured material.
    /// `base_color_factor` stays the resolved color even with a texture set:
    /// glTF multiplies the two, and colorized materials rely on that tint.
    pub base_color_texture: Option<usize>,
}

impl Default for PbrMetallicRoughness {
    fn default() -> Self {
        Self {
            base_color_factor: [1.0, 1.0, 1.0, 1.0],
            metallic_factor: 0.0,
            roughness_factor: 0.8,
            base_color_texture: None,
        }
    }
}

/// One glTF material.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct GltfMaterial {
    pub name: String,
    pub texture_path: String,
    pub pbr_metallic_roughness: PbrMetallicRoughness,
    pub double_sided: bool,
}

/// One texture image referenced by the scene's glTF materials.
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct SceneTexture {
    /// The image file's raw bytes, exactly as stored in the .skp.
    pub data: ByteBuffer,
    /// Sniffed from the bytes, not from `filename`.
    pub mime_type: String,
    pub filename: String,
}

/// An analytic arc recovered for a loose-edge run (Python port extension):
/// points in the run's own frame plus 0-based `(start, mid, end)` index
/// triples into them, ready to become IFC4 `IfcArcIndex` entries.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct CurveArc {
    pub points: Vec<[f64; 3]>,
    pub segments: Vec<[usize; 3]>,
}

/// Metadata for one run of *loose* edges - edges no face uses (Python port
/// extension; the C++ port does not emit these). Points are world-space
/// metres in glTF's Y-up frame, like `GlbPrimitive::positions`.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct CurveSetMetadata {
    pub name: String,
    pub definition_name: String,
    pub layer: String,
    pub path: String,
    /// `name` was synthesised rather than read from the file.
    pub name_is_generated: bool,
    pub points_m: Vec<[f64; 3]>,
    /// The run returns to its own first point.
    pub closed: bool,
    /// Set only when the run provably is one whole circular arc.
    pub arc: Option<CurveArc>,
    pub attribute_dictionaries: StringDictionaries,
}

/// The result of `build_scene()`: the placed instances baked into a flat,
/// world-space scene.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct Scene {
    pub scene_hierarchy: InstanceNode,
    pub mesh_index: BTreeMap<String, MeshMetadata>,
    pub glb_primitives: Vec<GlbPrimitive>,
    pub gltf_materials: Vec<GltfMaterial>,
    /// Distinct texture images the placed materials use, deduplicated by
    /// source bytes.
    pub textures: Vec<SceneTexture>,
    /// Each layer's own hidden/visible state, by layer name.
    pub layer_hidden: BTreeMap<String, bool>,
    /// Loose-edge runs, one per chained polyline (Python port extension; the
    /// meshes above are unaffected by them). Empty for a model whose geometry
    /// is all faces.
    pub curve_sets: Vec<CurveSetMetadata>,
}
