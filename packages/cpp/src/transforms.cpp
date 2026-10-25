#include "internal.hpp"

namespace openskp {
double transform_determinant(const std::vector<double>& m) {
  if (m.size() < 9) return 1.0;
  return m[0] * (m[4] * m[8] - m[5] * m[7]) - m[1] * (m[3] * m[8] - m[5] * m[6]) +
         m[2] * (m[3] * m[7] - m[4] * m[6]);
}

// Fold a 13-value instance matrix's scale term (index 12) into its rotation
// and translation, so index 12 is 1.
//
// SketchUp can keep a uniform scale in the transformation's homogeneous term:
// the 3x3 stays as it is, the translation is in the scaled units, and the 16th
// value `w` is the divisor (`Transformation#to_a`'s w = 100 means 1/100). The
// 13-value matrix stores `1/w` at index 12, so a point transforms as
// `s * (R p + t)` with `s = m[12]`; folding gives the equivalent
// `(s R) p + s t` with `s = 1`, which every consumer that reads only indices
// 0-11 handles correctly (openskp#444). A matrix whose scale is already 1 -
// everything SketchUp writes except that case - is returned unchanged.
std::vector<double> fold_scale(const std::vector<double>& m) {
  if (m.size() < 13 || m[12] == 1.0) return m;
  std::vector<double> o(13);
  for (size_t i = 0; i < 12; ++i) o[i] = m[i] * m[12];
  o[12] = 1.0;
  return o;
}

std::array<double, 3> transform_point(const std::vector<double>& matrix,
                                      const std::array<double, 3>& p) {
  if (matrix.size() < 12) return p;
  const auto m = fold_scale(matrix);
  return {m[0] * p[0] + m[1] * p[1] + m[2] * p[2] + m[9],
          m[3] * p[0] + m[4] * p[1] + m[5] * p[2] + m[10],
          m[6] * p[0] + m[7] * p[1] + m[8] * p[2] + m[11]};
}

std::array<double, 3> transform_normal(const std::vector<double>& m,
                                       const std::array<double, 3>& n) {
  if (m.size() < 9) return n;
  const auto determinant = transform_determinant(m);
  if (determinant == 0.0) {
    return {m[0] * n[0] + m[1] * n[1] + m[2] * n[2], m[3] * n[0] + m[4] * n[1] + m[5] * n[2],
            m[6] * n[0] + m[7] * n[1] + m[8] * n[2]};
  }
  const auto sign = determinant < 0.0 ? -1.0 : 1.0;
  return {sign * ((m[4] * m[8] - m[5] * m[7]) * n[0] + (m[5] * m[6] - m[3] * m[8]) * n[1] +
                  (m[3] * m[7] - m[4] * m[6]) * n[2]),
          sign * ((m[2] * m[7] - m[1] * m[8]) * n[0] + (m[0] * m[8] - m[2] * m[6]) * n[1] +
                  (m[1] * m[6] - m[0] * m[7]) * n[2]),
          sign * ((m[1] * m[5] - m[2] * m[4]) * n[0] + (m[2] * m[3] - m[0] * m[5]) * n[1] +
                  (m[0] * m[4] - m[1] * m[3]) * n[2])};
}

std::vector<double> multiply_matrices(const std::vector<double>& a, const std::vector<double>& b) {
  if (a.empty()) return b.size() == 13 ? fold_scale(b) : b;
  if (b.empty()) return a.size() == 13 ? fold_scale(a) : a;
  std::vector<double> p(13), c(13), o(13);
  for (size_t i = 0; i < 13; ++i) {
    p[i] = i < a.size() ? a[i] : 0;
    c[i] = i < b.size() ? b[i] : 0;
  }
  if (a.size() < 13) p[12] = 1;
  if (b.size() < 13) c[12] = 1;
  // Both operands' scale terms are folded in first, so the product's own is 1.
  p = fold_scale(p);
  c = fold_scale(c);
  for (int r = 0; r < 3; ++r)
    for (int q = 0; q < 3; ++q)
      o[r * 3 + q] = p[r * 3] * c[q] + p[r * 3 + 1] * c[3 + q] + p[r * 3 + 2] * c[6 + q];
  for (int r = 0; r < 3; ++r)
    o[9 + r] = p[r * 3] * c[9] + p[r * 3 + 1] * c[10] + p[r * 3 + 2] * c[11] + p[9 + r];
  o[12] = 1.0;  // both operands were folded (index 12 is 1)
  return o;
}
}  // namespace openskp
