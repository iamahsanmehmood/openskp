#include <gtest/gtest.h>
#include <string>
#include <tuple>
#include <utility>
#include <vector>

#include <openskp/openskp.hpp>

#include "test_helpers.hpp"

// Tags with a line style in SketchUp 2019 and 2020 files (issue #514).
//
// Since SketchUp 2019 a tag can carry a line style ("Dash", "Dot", ...). A tag
// that has one points at a CCustomLineStyle object right after its own fields
// (both plain-colour and textured CLayer records): nothing for a tag without a
// line style, the style written in full the first time a tag uses it (a
// new-class record, then the object), a class-ref for a second style, and a
// back-ref for a style that an earlier tag already wrote. The style is an
// object in the walk like any other and takes a store-map slot.

namespace openskp {
namespace {

struct ExpectedModel {
  std::string name;
  std::vector<std::string> tags;
  std::vector<std::string> line_styles;
  std::vector<std::pair<std::string, Color4>> materials;
};

const std::vector<ExpectedModel>& models() {
  static const std::vector<ExpectedModel> kModels = {
      {"sdk_saved_tag_line_styles_2020.skp",
       {"Tag 0 ", "Tag 1 Dash", "Tag 2 ", "Tag 3 Dash", "Tag 4 Dot", "Tag 5 Dash"},
       {"", "", "Dash", "", "Dash", "Dot", "Dash"},
       {}},
      {"sdk_saved_tag_line_styles_2019.skp",
       {"Tag 0 ", "Tag 1 Dash", "Tag 2 ", "Tag 3 Dash", "Tag 4 Dot", "Tag 5 Dash"},
       {"", "", "Dash", "", "Dash", "Dot", "Dash"},
       {}},
      {"sdk_saved_tag_line_styles_materials_2020.skp",
       {"Tag 0 Dash", "Tag 1 ", "Tag 2 Dot", "Tag 3 Dash", "Tag 4 Dot"},
       {"", "Dash", "", "Dot", "Dash", "Dot"},
       {{"Paint 0", {10, 20, 30, 255}},
        {"Paint 1", {50, 20, 30, 255}},
        {"Paint 2", {90, 20, 30, 255}}}},
      {"sdk_saved_textured_tag_line_styles_2019.skp",
       {"Tag 0 Tex Dash", "Tag 1 Tex ", "Tag 2 Tex Dot", "Tag 3 Tex Dash", "Tag 4 Plain Dot"},
       {"", "Dash", "", "Dot", "Dash", "Dot"},
       {}},
      {"sdk_saved_textured_tag_line_styles_2020.skp",
       {"Tag 0 Tex Dash", "Tag 1 Tex ", "Tag 2 Tex Dot", "Tag 3 Tex Dash", "Tag 4 Plain Dot"},
       {"", "Dash", "", "Dot", "Dash", "Dot"},
       {}},
  };
  return kModels;
}

TEST(LegacyTagLineStyles, ModelWhoseTagsHaveLineStylesOpens) {
  for (const auto& expected : models()) {
    SCOPED_TRACE(expected.name);
    const auto model = SkpFile::open(test::fixture(expected.name.c_str())).parse();
    std::vector<std::string> expected_layers = {"Layer0"};
    expected_layers.insert(expected_layers.end(), expected.tags.begin(), expected.tags.end());
    std::vector<std::string> actual_layers;
    std::vector<std::string> actual_line_styles;
    for (const auto& layer : model.layers) {
      actual_layers.push_back(layer.name);
      actual_line_styles.push_back(layer.line_style);
    }
    EXPECT_EQ(actual_layers, expected_layers);
    EXPECT_EQ(actual_line_styles, expected.line_styles);
    EXPECT_EQ(model.root().faces.size(), expected.tags.size());

    std::vector<std::pair<std::string, Color4>> actual_materials;
    for (const auto& mat : model.materials) {
      actual_materials.emplace_back(mat.name, mat.color);
    }
    EXPECT_EQ(actual_materials, expected.materials);
  }
}

TEST(LegacyTagLineStyles, EachFaceStaysOnItsOwnTag) {
  // Reading the line styles takes store-map slots between the tags, so a tag's
  // slot (the id its faces carry) is only right when the walk counted them.
  for (const auto& expected : models()) {
    SCOPED_TRACE(expected.name);
    const auto model = SkpFile::open(test::fixture(expected.name.c_str())).parse();
    std::vector<std::string> face_tags;
    for (const auto& [id, face] : model.root().faces) face_tags.push_back(face.layer);
    EXPECT_EQ(face_tags, expected.tags);
  }
}

TEST(LegacyTagLineStyles, Vff2025TagLineStylesMatchSketchUpSdk) {
  const auto model = SkpFile::open(test::fixture("sdk_saved_tag_line_styles_2025.skp")).parse();
  std::vector<std::tuple<std::string, bool, std::string>> actual;
  for (const auto& layer : model.layers) {
    actual.emplace_back(layer.name, layer.hidden, layer.line_style);
  }
  const std::vector<std::tuple<std::string, bool, std::string>> expected = {
      {"Layer0", false, ""},
      {"Tag 0 Plain", false, ""},
      {"Tag 1 Solid Basic", false, "Solid Basic"},
      {"Tag 2 Dash", false, "Dash"},
      {"Tag 3 Dot", false, "Dot"},
      {"Tag 4 Dash dot", false, "Dash dot"},
      {"Tag 5 Shared Dash", false, "Dash"},
      {"Tag 6 Hidden Long-dash double-dash", true, "Long-dash double-dash"},
      {"Tag 7 Custom Wide Dash", false, "Custom Wide Dash"},
  };
  EXPECT_EQ(actual, expected);
  std::vector<std::string> face_tags;
  for (const auto& [id, face] : model.root().faces) face_tags.push_back(face.layer);
  std::vector<std::string> expected_face_tags;
  for (std::size_t i = 1; i < expected.size(); ++i) {
    expected_face_tags.push_back(std::get<0>(expected[i]));
  }
  EXPECT_EQ(face_tags, expected_face_tags);
}

}  // namespace
}  // namespace openskp
