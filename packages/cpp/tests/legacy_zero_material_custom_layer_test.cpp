#include <gtest/gtest.h>

#include <openskp/openskp.hpp>

#include "test_helpers.hpp"

namespace openskp {
namespace {

// Regression for SketchUp 2018 (file version 18) saves with no CMaterial
// records and a custom tag listed ahead of Layer0.
//
// Those files take probe_layer_anchor_bases (the two-material bootstrap
// needs mc >= 2). The declared layer_count is 1; Layer0 follows after a
// 16-byte colour-layer extension. Missing that extension left the probe
// on padding: `base probe: anchor resolved to`.
//
// Fixture is a SketchUp 2018 layout sample (two construction lines, no
// faces). Identifiable strings were replaced with same-length ASCII so the
// MFC record sizes are unchanged.

TEST(LegacyZeroMaterialCustomLayer, ParsesV18CustomTagAheadOfLayer0) {
  auto file = SkpFile::open(test::fixture("zero_material_custom_layer_v18.skp"));
  auto model = file.parse();

  EXPECT_EQ(model.version, "{18.0.16975}");
  EXPECT_TRUE(model.materials.empty());
  ASSERT_GE(model.layers.size(), 2u);
  bool found_layer0 = false;
  bool found_guide = false;
  for (const auto& layer : model.layers) {
    if (layer.name == "Layer0") found_layer0 = true;
    if (layer.name == "Guide") found_guide = true;
  }
  EXPECT_TRUE(found_layer0);
  EXPECT_TRUE(found_guide);
  EXPECT_TRUE(model.root().faces.empty());
  EXPECT_EQ(model.root().construction_lines.size(), 2u);
}

}  // namespace
}  // namespace openskp
