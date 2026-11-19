#include <algorithm>
#include <gtest/gtest.h>
#include <map>
#include <set>
#include <string>

#include <openskp/openskp.hpp>

#include "test_helpers.hpp"

namespace openskp {
namespace {

// The entities a scene hides (openskp#472).
//
// Fixtures: scene_hidden_entities_2017/2019/2020/2024.skp - one model built and saved through
// SketchUp 2024's own library (SUSceneSetDrawingElementHidden) as SketchUp 2017, 2019, 2020 and
// 2024 files. A ground box, a definition "Part" placed as "Part A" (persistent id 70) and "Part B"
// (71) at model level, a group "Crate" (73) holding a third placement "Part C" (100), and six
// scenes, each saving its camera:
//
//   "All"             saves hidden objects, hides none
//   "No B, no crate"  hides Part B and Crate
//   "No A, no crate"  hides Part A and Crate
//   "No C"            hides Part C, inside the group
//   "Edge"            hides one of the box's edges (hidden geometry; persistent id 17)
//   "Not saved"       saves no hidden objects
//
// SketchUp's own SUSceneGetHiddenEntities reads the same sets back from every file, except that
// the 2017 and 2019 files keep no nested entity: SketchUp itself reads "No C" as hiding nothing
// there. No private content.

std::set<EntityId> hidden(const Page& page) {
  return {page.hidden_entity_ids.begin(), page.hidden_entity_ids.end()};
}

std::map<std::string, EntityId> placement_ids(const SkpModel& model) {
  std::map<std::string, EntityId> ids;
  auto add = [&](const Definition& d) {
    for (const auto& i : d.instances)
      if (i.id) ids[i.name] = *i.id;
  };
  add(model.root());
  for (const auto& [key, d] : model.definitions) add(d);
  return ids;
}

class SceneHiddenEntities : public ::testing::TestWithParam<const char*> {};

TEST_P(SceneHiddenEntities, EachSceneListsWhatItHides) {
  const auto model = SkpFile::open(test::fixture(GetParam())).parse();
  ASSERT_EQ(model.pages.size(), 6u);
  EXPECT_EQ(model.pages[0].name, "All");
  EXPECT_TRUE(model.pages[0].hidden_entity_ids.empty());
  EXPECT_EQ(model.pages[1].name, "No B, no crate");
  EXPECT_EQ(hidden(model.pages[1]), (std::set<EntityId>{71, 73}));
  EXPECT_EQ(model.pages[2].name, "No A, no crate");
  EXPECT_EQ(hidden(model.pages[2]), (std::set<EntityId>{70, 73}));
  EXPECT_EQ(model.pages[4].name, "Edge");
  EXPECT_EQ(hidden(model.pages[4]), (std::set<EntityId>{17}));
  EXPECT_EQ(model.pages[5].name, "Not saved");
  EXPECT_TRUE(model.pages[5].hidden_entity_ids.empty());
}

TEST_P(SceneHiddenEntities, TheIdsResolveThroughThePlacements) {
  const auto model = SkpFile::open(test::fixture(GetParam())).parse();
  ASSERT_EQ(model.pages.size(), 6u);
  const auto ids = placement_ids(model);
  ASSERT_EQ(ids.count("Part A"), 1u);
  ASSERT_EQ(ids.count("Part B"), 1u);
  ASSERT_EQ(ids.count("Crate"), 1u);
  EXPECT_EQ(hidden(model.pages[1]), (std::set<EntityId>{ids.at("Part B"), ids.at("Crate")}));
  EXPECT_EQ(hidden(model.pages[2]), (std::set<EntityId>{ids.at("Part A"), ids.at("Crate")}));
}

TEST_P(SceneHiddenEntities, TheHiddenEdgeIsTheBoxEdge) {
  const auto model = SkpFile::open(test::fixture(GetParam())).parse();
  ASSERT_EQ(model.pages.size(), 6u);
  const auto& edges = model.root().edges;
  const bool found = std::any_of(edges.begin(), edges.end(),
                                 [](const auto& e) { return e.second.persistent_id == 17; });
  EXPECT_TRUE(found);
}

INSTANTIATE_TEST_SUITE_P(Files, SceneHiddenEntities,
                         ::testing::Values("scene_hidden_entities_2017.skp",
                                           "scene_hidden_entities_2019.skp",
                                           "scene_hidden_entities_2020.skp",
                                           "scene_hidden_entities_2024.skp"));

TEST(SceneHiddenEntitiesNested, A2020OrNewerFileKeepsTheNestedPlacement) {
  for (const char* name : {"scene_hidden_entities_2020.skp", "scene_hidden_entities_2024.skp"}) {
    SCOPED_TRACE(name);
    const auto model = SkpFile::open(test::fixture(name)).parse();
    ASSERT_EQ(model.pages.size(), 6u);
    EXPECT_EQ(model.pages[3].name, "No C");
    EXPECT_EQ(hidden(model.pages[3]), (std::set<EntityId>{100}));
    EXPECT_EQ(placement_ids(model).at("Part C"), 100u);
  }
}

TEST(SceneHiddenEntitiesNested, A2017FileKeepsNoneLikeSketchUp) {
  const auto model = SkpFile::open(test::fixture("scene_hidden_entities_2017.skp")).parse();
  ASSERT_EQ(model.pages.size(), 6u);
  EXPECT_TRUE(model.pages[3].hidden_entity_ids.empty());
}

TEST(SceneHiddenEntitiesGondola, SketchUpsOwnSaveListsTheEdgesItsSceneHides) {
  // gondola_v20.skp's one scene hides twelve entities: edges hidden as geometry in that scene.
  const auto model = SkpFile::open(test::fixture("gondola_v20.skp")).parse();
  ASSERT_EQ(model.pages.size(), 1u);
  EXPECT_EQ(model.pages[0].hidden_entity_ids.size(), 12u);
}

}  // namespace
}  // namespace openskp
