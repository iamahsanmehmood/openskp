using System.Collections.Generic;
using System.Linq;
using Xunit;
using OpenSkp;

namespace OpenSkp.Tests
{
    /// <summary>
    /// Direct tests of Triangulator/Earcut against known shapes where a
    /// naive fan triangulation (this project's earlier approach) gives the
    /// wrong answer: a concave outline, and a polygon with a hole. Earcut
    /// (ported from the same library the TypeScript port already depends
    /// on) must handle both correctly.
    /// </summary>
    public class TriangulatorTests
    {
        private static double PolygonArea2D(List<(double X, double Y)> pts)
        {
            double sum = 0;
            for (int i = 0; i < pts.Count; i++)
            {
                var (x1, y1) = pts[i];
                var (x2, y2) = pts[(i + 1) % pts.Count];
                sum += x1 * y2 - x2 * y1;
            }
            return System.Math.Abs(sum) / 2.0;
        }

        private static double TrianglesArea(
            List<long[]> triangles, Dictionary<long, (double X, double Y, double Z)> vertices)
        {
            double total = 0;
            foreach (var tri in triangles)
            {
                var a = vertices[tri[0]];
                var b = vertices[tri[1]];
                var c = vertices[tri[2]];
                // 2D area (all z=0 in these tests) via cross product.
                double area = 0.5 * System.Math.Abs((b.X - a.X) * (c.Y - a.Y) - (c.X - a.X) * (b.Y - a.Y));
                total += area;
            }
            return total;
        }

        [Fact]
        public void ConcaveLShapeCoversExactAreaWithNoOverlap()
        {
            // An L-shaped hexagon (concave at one corner):
            // (0,0) (4,0) (4,2) (2,2) (2,4) (0,4) - area = 4*4 - 2*2 = 12.
            var vertices = new Dictionary<long, (double X, double Y, double Z)>
            {
                [0] = (0, 0, 0),
                [1] = (4, 0, 0),
                [2] = (4, 2, 0),
                [3] = (2, 2, 0),
                [4] = (2, 4, 0),
                [5] = (0, 4, 0),
            };
            var loops = new List<List<long>> { new List<long> { 0, 1, 2, 3, 4, 5 } };

            var triangles = Triangulator.TriangulateFace3D(vertices, loops, (0, 0, 1));

            Assert.Equal(4, triangles.Count); // n-2 triangles for a 6-gon
            double area = TrianglesArea(triangles, vertices);
            Assert.Equal(12.0, area, 6);

            // Every triangle must be non-degenerate and use only known vertices.
            foreach (var tri in triangles)
            {
                Assert.Equal(3, tri.Distinct().Count());
                foreach (var v in tri) Assert.True(vertices.ContainsKey(v));
            }
        }

        [Fact]
        public void SquareWithHoleExcludesHoleArea()
        {
            // Outer 10x10 square (0..10), inner 2x2 hole square (4..6) - area
            // must be 100 - 4 = 96, and no triangle may cover the hole's
            // interior (a fan-triangulation ignoring the hole would instead
            // cover the full 100).
            var vertices = new Dictionary<long, (double X, double Y, double Z)>
            {
                [0] = (0, 0, 0),
                [1] = (10, 0, 0),
                [2] = (10, 10, 0),
                [3] = (0, 10, 0),
                [10] = (4, 4, 0),
                [11] = (6, 4, 0),
                [12] = (6, 6, 0),
                [13] = (4, 6, 0),
            };
            var loops = new List<List<long>>
            {
                new List<long> { 0, 1, 2, 3 },
                new List<long> { 10, 11, 12, 13 },
            };

            var triangles = Triangulator.TriangulateFace3D(vertices, loops, (0, 0, 1));

            Assert.NotEmpty(triangles);
            double area = TrianglesArea(triangles, vertices);
            Assert.Equal(96.0, area, 6);

            // The hole's own vertices may appear (they bound the ring), but
            // no triangle may be entirely inside the hole (its centroid
            // must never land strictly inside the 4..6 square).
            foreach (var tri in triangles)
            {
                double cx = (vertices[tri[0]].X + vertices[tri[1]].X + vertices[tri[2]].X) / 3.0;
                double cy = (vertices[tri[0]].Y + vertices[tri[1]].Y + vertices[tri[2]].Y) / 3.0;
                bool centroidInHole = cx > 4 && cx < 6 && cy > 4 && cy < 6;
                Assert.False(centroidInHole);
            }
        }
    }
}
