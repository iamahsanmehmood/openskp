using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Threading;
using OpenSkp;
using Xunit;

namespace OpenSkp.Tests
{
    public class DxfExportTests
    {
        private static GlbPrimitive Triangle(string name) => new GlbPrimitive
        {
            GeomName = name,
            MaterialIndex = 0,
            Positions = new float[] { 0f, 0f, 0f, 1f, 0f, 0f, 0f, 1f, 0f },
            Normals = new float[] { 0f, 0f, 1f, 0f, 0f, 1f, 0f, 0f, 1f },
            Uvs = new float[] { 0f, 0f, 1f, 0f, 0f, 1f },
            Indices = new uint[] { 0, 1, 2 }
        };

        private static uint Crc32Ascii(string text)
        {
            uint crc = 0xFFFFFFFFu;
            foreach (char ch in text)
            {
                crc ^= (byte)(ch & 0xFF);
                for (int bit = 0; bit < 8; bit++)
                {
                    crc = (crc >> 1) ^ ((crc & 1u) != 0 ? 0xEDB88320u : 0u);
                }
            }
            return ~crc;
        }

        private static string SurrogateEscape(params byte[] raw)
        {
            var chars = new char[raw.Length];
            for (int i = 0; i < raw.Length; i++)
            {
                chars[i] = raw[i] < 0x80 ? (char)raw[i] : (char)(0xDC00 | raw[i]);
            }
            return new string(chars);
        }

        private static (HashSet<string> Layers, List<string> MeshLayers) Inspect(string dxf)
        {
            Assert.All(dxf, c => Assert.True(c < 128, "R2000 DXF contains raw non-ASCII bytes"));
            Assert.EndsWith("\r\n", dxf, StringComparison.Ordinal);
            string[] lines = dxf.Substring(0, dxf.Length - 2)
                .Split(new[] { "\r\n" }, StringSplitOptions.None);
            Assert.Equal(0, lines.Length % 2);

            var layers = new HashSet<string>(StringComparer.Ordinal);
            var meshLayers = new List<string>();
            var references = new List<string>();
            string record = "";
            int tableCount = 0;
            bool layerTable = false;
            bool entity = false;

            for (int k = 0; k < lines.Length; k += 2)
            {
                int group = int.Parse(lines[k].Trim(), CultureInfo.InvariantCulture);
                string value = lines[k + 1];
                if (group >= 60 && group <= 79)
                {
                    long iv = long.Parse(value, CultureInfo.InvariantCulture);
                    Assert.InRange(iv, -32768, 32767);
                }
                if (group == 0)
                {
                    record = value;
                    entity = false;
                }
                if (group == 100 && value == "AcDbEntity") entity = true;
                if (record == "TABLE" && group == 2) layerTable = value == "LAYER";
                if (record == "TABLE" && layerTable && group == 70)
                {
                    tableCount = int.Parse(value, CultureInfo.InvariantCulture);
                }
                if (record == "LAYER" && group == 2)
                {
                    Assert.True(layers.Add(value), $"duplicate LAYER {value}");
                    Assert.True(value.Length <= 2049);
                    var decoded = new List<ushort>();
                    for (int i = 0; i < value.Length;)
                    {
                        if (value[i] == '\\')
                        {
                            Assert.True(i + 7 <= value.Length && value.Substring(i, 3) == "\\U+");
                            decoded.Add(Convert.ToUInt16(value.Substring(i + 3, 4), 16));
                            i += 7;
                        }
                        else
                        {
                            char c = value[i++];
                            Assert.True(c >= 32 && c != 127);
                            decoded.Add(c);
                        }
                    }
                    Assert.InRange(decoded.Count, 1, 255);
                    for (int i = 0; i < decoded.Count; i++)
                    {
                        if (decoded[i] >= 0xD800 && decoded[i] <= 0xDBFF)
                        {
                            i++;
                            Assert.True(i < decoded.Count && decoded[i] >= 0xDC00 && decoded[i] <= 0xDFFF);
                        }
                        else
                        {
                            Assert.True(decoded[i] < 0xDC00 || decoded[i] > 0xDFFF);
                        }
                    }
                }
                if (group == 8 && entity)
                {
                    references.Add(value);
                    if (record == "POLYLINE" || record == "3DFACE")
                    {
                        meshLayers.Add(value);
                    }
                }
            }
            Assert.Equal(layers.Count, tableCount);
            foreach (string r in references)
            {
                Assert.Contains(r, layers);
            }
            return (layers, meshLayers);
        }

        [Fact]
        public void ToDxf_SerializesSceneTo3DxfText()
        {
            var scene = new Scene
            {
                SceneHierarchy = new InstanceNode { Name = "Root", DefinitionName = "RootDef" },
                GlbPrimitives = new List<GlbPrimitive> { Triangle("Walls") }
            };

            string dxfText = DxfExport.ToDxf(scene);
            Assert.Contains("$ACADVER", dxfText);
            Assert.Contains("AC1015", dxfText);
            Assert.Contains("POLYLINE", dxfText);
            Assert.Contains("AcDbPolyFaceMesh", dxfText);
            Assert.Contains("Walls", dxfText);
            Assert.Contains("EOF", dxfText);

            string dxf3d = DxfExport.ToDxf(scene, mode: "3dface");
            Assert.Contains("3DFACE", dxf3d);
            Assert.Contains("AcDbFace", dxf3d);
        }

        [Fact]
        public void UnicodeNamesLocaleLimitsAndMalformedMeshesMatchReference()
        {
            const string chinese = "mesh_27_ROOT__\u4e0a\u67dc(\u4fa7\u5305\u9876\u5e95)__\u9876\u677f_\u540a\u67dc";
            const string escaped = "mesh_27_ROOT__\\U+4E0A\\U+67DC(\\U+4FA7\\U+5305\\U+9876\\U+5E95)__\\U+9876\\U+677F_\\U+540A\\U+67DC";
            string longName = string.Concat(Enumerable.Repeat("\u67dc", 260));
            string[] names =
            {
                chinese, "Caf\u00e9", "\u67dc\U0001f600", longName + "A", longName + "B",
                new string('x', 245) + "\U0001f600" + new string('x', 20),
                "A/B", "A:B", "walls", "WALLS", "0", "Defpoints", "", "\t ",
                "line\nbreak\tname", chinese, new string('y', 300)
            };
            var scene = new Scene
            {
                GlbPrimitives = names.Select(Triangle).ToList()
            };

            foreach (string mode in new[] { "polyface", "3dface" })
            {
                string dxf = DxfExport.ToDxf(scene, 1.0, mode);
                var (layers, meshLayers) = Inspect(dxf);
                Assert.Contains(escaped, layers);
                Assert.Contains("Caf\\U+00E9", layers);
                Assert.Contains("\\U+67DC\\U+D83D\\U+DE00", layers);
                Assert.Equal(names.Length, meshLayers.Count);
                Assert.Equal(meshLayers[0], meshLayers[15]);
                var unique = meshLayers.Where((_, idx) => idx != 15).ToList();
                Assert.Equal(unique.Count, unique.Select(u => u.ToLowerInvariant()).Distinct().Count());

                string classic = DxfExport.ToDxf(scene, 1234.5, mode);
                CultureInfo previous = Thread.CurrentThread.CurrentCulture;
                try
                {
                    Thread.CurrentThread.CurrentCulture = new CultureInfo("de-DE");
                    Assert.Equal(classic, DxfExport.ToDxf(scene, 1234.5, mode));
                }
                finally
                {
                    Thread.CurrentThread.CurrentCulture = previous;
                }
            }

            string polyRef = DxfExport.ToDxf(scene, 1.0, "polyface");
            Assert.Equal(62908, polyRef.Length);
            Assert.Equal(0x561316A9u, Crc32Ascii(polyRef));

            string faceRef = DxfExport.ToDxf(scene, 1.0, "3dface");
            Assert.Equal(31833, faceRef.Length);
            Assert.Equal(0x89DCD865u, Crc32Ascii(faceRef));

            string emptyRef = DxfExport.ToDxf(new Scene(), DxfExport.MetresToInches, "polyface");
            Assert.Equal(16676, emptyRef.Length);
            Assert.Equal(0xFDEAA351u, Crc32Ascii(emptyRef));

            // 16-bit polyface limit fallback to 3DFACE
            var largePositions = new float[32768 * 3];
            largePositions[3] = 1f;
            largePositions[7] = 1f;
            var large = new GlbPrimitive
            {
                GeomName = "Large mesh",
                Positions = largePositions,
                Indices = new uint[] { 0, 1, 32767 }
            };
            var manyIndices = new uint[32768 * 3];
            for (int i = 0; i < 32768; i++)
            {
                manyIndices[i * 3] = 0;
                manyIndices[i * 3 + 1] = 1;
                manyIndices[i * 3 + 2] = 2;
            }
            var many = new GlbPrimitive
            {
                GeomName = "Many faces",
                Positions = new float[] { 0f, 0f, 0f, 1f, 0f, 0f, 0f, 1f, 0f },
                Indices = manyIndices
            };
            var limitScene = new Scene
            {
                GlbPrimitives = new List<GlbPrimitive> { large, many, Triangle("Fits") }
            };
            string limitDxf = DxfExport.ToDxf(limitScene, 1.0, "polyface");
            var (_, limitMeshLayers) = Inspect(limitDxf);
            Assert.Equal(1 + 32768 + 1, limitMeshLayers.Count);
            Assert.Equal(8863208, limitDxf.Length);
            Assert.Equal(0x1445729Au, Crc32Ascii(limitDxf));

            // Malformed mesh and non-finite scale rejection
            for (int failure = 0; failure < 5; failure++)
            {
                var bad = Triangle("Invalid mesh");
                if (failure == 0) bad.Positions = new float[] { 0f, 0f, 0f, 1f, 0f, 0f, 0f, 1f };
                if (failure == 1) bad.Indices = new uint[] { 0, 1 };
                if (failure == 2) bad.Indices = new uint[] { 3, 1, 2 };
                if (failure == 3) bad.Positions[0] = float.NaN;
                double scale = failure == 4 ? double.PositiveInfinity : 1.0;
                var badScene = new Scene { GlbPrimitives = new List<GlbPrimitive> { bad } };
                foreach (string mode in new[] { "polyface", "3dface" })
                {
                    Assert.Throws<ArgumentException>(() => DxfExport.ToDxf(badScene, scale, mode));
                }
            }
        }

        [Fact]
        public void MalformedUtf8AndSurrogateNamesKeepGeometryAndValidText()
        {
            var cases = new (string Bad, string Replacement)[]
            {
                (SurrogateEscape(0x80), "_"),
                (SurrogateEscape(0xFF), "_"),
                (SurrogateEscape(0xC0, 0xAF), "__"),
                (SurrogateEscape(0xE0, 0x80, 0xAF), "_"),
                (SurrogateEscape(0xF0, 0x80, 0x80, 0xAF), "_"),
                (SurrogateEscape(0xE4, (byte)'o', (byte)'k'), "_ok"),
                ("\uD800ok", "_ok"),
                (SurrogateEscape(0x80, 0xF0, 0x9F, 0x98, 0x80), "_\\U+D83D\\U+DE00"),
            };
            foreach (var (bad, replacement) in cases)
            {
                string name = "before" + bad + "after";
                var scene = new Scene
                {
                    GlbPrimitives = new List<GlbPrimitive>
                    {
                        Triangle(name), Triangle("unaffected"), Triangle(name)
                    }
                };
                var (_, meshLayers) = Inspect(DxfExport.ToDxf(scene, 1.0, "polyface"));
                string expected = "before" + replacement + "after";
                Assert.Equal(new[] { expected, "unaffected", expected }, meshLayers);
            }
        }

        [Fact]
        public void ToDxf_PreservesPaintedMaterialColorsAndSkipsUnpaintedBackPrimitives()
        {
            var builder = SkpCreate.NewFile();
            int redMat = builder.AddMaterial("Red", (255, 0, 0));
            int blueMat = builder.AddMaterial("Blue", (0, 0, 255));
            int redLayer = builder.AddLayer("RedLayer", (255, 0, 0));
            int blueLayer = builder.AddLayer("BlueLayer", (0, 0, 255));
            builder.AddFace(
                new (double, double, double)[] { (0, 0, 0), (10, 0, 0), (0, 10, 0) },
                material: redMat,
                backMaterial: redMat,
                layer: redLayer);
            builder.AddFace(
                new (double, double, double)[] { (20, 0, 0), (30, 0, 0), (20, 10, 0) },
                material: blueMat,
                backMaterial: blueMat,
                layer: blueLayer);
            // Unpainted face produces front + unpainted_back primitives in style mode;
            // DXF must export only front-surface primitives via Scene.SurfacePrimitives.
            builder.AddFace(new (double, double, double)[] { (40, 0, 0), (50, 0, 0), (40, 10, 0) });

            Scene scene = SkpFile.BuildScene(builder.ToBytes());
            Assert.Contains(scene.GlbPrimitives, p => p.UnpaintedBack);
            Assert.DoesNotContain(scene.SurfacePrimitives, p => p.UnpaintedBack);

            foreach (string mode in new[] { "polyface", "3dface" })
            {
                string dxf = DxfExport.ToDxf(scene, mode: mode);
                var (_, meshLayers) = Inspect(dxf);
                Assert.Equal(3, meshLayers.Count);

                string[] lines = dxf.Substring(0, dxf.Length - 2)
                    .Split(new[] { "\r\n" }, StringSplitOptions.None);
                var layerAci = new Dictionary<string, int>(StringComparer.Ordinal);
                var entityAci = new List<int>();
                string record = "";
                string currentLayer = "";
                bool entity = false;
                for (int k = 0; k < lines.Length; k += 2)
                {
                    int group = int.Parse(lines[k].Trim(), CultureInfo.InvariantCulture);
                    string val = lines[k + 1];
                    if (group == 0)
                    {
                        record = val;
                        currentLayer = "";
                        entity = false;
                    }
                    else if (group == 100 && val == "AcDbEntity")
                    {
                        entity = true;
                    }
                    else if (record == "LAYER" && group == 2)
                    {
                        currentLayer = val;
                    }
                    else if (record == "LAYER" && group == 62 && currentLayer != "0" && currentLayer != "Defpoints")
                    {
                        layerAci[currentLayer] = int.Parse(val, CultureInfo.InvariantCulture);
                    }
                    else if (entity && (record == "POLYLINE" || record == "3DFACE") && group == 62)
                    {
                        entityAci.Add(int.Parse(val, CultureInfo.InvariantCulture));
                    }
                }

                // Scene.GltfMaterials stores baseColorFactor as double[]; verify red (ACI 1) and blue (ACI 5)
                // are preserved on both LAYER records and mesh entities rather than falling back to grey (ACI 9).
                Assert.Contains(1, layerAci.Values);
                Assert.Contains(5, layerAci.Values);
                Assert.Contains(1, entityAci);
                Assert.Contains(5, entityAci);
            }
        }
    }
}
