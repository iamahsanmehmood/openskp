// Baked and instanced scene building from synthetic raw parses
// (`scene_test.cpp`), plus the Python port's loose-edge curve sets on
// synthetic definitions.

use std::collections::BTreeMap;
use std::sync::Arc;

use openskp::instanced_scene_build::build_instanced_scene_raw;
use openskp::internal::{build_model, RawDefinition, RawFace, RawInstance, RawMaterial, RawParsed};
use openskp::scene_build::build_scene_raw;
use openskp::{
    CoEdge, GlbPrimitive, ParseOptions, ParseStage, ParsedAttrDictionaries, ParsedAttribute,
};

fn opts() -> ParseOptions {
    ParseOptions::default()
}

fn co(edge_id: i64) -> CoEdge {
    CoEdge {
        edge_id,
        orientation: 1,
    }
}

fn add_material(parsed: &mut RawParsed, id: i64, m: RawMaterial) {
    parsed.material_id_to_name.insert(id, m.name.clone());
    parsed.materials.insert(m.name.clone(), Arc::new(m));
}

fn triangle(parsed: &mut RawParsed, back: Option<i64>) {
    let b = &mut parsed.root.builder;
    b.vertices = [
        (1, [0.0, 0.0, 0.0]),
        (2, [1.0, 0.0, 0.0]),
        (3, [0.0, 1.0, 0.0]),
    ]
    .into();
    b.edges = [
        (10, (Some(1), Some(2))),
        (11, (Some(2), Some(3))),
        (12, (Some(3), Some(1))),
    ]
    .into();
    b.faces.insert(
        20,
        RawFace {
            loops: vec![vec![co(10), co(11), co(12)]],
            normal: [0.0, 0.0, 1.0],
            material_id: Some(100),
            back_material_id: back,
            ..Default::default()
        },
    );
}

fn triangle_with_materials(distinct_back: bool) -> RawParsed {
    let mut parsed = RawParsed::default();
    triangle(&mut parsed, Some(if distinct_back { 200 } else { 100 }));
    add_material(
        &mut parsed,
        100,
        RawMaterial {
            name: "front".into(),
            r: 10,
            g: 20,
            b: 30,
            transparency: 0.5,
            ..Default::default()
        },
    );
    add_material(
        &mut parsed,
        200,
        RawMaterial {
            name: "back".into(),
            r: 40,
            g: 50,
            b: 60,
            transparency: 0.25,
            ..Default::default()
        },
    );
    parsed
}

fn winding_normal_dot(primitive: &GlbPrimitive) -> f64 {
    let first = primitive.indices[0] as usize * 3;
    let second = primitive.indices[1] as usize * 3;
    let third = primitive.indices[2] as usize * 3;
    let p = |i: usize| f64::from(primitive.positions[i]);
    let a = [
        p(second) - p(first),
        p(second + 1) - p(first + 1),
        p(second + 2) - p(first + 2),
    ];
    let b = [
        p(third) - p(first),
        p(third + 1) - p(first + 1),
        p(third + 2) - p(first + 2),
    ];
    let cross = [
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    ];
    cross[0] * f64::from(primitive.normals[first])
        + cross[1] * f64::from(primitive.normals[first + 1])
        + cross[2] * f64::from(primitive.normals[first + 2])
}

#[test]
fn uses_one_double_sided_primitive_when_face_materials_match() {
    let scene = build_scene_raw(triangle_with_materials(false), &opts()).unwrap();
    assert_eq!(scene.glb_primitives.len(), 1);
    assert_eq!(scene.gltf_materials.len(), 1);
    assert!(scene.gltf_materials[0].double_sided);
    assert_eq!(
        scene.gltf_materials[0]
            .pbr_metallic_roughness
            .base_color_factor[3],
        128.0 / 255.0
    );
}

#[test]
fn preserves_distinct_front_and_back_face_materials() {
    let scene = build_scene_raw(triangle_with_materials(true), &opts()).unwrap();
    assert_eq!(scene.glb_primitives.len(), 2);
    assert_eq!(scene.gltf_materials.len(), 2);

    let (mut found_front, mut found_back) = (false, false);
    for primitive in &scene.glb_primitives {
        let material = &scene.gltf_materials[primitive.material_index];
        assert!(!material.double_sided);
        assert!(primitive.normals.len() >= 2);
        let factor = material.pbr_metallic_roughness.base_color_factor;
        if factor[0] == 10.0 / 255.0 {
            found_front = true;
            assert!(primitive.normals[1] > 0.0);
            assert_eq!(factor[3], 128.0 / 255.0);
        } else if factor[0] == 40.0 / 255.0 {
            found_back = true;
            assert!(primitive.normals[1] < 0.0);
            assert_eq!(factor[3], 64.0 / 255.0);
        }
    }
    assert!(found_front);
    assert!(found_back);
}

// Opacity can come from the RGBA color record's alpha byte as well as from
// the XML transparency; both must reach base_color_factor.
fn triangle_with_raw_alpha(alpha: i32) -> RawParsed {
    let mut parsed = RawParsed::default();
    triangle(&mut parsed, Some(100));
    add_material(
        &mut parsed,
        100,
        RawMaterial {
            name: "glass".into(),
            r: 40,
            g: 70,
            b: 100,
            a: alpha,
            ..Default::default()
        },
    );
    parsed
}

#[test]
fn propagates_raw_color_alpha_byte_into_base_color_factor() {
    let scene = build_scene_raw(triangle_with_raw_alpha(128), &opts()).unwrap();
    assert_eq!(scene.gltf_materials.len(), 1);
    let alpha = scene.gltf_materials[0]
        .pbr_metallic_roughness
        .base_color_factor[3];
    assert!((alpha - 128.0 / 255.0).abs() <= 1.0 / 255.0);
}

#[test]
fn leaves_fully_opaque_raw_alpha_byte_for_byte_unchanged() {
    let scene = build_scene_raw(triangle_with_raw_alpha(255), &opts()).unwrap();
    assert_eq!(scene.gltf_materials.len(), 1);
    assert_eq!(
        scene.gltf_materials[0]
            .pbr_metallic_roughness
            .base_color_factor[3],
        1.0
    );
}

const IDENTITY: [f64; 13] = [
    1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0,
];

fn placed_instance(
    instance_name: &str,
    def_name: &str,
    dicts: ParsedAttrDictionaries,
    properties: BTreeMap<String, String>,
) -> RawParsed {
    let mut parsed = RawParsed::default();
    parsed.definitions.insert(
        1,
        RawDefinition {
            name: def_name.into(),
            ..Default::default()
        },
    );
    parsed.root.builder.instances.push(RawInstance {
        name: instance_name.into(),
        ref_idx: Some(1),
        matrix: IDENTITY.to_vec(),
        properties,
        attribute_dicts: dicts,
        ..Default::default()
    });
    parsed
}

fn s(v: &str) -> ParsedAttribute {
    ParsedAttribute::String(v.into())
}

fn sketchup_and_plugin_dicts() -> ParsedAttrDictionaries {
    let mut dicts = ParsedAttrDictionaries::new();
    dicts.insert(
        "dynamic_attributes".into(),
        [("name".into(), s("FromDC")), ("width".into(), s("10"))].into(),
    );
    dicts.insert("SU_InstanceSet".into(), [("Owner".into(), s(""))].into());
    dicts.insert(
        "fbd-einfo".into(),
        [("name".into(), s("FromPlugin"))].into(),
    );
    dicts
}

fn string_map(pairs: &[(&str, &str)]) -> BTreeMap<String, String> {
    pairs
        .iter()
        .map(|(k, v)| (k.to_string(), v.to_string()))
        .collect()
}

// The model keeps every dictionary; scene nodes drop dynamic_attributes
// (already on properties) and SU_InstanceSet, and neither may override the
// display name.
#[test]
fn scene_omits_sketchup_dictionaries_the_model_keeps() {
    let properties = string_map(&[("name", "FromDC"), ("width", "10")]);
    let model = build_model(
        placed_instance(
            "GLB-12",
            "wall_def",
            sketchup_and_plugin_dicts(),
            properties.clone(),
        ),
        &opts(),
    );
    assert_eq!(model.root().instances.len(), 1);
    let inst = &model.root().instances[0];
    assert_eq!(
        inst.attribute_dictionaries["dynamic_attributes"]["name"],
        "FromDC"
    );
    assert_eq!(
        inst.attribute_dictionaries["dynamic_attributes"]["width"],
        "10"
    );
    assert_eq!(inst.attribute_dictionaries["SU_InstanceSet"]["Owner"], "");
    assert_eq!(
        inst.attribute_dictionaries["fbd-einfo"]["name"],
        "FromPlugin"
    );

    let scene = build_scene_raw(
        placed_instance(
            "GLB-12",
            "wall_def",
            sketchup_and_plugin_dicts(),
            properties.clone(),
        ),
        &opts(),
    )
    .unwrap();
    assert_eq!(scene.scene_hierarchy.children.len(), 1);
    let baked = &scene.scene_hierarchy.children[0];
    assert_eq!(baked.name, "FromPlugin");
    assert_eq!(baked.properties, properties);
    let expected: BTreeMap<String, BTreeMap<String, String>> = [(
        "fbd-einfo".to_string(),
        string_map(&[("name", "FromPlugin")]),
    )]
    .into();
    assert_eq!(baked.attribute_dictionaries, expected);

    let instanced = build_instanced_scene_raw(
        placed_instance(
            "GLB-12",
            "wall_def",
            sketchup_and_plugin_dicts(),
            properties.clone(),
        ),
        &opts(),
    )
    .unwrap();
    assert_eq!(instanced.scene_hierarchy.children.len(), 1);
    let node = &instanced.scene_hierarchy.children[0];
    assert_eq!(node.name, "FromPlugin");
    assert!(!node.name_is_generated);
    assert_eq!(node.properties, properties);
    assert_eq!(node.attribute_dictionaries, baked.attribute_dictionaries);
}

#[test]
fn dynamic_attributes_name_does_not_override_display_name() {
    let mut dicts = ParsedAttrDictionaries::new();
    dicts.insert(
        "dynamic_attributes".into(),
        [("name".into(), s("FromDC"))].into(),
    );
    dicts.insert("SU_InstanceSet".into(), [("Owner".into(), s(""))].into());
    let properties = string_map(&[("name", "FromDC")]);

    let scene = build_scene_raw(
        placed_instance("", "Group#1", dicts.clone(), properties.clone()),
        &opts(),
    )
    .unwrap();
    assert_eq!(scene.scene_hierarchy.children.len(), 1);
    assert_eq!(scene.scene_hierarchy.children[0].name, "Component_1");
    assert!(scene.scene_hierarchy.children[0]
        .attribute_dictionaries
        .is_empty());
    assert_eq!(scene.scene_hierarchy.children[0].properties, properties);

    let instanced =
        build_instanced_scene_raw(placed_instance("", "Group#1", dicts, properties), &opts())
            .unwrap();
    assert_eq!(instanced.scene_hierarchy.children.len(), 1);
    assert_eq!(instanced.scene_hierarchy.children[0].name, "Component_1");
    assert!(instanced.scene_hierarchy.children[0].name_is_generated);
    assert!(instanced.scene_hierarchy.children[0]
        .attribute_dictionaries
        .is_empty());
}

#[test]
fn keeps_winding_and_normals_aligned_for_mirrored_instances() {
    let mut parsed = triangle_with_materials(true);
    let builder = std::mem::take(&mut parsed.root.builder);
    parsed.definitions.insert(
        1,
        RawDefinition {
            name: "mirrored triangle".into(),
            builder,
            ..Default::default()
        },
    );
    parsed.root.builder.instances.push(RawInstance {
        ref_idx: Some(1),
        matrix: vec![
            -2.0, 0.0, 0.0, 0.0, 3.0, 0.0, 0.0, 0.0, 4.0, 0.0, 0.0, 0.0, 1.0,
        ],
        ..Default::default()
    });

    let scene = build_scene_raw(parsed, &opts()).unwrap();
    assert_eq!(scene.glb_primitives.len(), 2);
    for primitive in &scene.glb_primitives {
        assert!(winding_normal_dot(primitive) > 0.0);
    }
}

// ── Rust-port extras: names, paths, mesh backfill, recursion ─────────────

#[test]
fn meshes_take_the_placing_instances_name_and_path() {
    let mut parsed = triangle_with_materials(false);
    let builder = std::mem::take(&mut parsed.root.builder);
    parsed.definitions.insert(
        7,
        RawDefinition {
            name: "Panel".into(),
            builder,
            ..Default::default()
        },
    );
    parsed.root.builder.instances.push(RawInstance {
        name: "P1".into(),
        ref_idx: Some(7),
        matrix: vec![
            1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 10.0, 0.0, 0.0, 1.0,
        ],
        properties: string_map(&[("k", "v")]),
        ..Default::default()
    });
    let scene = build_scene_raw(parsed, &opts()).unwrap();
    assert_eq!(scene.mesh_index.len(), 1);
    let (geom, meta) = scene.mesh_index.iter().next().unwrap();
    assert_eq!(geom, "mesh_0_ROOT__P1_Layer0");
    assert_eq!(meta.name, "P1");
    assert_eq!(meta.path, "ROOT / P1");
    assert_eq!(meta.definition_name, "Panel");
    assert_eq!(meta.properties, string_map(&[("k", "v")]));
    assert!(
        (meta.position_mm[0] - 254.0).abs() < 1e-9
            && meta.position_mm[1] == 0.0
            && meta.position_mm[2] == 0.0
    );
    let node = &scene.scene_hierarchy.children[0];
    assert_eq!(node.path, "ROOT / P1");
    assert!(!node.name_is_generated);
    assert_eq!(scene.scene_hierarchy.path, "ROOT");
}

#[test]
fn a_recursive_definition_is_an_error() {
    let mut parsed = RawParsed::default();
    let mut d = RawDefinition {
        name: "Loop".into(),
        ..Default::default()
    };
    d.builder.instances.push(RawInstance {
        ref_idx: Some(1),
        matrix: IDENTITY.to_vec(),
        ..Default::default()
    });
    parsed.definitions.insert(1, d);
    parsed.root.builder.instances.push(RawInstance {
        ref_idx: Some(1),
        matrix: IDENTITY.to_vec(),
        ..Default::default()
    });

    let error = build_scene_raw(parsed.clone(), &opts()).unwrap_err();
    assert_eq!(error.stage(), Some(ParseStage::BuildScene));
    assert_eq!(error.definition_id(), Some(1));
    assert_eq!(error.message(), "Recursive component definition");
    let error = build_instanced_scene_raw(parsed, &opts()).unwrap_err();
    assert_eq!(error.definition_id(), Some(1));
}

#[test]
fn an_instanced_definition_is_shared_per_paint_color() {
    let mut parsed = triangle_with_materials(false);
    // Unpainted faces so the fallback (instance paint) color matters.
    parsed.root.builder.faces.get_mut(&20).unwrap().material_id = None;
    parsed
        .root
        .builder
        .faces
        .get_mut(&20)
        .unwrap()
        .back_material_id = None;
    let builder = std::mem::take(&mut parsed.root.builder);
    parsed.definitions.insert(
        1,
        RawDefinition {
            name: "Tri".into(),
            builder,
            ..Default::default()
        },
    );
    for material_id in [None, None, Some(200)] {
        parsed.root.builder.instances.push(RawInstance {
            ref_idx: Some(1),
            matrix: IDENTITY.to_vec(),
            material_id,
            ..Default::default()
        });
    }
    let scene = build_instanced_scene_raw(parsed, &opts()).unwrap();
    let ids: Vec<_> = scene
        .scene_hierarchy
        .children
        .iter()
        .map(|n| n.mesh_resource_id.clone().unwrap())
        .collect();
    assert_eq!(ids, ["mesh_0", "mesh_0", "mesh_1"]);
    assert_eq!(scene.mesh_resources[0].variant_key, "1|136,136,136,255");
    assert_eq!(scene.mesh_resources[1].variant_key, "1|40,50,60,64");
    assert_eq!(scene.scene_hierarchy.mesh_resource_id, None);
    let bounds = scene.bounds.unwrap();
    assert_eq!(bounds.min, [0.0, 0.0, f64::from(-0.0254f32)]);
    assert_eq!(bounds.max, [f64::from(0.0254f32), 0.0, 0.0]);
}

// ── loose-edge curve sets (Python port extension) ───────────────────────

/// A definition with two loose edges on layer 5 ("Lines") and one face-free
/// open polyline curve (edges 32-33, curve 77) on no layer.
fn loose_edges(parsed: &mut RawParsed, into_root: bool) {
    parsed.layer_id_to_name.insert(5, "Lines".into());
    let b = if into_root {
        &mut parsed.root.builder
    } else {
        &mut parsed
            .definitions
            .entry(3)
            .or_insert_with(|| RawDefinition {
                name: "Sketch".into(),
                ..Default::default()
            })
            .builder
    };
    b.vertices.extend([
        (1, [0.0, 0.0, 0.0]),
        (2, [100.0, 0.0, 0.0]),
        (3, [100.0, 100.0, 0.0]),
        (4, [0.0, 0.0, 50.0]),
        (5, [10.0, 0.0, 50.0]),
        (6, [20.0, 0.0, 50.0]),
    ]);
    b.edges.extend([
        (30, (Some(1), Some(2))),
        (31, (Some(2), Some(3))),
        (32, (Some(5), Some(6))),
        (33, (Some(4), Some(5))),
    ]);
    b.edge_layers.extend([(30, 5), (31, 5)]);
    b.edge_curves.extend([(32, 77), (33, 77)]);
}

#[test]
fn root_loose_edges_become_generated_curve_sets() {
    let mut parsed = RawParsed::default();
    loose_edges(&mut parsed, true);
    let scene = build_scene_raw(parsed, &opts()).unwrap();
    let names: Vec<(&str, &str, bool)> = scene
        .curve_sets
        .iter()
        .map(|c| (c.name.as_str(), c.layer.as_str(), c.name_is_generated))
        .collect();
    assert_eq!(
        names,
        [
            ("Lines_1", "Lines", true),
            ("Lines_2", "Lines", true),
            ("Layer0_1", "Layer0", true)
        ]
    );
    let first = &scene.curve_sets[0];
    assert_eq!(first.points_m, [[0.0, 0.0, 0.0], [2.54, 0.0, 0.0]]);
    assert_eq!(first.path, "ROOT");
    assert_eq!(first.definition_name, "ROOT_MODEL");
    // The curve's two edges chain end to end, from the first open end the
    // Python reference's walk meets (vertex 6).
    let polyline = &scene.curve_sets[2];
    assert_eq!(
        polyline.points_m,
        [[0.508, 1.27, 0.0], [0.254, 1.27, 0.0], [0.0, 1.27, 0.0]]
    );
    assert!(!polyline.closed);
    assert!(polyline.arc.is_none());
    assert!(scene.glb_primitives.is_empty());
}

#[test]
fn component_loose_edges_keep_the_instance_name_and_share_one_resource() {
    let mut parsed = RawParsed::default();
    loose_edges(&mut parsed, false);
    for x in [0.0, 1000.0] {
        parsed.root.builder.instances.push(RawInstance {
            name: "S".into(),
            ref_idx: Some(3),
            matrix: vec![
                1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, x, 0.0, 0.0, 1.0,
            ],
            ..Default::default()
        });
    }
    let scene = build_scene_raw(parsed.clone(), &opts()).unwrap();
    assert_eq!(scene.curve_sets.len(), 6);
    assert!(scene
        .curve_sets
        .iter()
        .all(|c| c.name == "S" && !c.name_is_generated && c.path == "ROOT / S"));
    assert_eq!(scene.curve_sets[3].points_m[0], [25.4, 0.0, 0.0]);

    let instanced = build_instanced_scene_raw(parsed, &opts()).unwrap();
    assert_eq!(instanced.curve_resources.len(), 1);
    let resource = &instanced.curve_resources[0];
    assert_eq!(
        (
            resource.id.as_str(),
            resource.definition_id,
            resource.definition_name.as_str()
        ),
        ("curve_0", Some(3), "Sketch")
    );
    let layers: Vec<&str> = resource.curves.iter().map(|c| c.layer.as_str()).collect();
    assert_eq!(layers, ["Lines", "Lines", "Layer0"]);
    for node in &instanced.scene_hierarchy.children {
        assert_eq!(node.curve_resource_id.as_deref(), Some("curve_0"));
        assert_eq!(node.mesh_resource_id, None);
    }
    assert_eq!(instanced.scene_hierarchy.curve_resource_id, None);
}

#[test]
fn face_bounded_edges_are_not_curve_sets() {
    let scene = build_scene_raw(triangle_with_materials(false), &opts()).unwrap();
    assert!(scene.curve_sets.is_empty());
    let instanced = build_instanced_scene_raw(triangle_with_materials(false), &opts()).unwrap();
    assert!(instanced.curve_resources.is_empty());
}
