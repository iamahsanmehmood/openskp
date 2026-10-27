#include <algorithm>
#include <cmath>
#include <gtest/gtest.h>
#include <map>
#include <vector>

#include <openskp/openskp.hpp>

#include "test_helpers.hpp"

namespace openskp {
namespace {

// Definition::arcs: the analytic arc of every curve that is one (openskp#462).
//
// Fixtures: arcs_2017.skp, arcs_2020.skp (classic container) and arcs_2021.skp (VFF) - three arcs
// built through the installed SketchUp library: a loose arc (centre 0,0,0, radius 10, 0 to 90
// degrees, 12 segments); a disc (a full circle bounding a face, centre 50,0,0, radius 5, 24
// sides); and a half disc (an arc closed by its chord, bounding a face, centre 100,0,0, radius 8,
// 0 to 180 degrees, 12 segments). SketchUp reads the same centres, radii and angles back from all
// three files. Untitled.skp is a real file whose 368 arcs (24-edge circles) agree with
// SketchUp's own values to 5e-6 inches. No private content.

const char* const kNames[] = {"arcs_2017.skp", "arcs_2020.skp", "arcs_2021.skp"};
const double kPi = std::acos(-1.0);

struct Expected {
  double cx, radius, sweep;
  int edges;
  bool full;
};

const Expected kExpected[] = {
    {0.0, 10.0, kPi / 2, 12, false}, {50.0, 5.0, 2 * kPi, 24, true}, {100.0, 8.0, kPi, 12, false}};

TEST(ArcCurves, EveryArcIsReadWithItsCentreRadiusAndAngles) {
  for (const char* name : kNames) {
    SCOPED_TRACE(name);
    const auto model = SkpFile::open(test::fixture(name)).parse();
    std::vector<std::pair<EntityId, ArcCurve>> arcs(model.root().arcs.begin(),
                                                    model.root().arcs.end());
    std::sort(arcs.begin(), arcs.end(),
              [](const auto& a, const auto& b) { return a.second.center[0] < b.second.center[0]; });
    ASSERT_EQ(arcs.size(), 3u);
    for (size_t i = 0; i < 3; ++i) {
      const auto& a = arcs[i].second;
      const auto& e = kExpected[i];
      EXPECT_NEAR(a.center[0], e.cx, 1e-9);
      EXPECT_NEAR(a.center[1], 0.0, 1e-9);
      EXPECT_NEAR(a.center[2], 0.0, 1e-9);
      EXPECT_NEAR(a.normal[2], 1.0, 1e-9);
      EXPECT_NEAR(a.radius(), e.radius, 1e-9);
      EXPECT_NEAR(a.start_angle, 0.0, 1e-9);
      EXPECT_NEAR(std::fmod(a.end_angle - a.start_angle, 2 * kPi), std::fmod(e.sweep, 2 * kPi),
                  1e-9);
      EXPECT_EQ(a.is_full_circle(), e.full);
      EXPECT_NEAR(a.x_axis[0], e.radius, 1e-9);
      EXPECT_NEAR(a.y_axis[1], e.radius, 1e-9);
    }
  }
}

TEST(ArcCurves, AnArcIsJoinedToItsEdgesByCurveId) {
  for (const char* name : kNames) {
    SCOPED_TRACE(name);
    const auto model = SkpFile::open(test::fixture(name)).parse();
    std::map<EntityId, int> per_curve;
    for (const auto& e : model.root().edges)
      if (e.second.curve_id) ++per_curve[*e.second.curve_id];
    std::vector<std::pair<EntityId, ArcCurve>> arcs(model.root().arcs.begin(),
                                                    model.root().arcs.end());
    std::sort(arcs.begin(), arcs.end(),
              [](const auto& a, const auto& b) { return a.second.center[0] < b.second.center[0]; });
    ASSERT_EQ(arcs.size(), 3u);
    for (size_t i = 0; i < 3; ++i) EXPECT_EQ(per_curve[arcs[i].first], kExpected[i].edges);
  }
}

TEST(ArcCurves, AModelWithoutArcsHasNone) {
  for (const char* name : {"instance_scale_term_v6.skp", "instance_scale_term_2021.skp"}) {
    SCOPED_TRACE(name);
    const auto model = SkpFile::open(test::fixture(name)).parse();
    EXPECT_TRUE(model.root().arcs.empty());
    for (const auto& d : model.definitions) EXPECT_TRUE(d.second.arcs.empty());
  }
}

TEST(ArcCurves, UntitledHas368ArcsEachTheCircleSketchUpReports) {
  const auto model = SkpFile::open(test::fixture("Untitled.skp")).parse();
  int total = 0;
  auto check = [&](const Definition& d) {
    std::map<EntityId, int> per_curve;
    for (const auto& e : d.edges)
      if (e.second.curve_id) ++per_curve[*e.second.curve_id];
    for (const auto& a : d.arcs) {
      ++total;
      EXPECT_EQ(per_curve[a.first], 24);
      EXPECT_TRUE(a.second.is_full_circle());
    }
  };
  for (const auto& d : model.definitions) check(d.second);
  check(model.root());
  EXPECT_EQ(total, 368);
}

}  // namespace
}  // namespace openskp
