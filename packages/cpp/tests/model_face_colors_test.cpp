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

}  // namespace
}  // namespace openskp
