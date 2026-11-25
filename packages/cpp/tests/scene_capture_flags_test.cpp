#include <algorithm>
#include <functional>
#include <gtest/gtest.h>
#include <map>
#include <set>
#include <string>
#include <utility>
#include <vector>

#include <openskp/openskp.hpp>

#include "test_helpers.hpp"

namespace openskp {
namespace {

// Scene capture flags (#660) and classic 2015-2020 section lengths (#661).

const std::vector<std::string> kSceneOrder = {
    "Camera",        "RenderingOptions", "ShadowInfo",    "Axes",
    "Hidden",        "HiddenGeometry",   "HiddenObjects", "HiddenLayers",
    "SectionPlanes", "ShadowsCamera",    "AxesCamera",
};

const std::vector<std::pair<std::string, std::function<std::optional<bool>(const Page&)>>>&
all_use_props() {
  static const std::vector<std::pair<std::string, std::function<std::optional<bool>(const Page&)>>>
      kProps = {
          {"use_camera", [](const Page& p) { return p.use_camera(); }},
          {"use_rendering_options", [](const Page& p) { return p.use_rendering_options(); }},
          {"use_shadow_info", [](const Page& p) { return p.use_shadow_info(); }},
          {"use_axes", [](const Page& p) { return p.use_axes(); }},
          {"use_hidden_layers", [](const Page& p) { return p.use_hidden_layers(); }},
          {"use_section_planes", [](const Page& p) { return p.use_section_planes(); }},
          {"use_hidden_geometry", [](const Page& p) { return p.use_hidden_geometry(); }},
          {"use_hidden_objects", [](const Page& p) { return p.use_hidden_objects(); }},
      };
  return kProps;
}

std::map<std::string, Page> pages_by_name(const char* fname) {
  const auto model = SkpFile::open(test::fixture(fname)).parse();
  std::map<std::string, Page> out;
  for (const auto& p : model.pages) out.emplace(p.name, p);
  return out;
}

TEST(SceneCaptureFlagsUnit, ExposesBitConstants) {
  EXPECT_EQ(PAGE_USE_CAMERA, 0x001u);
  EXPECT_EQ(PAGE_USE_RENDERING_OPTIONS, 0x002u);
  EXPECT_EQ(PAGE_USE_SHADOW_INFO, 0x004u);
  EXPECT_EQ(PAGE_USE_AXES, 0x008u);
  EXPECT_EQ(PAGE_USE_HIDDEN, 0x010u);
  EXPECT_EQ(PAGE_USE_HIDDEN_LAYERS, 0x020u);
  EXPECT_EQ(PAGE_USE_SECTION_PLANES, 0x040u);
  EXPECT_EQ(PAGE_USE_HIDDEN_GEOMETRY, 0x080u);
  EXPECT_EQ(PAGE_USE_HIDDEN_OBJECTS, 0x100u);
}

TEST(SceneCaptureFlagsUnit, EmptyHiddenListIsToldApartAndSaved) {
  for (const char* fname : {"scene_hidden_entities_2017.skp", "scene_hidden_entities_2019.skp",
                            "scene_hidden_entities_2020.skp", "scene_hidden_entities_2024.skp"}) {
    SCOPED_TRACE(fname);
    const auto pages = pages_by_name(fname);
    EXPECT_TRUE(pages.at("All").hidden_entity_ids.empty());
    EXPECT_TRUE(pages.at("Not saved").hidden_entity_ids.empty());
    EXPECT_EQ(pages.at("All").capture_flags, 0x191u);
    EXPECT_EQ(pages.at("All").use_hidden_objects(), true);
    EXPECT_EQ(pages.at("All").use_hidden_geometry(), true);
    EXPECT_EQ(pages.at("Not saved").capture_flags, 0x001u);
    EXPECT_EQ(pages.at("Not saved").use_hidden_objects(), false);
    EXPECT_EQ(pages.at("Not saved").use_hidden_geometry(), false);
    EXPECT_EQ(pages.at("Not saved").use_camera(), true);

    for (const auto& [_, page] : pages) {
      if (!page.hidden_entity_ids.empty()) {
        EXPECT_TRUE(page.use_hidden_objects() == true || page.use_hidden_geometry() == true);
      }
    }
  }
}

TEST(SceneCaptureFlagsUnit, ScenesThatSaveNoCameraAndBuiltScenes) {
  const auto pages = pages_by_name("scene_without_camera_2021.skp");
  EXPECT_EQ(pages.at("With camera").capture_flags, 0x001u);
  EXPECT_EQ(pages.at("With camera").use_camera(), true);
  EXPECT_EQ(pages.at("With camera").use_hidden_layers(), false);
  EXPECT_EQ(pages.at("No camera").capture_flags, 0x020u);
  EXPECT_EQ(pages.at("No camera").use_camera(), false);
  EXPECT_EQ(pages.at("No camera").use_hidden_layers(), true);
  EXPECT_FALSE(pages.at("No camera").eye.has_value());
  EXPECT_EQ(pages.at("Plain").capture_flags, 0x000u);
  EXPECT_EQ(pages.at("Plain").use_camera(), false);
  EXPECT_EQ(pages.at("Plain").use_hidden_layers(), false);

  const auto skp = SkpFile::open(test::fixture("scene_without_camera_2021.skp"));
  const auto scene = skp.build_scene();
  const auto instanced = skp.build_instanced_scene();
  ASSERT_EQ(scene.pages.size(), 3u);
  ASSERT_EQ(instanced.pages.size(), 3u);
  EXPECT_EQ(scene.pages[0].capture_flags, 0x001u);
  EXPECT_EQ(scene.pages[1].capture_flags, 0x020u);
  EXPECT_EQ(scene.pages[2].capture_flags, 0x000u);
  EXPECT_EQ(instanced.pages, scene.pages);
}

TEST(SceneCaptureFlagsUnit, AllOrNothingAndApplicationSavedScene) {
  for (const char* fname : {"sdk_saved_sections_2019.skp", "sdk_saved_sections_2020.skp"}) {
    SCOPED_TRACE(fname);
    const auto pages = pages_by_name(fname);
    const auto& everything = pages.at("Everything");
    const auto& plain = pages.at("Plain");
    EXPECT_EQ(everything.capture_flags, 0x1FFu);
    EXPECT_EQ(plain.capture_flags, 0x000u);
    for (const auto& [prop_name, getter] : all_use_props()) {
      EXPECT_EQ(getter(everything), true) << prop_name;
      EXPECT_EQ(getter(plain), false) << prop_name;
    }
  }

  const auto gondola = pages_by_name("gondola_v20.skp");
  ASSERT_EQ(gondola.size(), 1u);
  const auto& page = gondola.begin()->second;
  EXPECT_EQ(page.capture_flags, 0x1FFu);
  EXPECT_EQ(page.use_hidden_geometry(), true);
}

TEST(SceneCaptureFlagsUnit, OlderCombinedBitAndUnsetFlags) {
  Page old_page;
  old_page.capture_flags = 0x011u;
  EXPECT_EQ(old_page.use_hidden_geometry(), true);
  EXPECT_EQ(old_page.use_hidden_objects(), true);

  Page split_page;
  split_page.capture_flags = 0x111u;
  EXPECT_EQ(split_page.use_hidden_objects(), true);
  EXPECT_EQ(split_page.use_hidden_geometry(), false);

  Page unset_page;
  EXPECT_FALSE(unset_page.capture_flags.has_value());
  for (const auto& [prop_name, getter] : all_use_props()) {
    EXPECT_FALSE(getter(unset_page).has_value()) << prop_name;
  }
}

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

  const std::map<std::string, std::pair<std::uint32_t, std::set<std::string>>> expected = {
      {"Camera", {0x001u, {"use_camera"}}},
      {"RenderingOptions", {0x002u, {"use_rendering_options"}}},
      {"ShadowInfo", {0x004u, {"use_shadow_info"}}},
      {"Axes", {0x008u, {"use_axes"}}},
      {"Hidden", {0x190u, {"use_hidden_geometry", "use_hidden_objects"}}},
      {"HiddenGeometry", {0x090u, {"use_hidden_geometry"}}},
      {"HiddenObjects", {0x110u, {"use_hidden_objects"}}},
      {"HiddenLayers", {0x020u, {"use_hidden_layers"}}},
      {"SectionPlanes", {0x040u, {"use_section_planes"}}},
      {"ShadowsCamera", {0x005u, {"use_shadow_info", "use_camera"}}},
      {"AxesCamera", {0x009u, {"use_axes", "use_camera"}}},
  };
  for (const auto& [scene_name, spec] : expected) {
    const auto& p = by_name.at(scene_name);
    EXPECT_EQ(p.capture_flags, spec.first) << scene_name;
    for (const auto& [prop_name, getter] : all_use_props()) {
      EXPECT_EQ(getter(p), spec.second.count(prop_name) > 0) << scene_name << "." << prop_name;
    }
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
