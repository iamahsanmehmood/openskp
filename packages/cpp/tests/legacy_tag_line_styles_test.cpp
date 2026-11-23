#include <gtest/gtest.h>
#include <string>
#include <utility>
#include <vector>

#include <openskp/openskp.hpp>

#include "test_helpers.hpp"

// Tags with a line style in SketchUp 2019 and 2020 files (issue #514).
//
// Since SketchUp 2019 a tag can carry a line style ("Dash", "Dot", ...). A tag
// that has one points at a CCustomLineStyle object right after its own fields:
// nothing for a tag without a line style, the style written in full the first
// time a tag uses it (a new-class record, then the object), a class-ref for a
// second style, and a back-ref for a style that an earlier tag already wrote.
// The style is an object in the walk like any other and takes a store-map slot.

namespace openskp {
namespace {

const std::vector<std::pair<std::string, std::vector<std::string>>>& models() {
  static const std::vector<std::pair<std::string, std::vector<std::string>>> kModels = {
      {"sdk_saved_tag_line_styles_2020.skp",
       {"Tag 0 ", "Tag 1 Dash", "Tag 2 ", "Tag 3 Dash", "Tag 4 Dot", "Tag 5 Dash"}},
      {"sdk_saved_tag_line_styles_2019.skp",
       {"Tag 0 ", "Tag 1 Dash", "Tag 2 ", "Tag 3 Dash", "Tag 4 Dot", "Tag 5 Dash"}},
      {"sdk_saved_tag_line_styles_materials_2020.skp",
       {"Tag 0 Dash", "Tag 1 ", "Tag 2 Dot", "Tag 3 Dash", "Tag 4 Dot"}},
  };
  return kModels;
}

TEST(LegacyTagLineStyles, ModelWhoseTagsHaveLineStylesOpens) {
  for (const auto& [name, expected_tags] : models()) {
    SCOPED_TRACE(name);
    const auto model = SkpFile::open(test::fixture(name.c_str())).parse();
    std::vector<std::string> expected_layers = {"Layer0"};
    expected_layers.insert(expected_layers.end(), expected_tags.begin(), expected_tags.end());
    std::vector<std::string> actual_layers;
    for (const auto& layer : model.layers) actual_layers.push_back(layer.name);
    EXPECT_EQ(actual_layers, expected_layers);
    EXPECT_EQ(model.root().faces.size(), expected_tags.size());
  }
}

TEST(LegacyTagLineStyles, EachFaceStaysOnItsOwnTag) {
  // Reading the line styles takes store-map slots between the tags, so a tag's
  // slot (the id its faces carry) is only right when the walk counted them.
  for (const auto& [name, expected_tags] : models()) {
    SCOPED_TRACE(name);
    const auto model = SkpFile::open(test::fixture(name.c_str())).parse();
    std::vector<std::string> face_tags;
    for (const auto& [id, face] : model.root().faces) face_tags.push_back(face.layer);
    EXPECT_EQ(face_tags, expected_tags);
  }
}

}  // namespace
}  // namespace openskp
