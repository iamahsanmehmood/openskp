//! The baked scene builder (`scene.cpp`'s `build_scene_raw`), plus the Python
//! port's loose-edge curve sets (`scene.py`'s `curve_sets`).

use std::collections::{BTreeMap, BTreeSet, HashMap};

use crate::curves::{chain_loose_edges, majority_layer, round6, solve_run_arc, INCHES_TO_M};
use crate::errors::SkpParseError;
use crate::face_groups::{
    build_local_face_groups, default_color, find_material, material_color, FaceColorKey,
    FaceGroupContext, GroupKey, TextureRegistry,
};
use crate::internal::strconv::c_stoll;
use crate::internal::{
    emit_log, emit_progress, multiply_matrices, transform_determinant, transform_normal,
    transform_point, Error, GeometryBuilder, RawInstance, RawParsed,
};
use crate::model::{stringify_attr_dict, EntityId, StringDictionaries};
use crate::observability::{LogLevel, ParseOptions, ParseStage, PROGRESS_INTERVAL};
use crate::scene::{
    CurveSetMetadata, GlbPrimitive, GltfMaterial, InstanceNode, MeshMetadata, Scene,
};

const INCHES_TO_MM: f64 = 25.4;

/// The 13-value identity matrix.
pub(crate) const IDENTITY: [f64; 13] = [
    1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0,
];

/// SketchUp's own auto-generated placeholder definition names
/// (`^(?:Group|Component)\d*#\d+$`: "Group#1", "Component#12", "Group86#2"),
/// which carry no more meaning than the internal index.
pub(crate) fn is_generic_definition_name(name: &str) -> bool {
    let Some(rest) = name
        .strip_prefix("Group")
        .or_else(|| name.strip_prefix("Component"))
    else {
        return false;
    };
    let rest = rest.trim_start_matches(|c: char| c.is_ascii_digit());
    let Some(index) = rest.strip_prefix('#') else {
        return false;
    };
    !index.is_empty() && index.bytes().all(|b| b.is_ascii_digit())
}

/// Scene nodes omit SketchUp's own dictionaries: `dynamic_attributes` is
/// already on `properties`, `SU_InstanceSet` is Owner/Status boilerplate.
pub(crate) fn omitted_from_scene(dict_name: &str) -> bool {
    dict_name == "dynamic_attributes" || dict_name == "SU_InstanceSet"
}

/// The attribute-dictionary display-name override: "name", "label" or "code"
/// on any dictionary except the omitted ones; first dictionary and first key
/// with a non-empty value wins.
pub(crate) fn name_override(i: &RawInstance) -> Option<String> {
    for (dict_name, entries) in &i.attribute_dicts {
        if omitted_from_scene(dict_name) {
            continue;
        }
        for key in ["name", "label", "code"] {
            if let Some(value) = entries.get(key) {
                let s = value.to_string();
                if !s.is_empty() {
                    return Some(s);
                }
            }
        }
    }
    None
}

/// The instance's dictionaries minus the omitted ones, stringified.
pub(crate) fn scene_dictionaries(i: &RawInstance) -> StringDictionaries {
    i.attribute_dicts
        .iter()
        .filter(|(name, _)| !omitted_from_scene(name))
        .map(|(name, entries)| (name.clone(), stringify_attr_dict(entries)))
        .collect()
}

/// An instance's layer: its own id resolved to a name, the parent's layer
/// when the id names no layer, or the raw text when it is not a number.
pub(crate) fn instance_layer(
    p: &RawParsed,
    o: &ParseOptions,
    i: &RawInstance,
    parent_layer: &str,
) -> String {
    if i.layer.is_empty() {
        return parent_layer.to_owned();
    }
    match c_stoll(&i.layer) {
        Some(id) => p
            .layer_id_to_name
            .get(&id)
            .cloned()
            .unwrap_or_else(|| parent_layer.to_owned()),
        None => {
            emit_log(
                o,
                LogLevel::Debug,
                &format!("Failed to resolve layer id '{}' to a name", i.layer),
            );
            i.layer.clone()
        }
    }
}

/// The definition's name, or `""` when the instance names none / an unknown
/// one.
pub(crate) fn definition_name(p: &RawParsed, i: &RawInstance) -> String {
    i.ref_idx
        .and_then(|r| p.definitions.get(&r))
        .map(|d| d.name.clone())
        .unwrap_or_default()
}

/// The fallback layer a definition's loose edges resolve to (Python port):
/// its own faces' majority layer when they carry one, else `layer`.
pub(crate) fn curve_fallback_layer(p: &RawParsed, b: &GeometryBuilder, layer: &str) -> String {
    match majority_layer(b.faces.values().map(|f| f.layer_id)) {
        Some(id) => p
            .layer_id_to_name
            .get(&id)
            .cloned()
            .unwrap_or_else(|| layer.to_owned()),
        None => layer.to_owned(),
    }
}

/// A run's own layer: its edges' majority layer, else `fallback`.
pub(crate) fn run_layer(
    p: &RawParsed,
    b: &GeometryBuilder,
    edge_ids: &[EntityId],
    fallback: &str,
) -> String {
    match majority_layer(edge_ids.iter().map(|e| b.edge_layers.get(e).copied())) {
        Some(id) => p
            .layer_id_to_name
            .get(&id)
            .cloned()
            .unwrap_or_else(|| fallback.to_owned()),
        None => fallback.to_owned(),
    }
}

/// A missing vertex (the C++ `std::out_of_range` from `map::at`).
pub(crate) fn build_error(e: Error) -> SkpParseError {
    SkpParseError::at_stage(e.message.to_string(), ParseStage::BuildScene).with_cause(e)
}

/// Maximum component nesting depth before rejecting the hierarchy.
pub(crate) const MAX_COMPONENT_DEPTH: usize = 256;

/// Maximum total expanded component instances in a scene build (guards against
/// exponential DAG expansion / "billion laughs" component graphs).
pub(crate) const MAX_EXPANDED_INSTANCES: u64 = 50_000_000;

/// Validates component hierarchy depth, cycles, and expanded instance bounds
/// before initiating expensive scene allocation. Uses memoization so DAGs with
/// exponential expansions are detected in linear time O(V + E) without allocating.
pub(crate) fn validate_hierarchy(p: &RawParsed) -> Result<(), SkpParseError> {
    fn check_def(
        builder: &crate::internal::GeometryBuilder,
        p: &RawParsed,
        active: &mut Vec<EntityId>,
        memo: &mut HashMap<EntityId, u64>,
    ) -> Result<u64, SkpParseError> {
        let mut total = 0u64;
        for i in &builder.instances {
            let Some(child_r) = i.ref_idx else { continue };
            if child_r != 0 {
                if active.contains(&child_r) {
                    return Err(recursive_definition(child_r));
                }
                if active.len() >= MAX_COMPONENT_DEPTH {
                    return Err(max_depth_exceeded(child_r));
                }
                let child_count = if let Some(&cached) = memo.get(&child_r) {
                    cached
                } else if let Some(d) = p.definitions.get(&child_r) {
                    active.push(child_r);
                    let res = check_def(&d.builder, p, active, memo);
                    active.pop();
                    let count = res?;
                    memo.insert(child_r, count);
                    count
                } else {
                    0
                };
                total = total
                    .checked_add(1)
                    .and_then(|t| t.checked_add(child_count))
                    .ok_or_else(|| max_instances_exceeded(child_r))?;
                if total > MAX_EXPANDED_INSTANCES {
                    return Err(max_instances_exceeded(child_r));
                }
            } else {
                total = total
                    .checked_add(1)
                    .ok_or_else(|| max_instances_exceeded(0))?;
                if total > MAX_EXPANDED_INSTANCES {
                    return Err(max_instances_exceeded(0));
                }
            }
        }
        Ok(total)
    }

    let mut active = Vec::new();
    let mut memo = HashMap::new();
    check_def(&p.root.builder, p, &mut active, &mut memo)?;
    Ok(())
}

pub(crate) fn recursive_definition(id: EntityId) -> SkpParseError {
    SkpParseError::at_stage("Recursive component definition", ParseStage::BuildScene)
        .with_definition_id(id)
}

pub(crate) fn max_depth_exceeded(id: EntityId) -> SkpParseError {
    SkpParseError::at_stage(
        "Component nesting depth exceeds maximum allowed limit",
        ParseStage::BuildScene,
    )
    .with_definition_id(id)
}

pub(crate) fn max_instances_exceeded(id: EntityId) -> SkpParseError {
    SkpParseError::at_stage(
        "Expanded component instance count exceeds maximum allowed limit",
        ParseStage::BuildScene,
    )
    .with_definition_id(id)
}

fn position_mm(m: &[f64]) -> [f64; 3] {
    [
        if m.len() > 9 {
            m[9] * INCHES_TO_MM
        } else {
            0.0
        },
        if m.len() > 10 {
            m[10] * INCHES_TO_MM
        } else {
            0.0
        },
        if m.len() > 11 {
            m[11] * INCHES_TO_MM
        } else {
            0.0
        },
    ]
}

/// The mesh-name form of a path: " / " -> "__", spaces -> "_", at most 80
/// bytes (a multi-byte character the cut splits becomes U+FFFD).
fn safe(s: &str) -> String {
    let s = s.replace(" / ", "__").replace(' ', "_");
    if s.len() > 80 {
        String::from_utf8_lossy(&s.as_bytes()[..80]).into_owned()
    } else {
        s
    }
}

fn last_path_segment(path: &str) -> &str {
    match path.rfind(" / ") {
        Some(k) => &path[k + 3..],
        None => path,
    }
}

type PathUpdate = (BTreeMap<String, String>, String, StringDictionaries);

struct Baker<'a> {
    p: &'a RawParsed,
    o: &'a ParseOptions,
    scene: Scene,
    textures: TextureRegistry,
    materials: BTreeMap<GroupKey, usize>,
    mesh_counter: usize,
    instance_counter: u64,
    active: BTreeSet<EntityId>,
    /// Deferred mesh backfill: each mesh's path is recorded verbatim by the
    /// exact instance that placed its definition, so one lookup per mesh after
    /// the walk gives it its real per-instance name / properties /
    /// dictionaries.
    path_updates: HashMap<String, PathUpdate>,
    /// Per-layer sequence numbers for generated root curve-set names.
    curve_seq: HashMap<String, usize>,
}

impl<'a> Baker<'a> {
    #[allow(clippy::too_many_arguments)]
    fn bake(
        &mut self,
        b: &'a GeometryBuilder,
        defname: &str,
        matrix: &[f64],
        layer: &str,
        path: &str,
        inherited: Option<FaceColorKey>,
    ) -> Result<Vec<InstanceNode>, SkpParseError> {
        let p = self.p;
        let fallback = inherited.unwrap_or_else(|| default_color(p, layer));
        let groups = {
            let textures = &mut self.textures;
            let mut texture_index_for = |m: Option<&_>| textures.index_for(m);
            let mut ctx = FaceGroupContext {
                texture_index_for: &mut texture_index_for,
                fallback_color: fallback,
            };
            build_local_face_groups(p, b, &mut ctx).map_err(build_error)?
        };
        let group_count = groups.len();
        for (key, g) in &groups {
            if g.tris.is_empty() {
                continue;
            }
            let mut geom = format!("mesh_{}_{}_{}", self.mesh_counter, safe(path), layer);
            self.mesh_counter += 1;
            if group_count > 1 {
                geom += &format!(
                    "_{}_{}_{}_{}{}",
                    key.color[0],
                    key.color[1],
                    key.color[2],
                    key.color[3],
                    if key.double_sided { "_ds" } else { "_ss" }
                );
            }
            let meta = MeshMetadata {
                name: if path == "ROOT" {
                    "ROOT".to_owned()
                } else {
                    last_path_segment(path).to_owned()
                },
                definition_name: defname.to_owned(),
                layer: layer.to_owned(),
                path: path.to_owned(),
                position_mm: position_mm(matrix),
                ..Default::default()
            };
            self.scene.mesh_index.insert(geom.clone(), meta);

            let mut prim = GlbPrimitive {
                geom_name: geom,
                ..Default::default()
            };
            let mirrored = transform_determinant(matrix) < 0.0;
            prim.indices.reserve(g.tris.len() * 3);
            for t in &g.tris {
                prim.indices.push(t[0] as u32);
                prim.indices.push(t[if mirrored { 2 } else { 1 }] as u32);
                prim.indices.push(t[if mirrored { 1 } else { 2 }] as u32);
            }
            let count = g.verts.len();
            prim.positions = vec![0.0; count * 3];
            prim.normals = vec![0.0; count * 3];
            prim.uvs = vec![0.0; count * 2];
            for i in 0..count {
                let pt = transform_point(matrix, g.verts[i]);
                prim.positions[i * 3] = (pt[0] * 0.0254) as f32;
                prim.positions[i * 3 + 1] = (pt[2] * 0.0254) as f32;
                prim.positions[i * 3 + 2] = (-pt[1] * 0.0254) as f32;
                prim.uvs[i * 2] = g.uvs[i][0];
                prim.uvs[i * 2 + 1] = g.uvs[i][1];
                let mut n = g.normals[i];
                let mut l = (n[0] * n[0] + n[1] * n[1] + n[2] * n[2]).sqrt();
                if !l.is_finite() || l < 1e-6 {
                    n = [0.0, 0.0, 1.0];
                } else {
                    for x in &mut n {
                        *x /= l;
                    }
                }
                let mut w = transform_normal(matrix, n);
                l = (w[0] * w[0] + w[1] * w[1] + w[2] * w[2]).sqrt();
                if !l.is_finite() || l < 1e-6 {
                    w = n;
                    l = 1.0;
                }
                prim.normals[i * 3] = (w[0] / l) as f32;
                prim.normals[i * 3 + 1] = (w[2] / l) as f32;
                prim.normals[i * 3 + 2] = (-w[1] / l) as f32;
            }
            prim.material_index = match self.materials.get(key) {
                Some(&idx) => idx,
                None => {
                    let idx = self.scene.gltf_materials.len();
                    let mut gm = GltfMaterial::default();
                    gm.pbr_metallic_roughness.base_color_factor = [
                        f64::from(key.color[0]) / 255.0,
                        f64::from(key.color[1]) / 255.0,
                        f64::from(key.color[2]) / 255.0,
                        f64::from(key.color[3]) / 255.0,
                    ];
                    gm.pbr_metallic_roughness.base_color_texture = key.texture_index;
                    gm.double_sided = key.double_sided;
                    self.scene.gltf_materials.push(gm);
                    self.materials.insert(*key, idx);
                    idx
                }
            };
            self.scene.glb_primitives.push(prim);
        }

        self.bake_curves(b, defname, matrix, layer, path);

        let mut children = Vec::new();
        for i in &b.instances {
            let child_layer = instance_layer(p, self.o, i, layer);
            let child_color = material_color(find_material(p, i.material_id)).or(inherited);

            let def_name = definition_name(p, i);
            let def_name_is_real = !def_name.is_empty() && !is_generic_definition_name(&def_name);
            let name_override = name_override(i);
            let inst_name = if !i.name.is_empty() {
                i.name.clone()
            } else if def_name_is_real {
                def_name.clone()
            } else {
                format!(
                    "Component_{}",
                    i.ref_idx.map(|r| r.to_string()).unwrap_or_default()
                )
            };
            let name_is_generated =
                !(name_override.is_some() || !i.name.is_empty() || def_name_is_real);
            let display_name = name_override.unwrap_or_else(|| inst_name.clone());

            let child_path = format!("{path} / {inst_name}");
            let mat = multiply_matrices(matrix, &i.matrix);
            let mut nested = Vec::new();
            if let Some(r) = i.ref_idx {
                if self.active.contains(&r) {
                    return Err(recursive_definition(r));
                }
                if self.active.len() >= MAX_COMPONENT_DEPTH {
                    return Err(max_depth_exceeded(r));
                }
                if self.instance_counter >= MAX_EXPANDED_INSTANCES {
                    return Err(max_instances_exceeded(r));
                }
                if let Some(d) = p.definitions.get(&r) {
                    self.active.insert(r);
                    nested = self.bake(
                        &d.builder,
                        &d.name,
                        &mat,
                        &child_layer,
                        &child_path,
                        child_color,
                    )?;
                    self.active.remove(&r);
                }
            }
            let dicts = scene_dictionaries(i);
            let node = InstanceNode {
                name: display_name.clone(),
                name_is_generated,
                definition_name: def_name,
                layer: child_layer,
                position_mm: position_mm(&mat),
                properties: i.properties.clone(),
                children: nested,
                attribute_dictionaries: dicts.clone(),
                path: child_path.clone(),
            };
            self.path_updates
                .insert(child_path, (i.properties.clone(), display_name, dicts));
            children.push(node);
            self.instance_counter += 1;
            if self.instance_counter.is_multiple_of(PROGRESS_INTERVAL) {
                emit_progress(
                    self.o,
                    ParseStage::BuildScene,
                    self.instance_counter,
                    self.instance_counter,
                );
            }
        }
        Ok(children)
    }

    /// Loose edges -> world-space polyline runs (Python port's `curve_sets`).
    fn bake_curves(
        &mut self,
        b: &GeometryBuilder,
        defname: &str,
        matrix: &[f64],
        layer: &str,
        path: &str,
    ) {
        let runs = chain_loose_edges(b);
        if runs.is_empty() {
            return;
        }
        let p = self.p;
        let fallback = curve_fallback_layer(p, b, layer);
        for run in runs {
            let mut pts = Vec::with_capacity(run.vertex_ids.len());
            for vk in &run.vertex_ids {
                // A dangling vertex would silently shorten the run; drop it.
                let Some(&v) = b.vertices.get(vk) else { break };
                let pt = transform_point(matrix, v);
                pts.push([
                    round6(pt[0] * INCHES_TO_M),
                    round6(pt[2] * INCHES_TO_M),
                    round6(-pt[1] * INCHES_TO_M),
                ]);
            }
            if pts.len() != run.vertex_ids.len() || pts.len() < 2 {
                continue;
            }
            let layer = run_layer(p, b, &run.edge_ids, &fallback);
            // Root-level loose geometry has no name in the file: label it with
            // its layer and a sequence number, flagged as generated.
            let (name, name_is_generated) = if path == "ROOT" {
                let seq = self.curve_seq.entry(layer.clone()).or_insert(0);
                *seq += 1;
                (format!("{layer}_{seq}"), true)
            } else {
                (last_path_segment(path).to_owned(), false)
            };
            let arc = solve_run_arc(b, &run.edge_ids, Some(matrix), &pts, run.closed);
            self.scene.curve_sets.push(CurveSetMetadata {
                name,
                definition_name: defname.to_owned(),
                layer,
                path: path.to_owned(),
                name_is_generated,
                points_m: pts,
                closed: run.closed,
                arc,
                attribute_dictionaries: StringDictionaries::new(),
            });
        }
    }
}

/// Bakes a reader's raw output into a world-space [`Scene`].
pub fn build_scene_raw(mut p: RawParsed, o: &ParseOptions) -> Result<Scene, SkpParseError> {
    let layer_hidden = std::mem::take(&mut p.layer_hidden);
    emit_log(
        o,
        LogLevel::Information,
        &format!(
            "Building scene: {} definitions available",
            p.definitions.len()
        ),
    );
    validate_hierarchy(&p)?;
    let mut baker = Baker {
        p: &p,
        o,
        scene: Scene {
            scene_hierarchy: InstanceNode {
                name: "ROOT".into(),
                definition_name: "ROOT_MODEL".into(),
                layer: "Layer0".into(),
                path: "ROOT".into(),
                ..Default::default()
            },
            layer_hidden,
            ..Default::default()
        },
        textures: TextureRegistry::default(),
        materials: BTreeMap::new(),
        mesh_counter: 0,
        instance_counter: 0,
        active: BTreeSet::new(),
        path_updates: HashMap::new(),
        curve_seq: HashMap::new(),
    };
    let children = baker.bake(
        &p.root.builder,
        "ROOT_MODEL",
        &IDENTITY,
        "Layer0",
        "ROOT",
        None,
    )?;
    let Baker {
        mut scene,
        textures,
        path_updates,
        instance_counter,
        ..
    } = baker;
    scene.scene_hierarchy.children = children;
    scene.textures = textures.textures;

    // Apply the deferred backfill now that every instance has been walked.
    for meta in scene.mesh_index.values_mut() {
        if let Some((properties, name, dicts)) = path_updates.get(&meta.path) {
            meta.properties = properties.clone();
            meta.name = name.clone();
            meta.attribute_dictionaries = dicts.clone();
        }
    }

    emit_log(
        o,
        LogLevel::Information,
        &format!(
            "Scene build complete: {} instances, {} meshes, {} primitives",
            instance_counter,
            scene.mesh_index.len(),
            scene.glb_primitives.len()
        ),
    );
    Ok(scene)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn generic_definition_names() {
        for name in ["Group#1", "Component#12", "Group86#2", "Component0#0"] {
            assert!(is_generic_definition_name(name), "{name}");
        }
        for name in [
            "Group",
            "Group#",
            "Group#1a",
            "group#1",
            "Wall#1",
            "Group 1#2",
            "",
            "#1",
        ] {
            assert!(!is_generic_definition_name(name), "{name}");
        }
    }

    #[test]
    fn safe_paths() {
        assert_eq!(safe("ROOT / a b / c"), "ROOT__a_b__c");
        assert_eq!(safe(&"x".repeat(100)).len(), 80);
    }
}
