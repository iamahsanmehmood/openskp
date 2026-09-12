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

            // 2D (u, v) projection for every loop, kept as parallel lists so
            // overlap detection below can work in plain (double,double)
            // coordinates without touching vertex IDs at all.
            var loops2d = new List<List<(double U, double V)>>();
            foreach (var loop in loops)
            {
                var pts = new List<(double, double)>(loop.Count);
                foreach (var vId in loop)
                {
                    if (!vertices3d.TryGetValue(vId, out var pt))
                    {
                        return new List<long[]>(); // missing vertex
                    }
                    double u = pt.X * ux + pt.Y * uy + pt.Z * uz;
                    double v = pt.X * vx + pt.Y * vy + pt.Z * vz;
                    pts.Add((u, v));
                }
                loops2d.Add(pts);
            }

            // Holes are supposed to be simple and mutually disjoint - that's
            // what Earcut's ring-based API assumes. A real file can carry
            // two hole loops that genuinely overlap (observed on a real
            // fixture: two ~0.69"-radius circles only 0.33" apart, evidently
            // meant as one slotted/oval cutout recorded as two separate full
            // circles). Feeding overlapping rings to Earcut is undefined -
            // ported from the same fix in the Python port (openskp#285):
            // rather than compute an explicit union boundary (which would
            // need a general polygon-clipping routine this project doesn't
            // otherwise need), fall back to triangulating the outer boundary
            // alone and discarding any triangle whose centroid falls inside
            // ANY hole - the same "triangulate then filter" strategy this
            // project's own Python port used before its earcut migration,
            // applied narrowly only when real overlap is detected. A no-op
            // for the overwhelming common case of genuinely disjoint holes.
            if (loops2d.Count > 2 && HolesOverlap(loops2d))
            {
                return TriangulateByFilteringHoles(loops, loops2d);
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
            int offset = 0;
            foreach (var pts in loops2d)
            {
                foreach (var (u, v) in pts)
                {
                    flatCoords[offset * 2] = u;
                    flatCoords[offset * 2 + 1] = v;
                    offset++;
                }
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

        /// <summary>True when at least one pair of hole loops (index 1+ in
        /// <paramref name="loops2d"/>) shares real area - not just a
        /// boundary point or edge, which is a normal, common pattern (e.g.
        /// two holes sharing a cut line). Detected via a vertex-containment
        /// test: for genuinely overlapping simple polygons (in particular
        /// the convex/circular holes real drilled geometry produces), the
        /// overlap region always contains at least one polygon's own
        /// vertex inside the other - a pathological overlap with no vertex
        /// crossing either boundary is possible in principle for very
        /// concave shapes but not observed in any real file so far.</summary>
        private static bool HolesOverlap(List<List<(double U, double V)>> loops2d)
        {
            for (int i = 1; i < loops2d.Count; i++)
            {
                for (int j = i + 1; j < loops2d.Count; j++)
                {
                    if (AnyVertexInside(loops2d[i], loops2d[j]) || AnyVertexInside(loops2d[j], loops2d[i]))
                    {
                        return true;
                    }
                }
            }
            return false;
        }

        private static bool AnyVertexInside(List<(double U, double V)> points, List<(double U, double V)> polygon)
        {
            foreach (var p in points)
            {
                if (PointInPolygon(p.U, p.V, polygon)) return true;
            }
            return false;
        }

        /// <summary>Standard ray-casting point-in-polygon test (even-odd
        /// rule) - true when (u, v) lies strictly inside the given closed
        /// 2D ring.</summary>
        private static bool PointInPolygon(double u, double v, List<(double U, double V)> polygon)
        {
            bool inside = false;
            int n = polygon.Count;
            for (int i = 0, j = n - 1; i < n; j = i++)
            {
                double ui = polygon[i].U, vi = polygon[i].V;
                double uj = polygon[j].U, vj = polygon[j].V;
                bool intersects = ((vi > v) != (vj > v)) &&
                    (u < (uj - ui) * (v - vi) / (vj - vi) + ui);
                if (intersects) inside = !inside;
            }
            return inside;
        }

        private static List<long[]> TriangulateByFilteringHoles(
            List<List<long>> loops, List<List<(double U, double V)>> loops2d)
        {
            var outerLoop = loops[0];
            var outer2d = loops2d[0];
            var flatOuter = new double[outer2d.Count * 2];
            for (int i = 0; i < outer2d.Count; i++)
            {
                flatOuter[i * 2] = outer2d[i].U;
                flatOuter[i * 2 + 1] = outer2d[i].V;
            }

            List<int> triIndices;
            try
            {
                triIndices = Earcut.Triangulate(flatOuter, Array.Empty<int>(), 2);
            }
            catch
            {
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
                int ia = triIndices[i], ib = triIndices[i + 1], ic = triIndices[i + 2];
                double cu = (outer2d[ia].U + outer2d[ib].U + outer2d[ic].U) / 3.0;
                double cv = (outer2d[ia].V + outer2d[ib].V + outer2d[ic].V) / 3.0;

                bool insideAnyHole = false;
                for (int h = 1; h < loops2d.Count; h++)
                {
                    if (PointInPolygon(cu, cv, loops2d[h])) { insideAnyHole = true; break; }
                }
                if (insideAnyHole) continue;

                result.Add(new[] { outerLoop[ia], outerLoop[ib], outerLoop[ic] });
            }
            return result;
        }
    }
}
