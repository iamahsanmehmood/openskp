#include "curves.hpp"

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <unordered_map>
#include <unordered_set>
#include <utility>

namespace openskp {
namespace {

constexpr double kPi = 3.141592653589793238462643383279502884;

std::int64_t py_int_hash(std::int64_t v) {
  constexpr std::uint64_t kModulus = (std::uint64_t{1} << 61) - 1;
  std::int64_t h = 0;
  if (v >= 0) {
    h = static_cast<std::int64_t>(static_cast<std::uint64_t>(v) % kModulus);
  } else {
    const std::uint64_t mag = std::uint64_t{0} - static_cast<std::uint64_t>(v);
    h = -static_cast<std::int64_t>(mag % kModulus);
  }
  return h == -1 ? -2 : h;
}

template <typename K, typename V>
class OrderedMap {
 public:
  V& entry(K k) {
    auto it = index_.find(k);
    if (it == index_.end()) {
      const std::size_t idx = keys_.size();
      keys_.push_back(k);
      values_.emplace_back();
      index_.emplace(k, idx);
      return values_[idx];
    }
    return values_[it->second];
  }

  const V* get(K k) const {
    auto it = index_.find(k);
    return it == index_.end() ? nullptr : &values_[it->second];
  }

  const std::vector<K>& keys() const { return keys_; }

  const std::vector<V>& values() const { return values_; }

 private:
  std::vector<K> keys_;
  std::vector<V> values_;
  std::unordered_map<K, std::size_t> index_;
};

std::vector<LooseEdgeRun> order_curve_edges(
    const std::unordered_map<EntityId, std::pair<EntityId, EntityId>>& edges,
    const std::vector<EntityId>& eids) {
  const auto order = py_set_order(eids);
  std::unordered_set<EntityId> remaining(eids.begin(), eids.end());
  std::vector<LooseEdgeRun> runs;

  auto ends = [&](EntityId e) -> std::pair<EntityId, EntityId> {
    auto it = edges.find(e);
    if (it == edges.end()) return {0, 0};
    return it->second;
  };

  while (!remaining.empty()) {
    OrderedMap<EntityId, std::vector<std::pair<EntityId, EntityId>>> adj;
    for (EntityId e : order) {
      if (!remaining.count(e)) continue;
      const auto [a, c] = ends(e);
      adj.entry(a).emplace_back(e, c);
      adj.entry(c).emplace_back(e, a);
    }
    if (adj.keys().empty()) break;

    EntityId start = adj.keys()[0];
    for (std::size_t i = 0; i < adj.keys().size(); ++i) {
      if (adj.values()[i].size() == 1) {
        start = adj.keys()[i];
        break;
      }
    }

    const auto* first_opts = adj.get(start);
    if (!first_opts || first_opts->empty()) break;
    EntityId eid = (*first_opts)[0].first;
    EntityId nxt = (*first_opts)[0].second;
    remaining.erase(eid);

    std::vector<EntityId> chain_eids{eid};
    std::vector<EntityId> chain{start, nxt};
    bool closed = false;

    while (true) {
      const auto* opts = adj.get(nxt);
      if (!opts) break;
      auto next_it = std::find_if(opts->begin(), opts->end(),
                                  [&](const auto& t) { return remaining.count(t.first) > 0; });
      if (next_it == opts->end()) break;
      const auto [e2, v2] = *next_it;
      remaining.erase(e2);
      chain_eids.push_back(e2);
      if (v2 == chain[0]) {
        closed = true;
        break;
      }
      chain.push_back(v2);
      nxt = v2;
    }

    runs.push_back(LooseEdgeRun{std::move(chain_eids), std::move(chain), closed});
  }
  return runs;
}

double py_sum3(double a, double b, double c) {
  const double values[3] = {a, b, c};
  double f_result = values[0];
  double comp = 0.0;
  for (int i = 1; i < 3; ++i) {
    const double x = values[i];
    const double t = f_result + x;
    if (std::abs(f_result) >= std::abs(x)) {
      comp += (f_result - t) + x;
    } else {
      comp += (x - t) + f_result;
    }
    f_result = t;
  }
  if (comp != 0.0 && std::isfinite(comp)) {
    f_result += comp;
  }
  return f_result;
}

double dist3(const std::array<double, 3>& p, const std::array<double, 3>& q) {
  const double dx = p[0] - q[0];
  const double dy = p[1] - q[1];
  const double dz = p[2] - q[2];
  return std::sqrt(dx * dx + dy * dy + dz * dz);
}

std::optional<std::string> resolve_explicit_run_layer(const RawParsed& parsed,
                                                      const GeometryBuilder& b,
                                                      const std::vector<EntityId>& eids,
                                                      const ParseOptions& options) {
  if (b.edge_layers.empty()) return std::nullopt;
  std::vector<std::pair<std::string, std::size_t>> counts;
  for (EntityId eid : eids) {
    auto li = b.edge_layers.find(eid);
    if (li == b.edge_layers.end() || li->second.empty() || li->second == "0") continue;
    auto it = std::find_if(counts.begin(), counts.end(),
                           [&](const auto& kv) { return kv.first == li->second; });
    if (it != counts.end()) {
      ++it->second;
    } else {
      counts.emplace_back(li->second, 1);
    }
  }
  if (counts.empty()) return std::nullopt;
  const auto best =
      std::max_element(counts.begin(), counts.end(),
                       [](const auto& lhs, const auto& rhs) { return lhs.second < rhs.second; });
  try {
    const auto id = std::stoll(best->first);
    if (id == 0) return std::nullopt;
    auto found = parsed.layer_id_to_name.find(id);
    if (found != parsed.layer_id_to_name.end()) return found->second;
  } catch (...) {
    emit_log(options, LogLevel::debug,
             "Failed to resolve edge layer id '" + best->first + "' to a name");
  }
  return std::nullopt;
}

}  // namespace

double round6(double x) {
  if (!std::isfinite(x) || std::abs(x) >= 8589934592.0 || x == 0.0) return x;
  const double abs_scaled = std::abs(x * 1e6);
  if (abs_scaled < 4.5e12) {
    const double fl = std::floor(abs_scaled);
    const double frac = abs_scaled - fl;
    if (std::abs(frac - 0.5) > 1e-3) {
      const double res = (frac > 0.5 ? fl + 1.0 : fl) / 1e6;
      return std::copysign(res, x);
    }
  }

  const double ax = std::abs(x);
  std::uint64_t bits = 0;
  std::memcpy(&bits, &ax, sizeof(bits));
  const int raw_exp = static_cast<int>((bits >> 52) & 0x7FFULL);
  if (raw_exp == 0) return std::copysign(0.0, x);
  const std::uint64_t mant = (std::uint64_t{1} << 52) | (bits & 0xFFFFFFFFFFFFFULL);
  const int shift = 1069 - raw_exp;
  if (shift >= 68) return std::copysign(0.0, x);

  const std::uint64_t lo1 = (mant & 0xFFFFFFFFULL) * 15625ULL;
  const std::uint64_t hi1 = (mant >> 32) * 15625ULL;
  const std::uint64_t lo = lo1 + (hi1 << 32);
  const std::uint64_t hi = (hi1 >> 32) + (lo < lo1 ? 1ULL : 0ULL);

  std::uint64_t k = 0;
  std::uint64_t half_bit = 0;
  bool rest_nonzero = false;
  if (shift >= 64) {
    const int s = shift - 64;
    k = hi >> s;
    if (s == 0) {
      half_bit = (lo >> 63) & 1ULL;
      rest_nonzero = (lo & 0x7FFFFFFFFFFFFFFFULL) != 0ULL;
    } else {
      half_bit = (hi >> (s - 1)) & 1ULL;
      rest_nonzero = ((hi & ((std::uint64_t{1} << (s - 1)) - 1ULL)) != 0ULL) || (lo != 0ULL);
    }
  } else {
    k = (hi << (64 - shift)) | (lo >> shift);
    half_bit = (lo >> (shift - 1)) & 1ULL;
    rest_nonzero = (lo & ((std::uint64_t{1} << (shift - 1)) - 1ULL)) != 0ULL;
  }
  if (half_bit == 1ULL && (rest_nonzero || (k & 1ULL) == 1ULL)) {
    ++k;
  }
  return std::copysign(static_cast<double>(k) / 1e6, x);
}

std::vector<EntityId> py_set_order(const std::vector<EntityId>& keys) {
  constexpr std::uint64_t kLinearProbes = 9;
  constexpr unsigned int kPerturbShift = 5;

  struct Entry {
    bool occupied = false;
    EntityId key = 0;
    std::int64_t hash = 0;
  };

  auto insert_clean = [&](std::vector<Entry>& table, EntityId key, std::int64_t hash) {
    const std::uint64_t mask = static_cast<std::uint64_t>(table.size() - 1);
    std::uint64_t perturb = static_cast<std::uint64_t>(hash);
    std::uint64_t i = static_cast<std::uint64_t>(hash) & mask;
    while (true) {
      if (!table[static_cast<std::size_t>(i)].occupied) {
        table[static_cast<std::size_t>(i)] = Entry{true, key, hash};
        return;
      }
      if (i + kLinearProbes <= mask) {
        for (std::uint64_t j = 1; j <= kLinearProbes; ++j) {
          if (!table[static_cast<std::size_t>(i + j)].occupied) {
            table[static_cast<std::size_t>(i + j)] = Entry{true, key, hash};
            return;
          }
        }
      }
      perturb >>= kPerturbShift;
      i = (i * 5 + 1 + perturb) & mask;
    }
  };

  std::vector<Entry> table(8);
  std::unordered_set<EntityId> seen;
  std::size_t used = 0;
  for (EntityId key : keys) {
    if (!seen.insert(key).second) continue;
    insert_clean(table, key, py_int_hash(key));
    ++used;
    const std::size_t mask = table.size() - 1;
    if (used * 5 >= mask * 3) {
      const std::size_t minused = used > 50000 ? used * 2 : used * 4;
      std::size_t newsize = 8;
      while (newsize <= minused) newsize <<= 1;
      std::vector<Entry> next_table(newsize);
      for (const auto& entry : table) {
        if (entry.occupied) insert_clean(next_table, entry.key, entry.hash);
      }
      table = std::move(next_table);
    }
  }

  std::vector<EntityId> out;
  out.reserve(used);
  for (const auto& entry : table) {
    if (entry.occupied) out.push_back(entry.key);
  }
  return out;
}

std::vector<LooseEdgeRun> chain_loose_edges(const GeometryBuilder& b) {
  if (b.edges.empty()) return {};

  std::unordered_set<EntityId> used;
  for (const auto& [fid, f] : b.faces) {
    for (const auto& loop : f.loops) {
      for (const auto& co : loop) {
        used.insert(co.edge_id);
      }
    }
  }

  std::unordered_map<EntityId, std::pair<EntityId, EntityId>> edge_ends;
  OrderedMap<EntityId, std::vector<EntityId>> groups;
  std::vector<LooseEdgeRun> runs;

  for (const auto& [eid, pair] : b.edges) {
    if (used.count(eid)) continue;
    if (!pair.first || !pair.second) continue;
    const EntityId a = *pair.first;
    const EntityId c = *pair.second;
    if (a == c) continue;

    auto ci = b.edge_curves.find(eid);
    if (ci != b.edge_curves.end() && ci->second != 0) {
      edge_ends.emplace(eid, std::make_pair(a, c));
      groups.entry(ci->second).push_back(eid);
    } else {
      runs.push_back(LooseEdgeRun{{eid}, {a, c}, false});
    }
  }

  for (const auto& eids : groups.values()) {
    auto sub = order_curve_edges(edge_ends, eids);
    runs.insert(runs.end(), std::make_move_iterator(sub.begin()),
                std::make_move_iterator(sub.end()));
  }
  return runs;
}

PreparedDefCurves prepare_def_curves(const RawParsed& parsed, const GeometryBuilder& b,
                                     const ParseOptions& options) {
  auto raw_runs = chain_loose_edges(b);
  if (raw_runs.empty()) return {};

  PreparedDefCurves out;
  out.runs.reserve(raw_runs.size());
  for (auto& run : raw_runs) {
    std::vector<Vec3> local_verts;
    local_verts.reserve(run.vertex_ids.size());
    bool complete = true;
    for (EntityId vk : run.vertex_ids) {
      auto vi = b.vertices.find(vk);
      if (vi == b.vertices.end()) {
        complete = false;
        break;
      }
      local_verts.push_back(vi->second);
    }
    if (!complete || local_verts.size() < 2) continue;
    auto explicit_layer = resolve_explicit_run_layer(parsed, b, run.edge_ids, options);
    out.runs.push_back(PreparedCurveRun{
        std::move(run.edge_ids),
        std::move(local_verts),
        run.closed,
        std::move(explicit_layer),
    });
  }
  if (out.runs.empty()) return {};

  if (!b.arc_curves.empty()) {
    out.curve_edge_counts.reserve(b.edge_curves.size());
    for (const auto& [eid, cid] : b.edge_curves) {
      if (cid != 0) ++out.curve_edge_counts[cid];
    }
  }
  return out;
}

std::string resolve_run_layer(const RawParsed& parsed, const GeometryBuilder& b,
                              const std::vector<EntityId>& eids, const std::string& fallback,
                              const ParseOptions& options) {
  return resolve_explicit_run_layer(parsed, b, eids, options).value_or(fallback);
}

std::optional<CurveArc> solve_run_arc(
    const GeometryBuilder& b, const std::vector<EntityId>& eids, const std::vector<double>* matrix,
    const std::vector<std::array<double, 3>>& pts, bool closed,
    const std::unordered_map<EntityId, std::size_t>* curve_edge_counts) {
  if (b.arc_curves.empty() || eids.empty() || pts.size() < 2) return std::nullopt;
  auto ci = b.edge_curves.find(eids[0]);
  if (ci == b.edge_curves.end()) return std::nullopt;
  const EntityId cid = ci->second;
  auto fi = b.arc_curves.find(cid);
  if (fi == b.arc_curves.end()) return std::nullopt;
  const auto& frame = fi->second;

  std::size_t curve_edge_count = 0;
  if (curve_edge_counts) {
    auto cnt_it = curve_edge_counts->find(cid);
    if (cnt_it != curve_edge_counts->end()) curve_edge_count = cnt_it->second;
  } else {
    for (const auto& [eid, ecid] : b.edge_curves) {
      if (ecid == cid) ++curve_edge_count;
    }
  }
  if (curve_edge_count != eids.size()) return std::nullopt;

  const std::vector<double> folded_storage =
      (matrix && matrix->size() >= 12) ? fold_scale(*matrix) : std::vector<double>{};
  const std::vector<double>* fm = folded_storage.size() >= 12 ? &folded_storage : nullptr;
  bool mirrored = false;
  if (fm) {
    const auto& m = *fm;
    const double det = m[0] * (m[4] * m[8] - m[5] * m[7]) - m[1] * (m[3] * m[8] - m[5] * m[6]) +
                       m[2] * (m[3] * m[7] - m[4] * m[6]);
    mirrored = det < 0.0;
  }

  auto xf_point = [&](const Vec3& p) -> std::array<double, 3> {
    if (!fm) {
      return {p[0] * kCurvesInchesToM, p[2] * kCurvesInchesToM, -p[1] * kCurvesInchesToM};
    }
    const auto& m = *fm;
    const double qx = m[0] * p[0] + m[1] * p[1] + m[2] * p[2] + m[9];
    const double qy = m[3] * p[0] + m[4] * p[1] + m[5] * p[2] + m[10];
    const double qz = m[6] * p[0] + m[7] * p[1] + m[8] * p[2] + m[11];
    return {qx * kCurvesInchesToM, qz * kCurvesInchesToM, -qy * kCurvesInchesToM};
  };

  auto xf_vec = [&](const Vec3& v) -> std::array<double, 3> {
    double x = v[0], y = v[1], z = v[2];
    if (fm) {
      const auto& m = *fm;
      x = m[0] * v[0] + m[1] * v[1] + m[2] * v[2];
      y = m[3] * v[0] + m[4] * v[1] + m[5] * v[2];
      z = m[6] * v[0] + m[7] * v[1] + m[8] * v[2];
    }
    return {x * kCurvesInchesToM, z * kCurvesInchesToM, -y * kCurvesInchesToM};
  };

  const auto c = xf_point(frame.center);
  const auto xa = xf_vec(frame.x_axis);
  const auto ya = xf_vec(frame.y_axis);
  const auto na = xf_vec(frame.normal);

  const double r2 = py_sum3(xa[0] * xa[0], xa[1] * xa[1], xa[2] * xa[2]);
  const double r = std::pow(r2, 0.5);
  if (!(r > 0.0)) return std::nullopt;

  const double nlen = std::pow(py_sum3(na[0] * na[0], na[1] * na[1], na[2] * na[2]), 0.5);
  if (!(nlen > 0.0)) return std::nullopt;
  const double n_sign = mirrored ? -1.0 : 1.0;
  const std::array<double, 3> nh{n_sign * na[0] / nlen, n_sign * na[1] / nlen,
                                 n_sign * na[2] / nlen};
  const std::array<double, 3> cross{
      nh[1] * xa[2] - nh[2] * xa[1],
      nh[2] * xa[0] - nh[0] * xa[2],
      nh[0] * xa[1] - nh[1] * xa[0],
  };
  if (dist3(cross, ya) > 1e-6 * r) return std::nullopt;

  const double inv_r = 1.0 / r;
  const std::array<double, 3> xh{xa[0] * inv_r, xa[1] * inv_r, xa[2] * inv_r};
  const std::array<double, 3> yh{ya[0] * inv_r, ya[1] * inv_r, ya[2] * inv_r};
  auto theta = [&](const std::array<double, 3>& p) -> double {
    const double dx = p[0] - c[0];
    const double dy = p[1] - c[1];
    const double dz = p[2] - c[2];
    return std::atan2(dx * yh[0] + dy * yh[1] + dz * yh[2], dx * xh[0] + dy * xh[1] + dz * xh[2]);
  };

  const double tol = std::max(1e-5, 1e-6 * r);
  for (const auto& p : pts) {
    if (std::abs(dist3(p, c) - r) > tol) return std::nullopt;
  }

  std::vector<double> thetas;
  thetas.reserve(pts.size());
  for (const auto& p : pts) thetas.push_back(theta(p));

  std::vector<std::pair<double, double>> pairs;
  pairs.reserve(pts.size());
  for (std::size_t i = 0; i + 1 < thetas.size(); ++i) {
    pairs.emplace_back(thetas[i], thetas[i + 1]);
  }
  if (closed && thetas.size() > 2) {
    pairs.emplace_back(thetas.back(), thetas.front());
  }

  const double step_limit = kPi * 0.5 * (1.0 + 1e-9);
  std::vector<double> steps;
  steps.reserve(pairs.size());
  double total = 0.0;
  for (const auto& [a, b_ang] : pairs) {
    double d = b_ang - a;
    while (d > kPi) d -= 2.0 * kPi;
    while (d < -kPi) d += 2.0 * kPi;
    if (std::abs(d) > step_limit) return std::nullopt;
    if (!steps.empty() && ((d > 0.0) != (steps[0] > 0.0))) return std::nullopt;
    steps.push_back(d);
    total += d;
  }
  if (steps.empty() || total == 0.0) return std::nullopt;

  auto at = [&](double t) -> std::array<double, 3> {
    const double ct = std::cos(t);
    const double st = std::sin(t);
    return {
        c[0] + ct * xa[0] + st * ya[0],
        c[1] + ct * xa[1] + st * ya[1],
        c[2] + ct * xa[2] + st * ya[2],
    };
  };

  const double t0 = thetas[0];
  if (std::abs(total) >= 2.0 * kPi - 1e-6 * std::max(1.0, std::abs(total))) {
    return CurveArc{
        {at(t0), at(t0 + total * 0.25), at(t0 + total * 0.5), at(t0 + total * 0.75)},
        {{0, 1, 2}, {2, 3, 0}},
    };
  }
  return CurveArc{
      {at(t0), at(t0 + total * 0.5), at(t0 + total)},
      {{0, 1, 2}},
  };
}

std::vector<LooseEdgeRun> Definition::loose_edge_runs() const {
  if (edges.empty()) return {};
  std::unordered_set<EntityId> used;
  for (const auto& [fid, face] : faces) {
    for (const auto& loop : face.loops) {
      for (const auto& co : loop) {
        used.insert(co.edge_id);
      }
    }
  }

  std::unordered_map<EntityId, std::pair<EntityId, EntityId>> edge_ends;
  OrderedMap<EntityId, std::vector<EntityId>> groups;
  std::vector<LooseEdgeRun> runs;

  for (const auto& [eid, edge] : edges) {
    if (used.count(eid)) continue;
    if (edge.v1_id == edge.v2_id) continue;
    if (edge.curve_id && *edge.curve_id != 0) {
      edge_ends.emplace(eid, std::make_pair(edge.v1_id, edge.v2_id));
      groups.entry(*edge.curve_id).push_back(eid);
    } else {
      runs.push_back(LooseEdgeRun{{eid}, {edge.v1_id, edge.v2_id}, false});
    }
  }

  for (const auto& eids : groups.values()) {
    auto sub = order_curve_edges(edge_ends, eids);
    runs.insert(runs.end(), std::make_move_iterator(sub.begin()),
                std::make_move_iterator(sub.end()));
  }
  return runs;
}

std::vector<LooseEdgeRun> loose_edge_runs(const Definition& definition) {
  return definition.loose_edge_runs();
}

}  // namespace openskp
