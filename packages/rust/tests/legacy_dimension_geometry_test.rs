// Legacy (MFC) linear dimensions keep their endpoints, plane frame and constrained offset.
// Ported from the C++ legacy_dimension_geometry_test.cpp (openskp#481, #474).
//
// The files come from this port's own writer (free dimensions: connection type 1, the point
// stored inline, null object refs), then get patched the way a real file can differ: a connection
// point that is a vertex written in full the first time it is referenced (a dimension comes before
// the geometry it is attached to), a back-reference to one, a forward reference, or an instance
// path. The last two cases write dimensions placed by an offset vector
// (`add_dimension_with_offset_vector`), at the root and inside a component definition.

mod common;

use common::{concat, f64s};
use openskp::create::{create, DefinitionOptions, GuideOptions, InstanceOptions, Point3};
use openskp::internal::read_u16;
use openskp::{ParseOptions, SkpFile, SkpModel, Vec3};

const A: Point3 = [1.0, 2.0, 3.0];
const B: Point3 = [4.0, 6.0, 3.0];
const C: Point3 = [11.0, 12.0, 13.0];
const D: Point3 = [14.0, 16.0, 13.0];

fn u32le(n: u32) -> Vec<u8> {
    n.to_le_bytes().to_vec()
}

fn tag(slot: u32, large: bool) -> Vec<u8> {
    if large {
        concat(&[&[0xff, 0x7f], &u32le(slot)])
    } else {
        (slot as u16).to_le_bytes().to_vec()
    }
}

fn point_bytes(p: Point3) -> Vec<u8> {
    f64s(&p)
}

fn connection(p: Point3) -> Vec<u8> {
    concat(&[&u32le(1), &u32le(4), &point_bytes(p)])
}

fn locate(bytes: &[u8], needle: &[u8]) -> usize {
    bytes
        .windows(needle.len())
        .position(|w| w == needle)
        .expect("dimension test record not found")
}

fn fixture() -> Vec<u8> {
    let mut builder = create();
    builder.add_dimension(A, B, -2.0).unwrap();
    builder.add_dimension(C, D, 3.0).unwrap();
    // A successful parse must retain entities after the annotations, too.
    builder
        .add_construction_point([101.0, 102.0, 103.0], &GuideOptions::default())
        .unwrap();
    builder.to_bytes().unwrap()
}

fn parse(bytes: Vec<u8>) -> SkpModel {
    SkpFile::from_buffer(bytes)
        .parse(&ParseOptions::default())
        .unwrap()
}

/// The second dimension shares the first one's font: a two-byte backref, followed by five
/// reserved bytes, then its first connection block.
fn font_slot(bytes: &[u8]) -> u32 {
    u32::from(read_u16(bytes, locate(bytes, &connection(C)) - 7).unwrap())
}

/// A CVertex object: an attribute-container ref, a v17+ PID mask, and xyz.
fn vertex(p: Point3, tag: &[u8]) -> Vec<u8> {
    concat(&[tag, &[0, 0, 0], &point_bytes(p)])
}

const VERTEX_CLASS: &[u8] = &[
    0xff, 0xff, 0, 0, 7, 0, b'C', b'V', b'e', b'r', b't', b'e', b'x',
];

/// Turns the free connection at `original` into a vertex connection whose object ref is `object`.
fn attach(bytes: &mut Vec<u8>, original: Point3, object: &[u8]) {
    let at = locate(bytes, &connection(original));
    bytes[at] = 2; // vertex connection, instead of a free point
    bytes[at + 8..at + 32].fill(0);
    bytes.splice(at + 32..at + 34, object.iter().copied()); // replaces the null object ref
}

fn expect_point(point: Option<Vec3>, expected: Point3) {
    let p = point.expect("point is set");
    for i in 0..3 {
        assert!((p[i] - expected[i]).abs() < 1e-9, "{p:?} != {expected:?}");
    }
}

fn expect_tail(model: &SkpModel) {
    let points = &model.root().construction_points;
    assert_eq!(points.len(), 1);
    assert_eq!(points[0].position, [101.0, 102.0, 103.0]);
}

#[test]
fn free_endpoints_and_frame_survive_parsing() {
    let model = parse(fixture());
    let dims = &model.root().dimensions;
    assert_eq!(dims.len(), 2);
    expect_point(dims[0].a, A);
    expect_point(dims[0].b, B);
    assert_eq!(dims[0].offset, -2.0);
    expect_point(dims[0].plane_x, [1.0, 0.0, 0.0]);
    expect_point(dims[0].normal, [0.0, 0.0, 1.0]);
    assert!(dims[0].offset_vector.is_none()); // free/aligned mode: do not invent an axis
    expect_point(dims[1].a, C);
    expect_point(dims[1].b, D);
    expect_tail(&model);
}

#[test]
fn inline_vertices_and_later_backrefs_keep_archive_aligned() {
    for large in [false, true] {
        let mut bytes = fixture();
        let font = font_slot(&bytes);
        // Inline class declaration, new object of that class (short/big form), then reuse those
        // vertices in the next dimension (short/big backrefs).
        attach(&mut bytes, D, &tag(font + 3, large));
        attach(&mut bytes, C, &tag(font + 2, large));
        let new_tag = if large { 0x8000_0000 } else { 0x8000 } | (font + 1);
        attach(&mut bytes, B, &vertex(B, &tag(new_tag, large)));
        attach(&mut bytes, A, &vertex(A, VERTEX_CLASS));
        let model = parse(bytes);
        assert_eq!(model.root().dimensions.len(), 2, "large {large}");
        for dim in &model.root().dimensions {
            expect_point(dim.a, A);
            expect_point(dim.b, B);
        }
        expect_tail(&model);
    }
}

#[test]
fn forward_vertex_references_resolve_after_archive_walk() {
    let mut bytes = fixture();
    let font = font_slot(&bytes);
    // First dimension's B defines the vertex class and object. The second dimension then
    // allocates itself, followed by the vertex referenced by A.
    attach(&mut bytes, C, &vertex(C, &tag(0x8000 | (font + 1), false)));
    attach(&mut bytes, B, &vertex(B, VERTEX_CLASS));
    attach(&mut bytes, A, &tag(font + 4, true));
    let model = parse(bytes);
    let dims = &model.root().dimensions;
    assert_eq!(dims.len(), 2);
    expect_point(dims[0].a, C);
    expect_point(dims[0].b, B);
    expect_point(dims[1].a, C);
    expect_tail(&model);
}

#[test]
fn plane_axes_preserve_signed_offset_with_reversed_endpoints() {
    for axis in [1u32, 2, 3] {
        for reversed in [false, true] {
            let mut builder = create();
            let (p1, p2) = if reversed { (B, A) } else { (A, B) };
            builder.add_dimension(p1, p2, -2.0).unwrap();
            let mut bytes = builder.to_bytes().unwrap();
            let at = locate(&bytes, &connection(p2)) + 32 + 2 + 10;
            // After the second connection's paths: normal, X axis, constrained axis.
            let frame = concat(&[&f64s(&[0.0, -1.0, 0.0, 1.0, 0.0, 0.0]), &u32le(axis)]);
            bytes[at..at + frame.len()].copy_from_slice(&frame);
            let model = parse(bytes);
            assert_eq!(model.root().dimensions.len(), 1);
            let dim = &model.root().dimensions[0];
            expect_point(dim.normal, [0.0, -1.0, 0.0]);
            expect_point(dim.plane_x, [1.0, 0.0, 0.0]);
            match axis {
                1 => expect_point(dim.offset_vector, [0.0, 0.0, -2.0]),
                2 => expect_point(dim.offset_vector, [2.0, 0.0, 0.0]),
                _ => assert!(dim.offset_vector.is_none(), "axis {axis}"),
            }
        }
    }
}

#[test]
fn nested_paths_are_consumed_without_publishing_local_coordinates() {
    let mut bytes = fixture();
    // Replace the first connection's empty first path with one unresolved instance ref. Endpoint
    // B and all following entities must still parse.
    let at = locate(&bytes, &connection(A)) + 32 + 2 + 2;
    bytes[at] = 1;
    let path = tag(1234, true);
    bytes.splice(at + 4..at + 4, path);
    let model = parse(bytes);
    let dims = &model.root().dimensions;
    assert_eq!(dims.len(), 2);
    assert!(dims[0].a.is_none());
    expect_point(dims[0].b, B);
    expect_point(dims[1].a, C);
    expect_tail(&model);
}

#[test]
fn vector_offsets_survive_root_and_nested_round_trips() {
    // (a, b, offset vector, expected offset vector: its part perpendicular to a->b)
    let samples: [(Point3, Point3, Point3, Point3); 9] = [
        (
            [1.0, 2.0, 3.0],
            [11.0, 2.0, 3.0],
            [0.0, 0.0, 4.0],
            [0.0, 0.0, 4.0],
        ),
        (
            [11.0, 2.0, 3.0],
            [1.0, 2.0, 3.0],
            [0.0, 0.0, -4.0],
            [0.0, 0.0, -4.0],
        ),
        (
            [1.0, 2.0, 3.0],
            [1.0, 2.0, 13.0],
            [-4.0, 0.0, 0.0],
            [-4.0, 0.0, 0.0],
        ),
        (
            [1.0, 2.0, 3.0],
            [4.0, 6.0, 8.0],
            [-4.0, 3.0, 0.0],
            [-4.0, 3.0, 0.0],
        ),
        (
            [1.0, 2.0, 3.0],
            [11.0, 2.0, 3.0],
            [7.0, -4.0, 0.0],
            [0.0, -4.0, 0.0],
        ),
        (
            [1.0, 2.0, 3.0],
            [1.0, 2.0, 13.0],
            [0.0, 0.0, 0.0],
            [0.0, 0.0, 0.0],
        ),
        (
            [1.0, 2.0, 3.0],
            [11.0, 2.0, 3.0],
            [7.0, 0.0, 0.0],
            [0.0, 0.0, 0.0],
        ),
        (
            [1.0, 2.0, 3.0],
            [4.0, 6.0, 8.0],
            [3.0, 4.0, 5.0],
            [0.0, 0.0, 0.0],
        ),
        (
            [1.0, 2.0, 3.0],
            [11.0, 2.0, 3.0],
            [0.0, 0.0, 1e-12],
            [0.0, 0.0, 1e-12],
        ),
    ];
    let mut builder = create();
    let mut definition = builder
        .add_component_definition("Dimensions", &DefinitionOptions::default())
        .unwrap();
    for (a, b, offset, _) in samples {
        definition
            .add_dimension_with_offset_vector(a, b, offset)
            .unwrap();
    }
    let definition = definition.close().unwrap();
    builder
        .add_instance(definition, &InstanceOptions::default())
        .unwrap();
    for (a, b, offset, _) in samples {
        builder
            .add_dimension_with_offset_vector(a, b, offset)
            .unwrap();
    }
    let model = parse(builder.to_bytes().unwrap());
    assert_eq!(model.root().dimensions.len(), samples.len());
    assert_eq!(model.definitions.len(), 1);
    let nested = &model.definitions.values().next().unwrap().dimensions;
    assert_eq!(nested.len(), samples.len());
    for dims in [&model.root().dimensions, nested] {
        for (dim, &(a, b, _, expected)) in dims.iter().zip(&samples) {
            expect_point(dim.a, a);
            expect_point(dim.b, b);
            expect_point(dim.offset_vector, expected);
        }
    }
}

#[test]
fn invalid_vector_dimension_does_not_corrupt_following_records() {
    let (a, b) = (A, B);
    let mut builder = create();
    let mut definition = builder
        .add_component_definition("Dimensions", &DefinitionOptions::default())
        .unwrap();
    for bad in [f64::INFINITY, f64::NAN] {
        assert!(definition
            .add_dimension_with_offset_vector(a, b, [bad, 0.0, 0.0])
            .is_err());
    }
    assert!(definition
        .add_dimension_with_offset_vector(a, a, [0.0, 0.0, 1.0])
        .is_err());
    definition
        .add_dimension_with_offset_vector(a, b, [0.0, 0.0, 2.0])
        .unwrap();
    let definition = definition.close().unwrap();
    builder
        .add_instance(definition, &InstanceOptions::default())
        .unwrap();
    for bad in [f64::INFINITY, f64::NAN] {
        assert!(builder
            .add_dimension_with_offset_vector([bad, 0.0, 0.0], b, [0.0, 0.0, 1.0])
            .is_err());
    }
    builder
        .add_dimension_with_offset_vector(a, b, [0.0, 0.0, 3.0])
        .unwrap();
    let model = parse(builder.to_bytes().unwrap());
    assert_eq!(model.root().dimensions.len(), 1);
    assert_eq!(model.definitions.len(), 1);
    let nested = &model.definitions.values().next().unwrap().dimensions;
    assert_eq!(nested.len(), 1);
    expect_point(model.root().dimensions[0].offset_vector, [0.0, 0.0, 3.0]);
    expect_point(nested[0].offset_vector, [0.0, 0.0, 2.0]);
}
