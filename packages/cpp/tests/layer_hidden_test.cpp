#include <gtest/gtest.h>

#include <openskp/openskp.hpp>

#include "internal.hpp"

// Layer::hidden - the on/off visibility bit. Already correctly extracted
// from legacy MFC files (legacy.cpp's CLayer branch, via V::hidden) but
// previously discarded before reaching the public model; now wired through
// RawParsed::layer_hidden alongside the existing layer_colors/
// layer_id_to_name maps. VFF files carry their own real visibility tag
// too (8E3C, see vff_layer_hidden_test.cpp) - both feed the same map.
//
// build_model() is called directly with a synthetic RawParsed (hand-crafting
// a real hidden-layer legacy binary stream isn't practical), matching the
// TypeScript port's buildModelFromParsed-based test.

namespace openskp::test {
namespace {

TEST(LayerHidden, ReportsHiddenLayerAsHidden) {
  RawParsed parsed;
  parsed.layer_order = {"Layer0", "Furniture"};
  parsed.layer_colors["Layer0"] = {136, 136, 136};
  parsed.layer_colors["Furniture"] = {200, 50, 50};
  parsed.layer_hidden["Layer0"] = false;
  parsed.layer_hidden["Furniture"] = true;

  auto model = build_model(std::move(parsed));

  bool found_layer0 = false, found_furniture = false;
  for (const auto& layer : model.layers) {
    if (layer.name == "Layer0") {
      found_layer0 = true;
      EXPECT_FALSE(layer.hidden);
    } else if (layer.name == "Furniture") {
      found_furniture = true;
      EXPECT_TRUE(layer.hidden);
    }
  }
  EXPECT_TRUE(found_layer0);
  EXPECT_TRUE(found_furniture);
}

TEST(LayerHidden, DefaultsToVisibleWhenMissingFromMap) {
  RawParsed parsed;
  parsed.layer_order = {"Layer0"};
  parsed.layer_colors["Layer0"] = {136, 136, 136};
  // layer_hidden deliberately left empty.

  auto model = build_model(std::move(parsed));

  ASSERT_EQ(model.layers.size(), 1);
  EXPECT_FALSE(model.layers[0].hidden);
}

// model.layers must come out in the source file's own order (material.xml
// archive-entry order for VFF, slot-scan order for legacy), not
// alphabetical - layer_colors is a std::map (sorted by name) so
// layer_order (a plain vector, populated in encounter order at every
// insertion site) is the only place that order survives. Deliberately
// picks names whose alphabetical order would differ from layer_order,
// so a regression back to iterating layer_colors directly would fail
// this test immediately.
TEST(LayerHidden, PreservesFileOrderNotAlphabeticalOrder) {
  RawParsed parsed;
  parsed.layer_order = {"Zebra", "Apple", "Middle"};
  parsed.layer_colors["Zebra"] = {1, 1, 1};
  parsed.layer_colors["Apple"] = {2, 2, 2};
  parsed.layer_colors["Middle"] = {3, 3, 3};

  auto model = build_model(std::move(parsed));

  ASSERT_EQ(model.layers.size(), 3);
  EXPECT_EQ(model.layers[0].name, "Zebra");
  EXPECT_EQ(model.layers[1].name, "Apple");
  EXPECT_EQ(model.layers[2].name, "Middle");
}

}  // namespace
}  // namespace openskp::test
