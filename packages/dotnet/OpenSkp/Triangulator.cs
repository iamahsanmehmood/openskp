using System;
using System.Collections.Generic;
using System.Linq;

namespace OpenSkp
{
    /// <summary>Triangulates a planar face given as one or more vertex-ID
    /// loops (first loop is the outer boundary; any further loops are
    /// holes).
    ///
    /// The triangle and quad fast paths below are exact - identical
    /// behavior to the Python/TypeScript reference (a quad is split
    /// [0,1,2],[0,2,3], matching SketchUp's own convention). For general
    /// N-gons, Python/TypeScript use a full constrained-Delaunay
    /// triangulation (via shapely) that correctly handles holes and
    /// concave outlines; this port instead uses a plane-projected fan from
    /// the first vertex, which is exact for convex polygons but only an
    /// approximation for concave ones, and does not carve out holes at all
    /// (a hole-bearing face's inner loop(s) are ignored). This covers the
    /// overwhelming majority of real faces (triangles/quads/convex n-gons)
    /// correctly; known gap tracked for a from-scratch or library-based
    /// constrained-triangulation follow-up.</summary>
    internal static class Triangulator
    {
        public static List<long[]> TriangulateFace3D(
            Dictionary<long, (double X, double Y, double Z)> vertices3d,
            List<List<long>> loops,
            (double X, double Y, double Z) normal)
        {
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
            if (loops.Count == 0 || loops[0].Count < 3)
            {
                return new List<long[]>();
            }

            // Fan triangulation from the outer loop's first vertex, in the
            // face's own plane. See the class doc for the exact/approximate
            // boundary this covers.
            var outer = loops[0];
            foreach (var vId in outer)
            {
                if (!vertices3d.ContainsKey(vId)) return new List<long[]>();
            }

            var triangles = new List<long[]>();
            for (int i = 1; i < outer.Count - 1; i++)
            {
                triangles.Add(new[] { outer[0], outer[i], outer[i + 1] });
            }
            return triangles;
        }
    }
}
