//! Port of `instanced_glb_test.cpp` (openskp#200), plus hand-built scenes
//! that exercise the exporter without the scene builder.

mod common;
mod gltf_helper;

use std::time::Instant;

use gltf_helper::{contains_bytes, int, load_glb, numbers, string};
use openskp::instanced_scene::IDENTITY_GLTF;
use openskp::{
    export_instanced_glb, to_glb, to_instanced_glb, GlbError, GlbOptions, GltfMaterial,
    InstancedGlbOptions, InstancedMeshResource, InstancedNode, InstancedScene, LocalPrimitive,
    ParseOptions, SceneTexture, SkpFile,
};

const JPEG_MAGIC: &[u8] = &[0xff, 0xd8, 0xff];

fn make_box_resource(id: &str) -> InstancedMeshResource {
    let positions: Vec<f32> = vec![
        0., 0., 0., 1., 0., 0., 1., 1., 0., 0., 1., 0., 0., 0., 1., 1., 0., 1., 1., 1., 1., 0., 1.,
        1.,
    ];
    let prim = LocalPrimitive {
        normals: vec![0.0; positions.len()],
        uvs: vec![0.0; positions.len() / 3 * 2],
        positions,
        indices: vec![0, 1, 2, 0, 2, 3, 4, 6, 5, 4, 7, 6],
        material_index: 0,
    };
    InstancedMeshResource {
        id: id.into(),
        definition_id: Some(1),
        definition_name: "Box".into(),
        variant_key: "1|255,255,255".into(),
        primitives: vec![prim],
    }
}

fn opts() -> InstancedGlbOptions {
    InstancedGlbOptions::default()
}

fn instanced(name: &str) -> InstancedScene {
    SkpFile::open(common::fixture(name))
        .unwrap()
        .build_instanced_scene(&ParseOptions::default())
        .unwrap()
}

fn translated(name: &str, mesh: Option<&str>, x: f64) -> InstancedNode {
    let mut matrix = IDENTITY_GLTF;
    matrix[12] = x;
    InstancedNode {
        name: name.into(),
        matrix,
        mesh_resource_id: mesh.map(Into::into),
        ..Default::default()
    }
}

#[test]
fn shares_one_mesh_between_placements() {
    let mut scene = InstancedScene {
        gltf_materials: vec![GltfMaterial::default()],
        ..Default::default()
    };
    scene.mesh_resources.push(make_box_resource("box"));
    scene.mesh_resources.push(InstancedMeshResource {
        id: "empty".into(),
        ..Default::default()
    });
    let mut root = InstancedNode {
        name: "ROOT".into(),
        ..Default::default()
    };
    root.children.push(translated("A", Some("box"), 2.0));
    let mut b = translated("", Some("box"), -2.5);
    b.definition_name = "BoxDef".into();
    b.children.push(InstancedNode {
        mesh_resource_id: Some("empty".into()),
        ..Default::default()
    });
    root.children.push(b);
    scene.scene_hierarchy = root;

    let model = load_glb(&to_instanced_glb(&scene, &opts()).unwrap());
    assert_eq!(
        string(&model.json.find("asset").unwrap().clone(), "generator"),
        "OpenSKP Instanced Exporter"
    );
    // The resource with no primitives produces no mesh.
    assert_eq!(model.list("meshes").len(), 1);
    assert_eq!(string(&model.list("meshes")[0], "name"), "Box");
    // One copy of the geometry: 4 arrays for one primitive.
    assert_eq!(model.list("accessors").len(), 4);

    let nodes = model.list("nodes");
    assert_eq!(nodes.len(), 4);
    let scene_nodes = numbers(&model.list("scenes")[0], "nodes");
    assert_eq!(scene_nodes, vec![0.0]);
    assert_eq!(string(&nodes[0], "name"), "ROOT");
    assert!(nodes[0].find("matrix").is_none());
    assert!(nodes[0].find("mesh").is_none());
    assert_eq!(numbers(&nodes[0], "children"), vec![1.0, 2.0]);
    assert_eq!(int(&nodes[1], "mesh"), 0);
    assert_eq!(numbers(&nodes[1], "matrix")[12], 2.0);
    assert_eq!(string(&nodes[2], "name"), "BoxDef");
    assert_eq!(int(&nodes[2], "mesh"), 0);
    assert_eq!(numbers(&nodes[2], "matrix")[12], -2.5);
    assert_eq!(numbers(&nodes[2], "children"), vec![3.0]);
    // Unnamed, identity, unresolved mesh: an empty object.
    assert_eq!(nodes[3].as_object().map(<[_]>::len), Some(0));
}

#[test]
fn embeds_textures_only_when_asked() {
    let mut scene = InstancedScene {
        gltf_materials: vec![GltfMaterial::default()],
        ..Default::default()
    };
    scene.gltf_materials[0]
        .pbr_metallic_roughness
        .base_color_texture = Some(0);
    scene.textures.push(SceneTexture {
        data: vec![0xff, 0xd8, 0xff, 0xe0, 1],
        mime_type: "image/jpeg".into(),
        filename: "a.jpg".into(),
    });
    scene.mesh_resources.push(make_box_resource("box"));
    scene.scene_hierarchy = translated("ROOT", Some("box"), 0.0);

    let without = to_instanced_glb(&scene, &opts()).unwrap();
    assert!(!contains_bytes(&without, b"\"images\""));
    assert!(!contains_bytes(&without, JPEG_MAGIC));
    let with = to_instanced_glb(&scene, &InstancedGlbOptions { textures: true }).unwrap();
    assert!(contains_bytes(&with, JPEG_MAGIC));
    let model = load_glb(&with);
    assert_eq!(model.list("images").len(), 1);
    assert_eq!(string(&model.list("images")[0], "mimeType"), "image/jpeg");
    let mat = &model.list("materials")[0];
    assert_eq!(string(mat, "alphaMode"), "MASK");
    assert_eq!(
        int(
            mat.find("pbrMetallicRoughness")
                .unwrap()
                .find("baseColorTexture")
                .unwrap(),
            "index"
        ),
        0
    );
}

#[test]
fn empty_scene_still_has_a_root_node_and_buffer() {
    let model = load_glb(&to_instanced_glb(&InstancedScene::default(), &opts()).unwrap());
    assert_eq!(model.list("nodes").len(), 1);
    assert_eq!(model.list("buffers").len(), 1);
    assert_eq!(int(&model.list("buffers")[0], "byteLength"), 0);
    assert!(model.bin.is_empty());
}

#[test]
fn rejects_malformed_resources() {
    let mut scene = InstancedScene {
        gltf_materials: vec![GltfMaterial::default()],
        ..Default::default()
    };
    let mut res = make_box_resource("box");
    res.primitives[0].indices[0] = 99;
    scene.mesh_resources.push(res);
    assert!(matches!(
        to_instanced_glb(&scene, &opts()),
        Err(GlbError::InvalidArgument(_))
    ));

    let mut scene = InstancedScene {
        gltf_materials: vec![GltfMaterial::default()],
        ..Default::default()
    };
    scene.gltf_materials[0]
        .pbr_metallic_roughness
        .base_color_factor[1] = 2.0;
    assert!(matches!(
        to_instanced_glb(&scene, &opts()),
        Err(GlbError::InvalidArgument(_))
    ));

    // Unlike the baked exporter, only finiteness is checked for these.
    let mut scene = InstancedScene {
        gltf_materials: vec![GltfMaterial::default()],
        ..Default::default()
    };
    scene.gltf_materials[0]
        .pbr_metallic_roughness
        .metallic_factor = 1.5;
    assert!(to_instanced_glb(&scene, &opts()).is_ok());
    scene.gltf_materials[0]
        .pbr_metallic_roughness
        .metallic_factor = f64::NAN;
    assert!(matches!(
        to_instanced_glb(&scene, &opts()),
        Err(GlbError::InvalidArgument(_))
    ));
}

#[test]
fn many_distinct_mesh_resources_stay_near_linear_not_quadratic() {
    // Regression test for openskp#305 (quadratic buffer growth).
    const COUNT: usize = 8000;
    let mut scene = InstancedScene {
        gltf_materials: vec![GltfMaterial::default()],
        ..Default::default()
    };
    let mut root = InstancedNode {
        name: "ROOT".into(),
        ..Default::default()
    };
    for i in 0..COUNT {
        let id = format!("mesh_{i}");
        scene.mesh_resources.push(make_box_resource(&id));
        root.children.push(InstancedNode {
            name: format!("Box{i}"),
            mesh_resource_id: Some(id),
            ..Default::default()
        });
    }
    scene.scene_hierarchy = root;

    let start = Instant::now();
    let bytes = to_instanced_glb(&scene, &opts()).unwrap();
    let elapsed = start.elapsed();
    assert!(!bytes.is_empty());
    assert!(
        elapsed.as_millis() < 5000,
        "took {elapsed:?} - looks quadratic again"
    );
}

#[test]
fn serializes_instanced_scene_with_shared_mesh() {
    let scene = instanced("gondola_v20.skp");
    let bytes = to_instanced_glb(&scene, &opts()).unwrap();
    assert_eq!(&bytes[..4], b"glTF");
    let model = load_glb(&bytes);
    assert_eq!(model.list("meshes").len(), scene.mesh_resources.len());
}

#[test]
fn export_omits_images_by_default() {
    let bytes = to_instanced_glb(&instanced("capilla_quiroz_v17.skp"), &opts()).unwrap();
    assert!(!contains_bytes(&bytes, b"\"images\""));
    assert!(!contains_bytes(&bytes, JPEG_MAGIC));
    assert!(load_glb(&bytes).list("images").is_empty());
}

#[test]
fn export_embeds_textures_when_asked() {
    let scene = instanced("capilla_quiroz_v17.skp");
    let without = to_instanced_glb(&scene, &opts()).unwrap();
    let with = to_instanced_glb(&scene, &InstancedGlbOptions { textures: true }).unwrap();
    assert!(with.len() > without.len());
    assert!(contains_bytes(&with, JPEG_MAGIC));
    let model = load_glb(&with);
    assert_eq!(model.list("images").len(), 3);
    for image in model.list("images") {
        assert!(int(image, "bufferView") >= 0);
        assert!(string(image, "mimeType").starts_with("image/"));
    }
    assert!(model.list("materials").iter().any(|m| m
        .find("pbrMetallicRoughness")
        .and_then(|p| p.find("baseColorTexture"))
        .is_some()));
}

#[test]
fn is_smaller_than_the_baked_export_on_a_file_with_repeated_geometry() {
    let baked = SkpFile::open(common::fixture("gondola_v20.skp"))
        .unwrap()
        .build_scene(&ParseOptions::default())
        .unwrap();
    let baked_bytes = to_glb(&baked, &GlbOptions::default()).unwrap();
    let instanced_bytes = to_instanced_glb(&instanced("gondola_v20.skp"), &opts()).unwrap();
    assert!(instanced_bytes.len() < baked_bytes.len());
}

#[test]
fn export_instanced_glb_file_round_trips() {
    let output = std::env::temp_dir().join("openskp-rs-instanced-export-test.glb");
    export_instanced_glb(
        &instanced("capilla_quiroz_v17.skp"),
        &output,
        &InstancedGlbOptions { textures: true },
    )
    .unwrap();
    let actual = std::fs::read(&output).unwrap();
    std::fs::remove_file(&output).unwrap();
    assert!(contains_bytes(&actual, JPEG_MAGIC));
}
