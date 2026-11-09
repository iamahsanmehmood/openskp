#include <cmath>

#include "internal.hpp"

namespace openskp {

std::optional<Vec3> compute_face_normal(const std::vector<Vec3>& points) {
  if (points.size() < 3) return {};

  Vec3 normal{};
  for (std::size_t i = 0; i < points.size(); ++i) {
    const auto& current = points[i];
    const auto& next = points[(i + 1) % points.size()];
    normal[0] += (current[1] - next[1]) * (current[2] + next[2]);
    normal[1] += (current[2] - next[2]) * (current[0] + next[0]);
    normal[2] += (current[0] - next[0]) * (current[1] + next[1]);
  }

  const double length =
      std::sqrt(normal[0] * normal[0] + normal[1] * normal[1] + normal[2] * normal[2]);
  if (length < 1e-12) return {};
  for (double& value : normal) value /= length;
  return normal;
}

namespace {
// Whether to split the quad along its 0-2 diagonal. A concave quad whose
// reflex corner is 1 or 3 has that diagonal outside it: triangles (0,1,2)
// and (0,2,3) then turn opposite ways and cover more than the face, and the
// 1-3 split covers exactly the face. A self-intersecting or warped quad can
// fail the turn test with the 1-3 split no better, so 1-3 is taken only when
// it covers less area.
bool split_quad_along_02(const std::map<EntityId, Vertex>& verts,
                         const std::vector<EntityId>& quad) {
  std::array<Vec3, 4> p;
  for (std::size_t i = 0; i < 4; ++i) {
    auto it = verts.find(quad[i]);
    if (it == verts.end()) return true;
    p[i] = {it->second.x, it->second.y, it->second.z};
  }
  // Twice the area vector of triangle (a, b, c).
  const auto area2 = [&](int a, int b, int c) {
    const Vec3 u{p[b][0] - p[a][0], p[b][1] - p[a][1], p[b][2] - p[a][2]};
    const Vec3 v{p[c][0] - p[a][0], p[c][1] - p[a][1], p[c][2] - p[a][2]};
    return Vec3{u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0]};
  };
  const auto dot = [](const Vec3& a, const Vec3& b) {
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
  };
  const auto t012 = area2(0, 1, 2), t023 = area2(0, 2, 3);
  if (dot(t012, t023) >= 0) return true;
  const auto t123 = area2(1, 2, 3), t130 = area2(1, 3, 0);
  return std::sqrt(dot(t123, t123)) + std::sqrt(dot(t130, t130)) >=
         std::sqrt(dot(t012, t012)) + std::sqrt(dot(t023, t023));
}
}  // namespace

std::vector<std::array<EntityId, 3>> triangulate_face_3d(
    const std::map<EntityId, Vertex>& verts, const std::vector<std::vector<EntityId>>& loops,
    Vec3 n) {
  if (loops.empty()) return {};
  if (loops.size() == 1 && loops[0].size() == 3) return {{loops[0][0], loops[0][1], loops[0][2]}};
  if (loops.size() == 1 && loops[0].size() == 4) {
    const auto& q = loops[0];
    if (split_quad_along_02(verts, q)) return {{{q[0], q[1], q[2]}}, {{q[0], q[2], q[3]}}};
    return {{{q[1], q[2], q[3]}}, {{q[1], q[3], q[0]}}};
  }
  double len = std::sqrt(n[0] * n[0] + n[1] * n[1] + n[2] * n[2]);
  if (len < 1e-6)
    n = {0, 0, 1};
  else
    for (auto& x : n) x /= len;
  Vec3 seed = std::abs(n[0]) < .9 ? Vec3{1, 0, 0} : Vec3{0, 1, 0};
  Vec3 u{n[1] * seed[2] - n[2] * seed[1], n[2] * seed[0] - n[0] * seed[2],
         n[0] * seed[1] - n[1] * seed[0]};
  len = std::sqrt(u[0] * u[0] + u[1] * u[1] + u[2] * u[2]);
  if (len < 1e-12)
    u = {1, 0, 0};
  else
    for (auto& x : u) x /= len;
  Vec3 v{n[1] * u[2] - n[2] * u[1], n[2] * u[0] - n[0] * u[2], n[0] * u[1] - n[1] * u[0]};
  std::vector<std::vector<EarPoint>> projected;
  for (auto& loop : loops) {
    std::vector<EarPoint> p;
    for (auto id : loop) {
      auto i = verts.find(id);
      if (i == verts.end()) return {};
      auto& q = i->second;
      p.push_back({q.x * u[0] + q.y * u[1] + q.z * u[2], q.x * v[0] + q.y * v[1] + q.z * v[2], id});
    }
    projected.push_back(std::move(p));
  }
  return earcut_2d(std::move(projected));
}
}  // namespace openskp
