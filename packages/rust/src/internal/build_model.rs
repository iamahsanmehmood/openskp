//! Shapes a [`RawParsed`] into the public [`SkpModel`] (`model.cpp`'s
//! `build_model`).

use std::collections::{BTreeMap, HashMap};
use std::sync::Arc;

use super::strconv::c_stoll;
use super::{RawDefinition, RawMaterial, RawParsed};
use crate::model::{
    ArcCurve, Definition, Dimension, Edge, EntityId, Face, Instance, Layer, Material, Page,
    SkpModel, Style, Texture, Vertex,
};
use crate::observability::{emit_log, LogLevel, ParseOptions};

/// Resolves a layer id held as text to its name; unknown ids keep the text.
fn resolve_layer(layer: &mut String, names: &BTreeMap<EntityId, String>, o: &ParseOptions) {
    if layer.is_empty() {
        return;
    }
    match c_stoll(layer) {
        Some(id) => {
            if let Some(name) = names.get(&id) {
                *layer = name.clone();
            }
        }
        None => emit_log(
            o,
            LogLevel::Debug,
            &format!("Failed to resolve layer id '{layer}' to a name"),
        ),
    }
}

fn resolve_layers(d: &mut RawDefinition, names: &BTreeMap<EntityId, String>, o: &ParseOptions) {
    for i in &mut d.builder.instances {
        resolve_layer(&mut i.layer, names, o);
    }
    for f in d.builder.faces.values_mut() {
        resolve_layer(&mut f.layer, names, o);
    }
    for cl in &mut d.builder.construction_lines {
        resolve_layer(&mut cl.layer, names, o);
    }
    for cp in &mut d.builder.construction_points {
        resolve_layer(&mut cp.layer, names, o);
    }
}

fn definition(
    id: EntityId,
    r: RawDefinition,
    layer_names: &BTreeMap<EntityId, String>,
) -> Definition {
    let b = r.builder;
    let mut d = Definition {
        id,
        guid: r.guid,
        name: r.name,
        always_faces_camera: r.always_faces_camera,
        shadows_face_sun: r.shadows_face_sun,
        is_image: r.is_image,
        is_group: r.is_group,
        ..Default::default()
    };
    for (&vid, v) in &b.vertices {
        d.vertices.insert(
            vid,
            Vertex {
                id: vid,
                x: v[0],
                y: v[1],
                z: v[2],
            },
        );
    }
    for (&eid, &(v1, v2)) in &b.edges {
        let flags = b.edge_flags.get(&eid).copied().unwrap_or(0);
        let layer_id = b.edge_layers.get(&eid).copied();
        let layer = match layer_id {
            Some(l) => layer_names
                .get(&l)
                .cloned()
                .unwrap_or_else(|| (l as u64).to_string()),
            None => String::new(),
        };
        d.edges.insert(
            eid,
            Edge {
                id: eid,
                v1_id: v1.unwrap_or(0),
                v2_id: v2.unwrap_or(0),
                soft: flags & 8 != 0,
                smooth: flags & 16 != 0,
                hidden: flags & 1 != 0,
                curve_id: b.edge_curves.get(&eid).copied(),
                layer,
                layer_id,
            },
        );
    }
    for (&cid, a) in &b.arc_curves {
        d.arcs.insert(
            cid,
            ArcCurve {
                center: a.center,
                normal: a.normal,
                x_axis: a.x_axis,
                y_axis: a.y_axis,
                start_angle: a.start_angle,
                end_angle: a.end_angle,
            },
        );
    }
    for (fid, f) in b.faces {
        d.faces.insert(
            fid,
            Face {
                id: fid,
                loops: f.loops,
                normal: Some(f.normal),
                material_id: f.material_id,
                back_material_id: f.back_material_id,
                uv_transform: f.uv_transform,
                uv_transform_back: f.uv_transform_back,
                uv_projected: f.uv_projected,
                uv_projected_back: f.uv_projected_back,
                hidden: f.hidden,
                layer: f.layer,
                layer_id: f.layer_id,
            },
        );
    }
    d.instances = b
        .instances
        .into_iter()
        .map(|i| Instance {
            name: i.name,
            ref_idx: i.ref_idx,
            guid: i.ref_guid,
            matrix: i.matrix,
            layer: i.layer,
            properties: i.properties,
            attribute_dictionaries: i.attribute_dicts,
            material_id: i.material_id,
            hidden: i.hidden,
            id: i.id,
        })
        .collect();
    d.section_planes = b.section_planes;
    d.texts = b.texts;
    d.dimensions = b.dimensions;
    d.construction_lines = b.construction_lines;
    d.construction_points = b.construction_points;
    d
}

/// Builds the public model from a reader's raw output.
pub fn build_model(p: RawParsed, o: &ParseOptions) -> SkpModel {
    let mut p = p;
    let mut m = SkpModel {
        version: std::mem::take(&mut p.version),
        units: p.units.take(),
        attribute_dictionaries: std::mem::take(&mut p.model_attribute_dicts),
        ..Default::default()
    };
    let layer_names = std::mem::take(&mut p.layer_id_to_name);
    for (id, mut d) in std::mem::take(&mut p.definitions) {
        resolve_layers(&mut d, &layer_names, o);
        m.definitions.insert(id, definition(id, d, &layer_names));
    }
    let mut root = std::mem::take(&mut p.root);
    resolve_layers(&mut root, &layer_names, o);
    m.root = definition(0, root, &layer_names);

    for name in &p.layer_order {
        let Some(&color) = p.layer_colors.get(name) else {
            continue;
        };
        let hidden = p.layer_hidden.get(name).copied().unwrap_or(false);
        let attribute_dictionaries = p
            .layer_attribute_dictionaries
            .get(name)
            .cloned()
            .unwrap_or_default();
        m.layers.push(Layer {
            name: name.clone(),
            color,
            hidden,
            attribute_dictionaries,
        });
    }
    // Pages (saved scenes): hidden layer ids resolve to names; unknown ids
    // (stale refs) are dropped.
    for pg in &p.pages {
        m.pages.push(Page {
            name: pg.name.clone(),
            eye: pg.eye,
            target: pg.target,
            up: pg.up,
            fov: pg.fov,
            parallel: pg.parallel,
            ortho_height: pg.ortho_height,
            hidden_layers: pg
                .hidden_layer_ids
                .iter()
                .filter_map(|id| layer_names.get(id).cloned())
                .collect(),
            selected: pg.selected,
        });
    }
    m.camera = p.camera;
    // Model-level linear dimensions (VFF; world space).
    for dm in &p.dimensions {
        m.dimensions.push(Dimension {
            text: dm.text.clone(),
            hidden: false,
            a: Some(dm.a),
            b: Some(dm.b),
            offset: dm.offset,
            plane_x: dm.plane_x,
            normal: dm.normal,
            offset_vector: dm.offset_vector,
        });
    }

    // Materials in name order; an id resolves through the name map first,
    // then the folder map, and only to a material the name map holds.
    let named: Vec<(String, Arc<RawMaterial>)> =
        std::mem::take(&mut p.materials).into_iter().collect();
    let raw_index: HashMap<*const RawMaterial, usize> = named
        .iter()
        .enumerate()
        .map(|(i, (_, r))| (Arc::as_ptr(r), i))
        .collect();
    let mut first_id: Vec<Option<EntityId>> = vec![None; named.len()];
    let by_key: HashMap<&str, &Arc<RawMaterial>> =
        named.iter().map(|(k, r)| (k.as_str(), r)).collect();
    for (&id, name) in &p.material_id_to_name {
        let r = by_key
            .get(name.as_str())
            .copied()
            .or_else(|| p.materials_by_folder.get(name));
        if let Some(&i) = r.and_then(|r| raw_index.get(&Arc::as_ptr(r))) {
            if first_id[i].is_none() {
                first_id[i] = Some(id);
            }
            m.material_indices.insert(id, i);
        }
    }
    drop(by_key);
    p.materials_by_folder.clear();
    for ((_, r), id) in named.into_iter().zip(first_id) {
        let r = Arc::try_unwrap(r).unwrap_or_else(|shared| (*shared).clone());
        m.materials.push(Material {
            name: r.name,
            color: [r.r as u8, r.g as u8, r.b as u8, r.a as u8],
            transparency: r.transparency,
            id,
            texture: r.texture.map(|t| Texture {
                filename: t.filename,
                width: t.x_scale,
                height: t.y_scale,
                data: t.data,
            }),
            colorized: r.colorized,
            colorize_type: r.colorize_type,
        });
    }
    for s in std::mem::take(&mut p.styles) {
        m.styles.push(Style {
            name: s.name,
            description: s.description,
            front_color: s.front_color,
            back_color: s.back_color,
            items: s.items,
            watermarks: s.watermarks,
            folder: s.folder,
            active: s.active,
            working_copy: s.working_copy,
            modified: s.modified,
        });
    }
    m
}
