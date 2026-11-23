using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.Json;
using Xunit;
using OpenSkp;

namespace OpenSkp.Tests
{
    public class UnpaintedFaceColorsTests
    {
        private static readonly (int R, int G, int B) Front = (11, 122, 233);
        private static readonly (int R, int G, int B) Back = (201, 102, 3);

        private static string FixturePath(string name) =>
            Path.Combine(AppContext.BaseDirectory, "fixtures", name);

        public static IEnumerable<object[]> Versions()
        {
            foreach (var v in new[] { "v4", "v5", "v6", "2016", "2017", "2018", "2019", "2020", "2021" })
            {
                yield return new object[] { v };
            }
        }

        private static (int R, int G, int B) RgbOf(IReadOnlyList<object> gltfMaterials, int matIdx)
        {
            var mat = (IDictionary<string, object>)gltfMaterials[matIdx];
            var pbr = (IDictionary<string, object>)mat["pbrMetallicRoughness"];
            var factor = (double[])pbr["baseColorFactor"];
            return (
                (int)Math.Round(factor[0] * 255.0),
                (int)Math.Round(factor[1] * 255.0),
                (int)Math.Round(factor[2] * 255.0)
            );
        }

        private static bool DoubleSidedOf(IReadOnlyList<object> gltfMaterials, int matIdx)
        {
            var mat = (IDictionary<string, object>)gltfMaterials[matIdx];
            return mat.TryGetValue("doubleSided", out var ds) && ds is bool b && b;
        }

        private readonly struct Pt : IEquatable<Pt>
        {
            public readonly double X, Y, Z;
            public Pt(double x, double y, double z)
            {
                X = Math.Round(x, 5);
                Y = Math.Round(y, 5);
                Z = Math.Round(z, 5);
            }
            public bool Equals(Pt other) => X == other.X && Y == other.Y && Z == other.Z;
            public override bool Equals(object? obj) => obj is Pt p && Equals(p);
            public override int GetHashCode() => HashCode.Combine(X, Y, Z);
        }

        private static List<(Pt A, Pt B, Pt C)> Corners(float[] positions, uint[] indices)
        {
            var pts = new List<Pt>(positions.Length / 3);
            for (int i = 0; i < positions.Length; i += 3)
            {
                pts.Add(new Pt(positions[i], positions[i + 1], positions[i + 2]));
            }
            var tris = new List<(Pt, Pt, Pt)>(indices.Length / 3);
            for (int t = 0; t < indices.Length; t += 3)
            {
                tris.Add((pts[(int)indices[t]], pts[(int)indices[t + 1]], pts[(int)indices[t + 2]]));
            }
            return tris;
        }

        private static bool SameWinding((Pt A, Pt B, Pt C) a, (Pt A, Pt B, Pt C) b) =>
            (a.A.Equals(b.A) && a.B.Equals(b.B) && a.C.Equals(b.C)) ||
            (a.B.Equals(b.A) && a.C.Equals(b.B) && a.A.Equals(b.C)) ||
            (a.C.Equals(b.A) && a.A.Equals(b.B) && a.B.Equals(b.C));

        private static List<((int R, int G, int B) Rgb, bool DoubleSided)> GlbMaterials(byte[] glbBytes)
        {
            uint jsonLen = BitConverter.ToUInt32(glbBytes, 12);
            using var doc = JsonDocument.Parse(Encoding.UTF8.GetString(glbBytes, 20, (int)jsonLen));
            var list = new List<((int R, int G, int B), bool)>();
            foreach (var mat in doc.RootElement.GetProperty("materials").EnumerateArray())
            {
                var factor = mat.GetProperty("pbrMetallicRoughness").GetProperty("baseColorFactor");
                var rgb = (
                    (int)Math.Round(factor[0].GetDouble() * 255.0),
                    (int)Math.Round(factor[1].GetDouble() * 255.0),
                    (int)Math.Round(factor[2].GetDouble() * 255.0)
                );
                bool ds = mat.TryGetProperty("doubleSided", out var dsProp) && dsProp.GetBoolean();
                list.Add((rgb, ds));
            }
            return list.OrderBy(x => x.Item1.Item1).ThenBy(x => x.Item1.Item2).ThenBy(x => x.Item1.Item3).ToList();
        }

        private static byte[] BuildBoxBytes()
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
            return builder.ToBytes();
        }

        [Theory]
        [MemberData(nameof(Versions))]
        public void EachSideShowsTheModelsFaceColour(string version)
        {
            var path = FixturePath($"face_colors_{version}.skp");
            var baked = SkpFile.BuildScene(path);
            var instanced = SkpFile.BuildInstancedScene(path);

            Assert.Equal(2, baked.GlbPrimitives.Count);
            var bakedSorted = baked.GlbPrimitives.OrderBy(p => p.UnpaintedBack).ToList();
            Assert.False(bakedSorted[0].UnpaintedBack);
            Assert.True(bakedSorted[1].UnpaintedBack);
            Assert.Equal(Front, RgbOf(baked.GltfMaterials, bakedSorted[0].MaterialIndex));
            Assert.Equal(Back, RgbOf(baked.GltfMaterials, bakedSorted[1].MaterialIndex));
            Assert.False(DoubleSidedOf(baked.GltfMaterials, bakedSorted[0].MaterialIndex));
            Assert.False(DoubleSidedOf(baked.GltfMaterials, bakedSorted[1].MaterialIndex));

            var fronts = Corners(bakedSorted[0].Positions, bakedSorted[0].Indices);
            var backs = Corners(bakedSorted[1].Positions, bakedSorted[1].Indices);
            Assert.NotEmpty(fronts);
            Assert.Equal(fronts.Count, backs.Count);
            for (int i = 0; i < fronts.Count; i++)
            {
                var setA = new HashSet<Pt> { fronts[i].A, fronts[i].B, fronts[i].C };
                var setB = new HashSet<Pt> { backs[i].A, backs[i].B, backs[i].C };
                Assert.Equal(setA, setB);
                Assert.False(SameWinding(fronts[i], backs[i]));
            }

            var instPrims = instanced.MeshResources.SelectMany(r => r.Primitives).OrderBy(p => p.UnpaintedBack).ToList();
            Assert.Equal(2, instPrims.Count);
            Assert.False(instPrims[0].UnpaintedBack);
            Assert.True(instPrims[1].UnpaintedBack);
            Assert.Equal(Front, RgbOf(instanced.GltfMaterials, instPrims[0].MaterialIndex));
            Assert.Equal(Back, RgbOf(instanced.GltfMaterials, instPrims[1].MaterialIndex));
            Assert.False(DoubleSidedOf(instanced.GltfMaterials, instPrims[0].MaterialIndex));
            Assert.False(DoubleSidedOf(instanced.GltfMaterials, instPrims[1].MaterialIndex));
        }

        [Theory]
        [InlineData("v4")]
        [InlineData("2017")]
        [InlineData("2021")]
        public void TagOptionIsTheOldBehaviour(string version)
        {
            var path = FixturePath($"face_colors_{version}.skp");
            var layer = SkpFile.Open(path).Layers[0];
            var layerRgb = (layer.ColorR, layer.ColorG, layer.ColorB);
            var opts = new SkpParseOptions { UnpaintedFaces = "tag" };

            var baked = SkpFile.BuildScene(path, opts);
            var bakedPrim = Assert.Single(baked.GlbPrimitives);
            Assert.Equal(layerRgb, RgbOf(baked.GltfMaterials, bakedPrim.MaterialIndex));
            Assert.True(DoubleSidedOf(baked.GltfMaterials, bakedPrim.MaterialIndex));
            Assert.False(bakedPrim.UnpaintedBack);

            var instanced = SkpFile.BuildInstancedScene(path, opts);
            var instPrim = Assert.Single(instanced.MeshResources.SelectMany(r => r.Primitives));
            Assert.Equal(layerRgb, RgbOf(instanced.GltfMaterials, instPrim.MaterialIndex));
            Assert.True(DoubleSidedOf(instanced.GltfMaterials, instPrim.MaterialIndex));
            Assert.False(instPrim.UnpaintedBack);
        }

        [Fact]
        public void FileWithoutFaceColoursUsesTagColour()
        {
            var path = FixturePath("legacy_v3_synthetic.skp");
            Assert.Null(SkpFile.Open(path).FaceFrontColor);

            var styleBaked = SkpFile.BuildScene(path);
            var tagBaked = SkpFile.BuildScene(path, new SkpParseOptions { UnpaintedFaces = "tag" });
            Assert.Equal(
                tagBaked.GlbPrimitives.Select(p => RgbOf(tagBaked.GltfMaterials, p.MaterialIndex)).ToList(),
                styleBaked.GlbPrimitives.Select(p => RgbOf(styleBaked.GltfMaterials, p.MaterialIndex)).ToList());
            Assert.DoesNotContain(styleBaked.GlbPrimitives, p => p.UnpaintedBack);

            var styleInst = SkpFile.BuildInstancedScene(path);
            var tagInst = SkpFile.BuildInstancedScene(path, new SkpParseOptions { UnpaintedFaces = "tag" });
            Assert.Equal(
                tagInst.MeshResources.SelectMany(r => r.Primitives).Select(p => RgbOf(tagInst.GltfMaterials, p.MaterialIndex)).ToList(),
                styleInst.MeshResources.SelectMany(r => r.Primitives).Select(p => RgbOf(styleInst.GltfMaterials, p.MaterialIndex)).ToList());
            Assert.DoesNotContain(styleInst.MeshResources.SelectMany(r => r.Primitives), p => p.UnpaintedBack);
        }

        [Fact]
        public void UnknownOptionThrowsArgumentException()
        {
            var path = FixturePath("face_colors_2017.skp");
            var opts = new SkpParseOptions { UnpaintedFaces = "layer" };
            var ex1 = Assert.Throws<ArgumentException>(() => SkpFile.BuildScene(path, opts));
            Assert.Contains("unpaintedFaces", ex1.Message, StringComparison.OrdinalIgnoreCase);
            var ex2 = Assert.Throws<ArgumentException>(() => SkpFile.BuildInstancedScene(path, opts));
            Assert.Contains("unpaintedFaces", ex2.Message, StringComparison.OrdinalIgnoreCase);
        }

        [Fact]
        public void PaintWinsOverFaceColours()
        {
            var builder = SkpCreate.NewFile();
            int red = builder.AddMaterial("Red", (200, 10, 10, 255));
            var square = new (double, double, double)[] { (0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0) };
            ComponentDefinitionBuilder comp;
            using (comp = builder.AddComponentDefinition("Painted"))
            {
                comp.AddFace(square.Select(p => (p.Item1, p.Item2, 9.0)).ToArray());
            }
            builder.AddFace(square, material: red);
            builder.AddFace(square.Select(p => (p.Item1, p.Item2, 5.0)).ToArray(), backMaterial: red);
            builder.AddInstance(comp, material: red);

            var bytes = builder.ToBytes();
            var parsed = Core.FullParse(bytes);
            Assert.NotNull(parsed.FaceColors);
            var (front, back) = parsed.FaceColors!.Value;
            Assert.NotEqual(front, back);

            // Also attach VFF-style D007/D107 on the placed instance so instance paint is exercised
            // independently of #596.
            long redMatId = parsed.MaterialIdToName.First(kv => kv.Value == "Red").Key;
            var placed = parsed.Root.Builder.Instances[0];
            placed.Children.Add(new TlvNode
            {
                Tag = "D007",
                Children = new List<TlvNode>
                {
                    new TlvNode { Tag = "D107", Payload = new[] { (byte)redMatId } },
                },
            });

            var baked = SceneBuilder.Build(parsed);
            var bakedSides = baked.GlbPrimitives
                .Select(p => (RgbOf(baked.GltfMaterials, p.MaterialIndex), DoubleSidedOf(baked.GltfMaterials, p.MaterialIndex), p.UnpaintedBack))
                .ToList();
            Assert.Contains(((200, 10, 10), true, false), bakedSides);
            Assert.Contains((front, false, false), bakedSides);
            Assert.Contains((back, false, false), bakedSides);
            Assert.DoesNotContain(baked.GlbPrimitives, p => p.UnpaintedBack);

            var instanced = InstancedSceneBuilder.Build(parsed);
            var instSides = instanced.MeshResources
                .SelectMany(r => r.Primitives)
                .Select(p => (RgbOf(instanced.GltfMaterials, p.MaterialIndex), DoubleSidedOf(instanced.GltfMaterials, p.MaterialIndex), p.UnpaintedBack))
                .ToList();
            Assert.Contains(((200, 10, 10), true, false), instSides);
            Assert.Contains((front, false, false), instSides);
            Assert.Contains((back, false, false), instSides);
            Assert.DoesNotContain(instanced.MeshResources.SelectMany(r => r.Primitives), p => p.UnpaintedBack);
        }

        [Fact]
        public void ComponentOnTwoTagsIsOneInstancedMesh()
        {
            var builder = SkpCreate.NewFile();
            int tagA = builder.AddLayer("A", color: (255, 0, 0));
            int tagB = builder.AddLayer("B", color: (0, 0, 255));
            ComponentDefinitionBuilder part;
            using (part = builder.AddComponentDefinition("Part"))
            {
                part.AddFace(new (double, double, double)[] { (0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0) });
            }
            builder.AddInstance(part, layer: tagA);
            builder.AddInstance(part, layer: tagB, translation: (5, 0, 0));

            var parsed = Core.FullParse(builder.ToBytes());
            long layerAId = parsed.LayerIdToName.First(kv => kv.Value == "A").Key;
            long layerBId = parsed.LayerIdToName.First(kv => kv.Value == "B").Key;
            parsed.Root.Builder.Instances[0].Children.Add(new TlvNode
            {
                Tag = "D007",
                Children = new List<TlvNode> { new TlvNode { Tag = "D207", Payload = new[] { (byte)layerAId } } },
            });
            parsed.Root.Builder.Instances[1].Children.Add(new TlvNode
            {
                Tag = "D007",
                Children = new List<TlvNode> { new TlvNode { Tag = "D207", Payload = new[] { (byte)layerBId } } },
            });

            Assert.Single(InstancedSceneBuilder.Build(parsed).MeshResources);
            Assert.Equal(
                2,
                InstancedSceneBuilder.Build(parsed, new SkpParseOptions { UnpaintedFaces = "tag" }).MeshResources.Count);
        }

        [Fact]
        public void SingleColourExportsKeepOneCopyOfEachFace()
        {
            var bytes = BuildBoxBytes();
            var style = SkpFile.BuildScene(bytes);
            var tag = SkpFile.BuildScene(bytes, new SkpParseOptions { UnpaintedFaces = "tag" });

            Assert.Equal(24, style.GlbPrimitives.Sum(p => p.Indices.Length / 3));
            Assert.Equal(12, style.SurfacePrimitives.Sum(p => p.Indices.Length / 3));
            Assert.Same(style.GlbPrimitives[0], style.SurfacePrimitives[0]);

            Assert.Equal(StlExport.ToStlAscii(tag), StlExport.ToStlAscii(style));
            Assert.Equal(12u, BitConverter.ToUInt32(StlExport.ToStlBinary(style), 80));

            int CountOccurrences(string haystack, string needle)
            {
                int count = 0, idx = 0;
                while ((idx = haystack.IndexOf(needle, idx, StringComparison.Ordinal)) >= 0)
                {
                    count++;
                    idx += needle.Length;
                }
                return count;
            }

            Assert.Equal(12, CountOccurrences(ObjExport.ToObj(style), "\nf "));
            Assert.Equal(12, CountOccurrences(ObjExport.ToObj(tag), "\nf "));
            Assert.Contains("element face 12\n", PlyExport.ToPlyAscii(style), StringComparison.Ordinal);

            int dxfStyle = CountOccurrences(DxfExport.ToDxf(style, mode: "3dface"), "3DFACE");
            int dxfTag = CountOccurrences(DxfExport.ToDxf(tag, mode: "3dface"), "3DFACE");
            Assert.True(dxfStyle > 0);
            Assert.Equal(dxfTag, dxfStyle);

            Assert.Equal(1, CountOccurrences(IfcExport.ToIfc(style), "IFCTRIANGULATEDFACESET"));
            Assert.Equal(1, CountOccurrences(IfcExport.ToIfc(tag), "IFCTRIANGULATEDFACESET"));
        }

        [Fact]
        public void GlbExportsShowBothSides()
        {
            var path = FixturePath("face_colors_2017.skp");
            var layer = SkpFile.Open(path).Layers[0];
            var layerRgb = (layer.ColorR, layer.ColorG, layer.ColorB);
            var expectedStyle = new List<((int, int, int), bool)> { (Front, false), (Back, false) }
                .OrderBy(x => x.Item1.Item1)
                .ToList();

            Assert.Equal(expectedStyle, GlbMaterials(GlbExport.ToGlb(SkpFile.BuildScene(path))));
            Assert.Equal(
                new List<((int, int, int), bool)> { (layerRgb, true) },
                GlbMaterials(GlbExport.ToGlb(SkpFile.BuildScene(path, new SkpParseOptions { UnpaintedFaces = "tag" }))));

            Assert.Equal(expectedStyle, GlbMaterials(InstancedGlbExport.ToInstancedGlb(SkpFile.BuildInstancedScene(path))));
            Assert.Equal(
                new List<((int, int, int), bool)> { (layerRgb, true) },
                GlbMaterials(InstancedGlbExport.ToInstancedGlb(SkpFile.BuildInstancedScene(path, new SkpParseOptions { UnpaintedFaces = "tag" }))));
        }
    }
}
