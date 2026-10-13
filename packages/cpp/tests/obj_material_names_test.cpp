#include <gtest/gtest.h>
#include <set>
#include <sstream>
#include <string>
#include <utility>
#include <vector>

#include "openskp/obj_export.hpp"

namespace openskp {
namespace {

std::vector<std::string> material_references(const std::string& text, const std::string& prefix) {
  std::istringstream input(text);
  std::vector<std::string> names;
  std::string line;
  while (std::getline(input, line)) {
    if (line.rfind(prefix, 0) == 0) {
      names.push_back(line.substr(prefix.size()));
    }
  }
  return names;
}

TEST(ObjExport, SanitizedMaterialNamesAreUniqueAndMatchObjReferences) {
  const std::vector<std::pair<std::vector<std::string>, std::vector<std::string>>> scenarios = {
      {{"Wood/Oak", "Wood_Oak", "Wood_Oak_2"}, {"Wood_Oak", "Wood_Oak_2_1", "Wood_Oak_2"}},
      {{"Oak", "Pine"}, {"Oak", "Pine"}},
      {{"A", "A", "A"}, {"A", "A_2", "A_3"}},
      {{"Material_1", ""}, {"Material_1", "Material_1_2"}},
      {{" ", "   "}, {"default_material", "default_material_2"}},
      {{"!!!", "???"}, {"___", "____2"}},
      {{"Oak", "oak"}, {"Oak", "oak"}},
  };

  for (const auto& [raw_names, expected] : scenarios) {
    Scene scene;
    scene.gltf_materials.resize(raw_names.size());
    for (std::size_t i = 0; i < raw_names.size(); ++i) {
      scene.gltf_materials[i].name = raw_names[i];
      GlbPrimitive prim;
      prim.material_index = static_cast<std::int32_t>(i);
      prim.positions = {0, 0, 0, 1, 0, 0, 0, 1, 0};
      prim.indices = {0, 1, 2};
      scene.glb_primitives.push_back(std::move(prim));
    }

    const auto mtl_names = material_references(to_mtl(scene), "newmtl ");
    const auto obj_names = material_references(to_obj(scene), "usemtl ");
    EXPECT_EQ(mtl_names, expected);
    EXPECT_EQ(obj_names, expected);
    EXPECT_EQ(std::set<std::string>(mtl_names.begin(), mtl_names.end()).size(), mtl_names.size());
  }
}

}  // namespace
}  // namespace openskp
