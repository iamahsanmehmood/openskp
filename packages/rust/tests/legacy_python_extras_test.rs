// What the Python reader surfaces from a classic file beyond the C++ one,
// ported from Python's test_edge_surfacing.py,
// test_loose_edge_runs_public_api.py and test_arc_curves.py, plus the typed
// attribute values of legacy.py's `read_typed`:
//
// * each edge's own layer (the drawbase layer field the walker always read
//   and then dropped) - the only layer signal a curve-only model has;
// * each face's own raw layer id;
// * SketchUp's own Edge#curve grouping (the CCurve/CArcCurve pointer) and the
//   14 doubles of a classic CArcCurve;
// * attribute values with their SketchUp type (Time, Length, Point3d,
//   Vector3d, nested Array) instead of folded into Integer / Vec.
//
// Every count below was read off these exact files. A count on its own is not
// enough to trust a table - it can fill with plausible-looking garbage and
// still count right - so each is paired with a relation the file itself
// forces.

mod common;

use std::collections::{BTreeMap, BTreeSet};

use common::fixture;
use openskp::create::{
    create, AttributeValue, DefinitionOptions, FaceOptions, InstanceOptions, LayerOptions,
};
use openskp::internal::{
    full_parse, read_legacy_attribute_value, GeometryBuilder, RawArcFrame, RawParsed,
};
use openskp::{AttributeKind, EntityId, ParseOptions, ParsedAttribute, SkpFile, Vec3};

const CAPILLA: &str = "capilla_quiroz_v17.skp"; // v17, circles only, one layer
const GONDOLA: &str = "gondola_v20.skp"; // v20, circles + ellipses, two layers

fn raw(name: &str) -> RawParsed {
    full_parse(&common::read_fixture(name), &ParseOptions::default()).unwrap()
}

/// Every definition's geometry builder, the root's included.
fn builders(p: &RawParsed) -> Vec<&GeometryBuilder> {
    p.definitions
        .values()
        .map(|d| &d.builder)
        .chain(std::iter::once(&p.root.builder))
        .collect()
}

/// One per-definition table merged across every definition.
fn table<V: Clone>(
    p: &RawParsed,
    f: impl Fn(&GeometryBuilder) -> &BTreeMap<EntityId, V>,
) -> BTreeMap<EntityId, V> {
    let mut merged = BTreeMap::new();
    for b in builders(p) {
        merged.extend(f(b).iter().map(|(k, v)| (*k, v.clone())));
    }
    merged
}

fn face_layers(p: &RawParsed) -> BTreeMap<Option<EntityId>, usize> {
    let mut counts = BTreeMap::new();
    for b in builders(p) {
        for f in b.faces.values() {
            *counts.entry(f.layer_id).or_default() += 1;
        }
    }
    counts
}

fn norm(v: Vec3) -> f64 {
    (v[0] * v[0] + v[1] * v[1] + v[2] * v[2]).sqrt()
}

fn cross(a: Vec3, b: Vec3) -> Vec3 {
    [
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    ]
}

fn dist(a: Vec3, b: Vec3) -> f64 {
    norm([a[0] - b[0], a[1] - b[1], a[2] - b[2]])
}

fn near(a: f64, b: f64, tol: f64) -> bool {
    (a - b).abs() <= tol
}

// ── edge layers ──────────────────────────────────────────────────────────

#[test]
fn classic_edges_carry_their_layer() {
    // gondola: 1102 of its edges sit on layer 43. The walker stores only
    // non-default ids, so 1102 is the number of *layered* edges.
    let layers = table(&raw(GONDOLA), |b| &b.edge_layers);
    assert_eq!(layers.len(), 1102);
    assert_eq!(
        layers.values().copied().collect::<BTreeSet<_>>(),
        BTreeSet::from([43])
    );
}

#[test]
fn a_single_layer_file_leaves_the_edge_layer_table_empty() {
    // capilla puts every edge on its default layer, so no id is stored.
    assert!(table(&raw(CAPILLA), |b| &b.edge_layers).is_empty());
}

#[test]
fn edge_layer_resolves_to_its_name_in_the_model() {
    // gondola's layered edges are on "Gondulas Laterais"
    // (test_loose_edge_runs_public_api / test_loose_edge_curve_sets).
    let model = SkpFile::open(fixture(GONDOLA))
        .unwrap()
        .parse(&ParseOptions::default())
        .unwrap();
    let layered: Vec<_> = model
        .definitions
        .values()
        .flat_map(|d| d.edges.values())
        .filter(|e| e.layer_id.is_some())
        .collect();
    assert_eq!(layered.len(), 1102);
    assert!(layered
        .iter()
        .all(|e| e.layer_id == Some(43) && e.layer == "Gondulas Laterais"));
    let unlayered = model
        .definitions
        .values()
        .flat_map(|d| d.edges.values())
        .find(|e| e.layer_id.is_none())
        .unwrap();
    assert_eq!(unlayered.layer, "");
}

// ── face layers ──────────────────────────────────────────────────────────

#[test]
fn classic_faces_carry_their_own_layer() {
    // 229 of gondola's 1887 faces are on layer 43; the other 1658 report the
    // file's default (0).
    assert_eq!(
        face_layers(&raw(GONDOLA)),
        BTreeMap::from([(Some(0), 1658), (Some(43), 229)])
    );
}

#[test]
fn default_layer_zero_is_read_not_dropped() {
    // capilla is single-layer: the value is the file's own 0, not None.
    assert_eq!(face_layers(&raw(CAPILLA)), BTreeMap::from([(Some(0), 181)]));
}

#[test]
fn face_layer_id_reaches_the_model() {
    let model = SkpFile::open(fixture(GONDOLA))
        .unwrap()
        .parse(&ParseOptions::default())
        .unwrap();
    let faces: Vec<_> = model
        .definitions
        .values()
        .flat_map(|d| d.faces.values())
        .collect();
    assert!(faces.iter().any(|f| f.layer_id == Some(43)));
    // Face::layer keeps the C++ contract for classic files (scene metadata
    // reads it): only the raw id is surfaced.
    assert!(faces.iter().all(|f| f.layer.is_empty()));
}

// ── curve grouping ───────────────────────────────────────────────────────

#[test]
fn classic_curve_pointers_and_their_ids() {
    // capilla: 39 edges belong to 6 Curves. All six are arcs, so the ids the
    // edges name must be exactly the ids the arc table is keyed by - two
    // independently decoded tables agreeing on one id space.
    let p = raw(CAPILLA);
    let curves = table(&p, |b| &b.edge_curves);
    assert_eq!(curves.len(), 39);
    assert!(curves.values().all(|&c| c > 0));
    let ids: BTreeSet<EntityId> = curves.values().copied().collect();
    assert_eq!(ids.len(), 6);
    assert_eq!(ids, table(&p, |b| &b.arc_curves).keys().copied().collect());
}

#[test]
fn the_two_curve_tables_are_independent() {
    // gondola: 1480 edges spread over 219 Curves, of which only 30 are arcs.
    let p = raw(GONDOLA);
    let curves = table(&p, |b| &b.edge_curves);
    assert_eq!(curves.len(), 1480);
    let ids: BTreeSet<EntityId> = curves.values().copied().collect();
    assert_eq!(ids.len(), 219);
    let arcs: BTreeSet<EntityId> = table(&p, |b| &b.arc_curves).keys().copied().collect();
    assert_eq!(arcs.len(), 30);
    assert!(!ids.is_subset(&arcs));
}

// ── arc frames ───────────────────────────────────────────────────────────

#[test]
fn arc_frame_layout_is_the_files_own() {
    // center[3], normal[3], x_axis[3], start, end, y_axis[3]: split the 14
    // doubles any other way and the file's own relations stop holding.
    let frames: Vec<RawArcFrame> = table(&raw(CAPILLA), |b| &b.arc_curves)
        .into_values()
        .collect();
    assert_eq!(frames.len(), 6);
    for f in &frames {
        assert!(near(norm(f.normal), 1.0, 1e-9));
        // y_axis == normal x x_axis, exactly, on a circle.
        assert!(near(dist(cross(f.normal, f.x_axis), f.y_axis), 0.0, 1e-9));
        assert!(near(norm(f.x_axis), 28.827831, 1e-5));
        assert!(near(norm(f.x_axis), norm(f.y_axis), 1e-5));
        assert!(near(f.start_angle, 0.0, 1e-12));
    }
    let ends: BTreeSet<i64> = frames
        .iter()
        .map(|f| (f.end_angle * 1e6).round() as i64)
        .collect();
    assert_eq!(ends, BTreeSet::from([829263, 837889]));
}

#[test]
fn elliptical_frames_are_kept_not_dropped() {
    // An arc scaled non-uniformly stores the affine image of a circle:
    // |x_axis| != |y_axis|. gondola has 16 of 30.
    let frames: Vec<RawArcFrame> = table(&raw(GONDOLA), |b| &b.arc_curves)
        .into_values()
        .collect();
    assert_eq!(frames.len(), 30);
    assert!(frames.iter().all(|f| near(norm(f.normal), 1.0, 1e-9)));
    let elliptical = frames
        .iter()
        .filter(|f| (norm(f.x_axis) - norm(f.y_axis)).abs() > 1e-9)
        .count();
    assert_eq!(elliptical, 16);
    let max = frames
        .iter()
        .map(|f| norm(f.x_axis))
        .fold(f64::MIN, f64::max);
    let min = frames
        .iter()
        .map(|f| norm(f.x_axis))
        .fold(f64::MAX, f64::min);
    assert!(near(max, 106.849109, 1e-5));
    assert!(near(min, 1.291375, 1e-5));
    let worst = frames
        .iter()
        .map(|f| dist(cross(f.normal, f.x_axis), f.y_axis))
        .fold(f64::MIN, f64::max);
    assert!(near(worst, 53.424554, 1e-5));
}

#[test]
fn edge_curve_ids_and_arcs_reach_the_model() {
    let model = SkpFile::open(fixture(GONDOLA))
        .unwrap()
        .parse(&ParseOptions::default())
        .unwrap();
    let all_edges = || {
        model
            .definitions
            .values()
            .chain(std::iter::once(model.root()))
            .flat_map(|d| d.edges.values())
    };
    assert_eq!(all_edges().filter(|e| e.curve_id.is_some()).count(), 1480);
    let arcs: usize = model
        .definitions
        .values()
        .chain(std::iter::once(model.root()))
        .map(|d| d.arcs.len())
        .sum();
    assert_eq!(arcs, 30);
}

// ── typed attribute values ───────────────────────────────────────────────

fn utf16_record(s: &str) -> Vec<u8> {
    let units: Vec<u16> = s.encode_utf16().collect();
    let mut out = vec![0xff, 0xfe, 0xff, units.len() as u8];
    for u in units {
        out.extend_from_slice(&u.to_le_bytes());
    }
    out
}

fn f64s(v: &[f64]) -> Vec<u8> {
    v.iter().flat_map(|x| x.to_le_bytes()).collect()
}

#[test]
fn every_legacy_attribute_type_keeps_its_kind() {
    let decode = |t: u8, bytes: &[u8]| {
        let (v, next) = read_legacy_attribute_value(bytes, 0, t).unwrap();
        assert_eq!(next, bytes.len(), "type {t} consumed the whole value");
        v
    };
    assert_eq!(decode(0, &[]), ParsedAttribute::Null);
    assert_eq!(
        decode(4, &(-7i32).to_le_bytes()),
        ParsedAttribute::Integer(-7)
    );
    assert_eq!(decode(6, &f64s(&[2.5])), ParsedAttribute::Float(2.5));
    assert_eq!(decode(7, &[1]), ParsedAttribute::Boolean(true));
    assert_eq!(decode(7, &[0]), ParsedAttribute::Boolean(false));
    let time = decode(9, &1_700_000_000u32.to_le_bytes());
    assert_eq!(time, ParsedAttribute::Time(1_700_000_000));
    assert_eq!(time.kind(), AttributeKind::Integer);
    assert_eq!(time.to_string(), "1700000000");
    assert_eq!(
        decode(10, &utf16_record("caf\u{e9}")),
        ParsedAttribute::String("caf\u{e9}".into())
    );
    let length = decode(12, &f64s(&[39.375]));
    assert_eq!(length, ParsedAttribute::Length(39.375));
    assert_eq!(length.kind(), AttributeKind::Length);
    assert_eq!(
        decode(17, &f64s(&[1.0, 2.0, 3.0])),
        ParsedAttribute::Point3d([1.0, 2.0, 3.0])
    );
    assert_eq!(
        decode(18, &f64s(&[0.0, 0.0, 1.0])),
        ParsedAttribute::Vector3d([0.0, 0.0, 1.0])
    );

    // a nested array: [4, [Length 1.5, "x"], None]
    let mut arr = 3u32.to_le_bytes().to_vec();
    arr.push(4);
    arr.extend_from_slice(&4i32.to_le_bytes());
    arr.push(11);
    arr.extend_from_slice(&2u32.to_le_bytes());
    arr.push(12);
    arr.extend_from_slice(&f64s(&[1.5]));
    arr.push(10);
    arr.extend_from_slice(&utf16_record("x"));
    arr.push(0);
    let array = decode(11, &arr);
    assert_eq!(
        array,
        ParsedAttribute::Array(vec![
            ParsedAttribute::Integer(4),
            ParsedAttribute::Array(vec![
                ParsedAttribute::Length(1.5),
                ParsedAttribute::String("x".into())
            ]),
            ParsedAttribute::Null,
        ])
    );
    assert_eq!(array.to_string(), "4,1.5,x,");
}

#[test]
fn unknown_or_oversized_attribute_values_are_errors() {
    assert!(read_legacy_attribute_value(&[0; 8], 0, 0x42).is_err());
    assert!(read_legacy_attribute_value(&200_000u32.to_le_bytes(), 0, 11).is_err());
    assert!(read_legacy_attribute_value(&[1, 2], 0, 6).is_err()); // truncated
}

#[test]
fn attribute_values_round_trip_through_a_written_classic_file_with_their_types() {
    let mut b = create();
    let roof = b.add_layer("Roof", &LayerOptions::default()).unwrap();
    let mut def = b
        .add_component_definition("Box", &DefinitionOptions::default())
        .unwrap();
    def.add_face(
        &[
            [0.0, 0.0, 0.0],
            [10.0, 0.0, 0.0],
            [10.0, 10.0, 0.0],
            [0.0, 10.0, 0.0],
        ],
        &FaceOptions {
            layer: Some(roof),
            ..Default::default()
        },
    )
    .unwrap();
    let handle = def.close().unwrap();
    let attributes = BTreeMap::from([
        ("name".to_owned(), AttributeValue::String("panel".into())),
        ("count".to_owned(), AttributeValue::Int32(-3)),
        ("ratio".to_owned(), AttributeValue::Double(0.25)),
        ("enabled".to_owned(), AttributeValue::Bool(true)),
        ("width".to_owned(), AttributeValue::Length(12.5)),
    ]);
    b.add_instance(
        handle,
        &InstanceOptions {
            attributes,
            attribute_dict_name: "spec".into(),
            ..Default::default()
        },
    )
    .unwrap();
    let bytes = b.to_bytes().unwrap();
    let parsed = full_parse(&bytes, &ParseOptions::default()).unwrap();

    let model = SkpFile::from_buffer(bytes)
        .parse(&ParseOptions::default())
        .unwrap();
    let inst = &model.root().instances[0];
    let spec = &inst.attribute_dictionaries["spec"];
    assert_eq!(spec["name"], ParsedAttribute::String("panel".into()));
    assert_eq!(spec["count"], ParsedAttribute::Integer(-3));
    assert_eq!(spec["ratio"], ParsedAttribute::Float(0.25));
    assert_eq!(spec["enabled"], ParsedAttribute::Boolean(true));
    assert_eq!(spec["width"], ParsedAttribute::Length(12.5));

    // the face's own layer id is the Roof layer's slot
    let def = model
        .definitions
        .values()
        .find(|d| d.name == "Box")
        .unwrap();
    let face = def.faces.values().next().unwrap();
    let layer_id = face.layer_id.expect("face layer id");
    assert_eq!(parsed.layer_id_to_name[&layer_id], "Roof");
}
