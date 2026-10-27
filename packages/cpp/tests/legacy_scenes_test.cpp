#include <gtest/gtest.h>

#include <openskp/openskp.hpp>

#include "test_helpers.hpp"

namespace openskp {
namespace {

// Scenes in a classic (pre-2021) file (openskp#445).
//
// gondola_v20.skp: SketchUp 2020's own save, one scene ("Cena1", capture flags 0x1FF) with a
// parallel-projection camera. sdk_saved_2019/2020.skp: built and saved through SketchUp's C API,
// which writes the scene class at a newer schema than the desktop application. Three scenes: "With
// camera" (eye 100, -200, 80 looking at 5, 5, 0), "No camera" (no saved camera, hides the layer
// "Furniture") and "Plain". SketchUp's own library reads the same scenes back.
// sdk_saved_sections_2019/2020.skp: the same model with two scenes, the first capturing everything
// a scene can (flags 0x1FF), so the second is only found if the first was read to its last byte.
// No private content.

TEST(LegacyScenes, GondolaReadsItsSceneWithTheCamera) {
  const auto model = SkpFile::open(test::fixture("gondola_v20.skp")).parse();
  ASSERT_EQ(model.pages.size(), 1u);
  const auto& p = model.pages[0];
  EXPECT_EQ(p.name, "Cena1");
  ASSERT_TRUE(p.eye.has_value());
  EXPECT_NEAR((*p.eye)[0], 2093.78994893, 1e-6);
  EXPECT_NEAR((*p.eye)[1], 412.425280402, 1e-6);
  EXPECT_NEAR((*p.eye)[2], 135.404197356, 1e-6);
  ASSERT_TRUE(p.target.has_value());
  EXPECT_NEAR((*p.target)[0], 1802.70152746, 1e-6);
  EXPECT_TRUE(p.parallel);
  EXPECT_NEAR(p.ortho_height, 180.650232984, 1e-6);
  EXPECT_TRUE(p.hidden_layers.empty());
}

TEST(LegacyScenes, SdkSavedScenesAreReadWithTheirCamerasAndHiddenLayers) {
  for (const char* name : {"sdk_saved_2019.skp", "sdk_saved_2020.skp"}) {
    SCOPED_TRACE(name);
    const auto model = SkpFile::open(test::fixture(name)).parse();
    ASSERT_EQ(model.pages.size(), 3u);
    EXPECT_EQ(model.pages[0].name, "With camera");
    EXPECT_EQ(model.pages[1].name, "No camera");
    EXPECT_EQ(model.pages[2].name, "Plain");
    ASSERT_TRUE(model.pages[0].eye.has_value());
    EXPECT_EQ(*model.pages[0].eye, (Vec3{100.0, -200.0, 80.0}));
    EXPECT_EQ(*model.pages[0].target, (Vec3{5.0, 5.0, 0.0}));
    EXPECT_EQ(model.pages[0].fov, 30.0);
    EXPECT_FALSE(model.pages[0].parallel);
    EXPECT_TRUE(model.pages[0].hidden_layers.empty());
    EXPECT_FALSE(model.pages[1].eye.has_value());
    EXPECT_FALSE(model.pages[1].target.has_value());
    ASSERT_EQ(model.pages[1].hidden_layers.size(), 1u);
    EXPECT_EQ(model.pages[1].hidden_layers[0], "Furniture");
    EXPECT_FALSE(model.pages[2].eye.has_value());
    EXPECT_TRUE(model.pages[2].hidden_layers.empty());
  }
}

TEST(LegacyScenes, ASceneCapturingEveryPropertyIsReadToItsLastByte) {
  for (const char* name : {"sdk_saved_sections_2019.skp", "sdk_saved_sections_2020.skp"}) {
    SCOPED_TRACE(name);
    const auto model = SkpFile::open(test::fixture(name)).parse();
    ASSERT_EQ(model.pages.size(), 2u);
    EXPECT_EQ(model.pages[0].name, "Everything");
    EXPECT_EQ(model.pages[1].name, "Plain");
    ASSERT_TRUE(model.pages[0].eye.has_value());
    EXPECT_EQ(*model.pages[0].eye, (Vec3{100.0, -200.0, 80.0}));
    ASSERT_EQ(model.pages[0].hidden_layers.size(), 1u);
    EXPECT_EQ(model.pages[0].hidden_layers[0], "Furniture");
    EXPECT_FALSE(model.pages[1].eye.has_value());
  }
}

}  // namespace
}  // namespace openskp
