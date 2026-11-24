using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Xunit;
using OpenSkp;

namespace OpenSkp.Tests
{
    public class SameSizeTexturesTests
    {
        private static readonly byte[] PngRed = new byte[]
        {
            0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0x00, 0x00, 0x00, 0x0d, 0x49, 0x48, 0x44, 0x52,
            0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x01, 0x08, 0x02, 0x00, 0x00, 0x00, 0x90, 0x77, 0x53,
            0xde, 0x00, 0x00, 0x00, 0x0f, 0x49, 0x44, 0x41, 0x54, 0x78, 0x01, 0x01, 0x04, 0x00, 0xfb, 0xff,
            0x00, 0xdc, 0x28, 0x1e, 0x03, 0x06, 0x01, 0x23, 0xe0, 0x4f, 0x0f, 0xcc, 0x00, 0x00, 0x00, 0x00,
            0x49, 0x45, 0x4e, 0x44, 0xae, 0x42, 0x60, 0x82,
        };

        private static readonly byte[] PngBlue = new byte[]
        {
            0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0x00, 0x00, 0x00, 0x0d, 0x49, 0x48, 0x44, 0x52,
            0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x01, 0x08, 0x02, 0x00, 0x00, 0x00, 0x90, 0x77, 0x53,
            0xde, 0x00, 0x00, 0x00, 0x0f, 0x49, 0x44, 0x41, 0x54, 0x78, 0x01, 0x01, 0x04, 0x00, 0xfb, 0xff,
            0x00, 0x1e, 0x5a, 0xdc, 0x01, 0xee, 0x01, 0x55, 0xda, 0x6c, 0x0b, 0x85, 0x00, 0x00, 0x00, 0x00,
            0x49, 0x45, 0x4e, 0x44, 0xae, 0x42, 0x60, 0x82,
        };

        private static List<int> TexturedMaterialIndices(IReadOnlyList<object> materials)
        {
            var indices = new List<int>();
            foreach (var matObj in materials)
            {
                var mat = (IDictionary<string, object>)matObj;
                var pbr = (IDictionary<string, object>)mat["pbrMetallicRoughness"];
                if (pbr.TryGetValue("baseColorTexture", out var texRefObj))
                {
                    var texRef = (IDictionary<string, object>)texRefObj;
                    indices.Add((int)texRef["index"]);
                }
            }
            return indices;
        }

        [Theory]
        [InlineData(false)]
        [InlineData(true)]
        public void TwoDifferentPngsOfEqualLengthGiveTwoTextures(bool instanced)
        {
            Assert.Equal(PngRed.Length, PngBlue.Length);
            Assert.Equal(PngRed.Take(16), PngBlue.Take(16));

            string dir = Path.Combine(Path.GetTempPath(), "openskp_645_" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(dir);
            try
            {
                string pathA = Path.Combine(dir, "a.png");
                string pathB = Path.Combine(dir, "b.png");
                string skpPath = Path.Combine(dir, "two.skp");
                File.WriteAllBytes(pathA, PngRed);
                File.WriteAllBytes(pathB, PngBlue);

                var b = SkpCreate.NewFile();
                int matA = b.AddTextureMaterial("MatA", pathA);
                int matB = b.AddTextureMaterial("MatB", pathB);
                b.AddFace(new[] { (0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (10.0, 10.0, 0.0), (0.0, 10.0, 0.0) }, material: matA);
                b.AddFace(new[] { (15.0, 0.0, 0.0), (25.0, 0.0, 0.0), (25.0, 10.0, 0.0), (15.0, 10.0, 0.0) }, material: matA);
                b.AddFace(new[] { (30.0, 0.0, 0.0), (40.0, 0.0, 0.0), (40.0, 10.0, 0.0), (30.0, 10.0, 0.0) }, material: matB);
                b.Save(skpPath);

                IReadOnlyList<SceneTexture> textures;
                IReadOnlyList<object> mats;
                if (instanced)
                {
                    var s = SkpFile.BuildInstancedScene(skpPath);
                    textures = s.Textures;
                    mats = s.GltfMaterials;
                }
                else
                {
                    var s = SkpFile.BuildScene(skpPath);
                    textures = s.Textures;
                    mats = s.GltfMaterials;
                }

                Assert.Equal(2, textures.Count);
                Assert.Equal(PngRed, textures[0].Data);
                Assert.Equal(PngBlue, textures[1].Data);
                var indices = TexturedMaterialIndices(mats);
                indices.Sort();
                Assert.Equal(new[] { 0, 1 }, indices);
            }
            finally
            {
                Directory.Delete(dir, recursive: true);
            }
        }

        [Theory]
        [InlineData(false)]
        [InlineData(true)]
        public void IdenticalPngBytesInTwoMaterialsGiveOneTexture(bool instanced)
        {
            string dir = Path.Combine(Path.GetTempPath(), "openskp_645_dup_" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(dir);
            try
            {
                string path1 = Path.Combine(dir, "one.png");
                string path2 = Path.Combine(dir, "two.png");
                string skpPath = Path.Combine(dir, "same.skp");
                File.WriteAllBytes(path1, PngRed);
                File.WriteAllBytes(path2, PngRed.ToArray());

                var b = SkpCreate.NewFile();
                int mat1 = b.AddTextureMaterial("Mat1", path1);
                int mat2 = b.AddTextureMaterial("Mat2", path2, opacity: 0.6);
                b.AddFace(new[] { (0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (10.0, 10.0, 0.0), (0.0, 10.0, 0.0) }, material: mat1);
                b.AddFace(new[] { (20.0, 0.0, 0.0), (30.0, 0.0, 0.0), (30.0, 10.0, 0.0), (20.0, 10.0, 0.0) }, material: mat2);
                b.Save(skpPath);

                IReadOnlyList<SceneTexture> textures;
                IReadOnlyList<object> mats;
                if (instanced)
                {
                    var s = SkpFile.BuildInstancedScene(skpPath);
                    textures = s.Textures;
                    mats = s.GltfMaterials;
                }
                else
                {
                    var s = SkpFile.BuildScene(skpPath);
                    textures = s.Textures;
                    mats = s.GltfMaterials;
                }

                var single = Assert.Single(textures);
                Assert.Equal(PngRed, single.Data);
                Assert.Equal(new[] { 0, 0 }, TexturedMaterialIndices(mats));
            }
            finally
            {
                Directory.Delete(dir, recursive: true);
            }
        }
    }
}
