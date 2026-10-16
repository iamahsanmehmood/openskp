using System;
using System.Collections.Generic;
using System.Text;
using OpenSkp;
using Xunit;

namespace OpenSkp.Tests
{
    public class PlyColorTests
    {
        [Fact]
        public void PlyWritesNestedGltfRgbaInAsciiAndBinary()
        {
            var scene = new Scene
            {
                GlbPrimitives = new List<GlbPrimitive>
                {
                    new GlbPrimitive
                    {
                        MaterialIndex = 0,
                        Positions = new float[] { 0f, 0f, 0f },
                        Normals = new float[] { 0f, 0f, 1f },
                        Uvs = new float[] { 0f, 0f },
                        Indices = Array.Empty<uint>()
                    }
                },
                GltfMaterials = new List<object>
                {
                    new Dictionary<string, object>
                    {
                        ["pbrMetallicRoughness"] = new Dictionary<string, object>
                        {
                            ["baseColorFactor"] = new double[] { 1.0, 0.0, 0.0, 0.5 }
                        }
                    }
                }
            };

            var ascii = PlyExport.ToPlyAscii(scene);
            var start = ascii.IndexOf("end_header\n", StringComparison.Ordinal) + "end_header\n".Length;
            Assert.EndsWith("255 0 0 128", ascii.Substring(start).Split('\n')[0].TrimEnd('\r'));

            var binary = PlyExport.ToPlyBinary(scene);
            var header = Encoding.ASCII.GetString(binary);
            start = header.IndexOf("end_header\n", StringComparison.Ordinal) + "end_header\n".Length;
            Assert.Equal(new byte[] { 255, 0, 0, 128 }, binary[(start + 32)..(start + 36)]);
        }
    }
}
