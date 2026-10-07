#include <cmath>
#include <gtest/gtest.h>
#include <set>

#include <openskp/openskp.hpp>

#include "test_helpers.hpp"

namespace openskp {
namespace {

// Port of openskp#284's "V3" fix (Python #407) to C++ (openskp#410): SketchUp
// 3 files parse.
//
// Fixture: fixtures/legacy_v3_synthetic.skp - the same small synthetic model
// as legacy_v6_synthetic.skp (4 component definitions - InnerFrame, Frame,
// Panel, Post; InnerFrame also placed as a nested group inside Panel; 10 root
// instances; a custom "Roof" layer; Frame's face painted flat red) built with
// the OpenSKP writer and saved down to SketchUp 3 through a real SketchUp
// version-downgrade export. SketchUp's default template rides along: ~35
// materials, 10 of them with embedded JPEG textures. No private content.
//
// A V3 file differs from every later era in a dozen small ways (see
// `kFirstV4` in legacy.cpp). Parsing without an exception was NOT enough to
// call it fixed - an intermediate state "succeeded" with one definition of
// four, a phantom layer and no root instances - so these tests assert the
// parsed structure and cross-check it against the V6 file from the same
// source. The layout is calibrated on this one file: draw-block bytes
// (hidden/soft/smooth) are undecoded and reported unset.

std::map<std::string, const Definition*> by_name(const SkpModel& model) {
  std::map<std::string, const Definition*> out;
  for (const auto& [id, def] : model.definitions) out[def.name] = &def;
  return out;
}

std::string def_name(const SkpModel& model, const Instance& inst) {
  return model.definitions.at(*inst.ref_idx).name;
}

TEST(LegacyV3Format, ParsesAndReportsItsVersion) {
  auto model = SkpFile::open(test::fixture("legacy_v3_synthetic.skp")).parse();
  EXPECT_EQ(model.version, "{3.0.1}");
}

TEST(LegacyV3Format, EveryDefinitionIsPresentWithItsGeometry) {
  auto model = SkpFile::open(test::fixture("legacy_v3_synthetic.skp")).parse();
  auto defs = by_name(model);
  std::set<std::string> names;
  for (const auto& [name, def] : defs) {
    names.insert(name);
    EXPECT_EQ(def->faces.size(), 1u) << name;
    EXPECT_EQ(def->edges.size(), 4u) << name;
    EXPECT_EQ(def->vertices.size(), 4u) << name;
  }
  EXPECT_EQ(names, (std::set<std::string>{"InnerFrame", "Frame", "Panel", "Post"}));
  // Frame is a 20x20 square at the origin
  std::set<double> xs, ys;
  for (const auto& [id, v] : defs.at("Frame")->vertices) {
    xs.insert(v.x);
    ys.insert(v.y);
  }
  EXPECT_EQ(xs, (std::set<double>{0.0, 20.0}));
  EXPECT_EQ(ys, (std::set<double>{0.0, 20.0}));
}

TEST(LegacyV3Format, PlacesEveryRootInstance) {
  auto model = SkpFile::open(test::fixture("legacy_v3_synthetic.skp")).parse();
  const auto& insts = model.root().instances;
  ASSERT_EQ(insts.size(), 10u);
  const std::vector<std::string> expected{"Frame", "Panel", "Post",  "Frame", "Panel",
                                          "Post",  "Frame", "Panel", "Post",  "Frame"};
  for (size_t i = 0; i < insts.size(); ++i) EXPECT_EQ(def_name(model, insts[i]), expected[i]) << i;
}

TEST(LegacyV3Format, NestedGroupIsPlacedInsidePanel) {
  auto model = SkpFile::open(test::fixture("legacy_v3_synthetic.skp")).parse();
  const auto* panel = by_name(model).at("Panel");
  ASSERT_EQ(panel->instances.size(), 1u);
  EXPECT_EQ(def_name(model, panel->instances[0]), "InnerFrame");
}

TEST(LegacyV3Format, FaceMaterialComesFromTheFacesOwnPointer) {
  // SketchUp 3's face opens with its FRONT MATERIAL pointer, not an
  // attribute container: Frame's face was painted red, the others not.
  auto model = SkpFile::open(test::fixture("legacy_v3_synthetic.skp")).parse();
  auto mats = model.materials_by_id();
  auto defs = by_name(model);
  const auto& painted = defs.at("Frame")->faces.begin()->second;
  ASSERT_TRUE(painted.material_id.has_value());
  const Material* red = mats.at(*painted.material_id);
  EXPECT_EQ(red->name, "Red");
  EXPECT_EQ(red->color[0], 255);
  EXPECT_EQ(red->color[1], 0);
  EXPECT_EQ(red->color[2], 0);
  for (const char* name : {"Panel", "Post", "InnerFrame"})
    EXPECT_FALSE(defs.at(name)->faces.begin()->second.material_id.has_value()) << name;
}

TEST(LegacyV3Format, NoFaceIsSpuriouslyHidden) {
  auto model = SkpFile::open(test::fixture("legacy_v3_synthetic.skp")).parse();
  for (const auto& [id, def] : model.definitions)
    for (const auto& [fid, face] : def.faces) EXPECT_FALSE(face.hidden);
}

TEST(LegacyV3Format, KeepsTheCustomLayer) {
  auto model = SkpFile::open(test::fixture("legacy_v3_synthetic.skp")).parse();
  std::set<std::string> names;
  for (const auto& l : model.layers) names.insert(l.name);
  EXPECT_TRUE(names.count("Layer0"));
  EXPECT_TRUE(names.count("Roof"));
}

TEST(LegacyV3Format, EmbeddedTemplateTexturesAreReadAsInlineImages) {
  auto model = SkpFile::open(test::fixture("legacy_v3_synthetic.skp")).parse();
  EXPECT_EQ(model.materials.size(), 35u);
  int textured = 0;
  for (const auto& m : model.materials) {
    if (!m.texture) continue;
    ++textured;
    ASSERT_TRUE(m.texture->data.has_value());
    const auto& d = *m.texture->data;
    ASSERT_GE(d.size(), 5u);
    // a real JPEG, not skipped bytes - ends exactly at its end-of-image marker
    EXPECT_EQ(d[0], 0xff);
    EXPECT_EQ(d[1], 0xd8);
    EXPECT_EQ(d[2], 0xff);
    EXPECT_EQ(d[d.size() - 2], 0xff);
    EXPECT_EQ(d[d.size() - 1], 0xd9);
  }
  EXPECT_EQ(textured, 10);
}

TEST(LegacyV3Format, MatchesSketchUp6BuiltFromTheSameSource) {
  auto v3 = SkpFile::open(test::fixture("legacy_v3_synthetic.skp")).parse();
  auto v6 = SkpFile::open(test::fixture("legacy_v6_synthetic.skp")).parse();
  auto d3 = by_name(v3);
  auto d6 = by_name(v6);
  ASSERT_EQ(d3.size(), d6.size());
  for (const auto& [name, a] : d3) {
    const auto* b = d6.at(name);
    EXPECT_EQ(a->faces.size(), b->faces.size()) << name;
    EXPECT_EQ(a->edges.size(), b->edges.size()) << name;
    EXPECT_EQ(a->vertices.size(), b->vertices.size()) << name;
  }
  ASSERT_EQ(v3.root().instances.size(), v6.root().instances.size());
  for (size_t i = 0; i < v3.root().instances.size(); ++i) {
    const auto& a = v3.root().instances[i];
    const auto& b = v6.root().instances[i];
    EXPECT_EQ(def_name(v3, a), def_name(v6, b));
    ASSERT_EQ(a.matrix.size(), b.matrix.size());
    for (size_t k = 0; k < a.matrix.size(); ++k) EXPECT_NEAR(a.matrix[k], b.matrix[k], 1e-9);
  }
}

}  // namespace
}  // namespace openskp
