#include <algorithm>
#include <gtest/gtest.h>
#include <map>

#include <openskp/openskp.hpp>

#include "test_helpers.hpp"

namespace openskp {
namespace {

// Definition::is_group: whether SketchUp made a definition for a group or a
// component (openskp#446).
//
// Fixtures: nesting_<version>.skp - the group "Outer" holds an instance of the
// component "Shelf", which holds the group "Inner", a face at each level. Built
// and saved through the installed SketchUp library as SketchUp 6, 2013, 2017,
// 2020 and 2021 (the last is the VFF container, the rest the classic one). The
// library reports two group definitions and one component definition for each
// (SUModelGetNumGroupDefinitions / SUModelGetNumComponentDefinitions). No
// private content.

const char* const kNames[] = {"nesting_v6.skp", "nesting_2013.skp", "nesting_2017.skp",
                              "nesting_2020.skp", "nesting_2021.skp"};

TEST(DefinitionIsGroup, GroupsAndTheComponentAreToldApart) {
  for (const char* name : kNames) {
    SCOPED_TRACE(name);
    const auto model = SkpFile::open(test::fixture(name)).parse();
    int groups = 0;
    int components = 0;
    for (const auto& d : model.definitions) (d.second.is_group ? groups : components)++;
    EXPECT_EQ(groups, 2);
    EXPECT_EQ(components, 1);
  }
}

TEST(DefinitionIsGroup, TheFlagFollowsTheNesting) {
  for (const char* name : kNames) {
    SCOPED_TRACE(name);
    const auto model = SkpFile::open(test::fixture(name)).parse();
    const auto& definitions = model.definitions;
    auto of = [&](const Instance& i) -> const Definition& { return definitions.at(*i.ref_idx); };

    ASSERT_EQ(model.root().instances.size(), 1u);
    const auto& outer = model.root().instances[0];
    EXPECT_EQ(outer.name, "Outer");
    EXPECT_TRUE(of(outer).is_group);

    ASSERT_EQ(of(outer).instances.size(), 1u);
    const auto& shelf = of(outer).instances[0];
    EXPECT_EQ(of(shelf).name, "Shelf");
    EXPECT_FALSE(of(shelf).is_group);

    ASSERT_EQ(of(shelf).instances.size(), 1u);
    const auto& inner = of(shelf).instances[0];
    EXPECT_EQ(inner.name, "Inner");
    EXPECT_TRUE(of(inner).is_group);
  }
}

TEST(DefinitionIsGroup, ADefinitionPlacedOnceIsNotTakenForAGroup) {
  // "Shelf" is placed exactly once, which is what the old guess got wrong
  for (const char* name : kNames) {
    SCOPED_TRACE(name);
    const auto model = SkpFile::open(test::fixture(name)).parse();
    std::map<EntityId, int> placements;
    for (const auto& d : model.definitions)
      for (const auto& i : d.second.instances) ++placements[*i.ref_idx];
    for (const auto& i : model.root().instances) ++placements[*i.ref_idx];
    const auto shelf = std::find_if(model.definitions.begin(), model.definitions.end(),
                                    [](const auto& kv) { return kv.second.name == "Shelf"; });
    ASSERT_NE(shelf, model.definitions.end());
    EXPECT_EQ(placements[shelf->first], 1);
    EXPECT_FALSE(shelf->second.is_group);
  }
}

}  // namespace
}  // namespace openskp
