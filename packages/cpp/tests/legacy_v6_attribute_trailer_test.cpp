#include <gtest/gtest.h>
#include <set>

#include <openskp/openskp.hpp>

#include "test_helpers.hpp"

namespace openskp {
namespace {

// Port of openskp#284/#385's Python fix to C++ (openskp#390): a SketchUp 6
// file's CAttributeNamed record has no trailing field, unlike v7+.
//
// Fixture: fixtures/legacy_v6_synthetic.skp - the same fixture committed
// for the Python fix in #385 (a small synthetic model: 4 component
// definitions - InnerFrame, Frame, Panel, Post; InnerFrame is also placed
// as a nested group inside Panel carrying an attribute dictionary; 10
// root-level instances - built with openskp.create() and saved via a real
// SketchUp version-downgrade export to v6).
//
// Before the fix, legacy.cpp's CAttributeNamed reader read a trailing u32
// unconditionally. That trailer is a v7+ addition - a real SketchUp 6 file
// ends the record at the empty-key terminator with nothing after it.
// Reading 4 phantom bytes silently ate into the next sibling's own tag
// (a group instance's drawbase/definition-ref fields), which didn't raise
// there - it surfaced many reads later, deep in the object graph, as an
// unrelated "back-ref to unwalked slot N" error.

TEST(LegacyV6AttributeTrailer, ParsesWithoutTheUnwalkedSlotError) {
  auto file = SkpFile::open(test::fixture("legacy_v6_synthetic.skp"));
  auto model = file.parse();
  EXPECT_EQ(model.version, "{6.0.1}");
}

TEST(LegacyV6AttributeTrailer, PlacesEveryRootInstance) {
  auto file = SkpFile::open(test::fixture("legacy_v6_synthetic.skp"));
  auto model = file.parse();
  std::vector<std::string> names;
  for (const auto& inst : model.root().instances) names.push_back(inst.name);
  EXPECT_EQ(names, (std::vector<std::string>{"", "P-1", "P-2", "P-3", "P-4", "P-5", "P-6", "P-7",
                                             "P-8", "P-9"}));
}

TEST(LegacyV6AttributeTrailer, EveryDefinitionPresent) {
  auto file = SkpFile::open(test::fixture("legacy_v6_synthetic.skp"));
  auto model = file.parse();
  std::set<std::string> names;
  for (const auto& [id, def] : model.definitions) names.insert(def.name);
  EXPECT_EQ(names, (std::set<std::string>{"InnerFrame", "Frame", "Panel", "Post"}));
}

TEST(LegacyV6AttributeTrailer, NestedGroupAttributeDictionarySurvives) {
  // The nested CGroup (InnerFrame, inside Panel) is exactly the kind of
  // record the phantom trailer corrupted - assert its own attribute
  // dictionary round-trips correctly, not just that parsing didn't crash.
  auto file = SkpFile::open(test::fixture("legacy_v6_synthetic.skp"));
  auto model = file.parse();
  const Definition* panel = nullptr;
  for (const auto& [id, def] : model.definitions) {
    if (def.name == "Panel") panel = &def;
  }
  ASSERT_NE(panel, nullptr);
  ASSERT_EQ(panel->instances.size(), 1u);
  const auto& inner = panel->instances[0];
  EXPECT_EQ(inner.name, "InnerFrame");

  const auto& dict = inner.attribute_dictionaries.at("fbd-info");
  EXPECT_EQ(dict.at("role").kind, ParsedAttribute::Kind::String);
  EXPECT_EQ(dict.at("role").text, "bracing");
  EXPECT_EQ(dict.at("count").kind, ParsedAttribute::Kind::Integer);
  EXPECT_EQ(dict.at("count").integer, 3);
}

}  // namespace
}  // namespace openskp
