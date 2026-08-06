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
