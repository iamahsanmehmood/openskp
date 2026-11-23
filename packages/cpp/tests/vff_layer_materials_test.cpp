#include <gtest/gtest.h>
#include <map>
#include <string>
#include <vector>

#include <openskp/openskp.hpp>

#include "test_helpers.hpp"

// Tag colour records (materials/Layer_*) in 2021+ files are excluded from
// SkpModel::materials while keeping tag colours on SkpModel::layers (#551).

namespace openskp {
namespace {

TEST(VffLayerMaterials, Arcs2021HasZeroMaterialsAndKeepsLayer0Colour) {
  const auto model = SkpFile::open(test::fixture("arcs_2021.skp")).parse();
  EXPECT_TRUE(model.materials.empty());
  ASSERT_EQ(model.layers.size(), 1u);
  EXPECT_EQ(model.layers[0].name, "Layer0");
  EXPECT_EQ(model.layers[0].color, (Color3{255, 84, 84}));
}

TEST(VffLayerMaterials, TextureTga2021ListsOnlyRealMaterial) {
  const auto model = SkpFile::open(test::fixture("texture_tga_2021.skp")).parse();
  ASSERT_EQ(model.materials.size(), 1u);
  EXPECT_EQ(model.materials[0].name, "Tex_TGA");
  ASSERT_EQ(model.layers.size(), 1u);
  EXPECT_EQ(model.layers[0].name, "Layer0");
  EXPECT_EQ(model.layers[0].color, (Color3{255, 84, 84}));
}

TEST(VffLayerMaterials, RealMaterialNamedLayerPrefixIsPreservedAlongsideTagColour) {
  const auto model =
      SkpFile::open(test::fixture("sdk_saved_layer_named_material_2021.skp")).parse();
  ASSERT_EQ(model.materials.size(), 1u);
  EXPECT_EQ(model.materials[0].name, "Layer_Foo");
  EXPECT_EQ(model.materials[0].color, (Color4{200, 10, 20, 255}));

  std::map<std::string, Color3> layers;
  for (const auto& layer : model.layers) layers[layer.name] = layer.color;
  EXPECT_EQ(layers.at("Foo"), (Color3{30, 140, 250}));
  EXPECT_EQ(layers.at("Layer0"), (Color3{255, 84, 84}));
}

}  // namespace
}  // namespace openskp
