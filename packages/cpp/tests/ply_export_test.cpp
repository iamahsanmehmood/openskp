#include "openskp/ply_export.hpp"

#include <gtest/gtest.h>

namespace openskp {
namespace {

TEST(PlyExport, SerializesSceneToPlyAsciiText) {
  Scene scene;
  GlbPrimitive prim;
  prim.geom_name = "Box";
  prim.material_index = 0;
  prim.positions = {0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0};
  prim.normals = {0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 0.0, 0.0, 1.0};
  prim.uvs = {0.0, 0.0, 1.0, 0.0, 0.0, 1.0};
  prim.indices = {0, 1, 2};
  scene.glb_primitives.push_back(prim);

  std::string ply_text = to_ply_ascii(scene);
  EXPECT_NE(ply_text.find("format ascii 1.0"), std::string::npos);
  EXPECT_NE(ply_text.find("element vertex 3"), std::string::npos);
  EXPECT_NE(ply_text.find("element face 1"), std::string::npos);
  EXPECT_NE(ply_text.find("3 0 1 2"), std::string::npos);
}

TEST(PlyExport, SerializesSceneToPlyBinaryData) {
  Scene scene;
  GlbPrimitive prim;
  prim.geom_name = "Box";
  prim.material_index = 0;
  prim.positions = {0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0};
  prim.normals = {0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 0.0, 0.0, 1.0};
  prim.uvs = {0.0, 0.0, 1.0, 0.0, 0.0, 1.0};
  prim.indices = {0, 1, 2};
  scene.glb_primitives.push_back(prim);

  std::vector<std::uint8_t> data = to_ply_binary(scene);
  std::string text(data.begin(), data.end());
  EXPECT_NE(text.find("format binary_little_endian 1.0"), std::string::npos);
  EXPECT_NE(text.find("element vertex 3"), std::string::npos);
  EXPECT_NE(text.find("element face 1"), std::string::npos);
}

}  // namespace
}  // namespace openskp
