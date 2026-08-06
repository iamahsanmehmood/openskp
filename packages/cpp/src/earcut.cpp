#include <algorithm>
#include <cmath>

#include "internal.hpp"

namespace openskp {
namespace {
double area(const std::vector<EarPoint>& p) {
  double a = 0;
  for (size_t i = 0; i < p.size(); ++i) {
    auto& q = p[(i + 1) % p.size()];
    a += p[i].x * q.y - q.x * p[i].y;
  }
  return a * .5;
}

double cross(const EarPoint& a, const EarPoint& b, const EarPoint& c) {
  return (b.x - a.x) * (c.y - a.y) - (b.y - a.y) * (c.x - a.x);
}

bool inside(const EarPoint& p, const EarPoint& a, const EarPoint& b, const EarPoint& c) {
  auto x = cross(a, b, p), y = cross(b, c, p), z = cross(c, a, p);
  return x >= -1e-12 && y >= -1e-12 && z >= -1e-12;
}

std::vector<std::array<EntityId, 3>> clip(std::vector<EarPoint> p) {
  std::vector<std::array<EntityId, 3>> out;
  if (area(p) < 0) std::reverse(p.begin(), p.end());
  size_t guard = 0;
  while (p.size() > 3 && guard++ < p.size() * p.size() * 4) {
    bool cut = false;
    for (size_t i = 0; i < p.size(); ++i) {
      auto a = (i + p.size() - 1) % p.size(), c = (i + 1) % p.size();
      if (cross(p[a], p[i], p[c]) <= 1e-12) continue;
      bool hit = false;
      for (size_t j = 0; j < p.size(); ++j)
        if (j != a && j != i && j != c && inside(p[j], p[a], p[i], p[c]) && p[j].id != p[a].id &&
            p[j].id != p[i].id && p[j].id != p[c].id) {
          hit = true;
          break;
        }
      if (hit) continue;
      out.push_back({p[a].id, p[i].id, p[c].id});
      p.erase(p.begin() + i);
      cut = true;
      break;
    }
    if (!cut) {
      for (size_t i = 0; i < p.size(); ++i) {
        auto a = (i + p.size() - 1) % p.size(), c = (i + 1) % p.size();
        if (std::abs(cross(p[a], p[i], p[c])) < 1e-10) {
          p.erase(p.begin() + i);
          cut = true;
          break;
        }
      }
      if (!cut) break;
    }
  }
  if (p.size() == 3 && std::abs(cross(p[0], p[1], p[2])) > 1e-12)
    out.push_back({p[0].id, p[1].id, p[2].id});
  return out;
}
}  // namespace

std::vector<std::array<EntityId, 3>> earcut_2d(std::vector<std::vector<EarPoint>> loops) {
  if (loops.empty()) return {};
  auto poly = std::move(loops[0]);
  if (area(poly) < 0) std::reverse(poly.begin(), poly.end());
  for (size_t h = 1; h < loops.size(); ++h) {
    auto hole = std::move(loops[h]);
    if (hole.empty()) continue;
    if (area(hole) > 0) std::reverse(hole.begin(), hole.end());
    size_t hi = 0;
    for (size_t i = 1; i < hole.size(); ++i)
      if (hole[i].x > hole[hi].x || (hole[i].x == hole[hi].x && hole[i].y < hole[hi].y)) hi = i;
    size_t oi = 0;
    double best = 1e300;
    for (size_t i = 0; i < poly.size(); ++i) {
      double dx = poly[i].x - hole[hi].x;
      if (dx >= 0) {
        double d = dx * dx + (poly[i].y - hole[hi].y) * (poly[i].y - hole[hi].y);
        if (d < best) {
          best = d;
          oi = i;
        }
      }
    }
    std::vector<EarPoint> merged;
    for (size_t i = 0; i <= oi; ++i) merged.push_back(poly[i]);
    merged.push_back(hole[hi]);
    for (size_t k = 1; k < hole.size(); ++k) merged.push_back(hole[(hi + k) % hole.size()]);
    merged.push_back(hole[hi]);
    merged.push_back(poly[oi]);
    for (size_t i = oi + 1; i < poly.size(); ++i) merged.push_back(poly[i]);
    poly = std::move(merged);
  }
  return clip(std::move(poly));
}
}  // namespace openskp
