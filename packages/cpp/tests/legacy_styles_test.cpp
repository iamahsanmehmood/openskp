#include <gtest/gtest.h>

#include <openskp/openskp.hpp>

#include "test_helpers.hpp"

// A classic (SketchUp 2020 and older) file keeps its styles as CSkpStyle objects in the file
// tail, with the same option ids, types and values a newer file's style.xml holds. The fixtures
// are SketchUp 2027's saves of one model - a triangle painted with a 30% opaque material - as
// SketchUp 2017 and 2020, with the style's Material Transparency (option 2005) on and off.
// SketchUp draws that triangle opaque when the option is off, so a renderer needs it.

namespace openskp::test {
namespace {

const Style* displayed(const SkpModel& model) {
  for (const auto& style : model.styles)
    if (style.working_copy) return &style;
  return nullptr;
}

TEST(LegacyStyles, ReadsMaterialTransparencyOfAClassicFile) {
  for (const char* year : {"2017", "2020"}) {
    for (bool on : {false, true}) {
      const std::string name =
          std::string("style_transparency_") + (on ? "on_" : "off_") + year + ".skp";
      SCOPED_TRACE(name);
      const auto model = SkpFile::open(fixture(name.c_str())).parse();

      ASSERT_EQ(model.styles.size(), 2U);
      EXPECT_EQ(model.styles[0].name, "[Simple Style]");
      EXPECT_TRUE(model.styles[0].active);
      EXPECT_FALSE(model.styles[0].working_copy);
      EXPECT_FALSE(model.styles[0].modified);
      EXPECT_FALSE(model.styles[1].active);
      EXPECT_TRUE(model.styles[1].working_copy);

      const auto* style = displayed(model);
      ASSERT_NE(style, nullptr);
      ASSERT_TRUE(style->items.count(2005));
      EXPECT_EQ(style->items.at(2005).type, 1);
      EXPECT_EQ(style->items.at(2005).value, on ? "1" : "0");
    }
  }
}

TEST(LegacyStyles, SpellsOptionValuesLikeAStyleXml) {
  const auto model = SkpFile::open(fixture("style_transparency_on_2020.skp")).parse();
  ASSERT_FALSE(model.styles.empty());
  const auto& style = model.styles[0];
  EXPECT_EQ(style.description, "[Default face colors. Blue sky and gray background color.]");
  EXPECT_EQ(style.front_color, (Color3{255, 255, 255}));
  EXPECT_EQ(style.back_color, (Color3{162, 183, 198}));
  // profile width: an int32
  EXPECT_EQ(style.items.at(1007).type, 4);
  EXPECT_EQ(style.items.at(1007).value, "2");
  // back face color: a signed int32 ABGR
  EXPECT_EQ(style.items.at(2003).value, "-3754078");
  // X-ray opacity: a double
  EXPECT_EQ(style.items.at(2008).type, 7);
  EXPECT_EQ(style.items.at(2008).value, "0.65");
  // the watermark list has no values in a classic file
  EXPECT_FALSE(style.items.count(5001));
}

// The same model saved in the current format gives the same answer through its style.xml.
TEST(LegacyStyles, AgreesWithTheCurrentFormat) {
  for (bool on : {false, true}) {
    const auto model = SkpFile::open(fixture(on ? "style_transparency_on_2021.skp"
                                                : "style_transparency_off_2021.skp"))
                           .parse();
    const auto* style = displayed(model);
    ASSERT_NE(style, nullptr);
    EXPECT_EQ(style->items.at(2005).type, 1);
    EXPECT_EQ(style->items.at(2005).value, on ? "1" : "0");
  }
}

// gondola_v20.skp has one scene, which holds the style; the working copy follows the scenes.
TEST(LegacyStyles, FindsAStyleStoredInAScene) {
  const auto model = SkpFile::open(fixture("gondola_v20.skp")).parse();
  ASSERT_EQ(model.styles.size(), 2U);
  EXPECT_EQ(model.styles[0].name, "[Architectural Design Style]");
  EXPECT_TRUE(model.styles[0].active);
  EXPECT_TRUE(model.styles[1].working_copy);
  EXPECT_EQ(model.styles[1].items.at(2005).value, "1");
}

// Files older than SketchUp 2017 have no pid before the style's GUID.
TEST(LegacyStyles, ReadsStylesOfOlderVersions) {
  for (const char* name : {"nesting_v6.skp", "nesting_2013.skp", "capilla_quiroz_v17.skp",
                           "zero_material_custom_layer_v18.skp", "legacy_v2019_synthetic.skp"}) {
    SCOPED_TRACE(name);
    const auto model = SkpFile::open(fixture(name)).parse();
    ASSERT_EQ(model.styles.size(), 2U);
    EXPECT_TRUE(model.styles[0].active);
    EXPECT_TRUE(model.styles[1].working_copy);
    EXPECT_TRUE(model.styles[1].items.count(2005));
  }
}

// Bytes that read like a style's strings and options but don't follow a CSkpStyle object's head
// are not a style: appended to a file they must not become its working copy.
TEST(LegacyStyles, IgnoresStyleLikeBytesOutsideAStyleObject) {
  auto data = read_fixture("style_transparency_off_2017.skp");
  const ByteBuffer empty_string{0xFF, 0xFE, 0xFF, 0x00};
  ByteBuffer fake(16, 0x41);  // where a GUID would be
  for (int i = 0; i < 3; ++i) {
    fake.insert(fake.end(), empty_string.begin(), empty_string.end());
    if (i == 0) fake.insert(fake.end(), {3, 0, 0, 0});
  }
  fake.insert(fake.end(), {8, 0, 0, 0});
  for (std::uint8_t id = 0; id < 8; ++id) fake.insert(fake.end(), {id, 0x10, 0, 0, 0, 0, 0, 0});
  data.insert(data.end(), fake.begin(), fake.end());

  const auto model = SkpFile::from_buffer(std::move(data)).parse();
  ASSERT_EQ(model.styles.size(), 2U);
  EXPECT_TRUE(model.styles[1].working_copy);
  EXPECT_EQ(model.styles[1].items.at(2005).value, "0");
}

// A tail of string headers that each claim to run to the end of the file is rejected by length,
// without decoding it once per header.
TEST(LegacyStyles, ScansATailOfOversizedStringHeadersQuickly) {
  auto data = read_fixture("style_transparency_off_2017.skp");
  const ByteBuffer header{0x80, 0x00, 0x00, 0x41, 0x41, 0x41, 0x41, 0x41, 0x41, 0x41,
                          0x41, 0x41, 0x41, 0x41, 0x41, 0x41, 0x41, 0x41, 0x41, 0xFF,
                          0xFE, 0xFF, 0xFF, 0xFF, 0xFF, 0x00, 0x00, 0x10, 0x00};
  for (int i = 0; i < 40000; ++i) data.insert(data.end(), header.begin(), header.end());

  const auto model = SkpFile::from_buffer(std::move(data)).parse();
  EXPECT_EQ(model.styles.size(), 2U);
}

// SketchUp 5 and older have no styles.
TEST(LegacyStyles, LeavesStylesEmptyWithoutAny) {
  const auto model = SkpFile::open(fixture("legacy_v5_synthetic.skp")).parse();
  EXPECT_TRUE(model.styles.empty());
}

}  // namespace
}  // namespace openskp::test
