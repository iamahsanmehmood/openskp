using System.Collections.Generic;
using OpenSkp;
using Xunit;

namespace OpenSkp.Tests
{
    public class PlyExportTests
    {
        [Fact]
        public void ToPlyAscii_SerializesSceneToPlyText()
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

            string plyText = PlyExport.ToPlyAscii(scene);
            Assert.Contains("format ascii 1.0", plyText);
            Assert.Contains("element vertex 3", plyText);
            Assert.Contains("element face 1", plyText);
            Assert.Contains("3 0 1 2", plyText);
        }

        [Fact]
        public void ToPlyBinary_SerializesSceneToBinaryPlyData()
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

            byte[] data = PlyExport.ToPlyBinary(scene);
            string text = System.Text.Encoding.ASCII.GetString(data);
            Assert.Contains("format binary_little_endian 1.0", text);
            Assert.Contains("element vertex 3", text);
            Assert.Contains("element face 1", text);
        }
    }
}
