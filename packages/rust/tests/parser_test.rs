mod common;

use std::collections::{BTreeMap, BTreeSet};

use common::{assert_near, fixture};
use openskp::{
    parse_skp, AttributeKind, Definition, EntityId, Material, ParseOptions, ParseStage, SkpFile,
    SkpModel, Vec3,
};

fn opts() -> ParseOptions {
    ParseOptions::default()
}

fn parse(name: &str) -> SkpModel {
    SkpFile::open(fixture(name))
        .unwrap()
        .parse(&opts())
        .unwrap()
}

fn expect_connected_normalized_loops(definition: &Definition) {
    for face in definition.faces.values() {
        for lp in &face.loops {
            assert!(!lp.is_empty());
            for i in 0..lp.len() {
                let use_ = &lp[i];
                let next_use = &lp[(i + 1) % lp.len()];
                assert!(use_.orientation == 1 || use_.orientation == -1);
                let edge = &definition.edges[&use_.edge_id];
                let next_edge = &definition.edges[&next_use.edge_id];
                let end = if use_.orientation == 1 {
                    edge.v2_id
                } else {
                    edge.v1_id
                };
                let next_start = if next_use.orientation == 1 {
                    next_edge.v1_id
                } else {
                    next_edge.v2_id
                };
                assert_eq!(end, next_start);
            }
        }
    }
}

// Style watermarks (style.xml item 5001): the "<MODEL SPACE>" separator is
// skipped, attributes are entity-decoded, and the image bytes come from the
// ZIP entry the watermark names. The style names its image relative to its own
// folder ("./logo.png"), the current style's "_1" working copy from the ZIP
// root ("watermarks/Logo.png").
#[test]
fn style_watermarks() {
    let model = parse("style_watermark.skp");
    assert_eq!(model.styles.len(), 2);
    for style in &model.styles {
        assert_eq!(style.watermarks.len(), 1);
        let w = &style.watermarks[0];
        assert_eq!(w.name, "Logo & Co");
        assert_eq!(
            w.image_path,
            if style.working_copy {
                "watermarks/Logo.png"
            } else {
                "./logo.png"
            }
        );
        assert_eq!(w.file_name, "logo.png");
        assert!(!w.attributes.contains_key("name"));
        assert_eq!(w.attributes["position"], "2");
        assert_eq!(w.attributes["alphaScale"], "0.75");
        assert_eq!(w.attributes["scale"], "0.3");
        assert_eq!(w.attributes["background"], "1");
        let image = w.image.as_ref().expect("watermark image");
        assert_eq!(image.len(), 70);
        assert_eq!(&image[1..4], b"PNG");
    }
}

#[test]
fn modern_untitled() {
    let model = parse("Untitled.skp");
    assert_eq!(model.version, "{25.0.575}");
    assert_eq!(model.units.as_deref(), Some("Millimeter"));
    assert_eq!(model.layers.len(), 14);
    assert_eq!(model.materials.len(), 15);
    assert_eq!(model.definitions.len(), 46);

    // Exact FILE order, not alphabetical.
    let expected_layer_order = [
        "Layer0",
        "BottomPlate",
        "TopPlate",
        "Stud",
        "Nog",
        "KingStud",
        "HeaderJackStud",
        "HeaderPlate1",
        "HeaderPlate2",
        "SillPlate1",
        "VerticalHeaderStud",
        "generic_frame",
        "dimension",
        "Hat Sections",
    ];
    let actual: Vec<&str> = model.layers.iter().map(|l| l.name.as_str()).collect();
    assert_eq!(actual, expected_layer_order);
    // None of this fixture's layers are hidden in its own Tags panel.
    assert!(model.layers.iter().all(|l| !l.hidden));

    let definition = &model.definitions[&66];
    assert_eq!(definition.name, "Group200#2");
    assert_eq!(definition.guid.len(), 32);
    assert_eq!(definition.vertices.len(), 136);
    assert_eq!(definition.edges.len(), 158);
    assert_eq!(definition.faces.len(), 26);
    assert!(!definition.is_image);
    assert!(!definition.always_faces_camera);

    let edge = definition.edges.values().next().unwrap();
    assert!(!edge.soft && !edge.smooth && !edge.hidden);

    let face = definition.faces.values().next().unwrap();
    assert!(!face.loops.is_empty() && !face.loops[0].is_empty());
    assert!(face.normal.is_some());
    assert!(face.back_material_id.is_none());
    assert!(face.uv_transform.is_none());
    assert!(face.uv_transform_back.is_none());
    // Every face in this fixture is visible - D307 reads the plain 0x06.
    assert!(definition.faces.values().all(|f| !f.hidden));
    expect_connected_normalized_loops(definition);

    let joined = model.material_by_id(26180).expect("material 26180");
    assert_eq!(joined.name, "*");
    assert_eq!(joined.id, Some(26180));
    let in_list = model.materials.iter().find(|m| m.name == "*").unwrap();
    assert!(std::ptr::eq(joined, in_list));

    let layer_material = model
        .materials
        .iter()
        .find(|m| m.name == "Layer_Layer0")
        .unwrap();
    assert_eq!(layer_material.transparency, 1.0);
    assert!(layer_material.id.is_none());
    assert!(layer_material.texture.is_none());
    assert!(!layer_material.colorized);
    assert_eq!(layer_material.colorize_type, 0);

    assert_eq!(model.styles.len(), 2);
    let s0 = &model.styles[0];
    assert_eq!(s0.name, "[Construction Documentation Style]");
    assert_eq!(s0.front_color, Some([255, 255, 255]));
    assert_eq!(s0.back_color, Some([164, 178, 187]));
    assert_eq!(
        s0.description,
        "[Default face colors. Profile Edges. White background.]"
    );
    // Every style.xml item is kept raw, keyed by SketchUp's item id.
    assert_eq!(s0.items.len(), 58);
    assert_eq!(s0.items[&1007].r#type, 4);
    assert_eq!(s0.items[&1007].value, "2");
    assert_eq!(s0.items[&2008].r#type, 7);
    assert_eq!(s0.items[&2008].value, "0.65000000000000002");
    // Item 5001 lists no watermarks here (only the "<MODEL SPACE>" separator).
    assert!(s0.watermarks.is_empty());
    // The current style is stored twice; model.dat's style catalog (0602 >
    // 7869) names it and its "_1" working copy.
    assert_eq!(s0.folder, "[Construction Documentation Style]");
    assert!(s0.active);
    assert!(!s0.working_copy);
    assert_eq!(
        model.styles[1].folder,
        "[Construction Documentation Style]_1"
    );
    assert!(!model.styles[1].active);
    assert!(model.styles[1].working_copy);

    // The view the model was saved with (FA01 > 34BC), independent of scenes.
    let camera = model.camera.expect("camera");
    assert_near(camera.eye[0], 452.09, 0.01);
    assert_near(camera.eye[1], -974.12, 0.01);
    assert_near(camera.eye[2], 367.93, 0.01);
    assert_near(camera.target[0], 226.24, 0.01);
    assert!(!camera.parallel);
    assert_eq!(camera.fov, 35.0);

    // Instance layer/properties from each instance's own D207 / DC05.
    let batten = model
        .root()
        .instances
        .iter()
        .find(|i| i.name == "BattenHatSection_1")
        .unwrap();
    assert_eq!(batten.layer, "Hat Sections");

    let w1 = model
        .root()
        .instances
        .iter()
        .find(|i| i.name == "W1")
        .unwrap();
    // "generator"/"profile" live under this SteelFramer-authored file's own
    // "steelframer-dict" dictionary, not "dynamic_attributes" - properties is
    // empty; attribute_dictionaries exposes every dictionary by name.
    assert!(w1.properties.is_empty());
    let steelframer = &w1.attribute_dictionaries["steelframer-dict"];
    assert_eq!(
        steelframer["generator"],
        "SteelFramer::Engine::PanelGenerator"
    );
    assert_eq!(steelframer["profile"], "362S200-43");
}

#[test]
fn modern_root_only() {
    let model = parse("SU_File.skp");
    assert_eq!(model.version, "{25.0.575}");
    assert!(model.definitions.is_empty());
    assert_eq!(model.root().name, "ROOT_MODEL");
    assert_eq!(model.layers.len(), 1);
    assert_eq!(model.layers[0].name, "Layer0");
    assert_eq!(model.materials.len(), 1);
    assert_eq!(model.materials[0].name, "Layer_Layer0");
}

#[test]
fn modern_root_only_scene() {
    let scene = SkpFile::open(fixture("SU_File.skp"))
        .unwrap()
        .build_scene(&opts())
        .unwrap();
    assert_eq!(scene.scene_hierarchy.name, "ROOT");
    assert_eq!(scene.scene_hierarchy.definition_name, "ROOT_MODEL");
    assert_eq!(scene.mesh_index.len(), 1);
    assert_eq!(
        scene.mesh_index.values().next().unwrap().definition_name,
        "ROOT_MODEL"
    );
}

// Dictionaries on the model itself, not on anything placed in it.
#[test]
fn modern_model_attribute_dictionaries() {
    let model = parse("SU_File.skp");
    let geo = &model.attribute_dictionaries["GeoReference"];
    assert_eq!(geo["LocationSource"], "Manual");
    assert_eq!(geo["Latitude"].kind(), AttributeKind::Float);
    assert_near(geo["Latitude"].number(), 40.0183, 1e-4);
    assert_eq!(geo["UsesGeoReferencing"].kind(), AttributeKind::Boolean);
    assert_eq!(geo["UsesGeoReferencing"].integer(), 0);
    assert_eq!(
        model.attribute_dictionaries["GSU_ContributorsInfo"]["VersionKey"].integer(),
        1000
    );
    assert_eq!(
        model.attribute_dictionaries["IfcGUIDs"]["ModelGUID"],
        "0Fvty1LOz2pP0l6Q3tLacy"
    );
}

#[test]
fn legacy_model_attribute_dictionaries_are_empty() {
    assert!(parse("capilla_quiroz_v17.skp")
        .attribute_dictionaries
        .is_empty());
}

#[test]
fn modern_instances_carry_unique_entity_ids() {
    let model = parse("Untitled.skp");
    let w1 = model
        .root()
        .instances
        .iter()
        .find(|i| i.name == "W1")
        .unwrap();
    assert_eq!(w1.id, Some(26135));

    let mut ids = BTreeSet::new();
    let mut collect = |definition: &Definition| {
        for instance in &definition.instances {
            let id = instance
                .id
                .unwrap_or_else(|| panic!("{} has no id", instance.name));
            assert!(ids.insert(id), "duplicate id {id}");
        }
    };
    collect(model.root());
    for definition in model.definitions.values() {
        collect(definition);
    }
    assert_eq!(ids.len(), 46);
}

#[test]
fn legacy_instances_have_no_entity_id() {
    let model = parse("gondola_v20.skp");
    for definition in model.definitions.values() {
        assert!(definition.instances.iter().all(|i| i.id.is_none()));
    }
    assert!(model.root().instances.iter().all(|i| i.id.is_none()));
}

#[test]
fn coedge_orientations_are_normalized_and_connected() {
    let model = parse("coedge_orientation_regression.skp");
    expect_connected_normalized_loops(model.root());
    for definition in model.definitions.values() {
        expect_connected_normalized_loops(definition);
    }
}

#[test]
fn legacy_matches_reference() {
    let file = SkpFile::open(fixture("capilla_quiroz_v17.skp")).unwrap();
    let model = file.parse(&opts()).unwrap();
    assert_eq!(model.version, "{17.0.18899}");
    assert!(model.units.is_none());
    for d in model.definitions.values() {
        assert!(d.instances.iter().all(|i| i.properties.is_empty()));
    }
    assert!(model
        .root()
        .instances
        .iter()
        .all(|i| i.properties.is_empty()));
    assert_eq!(model.definitions.len(), 2);

    let puerta = &model.definitions[&40];
    assert_eq!(puerta.name, "puerta");
    assert_eq!(puerta.faces.len(), 24);
    assert_eq!(puerta.edges.len(), 95);
    assert_eq!(puerta.vertices.len(), 64);
    assert_near(puerta.vertices[&45].x, 60.671292283583, 1e-9);
    assert_near(puerta.vertices[&45].y, 8.526512829121202e-14, 1e-18);
    assert_near(puerta.vertices[&45].z, 109.03580700984524, 1e-9);
    expect_connected_normalized_loops(puerta);

    let grada = &model.definitions[&395];
    assert_eq!(grada.name, "grada");
    assert_eq!(grada.faces.len(), 11);
    assert_eq!(grada.edges.len(), 30);
    assert_eq!(grada.vertices.len(), 20);

    assert!(model.root().construction_lines.is_empty());
    let cps = &model.root().construction_points;
    assert_eq!(cps.len(), 7);
    assert_near(cps[0].position[0], -44.69836289477159, 1e-9);
    assert_near(cps[0].position[1], 125.87449928268785, 1e-9);
    assert_near(cps[0].position[2], 90.15748031496064, 1e-9);
    assert_near(cps[6].position[0], 183.8551286073387, 1e-9);
    assert_near(cps[6].position[1], 125.95096929693227, 1e-9);
    assert_near(cps[6].position[2], 90.15748031496064, 1e-9);

    let expected_materials: BTreeSet<&str> = [
        "*1",
        "[0037_SandyBrown]",
        "[0048_PaleGoldenrod]",
        "[0050_LemonChiffon]",
        "[0062_YellowGreen]",
        "[0064_Chartreuse]",
        "[0069_LimeGreen]",
        "[0070_SpringGreen]",
        "[0097_DeepSkyBlue]",
        "[0102_RoyalBlue]",
        "[Color G03]",
        "[Polished Concrete New]",
        "[Polished Concrete Old]",
        "[Roofing Tile Spanish]",
        "[Translucent Glass Blue]",
        "[Translucent Glass Safety]",
    ]
    .into();
    assert_eq!(model.materials.len(), expected_materials.len());
    assert!(model
        .materials
        .iter()
        .all(|m: &Material| expected_materials.contains(m.name.as_str())));

    assert_eq!(model.layers.len(), 1);
    assert_eq!(model.layers[0].name, "Layer0");
    assert!(!model.layers[0].hidden);

    let mut minimum: Vec3 = [f64::INFINITY; 3];
    let mut maximum: Vec3 = [f64::NEG_INFINITY; 3];
    for definition in model.definitions.values() {
        for v in definition.vertices.values() {
            for (k, c) in [v.x, v.y, v.z].into_iter().enumerate() {
                minimum[k] = minimum[k].min(c);
                maximum[k] = maximum[k].max(c);
            }
        }
    }
    for (k, (lo, hi)) in [(0.0, 77.402), (0.0, 51.969), (0.0, 133.071)]
        .into_iter()
        .enumerate()
    {
        assert_near(minimum[k], lo, 0.01);
        assert_near(maximum[k], hi, 0.01);
    }
    assert_eq!(model.root().instances.len(), 3);
    assert!(model.root().instances.iter().all(|i| !i.hidden));
    assert!(puerta.faces.values().all(|f| !f.hidden));
    assert!(grada.faces.values().all(|f| !f.hidden));

    // 32 faces with a real uv_transform (positioned textures), 0 projected.
    let (mut with_uv, mut with_projected) = (0, 0);
    let mut count_uv = |d: &Definition| {
        for f in d.faces.values() {
            with_uv += usize::from(f.uv_transform.is_some());
            with_projected += usize::from(f.uv_projected);
        }
    };
    count_uv(model.root());
    for d in model.definitions.values() {
        count_uv(d);
    }
    assert_eq!(with_uv, 32);
    assert_eq!(with_projected, 0);
}

#[test]
fn legacy_scene_matches_reference() {
    let file = SkpFile::open(fixture("capilla_quiroz_v17.skp")).unwrap();
    let scene = file.build_scene(&opts()).unwrap();
    assert_eq!(scene.glb_primitives.len(), 21);
    assert_eq!(scene.mesh_index.len(), 21);
    assert_eq!(scene.gltf_materials.len(), 13);
    assert_eq!(scene.scene_hierarchy.children.len(), 3);
    let mut placed: Vec<&str> = scene
        .scene_hierarchy
        .children
        .iter()
        .map(|c| c.definition_name.as_str())
        .collect();
    placed.sort();
    assert_eq!(placed, ["grada", "grada", "puerta"]);
    for p in &scene.glb_primitives {
        assert_eq!(p.positions.len(), p.normals.len());
        assert_eq!(p.uvs.len(), p.positions.len() / 3 * 2);
        assert!(p.uvs.iter().all(|v| v.is_finite()));
        assert_eq!(p.indices.len() % 3, 0);
        assert!(p
            .indices
            .iter()
            .all(|&i| (i as usize) < p.positions.len() / 3));
    }
}

#[test]
fn modern_scene_matches_reference() {
    let scene = SkpFile::open(fixture("Untitled.skp"))
        .unwrap()
        .build_scene(&opts())
        .unwrap();
    assert_eq!(scene.scene_hierarchy.name, "ROOT");
    assert_eq!(scene.scene_hierarchy.definition_name, "ROOT_MODEL");
    assert!(!scene.scene_hierarchy.children.is_empty());
    assert_eq!(scene.mesh_index.len(), 43);
    assert_eq!(scene.glb_primitives.len(), 43);
    for (name, metadata) in &scene.mesh_index {
        assert!(!name.is_empty());
        assert!(!metadata.layer.is_empty());
    }
}

#[test]
fn invalid_inputs() {
    let error = SkpFile::from_buffer(vec![b'A'; 200])
        .parse(&opts())
        .unwrap_err();
    assert_eq!(error.stage(), Some(ParseStage::Header));

    let mut missing_zip = vec![0u8; 32];
    missing_zip[..4].copy_from_slice(&[0xff, 0xfe, 0xff, 0x0e]);
    let error = parse_skp(&missing_zip, &opts()).unwrap_err();
    assert_eq!(error.stage(), Some(ParseStage::ZipExtract));

    let mut truncated_zip = missing_zip.clone();
    truncated_zip[16..20].copy_from_slice(b"PK\x03\x04");
    let error = parse_skp(&truncated_zip, &opts()).unwrap_err();
    assert_eq!(error.stage(), Some(ParseStage::ZipExtract));
    assert_eq!(
        SkpFile::open("not-skp.txt").unwrap_err().kind(),
        std::io::ErrorKind::NotFound
    );
}

#[test]
fn materials_by_id_map() {
    let model = parse("Untitled.skp");
    let by_id = model.materials_by_id();
    assert!(!by_id.is_empty());
    for (&id, &mat) in &by_id {
        assert_eq!(mat.id, Some(id));
        assert!(std::ptr::eq(model.material_by_id(id).unwrap(), mat));
    }
}

#[test]
fn mesh_index_properties_are_populated() {
    let scene = SkpFile::open(fixture("Untitled.skp"))
        .unwrap()
        .build_scene(&opts())
        .unwrap();
    assert!(!scene.mesh_index.is_empty());
    let with_props = scene
        .mesh_index
        .values()
        .filter(|m| !m.properties.is_empty() || !m.attribute_dictionaries.is_empty())
        .count();
    assert!(with_props > scene.mesh_index.len() / 2);
}

#[test]
fn streaming_definitions_preserves_geometry() {
    let model = parse("Untitled.skp");
    assert_eq!(model.definitions.len(), 46);
    assert_eq!(model.layers.len(), 14);
    assert_eq!(model.materials.len(), 15);
    assert_eq!(model.dimensions.len(), 13);
}

#[test]
fn streaming_definitions_preserves_hierarchy() {
    let scene = SkpFile::open(fixture("Untitled.skp"))
        .unwrap()
        .build_instanced_scene(&opts())
        .unwrap();
    assert!(!scene.mesh_resources.is_empty());
    assert_eq!(scene.scene_hierarchy.name, "ROOT");
    assert!(!scene.scene_hierarchy.children.is_empty());
    assert_eq!(scene.gltf_materials.len(), 8);
    let total_faces: usize = scene
        .mesh_resources
        .iter()
        .flat_map(|r| &r.primitives)
        .map(|p| p.indices.len() / 3)
        .sum();
    // 16883: the post-openskp#285 overlapping-hole triangulation count.
    assert_eq!(total_faces, 16883);
}

#[test]
fn legacy_section_plane_text_dimension() {
    let model = parse("legacy_annotations.skp");
    assert_eq!(model.root().section_planes.len(), 1);
    let sp = &model.root().section_planes[0];
    assert_near(sp.plane[0], 0.0, 1e-6);
    assert_near(sp.plane[1], 0.0, 1e-6);
    assert_near(sp.plane[2], 1.0, 1e-6);
    assert_near(sp.plane[3], -30.0, 1e-6);
    assert!(!sp.hidden);
    assert_eq!(model.root().texts.len(), 1);
    assert_eq!(model.root().texts[0].text, "Hello");
    assert!(!model.root().texts[0].hidden);
    assert_eq!(model.root().dimensions.len(), 1);
}

// Python port extension: loose_edge_runs walks curve-grouped loose edges.
#[test]
fn loose_edge_runs_follow_curve_ids() {
    let model = parse("arcs_2021.skp");
    let root = model.root();
    let runs = root.loose_edge_runs();
    let mut per_curve: BTreeMap<EntityId, usize> = BTreeMap::new();
    for e in root.edges.values() {
        if let Some(c) = e.curve_id {
            *per_curve.entry(c).or_default() += 1;
        }
    }
    // The quarter arc is the fixture's only loose curve (the circle and the
    // half arc bound faces): one run covering all of its edges, open.
    assert_eq!(runs.len(), 1);
    let run = &runs[0];
    let cid = root.edges[&run.edge_ids[0]].curve_id.expect("curve id");
    assert!(run
        .edge_ids
        .iter()
        .all(|e| root.edges[e].curve_id == Some(cid)));
    assert_eq!(run.edge_ids.len(), per_curve[&cid]);
    assert_eq!(run.closed, root.arcs[&cid].is_full_circle());
    assert_eq!(run.vertex_ids.len(), run.edge_ids.len() + 1);
    // Consecutive edges share the walk's vertices.
    for (k, e) in run.edge_ids.iter().enumerate() {
        let edge = &root.edges[e];
        let ends = [edge.v1_id, edge.v2_id];
        assert!(ends.contains(&run.vertex_ids[k]) && ends.contains(&run.vertex_ids[k + 1]));
    }
}
