#include <gtest/gtest.h>

#include <openskp/openskp.hpp>

#include "internal.hpp"
#include "test_helpers.hpp"

namespace openskp {
namespace {

// A SketchUp 2020 model with nothing in it (no entities, no component definitions) opens.
//
// Fixture legacy_empty_2020.skp: an empty model saved as a SketchUp 2020 file (SUModelVersion 13)
// by SketchUp's own library, the blank-template case. SketchUp 2020 writes an empty string record
// and zero padding where the definition count would be; with nothing in the model the only count
// behind it is the root entity count, which is 0, and the filler search skipped it, so the whole
// file was rejected with an invalid definition count. The same file is a fixture of the other four
// ports. No private content.

TEST(LegacyEmptyModel, AnEmpty2020ModelParsesToAnEmptyModel) {
  const auto model = SkpFile::open(test::fixture("legacy_empty_2020.skp")).parse();
  ASSERT_EQ(model.layers.size(), 1u);
  EXPECT_EQ(model.layers[0].name, "Layer0");
  EXPECT_TRUE(model.definitions.empty());
  EXPECT_TRUE(model.materials.empty());
  EXPECT_TRUE(model.root().faces.empty());
  EXPECT_TRUE(model.root().instances.empty());
}

// 3 bytes of the previous record, the empty string record, `zeros` zero bytes, then a non-zero
// record.
ByteBuffer filler(size_t zeros, ByteBuffer tail = {0x58, 0xe7, 0x37, 0x6a}) {
  ByteBuffer bytes{0, 0, 0, 0xff, 0xfe, 0xff, 0};
  bytes.insert(bytes.end(), zeros, 0);
  bytes.insert(bytes.end(), tail.begin(), tail.end());
  return bytes;
}

TEST(LegacyEmptyModel, FillerReturnsTheRootCountOffsetThirteenBytesOfPaddingBehindTheLengthByte) {
  const auto at = find_empty_root_after_v20_filler(filler(26), 0);
  ASSERT_TRUE(at.has_value());
  EXPECT_EQ(*at, size_t{3 + 4 + 13});
}

TEST(LegacyEmptyModel, FillerTreatsANonEmptyStringAsRealData) {
  auto data = filler(30);
  data[6] = 5;
  EXPECT_FALSE(find_empty_root_after_v20_filler(data, 0).has_value());
}

TEST(LegacyEmptyModel, FillerRejectsANonZeroByteInThePaddingOrTheCount) {
  for (size_t at = 4; at < 4 + 13 + 4; ++at) {
    SCOPED_TRACE(at);
    auto data = filler(26);
    data[3 + at] = 1;
    EXPECT_FALSE(find_empty_root_after_v20_filler(data, 0).has_value());
  }
}

TEST(LegacyEmptyModel, FillerRejectsNoFillerNearbyAndATruncatedFiller) {
  EXPECT_FALSE(find_empty_root_after_v20_filler(ByteBuffer(64, 0), 0).has_value());
  EXPECT_FALSE(find_empty_root_after_v20_filler(filler(5, {}), 0).has_value());
}

}  // namespace
}  // namespace openskp
