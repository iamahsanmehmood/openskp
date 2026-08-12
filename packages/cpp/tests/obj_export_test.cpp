#include "openskp/obj_export.hpp"

#include <gtest/gtest.h>

namespace openskp {
namespace {

TEST(ObjExport, SerializesSceneToObjText) {
  Scene scene;
  GlbPrimitive prim;
  prim.geom_name = "Cube";
  prim.material_index = 0;
  prim.positions = {0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0};
  prim.normals = {0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 0.0, 0.0, 1.0};
  prim.uvs = {0.0, 0.0, 1.0, 0.0, 0.0, 1.0};
  prim.indices = {0, 1, 2};
  scene.glb_primitives.push_back(prim);

  std::string obj_text = to_obj(scene);
  EXPECT_NE(obj_text.find("# OpenSKP OBJ Export"), std::string::npos);
  EXPECT_NE(obj_text.find("o Cube"), std::string::npos);
  EXPECT_NE(obj_text.find("v 0.000000 0.000000 0.000000"), std::string::npos);
  EXPECT_NE(obj_text.find("f 1 2 3"), std::string::npos);
}

}  // namespace
}  // namespace openskp
