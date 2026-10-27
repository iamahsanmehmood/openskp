#include <gtest/gtest.h>

#include <openskp/openskp.hpp>

#include "test_helpers.hpp"

namespace openskp {
namespace {

// A leader text with any arrow type, and a font with a multi-byte pid, in a classic file
// (openskp#442).
//
// Fixture legacy_text_arrows_2017.skp: three leader texts written by openskp.create with arrow
// types 1 (dot), 2 (closed arrow) and 4 (open arrow) instead of the writer's 3, and a text font
// whose pid is two bytes, as in the real project's SketchUp 2017 to 2020 saves (a pid above 255
// takes two bytes). SketchUp 2025 opens it and reports the same three texts with arrow types 1, 2
// and 4. Before #442 this reader stopped on the first text. No private content.

TEST(LegacyTextArrows, EveryTextIsRead) {
  const auto model = SkpFile::open(test::fixture("legacy_text_arrows_2017.skp")).parse();
  ASSERT_EQ(model.root().texts.size(), 3u);
  EXPECT_EQ(model.root().texts[0].text, "Dot");
  EXPECT_EQ(model.root().texts[1].text, "Closed arrow");
  EXPECT_EQ(model.root().texts[2].text, "Open arrow");
}

}  // namespace
}  // namespace openskp
