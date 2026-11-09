// Analytic arcs (Definition::arcs) joined to their edges by curve id. The
// three fixtures hold the same drawing (a quarter arc r=10 at x=0, a full
// circle r=5 at x=50, a half arc r=8 at x=100) saved as 2017, 2020 and 2021.

mod common;

use std::collections::BTreeMap;
use std::f64::consts::PI;

use common::{assert_near, fixture};
use openskp::{ArcCurve, Definition, EntityId, ParseOptions, SkpFile, SkpModel};

struct Expected {
    cx: f64,
    radius: f64,
    sweep: f64,
    edges: usize,
    full: bool,
}

const EXPECTED: [Expected; 3] = [
    Expected {
        cx: 0.0,
        radius: 10.0,
        sweep: PI / 2.0,
        edges: 12,
        full: false,
    },
    Expected {
        cx: 50.0,
        radius: 5.0,
        sweep: 2.0 * PI,
        edges: 24,
        full: true,
    },
    Expected {
        cx: 100.0,
        radius: 8.0,
        sweep: PI,
        edges: 12,
        full: false,
    },
];

fn parse(name: &str) -> SkpModel {
    SkpFile::open(fixture(name))
        .unwrap()
        .parse(&ParseOptions::default())
        .unwrap()
}

fn sorted_arcs(d: &Definition) -> Vec<(EntityId, ArcCurve)> {
    let mut arcs: Vec<(EntityId, ArcCurve)> = d.arcs.iter().map(|(k, v)| (*k, *v)).collect();
    arcs.sort_by(|a, b| a.1.center[0].total_cmp(&b.1.center[0]));
    arcs
}

fn check_arcs(name: &str) {
    let model = parse(name);
    let arcs = sorted_arcs(model.root());
    assert_eq!(arcs.len(), 3, "{name}");
    for (i, (_, a)) in arcs.iter().enumerate() {
        let e = &EXPECTED[i];
        assert_near(a.center[0], e.cx, 1e-9);
        assert_near(a.center[1], 0.0, 1e-9);
        assert_near(a.center[2], 0.0, 1e-9);
        assert_near(a.normal[2], 1.0, 1e-9);
        assert_near(a.radius(), e.radius, 1e-9);
        assert_near(a.start_angle, 0.0, 1e-9);
        assert_near(
            (a.end_angle - a.start_angle) % (2.0 * PI),
            e.sweep % (2.0 * PI),
            1e-9,
        );
        assert_eq!(a.is_full_circle(), e.full, "{name}");
        assert_near(a.x_axis[0], e.radius, 1e-9);
        assert_near(a.y_axis[1], e.radius, 1e-9);
    }
}

fn check_joined(name: &str) {
    let model = parse(name);
    let mut per_curve: BTreeMap<EntityId, usize> = BTreeMap::new();
    for e in model.root().edges.values() {
        if let Some(c) = e.curve_id {
            *per_curve.entry(c).or_default() += 1;
        }
    }
    let arcs = sorted_arcs(model.root());
    assert_eq!(arcs.len(), 3, "{name}");
    for (i, (id, _)) in arcs.iter().enumerate() {
        assert_eq!(
            per_curve.get(id).copied().unwrap_or(0),
            EXPECTED[i].edges,
            "{name}"
        );
    }
}

#[test]
fn every_arc_is_read_with_its_centre_radius_and_angles_vff() {
    check_arcs("arcs_2021.skp");
}

#[test]
fn every_arc_is_read_with_its_centre_radius_and_angles_legacy() {
    check_arcs("arcs_2017.skp");
    check_arcs("arcs_2020.skp");
}

#[test]
fn an_arc_is_joined_to_its_edges_by_curve_id_vff() {
    check_joined("arcs_2021.skp");
}

#[test]
fn an_arc_is_joined_to_its_edges_by_curve_id_legacy() {
    check_joined("arcs_2017.skp");
    check_joined("arcs_2020.skp");
}

fn check_no_arcs(name: &str) {
    let model = parse(name);
    assert!(model.root().arcs.is_empty(), "{name}");
    assert!(
        model.definitions.values().all(|d| d.arcs.is_empty()),
        "{name}"
    );
}

#[test]
fn a_model_without_arcs_has_none_vff() {
    check_no_arcs("instance_scale_term_2021.skp");
}

#[test]
fn a_model_without_arcs_has_none_legacy() {
    check_no_arcs("instance_scale_term_v6.skp");
}

#[test]
fn untitled_has_368_arcs_each_the_circle_sketchup_reports() {
    let model = parse("Untitled.skp");
    let mut total = 0;
    let mut check = |d: &Definition| {
        let mut per_curve: BTreeMap<EntityId, usize> = BTreeMap::new();
        for e in d.edges.values() {
            if let Some(c) = e.curve_id {
                *per_curve.entry(c).or_default() += 1;
            }
        }
        for (id, a) in &d.arcs {
            total += 1;
            assert_eq!(per_curve.get(id).copied().unwrap_or(0), 24);
            assert!(a.is_full_circle());
        }
    };
    for d in model.definitions.values() {
        check(d);
    }
    check(model.root());
    assert_eq!(total, 368);
}
