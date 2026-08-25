#include <algorithm>
#include <cmath>
#include <functional>
#include <gtest/gtest.h>
#include <limits>
#include <set>
#include <unordered_map>
#include <unordered_set>

#include <openskp/openskp.hpp>

#include "test_helpers.hpp"

namespace openskp {
namespace {

// Real-file regression test for SketchUp 2020 (v20) classic .skp files.
//
// Fixture: fixtures/gondola_v20.skp - a retail gondola display authored in
// SketchUp 2020 (v20.1.235, ~755 KB), shared via the TypeScript port's
// PR #155.
//
// Before the v20 layout fixes, this file threw "implausible def entities"
// from the legacy walk: v20 writes records the v17 layout does not have,
// which left the reader a few bytes short and made it read garbage where a
// count was expected. The existing v17 fixture (capilla_quiroz_v17.skp) has
// only one layer and never exercised any of these paths, so the divergence
// went unnoticed.
//
// Every count below was read off this exact file after the fix and
// sanity-checked for plausibility (bounding box in metres, definitions
// carrying real geometry, instances actually placed in the scene) - a parse
// that "succeeds" while silently dropping placements would still be a bug,
// so the instance counts matter as much as the parse not throwing.

TEST(LegacyV20, ParsesRealV20FileThatPreviouslyThrew) {
  auto file = SkpFile::open(test::fixture("gondola_v20.skp"));
  auto model = file.parse();

  EXPECT_EQ(model.version, "{20.1.235}");
  // Legacy files carry no meta/meta.dat, same as v17.
  EXPECT_FALSE(model.units.has_value());

  EXPECT_EQ(model.definitions.size(), 20);
  EXPECT_EQ(model.materials.size(), 24);

  // v20 interleaves a null object-ref after EACH layer record; the count
  // is the number of REAL layers. The old reader counted the separators
  // as items and dropped every layer after the first - this fixture
  // really does carry "Gondulas Laterais" (visible in SketchUp), which
  // the previous assertion enshrined as missing. Nulls must still never
  // reach model.layers. Order-independent: model.layers is built from
  // layer_colors, a std::map keyed by name (sorted alphabetically, not
  // file order) - a pre-existing, unrelated characteristic of this
  // port's layer-building code (model.cpp), not something this fixture's
  // fix controls.
  ASSERT_EQ(model.layers.size(), 2);
  std::set<std::string> names;
  for (const auto& layer : model.layers) names.insert(layer.name);
  EXPECT_EQ(names, (std::set<std::string>{"Layer0", "Gondulas Laterais"}));

  // real geometry, not an empty shell
  std::size_t faces = 0, edges = 0, vertices = 0;
  for (const auto& [id, def] : model.definitions) {
    faces += def.faces.size();
    edges += def.edges.size();
    vertices += def.vertices.size();
  }
  EXPECT_EQ(faces, 1887u);
  EXPECT_EQ(edges, 9174u);
  EXPECT_EQ(vertices, 6543u);
}

TEST(LegacyV20, PlacesEveryRootInstance) {
  auto file = SkpFile::open(test::fixture("gondola_v20.skp"));
  auto model = file.parse();
  // 23 root-level placements: the definitions above are useless if the
  // instances that position them in the model are lost, which is exactly
  // what a subtly misaligned walk produces - a file that parses into an
  // almost-empty scene instead of throwing.
  EXPECT_EQ(model.root().instances.size(), 23u);

  auto file2 = SkpFile::open(test::fixture("gondola_v20.skp"));
  auto scene = file2.build_scene();
  EXPECT_EQ(scene.scene_hierarchy.children.size(), 23u);
  EXPECT_EQ(scene.glb_primitives.size(), 201u);
  EXPECT_EQ(scene.mesh_index.size(), 201u);
  EXPECT_FALSE(scene.gltf_materials.empty());
}

TEST(LegacyV20, ResolvesPlacedInstancesToDefinitionsThatCarryGeometry) {
  // Guards the failure mode a zero entity count produces: the definitions
  // an instance points at come back empty, so the file parses into a scene
  // of correctly-positioned but invisible groups. Counting definitions or
  // instances alone does not catch it - the two have to be checked
  // together.
  auto file = SkpFile::open(test::fixture("gondola_v20.skp"));
  auto model = file.parse();

  std::unordered_set<std::uint64_t> referenced;
  for (const auto& inst : model.root().instances) {
    if (inst.ref_idx) referenced.insert(*inst.ref_idx);
  }
  for (const auto& [id, def] : model.definitions) {
    for (const auto& inst : def.instances) {
      if (inst.ref_idx) referenced.insert(*inst.ref_idx);
    }
  }

  std::unordered_map<std::uint64_t, bool> memo;
  std::unordered_set<std::uint64_t> in_progress;
  std::function<bool(std::uint64_t)> carries_geometry = [&](std::uint64_t def_id) -> bool {
    auto cached = memo.find(def_id);
    if (cached != memo.end()) return cached->second;
    if (in_progress.count(def_id)) return false;  // reference cycle
    in_progress.insert(def_id);
    auto it = model.definitions.find(def_id);
    bool result = it != model.definitions.end() &&
                  (!it->second.faces.empty() ||
                   std::any_of(it->second.instances.begin(), it->second.instances.end(),
                               [&](const Instance& child) {
                                 return child.ref_idx && carries_geometry(*child.ref_idx);
                               }));
    in_progress.erase(def_id);
    memo[def_id] = result;
    return result;
  };

  for (auto def_id : referenced) {
    EXPECT_TRUE(carries_geometry(def_id)) << "definition " << def_id << " carries no geometry";
  }
}

TEST(LegacyV20, BakesGeometryAtAPlausibleRealWorldScale) {
  auto file = SkpFile::open(test::fixture("gondola_v20.skp"));
  auto scene = file.build_scene();
  double min[3] = {std::numeric_limits<double>::infinity(), std::numeric_limits<double>::infinity(),
                   std::numeric_limits<double>::infinity()};
  double max[3] = {-std::numeric_limits<double>::infinity(),
                   -std::numeric_limits<double>::infinity(),
                   -std::numeric_limits<double>::infinity()};
  for (const auto& prim : scene.glb_primitives) {
    for (std::size_t i = 0; i + 2 < prim.positions.size(); i += 3) {
      for (int a = 0; a < 3; ++a) {
        double v = prim.positions[i + a];
        if (v < min[a]) min[a] = v;
        if (v > max[a]) max[a] = v;
      }
    }
  }
  // a shop gondola display: metres, not the 1e3-off or degenerate box a
  // misaligned read produces
  EXPECT_NEAR(max[0] - min[0], 3.82, 0.1);
  EXPECT_NEAR(max[1] - min[1], 3.14, 0.1);
  EXPECT_NEAR(max[2] - min[2], 4.82, 0.1);
}

TEST(LegacyV20, GivesEveryBakedPrimitiveValidUvCoordinates) {
  auto file = SkpFile::open(test::fixture("gondola_v20.skp"));
  auto scene = file.build_scene();
  ASSERT_FALSE(scene.glb_primitives.empty());
  for (const auto& prim : scene.glb_primitives) {
    std::size_t n_verts = prim.positions.size() / 3;
    EXPECT_EQ(prim.uvs.size(), n_verts * 2);
    for (float uv : prim.uvs) {
      EXPECT_TRUE(std::isfinite(uv));
    }
  }
}

}  // namespace
}  // namespace openskp
