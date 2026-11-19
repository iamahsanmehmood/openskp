// Instanced scene building (`instanced_scene_test.cpp`, openskp#200; plus the
// Python port's curve-resource parity, `test_instanced_scene.py`).
//
// Both builders run over real fixtures, and flattening the instanced result
// must reproduce the baked builder's world-space triangles (and loose-edge
// runs): whatever the files contain - nesting, painted instances, layers,
// front/back materials, textures, holes, mirrors - has to come out the same
// either way.

mod common;

use common::fixture;
use openskp::{
    GltfMaterial, InstanceNode, InstancedNode, InstancedScene, ParseOptions, Scene, SkpFile,
};

const VFF_FIXTURES: [&str; 2] = ["SU_File.skp", "Untitled.skp"];
const LEGACY_FIXTURES: [&str; 3] = [
    "capilla_quiroz_v17.skp",
    "gondola_v20.skp",
    "single_material_v17.skp",
];

// Float32 round-off only: the baked path transforms in f64 then stores f32;
// the instanced path stores the local value as f32 and transforms after.
const TOLERANCE: f64 = 1e-5;

const IDENTITY4: [f64; 16] = [
    1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0,
];

fn mul4(a: &[f64; 16], b: &[f64; 16]) -> [f64; 16] {
    let mut out = [0.0; 16];
    for col in 0..4 {
        for row in 0..4 {
            out[col * 4 + row] = (0..4)
                .map(|k| a[k * 4 + row] * b[col * 4 + k])
                .fold(0.0, |s, x| s + x);
        }
    }
    out
}

fn apply_matrix(m: &[f64; 16], p: [f64; 3]) -> [f64; 3] {
    [
        m[0] * p[0] + m[4] * p[1] + m[8] * p[2] + m[12],
        m[1] * p[0] + m[5] * p[1] + m[9] * p[2] + m[13],
        m[2] * p[0] + m[6] * p[1] + m[10] * p[2] + m[14],
    ]
}

struct FlatTri {
    corners: [[f64; 3]; 3],
    material_index: usize,
}

fn flatten_instanced(scene: &InstancedScene) -> Vec<FlatTri> {
    fn visit(
        scene: &InstancedScene,
        node: &InstancedNode,
        parent: &[f64; 16],
        out: &mut Vec<FlatTri>,
    ) {
        let world = mul4(parent, &node.matrix);
        if let Some(id) = &node.mesh_resource_id {
            if let Some(resource) = scene.mesh_resources.iter().find(|r| &r.id == id) {
                for prim in &resource.primitives {
                    for tri in prim.indices.as_chunks::<3>().0 {
                        let corner = |vi: u32| {
                            let vi = vi as usize * 3;
                            let p = &prim.positions;
                            apply_matrix(
                                &world,
                                [f64::from(p[vi]), f64::from(p[vi + 1]), f64::from(p[vi + 2])],
                            )
                        };
                        out.push(FlatTri {
                            corners: [corner(tri[0]), corner(tri[1]), corner(tri[2])],
                            material_index: prim.material_index,
                        });
                    }
                }
            }
        }
        for child in &node.children {
            visit(scene, child, &world, out);
        }
    }
    let mut out = Vec::new();
    visit(scene, &scene.scene_hierarchy, &IDENTITY4, &mut out);
    out
}

fn flatten_baked(scene: &Scene) -> Vec<FlatTri> {
    let mut out = Vec::new();
    for prim in &scene.glb_primitives {
        for tri in prim.indices.as_chunks::<3>().0 {
            let corner = |vi: u32| {
                let vi = vi as usize * 3;
                let p = &prim.positions;
                [f64::from(p[vi]), f64::from(p[vi + 1]), f64::from(p[vi + 2])]
            };
            out.push(FlatTri {
                corners: [corner(tri[0]), corner(tri[1]), corner(tri[2])],
                material_index: prim.material_index,
            });
        }
    }
    out
}

fn buffer_bytes<'a>(prims: impl Iterator<Item = (usize, usize, usize, usize)> + 'a) -> usize {
    prims.map(|(p, n, u, i)| (p + n + u + i) * 4).sum()
}

fn materials_equal(a: &GltfMaterial, b: &GltfMaterial) -> bool {
    let (pa, pb) = (&a.pbr_metallic_roughness, &b.pbr_metallic_roughness);
    pa.base_color_factor == pb.base_color_factor
        && pa.metallic_factor == pb.metallic_factor
        && pa.roughness_factor == pb.roughness_factor
        && pa.base_color_texture.is_some() == pb.base_color_texture.is_some()
        && a.double_sided == b.double_sided
}

fn walk_metadata_parity(baked: &InstanceNode, instanced: &InstancedNode) {
    assert_eq!(instanced.name, baked.name);
    assert_eq!(instanced.definition_name, baked.definition_name);
    assert_eq!(instanced.layer, baked.layer);
    assert_eq!(instanced.position_mm, baked.position_mm);
    assert_eq!(instanced.properties, baked.properties);
    assert_eq!(instanced.name_is_generated, baked.name_is_generated);
    assert_eq!(
        instanced.attribute_dictionaries,
        baked.attribute_dictionaries
    );
    assert_eq!(instanced.children.len(), baked.children.len());
    for (b, i) in baked.children.iter().zip(&instanced.children) {
        walk_metadata_parity(b, i);
    }
}

fn scenes(name: &str) -> (Scene, InstancedScene) {
    let file = SkpFile::open(fixture(name)).unwrap();
    let o = ParseOptions::default();
    (
        file.build_scene(&o).unwrap(),
        file.build_instanced_scene(&o).unwrap(),
    )
}

fn reproduces_build_scenes_world_space_triangles(name: &str) {
    let (baked, instanced) = scenes(name);
    let baked_tris = flatten_baked(&baked);
    let instanced_tris = flatten_instanced(&instanced);
    assert_eq!(instanced_tris.len(), baked_tris.len(), "{name}");

    let delta =
        |p: &[f64; 3], q: &[f64; 3]| (0..3).map(|k| (p[k] - q[k]).abs()).fold(0.0, f64::max);
    let mut worst_delta: f64 = 0.0;
    let mut material_mismatches = 0;
    for (a, e) in instanced_tris.iter().zip(&baked_tris) {
        if !materials_equal(
            &instanced.gltf_materials[a.material_index],
            &baked.gltf_materials[e.material_index],
        ) {
            material_mismatches += 1;
        }
        // The baked path flips a mirrored instance's winding; the instanced
        // path leaves the mirror on the node. Accept either pairing.
        let (a, e) = (&a.corners, &e.corners);
        let direct = delta(&a[0], &e[0])
            .max(delta(&a[1], &e[1]))
            .max(delta(&a[2], &e[2]));
        let reflected = delta(&a[0], &e[0])
            .max(delta(&a[1], &e[2]))
            .max(delta(&a[2], &e[1]));
        worst_delta = worst_delta.max(direct.min(reflected));
    }
    assert_eq!(material_mismatches, 0, "{name}");
    assert!(worst_delta < TOLERANCE, "{name}: worst delta {worst_delta}");
}

fn never_stores_more_geometry_than_the_baked_path(name: &str) {
    let (baked, instanced) = scenes(name);
    let baked_bytes = buffer_bytes(baked.glb_primitives.iter().map(|p| {
        (
            p.positions.len(),
            p.normals.len(),
            p.uvs.len(),
            p.indices.len(),
        )
    }));
    let instanced_bytes = buffer_bytes(
        instanced
            .mesh_resources
            .iter()
            .flat_map(|r| &r.primitives)
            .map(|p| {
                (
                    p.positions.len(),
                    p.normals.len(),
                    p.uvs.len(),
                    p.indices.len(),
                )
            }),
    );
    // Equal when nothing repeats; strictly smaller once anything does.
    assert!(instanced_bytes <= baked_bytes, "{name}");
}

fn resolves_the_same_layers_and_dynamic_properties_per_node(name: &str) {
    let (baked, instanced) = scenes(name);
    walk_metadata_parity(&baked.scene_hierarchy, &instanced.scene_hierarchy);
}

type FlatCurve = (Vec<[f64; 3]>, bool, String, bool);

fn flatten_instanced_curves(scene: &InstancedScene) -> Vec<FlatCurve> {
    fn visit(
        scene: &InstancedScene,
        node: &InstancedNode,
        parent: &[f64; 16],
        out: &mut Vec<FlatCurve>,
    ) {
        let world = mul4(parent, &node.matrix);
        if let Some(id) = &node.curve_resource_id {
            if let Some(resource) = scene.curve_resources.iter().find(|r| &r.id == id) {
                for c in &resource.curves {
                    let points = c
                        .points_m
                        .iter()
                        .map(|&p| apply_matrix(&world, p))
                        .collect();
                    out.push((points, c.closed, c.layer.clone(), c.arc.is_some()));
                }
            }
        }
        for child in &node.children {
            visit(scene, child, &world, out);
        }
    }
    let mut out = Vec::new();
    visit(scene, &scene.scene_hierarchy, &IDENTITY4, &mut out);
    out
}

// openskp#316: a definition made only of loose edges must reach the instanced
// scene too, and flattening it must reproduce the baked curve sets.
fn reproduces_build_scenes_curve_sets(name: &str) {
    let (baked, instanced) = scenes(name);
    let baked_curves: Vec<FlatCurve> = baked
        .curve_sets
        .iter()
        .map(|c| {
            (
                c.points_m.clone(),
                c.closed,
                c.layer.clone(),
                c.arc.is_some(),
            )
        })
        .collect();
    let instanced_curves = flatten_instanced_curves(&instanced);
    assert_eq!(instanced_curves.len(), baked_curves.len(), "{name}");
    for (i, (inst, base)) in instanced_curves.iter().zip(&baked_curves).enumerate() {
        assert_eq!(
            inst.0.len(),
            base.0.len(),
            "{name} run {i}: point count differs"
        );
        for (pa, pe) in inst.0.iter().zip(&base.0) {
            for k in 0..3 {
                assert!(
                    (pa[k] - pe[k]).abs() < TOLERANCE,
                    "{name} run {i}: coordinate mismatch"
                );
            }
        }
        assert_eq!(inst.1, base.1, "{name} run {i}: closed flag differs");
        assert_eq!(inst.2, base.2, "{name} run {i}: layer differs");
        assert_eq!(inst.3, base.3, "{name} run {i}: arc detection differs");
    }
}

#[test]
fn reproduces_build_scenes_world_space_triangles_vff() {
    VFF_FIXTURES
        .iter()
        .for_each(|f| reproduces_build_scenes_world_space_triangles(f));
}

#[test]
fn reproduces_build_scenes_world_space_triangles_legacy() {
    LEGACY_FIXTURES
        .iter()
        .for_each(|f| reproduces_build_scenes_world_space_triangles(f));
}

#[test]
fn never_stores_more_geometry_than_the_baked_path_vff() {
    VFF_FIXTURES
        .iter()
        .for_each(|f| never_stores_more_geometry_than_the_baked_path(f));
}

#[test]
fn never_stores_more_geometry_than_the_baked_path_legacy() {
    LEGACY_FIXTURES
        .iter()
        .for_each(|f| never_stores_more_geometry_than_the_baked_path(f));
}

#[test]
fn resolves_the_same_layers_and_dynamic_properties_per_node_vff() {
    VFF_FIXTURES
        .iter()
        .for_each(|f| resolves_the_same_layers_and_dynamic_properties_per_node(f));
}

#[test]
fn resolves_the_same_layers_and_dynamic_properties_per_node_legacy() {
    LEGACY_FIXTURES
        .iter()
        .for_each(|f| resolves_the_same_layers_and_dynamic_properties_per_node(f));
}

#[test]
fn reproduces_build_scenes_curve_sets_vff() {
    VFF_FIXTURES
        .iter()
        .for_each(|f| reproduces_build_scenes_curve_sets(f));
}

#[test]
fn reproduces_build_scenes_curve_sets_legacy() {
    LEGACY_FIXTURES
        .iter()
        .for_each(|f| reproduces_build_scenes_curve_sets(f));
}

#[test]
fn untitled_bakes_its_loose_edges() {
    let (baked, instanced) = scenes("Untitled.skp");
    assert_eq!(baked.curve_sets.len(), 526);
    assert!(!instanced.curve_resources.is_empty());
    let bounds = instanced.bounds.expect("bounds");
    for k in 0..3 {
        assert!(bounds.min[k] <= bounds.max[k]);
        assert!((bounds.size[k] - (bounds.max[k] - bounds.min[k])).abs() < 1e-12);
    }
}
