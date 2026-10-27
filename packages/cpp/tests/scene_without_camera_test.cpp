#include <gtest/gtest.h>

#include <openskp/openskp.hpp>

#include "test_helpers.hpp"

namespace openskp {
namespace {

// A scene that doesn't save its camera (openskp#447).
//
// SketchUp lets a scene leave "Camera Location" unticked: such a scene saves no
// camera and still carries its name and the layers it hides. The 2021+ reader
// used to drop it.
//
// Fixture: scene_without_camera_2021.skp - one layer ("Furniture"), one face
// and three scenes built through SketchUp's own library: "With camera" (eye
// 100, -200, 80 looking at 5, 5, 0), "No camera" (SUSceneSetUseCamera off,
// hides Furniture) and "Plain" (no camera, hides nothing). SketchUp reports the
// same three scenes. No private content.

TEST(SceneWithoutCamera, EverySceneIsRead) {
  const auto model = SkpFile::open(test::fixture("scene_without_camera_2021.skp")).parse();
  ASSERT_EQ(model.pages.size(), 3u);
  EXPECT_EQ(model.pages[0].name, "With camera");
  EXPECT_EQ(model.pages[1].name, "No camera");
  EXPECT_EQ(model.pages[2].name, "Plain");
}

TEST(SceneWithoutCamera, ASavedCameraIsStillRead) {
  const auto model = SkpFile::open(test::fixture("scene_without_camera_2021.skp")).parse();
  ASSERT_EQ(model.pages.size(), 3u);
  ASSERT_TRUE(model.pages[0].eye.has_value());
  EXPECT_EQ(*model.pages[0].eye, (Vec3{100.0, -200.0, 80.0}));
  ASSERT_TRUE(model.pages[0].target.has_value());
  EXPECT_EQ(*model.pages[0].target, (Vec3{5.0, 5.0, 0.0}));
}

TEST(SceneWithoutCamera, ASceneWithoutACameraKeepsItsHiddenLayers) {
  const auto model = SkpFile::open(test::fixture("scene_without_camera_2021.skp")).parse();
  ASSERT_EQ(model.pages.size(), 3u);
  EXPECT_FALSE(model.pages[1].eye.has_value());
  EXPECT_FALSE(model.pages[1].target.has_value());
  EXPECT_FALSE(model.pages[1].up.has_value());
  ASSERT_EQ(model.pages[1].hidden_layers.size(), 1u);
  EXPECT_EQ(model.pages[1].hidden_layers[0], "Furniture");
  EXPECT_FALSE(model.pages[2].eye.has_value());
  EXPECT_TRUE(model.pages[2].hidden_layers.empty());
}

}  // namespace
}  // namespace openskp
