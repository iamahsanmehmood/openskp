#include "internal.hpp"

namespace openskp {
std::array<double, 3> transform_point(const std::vector<double>& m,
                                      const std::array<double, 3>& p) {
  if (m.size() < 12) return p;
  return {m[0] * p[0] + m[1] * p[1] + m[2] * p[2] + m[9],
          m[3] * p[0] + m[4] * p[1] + m[5] * p[2] + m[10],
          m[6] * p[0] + m[7] * p[1] + m[8] * p[2] + m[11]};
}

std::vector<double> multiply_matrices(const std::vector<double>& a, const std::vector<double>& b) {
  if (a.empty()) return b;
  if (b.empty()) return a;
  std::vector<double> p(13), c(13), o(13);
  for (size_t i = 0; i < 13; ++i) {
    p[i] = i < a.size() ? a[i] : 0;
    c[i] = i < b.size() ? b[i] : 0;
  }
  if (a.size() < 13) p[12] = 1;
  if (b.size() < 13) c[12] = 1;
  for (int r = 0; r < 3; ++r)
    for (int q = 0; q < 3; ++q)
      o[r * 3 + q] = p[r * 3] * c[q] + p[r * 3 + 1] * c[3 + q] + p[r * 3 + 2] * c[6 + q];
  for (int r = 0; r < 3; ++r)
    o[9 + r] = p[r * 3] * c[9] + p[r * 3 + 1] * c[10] + p[r * 3 + 2] * c[11] + p[9 + r];
  o[12] = p[12] * c[12];
  return o;
}
}  // namespace openskp
