//! Port of `obj_material_names_test.cpp`.

use std::collections::BTreeSet;

use openskp::{to_mtl, to_obj, GlbPrimitive, GltfMaterial, Scene};

fn material_references(text: &str, prefix: &str) -> Vec<String> {
    text.lines()
        .filter_map(|l| l.strip_prefix(prefix))
        .map(str::to_owned)
        .collect()
}

#[test]
fn sanitized_material_names_are_unique_and_match_obj_references() {
    let scenarios: &[(&[&str], &[&str])] = &[
        (
            &["Wood/Oak", "Wood_Oak", "Wood_Oak_2"],
            &["Wood_Oak", "Wood_Oak_2_1", "Wood_Oak_2"],
        ),
        (&["Oak", "Pine"], &["Oak", "Pine"]),
        (&["A", "A", "A"], &["A", "A_2", "A_3"]),
        (&["Material_1", ""], &["Material_1", "Material_1_2"]),
        (&[" ", "   "], &["default_material", "default_material_2"]),
        (&["!!!", "???"], &["___", "____2"]),
        (&["Oak", "oak"], &["Oak", "oak"]),
    ];
    for (raw, expected) in scenarios {
        let mut scene = Scene::default();
        for (i, name) in raw.iter().enumerate() {
            scene.gltf_materials.push(GltfMaterial {
                name: (*name).into(),
                ..Default::default()
            });
            scene.glb_primitives.push(GlbPrimitive {
                material_index: i,
                positions: vec![0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0],
                indices: vec![0, 1, 2],
                ..Default::default()
            });
        }
        let mtl_names = material_references(&to_mtl(&scene), "newmtl ");
        let obj_names = material_references(&to_obj(&scene, ""), "usemtl ");
        assert_eq!(mtl_names, *expected);
        assert_eq!(obj_names, *expected);
        assert_eq!(
            mtl_names.iter().collect::<BTreeSet<_>>().len(),
            mtl_names.len()
        );
    }
}
