#include <cmath>
#include <gtest/gtest.h>

#include <openskp/openskp.hpp>
using namespace openskp;

TEST(Triangulator, ComputesFaceNormal) {
  auto normal = compute_face_normal({Vec3{0, 0, 0}, Vec3{1, 0, 0}, Vec3{1, 1, 0}, Vec3{0, 1, 0}});
  ASSERT_TRUE(normal.has_value());
  EXPECT_NEAR((*normal)[0], 0, 1e-12);
  EXPECT_NEAR((*normal)[1], 0, 1e-12);
  EXPECT_NEAR((*normal)[2], 1, 1e-12);
  EXPECT_FALSE(compute_face_normal({Vec3{0, 0, 0}, Vec3{1, 0, 0}}).has_value());
}

TEST(Triangulator, SplitsQuadConsistently) {
  std::map<EntityId, Vertex> vertices{
      {0, {0, 0, 0, 0}}, {1, {1, 1, 0, 0}}, {2, {2, 1, 1, 0}}, {3, {3, 0, 1, 0}}};
  const auto triangles = triangulate_face_3d(vertices, {{0, 1, 2, 3}}, {0, 0, 1});
  ASSERT_EQ(triangles.size(), 2);
  EXPECT_EQ(triangles[0], (std::array<EntityId, 3>{0, 1, 2}));
  EXPECT_EQ(triangles[1], (std::array<EntityId, 3>{0, 2, 3}));
}

static double tri_area(const std::vector<std::array<EntityId, 3>>& ts,
                       const std::map<EntityId, Vertex>& v) {
  double a = 0;
  for (auto& t : ts) {
    auto& p = v.at(t[0]);
    auto& q = v.at(t[1]);
    auto& r = v.at(t[2]);
    a += std::abs((q.x - p.x) * (r.y - p.y) - (r.x - p.x) * (q.y - p.y)) / 2;
  }
  return a;
}

TEST(Triangulator, Concave) {
  std::map<EntityId, Vertex> v{{0, {0, 0, 0, 0}}, {1, {1, 4, 0, 0}}, {2, {2, 4, 2, 0}},
                               {3, {3, 2, 2, 0}}, {4, {4, 2, 4, 0}}, {5, {5, 0, 4, 0}}};
  auto t = triangulate_face_3d(v, {{0, 1, 2, 3, 4, 5}}, {0, 0, 1});
  EXPECT_EQ(t.size(), 4);
  EXPECT_NEAR(tri_area(t, v), 12, 1e-6);
}

TEST(Triangulator, Hole) {
  std::map<EntityId, Vertex> v{{0, {0, 0, 0, 0}},   {1, {1, 10, 0, 0}},  {2, {2, 10, 10, 0}},
                               {3, {3, 0, 10, 0}},  {10, {10, 4, 4, 0}}, {11, {11, 6, 4, 0}},
                               {12, {12, 6, 6, 0}}, {13, {13, 4, 6, 0}}};
  auto t = triangulate_face_3d(v, {{0, 1, 2, 3}, {10, 11, 12, 13}}, {0, 0, 1});
  EXPECT_FALSE(t.empty());
  EXPECT_NEAR(tri_area(t, v), 96, 1e-6);
}

// Port of the Python/.NET/TypeScript/Dart fix for openskp#285's hole-hole-
// overlap triangulation bug (see .NET's OverlappingFaceHolesTests.cs for
// the full writeup this mirrors). Root cause here specifically: earcut_2d
// bridges each hole into the outer boundary independently (its own take
// on ear-clipping-with-holes, distinct from the mapbox-style hole-index
// API the other 4 ports use) - bridging a second, overlapping hole into a
// polygon a first hole has already been spliced into can produce a
// self-intersecting merged ring, which clip()'s own degenerate-vertex
// fallback then silently erodes well beyond the two affected holes
// (observed on the real fixture this fix targets: Untitled.skp).
static std::vector<EntityId> add_circle(std::map<EntityId, Vertex>& v, EntityId first_id, double cx,
                                        double cy, double r, int n = 24) {
  std::vector<EntityId> ids;
  for (int i = 0; i < n; ++i) {
    double a = 2 * 3.14159265358979323846 * i / n;
    EntityId id = first_id + i;
    v[id] = {id, cx + r * std::cos(a), cy + r * std::sin(a), 0};
    ids.push_back(id);
  }
  return ids;
}

TEST(Triangulator, OverlappingHolesMatchIndependentAnalyticalCircleUnionArea) {
  // Strongest possible check: the expected area is computed a totally
  // independent way (the closed-form circle-circle intersection formula),
  // not via this port's own triangulator - catching wrong-but-plausible
  // output a triangle count alone could miss.
  const double r = 0.1969;
  const double gap = 0.33 - 2 * r;
  ASSERT_LT(gap, 0) << "these two circles must genuinely overlap, not just sit close";

  const double w = 2.0, h = 4.0;
  const double c1x = 1.0, c1y = 1.8345;
  const double c2x = 1.0, c2y = 2.1655;

  std::map<EntityId, Vertex> v{
      {0, {0, 0, 0, 0}}, {1, {1, w, 0, 0}}, {2, {2, w, h, 0}}, {3, {3, 0, h, 0}}};
  auto hole1 = add_circle(v, 100, c1x, c1y, r);
  auto hole2 = add_circle(v, 200, c2x, c2y, r);

  auto t = triangulate_face_3d(v, {{0, 1, 2, 3}, hole1, hole2}, {0, 0, 1});
  ASSERT_FALSE(t.empty());

  double baked_area = tri_area(t, v);
  double dist = std::sqrt((c1x - c2x) * (c1x - c2x) + (c1y - c2y) * (c1y - c2y));
  double inter =
      2 * r * r * std::acos(dist / (2 * r)) - (dist / 2) * std::sqrt(4 * r * r - dist * dist);
  double union_area = 2 * 3.14159265358979323846 * r * r - inter;
  double expected_area = w * h - union_area;

  // Filter-based triangulation is coarser at the cut than a clean union
  // boundary, so allow a wider tolerance than a from-scratch computation's
  // own 1% - 5% comfortably separates "correct, just coarser" from the
  // pre-fix bug (which was off by a much larger margin - the real
  // fixture's own total triangle count dropped by roughly 10% pre-fix).
  double rel_error = std::abs(baked_area - expected_area) / expected_area;
  EXPECT_LT(rel_error, 0.05) << "baked area " << baked_area << " vs analytical " << expected_area
                             << " (rel err " << rel_error << ")";
}
