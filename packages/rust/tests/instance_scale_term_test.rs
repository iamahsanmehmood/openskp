// An instance can be scaled through the 13-value matrix's scale term (index
// 12 = 1/w) instead of through its 3x3; the translation is then in the scaled
// units (openskp#444). Two cubes, one at the origin and one placed 2000 units
// out with a 0.01 scale term: SketchUp shows the second 20 inches away.

mod common;

use common::{assert_near, fixture};
use openskp::internal::{fold_scale, multiply_matrices, transform_point};
use openskp::{ParseOptions, SkpFile};

const INCH_MM: f64 = 25.4;

#[test]
fn scales_the_translated_point() {
    let m = [
        1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 2000.0, 0.0, 0.0, 0.01,
    ];
    let p = transform_point(&m, [100.0, 100.0, 100.0]);
    assert_near(p[0], 21.0, 1e-9);
    assert_near(p[1], 1.0, 1e-9);
    assert_near(p[2], 1.0, 1e-9);
}

#[test]
fn a_scale_of_one_changes_nothing() {
    let m = [
        2.0, 0.0, 0.0, 0.0, 3.0, 0.0, 0.0, 0.0, 4.0, 5.0, 6.0, 7.0, 1.0,
    ];
    assert_eq!(transform_point(&m, [1.0, 1.0, 1.0]), [7.0, 9.0, 11.0]);
    assert_eq!(fold_scale(&m), m.to_vec());
}

#[test]
fn scaling_through_the_3x3_and_through_the_term_agree() {
    let via3x3 = [
        0.01, 0.0, 0.0, 0.0, 0.01, 0.0, 0.0, 0.0, 0.01, 0.0, 0.0, 0.0, 1.0,
    ];
    let via_term = [
        1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.01,
    ];
    let a = transform_point(&via3x3, [50.0, 20.0, 10.0]);
    let b = transform_point(&via_term, [50.0, 20.0, 10.0]);
    for i in 0..3 {
        assert_near(a[i], b[i], 1e-9);
    }
}

#[test]
fn composition_matches_applying_one_transform_after_the_other() {
    let mut state = 12345u64;
    let mut next = move || {
        state = state
            .wrapping_mul(6364136223846793005)
            .wrapping_add(1442695040888963407);
        (state >> 11) as f64 / (1u64 << 53) as f64
    };
    let scales = [1.0, 1.0, 0.01, 0.5, 2.0, 0.1];
    let random_matrix = |next: &mut dyn FnMut() -> f64| {
        let mut m: Vec<f64> = (0..9).map(|_| next() * 4.0 - 2.0).collect();
        m.extend((0..3).map(|_| next() * 100.0 - 50.0));
        m.push(scales[(next() * 6.0) as usize % 6]);
        m
    };
    for k in 0..40 {
        let parent = random_matrix(&mut next);
        let child = random_matrix(&mut next);
        let p = [
            next() * 200.0 - 100.0,
            next() * 200.0 - 100.0,
            next() * 200.0 - 100.0,
        ];
        let composed = multiply_matrices(&parent, &child);
        let want = transform_point(&parent, transform_point(&child, p));
        let got = transform_point(&composed, p);
        for i in 0..3 {
            assert!((got[i] - want[i]).abs() <= 1e-6, "case {k}");
        }
    }
}

#[test]
fn a_composed_matrix_has_its_scale_folded_in() {
    let out = multiply_matrices(
        &[
            1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.01,
        ],
        &[
            1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 2000.0, 0.0, 0.0, 1.0,
        ],
    );
    assert_eq!(out[12], 1.0);
    assert_near(out[9], 20.0, 1e-9);
}

fn the_parse_still_reports_the_raw_matrix(file: &str) {
    let model = SkpFile::open(fixture(file))
        .unwrap()
        .parse(&ParseOptions::default())
        .unwrap();
    let found = model.root().instances.iter().any(|i| {
        i.matrix.len() > 12
            && (i.matrix[12] - 0.01).abs() < 1e-12
            && (i.matrix[9] - 2000.0).abs() < 1e-9
    });
    assert!(found, "{file}");
}

#[test]
fn the_parse_still_reports_the_raw_matrix_vff() {
    the_parse_still_reports_the_raw_matrix("instance_scale_term_2021.skp");
}

#[test]
fn the_parse_still_reports_the_raw_matrix_legacy() {
    the_parse_still_reports_the_raw_matrix("instance_scale_term_v6.skp");
}

fn the_scene_puts_both_cubes_where_sketchup_does(file: &str) {
    let scene = SkpFile::open(fixture(file))
        .unwrap()
        .build_scene(&ParseOptions::default())
        .unwrap();
    let mut xs: Vec<f64> = scene
        .scene_hierarchy
        .children
        .iter()
        .map(|n| n.position_mm[0])
        .collect();
    xs.sort_by(f64::total_cmp);
    assert_eq!(xs.len(), 2, "{file}");
    assert_near(xs[0], 0.0, 1e-6);
    assert_near(xs[1], 20.0 * INCH_MM, 1e-6); // 508 mm, not 50,800
}

#[test]
fn the_scene_puts_both_cubes_where_sketchup_does_vff() {
    the_scene_puts_both_cubes_where_sketchup_does("instance_scale_term_2021.skp");
}

#[test]
fn the_scene_puts_both_cubes_where_sketchup_does_legacy() {
    the_scene_puts_both_cubes_where_sketchup_does("instance_scale_term_v6.skp");
}

fn the_instanced_scene_node_matrices_carry_the_scale_and_the_translation(file: &str) {
    let scene = SkpFile::open(fixture(file))
        .unwrap()
        .build_instanced_scene(&ParseOptions::default())
        .unwrap();
    let placed = &scene.scene_hierarchy.children;
    assert_eq!(placed.len(), 2, "{file}");
    for n in placed {
        assert_near(n.matrix[0], 0.01, 1e-9);
        assert_near(n.matrix[5], 0.01, 1e-9);
        assert_near(n.matrix[10], 0.01, 1e-9);
    }
    let mut tx: Vec<f64> = placed.iter().map(|n| n.matrix[12]).collect();
    tx.sort_by(f64::total_cmp);
    assert_near(tx[0], 0.0, 1e-9);
    assert_near(tx[1], 20.0 * INCH_MM / 1000.0, 1e-9); // 0.508 m, not 50.8
}

#[test]
fn the_instanced_scene_node_matrices_carry_the_scale_and_the_translation_vff() {
    the_instanced_scene_node_matrices_carry_the_scale_and_the_translation(
        "instance_scale_term_2021.skp",
    );
}

#[test]
fn the_instanced_scene_node_matrices_carry_the_scale_and_the_translation_legacy() {
    the_instanced_scene_node_matrices_carry_the_scale_and_the_translation(
        "instance_scale_term_v6.skp",
    );
}
