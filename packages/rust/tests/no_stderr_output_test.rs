// Ensure openskp writes nothing to stdout or stderr when a warning is produced
// with no logging configured (#556), and routes warnings through ParseOptions.

use std::env;
use std::process::Command;
use std::sync::{Arc, Mutex};

use openskp::flatbuffers::FbStruct;
use openskp::fragments::schema::{self, Representation, RepresentationClass};
use openskp::fragments::{from_fragments, from_fragments_with_options, to_fragments};
use openskp::instanced_scene::IDENTITY_GLTF as K_IDENTITY;
use openskp::{
    GltfMaterial, InstancedMeshResource, InstancedNode, InstancedScene, LocalPrimitive, LogLevel,
    ParseOptions,
};

fn make_circle_extrusion_frag() -> Vec<u8> {
    let prim = LocalPrimitive {
        positions: vec![0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 1.0, 0.0, 0.0, 1.0, 0.0],
        normals: vec![0.0; 12],
        uvs: vec![0.0; 8],
        indices: vec![0, 1, 2, 0, 2, 3],
        material_index: 0,
        unpainted_back: false,
    };
    let resource = InstancedMeshResource {
        id: "mesh_0".to_string(),
        definition_id: Some(1),
        definition_name: "Box".to_string(),
        variant_key: "1|255,255,255".to_string(),
        primitives: vec![prim],
    };
    let leaf = InstancedNode {
        name: "Box1".to_string(),
        matrix: K_IDENTITY,
        mesh_resource_id: Some("mesh_0".to_string()),
        ..InstancedNode::default()
    };
    let root = InstancedNode {
        name: "ROOT".to_string(),
        matrix: K_IDENTITY,
        children: vec![leaf],
        ..InstancedNode::default()
    };
    let scene = InstancedScene {
        mesh_resources: vec![resource],
        gltf_materials: vec![GltfMaterial::default()],
        scene_hierarchy: root,
        ..InstancedScene::default()
    };
    let raw = to_fragments(&scene, true);
    let model = schema::root_as_model(&raw).unwrap();
    let meshes = model.meshes().unwrap().unwrap();
    let reps = meshes.representations().unwrap().unwrap();
    let rep = reps.get(0).unwrap().0;
    let mut rep_bytes = vec![0u8; Representation::SIZE];
    rep.write(&mut rep_bytes);
    let pos = raw
        .windows(rep_bytes.len())
        .position(|w| w == rep_bytes)
        .expect("unique representation bytes");
    let mut patched = raw;
    patched[pos + 28] = RepresentationClass::CIRCLE_EXTRUSION.0 as u8;
    patched
}

#[test]
fn child_probe_runs_silently() {
    if env::var_os("OPENSKP_STDIO_PROBE").is_none() {
        return;
    }
    let patched = make_circle_extrusion_frag();
    let back = from_fragments(&patched).unwrap();
    assert!(back.mesh_resources.iter().all(|r| r.primitives.is_empty()));
}

#[test]
fn warnings_write_nothing_to_stderr_without_logging_configured() {
    let exe = env::current_exe().unwrap();
    let out = Command::new(exe)
        .env("OPENSKP_STDIO_PROBE", "1")
        .args(["--exact", "child_probe_runs_silently", "--nocapture"])
        .output()
        .unwrap();
    assert!(out.status.success());
    let stdout = String::from_utf8_lossy(&out.stdout);
    let stderr = String::from_utf8_lossy(&out.stderr);
    assert!(
        !stdout.contains("RepresentationClass") && !stdout.contains("openskp"),
        "unexpected stdout: {stdout}"
    );
    assert_eq!(stderr, "");
}

#[test]
fn from_fragments_routes_warnings_to_log_callback() {
    let patched = make_circle_extrusion_frag();
    let seen: Arc<Mutex<Vec<(LogLevel, String)>>> = Arc::default();
    let seen_cb = Arc::clone(&seen);
    let opts = ParseOptions::default().with_log(move |level, msg| {
        seen_cb.lock().unwrap().push((level, msg.to_owned()));
    });
    let back = from_fragments_with_options(&patched, &opts).unwrap();
    assert!(back.mesh_resources.iter().all(|r| r.primitives.is_empty()));
    let logs = seen.lock().unwrap();
    assert!(logs
        .iter()
        .any(|(lvl, msg)| *lvl == LogLevel::Warning && msg.contains("RepresentationClass")));
}
