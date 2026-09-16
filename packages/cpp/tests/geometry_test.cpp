#include <gtest/gtest.h>

#include "internal.hpp"
#include "test_helpers.hpp"

namespace openskp {
namespace {

using test::concat;
using test::tlv;

GeometryBuilder geometry(ByteBuffer bytes) {
  GeometryBuilder builder;
  collect_geometry(parse_tlv_recursive(bytes, 0, bytes.size()), builder);
  return builder;
}

ByteBuffer uv_payload(const std::vector<double>* front, const std::vector<double>* back) {
  const auto side = [](const char* tag, const std::vector<double>& matrix) {
    return tlv(tag, tlv("1327", concat({tlv("1427", {1}), tlv("1527", test::f64s(matrix))})));
  };

  ByteBuffer sides;
  if (front) sides = concat({std::move(sides), side("1127", *front)});
  if (back) sides = concat({std::move(sides), side("1227", *back)});
  return concat({tlv("DE05", {0x2a}), tlv("DD05", tlv("B136", tlv("B236", tlv("1027", sides))))});
}

TEST(Geometry, ReadsInstanceMaterialAndDefault) {
  auto painted =
      geometry(tlv("6419", concat({tlv("6719", {5}), tlv("D007", tlv("D107", {0x33, 0x73}))})));
  ASSERT_EQ(painted.instances.size(), 1);
  EXPECT_EQ(painted.instances[0].ref_idx, 5);
  EXPECT_EQ(painted.instances[0].material_id, 0x7333);

  auto unpainted = geometry(tlv("6419", tlv("6719", {5})));
  ASSERT_EQ(unpainted.instances.size(), 1);
  EXPECT_FALSE(unpainted.instances[0].material_id.has_value());
}

TEST(Geometry, ExtractsFrontAndBackUvTransforms) {
  const std::vector<double> front{0, 1, 0, -1, 0, 0, 96, -96, 1};
  auto back = front;
  for (double& value : back) value *= 2;

  const auto face_data =
      concat({tlv("DE05", {0x2a}), tlv("D007", tlv("DC05", uv_payload(&front, &back)))});
  auto builder = geometry(tlv("AC0D", face_data));
  const auto& face = builder.faces.at(0x2a);
  ASSERT_TRUE(face.uv_transform.has_value());
  ASSERT_TRUE(face.uv_transform_back.has_value());
  for (std::size_t i = 0; i < front.size(); ++i) {
    EXPECT_DOUBLE_EQ((*face.uv_transform)[i], front[i]);
    EXPECT_DOUBLE_EQ((*face.uv_transform_back)[i], back[i]);
  }
}

TEST(Geometry, LeavesUvTransformsEmptyWithoutMappingBlock) {
  auto builder = geometry(
      tlv("AC0D", concat({tlv("DE05", {0x2a}), tlv("D007", tlv("DC05", tlv("DE05", {0x2a})))})));
  const auto& face = builder.faces.at(0x2a);
  EXPECT_FALSE(face.uv_transform.has_value());
  EXPECT_FALSE(face.uv_transform_back.has_value());
}

TEST(Geometry, ExtractsWrappedImagePlacement) {
  auto builder = geometry(tlv("9013", tlv("401F", tlv("6419", tlv("6719", {7})))));
  ASSERT_EQ(builder.instances.size(), 1);
  EXPECT_EQ(builder.instances[0].ref_idx, 7);
}

TEST(Geometry, MarksImageAndAlwaysFacesCameraDefinitions) {
  const auto image = tlv(
      "7C15", concat({tlv("DE05", {1}), tlv("7E15", test::bytes("imagen#1")), tlv("8315", {2})}));
  const auto billboard =
      tlv("7C15", concat({tlv("DE05", {2}), tlv("7E15", test::bytes("Susan")),
                          tlv("581B", concat({tlv("5D1B", {1}), tlv("5E1B", {1})}))}));
  const auto ordinary =
      tlv("7C15", concat({tlv("DE05", {3}), tlv("7E15", test::bytes("Chair")),
                          tlv("581B", concat({tlv("5D1B", {0}), tlv("5E1B", {0})}))}));

  const auto bytes = concat({image, billboard, ordinary});
  std::map<EntityId, RawDefinition> definitions;
  collect_definitions(parse_tlv_recursive(bytes, 0, bytes.size()), definitions);

  EXPECT_TRUE(definitions.at(1).is_image);
  EXPECT_FALSE(definitions.at(2).is_image);
  EXPECT_TRUE(definitions.at(2).always_faces_camera);
  EXPECT_TRUE(definitions.at(2).shadows_face_sun);
  EXPECT_FALSE(definitions.at(3).always_faces_camera);
  EXPECT_FALSE(definitions.at(3).shadows_face_sun);
}

TEST(Geometry, ExtractsBackMaterialAndEdgeFlags) {
  auto face_builder =
      geometry(tlv("AC0D", concat({tlv("DE05", {0x2a}), tlv("AF0D", {0x85, 0x8b, 0x06})})));
  const auto& face = face_builder.faces.at(0x2a);
  EXPECT_FALSE(face.material_id.has_value());
  EXPECT_EQ(face.back_material_id, 0x068b85);

  const auto edge = [](std::uint8_t id, std::uint8_t flags) {
    return tlv("B80B", concat({tlv("DE05", {id}), tlv("D007", tlv("D307", {flags}))}));
  };
  auto edge_builder = geometry(concat({edge(1, 0x06), edge(2, 0x07), edge(3, 0x1e)}));
  EXPECT_EQ(edge_builder.edge_flags.at(1), 0x06);
  EXPECT_EQ(edge_builder.edge_flags.at(2), 0x07);
  EXPECT_EQ(edge_builder.edge_flags.at(3), 0x1e);
}

TEST(Geometry, SectionPlaneTextDimensionDefaultsEmpty) {
  GeometryBuilder builder;
  EXPECT_TRUE(builder.section_planes.empty());
  EXPECT_TRUE(builder.texts.empty());
  EXPECT_TRUE(builder.dimensions.empty());
}

// Multi-value-type decoding for attribute dictionaries (openskp#285's VFF
// 9-value-type item). Byte shapes mirror Python's TestVffAttributeDictionaries
// exactly (also mirrored in .NET/TypeScript/Dart's own ports): a 6419
// instance's D007/DC05 payload holds a B436 (dictionary name) node followed
// by a sibling B536 (entries) node holding B636 (key)/A438 (type-tagged
// value) pairs.

ByteBuffer i32_bytes(std::int32_t value) {
  std::uint32_t bits = static_cast<std::uint32_t>(value);
  return {static_cast<std::uint8_t>(bits), static_cast<std::uint8_t>(bits >> 8),
          static_cast<std::uint8_t>(bits >> 16), static_cast<std::uint8_t>(bits >> 24)};
}

ByteBuffer entry_value(ByteBuffer inner_tlv) { return tlv("A438", std::move(inner_tlv)); }

ByteBuffer entry(const char* key, ByteBuffer inner_value_tlv) {
  return concat({tlv("B636", test::bytes(key)), entry_value(std::move(inner_value_tlv))});
}

ByteBuffer named_dict(const char* name, ByteBuffer entries_payload) {
  return concat({tlv("B436", test::bytes(name)), tlv("B536", std::move(entries_payload))});
}

std::map<std::string, std::map<std::string, std::string>> attribute_dicts_for(
    ByteBuffer dc05_payload) {
  auto builder = geometry(tlv("6419", tlv("D007", tlv("DC05", std::move(dc05_payload)))));
  return builder.instances.at(0).attribute_dicts;
}

TEST(Geometry, AttributeDictionariesGroupEntriesByDictionaryName) {
  auto dicts =
      attribute_dicts_for(named_dict("fbd-einfo", entry("code", tlv("AD38", test::bytes("Ks")))));
  EXPECT_EQ(dicts.at("fbd-einfo").at("code"), "Ks");
}

TEST(Geometry, AttributeDictionariesKeepTwoDictionariesDistinct) {
  // dynamic_attributes is SketchUp's own Dynamic Components dictionary -
  // collect_geometry redirects it to RawInstance::properties (the
  // backward-compatible view), not attribute_dicts, same as every other
  // port. attribute_dicts only ever holds every OTHER named dictionary.
  auto builder = geometry(tlv(
      "6419",
      tlv("D007",
          tlv("DC05",
              concat({
                  named_dict("dynamic_attributes", entry("width", tlv("AD38", test::bytes("10")))),
                  named_dict("FrameBuilder", entry("name", tlv("AD38", test::bytes("W-2")))),
              })))));
  const auto& instance = builder.instances.at(0);
  EXPECT_EQ(instance.properties.at("width"), "10");
  EXPECT_EQ(instance.attribute_dicts.at("FrameBuilder").at("name"), "W-2");
  EXPECT_EQ(instance.attribute_dicts.count("dynamic_attributes"), 0u);
}

TEST(Geometry, AttributeDictionariesDecodeLengthAndFloatAsDistinctTagsBothF64) {
  auto entries = concat({
      entry("depth", tlv("AF38", test::f64s({15.5}))),
      entry("price", tlv("A938", test::f64s({120.0}))),
  });
  auto dicts = attribute_dicts_for(named_dict("fbd-einfo", entries));
  EXPECT_EQ(dicts.at("fbd-einfo").at("depth"), "15.5");
  EXPECT_EQ(dicts.at("fbd-einfo").at("price"), "120");
}

TEST(Geometry, AttributeDictionariesDecodeIntegerValue) {
  auto dicts =
      attribute_dicts_for(named_dict("fbd-einfo", entry("angle", tlv("A738", i32_bytes(-7)))));
  EXPECT_EQ(dicts.at("fbd-einfo").at("angle"), "-7");
}

TEST(Geometry, AttributeDictionariesDecodeEmptyA438AsEmptyString) {
  auto dicts = attribute_dicts_for(named_dict("fbd-einfo", entry("child_thickness", {})));
  EXPECT_EQ(dicts.at("fbd-einfo").at("child_thickness"), "");
}

TEST(Geometry, AttributeDictionariesDecodePoint3dAndVector3d) {
  auto point_bytes = tlv("B438", test::f64s({0.0, 0.807085, 14.6551}));
  auto vector_bytes = tlv("B538", test::f64s({1.0, 0.0, 0.0}));
  auto entries = concat({entry("end_pos", point_bytes), entry("vector_new", vector_bytes)});
  auto dicts = attribute_dicts_for(named_dict("fbd-einfo", entries));
  EXPECT_EQ(dicts.at("fbd-einfo").at("end_pos"), "0,0.807085,14.6551");
  EXPECT_EQ(dicts.at("fbd-einfo").at("vector_new"), "1,0,0");
}

TEST(Geometry, AttributeDictionariesDecodeEmptyArrayAsEmptyString) {
  auto dicts =
      attribute_dicts_for(named_dict("fbd-einfo", entry("added_bolt_holes", tlv("AE38", {}))));
  EXPECT_EQ(dicts.at("fbd-einfo").at("added_bolt_holes"), "");
}

TEST(Geometry, AttributeDictionariesDecodeArrayOfFloats) {
  auto elems = concat({
      entry_value(tlv("A938", test::f64s({0.728}))),
      entry_value(tlv("A938", test::f64s({11.358}))),
      entry_value(tlv("A938", test::f64s({14.655}))),
  });
  auto dicts =
      attribute_dicts_for(named_dict("fbd-einfo", entry("flangeholes", tlv("AE38", elems))));
  EXPECT_EQ(dicts.at("fbd-einfo").at("flangeholes"), "0.728,11.358,14.655");
}

TEST(Geometry, AttributeDictionariesDecodeNestedArray) {
  // openskp#253's motivating case mirrored on the read side: fbd-profile-
  // cords style [[x, y], [x, y]] - an array whose elements are themselves
  // arrays.
  auto inner1 = concat({entry_value(tlv("A938", test::f64s({25.17}))),
                        entry_value(tlv("A938", test::f64s({0.07})))});
  auto inner2 = concat({entry_value(tlv("A938", test::f64s({25.17}))),
                        entry_value(tlv("A938", test::f64s({15.35})))});
  auto outer = concat({entry_value(tlv("AE38", inner1)), entry_value(tlv("AE38", inner2))});
  auto dicts =
      attribute_dicts_for(named_dict("fbd-einfo", entry("lip_side1_cords", tlv("AE38", outer))));
  EXPECT_EQ(dicts.at("fbd-einfo").at("lip_side1_cords"), "25.17,0.07,25.17,15.35");
}

TEST(Geometry, AttributeDictionariesLeaveUnrecognizedValueTagAsEmptyString) {
  // A type tag this decoder doesn't (yet) know is left as "" rather than
  // misinterpreted - safer than a wrong guess.
  auto dicts = attribute_dicts_for(
      named_dict("fbd-einfo", entry("mystery", tlv("EE99", ByteBuffer{0x01, 0x02}))));
  EXPECT_EQ(dicts.at("fbd-einfo").at("mystery"), "");
}

}  // namespace
}  // namespace openskp
