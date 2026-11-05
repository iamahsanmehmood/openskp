// Real-file regression test for SketchUp 2020 (v20) classic .skp files.
//
// Fixture: fixtures/gondola_v20.skp - a retail gondola display authored in
// SketchUp 2020 (v20.1.235, ~755 KB), shared via the TypeScript port's PR
// #155.
//
// Before the v20 layout fixes, this file threw "implausible def entities"
// from the legacy walk: v20 writes records the v17 layout does not have,
// which left the reader a few bytes short and made it read garbage where a
// count was expected.
//
// Every count below was read off this exact file after the fix - a parse that
// "succeeds" while silently dropping placements would still be a bug, so the
// instance counts matter as much as the parse not throwing.

mod common;

use std::collections::{BTreeSet, HashMap, HashSet};

use common::{assert_near, fixture};
use openskp::{EntityId, ParseOptions, SkpFile, SkpModel};

fn parse() -> SkpModel {
    SkpFile::open(fixture("gondola_v20.skp"))
        .unwrap()
        .parse(&ParseOptions::default())
        .unwrap()
}

#[test]
fn parses_real_v20_file_that_previously_threw() {
    let model = parse();
    assert_eq!(model.version, "{20.1.235}");
    // Legacy files carry no meta/meta.dat, same as v17.
    assert!(model.units.is_none());
    assert_eq!(model.definitions.len(), 20);
    assert_eq!(model.materials.len(), 24);

    // v20 interleaves a null object-ref after EACH layer record; the count is
    // the number of REAL layers. Nulls must never reach model.layers.
    let names: BTreeSet<&str> = model.layers.iter().map(|l| l.name.as_str()).collect();
    assert_eq!(model.layers.len(), 2);
    assert_eq!(names, BTreeSet::from(["Layer0", "Gondulas Laterais"]));

    // real geometry, not an empty shell
    let faces: usize = model.definitions.values().map(|d| d.faces.len()).sum();
    let edges: usize = model.definitions.values().map(|d| d.edges.len()).sum();
    let vertices: usize = model.definitions.values().map(|d| d.vertices.len()).sum();
    assert_eq!(faces, 1887);
    assert_eq!(edges, 9174);
    assert_eq!(vertices, 6543);
}

#[test]
fn places_every_root_instance() {
    // 23 root-level placements: the definitions are useless if the instances
    // that position them are lost, which is exactly what a subtly misaligned
    // walk produces.
    assert_eq!(parse().root().instances.len(), 23);
}

#[test]
fn places_every_root_instance_in_the_scene() {
    let scene = SkpFile::open(fixture("gondola_v20.skp"))
        .unwrap()
        .build_scene(&ParseOptions::default())
        .unwrap();
    assert_eq!(scene.scene_hierarchy.children.len(), 23);
    assert_eq!(scene.glb_primitives.len(), 201);
    assert_eq!(scene.mesh_index.len(), 201);
    assert!(!scene.gltf_materials.is_empty());
}

#[test]
fn resolves_placed_instances_to_definitions_that_carry_geometry() {
    // Guards the failure mode a zero entity count produces: the definitions an
    // instance points at come back empty, so the file parses into a scene of
    // correctly-positioned but invisible groups.
    let model = parse();
    let mut referenced = HashSet::new();
    for inst in &model.root().instances {
        referenced.extend(inst.ref_idx);
    }
    for def in model.definitions.values() {
        for inst in &def.instances {
            referenced.extend(inst.ref_idx);
        }
    }

    fn carries_geometry(
        model: &SkpModel,
        def_id: EntityId,
        memo: &mut HashMap<EntityId, bool>,
        in_progress: &mut HashSet<EntityId>,
    ) -> bool {
        if let Some(&cached) = memo.get(&def_id) {
            return cached;
        }
        if !in_progress.insert(def_id) {
            return false; // reference cycle
        }
        let result = match model.definitions.get(&def_id) {
            None => false,
            Some(d) => {
                !d.faces.is_empty()
                    || d.instances.iter().any(|c| {
                        c.ref_idx
                            .is_some_and(|r| carries_geometry(model, r, memo, in_progress))
                    })
            }
        };
        in_progress.remove(&def_id);
        memo.insert(def_id, result);
        result
    }

    let mut memo = HashMap::new();
    let mut in_progress = HashSet::new();
    for def_id in referenced {
        assert!(
            carries_geometry(&model, def_id, &mut memo, &mut in_progress),
            "definition {def_id} carries no geometry"
        );
    }
}

#[test]
#[allow(clippy::approx_constant)] // 3.14 m is the gondola's measured depth, not pi
fn bakes_geometry_at_a_plausible_real_world_scale() {
    let scene = SkpFile::open(fixture("gondola_v20.skp"))
        .unwrap()
        .build_scene(&ParseOptions::default())
        .unwrap();
    let mut min = [f64::INFINITY; 3];
    let mut max = [f64::NEG_INFINITY; 3];
    for prim in &scene.glb_primitives {
        for p in prim.positions.as_chunks::<3>().0 {
            for a in 0..3 {
                min[a] = min[a].min(f64::from(p[a]));
                max[a] = max[a].max(f64::from(p[a]));
            }
        }
    }
    // a shop gondola display: metres, not the 1e3-off or degenerate box a
    // misaligned read produces
    assert_near(max[0] - min[0], 3.82, 0.1);
    assert_near(max[1] - min[1], 3.14, 0.1);
    assert_near(max[2] - min[2], 4.82, 0.1);
}

#[test]
fn gives_every_baked_primitive_valid_uv_coordinates() {
    let scene = SkpFile::open(fixture("gondola_v20.skp"))
        .unwrap()
        .build_scene(&ParseOptions::default())
        .unwrap();
    assert!(!scene.glb_primitives.is_empty());
    for prim in &scene.glb_primitives {
        assert_eq!(prim.uvs.len(), prim.positions.len() / 3 * 2);
        assert!(prim.uvs.iter().all(|uv| uv.is_finite()));
    }
}
