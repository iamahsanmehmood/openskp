#include <gtest/gtest.h>

#include "internal.hpp"

namespace openskp {
namespace {

// material_xml()/style_xml() (core.cpp) extract attributes via std::regex
// rather than a real XML parser - the only two of this project's five
// ports that do (the other three use a real XML library and decode
// entities correctly by construction). Without decoding, a name like
// SketchUp's own "<auto>" default-material convention - which the raw XML
// bytes MUST spell as "&lt;auto&gt;" - came through still escaped.

TEST(XmlEntities, DecodesLtGt) {
  EXPECT_EQ(decode_xml_entities("&lt;auto&gt;"), "<auto>");
}

TEST(XmlEntities, DecodesAmpApostropheQuote) {
  EXPECT_EQ(decode_xml_entities("Tom &amp; Jerry&apos;s &quot;Wood&quot;"), "Tom & Jerry's \"Wood\"");
}

TEST(XmlEntities, DecodesNumericCharacterReferencesDecimalAndHex) {
  EXPECT_EQ(decode_xml_entities("&#65;&#x42;"), "AB");
}

TEST(XmlEntities, DoesNotDoubleDecodeAmpLt) {
  // A single left-to-right pass must consume "&amp;" as one match (5
  // chars), leaving the trailing "lt;" as literal text - never re-scanning
  // its own output to find a second "&lt;" to decode into "<".
  EXPECT_EQ(decode_xml_entities("&amp;lt;"), "&lt;");
}

TEST(XmlEntities, LeavesPlainTextUnchanged) {
  EXPECT_EQ(decode_xml_entities("Plain Wood Oak"), "Plain Wood Oak");
}

TEST(XmlEntities, LeavesMalformedEntityLikeTextAsIs) {
  // Not a real entity (no trailing ";", unknown name) - passed through
  // rather than guessed at.
  EXPECT_EQ(decode_xml_entities("Fish & Chips"), "Fish & Chips");
  EXPECT_EQ(decode_xml_entities("&nbsp;"), "&nbsp;");
}

}  // namespace
}  // namespace openskp
