/// 3D point/matrix helpers for scene baking. Ported from Python's
/// _core.py (transform_point / multiply_matrices) - matrices are
/// SketchUp's 13-element [3x3 rotation/scale | translation | 1.0] layout
/// unless noted.
class Transforms {
  /// Fold a 13-value instance matrix's scale term (index 12) into its
  /// rotation and translation, so index 12 is 1.
  ///
  /// SketchUp can keep a uniform scale in the transformation's homogeneous
  /// term: the 3x3 stays as it is, the translation is in the scaled units,
  /// and the 16th value `w` is the divisor (`Transformation#to_a`'s w = 100
  /// means 1/100). The 13-value matrix stores `1/w` at index 12, so a point
  /// transforms as `s * (R p + t)` with `s = m[12]`; folding gives the
  /// equivalent `(s R) p + s t` with `s = 1`, which every consumer that reads
  /// only indices 0-11 handles correctly (openskp#444). A matrix whose scale
  /// is already 1 - everything SketchUp writes except that case - is
  /// returned unchanged.
  static List<double> foldScale(List<double> m) {
    if (m.length < 13 || m[12] == 1.0) return m;
    final s = m[12];
    return [for (var i = 0; i < 12; i++) m[i] * s, 1.0];
  }

  static (double, double, double) transformPoint(List<double> matrix, (double, double, double) point) {
    if (matrix.length < 12) return point;
    matrix = foldScale(matrix);
    final (x, y, z) = point;
    final tx = matrix[0] * x + matrix[1] * y + matrix[2] * z + matrix[9];
    final ty = matrix[3] * x + matrix[4] * y + matrix[5] * z + matrix[10];
    final tz = matrix[6] * x + matrix[7] * y + matrix[8] * z + matrix[11];
    return (tx, ty, tz);
  }

  static List<double> multiplyMatrices(List<double> parent, List<double> child) {
    if (parent.isEmpty) return child.isEmpty ? child : foldScale(_pad13(child));
    if (child.isEmpty) return foldScale(_pad13(parent));

    // Both operands' scale terms are folded in first, so the product's own is 1.
    final p = foldScale(_pad13(parent));
    final c = foldScale(_pad13(child));

    final pR0 = [p[0], p[1], p[2], p[9]];
    final pR1 = [p[3], p[4], p[5], p[10]];
    final pR2 = [p[6], p[7], p[8], p[11]];

    final cC0 = [c[0], c[3], c[6], 0.0];
    final cC1 = [c[1], c[4], c[7], 0.0];
    final cC2 = [c[2], c[5], c[8], 0.0];
    final cC3 = [c[9], c[10], c[11], 1.0];

    double dot(List<double> row, List<double> col) => row[0] * col[0] + row[1] * col[1] + row[2] * col[2] + row[3] * col[3];

    final out = List<double>.filled(13, 0.0);
    out[0] = dot(pR0, cC0);
    out[1] = dot(pR0, cC1);
    out[2] = dot(pR0, cC2);
    out[3] = dot(pR1, cC0);
    out[4] = dot(pR1, cC1);
    out[5] = dot(pR1, cC2);
    out[6] = dot(pR2, cC0);
    out[7] = dot(pR2, cC1);
    out[8] = dot(pR2, cC2);
    out[9] = dot(pR0, cC3);
    out[10] = dot(pR1, cC3);
    out[11] = dot(pR2, cC3);
    out[12] = 1.0; // both operands were folded (index 12 is 1)
    return out;
  }

  static List<double> _pad13(List<double> m) {
    final arr = List<double>.filled(13, 0.0);
    for (int i = 0; i < 13; i++) {
      arr[i] = i < m.length ? m[i] : 0.0;
    }
    if (m.length < 13) arr[12] = 1.0;
    return arr;
  }
}
