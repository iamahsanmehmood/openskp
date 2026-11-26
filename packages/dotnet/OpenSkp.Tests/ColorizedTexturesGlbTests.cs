using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Text;
using System.Text.Json;
using Xunit;

namespace OpenSkp.Tests
{
    public class ColorizedTexturesGlbTests
    {
        private static string FixturePath(string name) =>
            Path.Combine(AppContext.BaseDirectory, "fixtures", name);

        private static (JsonElement Json, byte[] Binary) ParseGlb(byte[] bytes)
        {
            uint jsonChunkLen = BitConverter.ToUInt32(bytes, 12);
            string jsonStr = Encoding.UTF8.GetString(bytes, 20, (int)jsonChunkLen);
            JsonElement json = JsonDocument.Parse(jsonStr).RootElement.Clone();

            int binHeaderOffset = 20 + (int)jsonChunkLen;
            byte[] binary = Array.Empty<byte>();
            if (binHeaderOffset < bytes.Length)
            {
                uint binChunkLen = BitConverter.ToUInt32(bytes, binHeaderOffset);
                binary = new byte[binChunkLen];
                Buffer.BlockCopy(bytes, binHeaderOffset + 8, binary, 0, (int)binChunkLen);
            }
            return (json, binary);
        }

        private static byte[] ExtractGlbImageBytes(JsonElement json, byte[] binary, int imgIdx)
        {
            var img = json.GetProperty("images")[imgIdx];
            int bvIdx = img.GetProperty("bufferView").GetInt32();
            var bv = json.GetProperty("bufferViews")[bvIdx];
            int offset = bv.TryGetProperty("byteOffset", out var bo) ? bo.GetInt32() : 0;
            int length = bv.GetProperty("byteLength").GetInt32();
            var slice = new byte[length];
            Buffer.BlockCopy(binary, offset, slice, 0, length);
            return slice;
        }

        private static List<double> TranslationMatrix(double tx, double ty, double tz) =>
            new List<double>
            {
                1, 0, 0, 0,
                0, 1, 0, 0,
                0, 0, 1, 0,
                tx, ty, tz, 1,
            };

        private static GeometryBuilder MakeQuadBuilder(double size, long materialId)
        {
            var gb = new GeometryBuilder();
            gb.Vertices[1] = (0, 0, 0);
            gb.Vertices[2] = (size, 0, 0);
            gb.Vertices[3] = (size, size, 0);
            gb.Vertices[4] = (0, size, 0);
            gb.Edges[1] = (1, 2);
            gb.Edges[2] = (2, 3);
            gb.Edges[3] = (3, 4);
            gb.Edges[4] = (4, 1);
            gb.Faces[1] = new GeometryBuilderFace
            {
                Loops = new List<List<(long EdgeId, long Orientation)>>
                {
                    new List<(long, long)> { (1, 1), (2, 1), (3, 1), (4, 1) },
                },
                Normal = (0, 0, 1),
                MaterialId = materialId,
            };
            return gb;
        }

        private static (Scene Baked, InstancedScene Instanced) BuildColorizeQuadsScenes(int repeatsPerMaterial = 1)
        {
            var origParsed = Core.FullParse(File.ReadAllBytes(FixturePath("legacy_textured_colorize_2017.skp")));
            var parsed = new Core.RawParsed
            {
                Version = origParsed.Version,
                LayerColors = new Dictionary<string, (int R, int G, int B)> { ["Layer0"] = (200, 200, 200) },
                LayerIdToName = new Dictionary<long, string> { [1] = "Layer0" },
            };

            long nextMatId = 1;
            foreach (var kv in origParsed.Materials)
            {
                long id = nextMatId++;
                parsed.Materials[kv.Key] = kv.Value;
                parsed.MaterialIdToName[id] = kv.Key;
            }

            long shiftId = parsed.MaterialIdToName.First(kv => kv.Value == "shift").Key;
            parsed.DefsDict[1] = new Geometry.RawDefinition
            {
                Guid = "DEF_TILE",
                Name = "ColorizedTile",
                Builder = MakeQuadBuilder(12.0, shiftId),
            };

            var rootGb = parsed.Root.Builder;
            long vId = 1;
            long eId = 100;
            long fId = 1000;
            string[] names = { "plain", "tint", "shift", "tint_half" };
            for (int idx = 0; idx < names.Length; idx++)
            {
                long mId = parsed.MaterialIdToName.First(kv => kv.Value == names[idx]).Key;
                for (int r = 0; r < repeatsPerMaterial; r++)
                {
                    double x0 = idx * 24.0;
                    double y0 = r * 24.0;
                    long v1 = vId++, v2 = vId++, v3 = vId++, v4 = vId++;
                    rootGb.Vertices[v1] = (x0, y0, 0);
                    rootGb.Vertices[v2] = (x0 + 12.0, y0, 0);
                    rootGb.Vertices[v3] = (x0 + 12.0, y0 + 12.0, 0);
                    rootGb.Vertices[v4] = (x0, y0 + 12.0, 0);
                    long e1 = eId++, e2 = eId++, e3 = eId++, e4 = eId++;
                    rootGb.Edges[e1] = (v1, v2);
                    rootGb.Edges[e2] = (v2, v3);
                    rootGb.Edges[e3] = (v3, v4);
                    rootGb.Edges[e4] = (v4, v1);
                    rootGb.Faces[fId++] = new GeometryBuilderFace
                    {
                        Loops = new List<List<(long EdgeId, long Orientation)>>
                        {
                            new List<(long, long)> { (e1, 1), (e2, 1), (e3, 1), (e4, 1) },
                        },
                        Normal = (0, 0, 1),
                        MaterialId = mId,
                    };
                }
            }

            for (int r = 0; r < repeatsPerMaterial; r++)
            {
                rootGb.Instances.Add(new GeometryBuilderInstance
                {
                    RefIdx = 1,
                    Name = $"Tile_{r}",
                    Matrix = TranslationMatrix(200.0, r * 24.0, 0.0),
                });
            }

            return (SceneBuilder.Build(parsed), InstancedSceneBuilder.Build(parsed));
        }

        private static uint Crc32Test(byte[] buf, int offset, int length)
        {
            uint c = 0xFFFFFFFFu;
            for (int i = offset; i < offset + length; i++)
            {
                c ^= buf[i];
                for (int b = 0; b < 8; b++)
                {
                    c = (c & 1u) != 0 ? (0xEDB88320u ^ (c >> 1)) : (c >> 1);
                }
            }
            return c ^ 0xFFFFFFFFu;
        }

        private static void WriteBe32(byte[] buf, int offset, uint val)
        {
            buf[offset] = (byte)(val >> 24);
            buf[offset + 1] = (byte)(val >> 16);
            buf[offset + 2] = (byte)(val >> 8);
            buf[offset + 3] = (byte)val;
        }

        private static byte[] PngChunk(string type, byte[] payload)
        {
            var chunk = new byte[12 + payload.Length];
            WriteBe32(chunk, 0, (uint)payload.Length);
            for (int i = 0; i < 4; i++) chunk[4 + i] = (byte)type[i];
            Buffer.BlockCopy(payload, 0, chunk, 8, payload.Length);
            WriteBe32(chunk, 8 + payload.Length, Crc32Test(chunk, 4, 4 + payload.Length));
            return chunk;
        }

        private static byte[] ConcatBytes(params byte[][] parts)
        {
            int total = parts.Sum(p => p.Length);
            var res = new byte[total];
            int off = 0;
            foreach (var p in parts)
            {
                Buffer.BlockCopy(p, 0, res, off, p.Length);
                off += p.Length;
            }
            return res;
        }

        private static byte[] ZlibCompressTest(byte[] raw)
        {
            using var ms = new MemoryStream();
            ms.WriteByte(0x78);
            ms.WriteByte(0x9C);
            using (var ds = new DeflateStream(ms, CompressionLevel.Optimal, leaveOpen: true))
            {
                ds.Write(raw, 0, raw.Length);
            }
            uint adler = GlbTextures.ComputeAdler32(raw, 0, raw.Length);
            ms.WriteByte((byte)(adler >> 24));
            ms.WriteByte((byte)(adler >> 16));
            ms.WriteByte((byte)(adler >> 8));
            ms.WriteByte((byte)adler);
            return ms.ToArray();
        }

        private static byte[] MakeDeclaredSizePng(uint width, uint height)
        {
            byte[] sig = { 137, 80, 78, 71, 13, 10, 26, 10 };
            var ihdr = new byte[13];
            WriteBe32(ihdr, 0, width);
            WriteBe32(ihdr, 4, height);
            ihdr[8] = 8;
            ihdr[9] = 2;
            return ConcatBytes(
                sig,
                PngChunk("IHDR", ihdr),
                PngChunk("IDAT", new byte[] { 0x78, 0x9C }),
                PngChunk("IEND", Array.Empty<byte>()));
        }

        private static byte[] MakeTestSof0Jpeg(int width, int height, byte[] rgb)
        {
            byte[] app0 =
            {
                0xFF, 0xE0, 0x00, 0x10, 0x4A, 0x46, 0x49, 0x46,
                0x00, 0x01, 0x01, 0x00, 0x00, 0x01, 0x00, 0x01,
                0x00, 0x00,
            };
            byte[] sof0 =
            {
                0xFF, 0xC0, 0x00, 0x11, 0x08,
                (byte)((height >> 8) & 0xFF), (byte)(height & 0xFF),
                (byte)((width >> 8) & 0xFF), (byte)(width & 0xFF),
                0x03, 0x01, 0x11, 0x00, 0x02, 0x11, 0x01, 0x03, 0x11, 0x01,
            };
            int app2Len = 2 + 4 + rgb.Length;
            var app2 = new byte[2 + app2Len];
            app2[0] = 0xFF;
            app2[1] = 0xE2;
            app2[2] = (byte)((app2Len >> 8) & 0xFF);
            app2[3] = (byte)(app2Len & 0xFF);
            app2[4] = (byte)'T';
            app2[5] = (byte)'R';
            app2[6] = (byte)'G';
            app2[7] = (byte)'B';
            Buffer.BlockCopy(rgb, 0, app2, 8, rgb.Length);
            byte[] sosAndEoi =
            {
                0xFF, 0xDA, 0x00, 0x0C, 0x03, 0x01, 0x00, 0x02,
                0x11, 0x03, 0x11, 0x00, 0x3F, 0x00, 0x00, 0xFF, 0xD9,
            };
            return ConcatBytes(new byte[] { 0xFF, 0xD8 }, app0, sof0, app2, sosAndEoi);
        }

        private static byte[] InjectExifOrientationApp1(byte[] jpegBytes, int orientation = 6)
        {
            byte[] tiff =
            {
                0x49, 0x49, 42, 0, 8, 0, 0, 0,
                1, 0, 0x12, 0x01, 3, 0, 1, 0, 0, 0,
                (byte)(orientation & 0xFF), (byte)((orientation >> 8) & 0xFF), 0, 0,
                0, 0, 0, 0,
            };
            byte[] exifHeader = { (byte)'E', (byte)'x', (byte)'i', (byte)'f', 0, 0 };
            int segLen = 2 + exifHeader.Length + tiff.Length;
            var app1 = new byte[2 + segLen];
            app1[0] = 0xFF;
            app1[1] = 0xE1;
            app1[2] = (byte)((segLen >> 8) & 0xFF);
            app1[3] = (byte)(segLen & 0xFF);
            Buffer.BlockCopy(exifHeader, 0, app1, 4, exifHeader.Length);
            Buffer.BlockCopy(tiff, 0, app1, 4 + exifHeader.Length, tiff.Length);
            var rest = new byte[jpegBytes.Length - 2];
            Buffer.BlockCopy(jpegBytes, 2, rest, 0, rest.Length);
            return ConcatBytes(new byte[] { jpegBytes[0], jpegBytes[1] }, app1, rest);
        }

        private static bool HasExifSignature(byte[] bytes)
        {
            for (int i = 0; i + 6 <= bytes.Length; i++)
            {
                if (bytes[i] == (byte)'E' &&
                    bytes[i + 1] == (byte)'x' &&
                    bytes[i + 2] == (byte)'i' &&
                    bytes[i + 3] == (byte)'f' &&
                    bytes[i + 4] == 0 &&
                    bytes[i + 5] == 0)
                {
                    return true;
                }
            }
            return false;
        }

        private sealed class TestSof0JpegCodec : ISkpImageCodec
        {
            public int Decodes { get; private set; }
            public List<(int Quality, string MimeType)> Encodes { get; } = new List<(int, string)>();

            public (int Width, int Height, byte[] Rgba)? Decode(byte[] data, string mimeType)
            {
                Assert.Equal("image/jpeg", mimeType);
                Decodes++;
                if (data.Length < 4 || data[0] != 0xFF || data[1] != 0xD8) return null;
                int pos = 2;
                int width = 0;
                int height = 0;
                byte[]? rgbPayload = null;
                while (pos + 4 <= data.Length && data[pos] == 0xFF)
                {
                    while (pos < data.Length && data[pos] == 0xFF) pos++;
                    byte marker = data[pos++];
                    if (marker == 0xD9 || marker == 0xDA) break;
                    if (marker == 0x01 || (marker >= 0xD0 && marker <= 0xD8)) continue;
                    int segLen = (data[pos] << 8) | data[pos + 1];
                    if (marker == 0xC0)
                    {
                        height = (data[pos + 3] << 8) | data[pos + 4];
                        width = (data[pos + 5] << 8) | data[pos + 6];
                    }
                    else if (marker == 0xE2 && segLen >= 6 &&
                             data[pos + 2] == (byte)'T' &&
                             data[pos + 3] == (byte)'R' &&
                             data[pos + 4] == (byte)'G' &&
                             data[pos + 5] == (byte)'B')
                    {
                        int payloadLen = segLen - 6;
                        rgbPayload = new byte[payloadLen];
                        Buffer.BlockCopy(data, pos + 6, rgbPayload, 0, payloadLen);
                    }
                    pos += segLen;
                }
                if (width <= 0 || height <= 0 || rgbPayload == null || rgbPayload.Length < width * height * 3)
                {
                    return null;
                }
                var rgba = new byte[width * height * 4];
                for (int i = 0; i < width * height; i++)
                {
                    rgba[i * 4] = rgbPayload[i * 3];
                    rgba[i * 4 + 1] = rgbPayload[i * 3 + 1];
                    rgba[i * 4 + 2] = rgbPayload[i * 3 + 2];
                    rgba[i * 4 + 3] = 255;
                }
                return (width, height, rgba);
            }

            public byte[] Encode(byte[] rgba, int width, int height, string mimeType, int quality = 95)
            {
                Encodes.Add((quality, mimeType));
                Assert.Equal(width * height * 4, rgba.Length);
                var rgb = new byte[width * height * 3];
                for (int i = 0; i < width * height; i++)
                {
                    rgb[i * 3] = rgba[i * 4];
                    rgb[i * 3 + 1] = rgba[i * 4 + 1];
                    rgb[i * 3 + 2] = rgba[i * 4 + 2];
                }
                return MakeTestSof0Jpeg(width, height, rgb);
            }
        }

        private sealed class SpyNoOpCodec : ISkpImageCodec
        {
            public int DecodeCalls { get; private set; }

            public (int Width, int Height, byte[] Rgba)? Decode(byte[] data, string mimeType)
            {
                DecodeCalls++;
                return null;
            }

            public byte[] Encode(byte[] rgba, int width, int height, string mimeType, int quality = 95) =>
                Array.Empty<byte>();
        }

        [Fact]
        public void PublicApiExposesISkpImageCodecWithoutLeakingInternals()
        {
            var exported = typeof(GlbExport).Assembly.GetExportedTypes().Select(t => t.Name).ToHashSet();
            Assert.Contains(nameof(ISkpImageCodec), exported);
            Assert.DoesNotContain("GlbTextures", exported);
            Assert.DoesNotContain("GltfTextureBakeStats", exported);
        }

        [Fact]
        public void BakesShiftTintAndOpacityPngsWithoutMutatingSceneOrInstancedScene()
        {
            var (scene, instanced) = BuildColorizeQuadsScenes(3);

            string origSceneMatsJson = MiniJson.Serialize(scene.GltfMaterials);
            string origInstMatsJson = MiniJson.Serialize(instanced.GltfMaterials);
            byte[] origTexBytes = scene.Textures[0].Data.ToArray();

            var expected = new HashSet<string>
            {
                "200,40,40|40,40,200|100",
                "32,237,80|32,237,80|100",
                "172,253,46|253,46,172|100",
                "65,73,255|65,73,255|50",
            };

            string tmpBaked = Path.Combine(Path.GetTempPath(), $"openskp_col_baked_{Guid.NewGuid():N}.glb");
            string tmpInst = Path.Combine(Path.GetTempPath(), $"openskp_col_inst_{Guid.NewGuid():N}.glb");
            try
            {
                GlbExport.ExportGlb(scene, tmpBaked, new GlbOptions { Textures = true });
                InstancedGlbExport.ExportInstancedGlb(instanced, tmpInst, new InstancedGlbOptions { Textures = true });

                foreach (var glbBytes in new[]
                {
                    GlbExport.ToGlb(scene, new GlbOptions { Textures = true }),
                    InstancedGlbExport.ToInstancedGlb(instanced, new InstancedGlbOptions { Textures = true }),
                    File.ReadAllBytes(tmpBaked),
                    File.ReadAllBytes(tmpInst),
                })
                {
                    Assert.Equal(origSceneMatsJson, MiniJson.Serialize(scene.GltfMaterials));
                    Assert.Equal(origInstMatsJson, MiniJson.Serialize(instanced.GltfMaterials));
                    Assert.Equal(origTexBytes, scene.Textures[0].Data);
                    Assert.Equal(origTexBytes, instanced.Textures[0].Data);

                    var (json, binary) = ParseGlb(glbBytes);
                    Assert.Equal(4, json.GetProperty("images").GetArrayLength());
                    var actual = new HashSet<string>();
                    int texturedCount = 0;

                    foreach (var mat in json.GetProperty("materials").EnumerateArray())
                    {
                        var pbr = mat.GetProperty("pbrMetallicRoughness");
                        if (!pbr.TryGetProperty("baseColorTexture", out var texRef)) continue;
                        texturedCount++;
                        var bcf = pbr.GetProperty("baseColorFactor").EnumerateArray().Select(e => e.GetDouble()).ToArray();
                        Assert.Equal(1.0, bcf[0], 6);
                        Assert.Equal(1.0, bcf[1], 6);
                        Assert.Equal(1.0, bcf[2], 6);

                        int imgIdx = json.GetProperty("textures")[texRef.GetProperty("index").GetInt32()]
                            .GetProperty("source").GetInt32();
                        Assert.Equal("image/png", json.GetProperty("images")[imgIdx].GetProperty("mimeType").GetString());
                        byte[] imgBytes = ExtractGlbImageBytes(json, binary, imgIdx);
                        var (w, h, rgba) = GlbTextures.DecodePngRgba(imgBytes);
                        Assert.Equal(2, w);
                        Assert.Equal(1, h);
                        string left = string.Format(CultureInfo.InvariantCulture, "{0},{1},{2}", rgba[0], rgba[1], rgba[2]);
                        string right = string.Format(CultureInfo.InvariantCulture, "{0},{1},{2}", rgba[4], rgba[5], rgba[6]);
                        int opacityPct = (int)Math.Round(bcf[3] * 100.0);
                        actual.Add(string.Format(CultureInfo.InvariantCulture, "{0}|{1}|{2}", left, right, opacityPct));
                    }

                    Assert.Equal(4, texturedCount);
                    Assert.Equal(expected, actual);
                }
            }
            finally
            {
                if (File.Exists(tmpBaked)) File.Delete(tmpBaked);
                if (File.Exists(tmpInst)) File.Delete(tmpInst);
            }
        }

        [Fact]
        public void DecodesEachTextureOnceAndBakesOncePerUniqueColor()
        {
            var (scene, instanced) = BuildColorizeQuadsScenes(8);

            foreach (var (textures, materials) in new[]
            {
                (scene.Textures, scene.GltfMaterials),
                (instanced.Textures, instanced.GltfMaterials),
            })
            {
                var stats = new GltfTextureBakeStats();
                var resolved = GlbTextures.ResolveGltfTextures(textures, materials, embed: true, onLog: null, imageCodec: null, stats: stats);
                Assert.Equal(4, resolved.Textures.Count);
                Assert.Equal(1, stats.DecodeCalls);
                Assert.Equal(3, stats.ColorizeCalls);
            }

            byte[] png1x1 = GlbTextures.EncodePngU8(new byte[] { 180, 50, 90 }, 1, 1, 3);
            var sharedMats = new List<object>();
            foreach (double alpha in new[] { 1.0, 0.5 })
            {
                sharedMats.Add(new Dictionary<string, object>
                {
                    ["name"] = $"m_{alpha}",
                    ["pbrMetallicRoughness"] = new Dictionary<string, object>
                    {
                        ["baseColorFactor"] = new[] { 200.0 / 255.0, 90.0 / 255.0, 20.0 / 255.0, alpha },
                        ["baseColorTexture"] = new Dictionary<string, object> { ["index"] = 0 },
                        ["metallicFactor"] = 0.0,
                        ["roughnessFactor"] = 0.8,
                    },
                    ["extras"] = new Dictionary<string, object>
                    {
                        ["sketchup"] = new Dictionary<string, object>
                        {
                            ["colorized"] = true,
                            ["colorize_type"] = 0,
                        },
                    },
                });
            }

            var stats2 = new GltfTextureBakeStats();
            var res2 = GlbTextures.ResolveGltfTextures(
                new List<SceneTexture> { new SceneTexture { Data = png1x1, MimeType = "image/png", Filename = "one.png" } },
                sharedMats,
                embed: true,
                onLog: null,
                imageCodec: null,
                stats: stats2);
            Assert.Equal(1, stats2.DecodeCalls);
            Assert.Equal(1, stats2.ColorizeCalls);
            Assert.Single(res2.Textures);
        }

        [Fact]
        public void DecodesIndexedSubByteGreyscale16BitTrnsAndAdam7Pngs()
        {
            byte[] sig = { 137, 80, 78, 71, 13, 10, 26, 10 };

            // 1) 4-bit indexed PNG with PLTE + tRNS (2x1: alpha=64 red, opaque blue)
            byte[] ihdrIdx = { 0, 0, 0, 2, 0, 0, 0, 1, 4, 3, 0, 0, 0 };
            byte[] plte = { 250, 10, 20, 30, 40, 240 };
            byte[] trns = { 64 };
            byte[] idatIdx = ZlibCompressTest(new byte[] { 0, 0x01 });
            byte[] indexedPng = ConcatBytes(
                sig,
                PngChunk("IHDR", ihdrIdx),
                PngChunk("PLTE", plte),
                PngChunk("tRNS", trns),
                PngChunk("IDAT", idatIdx),
                PngChunk("IEND", Array.Empty<byte>()));
            var decIdx = GlbTextures.DecodePngRgba(indexedPng);
            Assert.Equal(new byte[] { 250, 10, 20, 64, 30, 40, 240, 255 }, decIdx.Rgba);

            // 2) 2-bit greyscale PNG with tRNS (4x1: samples 0, 1, 2, 3; sample 1 transparent)
            byte[] ihdrGrey = { 0, 0, 0, 4, 0, 0, 0, 1, 2, 0, 0, 0, 0 };
            byte[] trnsGrey = { 0, 1 };
            byte[] idatGrey = ZlibCompressTest(new byte[] { 0, 0b00_01_10_11 });
            byte[] greyPng = ConcatBytes(
                sig,
                PngChunk("IHDR", ihdrGrey),
                PngChunk("tRNS", trnsGrey),
                PngChunk("IDAT", idatGrey),
                PngChunk("IEND", Array.Empty<byte>()));
            var decGrey = GlbTextures.DecodePngRgba(greyPng);
            Assert.Equal(
                new byte[]
                {
                    0, 0, 0, 255,
                    85, 85, 85, 0,
                    170, 170, 170, 255,
                    255, 255, 255, 255,
                },
                decGrey.Rgba);

            // 3) 16-bit RGB PNG with Adam7 interlace (3x3)
            byte[] ihdrAdam7 = { 0, 0, 0, 3, 0, 0, 0, 3, 16, 2, 0, 0, 1 };
            var passScanlines = new List<byte>();
            var expectedGrid = new byte[3 * 3 * 4];
            for (int y = 0; y < 3; y++)
            {
                for (int x = 0; x < 3; x++)
                {
                    int i4 = (y * 3 + x) * 4;
                    expectedGrid[i4] = (byte)(x * 100);
                    expectedGrid[i4 + 1] = (byte)(y * 100);
                    expectedGrid[i4 + 2] = 128;
                    expectedGrid[i4 + 3] = 255;
                }
            }
            (int X0, int Dx, int Y0, int Dy)[] adam7 =
            {
                (0, 8, 0, 8), (4, 8, 0, 8), (0, 4, 4, 8), (2, 4, 0, 4),
                (0, 2, 2, 4), (1, 2, 0, 2), (0, 1, 1, 2),
            };
            foreach (var (x0, dx, y0, dy) in adam7)
            {
                int pw = 3 > x0 ? (3 - x0 + dx - 1) / dx : 0;
                int ph = 3 > y0 ? (3 - y0 + dy - 1) / dy : 0;
                if (pw == 0 || ph == 0) continue;
                for (int py = 0; py < ph; py++)
                {
                    passScanlines.Add(0);
                    int y = y0 + py * dy;
                    for (int px = 0; px < pw; px++)
                    {
                        int x = x0 + px * dx;
                        int[] c8s = { x * 100, y * 100, 128 };
                        foreach (int c8 in c8s)
                        {
                            int c16 = c8 * 257;
                            passScanlines.Add((byte)((c16 >> 8) & 0xFF));
                            passScanlines.Add((byte)(c16 & 0xFF));
                        }
                    }
                }
            }
            byte[] adam7Png = ConcatBytes(
                sig,
                PngChunk("IHDR", ihdrAdam7),
                PngChunk("IDAT", ZlibCompressTest(passScanlines.ToArray())),
                PngChunk("IEND", Array.Empty<byte>()));
            var decAdam7 = GlbTextures.DecodePngRgba(adam7Png);
            Assert.Equal(3, decAdam7.Width);
            Assert.Equal(3, decAdam7.Height);
            Assert.Equal(expectedGrid, decAdam7.Rgba);
        }

        [Fact]
        public void MatchesGreyscaleAlphaAchromaticTargetAndRowBlockRules()
        {
            byte[] grey = { 0, 0, 0, 0, 128, 128, 128, 64, 255, 255, 255, 255 };
            byte[] shifted = GlbTextures.ColorizeImageU8(grey, 3, 1, (200, 90, 20), 0, keepAlpha: true);
            byte[] tinted = GlbTextures.ColorizeImageU8(grey, 3, 1, (200, 90, 20), 1, keepAlpha: true);
            Assert.Equal(new byte[] { 0, 64, 255 }, new[] { shifted[3], shifted[7], shifted[11] });
            Assert.Equal(new byte[] { 0, 64, 255 }, new[] { tinted[3], tinted[7], tinted[11] });
            Assert.True(shifted[0] > 0);

            byte[] vivid = { 240, 30, 80, 180, 20, 210, 90, 255 };
            foreach (int ctype in new[] { 0, 1 })
            {
                byte[] desat = GlbTextures.ColorizeImageU8(vivid, 2, 1, (140, 140, 140), ctype, keepAlpha: true);
                Assert.Equal(desat[0], desat[1]);
                Assert.Equal(desat[1], desat[2]);
                Assert.Equal(180, desat[3]);
                Assert.Equal(desat[4], desat[5]);
                Assert.Equal(desat[5], desat[6]);
                Assert.Equal(255, desat[7]);
            }

            byte[] greyAvgChrom = { 255, 1, 1, 255, 1, 255, 255, 255 };
            byte[] s0 = GlbTextures.ColorizeImageU8(greyAvgChrom, 2, 1, (140, 140, 140), 0, keepAlpha: false);
            byte[] s1 = GlbTextures.ColorizeImageU8(greyAvgChrom, 2, 1, (140, 140, 140), 1, keepAlpha: false);
            int[] exp0 = { 255, 24, 24, 24, 255, 255 };
            int[] exp1 = { 255, 24, 24, 255, 24, 24 };
            for (int i = 0; i < 6; i++)
            {
                Assert.InRange(Math.Abs(s0[i] - exp0[i]), 0, 1);
                Assert.InRange(Math.Abs(s1[i] - exp1[i]), 0, 1);
            }

            var multiRow = new byte[19 * 37 * 4];
            uint seed = 7u;
            for (int i = 0; i < multiRow.Length; i++)
            {
                seed = unchecked(seed * 1664525u + 1013904223u);
                multiRow[i] = (byte)(seed & 0xFFu);
            }
            foreach (int ctype in new[] { 0, 1 })
            {
                byte[] whole = GlbTextures.ColorizeImageU8(multiRow, 19, 37, (200, 90, 20), ctype, keepAlpha: true, maxBlockPixels: 100000);
                byte[] chunked = GlbTextures.ColorizeImageU8(multiRow, 19, 37, (200, 90, 20), ctype, keepAlpha: true, maxBlockPixels: 40);
                Assert.Equal(whole, chunked);
            }
        }

        [Fact]
        public void StripsExifApp1WarnsOnceWithoutImageCodecAndBakesJpegsAtQuality95WithImageCodec()
        {
            byte[] baseRgb = { 200, 40, 40, 40, 40, 200 };
            byte[] cleanJpeg = MakeTestSof0Jpeg(2, 1, baseRgb);
            byte[] exifJpeg = InjectExifOrientationApp1(cleanJpeg, 6);
            Assert.True(HasExifSignature(exifJpeg));
            Assert.Equal(cleanJpeg, GlbTextures.StripJpegExifApp1(exifJpeg));

            Geometry.RawMaterial MakeJpegMat(string name, (int R, int G, int B) rgb, bool colorized, int colorizeType) =>
                new Geometry.RawMaterial
                {
                    Name = name,
                    R = rgb.R,
                    G = rgb.G,
                    B = rgb.B,
                    A = 255,
                    Transparency = 1.0,
                    Colorized = colorized,
                    ColorizeType = colorizeType,
                    Texture = new Geometry.RawTexture
                    {
                        Filename = "exif6.jpg",
                        XScale = 24.0,
                        YScale = 12.0,
                        Data = exifJpeg,
                    },
                };

            var jpgParsed = new Core.RawParsed
            {
                Version = "17.0.1",
                LayerColors = new Dictionary<string, (int R, int G, int B)> { ["Layer0"] = (200, 200, 200) },
                LayerIdToName = new Dictionary<long, string> { [1] = "Layer0" },
                Materials = new Dictionary<string, Geometry.RawMaterial>
                {
                    ["PlainJpg"] = MakeJpegMat("PlainJpg", (255, 255, 255), false, 0),
                    ["ShiftJpg"] = MakeJpegMat("ShiftJpg", (200, 90, 20), true, 0),
                    ["TintJpg"] = MakeJpegMat("TintJpg", (30, 160, 60), true, 1),
                },
                MaterialIdToName = new Dictionary<long, string>
                {
                    [1] = "PlainJpg",
                    [2] = "ShiftJpg",
                    [3] = "TintJpg",
                },
                DefsDict = new Dictionary<long, Geometry.RawDefinition>
                {
                    [1] = new Geometry.RawDefinition { Guid = "P1", Name = "P1", Builder = MakeQuadBuilder(24.0, 1) },
                    [2] = new Geometry.RawDefinition { Guid = "P2", Name = "P2", Builder = MakeQuadBuilder(24.0, 2) },
                    [3] = new Geometry.RawDefinition { Guid = "P3", Name = "P3", Builder = MakeQuadBuilder(24.0, 3) },
                },
            };
            jpgParsed.Root.Builder.Instances.Add(new GeometryBuilderInstance { RefIdx = 1, Name = "i1", Matrix = TranslationMatrix(0, 0, 0) });
            jpgParsed.Root.Builder.Instances.Add(new GeometryBuilderInstance { RefIdx = 2, Name = "i2", Matrix = TranslationMatrix(30, 0, 0) });
            jpgParsed.Root.Builder.Instances.Add(new GeometryBuilderInstance { RefIdx = 3, Name = "i3", Matrix = TranslationMatrix(60, 0, 0) });

            var scene = SceneBuilder.Build(jpgParsed);
            var instanced = InstancedSceneBuilder.Build(jpgParsed);
            Assert.Single(scene.Textures);
            Assert.Equal(exifJpeg, scene.Textures[0].Data);

            // 1) Without ImageCodec: 1 warning, EXIF APP1 stripped without re-encoding, tinted factors preserved.
            foreach (bool isInst in new[] { false, true })
            {
                var warnings = new List<string>();
                void OnLog(SkpLogLevel lvl, string msg)
                {
                    if (lvl == SkpLogLevel.Warning) warnings.Add(msg);
                }

                byte[] glb = isInst
                    ? InstancedGlbExport.ToInstancedGlb(instanced, new InstancedGlbOptions { Textures = true, OnLog = OnLog })
                    : GlbExport.ToGlb(scene, new GlbOptions { Textures = true, OnLog = OnLog });

                string warn = Assert.Single(warnings);
                Assert.Contains("ImageCodec", warn, StringComparison.Ordinal);

                var (json, binary) = ParseGlb(glb);
                Assert.Equal(1, json.GetProperty("images").GetArrayLength());
                Assert.Equal("image/jpeg", json.GetProperty("images")[0].GetProperty("mimeType").GetString());
                byte[] imgBytes = ExtractGlbImageBytes(json, binary, 0);
                Assert.False(HasExifSignature(imgBytes));
                Assert.Equal(cleanJpeg, imgBytes);

                var mats = json.GetProperty("materials").EnumerateArray().ToList();
                var shiftMat = Assert.Single(mats, m =>
                    m.TryGetProperty("extras", out var ex) &&
                    ex.GetProperty("sketchup").GetProperty("colorize_type").GetInt32() == 0);
                var tintMat = Assert.Single(mats, m =>
                    m.TryGetProperty("extras", out var ex) &&
                    ex.GetProperty("sketchup").GetProperty("colorize_type").GetInt32() == 1);

                var shiftBcf = shiftMat.GetProperty("pbrMetallicRoughness").GetProperty("baseColorFactor").EnumerateArray().Select(e => e.GetDouble()).ToArray();
                var tintBcf = tintMat.GetProperty("pbrMetallicRoughness").GetProperty("baseColorFactor").EnumerateArray().Select(e => e.GetDouble()).ToArray();
                Assert.Equal(200.0 / 255.0, shiftBcf[0], 9);
                Assert.Equal(90.0 / 255.0, shiftBcf[1], 9);
                Assert.Equal(20.0 / 255.0, shiftBcf[2], 9);
                Assert.Equal(1.0, shiftBcf[3], 9);
                Assert.Equal(30.0 / 255.0, tintBcf[0], 9);
                Assert.Equal(160.0 / 255.0, tintBcf[1], 9);
                Assert.Equal(60.0 / 255.0, tintBcf[2], 9);
                Assert.Equal(1.0, tintBcf[3], 9);
            }

            // 2) With ImageCodec: decoded once, encoded at quality 95 once per colour, white factor, EXIF stripped on plain.
            foreach (bool isInst in new[] { false, true })
            {
                var codec = new TestSof0JpegCodec();
                var warnings = new List<string>();
                void OnLog(SkpLogLevel lvl, string msg)
                {
                    if (lvl == SkpLogLevel.Warning) warnings.Add(msg);
                }

                byte[] glb = isInst
                    ? InstancedGlbExport.ToInstancedGlb(instanced, new InstancedGlbOptions { Textures = true, ImageCodec = codec, OnLog = OnLog })
                    : GlbExport.ToGlb(scene, new GlbOptions { Textures = true, ImageCodec = codec, OnLog = OnLog });

                Assert.Empty(warnings);
                Assert.Equal(1, codec.Decodes);
                Assert.Equal(new[] { (95, "image/jpeg"), (95, "image/jpeg") }, codec.Encodes);

                var (json, binary) = ParseGlb(glb);
                Assert.Equal(3, json.GetProperty("images").GetArrayLength());
                var seenPixels = new HashSet<string>();
                foreach (var mat in json.GetProperty("materials").EnumerateArray())
                {
                    var pbr = mat.GetProperty("pbrMetallicRoughness");
                    if (!pbr.TryGetProperty("baseColorTexture", out var texRef)) continue;
                    var bcf = pbr.GetProperty("baseColorFactor").EnumerateArray().Select(e => e.GetDouble()).ToArray();
                    Assert.Equal(new[] { 1.0, 1.0, 1.0, 1.0 }, bcf);
                    int imgIdx = json.GetProperty("textures")[texRef.GetProperty("index").GetInt32()]
                        .GetProperty("source").GetInt32();
                    Assert.Equal("image/jpeg", json.GetProperty("images")[imgIdx].GetProperty("mimeType").GetString());
                    byte[] blob = ExtractGlbImageBytes(json, binary, imgIdx);
                    Assert.False(HasExifSignature(blob));
                    var dec = codec.Decode(blob, "image/jpeg");
                    Assert.True(dec.HasValue);
                    var rgba = dec!.Value.Rgba;
                    seenPixels.Add(string.Format(
                        CultureInfo.InvariantCulture,
                        "{0},{1},{2}|{3},{4},{5}",
                        rgba[0], rgba[1], rgba[2],
                        rgba[4], rgba[5], rgba[6]));
                }

                Assert.Equal(
                    new HashSet<string>
                    {
                        "200,40,40|40,40,200",
                        "32,237,80|32,237,80",
                        "172,253,46|253,46,172",
                    },
                    seenPixels);
            }
        }

        [Fact]
        public void RejectsOversizePngAndJpegHeadersAndCorruptImagesWithoutDecoding()
        {
            byte[] oversizePng = MakeDeclaredSizePng(40000u, 40000u);
            Assert.True(oversizePng.Length < 100);

            var matShift = new Dictionary<string, object>
            {
                ["name"] = "OversizeShift",
                ["pbrMetallicRoughness"] = new Dictionary<string, object>
                {
                    ["baseColorFactor"] = new[] { 200.0 / 255.0, 90.0 / 255.0, 20.0 / 255.0, 1.0 },
                    ["baseColorTexture"] = new Dictionary<string, object> { ["index"] = 0 },
                    ["metallicFactor"] = 0.0,
                    ["roughnessFactor"] = 0.8,
                },
                ["extras"] = new Dictionary<string, object>
                {
                    ["sketchup"] = new Dictionary<string, object> { ["colorized"] = true, ["colorize_type"] = 0 },
                },
            };
            var matTint = new Dictionary<string, object>
            {
                ["name"] = "OversizeTint",
                ["pbrMetallicRoughness"] = new Dictionary<string, object>
                {
                    ["baseColorFactor"] = new[] { 30.0 / 255.0, 160.0 / 255.0, 60.0 / 255.0, 0.5 },
                    ["baseColorTexture"] = new Dictionary<string, object> { ["index"] = 0 },
                    ["metallicFactor"] = 0.0,
                    ["roughnessFactor"] = 0.8,
                },
                ["extras"] = new Dictionary<string, object>
                {
                    ["sketchup"] = new Dictionary<string, object> { ["colorized"] = true, ["colorize_type"] = 1 },
                },
            };

            var prim0 = new GlbPrimitive
            {
                Positions = new float[] { 0, 0, 0, 1, 0, 0, 0, 1, 0 },
                Normals = new float[] { 0, 0, 1, 0, 0, 1, 0, 0, 1 },
                Uvs = new float[] { 0, 0, 1, 0, 0, 1 },
                Indices = new uint[] { 0, 1, 2 },
                MaterialIndex = 0,
                GeomName = "tri0",
            };
            var prim1 = new GlbPrimitive
            {
                Positions = prim0.Positions,
                Normals = prim0.Normals,
                Uvs = prim0.Uvs,
                Indices = prim0.Indices,
                MaterialIndex = 1,
                GeomName = "tri1",
            };

            var baked = new Scene
            {
                GltfMaterials = new List<object> { matShift, matTint },
                Textures = new List<SceneTexture>
                {
                    new SceneTexture { Data = oversizePng, MimeType = "image/png", Filename = "oversize.png" },
                },
                GlbPrimitives = new List<GlbPrimitive> { prim0, prim1 },
            };

            var inst = new InstancedScene
            {
                SceneHierarchy = new InstancedNode { Name = "Root", MeshResourceId = "m0" },
                MeshResources = new List<InstancedMeshResource>
                {
                    new InstancedMeshResource
                    {
                        Id = "m0",
                        DefinitionId = 1,
                        DefinitionName = "Def",
                        VariantKey = "v0",
                        Primitives = new List<LocalPrimitive>
                        {
                            new LocalPrimitive
                            {
                                Positions = prim0.Positions,
                                Normals = prim0.Normals,
                                Uvs = prim0.Uvs,
                                Indices = prim0.Indices,
                                MaterialIndex = 0,
                            },
                            new LocalPrimitive
                            {
                                Positions = prim0.Positions,
                                Normals = prim0.Normals,
                                Uvs = prim0.Uvs,
                                Indices = prim0.Indices,
                                MaterialIndex = 1,
                            },
                        },
                    },
                },
                GltfMaterials = new List<object> { matShift, matTint },
                Textures = new List<SceneTexture>
                {
                    new SceneTexture { Data = oversizePng, MimeType = "image/png", Filename = "oversize.png" },
                },
            };

            foreach (bool isInst in new[] { false, true })
            {
                var warnings = new List<string>();
                void OnLog(SkpLogLevel lvl, string msg)
                {
                    if (lvl == SkpLogLevel.Warning) warnings.Add(msg);
                }

                byte[] glb = isInst
                    ? InstancedGlbExport.ToInstancedGlb(inst, new InstancedGlbOptions { Textures = true, OnLog = OnLog })
                    : GlbExport.ToGlb(baked, new GlbOptions { Textures = true, OnLog = OnLog });

                string w = Assert.Single(warnings);
                Assert.Contains("40000x40000", w, StringComparison.Ordinal);
                Assert.Contains("1600000000", w, StringComparison.Ordinal);
                Assert.Contains("178956970", w, StringComparison.Ordinal);

                var (json, binary) = ParseGlb(glb);
                Assert.Equal(1, json.GetProperty("images").GetArrayLength());
                Assert.Equal(oversizePng, ExtractGlbImageBytes(json, binary, 0));

                var bcf0 = json.GetProperty("materials")[0].GetProperty("pbrMetallicRoughness").GetProperty("baseColorFactor").EnumerateArray().Select(e => e.GetDouble()).ToArray();
                Assert.Equal(200.0 / 255.0, bcf0[0], 9);
                Assert.Equal(90.0 / 255.0, bcf0[1], 9);
                Assert.Equal(20.0 / 255.0, bcf0[2], 9);
                Assert.Equal(1.0, bcf0[3], 9);

                var bcf1 = json.GetProperty("materials")[1].GetProperty("pbrMetallicRoughness").GetProperty("baseColorFactor").EnumerateArray().Select(e => e.GetDouble()).ToArray();
                Assert.Equal(30.0 / 255.0, bcf1[0], 9);
                Assert.Equal(160.0 / 255.0, bcf1[1], 9);
                Assert.Equal(60.0 / 255.0, bcf1[2], 9);
                Assert.Equal(0.5, bcf1[3], 9);
            }

            // 2) Oversize JPEG header (50000x50000 = 2.5B pixels > int.MaxValue) never calls ImageCodec.Decode
            byte[] oversizeJpg = MakeTestSof0Jpeg(50000, 50000, new byte[] { 10, 20, 30 });
            var spyCodec = new SpyNoOpCodec();
            var jpgWarnings = new List<string>();
            var jpgScene = new Scene
            {
                GltfMaterials = baked.GltfMaterials,
                GlbPrimitives = baked.GlbPrimitives,
                Textures = new List<SceneTexture>
                {
                    new SceneTexture { Data = oversizeJpg, MimeType = "image/jpeg", Filename = "huge.jpg" },
                },
            };
            byte[] jpgGlb = GlbExport.ToGlb(jpgScene, new GlbOptions
            {
                Textures = true,
                ImageCodec = spyCodec,
                OnLog = (lvl, msg) =>
                {
                    if (lvl == SkpLogLevel.Warning) jpgWarnings.Add(msg);
                },
            });
            Assert.Equal(0, spyCodec.DecodeCalls);
            string jw = Assert.Single(jpgWarnings);
            Assert.Contains("50000x50000", jw, StringComparison.Ordinal);
            Assert.Contains("2500000000", jw, StringComparison.Ordinal);
            Assert.Contains("178956970", jw, StringComparison.Ordinal);
            var (jpgJson, jpgBin) = ParseGlb(jpgGlb);
            Assert.Equal(oversizeJpg, ExtractGlbImageBytes(jpgJson, jpgBin, 0));

            // 3) Truncated 8-byte PNG keeps source bytes, keeps tinted factor, warns once, writes nothing to Console.Error
            byte[] truncatedPng = { 137, 80, 78, 71, 13, 10, 26, 10 };
            var corruptScene = new Scene
            {
                GltfMaterials = new List<object> { matShift },
                GlbPrimitives = new List<GlbPrimitive> { prim0 },
                Textures = new List<SceneTexture>
                {
                    new SceneTexture { Data = truncatedPng, MimeType = "image/png", Filename = "truncated.png" },
                },
            };
            TextWriter origErr = Console.Error;
            using var errCapture = new StringWriter(CultureInfo.InvariantCulture);
            try
            {
                Console.SetError(errCapture);
                GlbExport.ToGlb(corruptScene, new GlbOptions { Textures = true });
            }
            finally
            {
                Console.SetError(origErr);
            }
            Assert.Equal(string.Empty, errCapture.ToString());

            var corruptWarnings = new List<string>();
            byte[] corruptGlb = GlbExport.ToGlb(corruptScene, new GlbOptions
            {
                Textures = true,
                OnLog = (lvl, msg) =>
                {
                    if (lvl == SkpLogLevel.Warning) corruptWarnings.Add(msg);
                },
            });
            Assert.Single(corruptWarnings);
            var (cJson, cBin) = ParseGlb(corruptGlb);
            Assert.Equal(truncatedPng, ExtractGlbImageBytes(cJson, cBin, 0));
            var cBcf = cJson.GetProperty("materials")[0].GetProperty("pbrMetallicRoughness").GetProperty("baseColorFactor").EnumerateArray().Select(e => e.GetDouble()).ToArray();
            Assert.Equal(200.0 / 255.0, cBcf[0], 9);
            Assert.Equal(90.0 / 255.0, cBcf[1], 9);
            Assert.Equal(20.0 / 255.0, cBcf[2], 9);
            Assert.Equal(1.0, cBcf[3], 9);
        }
    }
}
