#include <gtest/gtest.h>

#include "internal.hpp"

namespace openskp {
namespace {

// The v20 filler probe, exercised on synthetic records.
//
// Ported from the TypeScript fix (openskp#192, following the review on
// openskp#155): the original implementation walked forward to the first
// non-zero byte and treated it as the count's low byte. That cannot
// represent a count which is an exact multiple of 256 - its low byte IS
// 0x00, so the scan walks straight into the count and misaligns every read
// after it. Probing whole u32s at 4-byte strides fixes that, since no
// individual byte is ever inspected.
//
// Layout (see find_count_after_v20_filler, legacy.cpp):
//   <ff fe ff> <u8 0>   empty UTF-16 string
//   <zero padding>      length varies per call site, always pad % 4 == 1
//   <u32 count>

// Builds a filler record followed by `count`, then a class-record header.
ByteBuffer filler(uint32_t count, size_t pad) {
  ByteBuffer bytes{0xff, 0xfe, 0xff, 0x00};
  bytes.insert(bytes.end(), pad, 0x00);
  bytes.push_back(static_cast<uint8_t>(count & 0xff));
  bytes.push_back(static_cast<uint8_t>((count >> 8) & 0xff));
  bytes.push_back(static_cast<uint8_t>((count >> 16) & 0xff));
  bytes.push_back(static_cast<uint8_t>((count >> 24) & 0xff));
  bytes.insert(bytes.end(), {0xff, 0xff, 0x0b, 0x00});  // whatever record comes next
  return bytes;
}

TEST(V20Filler, ReadsCountsAndPaddingsSeenInRealV20Files) {
  // both padding lengths observed in gondola_v20.skp and a second v20 model
  auto hit1 = find_count_after_v20_filler(filler(20, 9), 0, 1000000);
  ASSERT_TRUE(hit1.has_value());
  EXPECT_EQ(hit1->count, 20u);
  EXPECT_EQ(hit1->next, 17u);

  auto hit2 = find_count_after_v20_filler(filler(5425, 13), 0, 5000000);
  ASSERT_TRUE(hit2.has_value());
  EXPECT_EQ(hit2->count, 5425u);
  EXPECT_EQ(hit2->next, 21u);
}

TEST(V20Filler, ReadsACountThatIsAnExactMultipleOf256) {
  // the regression this test exists for: a 0x00 low byte is
  // indistinguishable from padding to a byte-at-a-time scan
  for (uint32_t count : {256u, 512u, 1024u, 65536u, static_cast<uint32_t>(16777216 / 16)}) {
    for (size_t pad : {9u, 13u}) {
      auto hit = find_count_after_v20_filler(filler(count, pad), 0, 5000000);
      ASSERT_TRUE(hit.has_value()) << "count=" << count << " pad=" << pad;
      EXPECT_EQ(hit->count, count) << "count=" << count << " pad=" << pad;
    }
  }
}

TEST(V20Filler, ReportsWhereToResumeReading) {
  auto hit = find_count_after_v20_filler(filler(256, 13), 0, 5000000);
  ASSERT_TRUE(hit.has_value());
  // 4 (marker+len) + 13 (padding) + 4 (the count itself)
  EXPECT_EQ(hit->next, 21u);
}

TEST(V20Filler, IgnoresANonEmptyStringRecord) {
  // a real string here is genuine data; moving the cursor past it would
  // corrupt the parse
  ByteBuffer bytes{0xff, 0xfe, 0xff, 0x05, 0, 0, 0, 0, 0, 0, 0, 0, 20, 0, 0, 0};
  EXPECT_FALSE(find_count_after_v20_filler(bytes, 0, 1000000).has_value());
}

TEST(V20Filler, ReturnsNulloptWhenThereIsNoMarkerAhead) {
  ByteBuffer bytes(32, 0);  // all zeros, no ff fe ff
  EXPECT_FALSE(find_count_after_v20_filler(bytes, 0, 1000000).has_value());
}

TEST(V20Filler, RespectsTheCallersPlausibilityLimit) {
  // nrel's limit is 100_000: a value above it is not the count we want
  EXPECT_FALSE(find_count_after_v20_filler(filler(200000, 13), 0, 100000).has_value());
  auto hit = find_count_after_v20_filler(filler(200000, 13), 0, 5000000);
  ASSERT_TRUE(hit.has_value());
  EXPECT_EQ(hit->count, 200000u);
}

TEST(V20Filler, DoesNotRunPastTheEndOfTheBuffer) {
  ByteBuffer bytes{0xff, 0xfe, 0xff, 0x00, 0, 0};
  EXPECT_FALSE(find_count_after_v20_filler(bytes, 0, 1000000).has_value());
}

}  // namespace
}  // namespace openskp
