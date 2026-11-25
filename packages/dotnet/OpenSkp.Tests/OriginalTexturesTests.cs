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
    public class OriginalTexturesTests
    {
        private static string FixturePath(string name) =>
            Path.Combine(AppContext.BaseDirectory, "fixtures", name);

        public static IEnumerable<object[]> KeptFixtures()
        {
            var items = new (string Fixture, string Name, string Mime, string Format, string File, byte[]? Sig)[]
            {
                ("texture_tiff_2017.skp", "Tex_TIF", "image/tiff", "TIFF", "tex.tif", new byte[] { 0x49, 0x49, 0x2A, 0x00 }),
                ("texture_tiff_2021.skp", "Tex_TIF", "image/tiff", "TIFF", "tex.tif", new byte[] { 0x49, 0x49, 0x2A, 0x00 }),
                ("texture_tga_2021.skp", "Tex_TGA", "image/x-tga", "TGA", "tex.tga", null),
                ("texture_gif_2021.skp", "Tex_GIF", "image/gif", "GIF", "tex.gif", Encoding.ASCII.GetBytes("GIF89a")),
            };
            foreach (var instanced in new[] { false, true })
            {
                foreach (var item in items)
                {
                    yield return new object[] { instanced, item.Fixture, item.Name, item.Mime, item.Format, item.File, item.Sig! };
                }
            }
        }

        private static bool HasBaseColorTexture(object matObj)
        {
            var mat = (IDictionary<string, object>)matObj;
            var pbr = (IDictionary<string, object>)mat["pbrMetallicRoughness"];
            return pbr.ContainsKey("baseColorTexture");
        }

        [Theory]
        [MemberData(nameof(KeptFixtures))]
        public void KeepsTextureInOwnFormatWhenOriginalTexturesTrue(
            bool instanced, string fixture, string matName, string mime, string format, string file, byte[]? sig)
        {
            _ = format;
            _ = file;
            var path = FixturePath(fixture);
            var warnings = new List<string>();
            var opts = new SkpParseOptions
            {
                OriginalTextures = true,
                OnLog = (lvl, msg) =>
                {
                    if (lvl == SkpLogLevel.Warning && msg.StartsWith("Texture of material", StringComparison.Ordinal))
                    {
                        warnings.Add(msg);
                    }
                },
            };

            IReadOnlyList<SceneTexture> textures;
            IReadOnlyList<object> mats;
            if (instanced)
            {
                var s = SkpFile.BuildInstancedScene(path, opts);
                textures = s.Textures;
                mats = s.GltfMaterials;
            }
            else
            {
                var s = SkpFile.BuildScene(path, opts);
                textures = s.Textures;
                mats = s.GltfMaterials;
            }

            var tex = Assert.Single(textures);
            Assert.Equal(mime, tex.MimeType);
            if (sig != null)
            {
                Assert.Equal(sig, tex.Data.Take(sig.Length).ToArray());
            }
            var model = SkpFile.Parse(File.ReadAllBytes(path));
            var stored = model.Materials.First(m => m.Name == matName).Texture!.Data;
            Assert.Equal(stored, tex.Data);
            var textured = Assert.Single(mats, HasBaseColorTexture);
            var pbr = (IDictionary<string, object>)((IDictionary<string, object>)textured)["pbrMetallicRoughness"];
            var texRef = (IDictionary<string, object>)pbr["baseColorTexture"];
            Assert.Equal(0, texRef["index"]);
            Assert.Empty(warnings);
        }

        [Theory]
        [MemberData(nameof(KeptFixtures))]
        public void DropsTextureByDefaultAndLogsWarningNamingOption(
            bool instanced, string fixture, string matName, string mime, string format, string file, byte[]? sig)
        {
            _ = mime;
            _ = sig;
            var path = FixturePath(fixture);
            var warnings = new List<string>();
            var opts = new SkpParseOptions
            {
                OnLog = (lvl, msg) =>
                {
                    if (lvl == SkpLogLevel.Warning && msg.StartsWith("Texture of material", StringComparison.Ordinal))
                    {
                        warnings.Add(msg);
                    }
                },
            };

            if (instanced)
            {
                var s = SkpFile.BuildInstancedScene(path, opts);
                Assert.Empty(s.Textures);
                Assert.DoesNotContain(s.GltfMaterials, HasBaseColorTexture);
            }
            else
            {
                var s = SkpFile.BuildScene(path, opts);
                Assert.Empty(s.Textures);
                Assert.DoesNotContain(s.GltfMaterials, HasBaseColorTexture);
            }

            var message = Assert.Single(warnings);
            Assert.Equal(
                $"Texture of material '{matName}' ({file}) is {format}, which a scene cannot carry (only PNG and JPEG): " +
                "the material is built without its texture (pass OriginalTextures = true to keep it in its own format)",
                message);
        }

        [Theory]
        [InlineData(false, "texture_bmp_2021.skp")]
        [InlineData(true, "texture_bmp_2021.skp")]
        [InlineData(false, "capilla_quiroz_v17.skp")]
        [InlineData(true, "capilla_quiroz_v17.skp")]
        public void LeavesPngAndJpegTexturesUnchanged(bool instanced, string fixture)
        {
            var path = FixturePath(fixture);
            if (instanced)
            {
                var plain = SkpFile.BuildInstancedScene(path);
                var kept = SkpFile.BuildInstancedScene(path, new SkpParseOptions { OriginalTextures = true });
                Assert.NotEmpty(plain.Textures);
                Assert.Equal(plain.Textures.Select(t => t.MimeType), kept.Textures.Select(t => t.MimeType));
                Assert.Equal(plain.Textures.Count, kept.Textures.Count);
            }
            else
            {
                var plain = SkpFile.BuildScene(path);
                var kept = SkpFile.BuildScene(path, new SkpParseOptions { OriginalTextures = true });
                Assert.NotEmpty(plain.Textures);
                Assert.Equal(plain.Textures.Select(t => t.MimeType), kept.Textures.Select(t => t.MimeType));
                Assert.Equal(plain.Textures.Count, kept.Textures.Count);
            }
        }

        private static JsonElement ParseGlbJson(byte[] glb)
        {
            uint jsonLen = BitConverter.ToUInt32(glb, 12);
            using var doc = JsonDocument.Parse(glb.AsMemory(20, (int)jsonLen));
            return doc.RootElement.Clone();
        }

        [Theory]
        [InlineData(false)]
        [InlineData(true)]
        public void GlbExportOmitsLoneTiffTextureAndWarnsOnce(bool instanced)
        {
            var path = FixturePath("texture_tiff_2021.skp");
            var warnings = new List<string>();
            Action<SkpLogLevel, string> onLog = (lvl, msg) =>
            {
                if (lvl == SkpLogLevel.Warning) warnings.Add(msg);
            };
            byte[] glb = instanced
                ? InstancedGlbExport.ToInstancedGlb(
                    SkpFile.BuildInstancedScene(path, new SkpParseOptions { OriginalTextures = true }),
                    new InstancedGlbOptions { Textures = true, OnLog = onLog })
                : GlbExport.ToGlb(
                    SkpFile.BuildScene(path, new SkpParseOptions { OriginalTextures = true }),
                    new GlbOptions { Textures = true, OnLog = onLog });

            var doc = ParseGlbJson(glb);
            Assert.False(doc.TryGetProperty("images", out _));
            Assert.False(doc.TryGetProperty("textures", out _));
            Assert.False(doc.TryGetProperty("samplers", out _));
            foreach (var mat in doc.GetProperty("materials").EnumerateArray())
            {
                Assert.False(mat.GetProperty("pbrMetallicRoughness").TryGetProperty("baseColorTexture", out _));
                Assert.False(mat.TryGetProperty("alphaMode", out var am) && am.GetString() == "MASK");
            }
            var msg = Assert.Single(warnings);
            Assert.Equal(
                "Texture 'tex.tif' is TIFF, which glTF 2.0 cannot carry (only PNG and JPEG): leaving it out of the GLB",
                msg);
        }

        [Theory]
        [InlineData(false)]
        [InlineData(true)]
        public void GlbExportDropsTiffKeepsColorRemapsPngIndexAndWarnsOnce(bool instanced)
        {
            var path = FixturePath("texture_tiff_and_png_2021.skp");
            var warnings = new List<string>();
            Action<SkpLogLevel, string> onLog = (lvl, msg) =>
            {
                if (lvl == SkpLogLevel.Warning) warnings.Add(msg);
            };

            byte[] glb;
            if (instanced)
            {
                var scene = SkpFile.BuildInstancedScene(path, new SkpParseOptions { OriginalTextures = true });
                Assert.Equal(new[] { "image/tiff", "image/png" }, scene.Textures.Select(t => t.MimeType));
                glb = InstancedGlbExport.ToInstancedGlb(scene, new InstancedGlbOptions { Textures = true, OnLog = onLog });
                Assert.Equal(new[] { "image/tiff", "image/png" }, scene.Textures.Select(t => t.MimeType));
            }
            else
            {
                var scene = SkpFile.BuildScene(path, new SkpParseOptions { OriginalTextures = true });
                Assert.Equal(new[] { "image/tiff", "image/png" }, scene.Textures.Select(t => t.MimeType));
                glb = GlbExport.ToGlb(scene, new GlbOptions { Textures = true, OnLog = onLog });
                Assert.Equal(new[] { "image/tiff", "image/png" }, scene.Textures.Select(t => t.MimeType));
            }

            var doc = ParseGlbJson(glb);
            var images = doc.GetProperty("images").EnumerateArray().ToList();
            var singleImg = Assert.Single(images);
            Assert.Equal("image/png", singleImg.GetProperty("mimeType").GetString());
            Assert.Single(doc.GetProperty("textures").EnumerateArray());
            Assert.Single(doc.GetProperty("samplers").EnumerateArray());

            static double SrgbToLinear(double c) =>
                c <= 0.04045 ? c / 12.92 : Math.Pow((c + 0.055) / 1.055, 2.4);

            JsonElement? tiffMat = null;
            JsonElement? pngMat = null;
            foreach (var mat in doc.GetProperty("materials").EnumerateArray())
            {
                var pbr = mat.GetProperty("pbrMetallicRoughness");
                var bcf = pbr.GetProperty("baseColorFactor");
                if (pbr.TryGetProperty("baseColorTexture", out _))
                {
                    pngMat = mat;
                }
                else if (bcf[0].GetDouble() > 0.5 && bcf[2].GetDouble() < 0.1)
                {
                    tiffMat = mat;
                }
            }
            Assert.True(tiffMat.HasValue);
            Assert.False(tiffMat.Value.GetProperty("pbrMetallicRoughness").TryGetProperty("baseColorTexture", out _));
            Assert.False(tiffMat.Value.TryGetProperty("alphaMode", out _));
            var tiffBcf = tiffMat.Value.GetProperty("pbrMetallicRoughness").GetProperty("baseColorFactor");
            Assert.Equal(SrgbToLinear(200.0 / 255.0), tiffBcf[0].GetDouble(), 4);
            Assert.Equal(SrgbToLinear(60.0 / 255.0), tiffBcf[1].GetDouble(), 4);
            Assert.Equal(SrgbToLinear(40.0 / 255.0), tiffBcf[2].GetDouble(), 4);

            Assert.True(pngMat.HasValue);
            Assert.Equal(0, pngMat.Value.GetProperty("pbrMetallicRoughness").GetProperty("baseColorTexture").GetProperty("index").GetInt32());
            var pngBcf = pngMat.Value.GetProperty("pbrMetallicRoughness").GetProperty("baseColorFactor");
            Assert.Equal(new[] { 1.0, 1.0, 1.0, 1.0 }, pngBcf.EnumerateArray().Select(e => e.GetDouble()).ToArray());
            Assert.Equal("MASK", pngMat.Value.GetProperty("alphaMode").GetString());

            var warning = Assert.Single(warnings);
            Assert.Equal(
                "Texture 'tex.tif' is TIFF, which glTF 2.0 cannot carry (only PNG and JPEG): leaving it out of the GLB",
                warning);
        }
    }
}
