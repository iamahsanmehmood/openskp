//! The instanced scene graph (`instanced_scene.hpp`). Types only; the builder
//! lives in `instanced_scene_build.rs`.

use std::collections::BTreeMap;

use crate::model::{EntityId, StringDictionaries};
use crate::scene::{CurveArc, GltfMaterial, SceneTexture};

/// One reusable, DEFINITION-LOCAL triangulated mesh: the instanced
/// counterpart of `GlbPrimitive`, minus the world transform (metres, glTF
/// Y-up). Normal transformation is deferred to the consumer's node transform.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct LocalPrimitive {
    pub positions: Vec<f32>,
    pub normals: Vec<f32>,
    pub uvs: Vec<f32>,
    pub indices: Vec<u32>,
    pub material_index: usize,
}

/// A definition's geometry resolved for one rendering context. One definition
/// can yield more than one resource (e.g. painted two colors) - see
/// `variant_key`.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct InstancedMeshResource {
    pub id: String,
    /// `None` for the root definition (loose, non-component geometry).
    pub definition_id: Option<EntityId>,
    pub definition_name: String,
    pub variant_key: String,
    pub primitives: Vec<LocalPrimitive>,
}

/// One loose-edge run in definition-local space (Python port extension).
#[derive(Debug, Clone, PartialEq, Default)]
pub struct LocalCurve {
    pub points_m: Vec<[f64; 3]>,
    pub closed: bool,
    /// This run's own resolved layer.
    pub layer: String,
    pub arc: Option<CurveArc>,
}

/// A definition's loose edges, ready to be referenced by any number of
/// nodes - the loose-edge counterpart of [`InstancedMeshResource`] (Python
/// port extension).
#[derive(Debug, Clone, PartialEq, Default)]
pub struct InstancedCurveResource {
    pub id: String,
    pub definition_id: Option<EntityId>,
    pub definition_name: String,
    pub curves: Vec<LocalCurve>,
}

/// The glTF identity matrix.
pub const IDENTITY_GLTF: [f64; 16] = [
    1.0, 0.0, 0.0, 0.0, //
    0.0, 1.0, 0.0, 0.0, //
    0.0, 0.0, 1.0, 0.0, //
    0.0, 0.0, 0.0, 1.0,
];

/// One placed node in the instanced scene graph.
#[derive(Debug, Clone, PartialEq)]
pub struct InstancedNode {
    pub name: String,
    /// True when `name` is a generated fallback rather than a real name.
    pub name_is_generated: bool,
    pub definition_name: String,
    pub layer: String,
    /// This instance's own persistent GUID (VFF), or `""`.
    pub guid: String,
    /// Transform RELATIVE TO THE PARENT, column-major glTF (metres, Y-up).
    pub matrix: [f64; 16],
    pub position_mm: [f64; 3],
    pub properties: BTreeMap<String, String>,
    /// Every attribute dictionary other than `dynamic_attributes` and
    /// `SU_InstanceSet`, stringified.
    pub attribute_dictionaries: StringDictionaries,
    pub mesh_resource_id: Option<String>,
    /// Python port extension: the loose-edge resource this node places.
    pub curve_resource_id: Option<String>,
    pub children: Vec<InstancedNode>,
}

impl Default for InstancedNode {
    fn default() -> Self {
        Self {
            name: String::new(),
            name_is_generated: false,
            definition_name: String::new(),
            layer: String::new(),
            guid: String::new(),
            matrix: IDENTITY_GLTF,
            position_mm: [0.0; 3],
            properties: BTreeMap::new(),
            attribute_dictionaries: BTreeMap::new(),
            mesh_resource_id: None,
            curve_resource_id: None,
            children: Vec::new(),
        }
    }
}

/// Axis-aligned bounds of the scene as PLACED, metres and Y-up.
#[derive(Debug, Clone, Copy, PartialEq, Default)]
pub struct SceneBounds {
    pub min: [f64; 3],
    pub max: [f64; 3],
    pub size: [f64; 3],
    pub center: [f64; 3],
}

/// The result of `build_instanced_scene()`: the placed scene graph with
/// SketchUp's instancing PRESERVED.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct InstancedScene {
    pub bounds: Option<SceneBounds>,
    pub scene_hierarchy: InstancedNode,
    pub mesh_resources: Vec<InstancedMeshResource>,
    pub gltf_materials: Vec<GltfMaterial>,
    pub textures: Vec<SceneTexture>,
    /// The file's own per-layer visibility, by layer name.
    pub layer_hidden: BTreeMap<String, bool>,
    /// Loose-edge resources, one per (definition, effective layer) (Python
    /// port extension). Empty for a model whose geometry is all faces.
    pub curve_resources: Vec<InstancedCurveResource>,
}
