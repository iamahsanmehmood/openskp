#pragma once

#include <array>
#include <optional>
#include <string>
#include <vector>

#include "internal.hpp"

namespace openskp {

inline constexpr double kCurvesInchesToM = 0.0254;

double round6(double x);

std::vector<EntityId> py_set_order(const std::vector<EntityId>& keys);

std::vector<LooseEdgeRun> chain_loose_edges(const GeometryBuilder& b);

std::string resolve_run_layer(const RawParsed& parsed, const GeometryBuilder& b,
                              const std::vector<EntityId>& eids, const std::string& fallback,
                              const ParseOptions& options = {});

std::optional<CurveArc> solve_run_arc(const GeometryBuilder& b, const std::vector<EntityId>& eids,
                                      const std::vector<double>* matrix,
                                      const std::vector<std::array<double, 3>>& pts, bool closed);

}  // namespace openskp
