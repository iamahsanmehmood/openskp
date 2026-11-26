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
// the OpenSKP writer and re-saved at SketchUp 3 through the installed
// SketchUp library. It is small (8 KB) and carries no default-template
// materials or images. No private content.
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

// A V3 file with ONE material cannot use the two-material bootstrap; the
// slot-base probe used to read a layer count V3 does not write and rejected
// every such file ("implausible layer count in base probe"). Fixture: one
// 20x20 face painted with "Checker", an 8x8 PNG generated by this project (no
// third-party image), saved down to SketchUp 3. 4 KB, no default template.
TEST(LegacyV3Format, SingleMaterialParsesItsInlinePngAndPaintedFace) {
  auto model = SkpFile::open(test::fixture("legacy_v3_textured.skp")).parse();
  EXPECT_EQ(model.version, "{3.0.1}");
  ASSERT_EQ(model.materials.size(), 1u);
  EXPECT_EQ(model.materials[0].name, "Checker");
  ASSERT_TRUE(model.materials[0].texture && model.materials[0].texture->data);
  const auto& d = *model.materials[0].texture->data;
  const ByteBuffer png_head{0x89, 'P', 'N', 'G', 0x0d, 0x0a, 0x1a, 0x0a};
  const ByteBuffer png_tail{'I', 'E', 'N', 'D', 0xae, 0x42, 0x60, 0x82};
  ASSERT_GE(d.size(), 16u);
  EXPECT_EQ(ByteBuffer(d.begin(), d.begin() + 8), png_head);
  EXPECT_EQ(ByteBuffer(d.end() - 8, d.end()), png_tail);
  size_t faces = model.root().faces.size();
  const Face* face = faces ? &model.root().faces.begin()->second : nullptr;
  for (const auto& [id, def] : model.definitions) {
    faces += def.faces.size();
    if (!face && !def.faces.empty()) face = &def.faces.begin()->second;
  }
  ASSERT_EQ(faces, 1u);
  EXPECT_EQ(face->material_id, model.materials[0].id);
}

TEST(LegacyV3Format, CurvesTextAndGuideLineParseWithoutLoopDesync) {
  auto model = SkpFile::open(test::fixture("legacy_v3_curves_and_text.skp")).parse();
  EXPECT_EQ(model.version, "{3.0.1}");
  EXPECT_EQ(model.unread_root_entities, 0u);
  auto defs = by_name(model);
  auto mats = model.materials_by_id();
  ASSERT_EQ(defs.size(), 2u);
  ASSERT_TRUE(defs.count("CurvedPanel"));
  ASSERT_TRUE(defs.count("ArcDisk"));

  const auto* panel = defs.at("CurvedPanel");
  EXPECT_EQ(panel->faces.size(), 1u);
  EXPECT_EQ(panel->edges.size(), 6u);
  EXPECT_EQ(panel->vertices.size(), 6u);
  EXPECT_TRUE(panel->arcs.empty());
  std::set<EntityId> panel_curves;
  size_t panel_curved_edges = 0;
  for (const auto& [id, edge] : panel->edges) {
    if (edge.curve_id) {
      ++panel_curved_edges;
      panel_curves.insert(*edge.curve_id);
    }
  }
  EXPECT_EQ(panel_curved_edges, 4u);
  EXPECT_EQ(panel_curves.size(), 1u);
  ASSERT_TRUE(panel->faces.begin()->second.material_id.has_value());
  EXPECT_EQ(mats.at(*panel->faces.begin()->second.material_id)->name, "Red");

  const auto* disk = defs.at("ArcDisk");
  EXPECT_EQ(disk->faces.size(), 1u);
  EXPECT_EQ(disk->edges.size(), 8u);
  EXPECT_EQ(disk->vertices.size(), 8u);
  ASSERT_EQ(disk->arcs.size(), 1u);
  const auto& disk_arc = disk->arcs.begin()->second;
  EXPECT_EQ(disk_arc.center, (Vec3{10.0, 10.0, 0.0}));
  EXPECT_NEAR(disk_arc.radius(), 8.0, 1e-9);
  EXPECT_TRUE(disk_arc.is_full_circle());
  ASSERT_TRUE(disk->faces.begin()->second.material_id.has_value());
  EXPECT_EQ(mats.at(*disk->faces.begin()->second.material_id)->name, "Blue");

  ASSERT_EQ(model.root().instances.size(), 2u);
  EXPECT_EQ(def_name(model, model.root().instances[0]), "CurvedPanel");
  EXPECT_EQ(def_name(model, model.root().instances[1]), "ArcDisk");
  EXPECT_DOUBLE_EQ(model.root().instances[0].matrix[9], 0.0);
  EXPECT_DOUBLE_EQ(model.root().instances[1].matrix[9], 40.0);
  EXPECT_FALSE(model.root().instances[0].material_id.has_value());
  ASSERT_TRUE(model.root().instances[1].material_id.has_value());
  EXPECT_EQ(mats.at(*model.root().instances[1].material_id)->name, "Red");

  EXPECT_TRUE(model.root().faces.empty());
  EXPECT_EQ(model.root().edges.size(), 9u);
  EXPECT_EQ(model.root().vertices.size(), 11u);
  ASSERT_EQ(model.root().arcs.size(), 1u);
  const auto& root_arc = model.root().arcs.begin()->second;
  EXPECT_EQ(root_arc.center, (Vec3{0.0, 40.0, 0.0}));
  EXPECT_NEAR(root_arc.radius(), 10.0, 1e-9);
  EXPECT_FALSE(root_arc.is_full_circle());
  auto runs = model.root().loose_edge_runs();
  ASSERT_EQ(runs.size(), 2u);
  EXPECT_EQ(runs[0].edge_ids.size(), 6u);
  EXPECT_EQ(runs[0].vertex_ids.size(), 7u);
  EXPECT_FALSE(runs[0].closed);
  EXPECT_EQ(runs[1].edge_ids.size(), 3u);
  EXPECT_EQ(runs[1].vertex_ids.size(), 4u);
  EXPECT_FALSE(runs[1].closed);

  ASSERT_EQ(model.root().texts.size(), 1u);
  const auto& txt = model.root().texts[0];
  EXPECT_EQ(txt.text, "Label A");
  EXPECT_EQ(txt.point, (std::optional<Vec3>{Vec3{15.0, 15.0, 0.0}}));
  EXPECT_EQ(txt.label_point, (std::optional<Vec3>{Vec3{20.0, 20.0, 10.0}}));
  EXPECT_EQ(txt.screen_position,
            (std::optional<std::array<double, 2>>{std::array<double, 2>{0.0, 0.0}}));

  ASSERT_EQ(model.root().construction_lines.size(), 1u);
  const auto& cl = model.root().construction_lines[0];
  EXPECT_EQ(cl.point, (Vec3{0.0, 0.0, 0.0}));
  EXPECT_EQ(cl.direction, (Vec3{1.0, 0.0, 0.0}));
  EXPECT_FALSE(cl.start.has_value());
  EXPECT_FALSE(cl.end.has_value());
}

TEST(LegacyV3Format, ZeroMaterialsParsesViaLayerHeaderOffset) {
  auto model = SkpFile::open(test::fixture("legacy_v3_no_materials.skp")).parse();
  EXPECT_EQ(model.version, "{3.0.1}");
  EXPECT_EQ(model.unread_root_entities, 0u);
  EXPECT_TRUE(model.materials.empty());
  bool has_layer0 = false;
  for (const auto& l : model.layers) {
    if (l.name == "Layer0") has_layer0 = true;
  }
  EXPECT_TRUE(has_layer0);
  EXPECT_EQ(model.root().faces.size(), 1u);
  EXPECT_EQ(model.root().edges.size(), 4u);
  EXPECT_EQ(model.root().vertices.size(), 4u);
}

}  // namespace
}  // namespace openskp
