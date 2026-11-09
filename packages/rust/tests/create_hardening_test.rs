//! The writer rejects non-finite coordinates and other invalid input before writing anything.

use openskp::create::{
    create, ArcOptions, CircleOptions, FaceOptions, GuideOptions, PolylineOptions,
};
#[test]
fn add_face_rejects_nan_coordinates() {
    let mut builder = create();
    let pts = [
        [0.0, 0.0, 0.0],
        [f64::NAN, 0.0, 0.0],
        [10.0, 10.0, 0.0],
        [0.0, 10.0, 0.0],
    ];
    let result = builder.add_face(&pts, &FaceOptions::default());
    assert!(
        result.is_err(),
        "add_face must reject NaN coordinates in outer boundary"
    );
}

#[test]
fn add_face_rejects_nan_in_holes() {
    let mut builder = create();
    let face_pts = [
        [0.0, 0.0, 0.0],
        [100.0, 0.0, 0.0],
        [100.0, 100.0, 0.0],
        [0.0, 100.0, 0.0],
    ];
    let hole_with_nan = vec![[10.0, 10.0, 0.0], [f64::NAN, 10.0, 0.0], [20.0, 20.0, 0.0]];
    let opts = FaceOptions {
        holes: vec![hole_with_nan],
        ..Default::default()
    };
    let result = builder.add_face(&face_pts, &opts);
    assert!(
        result.is_err(),
        "add_face must reject NaN coordinates inside holes"
    );
}

#[test]
fn add_circle_rejects_non_finite_center_and_normal() {
    let mut builder = create();
    // Non-finite center
    assert!(
        builder
            .add_circle(
                [f64::NAN, 0.0, 0.0],
                [0.0, 0.0, 1.0],
                10.0,
                &CircleOptions::default(),
            )
            .is_err(),
        "add_circle must reject NaN center"
    );

    // Non-finite normal
    assert!(
        builder
            .add_circle(
                [0.0, 0.0, 0.0],
                [0.0, f64::INFINITY, 1.0],
                10.0,
                &CircleOptions::default(),
            )
            .is_err(),
        "add_circle must reject Infinity in normal"
    );

    // Non-finite radius
    assert!(
        builder
            .add_circle(
                [0.0, 0.0, 0.0],
                [0.0, 0.0, 1.0],
                f64::NAN,
                &CircleOptions::default(),
            )
            .is_err(),
        "add_circle must reject NaN radius"
    );
}

#[test]
fn add_circle_allows_negative_radius_for_cpp_parity() {
    let mut builder = create();
    // Negative radius is allowed in C++ implementation, must not be rejected here
    let result = builder.add_circle(
        [0.0, 0.0, 0.0],
        [0.0, 0.0, 1.0],
        -10.0,
        &CircleOptions::default(),
    );
    assert!(
        result.is_ok(),
        "add_circle must allow negative radius to maintain parity with C++ writer"
    );
}

#[test]
fn builders_reject_non_finite_coordinates() {
    let mut builder = create();

    // add_arc
    assert!(
        builder
            .add_arc(
                [0.0, 0.0, 0.0],
                [0.0, 0.0, 1.0],
                f64::NAN,
                0.0,
                1.0,
                &ArcOptions::default(),
            )
            .is_err(),
        "add_arc must reject NaN radius"
    );
    assert!(
        builder
            .add_arc(
                [f64::NAN, 0.0, 0.0],
                [0.0, 0.0, 1.0],
                10.0,
                0.0,
                1.0,
                &ArcOptions::default(),
            )
            .is_err(),
        "add_arc must reject NaN center"
    );

    // add_polyline
    assert!(
        builder
            .add_polyline(
                &[[0.0, 0.0, 0.0], [f64::NAN, 10.0, 0.0]],
                &PolylineOptions::default(),
            )
            .is_err(),
        "add_polyline must reject NaN point"
    );

    // add_construction_point
    assert!(
        builder
            .add_construction_point([0.0, f64::INFINITY, 0.0], &GuideOptions::default())
            .is_err(),
        "add_construction_point must reject Infinity"
    );

    // add_dimension
    assert!(
        builder
            .add_dimension([0.0, 0.0, 0.0], [f64::NAN, 0.0, 0.0], 1.0)
            .is_err(),
        "add_dimension must reject NaN point"
    );
}
