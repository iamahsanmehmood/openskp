#include <gtest/gtest.h>

#include "test_helpers.hpp"

namespace openskp {
namespace {

// Port of openskp#384's Python fix to C++ (openskp#412): legacy (MFC) linear
// dimensions anchored inside groups.
//
// Each connection ref of a CDimensionLinear is followed by an entity ref and
// two lists of entity refs - the instance paths of the anchored entity. On
// loose geometry they are a null ref and two empty lists (the zeros a fixed
// 42/82-byte layout used to skip); anchored inside nested components they
// carry a ref per component, and a fixed-size read slid off the record,
// silently cutting the root entity list short. The first time an instance is
// referenced MFC writes it in full, so a path can also hold a whole NEW
// object.
//
// Synthetic bytes only: the real file that exposed this (a SketchUp 2017
// cabinet, 21 of 96 root instances lost) is private and is not committed.

// A 6-byte big reference (0x7fff escape) to an existing object.
ByteBuffer big(uint32_t slot) {
  return {0xff, 0x7f, uint8_t(slot), uint8_t(slot >> 8), uint8_t(slot >> 16), uint8_t(slot >> 24)};
}

ByteBuffer u32(uint32_t n) {
  return {uint8_t(n), uint8_t(n >> 8), uint8_t(n >> 16), uint8_t(n >> 24)};
}

const ByteBuffer kTail{'T', 'A', 'I', 'L'};

auto never_new = [](std::size_t) -> std::pair<std::uint64_t, std::size_t> {
  throw std::runtime_error("unexpected new object");
};

TEST(LegacyDimensionPaths, LooseGeometryPathsAreEmptyAndTenBytes) {
  auto data = test::concat({{0, 0}, u32(0), u32(0), kTail});
  auto res = legacy_connection_paths(data, 0, never_new);
  EXPECT_FALSE(res.extra.has_value());
  EXPECT_TRUE(res.first.empty());
  EXPECT_TRUE(res.second.empty());
  EXPECT_EQ(res.next, 10u);  // the old fixed layout's zeros
}

TEST(LegacyDimensionPaths, PathsInsideNestedComponentsAreReadRefByRef) {
  auto data = test::concat({big(0x0f69ca), u32(2), big(0x166157), big(0x165bbc), u32(2),
                            big(0x166157), big(0x165bbd), kTail});
  auto res = legacy_connection_paths(data, 0, never_new);
  ASSERT_TRUE(res.extra.has_value());
  EXPECT_EQ(*res.extra, 0x0f69caULL);
  ASSERT_EQ(res.first.size(), 2u);
  EXPECT_EQ(*res.first[0], 0x166157ULL);
  EXPECT_EQ(*res.first[1], 0x165bbcULL);
  ASSERT_EQ(res.second.size(), 2u);
  EXPECT_EQ(*res.second[0], 0x166157ULL);
  EXPECT_EQ(*res.second[1], 0x165bbdULL);
  EXPECT_EQ(ByteBuffer(data.begin() + res.next, data.end()), kTail);
}

TEST(LegacyDimensionPaths, APathCanHoldAWholeNewObject) {
  // MFC serializes an object in full the first time it is referenced.
  const ByteBuffer new_tag{0xff, 0x7f, 0x67, 0x9f, 0x00, 0x80};
  auto data = test::concat({{0, 0}, u32(1), new_tag, {'O', 'B', 'J'}, u32(0), kTail});
  std::size_t asked_at = 0;
  auto res = legacy_connection_paths(data, 0, [&](std::size_t at) {
    asked_at = at;
    return std::make_pair(std::uint64_t(4242), at + 6 + 3);  // the object's own bytes
  });
  EXPECT_EQ(asked_at, 2u + 4u);
  EXPECT_FALSE(res.extra.has_value());
  ASSERT_EQ(res.first.size(), 1u);
  EXPECT_EQ(*res.first[0], 4242ULL);
  EXPECT_TRUE(res.second.empty());
  EXPECT_EQ(ByteBuffer(data.begin() + res.next, data.end()), kTail);
}

TEST(LegacyDimensionPaths, ImplausiblePathLengthIsRejected) {
  auto data = test::concat({{0, 0}, u32(100000)});
  EXPECT_THROW(legacy_connection_paths(data, 0, never_new), std::runtime_error);
}

}  // namespace
}  // namespace openskp
