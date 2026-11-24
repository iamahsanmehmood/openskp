#include <gtest/gtest.h>

#include <openskp/openskp.hpp>

#include "test_helpers.hpp"

namespace openskp {
namespace {

TEST(ScenePages, SceneAndInstancedScenePagesEqualParsedModelPages) {
  struct Case {
    const char* name;
    std::vector<std::string> expected_names;
  };

  const Case cases[] = {
      {"gondola_v20.skp", {"Cena1"}},
      {"sdk_saved_2020.skp", {"With camera", "No camera", "Plain"}},
      {"scene_without_camera_2021.skp", {"With camera", "No camera", "Plain"}},
      {"nesting_2017.skp", {}},
  };
  for (const auto& tc : cases) {
    SCOPED_TRACE(tc.name);
    const auto skp = SkpFile::open(test::fixture(tc.name));
    const auto model = skp.parse();
    const auto scene = skp.build_scene();
    const auto instanced = skp.build_instanced_scene();

    std::vector<std::string> names;
    for (const auto& p : model.pages) names.push_back(p.name);
    EXPECT_EQ(names, tc.expected_names);
    EXPECT_EQ(scene.pages, model.pages);
    EXPECT_EQ(instanced.pages, model.pages);
  }
}

TEST(ScenePages, KeepsNativeInchesAndHiddenLayersAndClearsOnFragmentsRoundTrip) {
  const auto skp = SkpFile::open(test::fixture("scene_without_camera_2021.skp"));
  const auto scene = skp.build_scene();
  const auto instanced = skp.build_instanced_scene();

  for (const auto* pages : {&scene.pages, &instanced.pages}) {
    ASSERT_EQ(pages->size(), 3u);
    EXPECT_EQ((*pages)[0].eye, (std::optional<Vec3>{{100.0, -200.0, 80.0}}));
    EXPECT_EQ((*pages)[0].target, (std::optional<Vec3>{{5.0, 5.0, 0.0}}));
    EXPECT_FALSE((*pages)[1].eye.has_value());
    EXPECT_EQ((*pages)[1].hidden_layers, (std::vector<std::string>{"Furniture"}));
  }

  const auto reloaded = from_fragments(to_fragments(instanced));
  EXPECT_TRUE(reloaded.pages.empty());
}

}  // namespace
}  // namespace openskp
