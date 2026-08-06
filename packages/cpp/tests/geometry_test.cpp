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
  const auto billboard = tlv("7C15", concat({tlv("DE05", {2}), tlv("7E15", test::bytes("Susan")),
                                             tlv("581B", tlv("5D1B", {1}))}));
  const auto ordinary = tlv("7C15", concat({tlv("DE05", {3}), tlv("7E15", test::bytes("Chair")),
                                            tlv("581B", tlv("5D1B", {0}))}));

  const auto bytes = concat({image, billboard, ordinary});
  std::map<EntityId, RawDefinition> definitions;
  collect_definitions(parse_tlv_recursive(bytes, 0, bytes.size()), definitions);

  EXPECT_TRUE(definitions.at(1).is_image);
  EXPECT_FALSE(definitions.at(2).is_image);
  EXPECT_TRUE(definitions.at(2).always_faces_camera);
  EXPECT_FALSE(definitions.at(3).always_faces_camera);
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

}  // namespace
}  // namespace openskp
