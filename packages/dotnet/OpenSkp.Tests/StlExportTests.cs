using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using OpenSkp;
using Xunit;

namespace OpenSkp.Tests
{
    public class StlExportTests
    {
        private static readonly (double X, double Y, double Z)[][] BoxFaces = new[]
        {
            new[] { (0.0, 0.0, 0.0), (0.0, 10.0, 0.0), (10.0, 10.0, 0.0), (10.0, 0.0, 0.0) },
            new[] { (0.0, 0.0, 10.0), (10.0, 0.0, 10.0), (10.0, 10.0, 10.0), (0.0, 10.0, 10.0) },
            new[] { (0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (10.0, 0.0, 10.0), (0.0, 0.0, 10.0) },
            new[] { (10.0, 0.0, 0.0), (10.0, 10.0, 0.0), (10.0, 10.0, 10.0), (10.0, 0.0, 10.0) },
            new[] { (10.0, 10.0, 0.0), (0.0, 10.0, 0.0), (0.0, 10.0, 10.0), (10.0, 10.0, 10.0) },
            new[] { (0.0, 10.0, 0.0), (0.0, 0.0, 0.0), (0.0, 0.0, 10.0), (0.0, 10.0, 10.0) },
        };

        private static byte[] BuildBoxBytes(string paint)
        {
            var builder = new SkpBuilder();
            int red = builder.AddMaterial("Red", (255, 0, 0, 255));
            int blue = builder.AddMaterial("Blue", (0, 0, 255, 255));
            for (int i = 0; i < BoxFaces.Length; i++)
            {
                int? front = null;
                int? back = null;
                if (paint == "one_face")
                {
                    if (i == 0) front = red;
                }
                else
                {
                    if (paint == "front" || paint == "both" || paint == "mixed") front = red;
                    if (paint == "back" || paint == "both" || paint == "mixed") back = blue;
                    if (paint == "mixed" && i % 2 == 1)
                    {
                        (front, back) = (back, front);
                    }
                }
                builder.AddFace(BoxFaces[i], material: front, backMaterial: back);
            }
            return builder.ToBytes();
        }

        private static List<((double X, double Y, double Z) V0, (double X, double Y, double Z) V1, (double X, double Y, double Z) V2)> ParseAsciiStl(string text)
        {
            var verts = new List<(double X, double Y, double Z)>();
            foreach (var rawLine in text.Split('\n'))
            {
                var line = rawLine.Trim();
                if (line.StartsWith("vertex ", StringComparison.Ordinal))
                {
                    var parts = line.Split(new[] { ' ', '\t' }, StringSplitOptions.RemoveEmptyEntries);
                    verts.Add((
                        double.Parse(parts[1], CultureInfo.InvariantCulture),
                        double.Parse(parts[2], CultureInfo.InvariantCulture),
                        double.Parse(parts[3], CultureInfo.InvariantCulture)));
                }
            }
            var tris = new List<((double, double, double), (double, double, double), (double, double, double))>();
            for (int i = 0; i + 2 < verts.Count; i += 3)
            {
                tris.Add((verts[i], verts[i + 1], verts[i + 2]));
            }
            return tris;
        }

        private static List<((double X, double Y, double Z) V0, (double X, double Y, double Z) V1, (double X, double Y, double Z) V2)> ParseBinaryStl(byte[] data)
        {
            uint count = BitConverter.ToUInt32(data, 80);
            Assert.Equal(84 + (int)count * 50, data.Length);
            var tris = new List<((double, double, double), (double, double, double), (double, double, double))>((int)count);
            for (int i = 0; i < (int)count; i++)
            {
                int b = 84 + i * 50 + 12;
                tris.Add((
                    (BitConverter.ToSingle(data, b), BitConverter.ToSingle(data, b + 4), BitConverter.ToSingle(data, b + 8)),
                    (BitConverter.ToSingle(data, b + 12), BitConverter.ToSingle(data, b + 16), BitConverter.ToSingle(data, b + 20)),
                    (BitConverter.ToSingle(data, b + 24), BitConverter.ToSingle(data, b + 28), BitConverter.ToSingle(data, b + 32))));
            }
            return tris;
        }

        private static void AssertClosed10InBox(
            List<((double X, double Y, double Z) V0, (double X, double Y, double Z) V1, (double X, double Y, double Z) V2)> tris)
        {
            Assert.Equal(12, tris.Count);
            static string VKey((double X, double Y, double Z) p) =>
                $"{Math.Round(p.X * 1e6)},{Math.Round(p.Y * 1e6)},{Math.Round(p.Z * 1e6)}";

            var edgeCounts = new Dictionary<string, int>();
            double volM3 = 0.0;
            foreach (var (v0, v1, v2) in tris)
            {
                double cx = v1.Y * v2.Z - v1.Z * v2.Y;
                double cy = v1.Z * v2.X - v1.X * v2.Z;
                double cz = v1.X * v2.Y - v1.Y * v2.X;
                volM3 += (v0.X * cx + v0.Y * cy + v0.Z * cz) / 6.0;

                var keys = new[] { VKey(v0), VKey(v1), VKey(v2) };
                for (int e = 0; e < 3; e++)
                {
                    var a = keys[e];
                    var b = keys[(e + 1) % 3];
                    var ek = string.CompareOrdinal(a, b) < 0 ? $"{a}|{b}" : $"{b}|{a}";
                    edgeCounts[ek] = edgeCounts.TryGetValue(ek, out var prev) ? prev + 1 : 1;
                }
            }

            double volCm3 = volM3 * 1e6;
            Assert.Equal(16387.1, Math.Round(volCm3, 1));
            Assert.InRange(volCm3, 16387.01, 16387.12);
            Assert.Equal(18, edgeCounts.Count);
            Assert.All(edgeCounts.Values, c => Assert.Equal(2, c));
        }

        [Fact]
        public void ToStlAscii_SerializesSceneToStlText()
        {
            var scene = new Scene
            {
                SceneHierarchy = new InstanceNode { Name = "Root", DefinitionName = "RootDef" },
                GlbPrimitives = new List<GlbPrimitive>
                {
                    new GlbPrimitive
                    {
                        GeomName = "Box",
                        MaterialIndex = 0,
                        Positions = new float[] { 0f, 0f, 0f, 1f, 0f, 0f, 0f, 1f, 0f },
                        Normals = new float[] { 0f, 0f, 1f, 0f, 0f, 1f, 0f, 0f, 1f },
                        Uvs = new float[] { 0f, 0f, 1f, 0f, 0f, 1f },
                        Indices = new uint[] { 0, 1, 2 }
                    }
                }
            };

            string stlText = StlExport.ToStlAscii(scene);
            Assert.Contains("solid OpenSKP_Model", stlText);
            Assert.Contains("facet normal 0.000000 0.000000 1.000000", stlText);
            Assert.Contains("vertex 0.000000 0.000000 0.000000", stlText);
            Assert.Contains("endsolid OpenSKP_Model", stlText);
            Assert.DoesNotContain('\r', stlText);
        }

        [Fact]
        public void ToStlBinary_SerializesSceneToBinaryStlData()
        {
            var scene = new Scene
            {
                SceneHierarchy = new InstanceNode { Name = "Root", DefinitionName = "RootDef" },
                GlbPrimitives = new List<GlbPrimitive>
                {
                    new GlbPrimitive
                    {
                        GeomName = "Box",
                        MaterialIndex = 0,
                        Positions = new float[] { 0f, 0f, 0f, 1f, 0f, 0f, 0f, 1f, 0f },
                        Normals = new float[] { 0f, 0f, 1f, 0f, 0f, 1f, 0f, 0f, 1f },
                        Uvs = new float[] { 0f, 0f, 1f, 0f, 0f, 1f },
                        Indices = new uint[] { 0, 1, 2 }
                    }
                }
            };

            byte[] data = StlExport.ToStlBinary(scene);
            Assert.Equal(80 + 4 + 50, data.Length);
        }

        [Theory]
        [InlineData("unpainted", false)]
        [InlineData("unpainted", true)]
        [InlineData("one_face", false)]
        [InlineData("one_face", true)]
        [InlineData("front", false)]
        [InlineData("front", true)]
        [InlineData("back", false)]
        [InlineData("back", true)]
        [InlineData("both", false)]
        [InlineData("both", true)]
        [InlineData("mixed", false)]
        [InlineData("mixed", true)]
        public void StlExport_OmitsBackTriangles_Produces12TrianglesAndVolume(string paint, bool binary)
        {
            var scene = SkpFile.BuildScene(BuildBoxBytes(paint));
            var tris = binary
                ? ParseBinaryStl(StlExport.ToStlBinary(scene))
                : ParseAsciiStl(StlExport.ToStlAscii(scene));
            AssertClosed10InBox(tris);
        }

        [Fact]
        public void ColorExportsAndInstancedScene_KeepBothPaintedSidesAndRecordBackTriangles()
        {
            var bytes = BuildBoxBytes("both");
            var baked = SkpFile.BuildScene(bytes);
            var instanced = SkpFile.BuildInstancedScene(bytes);

            Assert.Equal(24, baked.GlbPrimitives.Sum(p => p.Indices.Length / 3));
            Assert.Equal(12, baked.GlbPrimitives.Sum(p => p.BackTriangles.Length));
            Assert.Equal(12, instanced.MeshResources.SelectMany(r => r.Primitives).Sum(p => p.BackTriangles.Length));

            var ply = PlyExport.ToPlyAscii(baked);
            Assert.Contains("element face 24", ply);
            Assert.Contains("255 0 0 255", ply);
            Assert.Contains("0 0 255 255", ply);
        }
    }
}
