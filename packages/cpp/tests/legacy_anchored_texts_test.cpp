#include <gtest/gtest.h>

#include <openskp/openskp.hpp>

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

ByteBuffer text_record(std::uint32_t kind, Point point, ByteBuffer refs = {},
                       Point label = {0, 0, 0}) {
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
      test::f64s({label.x, label.y, label.z}),  // label position
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
  const auto data = test::concat({text_record(1, {1, 2, 3}, {}, {4, 5, 6}), definition_tail()});
  Slots slots;
  const auto res = slots.read(data, 1);
  EXPECT_EQ(res.items, (Items{{kFirstFree, "CText"}}));
  EXPECT_EQ(slots.next, kFirstFree + 1);
  const auto& geom = slots.slots.at(kFirstFree).v->annotation_text->geometry;
  EXPECT_EQ(geom.point, (Vec3{1.0, 2.0, 3.0}));
  EXPECT_EQ(geom.label_point, (Vec3{4.0, 5.0, 6.0}));
  EXPECT_EQ(ByteBuffer(data.begin() + res.next, data.end()), definition_tail());
}

TEST(LegacyAnchoredTexts, AVertexAnchoredLabelResolvesOnlyWithoutExtraOrInstancePaths) {
  const auto direct_refs = test::concat({u16(9), {0, 0}, ByteBuffer(8)});
  const auto direct = test::concat({text_record(2, {0, 0, 0}, direct_refs), definition_tail()});
  Slots s1;
  const auto r1 = s1.read(direct, 1);
  EXPECT_EQ(s1.slots.at(kFirstFree).v->annotation_text->vertex, 9u);
  EXPECT_EQ(ByteBuffer(direct.begin() + r1.next, direct.end()), definition_tail());

  const auto extra_refs = test::concat({u16(9), u16(8), ByteBuffer(8)});
  const auto with_extra = test::concat({text_record(2, {0, 0, 0}, extra_refs), definition_tail()});
  Slots s2;
  const auto r2 = s2.read(with_extra, 1);
  EXPECT_FALSE(s2.slots.at(kFirstFree).v->annotation_text->vertex.has_value());
  EXPECT_EQ(ByteBuffer(with_extra.begin() + r2.next, with_extra.end()), definition_tail());
}

TEST(LegacyAnchoredTexts, UnanchoredVertexScanRespectsLabelBounds) {
  const ByteBuffer crafted_x_bytes{0x09, 0x00, 0x00, 0x00, 0x00, 0x00, 0x10, 0x40};
  double crafted_x = 0.0;
  std::memcpy(&crafted_x, crafted_x_bytes.data(), sizeof(crafted_x));
  const auto rec = test::concat({
      u16(0x8000 | kText),
      {0, 0},
      ByteBuffer(8),
      {0, 0},
      u16(kFont),
      ByteBuffer(6),
      u32(2),
      u32(4),
      test::f64s({0, 0, 0}),
      crafted_x_bytes,
      test::f64s({5, 6}),
      test::f64s({-0.577, -0.577, -0.577}),
      u32(2),
      {1, 0, 0, 0, 1, 0, 3, 0, 0, 0, 1},
      {0xff, 0xfe, 0xff, 3, 'L', 0, 'S', 0, '1', 0},
      ByteBuffer(5),
      definition_tail(),
  });
  Slots slots;
  const auto res = slots.read(rec, 1);
  const auto& txt = *slots.slots.at(kFirstFree).v->annotation_text;
  EXPECT_FALSE(txt.vertex.has_value());
  EXPECT_EQ(txt.geometry.label_point, (Vec3{crafted_x, 5.0, 6.0}));
  EXPECT_EQ(ByteBuffer(rec.begin() + res.next, rec.end()), definition_tail());
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

TEST(LegacyAnchoredTexts, ClassicLeaderAndScreenTextFixturesMatchSdk) {
  const auto model = SkpFile::open(test::fixture("classic_text_anchors_2017.skp")).parse();
  ASSERT_EQ(model.root().texts.size(), 4u);

  const auto& free = model.root().texts[0];
  EXPECT_EQ(free.text, "Free callout");
  EXPECT_EQ(free.point, (Vec3{1.0, 2.0, 3.0}));
  EXPECT_EQ(free.label_point, (Vec3{5.0, 7.0, 9.0}));
  EXPECT_EQ(free.screen_position, (std::array<double, 2>{0.0, 0.0}));
  EXPECT_EQ(free.leader_type, 2);
  EXPECT_EQ(free.arrow_type, 3);
  EXPECT_TRUE(free.display_leader);
  EXPECT_FALSE(free.hidden);
  EXPECT_EQ(free.layer, "");

  const auto& vertex = model.root().texts[1];
  EXPECT_EQ(vertex.text, "Vertex callout");
  EXPECT_EQ(vertex.point, (Vec3{20.0, 30.0, 0.0}));
  EXPECT_EQ(vertex.label_point, (Vec3{10.0, 12.0, 14.0}));
  EXPECT_EQ(vertex.screen_position, (std::array<double, 2>{0.5, 0.5}));
  EXPECT_EQ(vertex.leader_type, 2);
  EXPECT_EQ(vertex.arrow_type, 4);
  EXPECT_TRUE(vertex.display_leader);
  EXPECT_FALSE(vertex.hidden);
  EXPECT_EQ(vertex.layer, "");

  const auto& view = model.root().texts[2];
  EXPECT_EQ(view.text, "View callout");
  EXPECT_EQ(view.point, (Vec3{3.0, 4.0, 5.0}));
  EXPECT_EQ(view.label_point, (Vec3{6.0, 7.0, 8.0}));
  EXPECT_EQ(view.screen_position, (std::array<double, 2>{0.5, 0.5}));
  EXPECT_EQ(view.leader_type, 1);
  EXPECT_EQ(view.arrow_type, 1);
  EXPECT_TRUE(view.display_leader);
  EXPECT_FALSE(view.hidden);
  EXPECT_EQ(view.layer, "");

  const auto& screen = model.root().texts[3];
  EXPECT_EQ(screen.text, "Screen note");
  EXPECT_EQ(screen.point, (Vec3{0.0, 0.0, 0.0}));
  EXPECT_EQ(screen.label_point, (Vec3{0.0, 0.0, 0.0}));
  ASSERT_TRUE(screen.screen_position.has_value());
  EXPECT_NEAR((*screen.screen_position)[0], 0.35, 1e-9);
  EXPECT_NEAR((*screen.screen_position)[1], 0.65, 1e-9);
  EXPECT_EQ(screen.leader_type, 0);
  EXPECT_EQ(screen.arrow_type, 0);
  EXPECT_TRUE(screen.display_leader);
  EXPECT_FALSE(screen.hidden);
  EXPECT_EQ(screen.layer, "");

  const auto v20 = SkpFile::open(test::fixture("sdk_saved_leaderless_text_edges_2020.skp")).parse();
  ASSERT_EQ(v20.root().texts.size(), 2u);
  const auto& title = v20.root().texts[0];
  EXPECT_EQ(title.text, "Screen title");
  EXPECT_EQ(title.point, (Vec3{0.0, 0.0, 0.0}));
  EXPECT_EQ(title.label_point, (Vec3{0.0, 0.0, 0.0}));
  EXPECT_EQ(title.screen_position, (std::array<double, 2>{0.0, 0.0}));
  EXPECT_EQ(title.leader_type, 0);
  EXPECT_EQ(title.arrow_type, 3);
  EXPECT_FALSE(title.display_leader);

  const auto& callout = v20.root().texts[1];
  EXPECT_EQ(callout.text, "Callout");
  EXPECT_EQ(callout.point, (Vec3{5.0, 5.0, 0.0}));
  EXPECT_EQ(callout.label_point, (Vec3{7.0, 7.0, 4.0}));
  EXPECT_EQ(callout.screen_position, (std::array<double, 2>{0.0, 0.0}));
  EXPECT_EQ(callout.leader_type, 2);
  EXPECT_EQ(callout.arrow_type, 3);
  EXPECT_TRUE(callout.display_leader);
}

TEST(LegacyAnchoredTexts, FaceAnchoredTextsMatchSdkInClassicAndVff) {
  const auto expect_vec3 = [](const std::optional<Vec3>& actual, const Vec3& expected) {
    ASSERT_TRUE(actual.has_value());
    EXPECT_NEAR((*actual)[0], expected[0], 1e-11);
    EXPECT_NEAR((*actual)[1], expected[1], 1e-11);
    EXPECT_NEAR((*actual)[2], expected[2], 1e-11);
  };

  for (const char* fixture_name :
       {"classic_face_text_anchors_2017.skp", "vff_face_text_anchors_2025.skp"}) {
    SCOPED_TRACE(fixture_name);
    const auto model = SkpFile::open(test::fixture(fixture_name)).parse();
    ASSERT_EQ(model.root().texts.size(), 4u);
    EXPECT_EQ(model.root().texts[0].text, "Root untextured");
    expect_vec3(model.root().texts[0].point, {9.0, 10.0, 2.0});
    expect_vec3(model.root().texts[0].label_point, {3.0, 4.0, 5.0});

    EXPECT_EQ(model.root().texts[1].text, "Root textured");
    expect_vec3(model.root().texts[1].point, {64.5, 3.6, 0.0});
    expect_vec3(model.root().texts[1].label_point, {4.0, 5.0, 6.0});

    EXPECT_EQ(model.root().texts[2].text, "On component");
    expect_vec3(model.root().texts[2].point, {90.0, 56.0, 12.0});
    expect_vec3(model.root().texts[2].label_point, {5.0, 6.0, 7.0});

    EXPECT_EQ(model.root().texts[3].text, "On group");
    expect_vec3(model.root().texts[3].point, {56.0, 32.0, 11.0});
    expect_vec3(model.root().texts[3].label_point, {6.0, 7.0, 8.0});

    const Definition* own_def = nullptr;
    for (const auto& [_, def] : model.definitions) {
      if (def.name == "OwnTextPanel") own_def = &def;
    }
    ASSERT_NE(own_def, nullptr);
    ASSERT_EQ(own_def->texts.size(), 1u);
    EXPECT_EQ(own_def->texts[0].text, "In component");
    expect_vec3(own_def->texts[0].point, {3.0, 5.0, 1.0});
    expect_vec3(own_def->texts[0].label_point, {2.0, 3.0, 4.0});
  }

  const auto capilla = SkpFile::open(test::fixture("capilla_quiroz_v17.skp")).parse();
  const TextEntity* area = nullptr;
  for (const auto& t : capilla.root().texts) {
    if (t.text == "4.06 m\xC2\xB2") area = &t;
  }
  ASSERT_NE(area, nullptr);
  expect_vec3(area->point, {19.346940925615975, 154.92910008345638, 112.92862002631385});
}

}  // namespace
}  // namespace openskp
