#include <gtest/gtest.h>

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

}  // namespace
}  // namespace openskp::test
