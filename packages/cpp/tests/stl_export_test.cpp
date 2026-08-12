#include "openskp/stl_export.hpp"

#include <gtest/gtest.h>

namespace openskp {
namespace {

TEST(StlExport, SerializesSceneToStlAsciiText) {
  Scene scene;
  GlbPrimitive prim;
  prim.geom_name = "Box";
  prim.material_index = 0;
  prim.positions = {0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0};
  prim.normals = {0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 0.0, 0.0, 1.0};
  prim.uvs = {0.0, 0.0, 1.0, 0.0, 0.0, 1.0};
  prim.indices = {0, 1, 2};
  scene.glb_primitives.push_back(prim);

  std::string stl_text = to_stl_ascii(scene, 1.0f);
  EXPECT_NE(stl_text.find("solid OpenSKP_Model"), std::string::npos);
  EXPECT_NE(stl_text.find("facet normal 0.000000 0.000000 1.000000"), std::string::npos);
  EXPECT_NE(stl_text.find("vertex 0.000000 0.000000 0.000000"), std::string::npos);
  EXPECT_NE(stl_text.find("endsolid OpenSKP_Model"), std::string::npos);
}

TEST(StlExport, SerializesSceneToStlBinaryData) {
  Scene scene;
  GlbPrimitive prim;
  prim.geom_name = "Box";
  prim.material_index = 0;
  prim.positions = {0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0};
  prim.normals = {0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 0.0, 0.0, 1.0};
  prim.uvs = {0.0, 0.0, 1.0, 0.0, 0.0, 1.0};
  prim.indices = {0, 1, 2};
  scene.glb_primitives.push_back(prim);

  std::vector<std::uint8_t> data = to_stl_binary(scene, 1.0f);
  EXPECT_EQ(data.size(), 80 + 4 + 50);  // Header + uint32 count + 1 triangle
}

}  // namespace
}  // namespace openskp
