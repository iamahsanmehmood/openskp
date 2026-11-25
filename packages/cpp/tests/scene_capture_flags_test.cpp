#include <algorithm>
#include <gtest/gtest.h>
#include <map>
#include <set>
#include <string>
#include <vector>

#include <openskp/openskp.hpp>

#include "test_helpers.hpp"

namespace openskp {
namespace {

// Classic 2015-2020 scene capture flags, 62/108 shadow/axes lengths, and pre-2017 schema 15 (#661).

const std::vector<std::string> kSceneOrder = {
    "Camera",        "RenderingOptions", "ShadowInfo",    "Axes",
    "Hidden",        "HiddenGeometry",   "HiddenObjects", "HiddenLayers",
    "SectionPlanes", "ShadowsCamera",    "AxesCamera",
};

class SceneCaptureFlags : public ::testing::TestWithParam<int> {};

TEST_P(SceneCaptureFlags, ReadsAllScenesWithCamerasAndHiddenLayers) {
  const std::string fname = "scene_capture_flags_" + std::to_string(GetParam()) + ".skp";
  const auto model = SkpFile::open(test::fixture(fname.c_str())).parse();
  ASSERT_EQ(model.pages.size(), kSceneOrder.size());
  std::map<std::string, Page> by_name;
  for (size_t i = 0; i < kSceneOrder.size(); ++i) {
    EXPECT_EQ(model.pages[i].name, kSceneOrder[i]);
    by_name.emplace(model.pages[i].name, model.pages[i]);
  }

  const auto& cam = by_name.at("Camera");
  ASSERT_TRUE(cam.eye.has_value());
  EXPECT_EQ(*cam.eye, (Vec3{120.0, -240.0, 90.0}));
  ASSERT_TRUE(cam.target.has_value());
  EXPECT_EQ(*cam.target, (Vec3{5.0, 5.0, 0.0}));
  ASSERT_TRUE(cam.up.has_value());
  EXPECT_NEAR((*cam.up)[0], -0.13407807210149128, 1e-9);
  EXPECT_NEAR((*cam.up)[1], 0.28564458839013357, 1e-9);
  EXPECT_NEAR((*cam.up)[2], 0.9489100271917137, 1e-9);
  EXPECT_FALSE(cam.parallel);
  EXPECT_NEAR(cam.fov, 42.0, 1e-9);

  const auto& shadows_cam = by_name.at("ShadowsCamera");
  ASSERT_TRUE(shadows_cam.eye.has_value());
  EXPECT_EQ(*shadows_cam.eye, (Vec3{0.0, -300.0, 150.0}));
  ASSERT_TRUE(shadows_cam.target.has_value());
  EXPECT_EQ(*shadows_cam.target, (Vec3{0.0, 0.0, 0.0}));
  ASSERT_TRUE(shadows_cam.up.has_value());
  EXPECT_NEAR((*shadows_cam.up)[0], 0.0, 1e-9);
  EXPECT_NEAR((*shadows_cam.up)[1], 0.44721359549995804, 1e-9);
  EXPECT_NEAR((*shadows_cam.up)[2], 0.8944271909999161, 1e-9);
  EXPECT_TRUE(shadows_cam.parallel);
  EXPECT_NEAR(shadows_cam.ortho_height, 80.0, 1e-9);

  const auto& axes_cam = by_name.at("AxesCamera");
  ASSERT_TRUE(axes_cam.eye.has_value());
  EXPECT_EQ(*axes_cam.eye, (Vec3{-180.0, -180.0, 110.0}));
  ASSERT_TRUE(axes_cam.target.has_value());
  EXPECT_EQ(*axes_cam.target, (Vec3{10.0, 10.0, 0.0}));
  ASSERT_TRUE(axes_cam.up.has_value());
  EXPECT_NEAR((*axes_cam.up)[0], 0.2678945632789157, 1e-9);
  EXPECT_NEAR((*axes_cam.up)[1], 0.2678945632789157, 1e-9);
  EXPECT_NEAR((*axes_cam.up)[2], 0.9254539458726179, 1e-9);
  EXPECT_FALSE(axes_cam.parallel);
  EXPECT_NEAR(axes_cam.fov, 55.0, 1e-9);

  for (const auto& name : kSceneOrder) {
    const auto& p = by_name.at(name);
    if (name != "Camera" && name != "ShadowsCamera" && name != "AxesCamera") {
      EXPECT_FALSE(p.eye.has_value()) << name;
      EXPECT_FALSE(p.target.has_value()) << name;
    }
    if (name == "HiddenLayers") {
      EXPECT_EQ(p.hidden_layers, std::vector<std::string>{"HiddenTag"});
    } else {
      EXPECT_TRUE(p.hidden_layers.empty()) << name;
    }
  }
}

TEST_P(SceneCaptureFlags, ResolvesHiddenEntitiesByVersion) {
  const int year = GetParam();
  const std::string fname = "scene_capture_flags_" + std::to_string(year) + ".skp";
  const auto model = SkpFile::open(test::fixture(fname.c_str())).parse();
  ASSERT_EQ(model.pages.size(), kSceneOrder.size());
  std::map<std::string, Page> by_name;
  for (const auto& p : model.pages) by_name.emplace(p.name, p);

  if (year < 2017) {
    for (const auto& p : model.pages) EXPECT_TRUE(p.hidden_entity_ids.empty()) << p.name;
  } else {
    auto it = std::find_if(model.root().instances.begin(), model.root().instances.end(),
                           [](const auto& i) { return i.name == "Crate"; });
    ASSERT_NE(it, model.root().instances.end());
    ASSERT_TRUE(it->id.has_value());
    EXPECT_EQ(*it->id, 30u);
    const bool found_edge = std::any_of(model.root().edges.begin(), model.root().edges.end(),
                                        [](const auto& e) { return e.second.persistent_id == 21; });
    EXPECT_TRUE(found_edge);

    const auto& hidden_ids = by_name.at("Hidden").hidden_entity_ids;
    EXPECT_EQ((std::set<EntityId>(hidden_ids.begin(), hidden_ids.end())),
              (std::set<EntityId>{21, 30}));
    EXPECT_EQ(by_name.at("HiddenGeometry").hidden_entity_ids, (std::vector<EntityId>{21}));
    EXPECT_EQ(by_name.at("HiddenObjects").hidden_entity_ids, (std::vector<EntityId>{30}));
    for (const auto& name : kSceneOrder) {
      if (name != "Hidden" && name != "HiddenGeometry" && name != "HiddenObjects") {
        EXPECT_TRUE(by_name.at(name).hidden_entity_ids.empty()) << name;
      }
    }
  }
}

INSTANTIATE_TEST_SUITE_P(Years, SceneCaptureFlags,
                         ::testing::Values(2015, 2016, 2017, 2018, 2019, 2020));

}  // namespace
}  // namespace openskp
