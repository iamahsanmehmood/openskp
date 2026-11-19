mod common;

use openskp::internal::{
    extract_version, is_legacy, legacy_instance_has_guid, multiply_matrices, parse_tlv_recursive,
    parse_varint, read_f64, read_u16, read_u32, transform_determinant, transform_normal,
    transform_point, valid_header, ErrorKind,
};

#[test]
fn tlv_checked_reads_and_varints() {
    let b = [0x34u8, 0x12, 0x78, 0x56, 0x34, 0x12];
    assert_eq!(read_u16(&b, 0).unwrap(), 0x1234);
    assert_eq!(read_u32(&b, 2).unwrap(), 0x12345678);
    assert_eq!(parse_varint(&b, 0, 2).unwrap(), 0x1234);
    assert_eq!(read_u32(&b, 4).unwrap_err().kind, ErrorKind::OutOfRange);
    assert_eq!(
        parse_varint(&b, 0, 9).unwrap_err().kind,
        ErrorKind::OutOfRange
    );
    let wide = [0u8; 9];
    assert_eq!(
        parse_varint(&wide, 0, 9).unwrap_err().kind,
        ErrorKind::Overflow
    );
}

#[test]
fn tlv_reads_float64_little_endian() {
    let data = [0u8, 0, 0, 0, 0, 0, 0xf0, 0x3f];
    assert_eq!(read_f64(&data, 0).unwrap(), 1.0);
    assert_eq!(read_f64(&data, 1).unwrap_err().kind, ErrorKind::OutOfRange);
}

#[test]
fn tlv_recursive_containers() {
    let b = [0xF4u8, 0x01, 0x07, 0, 0, 0, 0xAA, 0x00, 0x01, 0, 0, 0, 0x42];
    let n = parse_tlv_recursive(&b, 0, b.len()).unwrap();
    assert_eq!(n.len(), 1);
    assert_eq!(n[0].children.len(), 1);
    assert_eq!(n[0].children[0].payload, vec![0x42]);
}

#[test]
fn vff_header_and_version() {
    let mut d = vec![0xffu8, 0xfe, 0xff, 0x0e, 0xff, 0xfe, 0xff, 0];
    let v = "{25.0.575}";
    for c in v.bytes() {
        d.push(c);
        d.push(0);
    }
    assert!(valid_header(&d));
    assert_eq!(extract_version(&d), v);
    assert!(!valid_header(&[0xff, 0xff, 0xff, 0x0e]));
}

#[test]
fn legacy_detects_classic_container() {
    assert!(is_legacy(&common::read_fixture("capilla_quiroz_v17.skp")));
    assert!(!is_legacy(&common::read_fixture("Untitled.skp")));
}

// A class-schema gate looked plausible but doesn't hold across real files
// (openskp#284/#391) - the file's own version is the only signal that does:
// pre-2014 files never carry the trailing GUID, 2014+ always do.
#[test]
fn legacy_gates_instance_guid_by_file_version() {
    assert!(!legacy_instance_has_guid(7));
    assert!(!legacy_instance_has_guid(13));
    assert!(legacy_instance_has_guid(14));
    assert!(legacy_instance_has_guid(25));
}

#[test]
fn transforms_composition() {
    let a = [
        1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 2.0, 3.0, 4.0, 1.0,
    ];
    let b = [
        1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 5.0, 6.0, 7.0, 1.0,
    ];
    let m = multiply_matrices(&a, &b);
    assert_eq!(transform_point(&m, [1.0, 1.0, 1.0]), [8.0, 10.0, 12.0]);
}

#[test]
fn transforms_translates_point() {
    let matrix = [
        1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 5.0, 10.0, -2.0, 1.0,
    ];
    assert_eq!(transform_point(&matrix, [1.0, 2.0, 3.0]), [6.0, 12.0, 1.0]);
}

#[test]
fn transforms_detects_mirrors_and_transforms_normals_by_inverse_transpose() {
    let matrix = [
        -2.0, 0.0, 0.0, 0.0, 3.0, 0.0, 0.0, 0.0, 4.0, 0.0, 0.0, 0.0, 1.0,
    ];
    assert_eq!(transform_determinant(&matrix), -24.0);
    assert_eq!(
        transform_normal(&matrix, [1.0, 1.0, 1.0]),
        [-12.0, 8.0, 6.0]
    );
}
