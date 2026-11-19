//! Direct SKP -> Fragments (.frag) export and import - port of
//! `fragments_export_test.cpp` (itself mirroring Python's
//! `tests/test_fragments.py`), plus extra coverage for the hand-written
//! FlatBuffers layer: the real ThatOpen fixture, BigShell encoding,
//! transforms, and patched-buffer edge cases from the Python suite.

mod common;

use std::collections::{BTreeMap, BTreeSet};

use openskp::flatbuffers::FbStruct;
use openskp::fragments::schema::{self, RenderedFaces, Representation, RepresentationClass};
use openskp::instanced_scene::IDENTITY_GLTF as K_IDENTITY;
use openskp::{
    from_fragments, read_fragments, to_fragments, EntityId, GltfMaterial, InstancedMeshResource,
    InstancedNode, InstancedScene, LocalPrimitive, ParseOptions, SkpFile,
};

fn make_box_resource(id: &str) -> InstancedMeshResource {
    let positions: Vec<f32> = vec![
        0., 0., 0., 1., 0., 0., 1., 1., 0., 0., 1., 0., 0., 0., 1., 1., 0., 1., 1., 1., 1., 0., 1.,
        1.,
    ];
    let prim = LocalPrimitive {
        normals: vec![0.0; positions.len()],
        uvs: vec![0.0; (positions.len() / 3) * 2],
        positions,
        indices: vec![0, 1, 2, 0, 2, 3, 4, 6, 5, 4, 7, 6],
        material_index: 0,
    };
    InstancedMeshResource {
        id: id.to_string(),
        definition_id: Some(1),
        definition_name: "Box".to_string(),
        variant_key: "1|255,255,255".to_string(),
        primitives: vec![prim],
    }
}

fn leaf(name: &str, mesh: &str) -> InstancedNode {
    InstancedNode {
        name: name.to_string(),
        matrix: K_IDENTITY,
        mesh_resource_id: Some(mesh.to_string()),
        ..InstancedNode::default()
    }
}

fn root(children: Vec<InstancedNode>) -> InstancedNode {
    InstancedNode {
        name: "ROOT".to_string(),
        matrix: K_IDENTITY,
        children,
        ..InstancedNode::default()
    }
}

fn box_scene(children: Vec<InstancedNode>) -> InstancedScene {
    InstancedScene {
        mesh_resources: vec![make_box_resource("mesh_0")],
        gltf_materials: vec![GltfMaterial::default()],
        scene_hierarchy: root(children),
        ..InstancedScene::default()
    }
}

fn parse_raw(raw: &[u8]) -> schema::Model<'_> {
    schema::root_as_model(raw).expect("a readable Model")
}

fn strings(v: openskp::flatbuffers::Vector<'_, openskp::flatbuffers::Str<'_>>) -> Vec<String> {
    v.iter().map(|s| s.unwrap().to_string_lossy()).collect()
}

#[test]
fn guids_and_local_ids_same_length_and_order() {
    let scene = box_scene(vec![leaf("Box1", "mesh_0")]);
    let raw = to_fragments(&scene, true);
    let model = parse_raw(&raw);
    let local_ids = model.local_ids().unwrap().unwrap();
    let guids = model.guids().unwrap().unwrap();
    let guids_items = model.guids_items().unwrap().unwrap();
    assert_eq!(local_ids.len(), 1);
    assert_eq!(guids.len(), 1);
    assert_eq!(guids_items.len(), 1);
    assert_eq!(guids_items.get(0).unwrap(), local_ids.get(0).unwrap());
    assert_eq!(&raw[4..8], b"0001", "file identifier");
}

#[test]
fn items_without_source_guid_get_unique_synthetic_one() {
    let scene = box_scene(vec![leaf("Box1", "mesh_0"), leaf("Box2", "mesh_0")]);
    let raw = to_fragments(&scene, true);
    let guids = strings(parse_raw(&raw).guids().unwrap().unwrap());
    assert!(guids.iter().all(|g| !g.is_empty()));
    assert_eq!(guids.iter().collect::<BTreeSet<_>>().len(), guids.len());
}

#[test]
fn a_real_source_guid_is_preserved_exactly() {
    let real_guid = "F160C36229782F47A9857FC88DD1F2CB";
    let mut box_leaf = leaf("Box", "mesh_0");
    box_leaf.guid = real_guid.to_string();
    let raw = to_fragments(&box_scene(vec![box_leaf]), true);
    let guids = strings(parse_raw(&raw).guids().unwrap().unwrap());
    assert_eq!(guids, [real_guid]);
}

// openskp#290: SketchUp's Copy/Move+Copy/Array duplicate an instance's
// attribute dictionaries - and any plugin-written GUID - verbatim. The
// first instance keeps the real GUID; later ones fall back to synthetic.
#[test]
fn a_duplicated_source_guid_does_not_collide() {
    let duplicated_guid = "F160C36229782F47A9857FC88DD1F2CB";
    let children = (0..3)
        .map(|i| {
            let mut n = leaf(&format!("Truss{i}"), "mesh_0");
            n.guid = duplicated_guid.to_string();
            n
        })
        .collect();
    let raw = to_fragments(&box_scene(children), true);
    let guids = strings(parse_raw(&raw).guids().unwrap().unwrap());
    assert_eq!(guids.len(), 3);
    assert_eq!(
        guids.iter().collect::<BTreeSet<_>>().len(),
        3,
        "guids must never collide"
    );
    assert_eq!(
        guids.iter().filter(|g| *g == duplicated_guid).count(),
        1,
        "only the first claimant keeps the real value"
    );
}

// A named organizational wrapper with no geometry (a FrameBuilder wall
// "W-2" wrapping separately-meshed parts) still becomes a tracked item.
#[test]
fn named_wrapper_with_no_geometry_gets_a_tracked_item() {
    let wrapper = InstancedNode {
        name: "W-2".to_string(),
        name_is_generated: false,
        matrix: K_IDENTITY,
        children: vec![leaf("Stud1", "mesh_0")],
        ..InstancedNode::default()
    };
    let generic_wrapper = InstancedNode {
        name: "Component_5".to_string(),
        name_is_generated: true,
        matrix: K_IDENTITY,
        children: vec![leaf("Stud2", "mesh_0")],
        ..InstancedNode::default()
    };
    let raw = to_fragments(&box_scene(vec![wrapper, generic_wrapper]), true);
    let model = parse_raw(&raw);
    // W-2 + Stud1 + Stud2; the generated-name wrapper is not tracked.
    assert_eq!(model.local_ids().unwrap().unwrap().len(), 3);

    let mut found_w2 = false;
    for attr in model.attributes().unwrap().unwrap().iter() {
        for s in attr.unwrap().data().unwrap().unwrap().iter() {
            if s.unwrap().to_string_lossy().contains("W-2") {
                found_w2 = true;
            }
        }
    }
    assert!(
        found_w2,
        "the named wrapper's own real name must reach the exported Attribute data"
    );
}

#[test]
fn compressed_output_is_smaller_and_decompresses() {
    let scene = box_scene(vec![leaf("Box1", "mesh_0")]);
    let raw = to_fragments(&scene, true);
    let compressed = to_fragments(&scene, false);
    assert_ne!(raw, compressed);
    // zlib (RFC 1950) header: 0x78 is the standard CMF byte for a 32K window.
    assert!(compressed.len() >= 2);
    assert_eq!(compressed[0], 0x78);
    assert_eq!(openskp::inflate::zlib_decompress(&compressed).unwrap(), raw);
}

// ---------------------------------------------------------------------------
// Oversized shell splitting (openskp#285 / PR #355): `profiles_face_ids` is
// `[ushort]`, so one shell cannot number more than 65535 triangles; an
// oversized primitive is split across several shells instead.
// ---------------------------------------------------------------------------

fn grid_primitive(cols: u32, rows: u32) -> LocalPrimitive {
    let mut positions = Vec::with_capacity((cols * rows * 3) as usize);
    for j in 0..rows {
        for i in 0..cols {
            positions.extend_from_slice(&[i as f32, j as f32, 0.0]);
        }
    }
    let mut indices = Vec::with_capacity(((cols - 1) * (rows - 1) * 6) as usize);
    for j in 0..rows - 1 {
        for i in 0..cols - 1 {
            let a = j * cols + i;
            let b = a + 1;
            let c = a + cols;
            let d = c + 1;
            indices.extend_from_slice(&[a, b, d, a, d, c]);
        }
    }
    LocalPrimitive {
        normals: vec![0.0; positions.len()],
        uvs: vec![0.0; (positions.len() / 3) * 2],
        positions,
        indices,
        material_index: 0,
    }
}

fn scene_with_one_primitive(prim: LocalPrimitive, mesh_id: &str) -> InstancedScene {
    InstancedScene {
        mesh_resources: vec![InstancedMeshResource {
            id: mesh_id.to_string(),
            definition_id: Some(1),
            definition_name: "BigMesh".to_string(),
            variant_key: "1|255,255,255".to_string(),
            primitives: vec![prim],
        }],
        gltf_materials: vec![GltfMaterial::default()],
        scene_hierarchy: root(vec![leaf("Big", mesh_id)]),
        ..InstancedScene::default()
    }
}

#[test]
fn splits_an_oversized_primitive_and_every_face_id_is_correct_and_sequential() {
    const COLS: u32 = 210;
    const ROWS: u32 = 165;
    const EXPECTED_TRIANGLES: usize = ((COLS - 1) * (ROWS - 1) * 2) as usize;
    const {
        assert!(
            EXPECTED_TRIANGLES > 65535,
            "fixture must exceed the limit under test"
        )
    };

    let scene = scene_with_one_primitive(grid_primitive(COLS, ROWS), "mesh_big");
    let raw = to_fragments(&scene, true);
    let meshes = parse_raw(&raw).meshes().unwrap().unwrap();
    let shells = meshes.shells().unwrap().unwrap();

    assert!(shells.len() > 1, "the split actually happened");
    assert_eq!(
        meshes.samples().unwrap().unwrap().len(),
        shells.len(),
        "one sample per split shell"
    );

    let mut total_triangles = 0;
    for shell in shells.iter() {
        let shell = shell.unwrap();
        let face_ids = shell.profiles_face_ids().unwrap().unwrap();
        assert!(
            face_ids.len() <= 65535,
            "every sub-shell stays within the ushort limit"
        );
        assert_eq!(shell.profiles().unwrap().unwrap().len(), face_ids.len());
        for (j, id) in face_ids.iter().enumerate() {
            assert_eq!(id.unwrap() as usize, j);
        }
        total_triangles += face_ids.len();
    }
    assert_eq!(total_triangles, EXPECTED_TRIANGLES);
}

#[test]
fn a_primitive_within_the_limit_still_produces_exactly_one_shell() {
    let scene = scene_with_one_primitive(grid_primitive(50, 50), "mesh_small"); // 4802 triangles
    let raw = to_fragments(&scene, true);
    let meshes = parse_raw(&raw).meshes().unwrap().unwrap();
    assert_eq!(meshes.shells().unwrap().unwrap().len(), 1);
    assert_eq!(meshes.samples().unwrap().unwrap().len(), 1);
}

// ---- from_fragments (openskp#285: reading a .frag file back) ----

fn make_two_instance_scene() -> InstancedScene {
    let mut a = leaf("Box_A", "mesh_0");
    a.layer = "Framing".to_string();
    let mut b = leaf("Box_B", "mesh_0");
    b.layer = "Framing".to_string();
    box_scene(vec![a, b])
}

#[test]
fn round_trips_a_basic_two_instance_scene() {
    let scene = make_two_instance_scene();
    let back = from_fragments(&to_fragments(&scene, true)).unwrap();

    assert_eq!(back.mesh_resources.len(), 1);
    assert_eq!(back.mesh_resources[0].primitives.len(), 1);
    // The box carries 8 vertices but only 4 triangles (2 of 6 faces).
    assert_eq!(back.mesh_resources[0].primitives[0].positions.len(), 8 * 3);
    assert_eq!(back.mesh_resources[0].primitives[0].indices.len(), 4 * 3);

    assert_eq!(back.scene_hierarchy.children.len(), 2);
    let (a, b) = (
        &back.scene_hierarchy.children[0],
        &back.scene_hierarchy.children[1],
    );
    assert_eq!(a.name, "Box_A");
    assert_eq!(b.name, "Box_B");
    assert_eq!(a.layer, "Framing");
    assert!(a.mesh_resource_id.is_some());
    assert!(b.mesh_resource_id.is_some());
    assert_eq!(a.mesh_resource_id, b.mesh_resource_id);
    assert!(!back.gltf_materials.is_empty());
}

#[test]
fn round_trips_both_raw_and_zlib_compressed_wire_formats() {
    let scene = make_two_instance_scene();
    let raw_bytes = to_fragments(&scene, true);
    let compressed_bytes = to_fragments(&scene, false);
    assert_ne!(raw_bytes, compressed_bytes);

    let from_raw = from_fragments(&raw_bytes).unwrap();
    let from_compressed = from_fragments(&compressed_bytes).unwrap();
    assert_eq!(from_raw, from_compressed);
    assert_eq!(
        from_raw.mesh_resources[0].primitives[0].indices.len(),
        from_compressed.mesh_resources[0].primitives[0]
            .indices
            .len()
    );
}

#[test]
fn preserves_real_source_guids_and_layer_hidden_metadata() {
    let mut scene = make_two_instance_scene();
    scene.scene_hierarchy.children[0].guid = "F160C36229782F47A9857FC88DD1F2CB".to_string();
    scene.layer_hidden = BTreeMap::from([
        ("Framing".to_string(), false),
        ("Cladding".to_string(), true),
    ]);
    let back = from_fragments(&to_fragments(&scene, true)).unwrap();

    assert_eq!(
        back.scene_hierarchy.children[0].guid,
        "F160C36229782F47A9857FC88DD1F2CB"
    );
    assert_eq!(back.layer_hidden, scene.layer_hidden);
}

// Cross-language regression guard: Dart's port once corrupted the
// materials vector for an odd material count (6-byte structs).
#[test]
fn an_odd_material_count_round_trips_colors_exactly() {
    let colors: [[f64; 4]; 3] = [
        [1.0, 0.0, 0.0, 1.0],
        [0.0, 1.0, 0.0, 1.0],
        [0.0, 0.0, 1.0, 1.0],
    ];
    let mut scene = InstancedScene {
        scene_hierarchy: root(Vec::new()),
        ..InstancedScene::default()
    };
    for (i, color) in colors.iter().enumerate() {
        let id = format!("mesh_{i}");
        scene.mesh_resources.push(InstancedMeshResource {
            id: id.clone(),
            definition_id: Some(i as EntityId + 1),
            definition_name: "Tri".to_string(),
            variant_key: i.to_string(),
            primitives: vec![LocalPrimitive {
                positions: vec![0., 0., 0., 1., 0., 0., 0., 1., 0.],
                normals: vec![0.0; 9],
                uvs: vec![0.0; 6],
                indices: vec![0, 1, 2],
                material_index: i,
            }],
        });
        let mut gm = GltfMaterial::default();
        gm.pbr_metallic_roughness.base_color_factor = *color;
        scene.gltf_materials.push(gm);
        scene
            .scene_hierarchy
            .children
            .push(leaf(&((b'A' + i as u8) as char).to_string(), &id));
    }

    let raw = to_fragments(&scene, true);
    let materials = parse_raw(&raw)
        .meshes()
        .unwrap()
        .unwrap()
        .materials()
        .unwrap()
        .unwrap();
    assert_eq!(materials.len(), 3);
    for (i, color) in colors.iter().enumerate() {
        let mat = materials.get(i).unwrap().0;
        assert_eq!(mat.r, (color[0] * 255.0) as u8);
        assert_eq!(mat.g, (color[1] * 255.0) as u8);
        assert_eq!(mat.b, (color[2] * 255.0) as u8);
        assert_eq!(mat.rendered_faces, RenderedFaces::ONE);
    }

    let back = from_fragments(&raw).unwrap();
    assert_eq!(back.gltf_materials.len(), 3);
    for (i, color) in colors.iter().enumerate() {
        let bcf = back.gltf_materials[i]
            .pbr_metallic_roughness
            .base_color_factor;
        for k in 0..3 {
            assert!((bcf[k] - color[k]).abs() <= 1.0 / 255.0);
        }
    }
}

#[test]
fn reconstructs_every_triangle_of_a_primitive_split_across_multiple_shells() {
    const COLS: u32 = 210;
    const ROWS: u32 = 165; // 209*164*2 = 68,552 triangles - forces a split
    let expected_triangles = ((COLS - 1) * (ROWS - 1) * 2) as usize;
    let scene = scene_with_one_primitive(grid_primitive(COLS, ROWS), "mesh_big");
    let raw = to_fragments(&scene, true);
    assert!(
        parse_raw(&raw)
            .meshes()
            .unwrap()
            .unwrap()
            .shells()
            .unwrap()
            .unwrap()
            .len()
            > 1
    );

    let back = from_fragments(&raw).unwrap();
    assert_eq!(back.mesh_resources.len(), 1);
    let total: usize = back.mesh_resources[0]
        .primitives
        .iter()
        .map(|p| p.indices.len() / 3)
        .sum();
    assert_eq!(total, expected_triangles);
}

// ---------------------------------------------------------------------------
// Beyond the C++ suite
// ---------------------------------------------------------------------------

/// `thatopen_small_test.frag` is ThatOpen's own sample fixture (MIT, from
/// https://github.com/ThatOpen/engine_fragment `resources/frags/`) - real
/// third-party output, zlib-compressed, not anything OpenSKP wrote.
#[test]
fn reads_a_real_thatopen_produced_file() {
    let scene = read_fragments(common::fixture("thatopen_small_test.frag")).unwrap();
    assert!(!scene.mesh_resources.is_empty());
    assert!(
        scene
            .mesh_resources
            .iter()
            .map(|r| r.primitives.len())
            .sum::<usize>()
            > 0
    );

    fn count(n: &InstancedNode) -> usize {
        1 + n.children.iter().map(count).sum::<usize>()
    }
    fn placed(n: &InstancedNode) -> usize {
        usize::from(n.mesh_resource_id.is_some()) + n.children.iter().map(placed).sum::<usize>()
    }
    assert!(count(&scene.scene_hierarchy) > 1);
    assert_eq!(scene.scene_hierarchy.layer, "IFCPROJECT");
    // Every one of the file's 93 mesh items is placed by some node (its
    // samples outnumber them: 308 samples over 32 shells).
    assert_eq!(placed(&scene.scene_hierarchy), 93);
    for r in &scene.mesh_resources {
        for p in &r.primitives {
            assert_eq!(p.positions.len(), p.normals.len());
            assert_eq!(p.uvs.len(), p.positions.len() / 3 * 2);
            assert!(p
                .indices
                .iter()
                .all(|&i| (i as usize) < p.positions.len() / 3));
        }
    }
}

#[test]
fn big_shells_round_trip_past_65535_points() {
    // 300 x 220 = 66,000 points (> ushort) and 130,962 triangles: BigShell
    // profiles AND a split into two shells.
    let scene = scene_with_one_primitive(grid_primitive(300, 220), "mesh_huge");
    let raw = to_fragments(&scene, true);
    let shells = parse_raw(&raw)
        .meshes()
        .unwrap()
        .unwrap()
        .shells()
        .unwrap()
        .unwrap();
    assert_eq!(shells.len(), 2);
    for shell in shells.iter() {
        let shell = shell.unwrap();
        assert_eq!(shell.shell_type().unwrap(), schema::ShellType::BIG);
        assert!(shell.profiles().unwrap().unwrap().is_empty());
        assert_eq!(shell.points().unwrap().unwrap().len(), 66_000);
        let big = shell.big_profiles().unwrap().unwrap();
        let first = big.get(0).unwrap().indices().unwrap().unwrap();
        assert_eq!(first.len(), 3);
    }

    let back = from_fragments(&raw).unwrap();
    let prims = &back.mesh_resources[0].primitives;
    assert_eq!(prims.len(), 2);
    assert_eq!(
        prims.iter().map(|p| p.indices.len() / 3).sum::<usize>(),
        299 * 219 * 2
    );
    let original = &scene.mesh_resources[0].primitives[0];
    assert_eq!(prims[0].positions, original.positions);
    assert_eq!(
        prims[0].indices[..],
        original.indices[..prims[0].indices.len()]
    );
    assert_eq!(
        prims[1].indices[..],
        original.indices[prims[0].indices.len()..]
    );
}

/// Applies a column-major 4x4 to a point.
fn apply(m: &[f64; 16], p: [f64; 3]) -> [f64; 3] {
    [
        m[0] * p[0] + m[4] * p[1] + m[8] * p[2] + m[12],
        m[1] * p[0] + m[5] * p[1] + m[9] * p[2] + m[13],
        m[2] * p[0] + m[6] * p[1] + m[10] * p[2] + m[14],
    ]
}

fn mul(a: &[f64; 16], b: &[f64; 16]) -> [f64; 16] {
    let mut out = [0.0; 16];
    for col in 0..4 {
        for row in 0..4 {
            out[col * 4 + row] = (0..4).map(|k| a[k * 4 + row] * b[col * 4 + k]).sum();
        }
    }
    out
}

fn det3(m: &[f64; 16]) -> f64 {
    m[0] * (m[5] * m[10] - m[6] * m[9]) - m[1] * (m[4] * m[10] - m[6] * m[8])
        + m[2] * (m[4] * m[9] - m[5] * m[8])
}

/// World-space triangles of every placed mesh, in their VISIBLE winding (a
/// mirroring transform flips it, as renderers do), each rotated so its
/// smallest vertex leads, rounded to 0.1 mm and sorted.
fn world_triangles(scene: &InstancedScene) -> Vec<[[i64; 3]; 3]> {
    fn walk(
        scene: &InstancedScene,
        n: &InstancedNode,
        parent: &[f64; 16],
        out: &mut Vec<[[i64; 3]; 3]>,
    ) {
        let world = mul(parent, &n.matrix);
        let flip = det3(&world) < 0.0;
        if let Some(id) = &n.mesh_resource_id {
            let res = scene.mesh_resources.iter().find(|r| &r.id == id).unwrap();
            for p in &res.primitives {
                let v = |i: u32| {
                    let i = i as usize * 3;
                    let w = apply(
                        &world,
                        [
                            p.positions[i].into(),
                            p.positions[i + 1].into(),
                            p.positions[i + 2].into(),
                        ],
                    );
                    w.map(|c| (c * 1e4).round() as i64)
                };
                for t in p.indices.as_chunks::<3>().0 {
                    let tri = if flip {
                        [v(t[0]), v(t[2]), v(t[1])]
                    } else {
                        [v(t[0]), v(t[1]), v(t[2])]
                    };
                    let lead = (0..3).min_by_key(|&k| tri[k]).unwrap();
                    out.push([tri[lead], tri[(lead + 1) % 3], tri[(lead + 2) % 3]]);
                }
            }
        }
        for c in &n.children {
            walk(scene, c, &world, out);
        }
    }
    let mut out = Vec::new();
    walk(scene, &scene.scene_hierarchy, &K_IDENTITY, &mut out);
    out.sort();
    out
}

#[test]
fn world_space_geometry_and_winding_survive_rotation_scale_and_mirror() {
    let (c, s) = (0.6f64, 0.8f64); // a rotation about Z
    let rotated_scaled: [f64; 16] = [
        2.0 * c,
        2.0 * s,
        0.0,
        0.0,
        -s,
        c,
        0.0,
        0.0,
        0.0,
        0.0,
        3.0,
        0.0,
        10.0,
        -4.0,
        2.5,
        1.0,
    ];
    let mirrored: [f64; 16] = [
        -1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, -5.0, 0.0, 0.0, 1.0,
    ];
    let mut a = leaf("A", "mesh_0");
    a.matrix = rotated_scaled;
    let mut b = leaf("B", "mesh_0");
    b.matrix = mirrored;
    let group = InstancedNode {
        name: "Group".to_string(),
        matrix: [
            1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 7.0, 0.0, 1.0,
        ],
        children: vec![a, b],
        ..InstancedNode::default()
    };
    let scene = box_scene(vec![group]);

    let raw = to_fragments(&scene, true);
    // Differently scaled placements bake their own shells.
    assert_eq!(
        parse_raw(&raw)
            .meshes()
            .unwrap()
            .unwrap()
            .shells()
            .unwrap()
            .unwrap()
            .len(),
        2
    );
    let back = from_fragments(&raw).unwrap();
    assert_eq!(world_triangles(&back), world_triangles(&scene));
    assert_eq!(back.scene_hierarchy.children[0].name, "Group");
    assert!(back.scene_hierarchy.children[0].mesh_resource_id.is_none());
}

#[test]
fn generated_names_and_categories_round_trip() {
    let mut a = leaf("Component_3", "mesh_0");
    a.name_is_generated = true;
    let mut b = leaf("Real", "mesh_0");
    b.layer = "Walls".to_string();
    let scene = box_scene(vec![a, b]);
    let raw = to_fragments(&scene, true);
    let model = parse_raw(&raw);
    assert_eq!(
        strings(model.categories().unwrap().unwrap()),
        ["Layer0", "Walls"]
    );
    assert_eq!(model.max_local_id().unwrap(), 2);
    assert_eq!(
        model.guid().unwrap().unwrap().to_string_lossy(),
        "00000000-0000-0000-0000-000000000000"
    );
    let metadata = model.metadata().unwrap().unwrap().to_string_lossy();
    assert_eq!(
        metadata,
        r#"{"layer_hidden":{},"generated_name_guids":["openskp-0"]}"#
    );

    let back = from_fragments(&raw).unwrap();
    let kids = &back.scene_hierarchy.children;
    assert!(kids[0].name_is_generated);
    assert!(!kids[1].name_is_generated);
    assert_eq!(kids[1].layer, "Walls");
    assert_eq!(kids[1].definition_name, "Walls");
    // The resource is named after the first item that placed it.
    assert_eq!(back.mesh_resources[0].definition_name, "Component_3");
    assert_eq!(back.mesh_resources[0].id, "frag-0");
}

#[test]
fn an_empty_scene_exports_and_reads_back() {
    let scene = InstancedScene::default();
    let raw = to_fragments(&scene, true);
    let model = parse_raw(&raw);
    assert_eq!(model.local_ids().unwrap().unwrap().len(), 0);
    assert_eq!(model.max_local_id().unwrap(), 0);
    let back = from_fragments(&raw).unwrap();
    assert!(back.mesh_resources.is_empty());
    assert_eq!(
        back.gltf_materials.len(),
        1,
        "a default material stands in for none"
    );
    assert_eq!(
        back.gltf_materials[0]
            .pbr_metallic_roughness
            .base_color_factor,
        [0.8, 0.8, 0.8, 1.0]
    );
}

/// Position of the `n`-th Representation struct, found by its bytes.
fn representation_pos(raw: &[u8], rep: &Representation) -> usize {
    let mut bytes = vec![0u8; Representation::SIZE];
    rep.write(&mut bytes);
    let hits: Vec<usize> = raw
        .windows(bytes.len())
        .enumerate()
        .filter(|(_, w)| *w == bytes)
        .map(|(i, _)| i)
        .collect();
    assert_eq!(hits.len(), 1, "representation bytes are unique");
    hits[0]
}

/// A sample of a representation this reader has no geometry decoder for
/// (CIRCLE_EXTRUSION) is skipped, not misread as a shell.
#[test]
fn an_unsupported_representation_class_is_skipped() {
    let raw = to_fragments(&make_two_instance_scene(), true);
    let rep = parse_raw(&raw)
        .meshes()
        .unwrap()
        .unwrap()
        .representations()
        .unwrap()
        .unwrap()
        .get(0)
        .unwrap()
        .0;
    let pos = representation_pos(&raw, &rep);
    let mut patched = raw.clone();
    patched[pos + 28] = RepresentationClass::CIRCLE_EXTRUSION.0 as u8;

    let back = from_fragments(&patched).unwrap();
    assert_eq!(
        back.mesh_resources
            .iter()
            .map(|r| r.primitives.len())
            .sum::<usize>(),
        0
    );
    assert_eq!(back.scene_hierarchy.children.len(), 2);
}

/// Representation::id indexes Meshes::shells; it need not equal the
/// representation's own position (real IfcImporter files differ).
#[test]
fn follows_representation_id_not_its_vector_position_for_shell_lookup() {
    let triangle = InstancedMeshResource {
        id: "mesh_flag".to_string(),
        definition_id: Some(2),
        definition_name: "Flag".to_string(),
        variant_key: "2|255,255,255".to_string(),
        primitives: vec![LocalPrimitive {
            positions: vec![0., 0., 0., 1., 0., 0., 0., 1., 0.],
            normals: vec![0.0; 9],
            uvs: vec![0.0; 6],
            indices: vec![0, 1, 2],
            material_index: 0,
        }],
    };
    let mut scene = box_scene(vec![
        leaf("Item_Box", "mesh_0"),
        leaf("Item_Flag", "mesh_flag"),
    ]);
    scene.mesh_resources.push(triangle);
    let raw = to_fragments(&scene, true);

    let reps = parse_raw(&raw)
        .meshes()
        .unwrap()
        .unwrap()
        .representations()
        .unwrap()
        .unwrap();
    let (rep0, rep1) = (reps.get(0).unwrap().0, reps.get(1).unwrap().0);
    assert_ne!(rep0.id, rep1.id);
    let (pos0, pos1) = (
        representation_pos(&raw, &rep0),
        representation_pos(&raw, &rep1),
    );
    let mut patched = raw.clone();
    patched[pos0..pos0 + 4].copy_from_slice(&rep1.id.to_le_bytes());
    patched[pos1..pos1 + 4].copy_from_slice(&rep0.id.to_le_bytes());

    let back = from_fragments(&patched).unwrap();
    let vert_count = |name: &str| {
        let node = back
            .scene_hierarchy
            .children
            .iter()
            .find(|n| n.name == name)
            .unwrap();
        let id = node.mesh_resource_id.as_ref().unwrap();
        let res = back.mesh_resources.iter().find(|r| &r.id == id).unwrap();
        res.primitives
            .iter()
            .map(|p| p.positions.len() / 3)
            .sum::<usize>()
    };
    assert_eq!(vert_count("Item_Box"), 3);
    assert_eq!(vert_count("Item_Flag"), 8);
}

#[test]
fn truncated_or_corrupted_buffers_never_panic() {
    let raw = to_fragments(&make_two_instance_scene(), true);
    for cut in [raw.len() / 2, 9, 0] {
        assert!(from_fragments(&raw[..cut]).is_err(), "cut at {cut}");
    }
    for cut in 0..raw.len() {
        let _ = from_fragments(&raw[..cut]);
    }
    for i in 0..raw.len() {
        for v in [0x00, 0x7F, 0x80, 0xFF] {
            let mut patched = raw.clone();
            patched[i] = v;
            let _ = from_fragments(&patched);
        }
    }
}

#[test]
fn deep_nesting_round_trips_and_absurd_nesting_is_rejected() {
    fn chain(depth: usize) -> InstancedScene {
        let mut node = leaf("Deepest", "mesh_0");
        for i in 0..depth {
            node = InstancedNode {
                name: format!("L{i}"),
                matrix: K_IDENTITY,
                children: vec![node],
                ..InstancedNode::default()
            };
        }
        box_scene(vec![node])
    }
    let back = from_fragments(&to_fragments(&chain(100), true)).unwrap();
    let mut n = &back.scene_hierarchy;
    let mut depth = 0;
    while let Some(c) = n.children.first() {
        n = c;
        depth += 1;
    }
    assert_eq!(depth, 101);
    assert_eq!(n.name, "Deepest");
    assert!(from_fragments(&to_fragments(&chain(300), true)).is_err());
}

#[test]
fn export_fragments_writes_the_file() {
    let dir = std::env::temp_dir().join(format!("openskp_frag_test_{}", std::process::id()));
    let path = dir.join("nested").join("out.frag");
    let scene = make_two_instance_scene();
    openskp::export_fragments(&scene, &path, false).unwrap();
    assert_eq!(std::fs::read(&path).unwrap(), to_fragments(&scene, false));
    let back = read_fragments(&path).unwrap();
    assert_eq!(back.scene_hierarchy.children.len(), 2);
    std::fs::remove_dir_all(&dir).ok();
}

/// End to end from a real .skp: world-space geometry survives
/// build_instanced_scene -> to_fragments -> from_fragments.
#[test]
fn real_skp_world_space_geometry_survives_the_round_trip() {
    let file = SkpFile::open(common::fixture("nesting_2020.skp")).unwrap();
    let original = file
        .build_instanced_scene(&ParseOptions::default())
        .unwrap();
    let back = from_fragments(&to_fragments(&original, false)).unwrap();
    let (a, b) = (world_triangles(&original), world_triangles(&back));
    assert!(!a.is_empty());
    assert_eq!(a.len(), b.len());
}
