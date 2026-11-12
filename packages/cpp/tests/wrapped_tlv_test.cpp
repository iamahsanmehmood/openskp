#include <gtest/gtest.h>

#include "internal.hpp"

namespace openskp {
namespace {
constexpr std::size_t wrap = static_cast<std::size_t>(std::uint64_t{1} << 32);

struct SparseRecords {
  std::map<std::size_t, std::uint32_t> lengths;
  std::set<std::size_t> containers;

  std::vector<TlvHeader> scan(std::size_t end) {
    return tlv_headers(
        0, end,
        [&](std::size_t p) {
          const auto it = lengths.find(p);
          return it == lengths.end() ? UINT32_MAX : it->second;
        },
        [&](std::size_t p) { return containers.count(p) != 0; });
  }
};

TEST(WrappedTlv, NestedContainersAndFollowingSibling) {
  if (sizeof(std::size_t) < 8) GTEST_SKIP();
  // Four wrapped containers, a large opaque leaf and a final empty sibling.
  SparseRecords d;
  for (std::size_t p = 0; p < 24; p += 6) {
    d.lengths[p] = static_cast<std::uint32_t>(wrap + 30 - p - 6);
    d.containers.insert(p);
  }
  d.lengths[24] = UINT32_MAX;
  d.lengths[wrap + 29] = 1;
  // Leaf ends at wrap+29, then a seven-byte leaf finishes the containers.
  for (std::size_t p = 0; p < 24; p += 6) d.lengths[p] += 6;
  d.lengths[wrap + 36] = 0;
  const auto records = d.scan(wrap + 42);
  ASSERT_EQ(records.size(), 2u);
  EXPECT_EQ(records[0].size, wrap + 30);
  EXPECT_EQ(records[1].offset, wrap + 36);
}

TEST(WrappedTlv, MultipleWraps) {
  if (sizeof(std::size_t) < 8) GTEST_SKIP();
  SparseRecords d{{{0, 10}, {6, UINT32_MAX}, {wrap + 11, UINT32_MAX}}, {0}};
  const auto records = d.scan(2 * wrap + 16);
  ASSERT_EQ(records.size(), 1u);
  EXPECT_EQ(records[0].size, 2 * wrap + 10);
}

TEST(WrappedTlv, RejectsInvalidChildrenAndSuffixAndOpaqueLeaves) {
  if (sizeof(std::size_t) < 8) GTEST_SKIP();
  SparseRecords d{{{0, 5}, {6, UINT32_MAX}}, {0}};
  auto records = d.scan(wrap + 11);
  ASSERT_EQ(records.size(), 1u);
  EXPECT_EQ(records[0].size, wrap + 5);
  d.lengths[6] = UINT32_MAX - 1;
  records = d.scan(wrap + 11);
  EXPECT_EQ(records.front().size, 5u);
  d.lengths[6] = UINT32_MAX;
  records = d.scan(wrap + 12);  // Neither partition consumes the trailing byte.
  EXPECT_EQ(records.front().size, 5u);
  d.containers.clear();
  records = d.scan(wrap + 11);
  EXPECT_EQ(records.front().size, 5u);
}

TEST(WrappedTlv, KeepsAnExactLargePartitionAndNonzeroStart) {
  if (sizeof(std::size_t) < 8) GTEST_SKIP();
  SparseRecords d{{{0, UINT32_MAX}, {wrap + 5, 0}}, {0}};
  const auto records = d.scan(wrap + 11);
  ASSERT_EQ(records.size(), 2u);
  EXPECT_EQ(records.front().size, UINT32_MAX);
  EXPECT_EQ(records.back().offset, wrap + 5);
  const auto nested = tlv_headers(
      7, wrap + 18, [](std::size_t p) -> std::uint32_t { return p == 7 ? 5 : UINT32_MAX; },
      [](std::size_t p) { return p == 7; });
  ASSERT_EQ(nested.size(), 1u);
  EXPECT_EQ(nested.front().offset, 7u);
  EXPECT_EQ(nested.front().size, wrap + 5);
}

TEST(WrappedTlv, ZeroFilledPayloadDoesNotExhaustRecovery) {
  if (sizeof(std::size_t) < 8) GTEST_SKIP();
  constexpr std::size_t padding_headers = 1000005;
  for (bool nested : {false, true}) {
    std::size_t reads = 0;
    const std::size_t leaf = nested ? 12 : 6;
    const auto records = tlv_headers(
        0, wrap + leaf + 5,
        [&](std::size_t p) -> std::uint32_t {
          ++reads;
          if (p == 0) return static_cast<std::uint32_t>(leaf - 1);
          if (nested && p == 6) return 5;
          if (p == leaf) return UINT32_MAX;
          // Both short endpoints land here, inside the single opaque leaf.
          if (p >= leaf + 5 && p < leaf + 5 + 6 * padding_headers) return 0;
          return UINT32_MAX;
        },
        [&](std::size_t p) { return p == 0 || (nested && p == 6); });
    ASSERT_EQ(records.size(), 1u);
    EXPECT_EQ(records.front().size, wrap + leaf - 1);
    // Each inexact level is walked once, rather than rescanning its fake heads.
    EXPECT_LT(reads, (nested ? 2 : 1) * padding_headers + 20);
  }
}

TEST(WrappedTlv, BoundsFailedAlternativesAndPreservesTheOriginalPartition) {
  if (sizeof(std::size_t) < 8) GTEST_SKIP();
  std::size_t reads = 0;
  const auto records = tlv_headers(
      0, wrap + 1801,
      [&](std::size_t p) -> std::uint32_t {
        ++reads;
        return p < 1800 ? 0 : UINT32_MAX;
      },
      [](std::size_t) { return true; });
  ASSERT_EQ(records.size(), 300u);
  EXPECT_EQ(records.front().offset, 0u);
  EXPECT_EQ(records.back().offset, 1794u);
  EXPECT_EQ(records.front().size, 0u);
  EXPECT_EQ(records.back().size, 0u);
  // A malformed range must not try every candidate indefinitely.
  EXPECT_LT(reads, 600u);
}

TEST(WrappedTlv, ReusesOverlappingInvalidSuffixes) {
  if (sizeof(std::size_t) < 8) GTEST_SKIP();
  for (std::size_t count : {100000u, 1000000u}) {
    std::size_t reads = 0;
    const auto records = tlv_headers(
        0, wrap + 6 * count + 1,
        [&](std::size_t p) -> std::uint32_t {
          ++reads;
          if (p < 1536 || p >= wrap) return 0;
          return UINT32_MAX;
        },
        [](std::size_t p) { return p < 1536; });
    // 256 possible containers precede a large opaque leaf. Their extended
    // endpoints land on overlapping zero-filled tails, each with one spare
    // byte. Scan each alignment once, not once per candidate (256 times).
    ASSERT_EQ(records.size(), count);
    EXPECT_EQ(records.front().size, 0u);
    EXPECT_EQ(records[256].size, UINT32_MAX);
    EXPECT_LT(reads, 2 * count + 1024);
  }
}

TEST(WrappedTlv, CachedFailureDoesNotHideAnExpandableContainer) {
  if (sizeof(std::size_t) < 8) GTEST_SKIP();
  // The ordinary root walk reaches a broken suffix through a short wrapper.
  // That suffix is invalid, but the wrapper can skip it and still be valid.
  SparseRecords d{{{0, 11}, {6, 5}, {12, UINT32_MAX}}, {0, 6}};
  const auto records = d.scan(wrap + 17);
  ASSERT_EQ(records.size(), 1u);
  EXPECT_EQ(records.front().size, wrap + 11);
}

TEST(WrappedTlv, PreservesExactAndSmallLenientRanges) {
  SparseRecords d{{{0, 3}, {9, 0}}, {0}};
  EXPECT_EQ(d.scan(15).size(), 2u);
  EXPECT_EQ(d.scan(16).size(), 2u);
  EXPECT_EQ(d.scan(8).size(), 0u);
  EXPECT_TRUE(d.scan(0).empty());
  ByteBuffer bytes{0xF4, 0x01, 6, 0, 0, 0, 0xF6, 0x01, 0, 0, 0, 0};
  const auto nodes = parse_tlv_recursive(bytes, 0, bytes.size());
  ASSERT_EQ(nodes.size(), 1u);
  ASSERT_EQ(nodes[0].children.size(), 1u);
  EXPECT_EQ(nodes[0].children[0].tag, "F601");
  EXPECT_THROW(tlv_headers(bytes, 0, bytes.size() + 1), std::out_of_range);
}
}  // namespace
}  // namespace openskp
