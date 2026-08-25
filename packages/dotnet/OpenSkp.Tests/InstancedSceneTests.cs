using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using System.Text.Json;
using Xunit;
using OpenSkp;

namespace OpenSkp.Tests
{
    /// <summary>
    /// Instanced scene building and export (openskp#200, ported from
    /// TypeScript's buildInstancedScene()/toInstancedGLB()).
    ///
    /// The strongest correctness evidence available: run BOTH builders over
    /// the repository's real .skp fixtures and require that flattening the
    /// instanced result reproduces the baked builder's world-space
    /// triangles exactly. This covers, on genuine files, everything a
    /// synthetic test would cover piecewise - nested groups/components,
    /// instance-painted materials, layers, front/back materials, textures,
    /// holes, mirrored transforms - because whatever those files happen to
    /// contain has to come out the same either way.
    /// </summary>
    public class InstancedSceneTests
    {
        // One modern VFF container plus two legacy MFC ones, so both parse
        // paths feed the instanced builder here too.
        public static IEnumerable<object[]> Fixtures => new[]
        {
            new object[] { "SU_File.skp" },
            new object[] { "Untitled.skp" },
            new object[] { "capilla_quiroz_v17.skp" },
            new object[] { "gondola_v20.skp" },
            new object[] { "single_material_v17.skp" },
        };

        private static string FixturePath(string name) =>
            Path.Combine(AppContext.BaseDirectory, "fixtures", name);

        // Float32 round-off only, same tolerance and justification as the
        // TypeScript reference: the baked path transforms in float64 then
        // stores the world-space result as float32; the instanced path
        // stores the local-space value as float32 and transforms
        // afterwards. Both are single-rounding-step correct, but round at
        // different moments, so a coordinate can land one float32 ulp apart
        // between them.
        private const double Tolerance = 1e-5;

        private static double[] Mul4(double[] a, double[] b)
        {
            var outM = new double[16];
            for (var col = 0; col < 4; col++)
            {
                for (var row = 0; row < 4; row++)
                {
                    double s = 0;
                    for (var k = 0; k < 4; k++) s += a[k * 4 + row] * b[col * 4 + k];
                    outM[col * 4 + row] = s;
                }
            }
            return outM;
        }

        private static (double X, double Y, double Z) ApplyMatrix(double[] m, (double X, double Y, double Z) p) => (
            m[0] * p.X + m[4] * p.Y + m[8] * p.Z + m[12],
            m[1] * p.X + m[5] * p.Y + m[9] * p.Z + m[13],
            m[2] * p.X + m[6] * p.Y + m[10] * p.Z + m[14]
        );

        private static readonly double[] Identity4 = { 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1 };

        private readonly struct FlatTriangle
        {
            public readonly (double X, double Y, double Z) A, B, C;
            public readonly int MaterialIndex;
            public FlatTriangle((double, double, double) a, (double, double, double) b, (double, double, double) c, int mat)
            {
                A = a; B = b; C = c; MaterialIndex = mat;
            }
        }

        /// <summary>Walk the instanced tree, composing node transforms, and
        /// emit every triangle in world space - i.e. reconstruct what
        /// SkpFile.BuildScene() bakes. Test-only: the whole point of the
        /// instanced output is to avoid materialising this.</summary>
        private static List<FlatTriangle> FlattenInstanced(InstancedScene scene)
        {
            var byId = new Dictionary<string, InstancedMeshResource>();
            foreach (var r in scene.MeshResources) byId[r.Id] = r;
            var outTris = new List<FlatTriangle>();

            void Visit(InstancedNode node, double[] parent)
            {
                var world = Mul4(parent, node.Matrix);
                if (node.MeshResourceId != null && byId.TryGetValue(node.MeshResourceId, out var res))
                {
                    foreach (var prim in res.Primitives)
                    {
                        for (var i = 0; i < prim.Indices.Length; i += 3)
                        {
                            var tri = new (double, double, double)[3];
                            for (var k = 0; k < 3; k++)
                            {
                                var vi = prim.Indices[i + k];
                                tri[k] = ApplyMatrix(world, (prim.Positions[vi * 3], prim.Positions[vi * 3 + 1], prim.Positions[vi * 3 + 2]));
                            }
                            outTris.Add(new FlatTriangle(tri[0], tri[1], tri[2], prim.MaterialIndex));
                        }
                    }
                }
                foreach (var child in node.Children) Visit(child, world);
            }
            Visit(scene.SceneHierarchy, Identity4);
            return outTris;
        }

        private static List<FlatTriangle> FlattenBaked(Scene scene)
        {
            var outTris = new List<FlatTriangle>();
            foreach (var prim in scene.GlbPrimitives)
            {
                for (var i = 0; i < prim.Indices.Length; i += 3)
                {
                    var tri = new (double, double, double)[3];
                    for (var k = 0; k < 3; k++)
                    {
                        var vi = prim.Indices[i + k];
                        tri[k] = (prim.Positions[vi * 3], prim.Positions[vi * 3 + 1], prim.Positions[vi * 3 + 2]);
                    }
                    outTris.Add(new FlatTriangle(tri[0], tri[1], tri[2], prim.MaterialIndex));
                }
            }
            return outTris;
        }

        private static long InstancedBufferBytes(InstancedScene scene)
        {
            long total = 0;
            foreach (var r in scene.MeshResources)
                foreach (var p in r.Primitives)
                    total += p.Positions.Length * 4L + p.Normals.Length * 4L + p.Uvs.Length * 4L + p.Indices.Length * 4L;
            return total;
        }

        private static long BakedBufferBytes(Scene scene)
        {
            long total = 0;
            foreach (var p in scene.GlbPrimitives)
                total += p.Positions.Length * 4L + p.Normals.Length * 4L + p.Uvs.Length * 4L + p.Indices.Length * 4L;
            return total;
        }

        // Minimal JSON-content comparison for material dictionaries, so
        // materials are matched by CONTENT rather than index (both paths
        // build the same glTF material table but allocate into it in their
        // own encounter order).
        private static string MaterialKey(object mat)
        {
            if (mat is IDictionary<string, object> dict)
            {
                var parts = new List<string>();
                foreach (var kv in dict) parts.Add($"\"{kv.Key}\":{MaterialKey(kv.Value)}");
                parts.Sort(StringComparer.Ordinal);
                return "{" + string.Join(",", parts) + "}";
            }
            if (mat is System.Collections.IEnumerable en && !(mat is string))
            {
                var parts = new List<string>();
                foreach (var item in en) parts.Add(MaterialKey(item));
                return "[" + string.Join(",", parts) + "]";
            }
            if (mat is bool b) return b ? "true" : "false";
            if (mat is string s) return "\"" + s + "\"";
            if (mat == null) return "null";
            return Convert.ToString(mat, System.Globalization.CultureInfo.InvariantCulture) ?? "null";
        }

        [Theory]
        [MemberData(nameof(Fixtures))]
        public void ReproducesBuildScenesWorldSpaceTriangles(string fixtureName)
        {
            var bytes = File.ReadAllBytes(FixturePath(fixtureName));
            var baked = SkpFile.BuildScene(bytes);
            var instanced = SkpFile.BuildInstancedScene(bytes);

            var bakedTris = FlattenBaked(baked);
            var instTris = FlattenInstanced(instanced);

            Assert.Equal(bakedTris.Count, instTris.Count);

            double worstDelta = 0;
            var materialMismatches = 0;
            var n = Math.Min(bakedTris.Count, instTris.Count);
            for (var i = 0; i < n; i++)
            {
                var a = instTris[i];
                var e = bakedTris[i];

                if (MaterialKey(instanced.GltfMaterials[a.MaterialIndex]) != MaterialKey(baked.GltfMaterials[e.MaterialIndex]))
                {
                    materialMismatches++;
                }

                foreach (var (pa, pe) in new[] { (a.A, e.A), (a.B, e.B), (a.C, e.C) })
                {
                    worstDelta = Math.Max(worstDelta, Math.Abs(pa.X - pe.X));
                    worstDelta = Math.Max(worstDelta, Math.Abs(pa.Y - pe.Y));
                    worstDelta = Math.Max(worstDelta, Math.Abs(pa.Z - pe.Z));
                }
            }

            Assert.Equal(0, materialMismatches);
            Assert.True(worstDelta < Tolerance, $"worst delta {worstDelta} exceeds tolerance");
        }

        [Theory]
        [MemberData(nameof(Fixtures))]
        public void NeverStoresMoreGeometryThanTheBakedPath(string fixtureName)
        {
            var bytes = File.ReadAllBytes(FixturePath(fixtureName));
            var bakedBytes = BakedBufferBytes(SkpFile.BuildScene(bytes));
            var instancedBytes = InstancedBufferBytes(SkpFile.BuildInstancedScene(bytes));

            // Equal when nothing repeats; strictly smaller once anything does.
            Assert.True(instancedBytes <= bakedBytes);
        }

        [Theory]
        [MemberData(nameof(Fixtures))]
        public void ResolvesTheSameLayersAndDynamicPropertiesPerNode(string fixtureName)
        {
            var bytes = File.ReadAllBytes(FixturePath(fixtureName));
            var baked = SkpFile.BuildScene(bytes);
            var instanced = SkpFile.BuildInstancedScene(bytes);

            // Walk both trees in lockstep: the instance walk order is
            // identical, so a divergence in metadata shows up as a
            // mismatch here.
            void Walk(InstanceNode b, InstancedNode i)
            {
                Assert.Equal(b.Name, i.Name);
                Assert.Equal(b.DefinitionName, i.DefinitionName);
                Assert.Equal(b.Layer, i.Layer);
                Assert.Equal(b.PositionMm, i.PositionMm);
                Assert.Equal(b.Properties, i.Properties);
                Assert.Equal(b.Children.Count, i.Children.Count);
                for (var k = 0; k < b.Children.Count; k++) Walk(b.Children[k], i.Children[k]);
            }
            Walk(baked.SceneHierarchy, instanced.SceneHierarchy);
        }

        private static (JsonElement Json, byte[] Binary) ParseGlb(byte[] bytes)
        {
            var jsonChunkLen = BitConverter.ToUInt32(bytes, 12);
            var jsonStr = Encoding.UTF8.GetString(bytes, 20, (int)jsonChunkLen);
            var json = JsonDocument.Parse(jsonStr).RootElement;

            var binHeaderOffset = 20 + (int)jsonChunkLen;
            byte[] binary = Array.Empty<byte>();
            if (binHeaderOffset < bytes.Length)
            {
                var binChunkLen = BitConverter.ToUInt32(bytes, binHeaderOffset);
                binary = new byte[binChunkLen];
                Array.Copy(bytes, binHeaderOffset + 8, binary, 0, (int)binChunkLen);
            }
            return (json, binary);
        }

        private static readonly byte[] JpegMagic = { 0xFF, 0xD8, 0xFF };

        private static bool ContainsBytes(byte[] haystack, byte[] needle)
        {
            for (var i = 0; i <= haystack.Length - needle.Length; i++)
            {
                var match = true;
                for (var j = 0; j < needle.Length; j++)
                {
                    if (haystack[i + j] != needle[j]) { match = false; break; }
                }
                if (match) return true;
            }
            return false;
        }

        private static readonly string CapillaFixture = "capilla_quiroz_v17.skp";

        [Fact]
        public void ExportOmitsImagesByDefault()
        {
            var bytes = File.ReadAllBytes(FixturePath(CapillaFixture));
            var scene = SkpFile.BuildInstancedScene(bytes);
            var glb = InstancedGlbExport.ToInstancedGlb(scene);

            var (json, _) = ParseGlb(glb);
            Assert.False(json.TryGetProperty("images", out _));
            Assert.False(ContainsBytes(glb, JpegMagic));
        }

        [Fact]
        public void ExportEmbedsTexturesWhenAsked()
        {
            var bytes = File.ReadAllBytes(FixturePath(CapillaFixture));
            var scene = SkpFile.BuildInstancedScene(bytes);
            var withoutTextures = InstancedGlbExport.ToInstancedGlb(scene);
            var withTextures = InstancedGlbExport.ToInstancedGlb(scene, new InstancedGlbOptions { Textures = true });

            Assert.True(withTextures.Length > withoutTextures.Length);
            Assert.True(ContainsBytes(withTextures, JpegMagic));

            var (json, _) = ParseGlb(withTextures);
            Assert.True(json.TryGetProperty("images", out var images));
            Assert.Equal(3, images.GetArrayLength());
        }

        [Fact]
        public void IsSmallerThanTheBakedExportOnAFileWithRepeatedGeometry()
        {
            var bytes = File.ReadAllBytes(FixturePath("gondola_v20.skp"));
            var baked = GlbExport.ToGlb(SkpFile.BuildScene(bytes));
            var instanced = InstancedGlbExport.ToInstancedGlb(SkpFile.BuildInstancedScene(bytes));

            Assert.True(instanced.Length < baked.Length);
        }

        [Fact]
        public void ExportInstancedGlbFileRoundTrips()
        {
            var bytes = File.ReadAllBytes(FixturePath(CapillaFixture));
            var scene = SkpFile.BuildInstancedScene(bytes);
            var tmp = Path.Combine(Path.GetTempPath(), "openskp-dotnet-instanced-test.glb");
            try
            {
                InstancedGlbExport.ExportInstancedGlb(scene, tmp, new InstancedGlbOptions { Textures = true });
                var fileBytes = File.ReadAllBytes(tmp);
                Assert.True(ContainsBytes(fileBytes, JpegMagic));
            }
            finally
            {
                if (File.Exists(tmp)) File.Delete(tmp);
            }
        }
    }
}
