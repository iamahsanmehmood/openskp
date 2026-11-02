#include <gtest/gtest.h>
#include <map>

#include <openskp/openskp.hpp>

#include "test_helpers.hpp"

namespace openskp {
namespace {

// Textured and colorized materials in a classic file read as SketchUp reads them.
//
// Fixture legacy_textured_colorize_2017.skp: four materials with the same 2x1 texture, made and
// saved as a SketchUp 2017 file by SketchUp's own library: plain (type 1, textured), tint and
// shift (type 2, colorized, colorize mode tint 1 and shift 0) and tint_half (a tint at opacity
// 0.5). The same file is a fixture of the other four ports, which read textured materials as
// colorized before. No private content.

TEST(LegacyTexturedColorize, TextureTypeColorizeModeAndPaintColour) {
  const auto model = SkpFile::open(test::fixture("legacy_textured_colorize_2017.skp")).parse();
  std::map<std::string, Material> by_name;
  for (const auto& m : model.materials) by_name[m.name] = m;
  ASSERT_EQ(by_name.size(), 4u);

  const auto expect = [&](const char* name, Color3 rgb, bool colorized, int mode,
                          double transparency) {
    SCOPED_TRACE(name);
    const auto& m = by_name.at(name);
    EXPECT_EQ(m.color[0], rgb[0]);
    EXPECT_EQ(m.color[1], rgb[1]);
    EXPECT_EQ(m.color[2], rgb[2]);
    EXPECT_EQ(m.colorized, colorized);
    EXPECT_EQ(m.colorize_type, mode);
    EXPECT_NEAR(m.transparency, transparency, 1e-9);
  };
  expect("plain", {120, 40, 120}, false, 0, 1.0);
  expect("tint", {30, 160, 60}, true, 1, 1.0);
  expect("shift", {200, 90, 20}, true, 0, 1.0);
  expect("tint_half", {10, 20, 230}, true, 1, 0.5);
}

}  // namespace
}  // namespace openskp
