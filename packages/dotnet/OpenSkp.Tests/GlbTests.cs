using System;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.Json;
using Xunit;
using OpenSkp;

namespace OpenSkp.Tests
{
    /// <summary>
    /// Tests for GlbExport - the from-scratch binary glTF (.glb) writer
    /// (no external dependency, matching how this project stays
    /// dependency-light everywhere except C++'s bundled TinyGLTF).
    /// Uses System.Text.Json here only to parse the *output* for
    /// assertions - that's part of the modern .NET SDK the test project
    /// already targets (net9.0), not a new dependency on the library
    /// itself (which stays netstandard2.0, hence GlbExport's own
    /// hand-rolled MiniJson).
    /// </summary>
    public class GlbTests
    {
        private static string FixturePath(string name) =>
            Path.Combine(AppContext.BaseDirectory, "fixtures", name);

        private static string CppFixturePath(string name)
        {
            var dir = new DirectoryInfo(AppContext.BaseDirectory);
            while (dir != null)
            {
                var candidate = Path.Combine(dir.FullName, "packages", "cpp", "tests", "fixtures", name);
                if (File.Exists(candidate)) return candidate;
                dir = dir.Parent;
            }
            throw new FileNotFoundException($"Could not locate C++ fixture {name}");
        }

        private static Scene TriangleScene()
        {
            return new Scene
            {
                GltfMaterials = new System.Collections.Generic.List<object>
                {
                    new
                    {
                        pbrMetallicRoughness = new
                        {
                            baseColorFactor = new[] { 0.25, 0.5, 0.75, 1.0 },
                            metallicFactor = 0.1,
                            roughnessFactor = 0.9,
                        },
                    },
                },
                GlbPrimitives = new System.Collections.Generic.List<GlbPrimitive>
                {
                    new GlbPrimitive
                    {
                        Positions = new float[] { 1, 2, 3, -4, 5, 0, 2, -1, 7 },
                        Normals = new float[] { 0, 0, 1, 0, 0, 1, 0, 0, 1 },
                        Uvs = new float[] { 0, 0, 1, 0, 0, 1 },
                        Indices = new uint[] { 0, 1, 2 },
                        MaterialIndex = 0,
                        GeomName = "triangle",
                    },
                },
            };
        }

        [Fact]
        public void SerializesSceneAndBinaryData()
        {
            var bytes = GlbExport.ToGlb(TriangleScene());
            Assert.True(bytes.Length >= 12);
            Assert.Equal("glTF", Encoding.ASCII.GetString(bytes, 0, 4));

            var (json, binary) = ParseGlb(bytes);
            Assert.Equal("2.0", json.GetProperty("asset").GetProperty("version").GetString());

            var meshes = json.GetProperty("meshes");
            Assert.Equal(1, meshes.GetArrayLength());
            var prim = meshes[0].GetProperty("primitives")[0];
            var attrs = prim.GetProperty("attributes");
            Assert.True(attrs.TryGetProperty("POSITION", out _));
            Assert.True(attrs.TryGetProperty("NORMAL", out _));
            Assert.True(attrs.TryGetProperty("TEXCOORD_0", out _));
            Assert.Equal(0, prim.GetProperty("material").GetInt32());

            var accessors = json.GetProperty("accessors");
            var posAccessor = accessors[attrs.GetProperty("POSITION").GetInt32()];
            Assert.Equal(5126, posAccessor.GetProperty("componentType").GetInt32());
            Assert.Equal("VEC3", posAccessor.GetProperty("type").GetString());
            Assert.Equal(3, posAccessor.GetProperty("count").GetInt32());
            var min = posAccessor.GetProperty("min").EnumerateArray().Select(e => e.GetDouble()).ToArray();
            var max = posAccessor.GetProperty("max").EnumerateArray().Select(e => e.GetDouble()).ToArray();
            Assert.Equal(new[] { -4.0, -1.0, 0.0 }, min);
            Assert.Equal(new[] { 2.0, 5.0, 7.0 }, max);

            var uvAccessor = accessors[attrs.GetProperty("TEXCOORD_0").GetInt32()];
            Assert.Equal(5126, uvAccessor.GetProperty("componentType").GetInt32());
            Assert.Equal("VEC2", uvAccessor.GetProperty("type").GetString());
            Assert.Equal(3, uvAccessor.GetProperty("count").GetInt32());
            var uvBufferView = json.GetProperty("bufferViews")[uvAccessor.GetProperty("bufferView").GetInt32()];
            var uvOffset = uvBufferView.GetProperty("byteOffset").GetInt32();
            Assert.Equal(1.0f, BitConverter.ToSingle(binary, uvOffset + 2 * 4));

            var materials = json.GetProperty("materials");
            var pbr = materials[0].GetProperty("pbrMetallicRoughness");
            static double SrgbToLinear(double c) =>
                c <= 0.04045 ? c / 12.92 : Math.Pow((c + 0.055) / 1.055, 2.4);
            Assert.Equal(SrgbToLinear(0.25), pbr.GetProperty("baseColorFactor")[0].GetDouble(), 9);
            Assert.Equal(SrgbToLinear(0.5), pbr.GetProperty("baseColorFactor")[1].GetDouble(), 9);
            Assert.Equal(SrgbToLinear(0.75), pbr.GetProperty("baseColorFactor")[2].GetDouble(), 9);
            Assert.Equal(1.0, pbr.GetProperty("baseColorFactor")[3].GetDouble(), 9);
            Assert.Equal(0.1, pbr.GetProperty("metallicFactor").GetDouble());
            Assert.Equal(0.9, pbr.GetProperty("roughnessFactor").GetDouble());
        }

        [Fact]
        public void SerializesAnEmptyScene()
        {
            var bytes = GlbExport.ToGlb(new Scene());
            var (json, _) = ParseGlb(bytes);
            Assert.Equal(0, json.GetProperty("meshes").GetArrayLength());
            Assert.Equal(0, json.GetProperty("nodes").GetArrayLength());
        }

        [Fact]
        public void RejectsMalformedGeometry()
        {
            var scene = TriangleScene();
            scene.GlbPrimitives[0].Positions = scene.GlbPrimitives[0].Positions.Take(8).ToArray();
            Assert.Throws<ArgumentException>(() => GlbExport.ToGlb(scene));

            scene = TriangleScene();
            scene.GlbPrimitives[0].Normals = scene.GlbPrimitives[0].Normals.Take(2).ToArray();
            Assert.Throws<ArgumentException>(() => GlbExport.ToGlb(scene));

            scene = TriangleScene();
            scene.GlbPrimitives[0].Uvs = scene.GlbPrimitives[0].Uvs.Take(1).ToArray();
            Assert.Throws<ArgumentException>(() => GlbExport.ToGlb(scene));

            scene = TriangleScene();
            scene.GlbPrimitives[0].Indices = Array.Empty<uint>();
            Assert.Throws<ArgumentException>(() => GlbExport.ToGlb(scene));

            scene = TriangleScene();
            scene.GlbPrimitives[0].Indices = new uint[] { 0, 1, 5 };
            Assert.Throws<ArgumentException>(() => GlbExport.ToGlb(scene));

            scene = TriangleScene();
            scene.GlbPrimitives[0].MaterialIndex = 1;
            Assert.Throws<ArgumentException>(() => GlbExport.ToGlb(scene));
        }

        [Fact]
        public void RejectsNonFiniteValues()
        {
            var scene = TriangleScene();
            scene.GlbPrimitives[0].Positions[0] = float.NaN;
            Assert.Throws<ArgumentException>(() => GlbExport.ToGlb(scene));

            scene = TriangleScene();
            scene.GlbPrimitives[0].Normals[0] = float.PositiveInfinity;
            Assert.Throws<ArgumentException>(() => GlbExport.ToGlb(scene));

            scene = TriangleScene();
            scene.GlbPrimitives[0].Uvs[0] = float.NegativeInfinity;
            Assert.Throws<ArgumentException>(() => GlbExport.ToGlb(scene));
        }

        [Fact]
        public void ExportsRealFixtureMatchingToGlbAndBuildScene()
        {
            var scene = SkpFile.BuildScene(FixturePath("capilla_quiroz_v17.skp"));
            var expected = GlbExport.ToGlb(scene);

            var output = Path.Combine(Path.GetTempPath(), "openskp-dotnet-glb-test.glb");
            GlbExport.ExportGlb(scene, output);
            try
            {
                var actual = File.ReadAllBytes(output);
                Assert.Equal(expected, actual);

                var (json, binary) = ParseGlb(actual);
                var meshes = json.GetProperty("meshes")[0].GetProperty("primitives");
                Assert.Equal(scene.GlbPrimitives.Count, meshes.GetArrayLength());

                // Every primitive must carry TEXCOORD_0, and the decoded
                // values must exactly match the source GlbPrimitive.Uvs
                // that fed the writer - a real round-trip check, not
                // just "some accessor exists."
                for (var i = 0; i < scene.GlbPrimitives.Count; i++)
                {
                    var prim = scene.GlbPrimitives[i];
                    var attrs = meshes[i].GetProperty("attributes");
                    Assert.True(attrs.TryGetProperty("TEXCOORD_0", out var uvAccessorIdx));
                    var uvAccessor = json.GetProperty("accessors")[uvAccessorIdx.GetInt32()];
                    var uvBufferView = json.GetProperty("bufferViews")[uvAccessor.GetProperty("bufferView").GetInt32()];
                    var uvOffset = uvBufferView.GetProperty("byteOffset").GetInt32();
                    var uvCount = uvAccessor.GetProperty("count").GetInt32();
                    Assert.Equal(prim.Uvs.Length, uvCount * 2);
                    for (var j = 0; j < prim.Uvs.Length; j++)
                    {
                        Assert.Equal(prim.Uvs[j], BitConverter.ToSingle(binary, uvOffset + j * 4));
                    }
                }
            }
            finally
            {
                File.Delete(output);
            }
        }

        [Fact]
        public void ReportsFileFailuresWithoutCreatingDirectories()
        {
            var output = Path.Combine(Path.GetTempPath(), "openskp-missing-parent-" + Guid.NewGuid(), "asset.glb");
            Assert.ThrowsAny<Exception>(() => GlbExport.ExportGlb(TriangleScene(), output));
        }

        [Fact]
        public void ConvertsSrgbBaseColorFactorToLinearWhileKeepingSceneSrgb()
        {
            static double SrgbToLinear(double c) =>
                c <= 0.04045 ? c / 12.92 : Math.Pow((c + 0.055) / 1.055, 2.4);

            var builder = SkpCreate.NewFile();
            var specs = new (string Name, byte R, byte G, byte B, byte A)[]
            {
                ("LayerRed", 255, 84, 84, 255),
                ("White", 255, 255, 255, 255),
                ("Black", 0, 0, 0, 255),
                ("MidGrey", 188, 188, 188, 255),
                ("DarkToe", 10, 10, 10, 128),
            };
            var handles = specs.Select(s => builder.AddMaterial(s.Name, (s.R, s.G, s.B, s.A))).ToArray();
            for (int i = 0; i < handles.Length; i++)
            {
                double x = i * 10.0;
                builder.AddFace(
                    new[] { (x, 0.0, 0.0), (x + 5.0, 0.0, 0.0), (x + 5.0, 5.0, 0.0), (x, 5.0, 0.0) },
                    material: handles[i]);
            }

            var skpBytes = builder.ToBytes();
            var bakedScene = SkpFile.BuildScene(skpBytes);
            var instancedScene = SkpFile.BuildInstancedScene(skpBytes);

            foreach (var glbBytes in new[] { GlbExport.ToGlb(bakedScene), InstancedGlbExport.ToInstancedGlb(instancedScene) })
            {
                var (json, _) = ParseGlb(glbBytes);
                var factors = json.GetProperty("materials")
                    .EnumerateArray()
                    .Select(m => m.GetProperty("pbrMetallicRoughness")
                        .GetProperty("baseColorFactor")
                        .EnumerateArray()
                        .Select(e => e.GetDouble())
                        .ToArray())
                    .ToList();

                double[]? FindClose(double[] target) =>
                    factors.FirstOrDefault(f => target.Select((v, idx) => Math.Abs(f[idx] - v) < 1e-4).All(ok => ok));

                var red = FindClose(new[] { 1.0, SrgbToLinear(84.0 / 255.0), SrgbToLinear(84.0 / 255.0), 1.0 });
                Assert.NotNull(red);
                Assert.Equal(1.0, red![0]);
                Assert.Equal(0.0887, red[1], 4);
                Assert.Equal(0.0887, red[2], 4);
                Assert.Equal(1.0, red[3]);

                Assert.Equal(new[] { 1.0, 1.0, 1.0, 1.0 }, FindClose(new[] { 1.0, 1.0, 1.0, 1.0 }));
                Assert.Equal(new[] { 0.0, 0.0, 0.0, 1.0 }, FindClose(new[] { 0.0, 0.0, 0.0, 1.0 }));

                var mid = FindClose(new[] { SrgbToLinear(188.0 / 255.0), SrgbToLinear(188.0 / 255.0), SrgbToLinear(188.0 / 255.0), 1.0 });
                Assert.NotNull(mid);
                Assert.Equal(0.503, mid![0], 3);
                Assert.Equal(SrgbToLinear(188.0 / 255.0), mid[0], 9);

                var toe = FindClose(new[] { (10.0 / 255.0) / 12.92, (10.0 / 255.0) / 12.92, (10.0 / 255.0) / 12.92, 128.0 / 255.0 });
                Assert.NotNull(toe);
                Assert.Equal((10.0 / 255.0) / 12.92, toe![0], 9);
                Assert.Equal(128.0 / 255.0, toe[3], 6);
            }

            // Scene GltfMaterials stay sRGB.
            var sceneFactors = bakedScene.GltfMaterials
                .Cast<System.Collections.Generic.IDictionary<string, object>>()
                .Select(m => (double[])((System.Collections.Generic.IDictionary<string, object>)m["pbrMetallicRoughness"])["baseColorFactor"])
                .ToList();
            Assert.Contains(sceneFactors, f => f[0] == 1.0 && Math.Abs(f[1] - 84.0 / 255.0) < 1e-6);
            Assert.Contains(sceneFactors, f => Math.Abs(f[0] - 188.0 / 255.0) < 1e-6);

            // Embedded non-colorized textures write [1, 1, 1, alpha], while scene
            // GltfMaterials keeps average colours and colorized materials stay tinted (#657).
            var capillaScene = SkpFile.BuildScene(FixturePath("capilla_quiroz_v17.skp"));
            var capillaInstanced = SkpFile.BuildInstancedScene(FixturePath("capilla_quiroz_v17.skp"));
            var sceneTexFactors = capillaScene.GltfMaterials
                .Cast<System.Collections.Generic.IDictionary<string, object>>()
                .Select(m => (System.Collections.Generic.IDictionary<string, object>)m["pbrMetallicRoughness"])
                .Where(p => p.ContainsKey("baseColorTexture"))
                .Select(p => (double[])p["baseColorFactor"])
                .ToList();
            Assert.Contains(sceneTexFactors, f => f[0] < 0.6 && f[2] < 0.2);
            var expectedWhiteFactors = sceneTexFactors.Select(f => new[] { 1.0, 1.0, 1.0, f[3] }).ToList();

            foreach (var capillaGlb in new[]
            {
                GlbExport.ToGlb(capillaScene, new GlbOptions { Textures = true }),
                InstancedGlbExport.ToInstancedGlb(capillaInstanced, new InstancedGlbOptions { Textures = true }),
            })
            {
                var (capillaJson, _) = ParseGlb(capillaGlb);
                var actualTexFactors = capillaJson.GetProperty("materials")
                    .EnumerateArray()
                    .Select(m => m.GetProperty("pbrMetallicRoughness"))
                    .Where(p => p.TryGetProperty("baseColorTexture", out _))
                    .Select(p => p.GetProperty("baseColorFactor").EnumerateArray().Select(e => e.GetDouble()).ToArray())
                    .ToList();
                Assert.Equal(expectedWhiteFactors.Count, actualTexFactors.Count);
                for (int i = 0; i < expectedWhiteFactors.Count; i++)
                {
                    for (int c = 0; c < 4; c++)
                    {
                        Assert.Equal(expectedWhiteFactors[i][c], actualTexFactors[i][c], 9);
                    }
                }
            }

            // End-to-end model -> scene -> GLB on a real .skp containing one plain and one
            // colorized material sharing the same RGB (200, 80, 60) and PNG image (#657).
            var bothPath = CppFixturePath("colorized_and_plain_texture.skp");
            var bothBaked = SkpFile.BuildScene(bothPath);
            var bothInstanced = SkpFile.BuildInstancedScene(bothPath);
            foreach (var rawMats in new[] { bothBaked.GltfMaterials, bothInstanced.GltfMaterials })
            {
                var mats = rawMats.Cast<System.Collections.Generic.IDictionary<string, object>>().ToList();
                Assert.Equal(2, mats.Count);
                var plainMat = Assert.Single(mats, m => !m.ContainsKey("extras"));
                var colMat = Assert.Single(mats, m => m.ContainsKey("extras"));
                var skpExtras = (System.Collections.Generic.IDictionary<string, object>)
                    ((System.Collections.Generic.IDictionary<string, object>)colMat["extras"])["sketchup"];
                Assert.Equal(true, skpExtras["colorized"]);
                Assert.Equal(1, skpExtras["colorize_type"]);
                foreach (var m in mats)
                {
                    var bcf = (double[])((System.Collections.Generic.IDictionary<string, object>)m["pbrMetallicRoughness"])["baseColorFactor"];
                    Assert.Equal(200.0 / 255.0, bcf[0], 9);
                    Assert.Equal(80.0 / 255.0, bcf[1], 9);
                    Assert.Equal(60.0 / 255.0, bcf[2], 9);
                    Assert.Equal(1.0, bcf[3], 9);
                }
            }

            foreach (var withTexGlb in new[]
            {
                GlbExport.ToGlb(bothBaked, new GlbOptions { Textures = true }),
                InstancedGlbExport.ToInstancedGlb(bothInstanced, new InstancedGlbOptions { Textures = true }),
            })
            {
                var (withTexJson, _) = ParseGlb(withTexGlb);
                var mats = withTexJson.GetProperty("materials").EnumerateArray().ToList();
                Assert.Equal(2, mats.Count);
                var plainGlb = Assert.Single(mats, m => !m.TryGetProperty("extras", out _));
                var colGlb = Assert.Single(mats, m => m.TryGetProperty("extras", out _));
                Assert.Equal(0, plainGlb.GetProperty("pbrMetallicRoughness").GetProperty("baseColorTexture").GetProperty("index").GetInt32());
                var plainBcf = plainGlb.GetProperty("pbrMetallicRoughness").GetProperty("baseColorFactor").EnumerateArray().Select(e => e.GetDouble()).ToArray();
                Assert.Equal(new[] { 1.0, 1.0, 1.0, 1.0 }, plainBcf);

                var colExtras = colGlb.GetProperty("extras").GetProperty("sketchup");
                Assert.True(colExtras.GetProperty("colorized").GetBoolean());
                Assert.Equal(1, colExtras.GetProperty("colorize_type").GetInt32());
                Assert.Equal(0, colGlb.GetProperty("pbrMetallicRoughness").GetProperty("baseColorTexture").GetProperty("index").GetInt32());
                var colBcf = colGlb.GetProperty("pbrMetallicRoughness").GetProperty("baseColorFactor").EnumerateArray().Select(e => e.GetDouble()).ToArray();
                Assert.Equal(200.0 / 255.0, colBcf[0], 9);
                Assert.Equal(80.0 / 255.0, colBcf[1], 9);
                Assert.Equal(60.0 / 255.0, colBcf[2], 9);
                Assert.Equal(1.0, colBcf[3], 9);
            }

            var expectedLinearTint = new[]
            {
                SrgbToLinear(200.0 / 255.0),
                SrgbToLinear(80.0 / 255.0),
                SrgbToLinear(60.0 / 255.0),
                1.0,
            };
            foreach (var noTexGlb in new[]
            {
                GlbExport.ToGlb(bothBaked, new GlbOptions { Textures = false }),
                InstancedGlbExport.ToInstancedGlb(bothInstanced, new InstancedGlbOptions { Textures = false }),
            })
            {
                var (noTexJson, _) = ParseGlb(noTexGlb);
                Assert.False(noTexJson.TryGetProperty("textures", out _));
                var mats = noTexJson.GetProperty("materials").EnumerateArray().ToList();
                Assert.Equal(2, mats.Count);
                foreach (var m in mats)
                {
                    var pbr = m.GetProperty("pbrMetallicRoughness");
                    Assert.False(pbr.TryGetProperty("baseColorTexture", out _));
                    var bcf = pbr.GetProperty("baseColorFactor").EnumerateArray().Select(e => e.GetDouble()).ToArray();
                    for (int c = 0; c < 4; c++)
                    {
                        Assert.Equal(expectedLinearTint[c], bcf[c], 9);
                    }
                }
            }
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
    }
}
