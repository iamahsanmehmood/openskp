#include <gtest/gtest.h>

#include "test_helpers.hpp"

namespace openskp {
namespace {

// Legacy (MFC) text labels anchored to an entity.
//
// 16 bytes after its font, a CText carries the connection block dimensions use,
// [u32 type][u32 4][point3d]: type 0 or 1 when the label is anchored to nothing (1 for a leader
// pointing at a free point stored inline), other types (2, 4, 5 and 7 seen) for a label anchored
// to an entity. As in a dimension, the block is followed by the anchored entity's ref and its
// instance paths, null and empty for types 0 and 1. MFC writes an object in full the first time
// it is referenced, so a path can hold a whole NEW instance: the component a label is anchored to,
// when the label comes first in its entity list. The reader skipped the block as bytes, so that
// instance never got its store-map slots and every later reference was off by them: the
// auditorium (CR4) models of the BRAS database, whose loudspeaker labels are anchored to the
// loudspeakers, did not open, and two of its coupled-room (CR1) models lost root entities (one
// read 5 of its 318). The entity list's declared count holds such an instance, but SketchUp does
// not always list it again with a back-reference.
//
// Synthetic bytes only. A vertex stands in for the anchored instance: its record is short, and
// the list's bookkeeping does not depend on the class.

constexpr std::uint64_t kText = 1, kFontClass = 2, kFont = 3, kVertexClass = 4;  // pre-registered
constexpr std::uint64_t kFirstFree = 5;

ByteBuffer u16(std::uint16_t n) { return {std::uint8_t(n), std::uint8_t(n >> 8)}; }

ByteBuffer u32(std::uint32_t n) {
  return {std::uint8_t(n), std::uint8_t(n >> 8), std::uint8_t(n >> 16), std::uint8_t(n >> 24)};
}

// A SketchUp 2015 archive's slot table that knows CText, a font and CVertex.
struct Slots {
  std::unordered_map<std::uint64_t, LegacySlotEntry> slots{
      {kText, {true, "CText", 0, {}}},
      {kFontClass, {true, "CSkFont", 0, {}}},
      {kFont, {false, "CSkFont", 0, {}}},
      {kVertexClass, {true, "CVertex", 0, {}}},
  };
  std::unordered_map<std::string, std::uint64_t> class_slot{
      {"CText", kText}, {"CSkFont", kFontClass}, {"CVertex", kVertexClass}};
  std::uint64_t next = kFirstFree;

  LegacyEntityListResult read(const ByteBuffer& data, std::uint32_t count) {
    return read_legacy_entity_list(data, 0, 15, count, LegacySlotTable{slots, class_slot, next});
  }
};

// A new CText: its class tag and record. `refs` is what follows the connection block: the
// anchored entity's ref and its instance paths.
struct Point {
  double x, y, z;
};

ByteBuffer text_record(std::uint32_t kind, Point point, ByteBuffer refs = {}) {
  return test::concat({
      u16(0x8000 | kText),
      {0, 0},  // no attribute container
      ByteBuffer(8),
      {0, 0},          // draw block, layer 0
      u16(kFont),      // the font, a back-reference
      ByteBuffer(16),  // screen fractions
      u32(kind),
      u32(4),
      test::f64s({point.x, point.y, point.z}),
      refs.empty() ? ByteBuffer(12) : refs,
      test::f64s({0, 0, 0}),  // label position
      test::f64s({-0.577, -0.577, -0.577}),
      u32(2),
      {1, 0, 0, 0, 1, 0, 3, 0, 0, 0, 1},  // arrow 3
      {0xff, 0xfe, 0xff, 3, 'L', 0, 'S', 0, '1', 0},
      ByteBuffer(5),
  });
}

ByteBuffer new_vertex() {
  return test::concat({u16(0x8000 | kVertexClass), {0, 0}, test::f64s({1, 2, 3})});
}

// Anchored to entity 9 inside an instance written here in full.
ByteBuffer paths_with_new_instance() {
  return test::concat({u16(9), {0, 0}, u32(1), new_vertex(), u32(0)});
}

// No relationships, then a GUID and the definition's name.
ByteBuffer definition_tail() {
  return test::concat(
      {ByteBuffer(4), ByteBuffer(2), ByteBuffer(16), {0xff, 0xfe, 0xff, 2, 'L', 0, 'S', 0}});
}

using Items = std::vector<std::pair<std::uint64_t, std::string>>;

TEST(LegacyAnchoredTexts, AnAnchoredLabelReadsTheInstanceWrittenInItsPath) {
  const std::pair<std::uint32_t, Point> cases[] = {
      {2, {0, 0, 0}}, {4, {0, 0, 0}}, {5, {12.5, -3.0, 40.0}}, {7, {1, 2, 3}}};
  for (const auto& [kind, point] : cases) {
    SCOPED_TRACE(kind);
    const auto data =
        test::concat({text_record(kind, point, paths_with_new_instance()), definition_tail()});
    Slots slots;
    const auto res = slots.read(data, 2);
    EXPECT_EQ(res.items, (Items{{kFirstFree, "CText"}, {kFirstFree + 1, "CVertex"}}));
    EXPECT_EQ(slots.slots.at(kFirstFree + 1).name, "CVertex");
    EXPECT_EQ(ByteBuffer(data.begin() + res.next, data.end()), definition_tail());
  }
}

TEST(LegacyAnchoredTexts, AFreeLabelReadsNoPaths) {
  const auto data = test::concat({text_record(1, {1, 2, 3}), definition_tail()});
  Slots slots;
  const auto res = slots.read(data, 1);
  EXPECT_EQ(res.items, (Items{{kFirstFree, "CText"}}));
  EXPECT_EQ(slots.next, kFirstFree + 1);
  EXPECT_EQ(ByteBuffer(data.begin() + res.next, data.end()), definition_tail());
}

TEST(LegacyAnchoredTexts, AnInstanceWrittenInALabelAndListedAgainIsListedOnce) {
  const auto data = test::concat({text_record(2, {0, 0, 0}, paths_with_new_instance()),
                                  u16(kFirstFree + 1),  // its back-reference
                                  definition_tail()});
  Slots slots;
  const auto res = slots.read(data, 2);
  EXPECT_EQ(res.items, (Items{{kFirstFree, "CText"}, {kFirstFree + 1, "CVertex"}}));
  EXPECT_EQ(ByteBuffer(data.begin() + res.next, data.end()), definition_tail());
}

// A path runs from the outermost instance in; the inner ones live in other definitions.
TEST(LegacyAnchoredTexts, OnlyTheOutermostInstanceOfAPathIsOneOfTheListsItems) {
  const auto refs = test::concat({u16(9), {0, 0}, u32(2), new_vertex(), new_vertex(), u32(0)});
  const auto data = test::concat({text_record(2, {0, 0, 0}, refs), definition_tail()});
  Slots slots;
  const auto res = slots.read(data, 2);
  EXPECT_EQ(res.items, (Items{{kFirstFree, "CText"}, {kFirstFree + 1, "CVertex"}}));
  EXPECT_EQ(slots.slots.at(kFirstFree + 2).name, "CVertex");  // read, not listed
  EXPECT_EQ(ByteBuffer(data.begin() + res.next, data.end()), definition_tail());
}

}  // namespace
}  // namespace openskp
