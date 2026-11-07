mod common;

use std::collections::BTreeMap;

use common::{bytes, concat, f64s, tlv};
use openskp::internal::{
    collect_definitions, collect_geometry, parse_tlv_recursive, GeometryBuilder, RawDefinition,
};
use openskp::{AttributeKind, ParsedAttrDictionaries, ParsedAttribute};

fn geometry(b: &[u8]) -> GeometryBuilder {
    let mut builder = GeometryBuilder::default();
    collect_geometry(&parse_tlv_recursive(b, 0, b.len()).unwrap(), &mut builder).unwrap();
    builder
}

fn uv_payload(front: Option<&[f64]>, back: Option<&[f64]>) -> Vec<u8> {
    let side = |tag: &str, m: &[f64]| {
        tlv(
            tag,
            &tlv(
                "1327",
                &concat(&[&tlv("1427", &[1]), &tlv("1527", &f64s(m))]),
            ),
        )
    };
    let mut sides = Vec::new();
    if let Some(f) = front {
        sides.extend(side("1127", f));
    }
    if let Some(b) = back {
        sides.extend(side("1227", b));
    }
    concat(&[
        &tlv("DE05", &[0x2a]),
        &tlv("DD05", &tlv("B136", &tlv("B236", &tlv("1027", &sides)))),
    ])
}

#[test]
fn reads_instance_material_and_default() {
    let painted = geometry(&tlv(
        "6419",
        &concat(&[
            &tlv("6719", &[5]),
            &tlv("D007", &tlv("D107", &[0x33, 0x73])),
        ]),
    ));
    assert_eq!(painted.instances.len(), 1);
    assert_eq!(painted.instances[0].ref_idx, Some(5));
    assert_eq!(painted.instances[0].material_id, Some(0x7333));

    let unpainted = geometry(&tlv("6419", &tlv("6719", &[5])));
    assert_eq!(unpainted.instances.len(), 1);
    assert!(unpainted.instances[0].material_id.is_none());
}

#[test]
fn extracts_front_and_back_uv_transforms() {
    let front = [0.0, 1.0, 0.0, -1.0, 0.0, 0.0, 96.0, -96.0, 1.0];
    let back: Vec<f64> = front.iter().map(|v| v * 2.0).collect();
    let face_data = concat(&[
        &tlv("DE05", &[0x2a]),
        &tlv("D007", &tlv("DC05", &uv_payload(Some(&front), Some(&back)))),
    ]);
    let builder = geometry(&tlv("AC0D", &face_data));
    let face = &builder.faces[&0x2a];
    assert_eq!(face.uv_transform.unwrap().to_vec(), front.to_vec());
    assert_eq!(face.uv_transform_back.unwrap().to_vec(), back);
}

#[test]
fn leaves_uv_transforms_empty_without_mapping_block() {
    let builder = geometry(&tlv(
        "AC0D",
        &concat(&[
            &tlv("DE05", &[0x2a]),
            &tlv("D007", &tlv("DC05", &tlv("DE05", &[0x2a]))),
        ]),
    ));
    let face = &builder.faces[&0x2a];
    assert!(face.uv_transform.is_none());
    assert!(face.uv_transform_back.is_none());
}

#[test]
fn extracts_wrapped_image_placement() {
    let builder = geometry(&tlv("9013", &tlv("401F", &tlv("6419", &tlv("6719", &[7])))));
    assert_eq!(builder.instances.len(), 1);
    assert_eq!(builder.instances[0].ref_idx, Some(7));
}

#[test]
fn marks_image_and_always_faces_camera_definitions() {
    let image = tlv(
        "7C15",
        &concat(&[
            &tlv("DE05", &[1]),
            &tlv("7E15", &bytes("imagen#1")),
            &tlv("8315", &[2]),
        ]),
    );
    let billboard = tlv(
        "7C15",
        &concat(&[
            &tlv("DE05", &[2]),
            &tlv("7E15", &bytes("Susan")),
            &tlv("581B", &concat(&[&tlv("5D1B", &[1]), &tlv("5E1B", &[1])])),
        ]),
    );
    let ordinary = tlv(
        "7C15",
        &concat(&[
            &tlv("DE05", &[3]),
            &tlv("7E15", &bytes("Chair")),
            &tlv("581B", &concat(&[&tlv("5D1B", &[0]), &tlv("5E1B", &[0])])),
        ]),
    );
    let all = concat(&[&image, &billboard, &ordinary]);
    let mut definitions: BTreeMap<i64, RawDefinition> = BTreeMap::new();
    collect_definitions(
        &parse_tlv_recursive(&all, 0, all.len()).unwrap(),
        &mut definitions,
    )
    .unwrap();

    assert!(definitions[&1].is_image);
    assert!(!definitions[&2].is_image);
    assert!(definitions[&2].always_faces_camera);
    assert!(definitions[&2].shadows_face_sun);
    assert!(!definitions[&3].always_faces_camera);
    assert!(!definitions[&3].shadows_face_sun);
}

#[test]
fn extracts_back_material_and_edge_flags() {
    let face_builder = geometry(&tlv(
        "AC0D",
        &concat(&[&tlv("DE05", &[0x2a]), &tlv("AF0D", &[0x85, 0x8b, 0x06])]),
    ));
    let face = &face_builder.faces[&0x2a];
    assert!(face.material_id.is_none());
    assert_eq!(face.back_material_id, Some(0x068b85));

    let edge = |id: u8, flags: u8| {
        tlv(
            "B80B",
            &concat(&[&tlv("DE05", &[id]), &tlv("D007", &tlv("D307", &[flags]))]),
        )
    };
    let edge_builder = geometry(&concat(&[&edge(1, 0x06), &edge(2, 0x07), &edge(3, 0x1e)]));
    assert_eq!(edge_builder.edge_flags[&1], 0x06);
    assert_eq!(edge_builder.edge_flags[&2], 0x07);
    assert_eq!(edge_builder.edge_flags[&3], 0x1e);
}

// Python port extension: an edge's own D007 > D207 layer id and its BB0B curve.
#[test]
fn extracts_edge_layer_and_curve() {
    let e = tlv(
        "B80B",
        &concat(&[
            &tlv("DE05", &[9]),
            &tlv("D007", &tlv("D207", &[0x12, 0x01])),
            &tlv("BB0B", &[0x40]),
        ]),
    );
    let b = geometry(&e);
    assert_eq!(b.edge_layers[&9], 0x0112);
    assert_eq!(b.edge_curves[&9], 0x40);
}

#[test]
fn section_plane_text_dimension_defaults_empty() {
    let builder = GeometryBuilder::default();
    assert!(builder.section_planes.is_empty());
    assert!(builder.texts.is_empty());
    assert!(builder.dimensions.is_empty());
}

// Multi-value-type decoding for attribute dictionaries. A 6419 instance's
// D007/DC05 payload holds a B436 (dictionary name) node followed by a sibling
// B536 (entries) node holding B636 (key) / A438 (type-tagged value) pairs.

fn entry_value(inner: &[u8]) -> Vec<u8> {
    tlv("A438", inner)
}

fn entry(key: &str, inner: &[u8]) -> Vec<u8> {
    concat(&[&tlv("B636", &bytes(key)), &entry_value(inner)])
}

fn named_dict(name: &str, entries: &[u8]) -> Vec<u8> {
    concat(&[&tlv("B436", &bytes(name)), &tlv("B536", entries)])
}

fn attribute_dicts_for(dc05: &[u8]) -> ParsedAttrDictionaries {
    geometry(&tlv("6419", &tlv("D007", &tlv("DC05", dc05))))
        .instances
        .remove(0)
        .attribute_dicts
}

#[test]
fn attribute_dictionaries_group_entries_by_dictionary_name() {
    let dicts = attribute_dicts_for(&named_dict(
        "fbd-einfo",
        &entry("code", &tlv("AD38", &bytes("Ks"))),
    ));
    assert_eq!(dicts["fbd-einfo"]["code"], "Ks");
}

// Every named dictionary lands on attribute_dicts with native types;
// properties remains the stringified view of dynamic_attributes.
#[test]
fn attribute_dictionaries_keep_two_dictionaries_distinct() {
    let builder = geometry(&tlv(
        "6419",
        &tlv(
            "D007",
            &tlv(
                "DC05",
                &concat(&[
                    &named_dict(
                        "dynamic_attributes",
                        &entry("width", &tlv("AD38", &bytes("10"))),
                    ),
                    &named_dict("SU_InstanceSet", &entry("Owner", &tlv("AD38", &bytes("")))),
                    &named_dict("FrameBuilder", &entry("name", &tlv("AD38", &bytes("W-2")))),
                ]),
            ),
        ),
    ));
    let instance = &builder.instances[0];
    assert_eq!(instance.properties["width"], "10");
    assert_eq!(instance.attribute_dicts["FrameBuilder"]["name"], "W-2");
    assert_eq!(
        instance.attribute_dicts["dynamic_attributes"]["width"],
        "10"
    );
    assert_eq!(instance.attribute_dicts["SU_InstanceSet"]["Owner"], "");
}

#[test]
fn attribute_dictionaries_decode_length_and_float_as_distinct_tags_both_f64() {
    let entries = concat(&[
        &entry("depth", &tlv("AF38", &f64s(&[15.5]))),
        &entry("price", &tlv("A938", &f64s(&[120.0]))),
    ]);
    let dicts = attribute_dicts_for(&named_dict("fbd-einfo", &entries));
    let depth = &dicts["fbd-einfo"]["depth"];
    let price = &dicts["fbd-einfo"]["price"];
    // Same f64 payload; AF38 keeps its Length kind so it can be written back.
    assert_eq!(depth.kind(), AttributeKind::Length);
    assert_eq!(depth.number(), 15.5);
    assert_eq!(price.kind(), AttributeKind::Float);
    assert_eq!(price.number(), 120.0);
    assert_eq!(depth.to_string(), "15.5");
    assert_eq!(price.to_string(), "120");
}

// SketchUp 2024 `set_attribute(..., true)` on a face or instance: A438 { AA38 01 }.
#[test]
fn attribute_dictionaries_decode_boolean_value() {
    let entries = concat(&[
        &entry("is_a_face", &tlv("AA38", &[1])),
        &entry("flag", &tlv("AA38", &[0])),
    ]);
    let dicts = attribute_dicts_for(&named_dict("SPIRIT_ELEMENT_DICT", &entries));
    let yes = &dicts["SPIRIT_ELEMENT_DICT"]["is_a_face"];
    let no = &dicts["SPIRIT_ELEMENT_DICT"]["flag"];
    assert_eq!(*yes, ParsedAttribute::Boolean(true));
    assert_eq!(yes.integer(), 1);
    assert_eq!(*no, ParsedAttribute::Boolean(false));
    assert_eq!(no.integer(), 0);
    assert_eq!(yes.to_string(), "1");
}

#[test]
fn attribute_dictionaries_decode_integer_value() {
    let dicts = attribute_dicts_for(&named_dict(
        "fbd-einfo",
        &entry("angle", &tlv("A738", &(-7i32).to_le_bytes())),
    ));
    let angle = &dicts["fbd-einfo"]["angle"];
    assert_eq!(angle.kind(), AttributeKind::Integer);
    assert_eq!(angle.integer(), -7);
    assert_eq!(angle.to_string(), "-7");
}

#[test]
fn attribute_dictionaries_decode_empty_a438_as_null() {
    let dicts = attribute_dicts_for(&named_dict("fbd-einfo", &entry("child_thickness", &[])));
    assert_eq!(dicts["fbd-einfo"]["child_thickness"], ParsedAttribute::Null);
    assert_eq!(dicts["fbd-einfo"]["child_thickness"].to_string(), "");
}

#[test]
fn attribute_dictionaries_decode_point3d_and_vector3d() {
    let point = tlv("B438", &f64s(&[0.0, 0.807085, 14.6551]));
    let vector = tlv("B538", &f64s(&[1.0, 0.0, 0.0]));
    let entries = concat(&[&entry("end_pos", &point), &entry("vector_new", &vector)]);
    let dicts = attribute_dicts_for(&named_dict("fbd-einfo", &entries));
    let end_pos = &dicts["fbd-einfo"]["end_pos"];
    let vector_new = &dicts["fbd-einfo"]["vector_new"];
    assert_eq!(end_pos.kind(), AttributeKind::Vec);
    assert_eq!(*end_pos, ParsedAttribute::Point3d([0.0, 0.807085, 14.6551]));
    assert_eq!(vector_new.kind(), AttributeKind::Vec);
    assert_eq!(*vector_new, ParsedAttribute::Vector3d([1.0, 0.0, 0.0]));
    assert_eq!(end_pos.to_string(), "0,0.807085,14.6551");
    assert_eq!(vector_new.to_string(), "1,0,0");
}

#[test]
fn attribute_dictionaries_decode_empty_array() {
    let dicts = attribute_dicts_for(&named_dict(
        "fbd-einfo",
        &entry("added_bolt_holes", &tlv("AE38", &[])),
    ));
    let holes = &dicts["fbd-einfo"]["added_bolt_holes"];
    assert_eq!(holes.kind(), AttributeKind::Array);
    assert!(holes.array().is_empty());
    assert_eq!(holes.to_string(), "");
}

#[test]
fn attribute_dictionaries_decode_array_of_floats() {
    let elems = concat(&[
        &entry_value(&tlv("A938", &f64s(&[0.728]))),
        &entry_value(&tlv("A938", &f64s(&[11.358]))),
        &entry_value(&tlv("A938", &f64s(&[14.655]))),
    ]);
    let dicts = attribute_dicts_for(&named_dict(
        "fbd-einfo",
        &entry("flangeholes", &tlv("AE38", &elems)),
    ));
    let holes = &dicts["fbd-einfo"]["flangeholes"];
    assert_eq!(holes.kind(), AttributeKind::Array);
    assert_eq!(holes.array().len(), 3);
    assert_eq!(holes.array()[0].number(), 0.728);
    assert_eq!(holes.to_string(), "0.728,11.358,14.655");
}

// fbd-profile-cords style [[x, y], [x, y]] - an array of arrays.
#[test]
fn attribute_dictionaries_decode_nested_array() {
    let inner1 = concat(&[
        &entry_value(&tlv("A938", &f64s(&[25.17]))),
        &entry_value(&tlv("A938", &f64s(&[0.07]))),
    ]);
    let inner2 = concat(&[
        &entry_value(&tlv("A938", &f64s(&[25.17]))),
        &entry_value(&tlv("A938", &f64s(&[15.35]))),
    ]);
    let outer = concat(&[
        &entry_value(&tlv("AE38", &inner1)),
        &entry_value(&tlv("AE38", &inner2)),
    ]);
    let dicts = attribute_dicts_for(&named_dict(
        "fbd-einfo",
        &entry("lip_side1_cords", &tlv("AE38", &outer)),
    ));
    let cords = &dicts["fbd-einfo"]["lip_side1_cords"];
    assert_eq!(cords.kind(), AttributeKind::Array);
    assert_eq!(cords.array().len(), 2);
    assert_eq!(cords.array()[0].kind(), AttributeKind::Array);
    assert_eq!(cords.to_string(), "25.17,0.07,25.17,15.35");
}

// A type tag this decoder doesn't know is left as Null rather than
// misinterpreted.
#[test]
fn attribute_dictionaries_leave_unrecognized_value_tag_as_null() {
    let dicts = attribute_dicts_for(&named_dict(
        "fbd-einfo",
        &entry("mystery", &tlv("EE99", &[0x01, 0x02])),
    ));
    assert_eq!(dicts["fbd-einfo"]["mystery"], ParsedAttribute::Null);
    assert_eq!(dicts["fbd-einfo"]["mystery"].to_string(), "");
}

#[test]
fn extracts_vff_construction_point_and_line() {
    let builder = geometry(&concat(&[
        &tlv(
            "9213",
            &tlv(
                "6C42",
                &concat(&[
                    &tlv("6D42", &f64s(&[1.0, 2.0, 3.0])),
                    &tlv("6E42", &f64s(&[0.0, 0.0, 0.0])),
                    &tlv("6F42", &[0]),
                ]),
            ),
        ),
        &tlv(
            "9113",
            &concat(&[
                &tlv(
                    "6942",
                    &tlv("6A42", &f64s(&[0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 12.0])),
                ),
                &tlv(
                    "6942",
                    &tlv("6A42", &f64s(&[0.0, 0.0, 4.0, 0.0, 0.0, 1.0, -1e30, 1e30])),
                ),
            ]),
        ),
    ]));
    assert_eq!(builder.construction_points.len(), 1);
    assert_eq!(builder.construction_points[0].position, [1.0, 2.0, 3.0]);
    // No drawing-element base: visible, default layer.
    assert!(!builder.construction_points[0].hidden);
    assert!(builder.construction_points[0].layer.is_empty());
    assert_eq!(builder.construction_lines.len(), 2);
    assert!(builder.construction_lines[0].start.is_some());
    assert_eq!(builder.construction_lines[0].end.unwrap()[0], 12.0);
    assert!(builder.construction_lines[1].start.is_none());
    assert!(builder.construction_lines[1].end.is_none());
    assert_eq!(builder.construction_lines[1].point[2], 4.0);
    assert!(!builder.construction_lines[0].hidden);
    assert!(builder.construction_lines[0].layer.is_empty());
}

// Ported from the C++ geometry_test.cpp (openskp#475).
#[test]
fn reads_vff_guide_hidden_flag_and_layer() {
    // 6842 > D007 > D307 display flags (0x06 base, +0x01 hidden), D207 layer id.
    let drawbase = |flags: u8, layer: Option<u8>| {
        let d307 = tlv("D307", &[flags]);
        let d007 = match layer {
            Some(l) => concat(&[&tlv("D207", &[l]), &d307]),
            None => d307,
        };
        tlv("6842", &tlv("D007", &d007))
    };
    let builder = geometry(&concat(&[
        &tlv(
            "9213",
            &concat(&[
                &tlv(
                    "6C42",
                    &concat(&[&drawbase(0x07, None), &tlv("6D42", &f64s(&[1.0, 2.0, 3.0]))]),
                ),
                &tlv(
                    "6C42",
                    &concat(&[
                        &drawbase(0x06, Some(4)),
                        &tlv("6D42", &f64s(&[4.0, 5.0, 6.0])),
                    ]),
                ),
            ]),
        ),
        &tlv(
            "9113",
            &tlv(
                "6942",
                &concat(&[
                    &drawbase(0x07, Some(3)),
                    &tlv("6A42", &f64s(&[0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 12.0])),
                ]),
            ),
        ),
    ]));
    let points = &builder.construction_points;
    assert_eq!(points.len(), 2);
    assert!(points[0].hidden);
    assert!(points[0].layer.is_empty());
    assert!(!points[1].hidden);
    assert_eq!(points[1].layer, "4");
    let lines = &builder.construction_lines;
    assert_eq!(lines.len(), 1);
    assert!(lines[0].hidden);
    assert_eq!(lines[0].layer, "3");
}

// A varint wider than 64 bits is an error, not a silent truncation.
#[test]
fn oversized_entity_id_is_an_error() {
    let bad = tlv(
        "C409",
        &concat(&[&tlv("DE05", &[1; 9]), &tlv("C509", &f64s(&[0.0, 0.0, 0.0]))]),
    );
    let mut builder = GeometryBuilder::default();
    let nodes = parse_tlv_recursive(&bad, 0, bad.len()).unwrap();
    assert!(collect_geometry(&nodes, &mut builder).is_err());
}
