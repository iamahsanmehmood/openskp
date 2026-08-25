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
    /// GLB texture embedding and the material-identity fix it depends on
    /// (openskp#193, ported from TypeScript).
    ///
    /// Before this, GltfMaterials was keyed on (color, doubleSided) alone,
    /// so two different textures that happened to average to the same RGB
    /// would silently collapse into one material and lose an image. Fixed
    /// by keying on (color, doubleSided, textureIndex) instead, at both the
    /// face-grouping and material-dedup layers.
    ///
    /// Fixture: capilla_quiroz_v17.skp, which carries 3 real, distinct JPEG
    /// textures - real coverage, not a synthetic mock.
    /// </summary>
    public class GlbTexturesTests
    {
        private static readonly string FixturePath =
            Path.Combine(AppContext.BaseDirectory, "fixtures", "capilla_quiroz_v17.skp");

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

        private static bool ContainsBytes(byte[] haystack, byte[] needle)
        {
            for (int i = 0; i <= haystack.Length - needle.Length; i++)
            {
                bool match = true;
                for (int j = 0; j < needle.Length; j++)
                {
                    if (haystack[i + j] != needle[j]) { match = false; break; }
                }
                if (match) return true;
            }
            return false;
        }

        [Fact]
        public void SceneDeduplicatesTexturesAndKeysMaterialsByThem()
        {
            var scene = SkpFile.BuildScene(FixturePath);

            Assert.Equal(3, scene.Textures.Count);
            foreach (var tex in scene.Textures)
            {
                Assert.True(tex.MimeType == "image/jpeg" || tex.MimeType == "image/png");
                Assert.True(tex.Data.Length > 0);
            }

            int textured = 0;
            foreach (var m in scene.GltfMaterials)
            {
                var dict = (IDictionary<string, object>)m;
                var pbr = (IDictionary<string, object>)dict["pbrMetallicRoughness"];
                if (pbr.TryGetValue("baseColorTexture", out var texRef))
                {
                    textured++;
                    var texDict = (IDictionary<string, object>)texRef;
                    int idx = (int)texDict["index"];
                    Assert.InRange(idx, 0, scene.Textures.Count - 1);
                }
            }
            Assert.Equal(4, textured);
        }

        [Fact]
        public void ExportOmitsImagesByDefault()
        {
            var scene = SkpFile.BuildScene(FixturePath);
            var bytes = GlbExport.ToGlb(scene);

            Assert.DoesNotContain("\"images\"", Encoding.ASCII.GetString(bytes));
            Assert.False(ContainsBytes(bytes, new byte[] { 0xFF, 0xD8, 0xFF })); // JPEG magic
        }

        [Fact]
        public void ExportEmbedsTexturesWhenAsked()
        {
            var scene = SkpFile.BuildScene(FixturePath);
            var noTex = GlbExport.ToGlb(scene);
            var withTex = GlbExport.ToGlb(scene, new GlbOptions { Textures = true });

            Assert.True(withTex.Length > noTex.Length);
            Assert.Contains("\"images\"", Encoding.ASCII.GetString(withTex));
            Assert.True(ContainsBytes(withTex, new byte[] { 0xFF, 0xD8, 0xFF })); // real JPEG bytes

            var (json, _) = ParseGlb(withTex);
            var images = json.GetProperty("images");
            Assert.Equal(3, images.GetArrayLength());
            foreach (var img in images.EnumerateArray())
            {
                Assert.True(img.TryGetProperty("bufferView", out _));
                Assert.True(img.TryGetProperty("mimeType", out var mt));
                Assert.StartsWith("image/", mt.GetString());
            }
        }

        [Fact]
        public void ExportGlbFileWritesEmbeddedTextures()
        {
            var scene = SkpFile.BuildScene(FixturePath);
            var tmp = Path.Combine(Path.GetTempPath(), $"openskp_glbtex_{Guid.NewGuid():N}.glb");
            try
            {
                GlbExport.ExportGlb(scene, tmp, new GlbOptions { Textures = true });
                var bytes = File.ReadAllBytes(tmp);
                Assert.True(ContainsBytes(bytes, new byte[] { 0xFF, 0xD8, 0xFF }));
            }
            finally
            {
                if (File.Exists(tmp)) File.Delete(tmp);
            }
        }
    }
}
