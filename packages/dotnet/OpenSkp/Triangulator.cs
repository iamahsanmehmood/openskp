using System;
using System.Collections.Generic;

namespace OpenSkp
{
    /// <summary>Triangulates a planar face given as one or more vertex-ID
    /// loops (first loop is the outer boundary; any further loops are
    /// holes). Ported from the TypeScript reference implementation
    /// (triangulator.ts's triangulateFace3D): projects the 3D loop
    /// vertices onto the face's own plane using its normal, then runs
    /// Earcut (see Earcut.cs) on the flattened 2D coordinates - correctly
    /// handling concave outlines and holes, same as the Python/TypeScript
    /// ports.</summary>
    internal static class Triangulator
    {
        public static List<long[]> TriangulateFace3D(
            Dictionary<long, (double X, double Y, double Z)> vertices3d,
            List<List<long>> loops,
            (double X, double Y, double Z) normal)
        {
            if (loops.Count == 0) return new List<long[]>();

            // Trivial fast path for simple triangles and quads (no holes) -
            // identical to the reference implementation.
            if (loops.Count == 1 && loops[0].Count == 3)
            {
                return new List<long[]> { loops[0].ToArray() };
            }
            if (loops.Count == 1 && loops[0].Count == 4)
            {
                var v = loops[0];
                return new List<long[]>
                {
                    new[] { v[0], v[1], v[2] },
                    new[] { v[0], v[2], v[3] },
                };
            }

            double nx = normal.X, ny = normal.Y, nz = normal.Z;
            double normVal = Math.Sqrt(nx * nx + ny * ny + nz * nz);
            if (normVal > 1e-6)
            {
                nx /= normVal; ny /= normVal; nz /= normVal;
            }
            else
            {
                nx = 0; ny = 0; nz = 1;
            }

            double uAxisX = Math.Abs(nx) < 0.9 ? 1.0 : 0.0;
            double uAxisY = Math.Abs(nx) < 0.9 ? 0.0 : 1.0;
            double uAxisZ = 0.0;

            double ux = ny * uAxisZ - nz * uAxisY;
            double uy = nz * uAxisX - nx * uAxisZ;
            double uz = nx * uAxisY - ny * uAxisX;
            double uLen = Math.Sqrt(ux * ux + uy * uy + uz * uz);
            if (uLen < 1e-12)
            {
                ux = 1.0; uy = 0.0; uz = 0.0;
            }
            else
            {
                ux /= uLen; uy /= uLen; uz /= uLen;
            }

            double vx = ny * uz - nz * uy;
            double vy = nz * ux - nx * uz;
            double vz = nx * uy - ny * ux;
            double vLen = Math.Sqrt(vx * vx + vy * vy + vz * vz);
            if (vLen > 1e-12)
            {
                vx /= vLen; vy /= vLen; vz /= vLen;
            }

            var allVIds = new List<long>();
            var holeIndices = new List<int>();
            int currentOffset = 0;
            for (int l = 0; l < loops.Count; l++)
            {
                if (l > 0) holeIndices.Add(currentOffset);
                foreach (var vId in loops[l]) allVIds.Add(vId);
                currentOffset += loops[l].Count;
            }

            var flatCoords = new double[allVIds.Count * 2];
            for (int i = 0; i < allVIds.Count; i++)
            {
                if (!vertices3d.TryGetValue(allVIds[i], out var pt))
                {
                    return new List<long[]>(); // missing vertex
                }
                double u = pt.X * ux + pt.Y * uy + pt.Z * uz;
                double v = pt.X * vx + pt.Y * vy + pt.Z * vz;
                flatCoords[i * 2] = u;
                flatCoords[i * 2 + 1] = v;
            }

            List<int> triIndices;
            try
            {
                triIndices = Earcut.Triangulate(flatCoords, holeIndices.ToArray(), 2);
            }
            catch
            {
                // Fallback: simple fan triangulation of the outer loop, matching
                // the reference implementation's own fallback for a failed earcut.
                var outerLoop = loops[0];
                var fallback = new List<long[]>();
                for (int i = 1; i < outerLoop.Count - 1; i++)
                {
                    fallback.Add(new[] { outerLoop[0], outerLoop[i], outerLoop[i + 1] });
                }
                return fallback;
            }

            var result = new List<long[]>();
            for (int i = 0; i < triIndices.Count; i += 3)
            {
                result.Add(new[]
                {
                    allVIds[triIndices[i]],
                    allVIds[triIndices[i + 1]],
                    allVIds[triIndices[i + 2]],
                });
            }
            return result;
        }
    }
}
