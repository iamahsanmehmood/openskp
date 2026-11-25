//! The instanced scene builder (`instanced_scene.cpp`'s
//! `build_instanced_scene_raw`), plus the Python port's loose-edge curve
//! resources (`instanced_scene.py`'s `curve_resources`).

use std::collections::{BTreeMap, BTreeSet, HashMap};

use crate::curves::{prepare_def_curves, round6, solve_run_arc_indexed, INCHES_TO_M};
use crate::errors::SkpParseError;
use crate::face_groups::{
    build_local_face_groups, default_color, find_material, material_color, unpainted_style_colors,
    FaceColorKey, FaceGroupContext, GroupKey, TextureRegistry,
};
use crate::instanced_scene::{
    InstancedCurveResource, InstancedMeshResource, InstancedNode, InstancedScene, LocalCurve,
    LocalPrimitive, SceneBounds, IDENTITY_GLTF,
};
use crate::internal::{
    build_pages, emit_log, emit_progress, fold_scale, multiply_matrices, GeometryBuilder,
    RawMaterial, RawParsed,
};
use crate::model::{EntityId, Vec3};
use crate::observability::{LogLevel, ParseOptions, ParseStage, PROGRESS_INTERVAL};
use crate::scene::{CurveArc, GltfMaterial};
use crate::scene_build::{
    build_error, definition_name, instance_layer, is_generic_definition_name, max_depth_exceeded,
    max_instances_exceeded, name_override, recursive_definition, scene_dictionaries,
    summarize_face_layers, FaceLayerSummary, IDENTITY, MAX_COMPONENT_DEPTH, MAX_EXPANDED_INSTANCES,
};

const INCHES_TO_MM: f64 = 25.4;

/// One instance's 13-value SketchUp matrix (inches, Z-up) as a 16-value
/// column-major glTF matrix (metres, Y-up).
///
/// The axis change is the similarity transform C * M * C^-1 with
/// C: (x, y, z) -> (x, z, -y), so it composes correctly through nesting. The
/// scale term (index 12) is folded in first (openskp#444). A matrix shorter
/// than 9 values (the C++ reads past its end) reads as zeros.
fn to_gltf_matrix(raw: &[f64]) -> [f64; 16] {
    let m = fold_scale(raw);
    let at = |k: usize| m.get(k).copied().unwrap_or(0.0);
    let (a, b, c) = (at(0), at(1), at(2));
    let (d, e, f) = (at(3), at(4), at(5));
    let (g, h, i) = (at(6), at(7), at(8));
    let tx = if m.len() > 9 { m[9] } else { 0.0 };
    let ty = if m.len() > 10 { m[10] } else { 0.0 };
    let tz = if m.len() > 11 { m[11] } else { 0.0 };

    let (r00, r01, r02) = (a, c, -b);
    let (r10, r11, r12) = (g, i, -h);
    let (r20, r21, r22) = (-d, -f, e);

    [
        r00,
        r10,
        r20,
        0.0,
        r01,
        r11,
        r21,
        0.0,
        r02,
        r12,
        r22,
        0.0,
        tx * INCHES_TO_M,
        tz * INCHES_TO_M,
        -ty * INCHES_TO_M,
        1.0,
    ]
}

/// `a * b` of two column-major 4x4 matrices.
fn mul4(a: &[f64; 16], b: &[f64; 16]) -> [f64; 16] {
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

fn resource_key(
    defid: Option<EntityId>,
    front: &FaceColorKey,
    back: &FaceColorKey,
    inherited_material: Option<&RawMaterial>,
) -> String {
    let mut key = format!(
        "{}|{},{},{},{}",
        defid
            .map(|d| d.to_string())
            .unwrap_or_else(|| "ROOT".into()),
        front[0],
        front[1],
        front[2],
        front[3]
    );
    if back != front {
        let [r, g, b, a] = *back;
        key.push_str(&format!("/{r},{g},{b},{a}"));
    }
    if let Some(m) = inherited_material {
        key.push('|');
        key.push_str(&m.name);
        if m.colorized {
            key.push_str(&format!(":colorized:{}", m.colorize_type));
        }
    }
    key
}

type LocalBox = Option<([f64; 3], [f64; 3])>;

type LocalCurveTemplate = (Vec<Vec3>, bool, Option<String>, Option<CurveArc>);

struct Instancer<'a> {
    p: &'a RawParsed,
    o: &'a ParseOptions,
    style_colors: Option<(FaceColorKey, FaceColorKey)>,
    textures: TextureRegistry,
    gltf_materials: Vec<GltfMaterial>,
    /// Materials deduped globally, by the same key that groups faces.
    material_index_by_key: BTreeMap<GroupKey, usize>,
    mesh_resources: Vec<InstancedMeshResource>,
    resource_index_by_key: HashMap<String, usize>,
    curve_resources: Vec<InstancedCurveResource>,
    curve_resource_id_by_key: HashMap<String, Option<String>>,
    prepared_local_curves: HashMap<Option<EntityId>, Vec<LocalCurveTemplate>>,
    face_layers: HashMap<Option<EntityId>, FaceLayerSummary>,
    instance_counter: u64,
    active: BTreeSet<EntityId>,
}

impl<'a> Instancer<'a> {
    fn face_layer_summary(
        &mut self,
        defid: Option<EntityId>,
        b: &GeometryBuilder,
    ) -> &FaceLayerSummary {
        let p = self.p;
        self.face_layers
            .entry(defid)
            .or_insert_with(|| summarize_face_layers(p, b))
    }
    fn material_index(&mut self, key: &GroupKey) -> usize {
        let mut mat_key = *key;
        mat_key.unpainted_back = false;
        if let Some(&found) = self.material_index_by_key.get(&mat_key) {
            return found;
        }
        let idx = self.gltf_materials.len();
        let mut gm = GltfMaterial::default();
        gm.pbr_metallic_roughness.base_color_factor = [
            f64::from(mat_key.color[0]) / 255.0,
            f64::from(mat_key.color[1]) / 255.0,
            f64::from(mat_key.color[2]) / 255.0,
            f64::from(mat_key.color[3]) / 255.0,
        ];
        gm.pbr_metallic_roughness.base_color_texture = mat_key.texture_index;
        gm.double_sided = mat_key.double_sided;
        gm.colorized = mat_key.colorized && mat_key.texture_index.is_some();
        gm.colorize_type = if gm.colorized {
            mat_key.colorize_type
        } else {
            0
        };
        self.gltf_materials.push(gm);
        self.material_index_by_key.insert(mat_key, idx);
        idx
    }

    /// The local-space mesh resource for a definition in one rendering
    /// context, built once per (definition, effective fallback color,
    /// inherited material name): the only inputs that change what the face
    /// grouping produces. `None` when the definition has no faces.
    fn mesh_resource_for_builder(
        &mut self,
        builder: &GeometryBuilder,
        defname: &str,
        defid: Option<EntityId>,
        inherited_material: Option<&'a RawMaterial>,
        layer: &str,
    ) -> Result<Option<String>, SkpParseError> {
        if builder.faces.is_empty() {
            return Ok(None);
        }
        let (fallback, fallback_back) = match material_color(inherited_material) {
            Some(c) => (c, c),
            None => match self.style_colors {
                Some((front, back)) => (front, back),
                None => {
                    let majority = self
                        .face_layer_summary(defid, builder)
                        .majority_layer
                        .clone();
                    let color_layer = majority.as_deref().unwrap_or(layer);
                    let c = default_color(self.p, color_layer);
                    (c, c)
                }
            },
        };
        let p = self.p;
        let key = resource_key(defid, &fallback, &fallback_back, inherited_material);
        if let Some(&hit) = self.resource_index_by_key.get(&key) {
            return Ok(Some(self.mesh_resources[hit].id.clone()));
        }

        let groups = {
            let textures = &mut self.textures;
            let mut texture_index_for = |m: Option<&_>| textures.index_for(m);
            let mut ctx = FaceGroupContext {
                texture_index_for: &mut texture_index_for,
                fallback_color: fallback,
                fallback_back_color: Some(fallback_back),
                fallback_material: inherited_material,
            };
            build_local_face_groups(p, builder, &mut ctx).map_err(build_error)?
        };

        let mut primitives = Vec::new();
        for (group_key, g) in &groups {
            if g.tris.is_empty() {
                continue;
            }
            let count = g.verts.len();
            let mut prim = LocalPrimitive {
                positions: vec![0.0; count * 3],
                normals: vec![0.0; count * 3],
                uvs: vec![0.0; count * 2],
                unpainted_back: group_key.unpainted_back,
                ..Default::default()
            };
            for i in 0..count {
                // Local space: only the inches -> metres scale and the Z-up ->
                // Y-up axis swap; the node carries the placement.
                let v = g.verts[i];
                prim.positions[i * 3] = (v[0] * INCHES_TO_M) as f32;
                prim.positions[i * 3 + 1] = (v[2] * INCHES_TO_M) as f32;
                prim.positions[i * 3 + 2] = (-v[1] * INCHES_TO_M) as f32;
                prim.uvs[i * 2] = g.uvs[i][0];
                prim.uvs[i * 2 + 1] = g.uvs[i][1];

                let mut n = g.normals[i];
                let l = (n[0] * n[0] + n[1] * n[1] + n[2] * n[2]).sqrt();
                if l < 1e-6 {
                    n = [0.0, 0.0, 1.0];
                } else {
                    for x in &mut n {
                        *x /= l;
                    }
                }
                prim.normals[i * 3] = n[0] as f32;
                prim.normals[i * 3 + 1] = n[2] as f32;
                prim.normals[i * 3 + 2] = (-n[1]) as f32;
            }
            prim.indices.reserve(g.tris.len() * 3);
            for t in &g.tris {
                prim.indices.extend([t[0] as u32, t[1] as u32, t[2] as u32]);
            }
            prim.material_index = self.material_index(group_key);
            primitives.push(prim);
        }
        if primitives.is_empty() {
            return Ok(None);
        }

        let index = self.mesh_resources.len();
        let id = format!("mesh_{index}");
        self.mesh_resources.push(InstancedMeshResource {
            id: id.clone(),
            definition_id: defid,
            definition_name: defname.to_owned(),
            variant_key: key.clone(),
            primitives,
        });
        self.resource_index_by_key.insert(key, index);
        Ok(Some(id))
    }

    /// The local-space loose-edge resource for a definition in one inherited
    /// layer context (Python port's `curve_resource_for`), built once per
    /// (definition, layer). `None` when the definition has no loose edges.
    fn curve_resource_for(
        &mut self,
        builder: &GeometryBuilder,
        defname: &str,
        defid: Option<EntityId>,
        layer: &str,
    ) -> Option<String> {
        if !self.o.include_curve_sets {
            return None;
        }
        let uniform = self
            .face_layer_summary(defid, builder)
            .uniform_layer
            .clone();
        let effective_layer = if layer == "Layer0" {
            uniform.as_deref().unwrap_or(layer)
        } else {
            layer
        };
        let key = format!(
            "{}|{effective_layer}",
            defid
                .map(|d| d.to_string())
                .unwrap_or_else(|| "ROOT".into())
        );
        if let Some(hit) = self.curve_resource_id_by_key.get(&key) {
            return hit.clone();
        }
        let p = self.p;
        let templates = self.prepared_local_curves.entry(defid).or_insert_with(|| {
            let prepared = prepare_def_curves(p, builder);
            prepared
                .runs
                .into_iter()
                .map(|run| {
                    let pts: Vec<Vec3> = run
                        .local_verts
                        .iter()
                        .map(|&v| {
                            [
                                round6(v[0] * INCHES_TO_M),
                                round6(v[2] * INCHES_TO_M),
                                round6(-v[1] * INCHES_TO_M),
                            ]
                        })
                        .collect();
                    let arc = solve_run_arc_indexed(
                        builder,
                        &run.edge_ids,
                        None,
                        &pts,
                        run.closed,
                        Some(&prepared.curve_edge_counts),
                    );
                    (pts, run.closed, run.explicit_layer, arc)
                })
                .collect()
        });
        if templates.is_empty() {
            self.curve_resource_id_by_key.insert(key, None);
            return None;
        }
        let curves: Vec<LocalCurve> = templates
            .iter()
            .map(|(pts, closed, explicit_layer, arc)| LocalCurve {
                points_m: pts.clone(),
                closed: *closed,
                layer: explicit_layer
                    .clone()
                    .unwrap_or_else(|| effective_layer.to_owned()),
                arc: arc.clone(),
            })
            .collect();
        let id = format!("curve_{}", self.curve_resources.len());
        self.curve_resources.push(InstancedCurveResource {
            id: id.clone(),
            definition_id: defid,
            definition_name: defname.to_owned(),
            curves,
        });
        self.curve_resource_id_by_key.insert(key, Some(id.clone()));
        Some(id)
    }

    /// Walks a definition's placed instances, one node each. `current_matrix`
    /// is the accumulated SketchUp-space matrix, used ONLY for each node's
    /// absolute `position_mm`; the geometry never sees it.
    fn walk(
        &mut self,
        builder: &'a GeometryBuilder,
        current_matrix: &[f64],
        parent_layer: &str,
        inherited_material: Option<&'a RawMaterial>,
    ) -> Result<Vec<InstancedNode>, SkpParseError> {
        let p = self.p;
        let mut nodes = Vec::new();
        for i in &builder.instances {
            let new_matrix = multiply_matrices(current_matrix, &i.matrix);
            let l_name = instance_layer(p, self.o, i, parent_layer);
            let inst_material = find_material(p, i.material_id).or(inherited_material);

            let def_name = definition_name(p, i);
            let def_name_is_real = !def_name.is_empty() && !is_generic_definition_name(&def_name);
            let name_override = name_override(i);
            let inst_name = if !i.name.is_empty() {
                i.name.clone()
            } else if def_name_is_real {
                def_name
            } else {
                format!("Component_{}", i.ref_idx.unwrap_or(0))
            };
            let name_is_generated =
                !(name_override.is_some() || !i.name.is_empty() || def_name_is_real);

            let mut node = InstancedNode {
                name: name_override.unwrap_or(inst_name),
                name_is_generated,
                guid: i.ref_guid.clone(),
                layer: l_name.clone(),
                matrix: to_gltf_matrix(&i.matrix),
                position_mm: [
                    if new_matrix.len() > 9 {
                        new_matrix[9] * INCHES_TO_MM
                    } else {
                        0.0
                    },
                    if new_matrix.len() > 10 {
                        new_matrix[10] * INCHES_TO_MM
                    } else {
                        0.0
                    },
                    if new_matrix.len() > 11 {
                        new_matrix[11] * INCHES_TO_MM
                    } else {
                        0.0
                    },
                ],
                properties: i.properties.clone(),
                attribute_dictionaries: scene_dictionaries(i),
                hidden: Some(i.hidden),
                ..Default::default()
            };

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
                    node.definition_name = d.name.clone();
                    node.mesh_resource_id = self.mesh_resource_for_builder(
                        &d.builder,
                        &d.name,
                        Some(r),
                        inst_material,
                        &l_name,
                    )?;
                    node.children = self.walk(&d.builder, &new_matrix, &l_name, inst_material)?;
                    if l_name == "Layer0" && node.children.is_empty() {
                        let summary = self.face_layer_summary(Some(r), &d.builder);
                        if let Some(uniform) = &summary.uniform_layer {
                            node.layer = uniform.clone();
                        }
                    }
                    // After the children, as in the Python reference, so curve
                    // resource ids number the same there and here.
                    node.curve_resource_id =
                        self.curve_resource_for(&d.builder, &d.name, Some(r), &l_name);
                    self.active.remove(&r);
                }
            }

            nodes.push(node);
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
        Ok(nodes)
    }
}

/// Builds the instanced scene graph from a reader's raw output.
pub fn build_instanced_scene_raw(
    mut p: RawParsed,
    o: &ParseOptions,
) -> Result<InstancedScene, SkpParseError> {
    let style_colors = unpainted_style_colors(&p, &o.unpainted_faces)?;
    let layer_hidden = std::mem::take(&mut p.layer_hidden);
    let pages = build_pages(&p.pages, &p.layer_id_to_name);
    emit_log(
        o,
        LogLevel::Information,
        &format!(
            "Building instanced scene: {} definitions available",
            p.definitions.len()
        ),
    );
    crate::scene_build::validate_hierarchy(&p)?;
    let mut inst = Instancer {
        p: &p,
        o,
        style_colors,
        textures: TextureRegistry::from_options(o),
        gltf_materials: Vec::new(),
        material_index_by_key: BTreeMap::new(),
        mesh_resources: Vec::new(),
        resource_index_by_key: HashMap::new(),
        curve_resources: Vec::new(),
        curve_resource_id_by_key: HashMap::new(),
        prepared_local_curves: HashMap::new(),
        face_layers: HashMap::new(),
        instance_counter: 0,
        active: BTreeSet::new(),
    };
    let root_children = inst.walk(&p.root.builder, &IDENTITY, "Layer0", None)?;

    // Loose geometry drawn straight into the model becomes the root node's
    // own mesh (and curve) resource.
    let root_mesh_resource_id =
        inst.mesh_resource_for_builder(&p.root.builder, "ROOT_MODEL", None, None, "Layer0")?;
    let root_curve_resource_id =
        inst.curve_resource_for(&p.root.builder, "ROOT_MODEL", None, "Layer0");

    let scene_hierarchy = InstancedNode {
        name: "ROOT".into(),
        definition_name: "ROOT_MODEL".into(),
        layer: "Layer0".into(),
        matrix: IDENTITY_GLTF,
        hidden: Some(false),
        mesh_resource_id: root_mesh_resource_id,
        curve_resource_id: root_curve_resource_id,
        children: root_children,
        ..Default::default()
    };

    let Instancer {
        textures,
        gltf_materials,
        mesh_resources,
        curve_resources,
        instance_counter,
        ..
    } = inst;

    let bounds = scene_bounds(&scene_hierarchy, &mesh_resources);

    emit_log(
        o,
        LogLevel::Information,
        &format!(
            "Instanced scene build complete: {} instances, {} mesh resources",
            instance_counter,
            mesh_resources.len()
        ),
    );
    Ok(InstancedScene {
        bounds,
        scene_hierarchy,
        mesh_resources,
        gltf_materials,
        textures: textures.textures,
        layer_hidden,
        curve_resources,
        unread_root_entities: p.unread_root_entities,
        pages,
    })
}

/// Bounds of the scene AS PLACED: each resource's local box corners through
/// the accumulated node matrix (exact for axis-aligned bounds under an affine
/// transform).
fn scene_bounds(root: &InstancedNode, resources: &[InstancedMeshResource]) -> Option<SceneBounds> {
    let by_id: HashMap<&str, &InstancedMeshResource> =
        resources.iter().map(|r| (r.id.as_str(), r)).collect();
    let mut cache: HashMap<String, LocalBox> = HashMap::new();
    let mut local_box = |id: &str| -> LocalBox {
        if let Some(cached) = cache.get(id) {
            return *cached;
        }
        let mut found = None;
        if let Some(res) = by_id.get(id) {
            let mut lo = [f64::INFINITY; 3];
            let mut hi = [f64::NEG_INFINITY; 3];
            for prim in &res.primitives {
                let mut i = 0;
                while i + 2 < prim.positions.len() {
                    for k in 0..3 {
                        let v = f64::from(prim.positions[i + k]);
                        if v < lo[k] {
                            lo[k] = v;
                        }
                        if v > hi[k] {
                            hi[k] = v;
                        }
                    }
                    i += 3;
                }
            }
            if !lo[0].is_infinite() {
                found = Some((lo, hi));
            }
        }
        cache.insert(id.to_owned(), found);
        found
    };

    let mut b_min = [f64::INFINITY; 3];
    let mut b_max = [f64::NEG_INFINITY; 3];
    fn accumulate(
        node: &InstancedNode,
        parent: &[f64; 16],
        local_box: &mut dyn FnMut(&str) -> LocalBox,
        b_min: &mut [f64; 3],
        b_max: &mut [f64; 3],
    ) {
        let world = mul4(parent, &node.matrix);
        if let Some(id) = &node.mesh_resource_id {
            if let Some((lo, hi)) = local_box(id) {
                for c in 0..8 {
                    let x = if c & 1 != 0 { hi[0] } else { lo[0] };
                    let y = if c & 2 != 0 { hi[1] } else { lo[1] };
                    let z = if c & 4 != 0 { hi[2] } else { lo[2] };
                    let w = [
                        world[0] * x + world[4] * y + world[8] * z + world[12],
                        world[1] * x + world[5] * y + world[9] * z + world[13],
                        world[2] * x + world[6] * y + world[10] * z + world[14],
                    ];
                    for k in 0..3 {
                        if w[k] < b_min[k] {
                            b_min[k] = w[k];
                        }
                    }
                    for k in 0..3 {
                        if w[k] > b_max[k] {
                            b_max[k] = w[k];
                        }
                    }
                }
            }
        }
        for child in &node.children {
            accumulate(child, &world, local_box, b_min, b_max);
        }
    }
    accumulate(root, &IDENTITY_GLTF, &mut local_box, &mut b_min, &mut b_max);

    if b_min[0].is_infinite() {
        return None;
    }
    Some(SceneBounds {
        min: b_min,
        max: b_max,
        size: [
            b_max[0] - b_min[0],
            b_max[1] - b_min[1],
            b_max[2] - b_min[2],
        ],
        center: [
            (b_min[0] + b_max[0]) / 2.0,
            (b_min[1] + b_max[1]) / 2.0,
            (b_min[2] + b_max[2]) / 2.0,
        ],
    })
}
