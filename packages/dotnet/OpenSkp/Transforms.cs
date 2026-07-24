using System;
using System.Collections.Generic;

namespace OpenSkp
{
    /// <summary>3D point/matrix helpers for scene baking. Ported from
    /// Python's _core.py (transform_point / multiply_matrices) - matrices
    /// are SketchUp's 13-element [3x3 rotation/scale | translation | 1.0]
    /// layout unless noted.</summary>
    internal static class Transforms
    {
        public static (double X, double Y, double Z) TransformPoint(double[] matrix, (double X, double Y, double Z) point)
        {
            if (matrix == null || matrix.Length < 12)
            {
                return point;
            }
            double x = point.X, y = point.Y, z = point.Z;
            double tx = matrix[0] * x + matrix[1] * y + matrix[2] * z + matrix[9];
            double ty = matrix[3] * x + matrix[4] * y + matrix[5] * z + matrix[10];
            double tz = matrix[6] * x + matrix[7] * y + matrix[8] * z + matrix[11];
            return (tx, ty, tz);
        }

        public static List<double> MultiplyMatrices(List<double> parent, List<double> child)
        {
            if (parent == null || parent.Count == 0) return child;
            if (child == null || child.Count == 0) return parent;

            double[] p = Pad13(parent);
            double[] c = Pad13(child);

            double[] pR0 = { p[0], p[1], p[2], p[9] };
            double[] pR1 = { p[3], p[4], p[5], p[10] };
            double[] pR2 = { p[6], p[7], p[8], p[11] };

            double[] cC0 = { c[0], c[3], c[6], 0 };
            double[] cC1 = { c[1], c[4], c[7], 0 };
            double[] cC2 = { c[2], c[5], c[8], 0 };
            double[] cC3 = { c[9], c[10], c[11], 1 };

            static double Dot(double[] row, double[] col) =>
                row[0] * col[0] + row[1] * col[1] + row[2] * col[2] + row[3] * col[3];

            var outArr = new double[13];
            outArr[0] = Dot(pR0, cC0);
            outArr[1] = Dot(pR0, cC1);
            outArr[2] = Dot(pR0, cC2);
            outArr[3] = Dot(pR1, cC0);
            outArr[4] = Dot(pR1, cC1);
            outArr[5] = Dot(pR1, cC2);
            outArr[6] = Dot(pR2, cC0);
            outArr[7] = Dot(pR2, cC1);
            outArr[8] = Dot(pR2, cC2);
            outArr[9] = Dot(pR0, cC3);
            outArr[10] = Dot(pR1, cC3);
            outArr[11] = Dot(pR2, cC3);
            outArr[12] = p[12] * c[12];
            return new List<double>(outArr);
        }

        private static double[] Pad13(List<double> m)
        {
            var arr = new double[13];
            for (int i = 0; i < 13; i++)
            {
                arr[i] = i < m.Count ? m[i] : 0.0;
            }
            if (m.Count < 13) arr[12] = 1.0;
            return arr;
        }
    }
}
