//! Instancing-preserving GLB export (`instanced_glb.hpp` /
//! `instanced_glb.cpp`).

use std::collections::HashMap;
use std::path::Path;

use crate::glb::{
    check_finite, document, material_json, validate_primitive, write_glb, GlbError, GlbResult,
    GltfBuilder,
};
use crate::instanced_scene::{InstancedNode, InstancedScene, IDENTITY_GLTF};
use crate::json_export::JsonValue;

/// Options for [`to_instanced_glb`] / [`export_instanced_glb`].
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct InstancedGlbOptions {
    /// Embed the scene's texture images (see `GlbOptions::textures`).
    pub textures: bool,
}

fn validate_scene(scene: &InstancedScene) -> GlbResult<()> {
    if scene.gltf_materials.len() > i32::MAX as usize {
        return Err(GlbError::Length(
            "scene has too many GLB model entries".into(),
        ));
    }
    for (mi, mat) in scene.gltf_materials.iter().enumerate() {
        let pbr = &mat.pbr_metallic_roughness;
        for &v in &pbr.base_color_factor {
            check_finite(v, &format!("material {mi} base_color_factor"))?;
            if !(0.0..=1.0).contains(&v) {
                return Err(GlbError::InvalidArgument(format!(
                    "material {mi} base_color_factor must be between 0 and 1"
                )));
            }
        }
        check_finite(
            pbr.metallic_factor,
            &format!("material {mi} metallic_factor"),
        )?;
        check_finite(
            pbr.roughness_factor,
            &format!("material {mi} roughness_factor"),
        )?;
    }
    for (ri, resource) in scene.mesh_resources.iter().enumerate() {
        for (pi, p) in resource.primitives.iter().enumerate() {
            validate_primitive(
                &format!("resource {ri} primitive {pi} "),
                &p.positions,
                &p.normals,
                &p.uvs,
                &p.indices,
                p.material_index,
                scene.gltf_materials.len(),
            )?;
        }
    }
    Ok(())
}

fn is_identity(m: &[f64; 16]) -> bool {
    m.iter()
        .zip(IDENTITY_GLTF.iter())
        .all(|(a, b)| (a - b).abs() <= 1e-12)
}

/// Appends `node` and its subtree in pre-order; returns `node`'s index.
fn emit_node(
    nodes: &mut Vec<JsonValue>,
    node: &InstancedNode,
    mesh_index_by_id: &HashMap<&str, usize>,
) -> usize {
    let mut gltf_node = JsonValue::object();
    if !node.name.is_empty() {
        gltf_node.set("name", &node.name);
    } else if !node.definition_name.is_empty() {
        gltf_node.set("name", &node.definition_name);
    }
    // glTF treats an omitted matrix as the identity.
    if !is_identity(&node.matrix) {
        gltf_node.set(
            "matrix",
            JsonValue::Array(node.matrix.iter().map(|&v| v.into()).collect()),
        );
    }
    if let Some(&mesh) = node
        .mesh_resource_id
        .as_deref()
        .and_then(|id| mesh_index_by_id.get(id))
    {
        gltf_node.set("mesh", mesh);
    }
    let idx = nodes.len();
    nodes.push(gltf_node);
    let children: Vec<JsonValue> = node
        .children
        .iter()
        .map(|c| JsonValue::from(emit_node(nodes, c, mesh_index_by_id)))
        .collect();
    if !children.is_empty() {
        nodes[idx].set("children", children);
    }
    idx
}

/// Serializes an [`InstancedScene`] as a binary glTF 2.0 (GLB) asset,
/// PRESERVING instancing: each mesh resource is written to the binary
/// buffer exactly once, and every placement is a glTF node whose `mesh`
/// points at it. A component placed 1,000 times contributes one copy of its
/// vertex/index buffers plus 1,000 node transforms.
pub fn to_instanced_glb(
    scene: &InstancedScene,
    options: &InstancedGlbOptions,
) -> GlbResult<Vec<u8>> {
    validate_scene(scene)?;
    let materials: Vec<JsonValue> = scene
        .gltf_materials
        .iter()
        .map(|m| material_json(m, options.textures))
        .collect();

    let mut builder = GltfBuilder::new();
    // Reserve the final size once so appends stay amortized O(1)
    // (openskp#305).
    let total: usize = scene
        .mesh_resources
        .iter()
        .flat_map(|r| &r.primitives)
        .map(|p| (p.positions.len() + p.normals.len() + p.uvs.len() + p.indices.len()) * 4)
        .sum();
    builder.binary.reserve(total);

    let mut meshes = Vec::new();
    let mut mesh_index_by_id: HashMap<&str, usize> = HashMap::new();
    for resource in &scene.mesh_resources {
        let mut primitives = Vec::with_capacity(resource.primitives.len());
        for p in &resource.primitives {
            primitives.push(builder.add_primitive(
                &p.positions,
                &p.normals,
                &p.uvs,
                &p.indices,
                p.material_index,
            )?);
        }
        if primitives.is_empty() {
            continue;
        }
        let mut mesh = JsonValue::object();
        mesh.set(
            "name",
            if resource.definition_name.is_empty() {
                &resource.id
            } else {
                &resource.definition_name
            },
        );
        mesh.set("primitives", primitives);
        mesh_index_by_id.insert(&resource.id, meshes.len());
        meshes.push(mesh);
    }

    let textures = if options.textures && !scene.textures.is_empty() {
        Some(builder.add_textures(&scene.textures)?)
    } else {
        None
    };

    let mut nodes = Vec::new();
    let root = emit_node(&mut nodes, &scene.scene_hierarchy, &mesh_index_by_id);
    let mut gltf_scene = JsonValue::object();
    gltf_scene.set("name", "OpenSKP Instanced Scene");
    gltf_scene.set("nodes", vec![JsonValue::from(root)]);

    let len = builder.binary.len();
    let (doc, binary) = document(
        "OpenSKP Instanced Exporter",
        builder,
        Some(len),
        materials,
        meshes,
        nodes,
        gltf_scene,
        textures,
    );
    write_glb(&doc, &binary)
}

/// Serializes an [`InstancedScene`] and writes the GLB bytes to
/// `output_path`. The parent directory is not created.
pub fn export_instanced_glb(
    scene: &InstancedScene,
    output_path: impl AsRef<Path>,
    options: &InstancedGlbOptions,
) -> GlbResult<()> {
    let bytes = to_instanced_glb(scene, options)?;
    std::fs::write(output_path, bytes)?;
    Ok(())
}
