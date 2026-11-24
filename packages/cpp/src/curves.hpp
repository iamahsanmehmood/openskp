#pragma once

#include <array>
#include <optional>
#include <string>
#include <unordered_map>
#include <vector>

#include "internal.hpp"

namespace openskp {

inline constexpr double kCurvesInchesToM = 0.0254;

struct PreparedCurveRun {
  std::vector<EntityId> edge_ids;
  std::vector<Vec3> local_verts;
  bool closed{};
  std::optional<std::string> explicit_layer;
};

struct PreparedDefCurves {
  std::vector<PreparedCurveRun> runs;
  std::unordered_map<EntityId, std::size_t> curve_edge_counts;
};

double round6(double x);

std::vector<EntityId> py_set_order(const std::vector<EntityId>& keys);

std::vector<LooseEdgeRun> chain_loose_edges(const GeometryBuilder& b);

PreparedDefCurves prepare_def_curves(const RawParsed& parsed, const GeometryBuilder& b,
                                     const ParseOptions& options = {});

std::string resolve_run_layer(const RawParsed& parsed, const GeometryBuilder& b,
                              const std::vector<EntityId>& eids, const std::string& fallback,
                              const ParseOptions& options = {});

std::optional<CurveArc> solve_run_arc(
    const GeometryBuilder& b, const std::vector<EntityId>& eids, const std::vector<double>* matrix,
    const std::vector<std::array<double, 3>>& pts, bool closed,
    const std::unordered_map<EntityId, std::size_t>* curve_edge_counts = nullptr);

}  // namespace openskp
