#include <cstdint>
#include <gtest/gtest.h>
#include <map>
#include <set>
#include <string>
#include <vector>

#include <openskp/openskp.hpp>

namespace openskp {
namespace {

const std::vector<Point3> kWallRect{{0, 0, 0}, {20, 0, 0}, {20, 10, 0}, {0, 10, 0}};

SkpFile build_fixture() {
  auto builder = create();
  auto& wall = builder->add_component_definition("Wall");
  wall.add_face(kWallRect);
  wall.close();

  InstanceOptions w1;
  w1.name = "W1";
  w1.attributes["name"] = std::string{"CustomOverride"};
  w1.attributes["sku"] = std::string{"B42"};
  builder->add_instance(wall, w1);

  InstanceOptions w2;
  w2.name = "W2";
  w2.translation = {30, 0, 0};
  w2.attribute_dict_name = "TechSteel";
  w2.attributes["label"] = std::string{"TS-9"};
  w2.attributes["count"] = std::int32_t{3};
  builder->add_instance(wall, w2);

  InstanceOptions w3;
  w3.name = "W3";
  w3.translation = {60, 0, 0};
  w3.attribute_dict_name = "dynamic_attributes";
  w3.attributes["name"] = std::string{"IgnoredDynamic"};
  w3.attributes["lenx"] = std::int32_t{20};
  builder->add_instance(wall, w3);

  return SkpFile::from_buffer(builder->to_bytes());
}

TEST(ClassicInstanceAttrs, NamesAndDictionariesMatchInBothSceneTypes) {
  const auto skp = build_fixture();
  const auto baked = skp.build_scene();
  const auto instanced = skp.build_instanced_scene();

  using Dicts = std::map<std::string, std::map<std::string, std::string>>;
  using Props = std::map<std::string, std::string>;

  const Dicts expected_w1{{"attributes", {{"name", "CustomOverride"}, {"sku", "B42"}}}};
  const Dicts expected_w2{{"TechSteel", {{"count", "3"}, {"label", "TS-9"}}}};
  const Props expected_w3_props{{"lenx", "20"}, {"name", "IgnoredDynamic"}};

  const auto& bnodes = baked.scene_hierarchy.children;
  ASSERT_EQ(bnodes.size(), 3u);
  EXPECT_EQ(bnodes[0].name, "CustomOverride");
  EXPECT_EQ(bnodes[1].name, "TS-9");
  EXPECT_EQ(bnodes[2].name, "W3");
  EXPECT_EQ(bnodes[0].attribute_dictionaries, expected_w1);
  EXPECT_TRUE(bnodes[0].properties.empty());
  EXPECT_EQ(bnodes[1].attribute_dictionaries, expected_w2);
  EXPECT_TRUE(bnodes[1].properties.empty());
  EXPECT_TRUE(bnodes[2].attribute_dictionaries.empty());
  EXPECT_EQ(bnodes[2].properties, expected_w3_props);

  const auto& inodes = instanced.scene_hierarchy.children;
  ASSERT_EQ(inodes.size(), 3u);
  EXPECT_EQ(inodes[0].name, "CustomOverride");
  EXPECT_EQ(inodes[1].name, "TS-9");
  EXPECT_EQ(inodes[2].name, "W3");
  EXPECT_FALSE(inodes[0].name_is_generated);
  EXPECT_FALSE(inodes[1].name_is_generated);
  EXPECT_FALSE(inodes[2].name_is_generated);
  EXPECT_EQ(inodes[0].attribute_dictionaries, expected_w1);
  EXPECT_TRUE(inodes[0].properties.empty());
  EXPECT_EQ(inodes[1].attribute_dictionaries, expected_w2);
  EXPECT_TRUE(inodes[1].properties.empty());
  EXPECT_TRUE(inodes[2].attribute_dictionaries.empty());
  EXPECT_EQ(inodes[2].properties, expected_w3_props);

  std::map<std::string, MeshMetadata> by_name;
  for (const auto& prim : baked.surface_primitives()) {
    const auto& meta = baked.mesh_index.at(prim.geom_name);
    by_name.emplace(meta.name, meta);
  }
  ASSERT_EQ(by_name.size(), 3u);
  EXPECT_EQ(by_name.at("CustomOverride").attribute_dictionaries, expected_w1);
  EXPECT_EQ(by_name.at("TS-9").attribute_dictionaries, expected_w2);
  EXPECT_TRUE(by_name.at("W3").attribute_dictionaries.empty());
  EXPECT_EQ(by_name.at("W3").properties, expected_w3_props);
}

}  // namespace
}  // namespace openskp
