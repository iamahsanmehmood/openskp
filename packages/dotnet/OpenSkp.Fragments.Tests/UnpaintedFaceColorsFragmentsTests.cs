using System.Collections.Generic;
using System.Linq;
using Xunit;
using OpenSkp;

namespace OpenSkp.Fragments.Tests
{
    public class UnpaintedFaceColorsFragmentsTests
    {
        [Fact]
        public void FragmentsKeepOneCopyOfEachUnpaintedFace()
        {
            var builder = SkpCreate.NewFile();
            var pts = new List<(double, double, double)>();
            foreach (double z in new[] { 0.0, 10.0 })
                foreach (double y in new[] { 0.0, 10.0 })
                    foreach (double x in new[] { 0.0, 10.0 })
                        pts.Add((x, y, z));
            foreach (var (a, b, c, d) in new[]
            {
                (0, 2, 3, 1), (4, 5, 7, 6), (0, 1, 5, 4),
                (1, 3, 7, 5), (3, 2, 6, 7), (2, 0, 4, 6),
            })
            {
                builder.AddFace(new[] { pts[a], pts[b], pts[c], pts[d] });
            }
            var bytes = builder.ToBytes();

            static int Triangles(InstancedScene scene)
            {
                var roundTrip = FragmentsExport.FromFragments(FragmentsExport.ToFragments(scene));
                return roundTrip.MeshResources.Sum(r => r.Primitives.Sum(p => p.Indices.Length / 3));
            }

            Assert.Equal(12, Triangles(SkpFile.BuildInstancedScene(bytes)));
            Assert.Equal(
                12,
                Triangles(SkpFile.BuildInstancedScene(bytes, new SkpParseOptions { UnpaintedFaces = "tag" })));
        }
    }
}
