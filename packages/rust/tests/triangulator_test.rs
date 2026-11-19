// Face normals and planar-face triangulation (`triangulator_test.cpp`).

mod common;

use std::collections::BTreeMap;

use common::assert_near;
use openskp::{compute_face_normal, triangulate_face_3d, EntityId, Vertex};

fn vertices(points: &[(EntityId, f64, f64, f64)]) -> BTreeMap<EntityId, Vertex> {
    points
        .iter()
        .map(|&(id, x, y, z)| (id, Vertex { id, x, y, z }))
        .collect()
}

fn tri_area(ts: &[[EntityId; 3]], v: &BTreeMap<EntityId, Vertex>) -> f64 {
    ts.iter()
        .map(|t| {
            let (p, q, r) = (&v[&t[0]], &v[&t[1]], &v[&t[2]]);
            ((q.x - p.x) * (r.y - p.y) - (r.x - p.x) * (q.y - p.y)).abs() / 2.0
        })
        .sum()
}

#[test]
fn computes_face_normal() {
    let normal = compute_face_normal(&[
        [0.0, 0.0, 0.0],
        [1.0, 0.0, 0.0],
        [1.0, 1.0, 0.0],
        [0.0, 1.0, 0.0],
    ])
    .unwrap();
    assert_near(normal[0], 0.0, 1e-12);
    assert_near(normal[1], 0.0, 1e-12);
    assert_near(normal[2], 1.0, 1e-12);
    assert!(compute_face_normal(&[[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]]).is_none());
}

#[test]
fn splits_quad_consistently() {
    let v = vertices(&[
        (0, 0.0, 0.0, 0.0),
        (1, 1.0, 0.0, 0.0),
        (2, 1.0, 1.0, 0.0),
        (3, 0.0, 1.0, 0.0),
    ]);
    let triangles = triangulate_face_3d(&v, &[vec![0, 1, 2, 3]], [0.0, 0.0, 1.0]);
    assert_eq!(triangles, [[0, 1, 2], [0, 2, 3]]);
}

#[test]
fn concave() {
    let v = vertices(&[
        (0, 0.0, 0.0, 0.0),
        (1, 4.0, 0.0, 0.0),
        (2, 4.0, 2.0, 0.0),
        (3, 2.0, 2.0, 0.0),
        (4, 2.0, 4.0, 0.0),
        (5, 0.0, 4.0, 0.0),
    ]);
    let t = triangulate_face_3d(&v, &[vec![0, 1, 2, 3, 4, 5]], [0.0, 0.0, 1.0]);
    assert_eq!(t.len(), 4);
    assert_near(tri_area(&t, &v), 12.0, 1e-6);
}

// Ported from the C++ triangulator_test.cpp (openskp#495).
#[test]
fn concave_quad_splits_along_inside_diagonal() {
    // A dart of area 4 whose reflex corner is vertex 1: the 0-2 diagonal runs outside it.
    let v = vertices(&[
        (0, 4.0, 0.0, 0.0),
        (1, 1.0, 1.0, 0.0),
        (2, 0.0, 4.0, 0.0),
        (3, 0.0, 0.0, 0.0),
    ]);
    let t = triangulate_face_3d(&v, &[vec![0, 1, 2, 3]], [0.0, 0.0, 1.0]);
    assert_eq!(t.len(), 2);
    assert_near(tri_area(&t, &v), 4.0, 1e-9);
}

#[test]
fn hole() {
    let v = vertices(&[
        (0, 0.0, 0.0, 0.0),
        (1, 10.0, 0.0, 0.0),
        (2, 10.0, 10.0, 0.0),
        (3, 0.0, 10.0, 0.0),
        (10, 4.0, 4.0, 0.0),
        (11, 6.0, 4.0, 0.0),
        (12, 6.0, 6.0, 0.0),
        (13, 4.0, 6.0, 0.0),
    ]);
    let t = triangulate_face_3d(
        &v,
        &[vec![0, 1, 2, 3], vec![10, 11, 12, 13]],
        [0.0, 0.0, 1.0],
    );
    assert!(!t.is_empty());
    assert_near(tri_area(&t, &v), 96.0, 1e-6);
}

#[test]
fn a_missing_vertex_on_the_general_path_yields_nothing() {
    let v = vertices(&[
        (0, 0.0, 0.0, 0.0),
        (1, 1.0, 0.0, 0.0),
        (2, 1.0, 1.0, 0.0),
        (3, 0.0, 1.0, 0.0),
    ]);
    assert!(triangulate_face_3d(&v, &[vec![0, 1, 2, 3, 99]], [0.0, 0.0, 1.0]).is_empty());
    assert!(triangulate_face_3d(&v, &[], [0.0, 0.0, 1.0]).is_empty());
}

/// `n` vertices on a circle, ids from `first_id`.
fn add_circle(
    v: &mut BTreeMap<EntityId, Vertex>,
    first_id: EntityId,
    cx: f64,
    cy: f64,
    r: f64,
) -> Vec<EntityId> {
    let n = 24;
    (0..n)
        .map(|i| {
            let a = 2.0 * std::f64::consts::PI * f64::from(i) / f64::from(n);
            let id = first_id + EntityId::from(i);
            v.insert(
                id,
                Vertex {
                    id,
                    x: cx + r * a.cos(),
                    y: cy + r * a.sin(),
                    z: 0.0,
                },
            );
            id
        })
        .collect()
}

// Two overlapping circular holes (openskp#285): bridging each hole into the
// outer boundary independently produced a self-intersecting ring that the
// degenerate-vertex fallback then eroded; the area must instead match the
// closed-form circle-union area.
#[test]
fn overlapping_holes_match_independent_analytical_circle_union_area() {
    let r = 0.1969;
    let gap = 0.33 - 2.0 * r;
    assert!(
        gap < 0.0,
        "these two circles must genuinely overlap, not just sit close"
    );

    let (w, h) = (2.0, 4.0);
    let (c1x, c1y) = (1.0, 1.8345);
    let (c2x, c2y) = (1.0, 2.1655);

    let mut v = vertices(&[
        (0, 0.0, 0.0, 0.0),
        (1, w, 0.0, 0.0),
        (2, w, h, 0.0),
        (3, 0.0, h, 0.0),
    ]);
    let hole1 = add_circle(&mut v, 100, c1x, c1y, r);
    let hole2 = add_circle(&mut v, 200, c2x, c2y, r);

    let t = triangulate_face_3d(&v, &[vec![0, 1, 2, 3], hole1, hole2], [0.0, 0.0, 1.0]);
    assert!(!t.is_empty());

    let baked_area = tri_area(&t, &v);
    let dist = ((c1x - c2x) * (c1x - c2x) + (c1y - c2y) * (c1y - c2y)).sqrt();
    let inter =
        2.0 * r * r * (dist / (2.0 * r)).acos() - (dist / 2.0) * (4.0 * r * r - dist * dist).sqrt();
    let union_area = 2.0 * std::f64::consts::PI * r * r - inter;
    let expected_area = w * h - union_area;

    // Filtering is coarser at the cut than a clean union boundary; 5%
    // separates "correct, just coarser" from the pre-fix bug.
    let rel_error = (baked_area - expected_area).abs() / expected_area;
    assert!(
        rel_error < 0.05,
        "baked area {baked_area} vs analytical {expected_area} (rel err {rel_error})"
    );
}
