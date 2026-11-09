// Baking loose edges into the scene, and the analytic arc behind one (Python
// port extension, `test_loose_edge_curve_sets.py`).
//
// * `chain_loose_edges` groups edges by the file's own Curve, never by vertex
//   adjacency: two Curves touching at a vertex are two Curves.
// * `solve_run_arc` returns a frame only when the file proves the run is one
//   whole circular arc, and `None` otherwise - the safe answer, since the
//   caller then emits the chords the file stores. Every test expecting an arc
//   is paired with one expecting `None` for a similar-looking case.

mod common;

use std::collections::{BTreeMap, BTreeSet};

use common::{fixture, read_fixture};
use openskp::curves::{chain_loose_edges, solve_run_arc, LooseRun, INCHES_TO_M};
use openskp::internal::{full_parse, GeometryBuilder, RawArcFrame, RawFace};
use openskp::{CoEdge, CurveArc, EntityId, ParseOptions, SkpFile, Vec3};

// The arc fixture's own frame: |x_axis| == |y_axis| == 40 inches.
const RADIUS_IN: f64 = 40.0;
const IDENTITY: [f64; 13] = [
    1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0,
];
const PI: f64 = std::f64::consts::PI;

fn builder(
    edges: &[(EntityId, EntityId, EntityId)],
    edge_curves: &[(EntityId, EntityId)],
    arc_curves: &[(EntityId, RawArcFrame)],
) -> GeometryBuilder {
    GeometryBuilder {
        edges: edges
            .iter()
            .map(|&(e, a, b)| (e, (Some(a), Some(b))))
            .collect(),
        edge_curves: edge_curves.iter().copied().collect(),
        arc_curves: arc_curves.iter().copied().collect(),
        ..Default::default()
    }
}

/// A circular frame in the layout the readers produce.
fn frame(radius: f64, sweep: f64) -> RawArcFrame {
    RawArcFrame {
        center: [0.0, 0.0, 0.0],
        normal: [0.0, 0.0, 1.0],
        x_axis: [radius, 0.0, 0.0],
        y_axis: [0.0, radius, 0.0],
        start_angle: 0.0,
        end_angle: sweep,
    }
}

/// Where `t` lands in the caller's world-space glTF-Y-up metre frame.
fn arc_point(t: f64, radius: f64) -> Vec3 {
    [
        radius * t.cos() * INCHES_TO_M,
        0.0,
        -radius * t.sin() * INCHES_TO_M,
    ]
}

fn approx(a: &Vec3, b: &Vec3) -> bool {
    (0..3).all(|k| (a[k] - b[k]).abs() <= (1e-6 * b[k].abs()).max(1e-12))
}

fn solve(b: &GeometryBuilder, eids: &[EntityId], pts: &[Vec3], closed: bool) -> Option<CurveArc> {
    solve_run_arc(b, eids, Some(&IDENTITY), pts, closed)
}

/// A closed run of `n` vertices spread evenly over a full turn.
fn closed_run(n: usize) -> Option<CurveArc> {
    let eids: Vec<EntityId> = (1..=n as EntityId).collect();
    let curves: Vec<(EntityId, EntityId)> = eids.iter().map(|&e| (e, 17)).collect();
    let b = builder(&[], &curves, &[(17, frame(RADIUS_IN, 2.0 * PI))]);
    let pts: Vec<Vec3> = (0..n)
        .map(|i| arc_point(2.0 * PI * i as f64 / n as f64, RADIUS_IN))
        .collect();
    solve(&b, &eids, &pts, true)
}

fn run(edge_ids: &[EntityId], vertex_ids: &[EntityId], closed: bool) -> LooseRun {
    LooseRun {
        edge_ids: edge_ids.to_vec(),
        vertex_ids: vertex_ids.to_vec(),
        closed,
    }
}

// ── chaining: grouping comes from the file, not from vertex adjacency ────

// Vertex ids stand in for the Python test's "a", "b", "c", "d".
const A: EntityId = 1;
const B: EntityId = 2;
const C: EntityId = 3;
const D: EntityId = 4;

#[test]
fn two_curves_touching_at_a_vertex_stay_two_runs() {
    let b = builder(&[(1, A, B), (2, B, C)], &[(1, 10), (2, 11)], &[]);
    let mut lens: Vec<usize> = chain_loose_edges(&b)
        .iter()
        .map(|r| r.vertex_ids.len())
        .collect();
    lens.sort();
    assert_eq!(lens, [2, 2]);
}

#[test]
fn an_edge_with_no_curve_is_its_own_run() {
    let b = builder(&[(1, A, B), (2, B, C)], &[], &[]);
    assert_eq!(
        chain_loose_edges(&b),
        [run(&[1], &[A, B], false), run(&[2], &[B, C], false)]
    );
}

#[test]
fn edges_used_by_a_face_are_not_loose() {
    let mut b = builder(&[(1, A, B), (2, B, C)], &[], &[]);
    b.faces.insert(
        9,
        RawFace {
            loops: vec![vec![CoEdge {
                edge_id: 1,
                orientation: 1,
            }]],
            ..Default::default()
        },
    );
    assert_eq!(chain_loose_edges(&b), [run(&[2], &[B, C], false)]);
}

#[test]
fn a_curve_is_ordered_end_to_end() {
    let b = builder(
        &[(1, A, B), (2, B, C), (3, C, D)],
        &[(1, 10), (2, 10), (3, 10)],
        &[],
    );
    assert_eq!(
        chain_loose_edges(&b),
        [run(&[1, 2, 3], &[A, B, C, D], false)]
    );
}

#[test]
fn a_closed_curve_does_not_repeat_its_first_vertex() {
    let b = builder(
        &[(1, A, B), (2, B, C), (3, C, A)],
        &[(1, 10), (2, 10), (3, 10)],
        &[],
    );
    let runs = chain_loose_edges(&b);
    assert_eq!(runs.len(), 1);
    assert!(runs[0].closed);
    assert_eq!(runs[0].vertex_ids, [A, B, C]);
}

#[test]
fn degenerate_and_dangling_edges_are_skipped() {
    let mut b = builder(&[(1, A, A), (3, B, C)], &[], &[]);
    b.edges.insert(2, (Some(A), None));
    assert_eq!(chain_loose_edges(&b), [run(&[3], &[B, C], false)]);
}

// ── the parser's own curves drive the chaining ───────────────────────────

/// One definition's actual parser tables with its faces removed, which makes
/// its curved edges loose.
fn real_loose_builder(data: &[u8], definition: &str) -> GeometryBuilder {
    let parsed = full_parse(data, &ParseOptions::default()).unwrap();
    let d = parsed
        .definitions
        .values()
        .find(|d| d.name == definition)
        .expect("definition");
    GeometryBuilder {
        edges: d.builder.edges.clone(),
        edge_curves: d.builder.edge_curves.clone(),
        arc_curves: d.builder.arc_curves.clone(),
        ..Default::default()
    }
}

fn check_curves_become_runs(b: &GeometryBuilder, expected: (usize, usize, usize, usize, usize)) {
    let runs = chain_loose_edges(b);
    let curves: BTreeSet<EntityId> = b.edge_curves.values().copied().collect();
    let unchained = b.edges.len() - b.edge_curves.len();
    assert_eq!((unchained, curves.len()), (expected.0, expected.1));
    assert_eq!(runs.len(), unchained + curves.len());
    assert_eq!(
        runs.iter().filter(|r| r.vertex_ids.len() > 2).count(),
        expected.2
    );
    assert_eq!(
        runs.iter().map(|r| r.vertex_ids.len()).max(),
        Some(expected.3)
    );
    assert_eq!(runs.iter().filter(|r| r.closed).count(), expected.4);
}

// Untitled, "Group240#2": 442 edges, 336 in 14 Curves, read through BB0B.
#[test]
fn a_vff_definitions_curves_become_runs() {
    let b = real_loose_builder(&read_fixture("Untitled.skp"), "Group240#2");
    check_curves_become_runs(&b, (106, 14, 14, 24, 14));
}

// gondola, "Laura": 995 edges, 929 of them in 154 Curves (21 single-edge).
#[test]
fn a_classic_definitions_curves_become_runs() {
    let b = real_loose_builder(&read_fixture("gondola_v20.skp"), "Laura");
    check_curves_become_runs(&b, (66, 154, 133, 42, 1));
}

// ── solve_run_arc accepts runs the file proves are one circular arc ──────

#[test]
fn whole_curve_on_a_circular_frame() {
    let b = builder(&[], &[(1, 17)], &[(17, frame(RADIUS_IN, PI / 2.0))]);
    let pts: Vec<Vec3> = [0.0, PI / 8.0, PI / 4.0, 3.0 * PI / 8.0, PI / 2.0]
        .iter()
        .map(|&t| arc_point(t, RADIUS_IN))
        .collect();
    let arc = solve(&b, &[1], &pts, false).expect("arc");
    assert_eq!(arc.segments, [[0, 1, 2]]);
    assert_eq!(arc.points.len(), 3);
    // The frame's own ends and sweep midpoint, not the stored vertices.
    assert!(approx(&arc.points[0], &arc_point(0.0, RADIUS_IN)));
    assert!(approx(&arc.points[1], &arc_point(PI / 4.0, RADIUS_IN)));
    assert!(approx(&arc.points[2], &arc_point(PI / 2.0, RADIUS_IN)));
}

#[test]
fn a_full_turn_becomes_two_half_turns() {
    let arc = closed_run(24).expect("arc");
    assert_eq!(arc.segments, [[0, 1, 2], [2, 3, 0]]);
    assert_eq!(arc.points.len(), 4);
}

// A 4-sided circle's closing step comes out ~2e-16 over a bare pi/2 bound.
#[test]
fn a_coarse_circle_is_not_refused_on_a_float_boundary() {
    let arc = closed_run(4).expect("arc");
    assert_eq!(arc.segments, [[0, 1, 2], [2, 3, 0]]);
}

#[test]
fn local_space_arcs_get_only_the_axis_swap() {
    let b = builder(&[], &[(1, 17)], &[(17, frame(RADIUS_IN, PI / 2.0))]);
    let pts = [arc_point(0.0, RADIUS_IN), arc_point(PI / 2.0, RADIUS_IN)];
    let arc = solve_run_arc(&b, &[1], None, &pts, false).expect("arc");
    assert!(approx(&arc.points[1], &arc_point(PI / 4.0, RADIUS_IN)));
}

// ── ... and refuses the rest (the chords are kept) ───────────────────────

#[test]
fn a_split_curve_has_no_single_arc() {
    let b = builder(
        &[],
        &[(1, 17), (2, 17)],
        &[(17, frame(RADIUS_IN, PI / 2.0))],
    );
    let pts = [arc_point(0.0, RADIUS_IN), arc_point(PI / 8.0, RADIUS_IN)];
    assert!(solve(&b, &[1], &pts, false).is_none());
}

#[test]
fn an_elliptical_frame_is_refused() {
    let mut f = frame(RADIUS_IN, PI / 2.0);
    f.y_axis = [0.0, RADIUS_IN / 2.0, 0.0];
    let b = builder(&[], &[(1, 17)], &[(17, f)]);
    let pts = [arc_point(0.0, RADIUS_IN), arc_point(PI / 8.0, RADIUS_IN)];
    assert!(solve(&b, &[1], &pts, false).is_none());
}

#[test]
fn a_vertex_off_the_circle_is_refused() {
    let b = builder(&[], &[(1, 17)], &[(17, frame(RADIUS_IN, PI / 2.0))]);
    let mut pts = [arc_point(0.0, RADIUS_IN), arc_point(PI / 8.0, RADIUS_IN)];
    pts[1][1] += 0.01; // 10 mm off, along the arc plane's normal
    assert!(solve(&b, &[1], &pts, false).is_none());
}

#[test]
fn rounding_noise_is_not_mistaken_for_a_disagreement() {
    let b = builder(&[], &[(1, 17)], &[(17, frame(RADIUS_IN, PI / 2.0))]);
    let mut pts = [arc_point(0.0, RADIUS_IN), arc_point(PI / 8.0, RADIUS_IN)];
    pts[1][1] += 1e-6;
    assert!(solve(&b, &[1], &pts, false).is_some());
}

// The tolerance is max(1e-5, 1e-6 * r): on a ~2.5 km radius a vertex 1 mm
// off the circle is still that circle, and one 10 mm off is not.
#[test]
fn the_tolerance_is_relative_on_a_large_arc() {
    let radius = 100000.0; // inches
    let b = builder(&[], &[(1, 17)], &[(17, frame(radius, PI / 2.0))]);
    let start = arc_point(0.0, radius);
    for (off, accepted) in [(0.001, true), (0.010, false)] {
        // In the plane, along the radius.
        let pts = [
            [start[0] + off, start[1], start[2]],
            arc_point(PI / 8.0, radius),
        ];
        assert_eq!(
            solve(&b, &[1], &pts, false).is_some(),
            accepted,
            "{off} m off the circle"
        );
    }
}

#[test]
fn an_edge_outside_the_arc_table_is_refused() {
    let b = builder(&[], &[(1, 17)], &[(17, frame(RADIUS_IN, PI / 2.0))]);
    let pts = [arc_point(0.0, RADIUS_IN), arc_point(PI / 8.0, RADIUS_IN)];
    assert!(solve(&b, &[2], &pts, false).is_none());
}

#[test]
fn a_plain_curve_is_not_an_arc() {
    let b = builder(&[], &[(1, 17)], &[]);
    let pts = [arc_point(0.0, RADIUS_IN), arc_point(PI / 8.0, RADIUS_IN)];
    assert!(solve(&b, &[1], &pts, false).is_none());
}

// ── end to end: a real file's loose edges arrive in the scene ────────────

fn build_scene(name: &str) -> openskp::Scene {
    SkpFile::open(fixture(name))
        .unwrap()
        .build_scene(&ParseOptions::default())
        .unwrap()
}

#[test]
fn a_classic_file_bakes_its_loose_edges() {
    let sc = build_scene("capilla_quiroz_v17.skp");
    assert_eq!(sc.curve_sets.len(), 20);
    assert!(sc.curve_sets.iter().all(|cs| cs.points_m.len() == 2));
    assert!(sc
        .curve_sets
        .iter()
        .all(|cs| !cs.closed && cs.arc.is_none()));
}

// Root-level loose geometry has no name in the file: it is named from its
// layer and a sequence number, flagged as generated.
#[test]
fn root_runs_are_named_from_layer_and_sequence() {
    let sc = build_scene("capilla_quiroz_v17.skp");
    let root: Vec<_> = sc
        .curve_sets
        .iter()
        .filter(|cs| cs.path == "ROOT")
        .collect();
    assert_eq!(root.len(), 5);
    let mut names: Vec<&str> = root.iter().map(|cs| cs.name.as_str()).collect();
    names.sort();
    assert_eq!(
        names,
        ["Layer0_1", "Layer0_2", "Layer0_3", "Layer0_4", "Layer0_5"]
    );
    assert!(root.iter().all(|cs| cs.name_is_generated));
}

#[test]
fn runs_inside_a_component_keep_the_real_name() {
    let sc = build_scene("capilla_quiroz_v17.skp");
    let inner: Vec<_> = sc
        .curve_sets
        .iter()
        .filter(|cs| cs.path == "ROOT / puerta")
        .collect();
    assert_eq!(inner.len(), 15);
    assert!(inner
        .iter()
        .all(|cs| cs.name == "puerta" && cs.definition_name == "puerta" && !cs.name_is_generated));
}

// gondola's layer 43 resolves to "Gondulas Laterais" and 1102 of its edges
// sit on it.
#[test]
fn an_edge_layer_reaches_the_curve_set() {
    let sc = build_scene("gondola_v20.skp");
    let layers: BTreeSet<&str> = sc.curve_sets.iter().map(|cs| cs.layer.as_str()).collect();
    assert_eq!(layers, BTreeSet::from(["Gondulas Laterais", "Layer0"]));
    assert_eq!(
        sc.curve_sets
            .iter()
            .filter(|cs| cs.layer == "Gondulas Laterais")
            .count(),
        57232
    );
}

// A VFF file's loose edges (counts cross-checked against the Python
// reference on the same file): 526 open runs, all inside placed groups,
// each resolved to its edges' own layer.
#[test]
fn a_vff_file_bakes_its_loose_edges() {
    let sc = build_scene("Untitled.skp");
    assert_eq!(sc.curve_sets.len(), 526);
    let mut per_layer: BTreeMap<&str, usize> = BTreeMap::new();
    for cs in &sc.curve_sets {
        assert!(cs.points_m.len() >= 2);
        assert!(!cs.name_is_generated && !cs.closed && cs.arc.is_none());
        *per_layer.entry(cs.layer.as_str()).or_default() += 1;
    }
    let expected: BTreeMap<&str, usize> = [
        ("BottomPlate", 36),
        ("HeaderPlate1", 6),
        ("KingStud", 48),
        ("Nog", 320),
        ("Stud", 86),
        ("TopPlate", 28),
        ("VerticalHeaderStud", 2),
    ]
    .into();
    assert_eq!(per_layer, expected);
}

// arcs_2021.skp's one loose arc, at the model root: a 13-point open run that
// recovers as one analytic arc.
#[test]
fn a_vff_root_arc_is_recovered() {
    let sc = build_scene("arcs_2021.skp");
    assert_eq!(sc.curve_sets.len(), 1);
    let cs = &sc.curve_sets[0];
    assert_eq!(
        (cs.name.as_str(), cs.layer.as_str(), cs.path.as_str()),
        ("Layer0_1", "Layer0", "ROOT")
    );
    assert!(cs.name_is_generated && !cs.closed);
    assert_eq!(cs.points_m.len(), 13);
    let arc = cs.arc.as_ref().expect("arc");
    assert_eq!(arc.segments, [[0, 1, 2]]);
    // The arc's ends are the run's own ends (up to the 6-decimal rounding of
    // the stored points).
    assert!((0..3).all(|k| (arc.points[0][k] - cs.points_m[0][k]).abs() < 1e-5));
    assert!((0..3).all(|k| (arc.points[2][k] - cs.points_m[12][k]).abs() < 1e-5));
}

// A closed 24-segment circle inside a group comes back as two half-turns.
#[test]
fn a_vff_closed_circle_becomes_two_half_turns() {
    let sc = build_scene("coedge_orientation_regression.skp");
    assert_eq!(sc.curve_sets.len(), 1200);
    let arcs: Vec<_> = sc.curve_sets.iter().filter(|cs| cs.arc.is_some()).collect();
    assert_eq!(arcs.len(), 5);
    let circle = arcs.iter().find(|cs| cs.closed).expect("closed arc");
    assert_eq!(circle.name, "Gruppo#195");
    assert_eq!(circle.points_m.len(), 24);
    assert_eq!(
        circle.arc.as_ref().unwrap().segments,
        [[0, 1, 2], [2, 3, 0]]
    );
}
