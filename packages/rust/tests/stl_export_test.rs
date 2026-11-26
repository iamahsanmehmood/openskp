//! Port of `stl_export_test.cpp`.

use std::collections::{BTreeMap, BTreeSet};

use openskp::create::{create, DefinitionOptions, FaceOptions, InstanceOptions};
use openskp::ply_export::to_ply_ascii;
use openskp::{
    export_stl, to_stl_ascii, to_stl_binary, GlbPrimitive, ParseOptions, Scene, SkpFile,
};

fn box_scene() -> Scene {
    let mut scene = Scene::default();
    scene.glb_primitives.push(GlbPrimitive {
        geom_name: "Box".into(),
        material_index: 0,
        positions: vec![0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0],
        normals: vec![0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 0.0, 0.0, 1.0],
        uvs: vec![0.0, 0.0, 1.0, 0.0, 0.0, 1.0],
        indices: vec![0, 1, 2],
        back_triangles: Vec::new(),
        unpainted_back: false,
    });
    scene
}

#[test]
fn serializes_scene_to_stl_ascii_text() {
    let stl = to_stl_ascii(&box_scene(), 1.0);
    assert!(stl.contains("solid OpenSKP_Model"));
    assert!(stl.contains("facet normal 0.000000 0.000000 1.000000"));
    assert!(stl.contains("vertex 0.000000 0.000000 0.000000"));
    assert!(stl.contains("endsolid OpenSKP_Model"));
}

#[test]
fn ascii_applies_scale() {
    assert_eq!(
        to_stl_ascii(&box_scene(), 1000.0),
        "solid OpenSKP_Model\n  facet normal 0.000000 0.000000 1.000000\n    outer loop\n\
         \x20     vertex 0.000000 0.000000 0.000000\n      vertex 1000.000000 0.000000 0.000000\n\
         \x20     vertex 0.000000 1000.000000 0.000000\n    endloop\n  endfacet\nendsolid OpenSKP_Model\n"
    );
}

#[test]
fn serializes_scene_to_stl_binary_data() {
    let data = to_stl_binary(&box_scene(), 1.0);
    assert_eq!(data.len(), 80 + 4 + 50);
    assert!(data.starts_with(b"# OpenSKP Binary STL Export\0"));
    assert_eq!(u32::from_le_bytes(data[80..84].try_into().unwrap()), 1);
    let f = |i: usize| f32::from_le_bytes(data[84 + i * 4..88 + i * 4].try_into().unwrap());
    assert_eq!([f(0), f(1), f(2)], [0.0, 0.0, 1.0]);
    assert_eq!([f(6), f(7), f(8)], [1.0, 0.0, 0.0]);
}

#[test]
fn export_writes_both_forms() {
    let dir = std::env::temp_dir().join("openskp-rs-stl-export");
    let scene = box_scene();
    export_stl(&scene, dir.join("a.stl"), true, 1.0).unwrap();
    export_stl(&scene, dir.join("b.stl"), false, 1.0).unwrap();
    assert_eq!(
        std::fs::read(dir.join("a.stl")).unwrap(),
        to_stl_binary(&scene, 1.0)
    );
    assert_eq!(
        std::fs::read_to_string(dir.join("b.stl")).unwrap(),
        to_stl_ascii(&scene, 1.0)
    );
    std::fs::remove_dir_all(&dir).unwrap();
}

const BOX_FACES: [[[f64; 3]; 4]; 6] = [
    [
        [0.0, 0.0, 0.0],
        [0.0, 10.0, 0.0],
        [10.0, 10.0, 0.0],
        [10.0, 0.0, 0.0],
    ],
    [
        [0.0, 0.0, 10.0],
        [10.0, 0.0, 10.0],
        [10.0, 10.0, 10.0],
        [0.0, 10.0, 10.0],
    ],
    [
        [0.0, 0.0, 0.0],
        [10.0, 0.0, 0.0],
        [10.0, 0.0, 10.0],
        [0.0, 0.0, 10.0],
    ],
    [
        [10.0, 0.0, 0.0],
        [10.0, 10.0, 0.0],
        [10.0, 10.0, 10.0],
        [10.0, 0.0, 10.0],
    ],
    [
        [10.0, 10.0, 0.0],
        [0.0, 10.0, 0.0],
        [0.0, 10.0, 10.0],
        [10.0, 10.0, 10.0],
    ],
    [
        [0.0, 10.0, 0.0],
        [0.0, 0.0, 0.0],
        [0.0, 0.0, 10.0],
        [0.0, 10.0, 10.0],
    ],
];

fn make_box_skp(paint: &str) -> SkpFile {
    let mut b = create();
    let red = b.add_material("Red", [255, 0, 0], None).unwrap();
    let blue = b.add_material("Blue", [0, 0, 255], None).unwrap();
    for (i, face) in BOX_FACES.iter().enumerate() {
        let mut front = matches!(paint, "front" | "both" | "mixed").then_some(red);
        let mut back = matches!(paint, "back" | "both" | "mixed").then_some(blue);
        // The same material occurs on both a front and a back. Grouping
        // those sides together would make it impossible to drop only backs.
        if paint == "mixed" && i % 2 == 1 {
            std::mem::swap(&mut front, &mut back);
        }
        b.add_face(
            face,
            &FaceOptions {
                material: front,
                back_material: back,
                ..Default::default()
            },
        )
        .unwrap();
    }
    SkpFile::from_buffer(b.to_bytes().unwrap())
}

fn parse_binary_stl_triangles(data: &[u8]) -> Vec<[[f64; 3]; 3]> {
    assert!(data.len() >= 84);
    let count = u32::from_le_bytes(data[80..84].try_into().unwrap()) as usize;
    assert_eq!(data.len(), 84 + count * 50);
    let mut tris = Vec::with_capacity(count);
    for i in 0..count {
        let off = 84 + i * 50;
        let f = |j: usize| {
            f64::from(f32::from_le_bytes(
                data[off + j * 4..off + (j + 1) * 4].try_into().unwrap(),
            ))
        };
        tris.push([[f(3), f(4), f(5)], [f(6), f(7), f(8)], [f(9), f(10), f(11)]]);
    }
    tris
}

fn parse_ascii_stl_triangles(text: &str) -> Vec<[[f64; 3]; 3]> {
    let mut tris = Vec::new();
    let mut current: Vec<[f64; 3]> = Vec::new();
    for line in text.lines() {
        let trimmed = line.trim();
        if let Some(rest) = trimmed.strip_prefix("vertex ") {
            let coords: Vec<f64> = rest
                .split_whitespace()
                .map(|s| s.parse::<f64>().unwrap())
                .collect();
            assert_eq!(coords.len(), 3);
            current.push([coords[0], coords[1], coords[2]]);
        } else if trimmed == "endfacet" {
            assert_eq!(current.len(), 3);
            tris.push([current[0], current[1], current[2]]);
            current.clear();
        }
    }
    assert!(current.is_empty());
    tris
}

fn quantize_pt(p: [f64; 3]) -> [i64; 3] {
    [
        (p[0] * 1e5).round() as i64,
        (p[1] * 1e5).round() as i64,
        (p[2] * 1e5).round() as i64,
    ]
}

fn assert_closed_oriented_10in_box(tris: &[[[f64; 3]; 3]], label: &str) {
    assert_eq!(tris.len(), 12, "{label}: expected 12 triangles");
    let mut undirected: BTreeMap<[[i64; 3]; 2], usize> = BTreeMap::new();
    let mut directed: BTreeSet<[[i64; 3]; 2]> = BTreeSet::new();
    let mut signed_volume = 0.0_f64;

    for tri in tris {
        let q = [
            quantize_pt(tri[0]),
            quantize_pt(tri[1]),
            quantize_pt(tri[2]),
        ];
        for e in [[q[0], q[1]], [q[1], q[2]], [q[2], q[0]]] {
            assert!(
                directed.insert(e),
                "{label}: duplicate directed half-edge {e:?}"
            );
            let key = if e[0] <= e[1] { e } else { [e[1], e[0]] };
            *undirected.entry(key).or_insert(0) += 1;
        }
        let [a, b, c] = *tri;
        let cross = [
            b[1] * c[2] - b[2] * c[1],
            b[2] * c[0] - b[0] * c[2],
            b[0] * c[1] - b[1] * c[0],
        ];
        signed_volume += a[0] * cross[0] + a[1] * cross[1] + a[2] * cross[2];
    }
    signed_volume /= 6.0;

    assert_eq!(undirected.len(), 18, "{label}: expected 18 unique edges");
    assert!(
        undirected.values().all(|&c| c == 2),
        "{label}: every edge must be shared by exactly 2 triangles"
    );
    for &[u, v] in &directed {
        assert!(
            directed.contains(&[v, u]),
            "{label}: missing opposite half-edge for [{u:?}, {v:?}]"
        );
    }

    let volume_cm3 = signed_volume * 1e6;
    let expected_cm3 = 0.254_f64.powi(3) * 1e6; // 16,387.064 cm^3 (10 in cube)
    let rel_err = (volume_cm3 - expected_cm3).abs() / expected_cm3;
    assert!(
        rel_err < 1e-5,
        "{label}: expected volume {expected_cm3:.1} cm^3, got {volume_cm3:.1} cm^3 (rel_err={rel_err})"
    );
}

#[test]
fn stl_is_a_closed_oriented_box_for_all_paint_modes() {
    let opts = ParseOptions::default();
    for paint in ["unpainted", "front", "back", "both", "mixed"] {
        let scene = make_box_skp(paint).build_scene(&opts).unwrap();
        let bin_tris = parse_binary_stl_triangles(&to_stl_binary(&scene, 1.0));
        assert_closed_oriented_10in_box(&bin_tris, &format!("{paint}/binary"));
        let ascii_tris = parse_ascii_stl_triangles(&to_stl_ascii(&scene, 1.0));
        assert_closed_oriented_10in_box(&ascii_tris, &format!("{paint}/ascii"));
    }
}

#[test]
fn color_export_keeps_both_painted_sides() {
    let scene = make_box_skp("both")
        .build_scene(&ParseOptions::default())
        .unwrap();
    let total_tris: usize = scene
        .glb_primitives
        .iter()
        .map(|p| p.indices.len() / 3)
        .sum();
    assert_eq!(total_tris, 24);
    let ply = to_ply_ascii(&scene);
    assert!(ply.contains("element face 24"));
    assert!(ply.contains("255 0 0 255"));
    assert!(ply.contains("0 0 255 255"));
}

#[test]
fn instanced_scene_records_back_triangles() {
    let opts = ParseOptions::default();
    for paint in ["both", "mixed"] {
        let instanced = make_box_skp(paint).build_instanced_scene(&opts).unwrap();
        let total_tris: usize = instanced
            .mesh_resources
            .iter()
            .flat_map(|r| &r.primitives)
            .map(|p| p.indices.len() / 3)
            .sum();
        let back_count: usize = instanced
            .mesh_resources
            .iter()
            .flat_map(|r| &r.primitives)
            .map(|p| p.back_triangles.len())
            .sum();
        assert_eq!(total_tris, 24, "{paint}");
        assert_eq!(back_count, 12, "{paint}");
    }
}

#[test]
fn mirrored_painted_box_stl_stays_closed_and_oriented() {
    let mut b = create();
    let red = b.add_material("Red", [255, 0, 0], None).unwrap();
    let blue = b.add_material("Blue", [0, 0, 255], None).unwrap();
    let mut def = b
        .add_component_definition("Box", &DefinitionOptions::default())
        .unwrap();
    for (i, face) in BOX_FACES.iter().enumerate() {
        let (front, back) = if i % 2 == 0 { (red, blue) } else { (blue, red) };
        def.add_face(
            face,
            &FaceOptions {
                material: Some(front),
                back_material: Some(back),
                ..Default::default()
            },
        )
        .unwrap();
    }
    let handle = def.close().unwrap();
    b.add_instance(
        handle,
        &InstanceOptions {
            matrix3x3: Some([-1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]),
            ..Default::default()
        },
    )
    .unwrap();

    let scene = SkpFile::from_buffer(b.to_bytes().unwrap())
        .build_scene(&ParseOptions::default())
        .unwrap();
    let bin_tris = parse_binary_stl_triangles(&to_stl_binary(&scene, 1.0));
    assert_closed_oriented_10in_box(&bin_tris, "mirrored/binary");
    let ascii_tris = parse_ascii_stl_triangles(&to_stl_ascii(&scene, 1.0));
    assert_closed_oriented_10in_box(&ascii_tris, "mirrored/ascii");
}

#[test]
fn stl_handles_duplicate_and_out_of_range_back_triangles() {
    let mut scene = Scene::default();
    scene.glb_primitives.push(GlbPrimitive {
        geom_name: "Box".into(),
        material_index: 0,
        positions: vec![0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0],
        normals: vec![0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 0.0, 0.0, 1.0],
        uvs: vec![0.0, 0.0, 1.0, 0.0, 0.0, 1.0],
        indices: vec![0, 1, 2, 0, 2, 1],
        back_triangles: vec![1, 1, 99],
        unpainted_back: false,
    });
    let bin = to_stl_binary(&scene, 1.0);
    let tris = parse_binary_stl_triangles(&bin);
    assert_eq!(tris.len(), 1);
    let ascii_tris = parse_ascii_stl_triangles(&to_stl_ascii(&scene, 1.0));
    assert_eq!(ascii_tris.len(), 1);
}
