#include <algorithm>
#include <cmath>
#include <gtest/gtest.h>
#include <map>
#include <set>
#include <string>
#include <vector>

#include <openskp/create.hpp>
#include <openskp/openskp.hpp>

#include "curves.hpp"
#include "test_helpers.hpp"

namespace openskp {
namespace {

constexpr double kRadiusIn = 40.0;
const double kPi = std::acos(-1.0);
const std::vector<double> kIdentity{1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1};

RawArcFrame make_frame(double radius = kRadiusIn, double sweep = std::acos(-1.0) / 2.0) {
  RawArcFrame f;
  f.center = {0.0, 0.0, 0.0};
  f.normal = {0.0, 0.0, 1.0};
  f.x_axis = {radius, 0.0, 0.0};
  f.y_axis = {0.0, radius, 0.0};
  f.start_angle = 0.0;
  f.end_angle = sweep;
  return f;
}

std::array<double, 3> arc_point(double t, double radius = kRadiusIn) {
  return {radius * std::cos(t) * kCurvesInchesToM, 0.0, -radius * std::sin(t) * kCurvesInchesToM};
}

TEST(LooseEdgeCurves, ChainLooseEdgesRespectsCurvesAndExcludesFaceEdges) {
  GeometryBuilder b1;
  b1.edges[1] = {10, 20};
  b1.edges[2] = {20, 30};
  b1.edge_curves[1] = 100;
  b1.edge_curves[2] = 101;
  auto runs1 = chain_loose_edges(b1);
  ASSERT_EQ(runs1.size(), 2u);
  EXPECT_EQ(runs1[0].vertex_ids.size(), 2u);
  EXPECT_EQ(runs1[1].vertex_ids.size(), 2u);

  GeometryBuilder open_b;
  open_b.edges[1] = {10, 20};
  open_b.edges[2] = {20, 30};
  open_b.edges[3] = {30, 40};
  open_b.edge_curves[2] = 50;
  open_b.edge_curves[3] = 50;
  RawFace face;
  face.loops.push_back({CoEdge{1, 1}});
  open_b.faces[9] = face;
  auto open_runs = chain_loose_edges(open_b);
  ASSERT_EQ(open_runs.size(), 1u);
  EXPECT_EQ(open_runs[0].edge_ids, (std::vector<EntityId>{2, 3}));
  EXPECT_EQ(open_runs[0].vertex_ids, (std::vector<EntityId>{20, 30, 40}));
  EXPECT_FALSE(open_runs[0].closed);

  GeometryBuilder closed_b;
  closed_b.edges[1] = {10, 20};
  closed_b.edges[2] = {20, 30};
  closed_b.edges[3] = {30, 10};
  closed_b.edge_curves[1] = 77;
  closed_b.edge_curves[2] = 77;
  closed_b.edge_curves[3] = 77;
  auto closed_runs = chain_loose_edges(closed_b);
  ASSERT_EQ(closed_runs.size(), 1u);
  EXPECT_TRUE(closed_runs[0].closed);
  EXPECT_EQ(closed_runs[0].vertex_ids.size(), 3u);
}

TEST(LooseEdgeCurves, SolveRunArcAcceptsCircularArcsAndRejectsInvalidOnes) {
  GeometryBuilder b;
  b.edge_curves[1] = 17;
  b.arc_curves[17] = make_frame();
  std::vector<std::array<double, 3>> pts{arc_point(0.0), arc_point(kPi / 8.0), arc_point(kPi / 4.0),
                                         arc_point(3.0 * kPi / 8.0), arc_point(kPi / 2.0)};
  auto arc = solve_run_arc(b, {1}, &kIdentity, pts, false);
  ASSERT_TRUE(arc.has_value());
  EXPECT_EQ(arc->segments, (std::vector<std::array<std::size_t, 3>>{{0, 1, 2}}));
  EXPECT_EQ(arc->points.size(), 3u);

  GeometryBuilder b4;
  for (EntityId i = 1; i <= 4; ++i) b4.edge_curves[i] = 17;
  b4.arc_curves[17] = make_frame(kRadiusIn, 2.0 * kPi);
  std::vector<std::array<double, 3>> pts4;
  for (int i = 0; i < 4; ++i) pts4.push_back(arc_point(2.0 * kPi * i / 4.0));
  auto arc4 = solve_run_arc(b4, {1, 2, 3, 4}, &kIdentity, pts4, true);
  ASSERT_TRUE(arc4.has_value());
  EXPECT_EQ(arc4->segments, (std::vector<std::array<std::size_t, 3>>{{0, 1, 2}, {2, 3, 0}}));

  b.edge_curves[2] = 17;
  EXPECT_FALSE(solve_run_arc(b, {1}, &kIdentity, {pts[0], pts[1]}, false).has_value());

  GeometryBuilder b_ellipse;
  b_ellipse.edge_curves[1] = 17;
  auto ef = make_frame();
  ef.y_axis = {0.0, kRadiusIn / 2.0, 0.0};
  b_ellipse.arc_curves[17] = ef;
  EXPECT_FALSE(solve_run_arc(b_ellipse, {1}, &kIdentity, {pts[0], pts[1]}, false).has_value());

  GeometryBuilder b_off;
  b_off.edge_curves[1] = 17;
  b_off.arc_curves[17] = make_frame();
  auto bad_pt = pts[1];
  bad_pt[1] += 0.01;
  EXPECT_FALSE(solve_run_arc(b_off, {1}, &kIdentity, {pts[0], bad_pt}, false).has_value());
}

TEST(LooseEdgeCurves, BuildSceneBakesLooseEdgeCurveSetsOnFixtures) {
  const auto capilla = SkpFile::open(test::fixture("capilla_quiroz_v17.skp"));
  const auto sc = capilla.build_scene();
  ASSERT_EQ(sc.curve_sets.size(), 20u);
  for (const auto& cs : sc.curve_sets) {
    EXPECT_EQ(cs.points_m.size(), 2u);
    EXPECT_FALSE(cs.closed);
    EXPECT_FALSE(cs.arc.has_value());
  }

  std::vector<std::string> root_names;
  for (const auto& cs : sc.curve_sets) {
    if (cs.path == "ROOT") {
      root_names.push_back(cs.name);
      EXPECT_TRUE(cs.name_is_generated);
    }
  }
  std::sort(root_names.begin(), root_names.end());
  EXPECT_EQ(root_names,
            (std::vector<std::string>{"Layer0_1", "Layer0_2", "Layer0_3", "Layer0_4", "Layer0_5"}));

  std::size_t inner_count = 0;
  for (const auto& cs : sc.curve_sets) {
    if (cs.path == "ROOT / puerta") {
      ++inner_count;
      EXPECT_EQ(cs.name, "puerta");
      EXPECT_EQ(cs.definition_name, "puerta");
      EXPECT_FALSE(cs.name_is_generated);
    }
  }
  EXPECT_EQ(inner_count, 15u);

  ParseOptions off_opts;
  off_opts.include_curve_sets = false;
  const auto sc_off = capilla.build_scene(off_opts);
  EXPECT_TRUE(sc_off.curve_sets.empty());
  EXPECT_EQ(sc_off.glb_primitives.size(), sc.glb_primitives.size());
  const auto inst_off = capilla.build_instanced_scene(off_opts);
  EXPECT_TRUE(inst_off.curve_resources.empty());

  const auto gondola_sc = SkpFile::open(test::fixture("gondola_v20.skp")).build_scene();
  std::set<std::string> gondola_layers;
  std::size_t gondulas_count = 0;
  for (const auto& cs : gondola_sc.curve_sets) {
    gondola_layers.insert(cs.layer);
    if (cs.layer == "Gondulas Laterais") ++gondulas_count;
  }
  EXPECT_EQ(gondola_layers, (std::set<std::string>{"Gondulas Laterais", "Layer0"}));
  EXPECT_EQ(gondulas_count, 57232u);

  const auto untitled_sc = SkpFile::open(test::fixture("Untitled.skp")).build_scene();
  ASSERT_EQ(untitled_sc.curve_sets.size(), 526u);
  std::map<std::string, std::size_t> per_layer;
  for (const auto& cs : untitled_sc.curve_sets) {
    EXPECT_GE(cs.points_m.size(), 2u);
    EXPECT_FALSE(cs.name_is_generated);
    EXPECT_FALSE(cs.closed);
    EXPECT_FALSE(cs.arc.has_value());
    ++per_layer[cs.layer];
  }
  const std::map<std::string, std::size_t> expected_layers{
      {"BottomPlate", 36}, {"HeaderPlate1", 6}, {"KingStud", 48},          {"Nog", 320},
      {"Stud", 86},        {"TopPlate", 28},    {"VerticalHeaderStud", 2},
  };
  EXPECT_EQ(per_layer, expected_layers);

  const auto arcs_sc = SkpFile::open(test::fixture("arcs_2021.skp")).build_scene();
  ASSERT_EQ(arcs_sc.curve_sets.size(), 1u);
  const auto& arc_cs = arcs_sc.curve_sets[0];
  EXPECT_EQ(arc_cs.name, "Layer0_1");
  EXPECT_EQ(arc_cs.layer, "Layer0");
  EXPECT_EQ(arc_cs.path, "ROOT");
  EXPECT_TRUE(arc_cs.name_is_generated);
  EXPECT_FALSE(arc_cs.closed);
  EXPECT_EQ(arc_cs.points_m.size(), 13u);
  ASSERT_TRUE(arc_cs.arc.has_value());
  EXPECT_EQ(arc_cs.arc->segments, (std::vector<std::array<std::size_t, 3>>{{0, 1, 2}}));
  for (int k = 0; k < 3; ++k) {
    EXPECT_NEAR(arc_cs.arc->points[0][k], arc_cs.points_m[0][k], 1e-5);
    EXPECT_NEAR(arc_cs.arc->points[2][k], arc_cs.points_m[12][k], 1e-5);
  }

  const auto coedge_sc =
      SkpFile::open(test::fixture("coedge_orientation_regression.skp")).build_scene();
  EXPECT_EQ(coedge_sc.curve_sets.size(), 1200u);
  std::vector<const CurveSetMetadata*> recovered_arcs;
  for (const auto& cs : coedge_sc.curve_sets) {
    if (cs.arc.has_value()) recovered_arcs.push_back(&cs);
  }
  EXPECT_EQ(recovered_arcs.size(), 7u);
  auto circle_it = std::find_if(recovered_arcs.begin(), recovered_arcs.end(),
                                [](const CurveSetMetadata* cs) { return cs->closed; });
  ASSERT_NE(circle_it, recovered_arcs.end());
  EXPECT_EQ((*circle_it)->name, "Gruppo#195");
  EXPECT_EQ((*circle_it)->points_m.size(), 24u);
  EXPECT_EQ((*circle_it)->arc->segments,
            (std::vector<std::array<std::size_t, 3>>{{0, 1, 2}, {2, 3, 0}}));
}

TEST(LooseEdgeCurves, LooseEdgeRunsMatchesInstancedSceneCurveResources) {
  const char* const kFixtures[] = {
      "SU_File.skp",
      "Untitled.skp",
      "capilla_quiroz_v17.skp",
      "gondola_v20.skp",
      "single_material_v17.skp",
  };
  for (const char* name : kFixtures) {
    SCOPED_TRACE(name);
    const auto skp = SkpFile::open(test::fixture(name));
    const auto model = skp.parse();
    const auto inst_scene = skp.build_instanced_scene();

    bool checked_any = false;
    for (const auto& res : inst_scene.curve_resources) {
      const Definition& def =
          res.definition_id ? model.definitions.at(*res.definition_id) : model.root();
      const auto runs = loose_edge_runs(def);
      ASSERT_EQ(runs.size(), res.curves.size());
      for (std::size_t i = 0; i < runs.size(); ++i) {
        EXPECT_EQ(runs[i].closed, res.curves[i].closed);
        ASSERT_EQ(runs[i].vertex_ids.size(), res.curves[i].points_m.size());
        for (std::size_t k = 0; k < runs[i].vertex_ids.size(); ++k) {
          const auto& v = def.vertices.at(runs[i].vertex_ids[k]);
          EXPECT_NEAR(round6(v.x * kCurvesInchesToM), res.curves[i].points_m[k][0], 1e-5);
          EXPECT_NEAR(round6(v.z * kCurvesInchesToM), res.curves[i].points_m[k][1], 1e-5);
          EXPECT_NEAR(round6(-v.y * kCurvesInchesToM), res.curves[i].points_m[k][2], 1e-5);
        }
      }
      checked_any = true;
    }

    if (!checked_any) {
      EXPECT_TRUE(model.root().loose_edge_runs().empty());
      for (const auto& [did, def] : model.definitions) {
        EXPECT_TRUE(def.loose_edge_runs().empty());
      }
    }
  }
}

TEST(LooseEdgeCurves, ParseOptionsPositionalInitializerPreservesOriginalTexturesFieldOrder) {
  ParseOptions opts{{}, {}, "tag", true};
  EXPECT_EQ(opts.unpainted_faces, "tag");
  EXPECT_TRUE(opts.original_textures);
  EXPECT_TRUE(opts.include_curve_sets);
}

TEST(LooseEdgeCurves, Round6MatchesPythonAcrossSignedSixteenthInchMultiples) {
  EXPECT_DOUBLE_EQ(round6(-1199.8125 * kCurvesInchesToM), -30.475237);
  EXPECT_DOUBLE_EQ(round6(0.0078125), 0.007812);
  EXPECT_DOUBLE_EQ(round6(0.0234375), 0.023438);
  EXPECT_TRUE(std::signbit(round6(-4e-7)));

  std::uint32_t h = 0x811c9dc5u;
  for (int i = -19200; i <= 19200; ++i) {
    const double v = round6((static_cast<double>(i) / 16.0) * kCurvesInchesToM);
    const auto um = static_cast<std::uint32_t>(static_cast<std::int64_t>(std::llround(v * 1e6)));
    for (int shift : {0, 8, 16, 24}) {
      h = (h ^ ((um >> shift) & 0xffu)) * 0x01000193u;
    }
  }
  EXPECT_EQ(h, 0xfb8548afu);
}

TEST(LooseEdgeCurves, SolveRunArcPreservesMirroredCircularArcs) {
  GeometryBuilder b;
  b.edge_curves[1] = 17;
  b.arc_curves[17] = make_frame();
  const std::vector<double> mirror{-1, 0, 0, 0, 1, 0, 0, 0, 1, 10, 0, 0, 1};
  std::vector<std::array<double, 3>> pts;
  for (double t : {0.0, kPi / 8.0, kPi / 4.0, 3.0 * kPi / 8.0, kPi / 2.0}) {
    pts.push_back({round6((-kRadiusIn * std::cos(t) + 10.0) * kCurvesInchesToM), 0.0,
                   round6(-kRadiusIn * std::sin(t) * kCurvesInchesToM)});
  }
  const auto arc = solve_run_arc(b, {1}, &mirror, pts, false);
  ASSERT_TRUE(arc.has_value());
  EXPECT_EQ(arc->segments, (std::vector<std::array<std::size_t, 3>>{{0, 1, 2}}));
  const double ex = (-kRadiusIn * std::cos(kPi / 4.0) + 10.0) * kCurvesInchesToM;
  const double ez = -kRadiusIn * std::sin(kPi / 4.0) * kCurvesInchesToM;
  EXPECT_NEAR(arc->points[1][0], ex, 1e-6);
  EXPECT_NEAR(arc->points[1][1], 0.0, 1e-6);
  EXPECT_NEAR(arc->points[1][2], ez, 1e-6);
}

TEST(LooseEdgeCurves, CurveResourcesDeduplicateByEffectiveLayerAndRespectOwnTags) {
  auto w = create();
  const int wood = w->add_layer("Wood");
  const int layer_a = w->add_layer("LayerA");
  const int layer_b = w->add_layer("LayerB");

  auto& def = w->add_component_definition("WoodWithCurve");
  FaceOptions fopts{};
  fopts.layer = wood;
  def.add_face({{0, 0, 0}, {2, 0, 0}, {2, 2, 0}, {0, 2, 0}}, fopts);
  def.add_polyline({{0, 3, 0}, {2, 3, 0}});
  def.close();

  InstanceOptions i0{};
  i0.name = "OnLayer0";
  w->add_instance(def, i0);

  InstanceOptions iw{};
  iw.name = "OnWood";
  iw.layer = wood;
  iw.translation = {5, 0, 0};
  w->add_instance(def, iw);

  InstanceOptions ia{};
  ia.name = "OnA";
  ia.layer = layer_a;
  ia.translation = {10, 0, 0};
  w->add_instance(def, ia);

  InstanceOptions ib{};
  ib.name = "OnB";
  ib.layer = layer_b;
  ib.translation = {15, 0, 0};
  w->add_instance(def, ib);

  const auto inst = SkpFile::from_buffer(w->to_bytes()).build_instanced_scene();
  ASSERT_EQ(inst.curve_resources.size(), 3u);
  std::map<std::string, const InstancedNode*> by_name;
  for (const auto& n : inst.scene_hierarchy.children) by_name[n.name] = &n;
  ASSERT_EQ(by_name["OnLayer0"]->curve_resource_id, by_name["OnWood"]->curve_resource_id);

  std::map<std::string, const InstancedCurveResource*> by_id;
  for (const auto& r : inst.curve_resources) by_id[r.id] = &r;
  EXPECT_EQ(by_id[*by_name["OnLayer0"]->curve_resource_id]->curves[0].layer, "Wood");
  EXPECT_EQ(by_id[*by_name["OnA"]->curve_resource_id]->curves[0].layer, "LayerA");
  EXPECT_EQ(by_id[*by_name["OnB"]->curve_resource_id]->curves[0].layer, "LayerB");
}

}  // namespace
}  // namespace openskp
