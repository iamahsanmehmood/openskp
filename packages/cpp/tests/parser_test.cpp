#include <algorithm>
#include <gtest/gtest.h>
#include <limits>
#include <set>

#include <openskp/openskp.hpp>

#include "test_helpers.hpp"

namespace openskp {
namespace {

TEST(Parser, ModernUntitled) {
  auto model = SkpFile::open(test::fixture("Untitled.skp")).parse();
  EXPECT_EQ(model.version, "{25.0.575}");
  EXPECT_EQ(model.layers.size(), 14);
  EXPECT_EQ(model.materials.size(), 15);
  EXPECT_EQ(model.definitions.size(), 46);

  const std::set<std::string> expected_layers{
      "Layer0",       "BottomPlate",  "TopPlate",           "Stud",
      "Nog",          "KingStud",     "HeaderJackStud",     "HeaderPlate1",
      "HeaderPlate2", "SillPlate1",   "VerticalHeaderStud", "generic_frame",
      "dimension",    "Hat Sections",
  };
  for (const auto& layer : model.layers) EXPECT_TRUE(expected_layers.count(layer.name));

  const auto& definition = model.definitions.at(66);
  EXPECT_EQ(definition.name, "Group200#2");
  EXPECT_EQ(definition.guid.size(), 32);
  EXPECT_EQ(definition.vertices.size(), 136);
  EXPECT_EQ(definition.edges.size(), 158);
  EXPECT_EQ(definition.faces.size(), 26);
  EXPECT_FALSE(definition.is_image);
  EXPECT_FALSE(definition.always_faces_camera);

  const auto& edge = definition.edges.begin()->second;
  EXPECT_FALSE(edge.soft);
  EXPECT_FALSE(edge.smooth);
  EXPECT_FALSE(edge.hidden);

  const auto& face = definition.faces.begin()->second;
  ASSERT_FALSE(face.loops.empty());
  ASSERT_FALSE(face.loops[0].empty());
  EXPECT_TRUE(face.normal.has_value());
  EXPECT_FALSE(face.back_material_id.has_value());
  EXPECT_FALSE(face.uv_transform.has_value());
  EXPECT_FALSE(face.uv_transform_back.has_value());

  auto* joined_material = model.material_by_id(26180);
  ASSERT_NE(joined_material, nullptr);
  EXPECT_EQ(joined_material->name, "*");
  EXPECT_EQ(joined_material->id, 26180);
  const auto material_in_list =
      std::find_if(model.materials.begin(), model.materials.end(),
                   [](const Material& value) { return value.name == "*"; });
  ASSERT_NE(material_in_list, model.materials.end());
  EXPECT_EQ(joined_material, &*material_in_list);

  const auto layer_material =
      std::find_if(model.materials.begin(), model.materials.end(),
                   [](const Material& value) { return value.name == "Layer_Layer0"; });
  ASSERT_NE(layer_material, model.materials.end());
  EXPECT_DOUBLE_EQ(layer_material->transparency, 1.0);
  EXPECT_FALSE(layer_material->id.has_value());
  EXPECT_FALSE(layer_material->texture.has_value());
  EXPECT_FALSE(layer_material->colorized);
  EXPECT_EQ(layer_material->colorize_type, 0);

  ASSERT_EQ(model.styles.size(), 2);
  EXPECT_EQ(model.styles[0].name, "[Construction Documentation Style]");
  EXPECT_EQ(model.styles[0].front_color, (Color3{255, 255, 255}));
  EXPECT_EQ(model.styles[0].back_color, (Color3{208, 209, 189}));
}

TEST(Parser, ModernRootOnly) {
  auto file = SkpFile::open(test::fixture("SU_File.skp"));
  auto model = file.parse();
  EXPECT_EQ(model.version, "{25.0.575}");
  EXPECT_TRUE(model.definitions.empty());
  EXPECT_EQ(model.root().name, "ROOT_MODEL");
  ASSERT_EQ(model.layers.size(), 1);
  EXPECT_EQ(model.layers[0].name, "Layer0");
  ASSERT_EQ(model.materials.size(), 1);
  EXPECT_EQ(model.materials[0].name, "Layer_Layer0");

  auto scene = file.build_scene();
  EXPECT_EQ(scene.scene_hierarchy.name, "ROOT");
  EXPECT_EQ(scene.scene_hierarchy.definition_name, "ROOT_MODEL");
  ASSERT_EQ(scene.mesh_index.size(), 1);
  EXPECT_EQ(scene.mesh_index.begin()->second.definition_name, "ROOT_MODEL");
}

TEST(Parser, LegacyMatchesReference) {
  auto file = SkpFile::open(test::fixture("capilla_quiroz_v17.skp"));
  auto model = file.parse();
  EXPECT_EQ(model.version, "{17.0.18899}");
  ASSERT_EQ(model.definitions.size(), 2);

  const auto& puerta = model.definitions.at(40);
  EXPECT_EQ(puerta.name, "puerta");
  EXPECT_EQ(puerta.faces.size(), 24);
  EXPECT_EQ(puerta.edges.size(), 95);
  EXPECT_EQ(puerta.vertices.size(), 64);
  EXPECT_NEAR(puerta.vertices.at(45).x, 60.671292283583, 1e-9);
  EXPECT_NEAR(puerta.vertices.at(45).y, 8.526512829121202e-14, 1e-18);
  EXPECT_NEAR(puerta.vertices.at(45).z, 109.03580700984524, 1e-9);

  const auto& grada = model.definitions.at(395);
  EXPECT_EQ(grada.name, "grada");
  EXPECT_EQ(grada.faces.size(), 11);
  EXPECT_EQ(grada.edges.size(), 30);
  EXPECT_EQ(grada.vertices.size(), 20);

  const std::set<std::string> expected_materials{
      "*1",
      "[0037_SandyBrown]",
      "[0048_PaleGoldenrod]",
      "[0050_LemonChiffon]",
      "[0062_YellowGreen]",
      "[0064_Chartreuse]",
      "[0069_LimeGreen]",
      "[0070_SpringGreen]",
      "[0097_DeepSkyBlue]",
      "[0102_RoyalBlue]",
      "[Color G03]",
      "[Polished Concrete New]",
      "[Polished Concrete Old]",
      "[Roofing Tile Spanish]",
      "[Translucent Glass Blue]",
      "[Translucent Glass Safety]",
  };
  ASSERT_EQ(model.materials.size(), expected_materials.size());
  for (const auto& material : model.materials) {
    EXPECT_TRUE(expected_materials.count(material.name));
  }

  Vec3 minimum{std::numeric_limits<double>::infinity(), std::numeric_limits<double>::infinity(),
               std::numeric_limits<double>::infinity()};
  Vec3 maximum{-std::numeric_limits<double>::infinity(), -std::numeric_limits<double>::infinity(),
               -std::numeric_limits<double>::infinity()};
  for (const auto& [id, definition] : model.definitions) {
    for (const auto& [vertex_id, vertex] : definition.vertices) {
      minimum[0] = std::min(minimum[0], vertex.x);
      minimum[1] = std::min(minimum[1], vertex.y);
      minimum[2] = std::min(minimum[2], vertex.z);
      maximum[0] = std::max(maximum[0], vertex.x);
      maximum[1] = std::max(maximum[1], vertex.y);
      maximum[2] = std::max(maximum[2], vertex.z);
    }
  }
  EXPECT_NEAR(minimum[0], 0, 0.01);
  EXPECT_NEAR(minimum[1], 0, 0.01);
  EXPECT_NEAR(minimum[2], 0, 0.01);
  EXPECT_NEAR(maximum[0], 77.402, 0.01);
  EXPECT_NEAR(maximum[1], 51.969, 0.01);
  EXPECT_NEAR(maximum[2], 133.071, 0.01);
  EXPECT_EQ(model.root().instances.size(), 3);

  auto scene = file.build_scene();
  EXPECT_EQ(scene.glb_primitives.size(), 21);
  EXPECT_EQ(scene.mesh_index.size(), 21);
  EXPECT_EQ(scene.gltf_materials.size(), 13);
  EXPECT_EQ(scene.scene_hierarchy.children.size(), 3);

  std::multiset<std::string> placed_definitions;
  for (const auto& child : scene.scene_hierarchy.children) {
    placed_definitions.insert(child.definition_name);
  }
  EXPECT_EQ(placed_definitions, (std::multiset<std::string>{"grada", "grada", "puerta"}));

  for (const auto& primitive : scene.glb_primitives) {
    EXPECT_EQ(primitive.positions.size(), primitive.normals.size());
    EXPECT_EQ(primitive.indices.size() % 3, 0);
    for (auto index : primitive.indices) EXPECT_LT(index, primitive.positions.size() / 3);
  }
}

TEST(Parser, ModernSceneMatchesReference) {
  auto scene = SkpFile::open(test::fixture("Untitled.skp")).build_scene();
  EXPECT_EQ(scene.scene_hierarchy.name, "ROOT");
  EXPECT_EQ(scene.scene_hierarchy.definition_name, "ROOT_MODEL");
  EXPECT_FALSE(scene.scene_hierarchy.children.empty());
  EXPECT_EQ(scene.mesh_index.size(), 43);
  EXPECT_EQ(scene.glb_primitives.size(), 43);
  for (const auto& [name, metadata] : scene.mesh_index) {
    EXPECT_FALSE(name.empty());
    EXPECT_FALSE(metadata.layer.empty());
  }
}

TEST(Parser, InvalidInputs) {
  try {
    SkpFile::from_buffer(ByteBuffer(200, 'A')).parse();
    FAIL() << "expected a structured parse error";
  } catch (const SkpParseError& error) {
    ASSERT_TRUE(error.stage().has_value());
    EXPECT_EQ(*error.stage(), ParseStage::header);
  }

  ByteBuffer missing_zip(32);
  missing_zip[0] = 0xff;
  missing_zip[1] = 0xfe;
  missing_zip[2] = 0xff;
  missing_zip[3] = 0x0e;
  try {
    parse_skp(missing_zip);
    FAIL() << "expected a missing ZIP error";
  } catch (const SkpParseError& error) {
    ASSERT_TRUE(error.stage().has_value());
    EXPECT_EQ(*error.stage(), ParseStage::zip_extract);
  }

  ByteBuffer truncated_zip = missing_zip;
  truncated_zip[16] = 'P';
  truncated_zip[17] = 'K';
  truncated_zip[18] = 3;
  truncated_zip[19] = 4;
  EXPECT_THROW(parse_skp(truncated_zip), SkpParseError);
  EXPECT_THROW(SkpFile::open("not-skp.txt"), std::filesystem::filesystem_error);
}

}  // namespace
}  // namespace openskp
