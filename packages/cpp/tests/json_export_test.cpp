#include <functional>
#include <gtest/gtest.h>

#include <openskp/openskp.hpp>

#include "test_helpers.hpp"

namespace openskp {
namespace {

// Real-file regression test for to_json() - openskp's canonical JSON
// export schema, shared with the Python (to_dict), TypeScript (toJSON),
// Dart (toJson), and .NET (JsonExport.ToDict) ports. Cross-checked
// directly against all four on this exact fixture.

TEST(JsonExport, MatchesOtherPortsGroundTruth) {
  auto model = SkpFile::open(test::fixture("capilla_quiroz_v17.skp")).parse();
  auto d = to_json(model);

  ASSERT_TRUE(d.is_object());
  EXPECT_EQ(d.find("format_version")->as_string(), "1.0");
  EXPECT_EQ(d.find("sketchup_version")->as_string(), "{17.0.18899}");
  EXPECT_EQ(d.find("total_definitions")->as_int(), 2);
  EXPECT_EQ(d.find("total_layers")->as_int(), 1);
  EXPECT_EQ(d.find("total_meshes")->as_int(), 0);

  const auto& root = *d.find("root");
  EXPECT_EQ(root.find("vertex_count")->as_int(), 251);
  EXPECT_EQ(root.find("edge_count")->as_int(), 390);
  EXPECT_EQ(root.find("face_count")->as_int(), 146);
  ASSERT_TRUE(root.find("instances")->is_array());
  EXPECT_EQ(root.find("instances")->as_array().size(), 3);
  const auto& first_inst = root.find("instances")->as_array()[0];
  ASSERT_TRUE(first_inst.is_object());
  EXPECT_EQ(first_inst.as_object().size(), 4u);
  EXPECT_NE(first_inst.find("name"), nullptr);
  EXPECT_NE(first_inst.find("ref_idx"), nullptr);
  EXPECT_NE(first_inst.find("guid"), nullptr);
  EXPECT_NE(first_inst.find("matrix"), nullptr);

  const auto& definitions = d.find("definitions")->as_object();
  const JsonValue* puerta = nullptr;
  for (const auto& [key, val] : definitions) {
    if (val.find("name")->as_string() == "puerta") {
      puerta = &val;
      break;
    }
  }
  ASSERT_NE(puerta, nullptr);
  EXPECT_EQ(puerta->find("id")->as_int(), 40);
  EXPECT_EQ(puerta->find("vertex_count")->as_int(), 64);
  EXPECT_EQ(puerta->find("edge_count")->as_int(), 95);
  EXPECT_EQ(puerta->find("face_count")->as_int(), 24);
  EXPECT_EQ(puerta->find("edges")->as_array().size(), 95u);
  EXPECT_EQ(puerta->find("faces")->as_array().size(), 24u);

  const auto& layers = d.find("layers")->as_array();
  const auto& layer_color = *layers[0].find("color");
  EXPECT_EQ(layer_color.find("r")->as_int(), 255);
  EXPECT_EQ(layer_color.find("g")->as_int(), 84);
  EXPECT_EQ(layer_color.find("b")->as_int(), 84);

  EXPECT_TRUE(d.find("mesh_index")->as_object().empty());
  EXPECT_TRUE(d.find("scene_hierarchy")->is_null());
}

TEST(JsonExport, IncludesSceneHierarchyAndMeshIndexWhenSceneIsPassed) {
  auto model = SkpFile::open(test::fixture("capilla_quiroz_v17.skp")).parse();
  auto scene = SkpFile::open(test::fixture("capilla_quiroz_v17.skp")).build_scene();
  auto d = to_json(model, &scene);

  EXPECT_EQ(static_cast<std::size_t>(d.find("total_meshes")->as_int()), scene.mesh_index.size());
  const auto& hierarchy = *d.find("scene_hierarchy");
  EXPECT_EQ(hierarchy.find("name")->as_string(), "ROOT");
  EXPECT_NE(hierarchy.find("definition_name"), nullptr);
  EXPECT_NE(hierarchy.find("position_mm"), nullptr);

  const auto& mesh_index = d.find("mesh_index")->as_object();
  ASSERT_FALSE(mesh_index.empty());
  const auto& first_mesh = mesh_index[0].second;
  EXPECT_NE(first_mesh.find("definition_name"), nullptr);
  EXPECT_NE(first_mesh.find("position_mm"), nullptr);
}

// Every attribute dictionary an instance carries (not just SketchUp's own
// dynamic_attributes, which `properties` already covers) must reach JSON
// output too - it was already correctly resolved by build_scene() but
// previously never serialized in mesh_index/scene_hierarchy at all,
// silently dropping third-party plugin data (e.g. a steel-detailing
// tool's own named dictionary) from every JSON/GLB-metadata consumer.
// Untitled.skp's real "W1" instance carries a real "steelframer-dict"
// (45 entries, including "generator"/"profile") - see Parser.
// ModernUntitled for the same real data verified at the raw-parse level.
TEST(JsonExport, IncludesAttributeDictionariesFromRealPluginData) {
  auto model = SkpFile::open(test::fixture("Untitled.skp")).parse();
  auto scene = SkpFile::open(test::fixture("Untitled.skp")).build_scene();
  auto d = to_json(model, &scene);

  std::function<const JsonValue*(const JsonValue&)> find_w1 =
      [&](const JsonValue& node) -> const JsonValue* {
    auto* name = node.find("name");
    if (name && name->as_string() == "W1") return &node;
    auto* children = node.find("children");
    if (children && children->is_array()) {
      for (auto& child : children->as_array()) {
        if (auto* found = find_w1(child)) return found;
      }
    }
    return nullptr;
  };
  const auto* w1 = find_w1(*d.find("scene_hierarchy"));
  ASSERT_NE(w1, nullptr);

  EXPECT_TRUE(w1->find("properties")->as_object().empty());
  const auto* dicts = w1->find("attribute_dictionaries");
  ASSERT_NE(dicts, nullptr);
  const auto* steelframer = dicts->find("steelframer-dict");
  ASSERT_NE(steelframer, nullptr);
  EXPECT_EQ(steelframer->find("generator")->as_string(), "SteelFramer::Engine::PanelGenerator");
  EXPECT_EQ(steelframer->find("profile")->as_string(), "362S200-43");
}

TEST(JsonExport, ProducesNonEmptySerializedOutput) {
  auto model = SkpFile::open(test::fixture("capilla_quiroz_v17.skp")).parse();
  auto scene = SkpFile::open(test::fixture("capilla_quiroz_v17.skp")).build_scene();
  auto d = to_json(model, &scene);
  auto text = to_json_string(d);
  EXPECT_FALSE(text.empty());
  EXPECT_NE(text.find("\"format_version\""), std::string::npos);
}

}  // namespace
}  // namespace openskp
