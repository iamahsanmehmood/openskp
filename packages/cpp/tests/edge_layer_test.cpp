#include <cmath>
#include <gtest/gtest.h>
#include <set>
#include <tuple>
#include <utility>
#include <vector>

#include <openskp/openskp.hpp>

#include "internal.hpp"
#include "test_helpers.hpp"

// Edge::layer - edges carry the same drawing-element layer reference faces
// and instances do (VFF D207 under D007, the legacy drawbase layer id), and
// it can differ from the layer of the faces an edge bounds: a sky dome's
// rim edges sit on "Sky Dome" while other boundaries stay on Layer0.
// build_model() resolves the layer id to its name like it does for faces.

namespace openskp::test {
namespace {

TEST(EdgeLayer, ResolvesTheEdgeLayerIdToItsName) {
  RawParsed parsed;
  parsed.layer_order = {"Layer0", "Sky Dome"};
  parsed.layer_colors["Layer0"] = {136, 136, 136};
  parsed.layer_colors["Sky Dome"] = {0, 0, 255};
  parsed.layer_id_to_name[1] = "Layer0";
  parsed.layer_id_to_name[42] = "Sky Dome";
  parsed.root.builder.vertices[1] = {0, 0, 0};
  parsed.root.builder.vertices[2] = {1, 0, 0};
  parsed.root.builder.vertices[3] = {0, 1, 0};
  parsed.root.builder.edges[10] = {EntityId(1), EntityId(2)};
  parsed.root.builder.edges[11] = {EntityId(2), EntityId(3)};
  parsed.root.builder.edges[12] = {EntityId(3), EntityId(1)};
  parsed.root.builder.edge_layers[10] = "42";
  parsed.root.builder.edge_layers[11] = "1";

  auto model = build_model(std::move(parsed));

  const auto& edges = model.root().edges;
  ASSERT_EQ(edges.size(), 3U);
  EXPECT_EQ(edges.at(10).layer, "Sky Dome");
  EXPECT_EQ(edges.at(11).layer, "Layer0");
  // An edge without a layer reference stays on the default layer.
  EXPECT_EQ(edges.at(12).layer, "");
}

// gondola_v20.skp (SketchUp 2020, classic format) draws its side panels on the
// "Gondulas Laterais" layer, edges included.
TEST(EdgeLayer, ReadsEdgeLayersFromAClassicFile) {
  const auto model = SkpFile::open(fixture("gondola_v20.skp")).parse();
  std::size_t on_layer = 0;
  const auto count = [&](const Definition& d) {
    for (const auto& [id, edge] : d.edges)
      if (edge.layer == "Gondulas Laterais") ++on_layer;
  };
  count(model.root());
  for (const auto& [id, d] : model.definitions) count(d);
  EXPECT_GT(on_layer, 0U);
}

TEST(EdgeLayer, UsesMajorityFaceLayerInBuildSceneAndBuildInstancedSceneForChairAndTable) {
  const auto chair_and_table = std::filesystem::path(OPENSKP_FIXTURES)
                                   .parent_path()
                                   .parent_path()
                                   .parent_path()
                                   .parent_path() /
                               "examples" / "web-viewer" / "samples" / "chair_and_table.skp";
  const auto skp = SkpFile::open(chair_and_table);
  const auto baked = skp.build_scene();
  const auto instanced = skp.build_instanced_scene();

  const auto rgb_set = [](const std::vector<GltfMaterial>& mats) {
    std::set<std::tuple<int, int, int>> s;
    for (const auto& m : mats) {
      const auto& bcf = m.pbr_metallic_roughness.base_color_factor;
      s.emplace(static_cast<int>(std::lround(bcf[0] * 255.0)),
                static_cast<int>(std::lround(bcf[1] * 255.0)),
                static_cast<int>(std::lround(bcf[2] * 255.0)));
    }
    return s;
  };

  const auto baked_colors = rgb_set(baked.gltf_materials);
  const auto instanced_colors = rgb_set(instanced.gltf_materials);

  EXPECT_EQ(baked_colors.count({255, 84, 84}), 0U);
  EXPECT_EQ(instanced_colors.count({255, 84, 84}), 0U);
  EXPECT_EQ(baked_colors.count({120, 150, 190}), 1U);
  EXPECT_EQ(baked_colors.count({190, 150, 120}), 1U);
  EXPECT_EQ(instanced_colors, baked_colors);

  std::vector<std::pair<std::string, std::string>> baked_layers;
  std::function<void(const InstanceNode&)> collect_baked = [&](const InstanceNode& n) {
    baked_layers.emplace_back(n.name, n.layer);
    for (const auto& c : n.children) collect_baked(c);
  };
  std::vector<std::pair<std::string, std::string>> instanced_layers;
  std::function<void(const InstancedNode&)> collect_instanced = [&](const InstancedNode& n) {
    instanced_layers.emplace_back(n.name, n.layer);
    for (const auto& c : n.children) collect_instanced(c);
  };
  collect_baked(baked.scene_hierarchy);
  collect_instanced(instanced.scene_hierarchy);
  EXPECT_EQ(instanced_layers, baked_layers);
}

}  // namespace
}  // namespace openskp::test
