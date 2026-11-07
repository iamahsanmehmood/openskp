// Public model types: C++ defaults, ParsedAttribute formatting (std::to_chars
// shortest round-trip), ArcCurve helpers and the error text.

use std::f64::consts::PI;

use openskp::model::format_double;
use openskp::{
    ArcCurve, AttributeKind, ConstructionLine, Layer, Material, Page, ParseStage, ParsedAttribute,
    SectionPlane, SkpModel, SkpParseError, ViewCamera,
};

#[test]
fn format_double_matches_to_chars() {
    let cases: [(f64, &str); 18] = [
        (0.0, "0"),
        (-0.0, "-0"),
        (120.0, "120"),
        (15.5, "15.5"),
        (-7.25, "-7.25"),
        (0.807085, "0.807085"),
        (0.65, "0.65"),
        (1e20, "1e+20"),
        (1e16, "1e+16"),
        (123456789012.0, "123456789012"),
        (123000.0, "123000"),
        (1e-7, "1e-07"),
        (0.0001, "1e-04"),
        (0.001, "0.001"),
        (1.5e300, "1.5e+300"),
        (5e-324, "5e-324"),
        (f64::INFINITY, "inf"),
        (f64::NEG_INFINITY, "-inf"),
    ];
    for (v, want) in cases {
        assert_eq!(format_double(v), want, "{v:e}");
    }
    assert_eq!(format_double(f64::NAN), "nan");
}

#[test]
fn parsed_attribute_kinds_and_text() {
    assert_eq!(
        ParsedAttribute::Time(1_700_000_000).to_string(),
        "1700000000"
    );
    assert_eq!(ParsedAttribute::Time(5).kind(), AttributeKind::Integer);
    assert_eq!(ParsedAttribute::Boolean(true).to_string(), "1");
    assert_eq!(ParsedAttribute::Length(2.5).to_string(), "2.5");
    assert_eq!(
        ParsedAttribute::Vector3d([1.0, -0.5, 1e20]).to_string(),
        "1,-0.5,1e+20"
    );
    assert_eq!(
        ParsedAttribute::Point3d([0.0; 3]).kind(),
        AttributeKind::Vec
    );
    assert_eq!(ParsedAttribute::Null.to_string(), "");
    assert_eq!(ParsedAttribute::from("x"), "x");
    assert_ne!(ParsedAttribute::Integer(1), "1");
    let nested = ParsedAttribute::Array(vec![
        ParsedAttribute::Array(vec![ParsedAttribute::Integer(1), ParsedAttribute::Null]),
        ParsedAttribute::String("s".into()),
    ]);
    assert_eq!(nested.to_string(), "1,,s");
}

#[test]
fn arc_curve_full_circle_in_both_conventions() {
    let arc = |start: f64, end: f64| ArcCurve {
        x_axis: [3.0, 4.0, 0.0],
        start_angle: start,
        end_angle: end,
        ..Default::default()
    };
    assert_eq!(arc(0.0, 0.0).radius(), 5.0);
    assert!(arc(0.0, 0.0).is_full_circle()); // classic
    assert!(arc(0.3, 0.3 + 2.0 * PI).is_full_circle()); // 2021+
    assert!(!arc(0.0, PI).is_full_circle());
    assert!(!arc(0.0, -PI / 2.0).is_full_circle());
}

#[test]
fn defaults_match_the_cpp_initializers() {
    assert_eq!(Layer::default().color, [200, 200, 200]);
    let m = Material::default();
    assert_eq!(m.color, [200, 200, 200, 255]);
    assert_eq!(m.transparency, 1.0);
    assert_eq!(SectionPlane::default().plane, [0.0, 0.0, 1.0, 0.0]);
    assert_eq!(ConstructionLine::default().direction, [1.0, 0.0, 0.0]);
    assert_eq!(Page::default().fov, 35.0);
    let cam = ViewCamera::default();
    assert_eq!(
        (cam.eye, cam.up, cam.fov),
        ([0.0, 0.0, 1.0], [0.0, 0.0, 1.0], 35.0)
    );
    let model = SkpModel::default();
    assert_eq!(model.version, "unknown");
    assert_eq!(model.root().guid, "ROOT");
    assert_eq!(model.root().name, "ROOT_MODEL");
    assert!(model.material_by_id(1).is_none());
}

#[test]
fn error_text_carries_every_context_field() {
    let e = SkpParseError::new("Failed while processing record")
        .with_stage(ParseStage::TlvWalk)
        .with_record(4, 9)
        .with_tag("")
        .with_offset(255);
    assert_eq!(
        e.to_string(),
        "Failed while processing record | stage=tlv_walk | record=4/9 | tag= | offset=0xff"
    );
    assert_eq!(e.message(), "Failed while processing record");
    assert_eq!(e.record_index(), Some(4));
    assert_eq!(e.tag(), Some(""));
}
