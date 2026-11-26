#include <gtest/gtest.h>
#include <set>
#include <utility>

#include <openskp/openskp.hpp>

#include "test_helpers.hpp"

namespace openskp {
namespace {

constexpr Color3 kWrittenFront{11, 122, 233};
constexpr Color3 kWrittenBack{201, 102, 3};
constexpr Color3 kAppFront{255, 255, 255};
constexpr Color3 kAppBack{164, 178, 187};
constexpr Color3 kSdkFront{225, 225, 200};
constexpr Color3 kSdkBack{128, 128, 200};

TEST(ModelFaceColors, MatchesSketchUpAcrossVersions) {
  struct Case {
    const char* name;
    Color3 front;
    Color3 back;
  };

  const Case cases[] = {
      {"face_colors_v4.skp", kWrittenFront, kWrittenBack},
      {"face_colors_v5.skp", kWrittenFront, kWrittenBack},
      {"face_colors_v6.skp", kWrittenFront, kWrittenBack},
      {"face_colors_2016.skp", kWrittenFront, kWrittenBack},
      {"face_colors_2017.skp", kWrittenFront, kWrittenBack},
      {"face_colors_2018.skp", kWrittenFront, kWrittenBack},
      {"face_colors_2019.skp", kWrittenFront, kWrittenBack},
      {"face_colors_2020.skp", kWrittenFront, kWrittenBack},
      {"face_colors_2021.skp", kWrittenFront, kWrittenBack},
      {"capilla_quiroz_v17.skp", kAppFront, kAppBack},
      {"gondola_v20.skp", kAppFront, kAppBack},
      {"zero_material_custom_layer_v18.skp", kAppFront, kAppBack},
      {"SU_File.skp", kAppFront, kAppBack},
      {"nesting_v6.skp", kSdkFront, kSdkBack},
      {"nesting_2013.skp", kSdkFront, kSdkBack},
      {"nesting_2021.skp", kSdkFront, kSdkBack},
      {"legacy_v4_synthetic.skp", kSdkFront, kSdkBack},
      {"legacy_v5_synthetic.skp", kSdkFront, kSdkBack},
      {"legacy_v2019_synthetic.skp", kSdkFront, kSdkBack},
  };
  for (const auto& tc : cases) {
    SCOPED_TRACE(tc.name);
    const auto model = SkpFile::open(test::fixture(tc.name)).parse();
    EXPECT_EQ(model.face_front_color, std::optional<Color3>{tc.front});
    EXPECT_EQ(model.face_back_color, std::optional<Color3>{tc.back});
  }
}

TEST(ModelFaceColors, SketchUp3GivesNullopt) {
  const auto model = SkpFile::open(test::fixture("legacy_v3_synthetic.skp")).parse();
  EXPECT_FALSE(model.face_front_color.has_value());
  EXPECT_FALSE(model.face_back_color.has_value());
}

TEST(ModelFaceColors, CurrentRenderingOptionsNotSavedStyle) {
  const auto model = SkpFile::open(test::fixture("face_colors_2021.skp")).parse();
  std::set<std::pair<std::optional<Color3>, std::optional<Color3>>> style_pairs;
  for (const auto& s : model.styles) {
    style_pairs.emplace(s.front_color, s.back_color);
  }
  EXPECT_GT(style_pairs.count({kSdkFront, kSdkBack}), 0u);
  EXPECT_GT(style_pairs.count({kWrittenFront, kWrittenBack}), 0u);
  EXPECT_EQ(model.face_front_color, std::optional<Color3>{kWrittenFront});
  EXPECT_EQ(model.face_back_color, std::optional<Color3>{kWrittenBack});
}

TEST(ModelColorByTag, MatchesSketchUpAcrossVersions) {
  struct CbtCase {
    const char* name;
    bool expected;
  };

  const CbtCase cases[] = {
      {"color_by_tag_v4.skp", true},         {"color_by_tag_v5.skp", true},
      {"color_by_tag_v6.skp", true},         {"color_by_tag_2017.skp", true},
      {"color_by_tag_2019.skp", true},       {"color_by_tag_2020.skp", true},
      {"color_by_tag_2020_off.skp", false},  {"color_by_tag_2021.skp", true},
      {"color_by_tag_2021_style.skp", true}, {"color_by_tag_2021_off.skp", false},
      {"legacy_v3_synthetic.skp", false},    {"face_colors_v4.skp", false},
      {"face_colors_v5.skp", false},         {"face_colors_v6.skp", false},
      {"face_colors_2016.skp", false},       {"face_colors_2017.skp", false},
      {"face_colors_2018.skp", false},       {"face_colors_2019.skp", false},
      {"face_colors_2020.skp", false},       {"face_colors_2021.skp", false},
  };
  for (const auto& tc : cases) {
    SCOPED_TRACE(tc.name);
    const auto model = SkpFile::open(test::fixture(tc.name)).parse();
    EXPECT_EQ(model.color_by_tag, tc.expected);
  }
}

TEST(ModelColorByTag, RoundTripsClassicWriter) {
  const std::vector<Point3> quad{
      {0.0, 0.0, 0.0}, {10.0, 0.0, 0.0}, {10.0, 10.0, 0.0}, {0.0, 10.0, 0.0}};

  struct WriterCase {
    bool set_option;
    double value;
    bool expected;
  };

  const WriterCase cases[] = {
      {false, 0.0, false},
      {true, 0.0, false},
      {true, 1.0, true},
  };
  for (const auto& tc : cases) {
    auto builder = create();
    if (tc.set_option) {
      DocumentOptions opts;
      opts.style_items[7011] = tc.value;
      builder->set_document_options(opts);
    }
    builder->add_face(quad);
    const auto model = SkpFile::from_buffer(builder->to_bytes()).parse();
    EXPECT_EQ(model.color_by_tag, tc.expected);
  }
}

TEST(ModelColorByTag, DoesNotChangeSceneOutput) {
  const std::pair<const char*, const char*> pairs[] = {
      {"color_by_tag_2020.skp", "color_by_tag_2020_off.skp"},
      {"color_by_tag_2021.skp", "color_by_tag_2021_off.skp"},
  };
  for (const auto& [on_name, off_name] : pairs) {
    SCOPED_TRACE(on_name);
    auto skp_on = SkpFile::open(test::fixture(on_name));
    auto skp_off = SkpFile::open(test::fixture(off_name));
    EXPECT_TRUE(skp_on.parse().color_by_tag);
    EXPECT_FALSE(skp_off.parse().color_by_tag);

    const auto scene_on = skp_on.build_scene();
    const auto scene_off = skp_off.build_scene();
    ASSERT_EQ(scene_on.gltf_materials.size(), scene_off.gltf_materials.size());
    for (std::size_t i = 0; i < scene_on.gltf_materials.size(); ++i) {
      EXPECT_EQ(scene_on.gltf_materials[i].pbr_metallic_roughness.base_color_factor,
                scene_off.gltf_materials[i].pbr_metallic_roughness.base_color_factor);
    }

    const auto inst_on = skp_on.build_instanced_scene();
    const auto inst_off = skp_off.build_instanced_scene();
    ASSERT_EQ(inst_on.gltf_materials.size(), inst_off.gltf_materials.size());
    for (std::size_t i = 0; i < inst_on.gltf_materials.size(); ++i) {
      EXPECT_EQ(inst_on.gltf_materials[i].pbr_metallic_roughness.base_color_factor,
                inst_off.gltf_materials[i].pbr_metallic_roughness.base_color_factor);
    }
  }
}

}  // namespace
}  // namespace openskp
