/// 3D point/matrix helpers for scene baking. Ported from Python's
/// _core.py (transform_point / multiply_matrices) - matrices are
/// SketchUp's 13-element [3x3 rotation/scale | translation | 1.0] layout
/// unless noted.
class Transforms {
  static (double, double, double) transformPoint(List<double> matrix, (double, double, double) point) {
    if (matrix.length < 12) return point;
    final (x, y, z) = point;
    final tx = matrix[0] * x + matrix[1] * y + matrix[2] * z + matrix[9];
    final ty = matrix[3] * x + matrix[4] * y + matrix[5] * z + matrix[10];
    final tz = matrix[6] * x + matrix[7] * y + matrix[8] * z + matrix[11];
    return (tx, ty, tz);
  }

  static List<double> multiplyMatrices(List<double> parent, List<double> child) {
    if (parent.isEmpty) return child;
    if (child.isEmpty) return parent;

    final p = _pad13(parent);
    final c = _pad13(child);

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
    out[12] = p[12] * c[12];
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
