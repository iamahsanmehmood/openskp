using System;
using System.Globalization;
using System.IO;
using System.Text;

namespace OpenSkp
{
    /// <summary>
    /// PLY (Polygon File Format) exporter for baked <see cref="Scene"/> objects.
    /// Supports both ASCII and Little-Endian Binary PLY formats with positions, normals, UVs, and RGBA colors.
    /// </summary>
    public static class PlyExport
    {
        private static (byte r, byte g, byte b, byte a) GetMaterialRgba(Scene scene, int matIdx)
        {
            if (matIdx >= 0 && scene.GltfMaterials != null && matIdx < scene.GltfMaterials.Count)
            {
                var mat = scene.GltfMaterials[matIdx];
                if (mat is System.Collections.IDictionary dict && dict.Contains("baseColorFactor") && dict["baseColorFactor"] is System.Collections.IEnumerable enumerable)
                {
                    var list = new System.Collections.Generic.List<float>();
                    foreach (var item in enumerable)
                    {
                        list.Add(Convert.ToSingle(item, CultureInfo.InvariantCulture));
                    }
                    if (list.Count >= 4)
                    {
                        byte r = (byte)Math.Max(0, Math.Min(255, (int)Math.Round(list[0] * 255.0f)));
                        byte g = (byte)Math.Max(0, Math.Min(255, (int)Math.Round(list[1] * 255.0f)));
                        byte b = (byte)Math.Max(0, Math.Min(255, (int)Math.Round(list[2] * 255.0f)));
                        byte a = (byte)Math.Max(0, Math.Min(255, (int)Math.Round(list[3] * 255.0f)));
                        return (r, g, b, a);
                    }
                }
            }
            return (200, 200, 200, 255);
        }

        /// <summary>
        /// Serialize a baked <see cref="Scene"/> into ASCII PLY text format.
        /// </summary>
        /// <param name="scene">The baked scene returned by <see cref="SkpFile.BuildScene(string, SkpParseOptions?)"/>.</param>
        /// <returns>Formatted ASCII PLY text string.</returns>
        public static string ToPlyAscii(Scene scene)
        {
            if (scene == null) throw new ArgumentNullException(nameof(scene));

            int totalVertices = 0;
            int totalFaces = 0;
            foreach (var prim in scene.GlbPrimitives)
            {
                totalVertices += prim.Positions.Length / 3;
                totalFaces += prim.Indices.Length / 3;
            }

            var sb = new StringBuilder();
            sb.AppendLine("ply");
            sb.AppendLine("format ascii 1.0");
            sb.AppendLine("comment Created by OpenSKP");
            sb.AppendLine($"element vertex {totalVertices}");
            sb.AppendLine("property float x");
            sb.AppendLine("property float y");
            sb.AppendLine("property float z");
            sb.AppendLine("property float nx");
            sb.AppendLine("property float ny");
            sb.AppendLine("property float nz");
            sb.AppendLine("property float u");
            sb.AppendLine("property float v");
            sb.AppendLine("property uchar red");
            sb.AppendLine("property uchar green");
            sb.AppendLine("property uchar blue");
            sb.AppendLine("property uchar alpha");
            sb.AppendLine($"element face {totalFaces}");
            sb.AppendLine("property list uchar int vertex_indices");
            sb.AppendLine("end_header");

            foreach (var prim in scene.GlbPrimitives)
            {
                var (r, g, b, a) = GetMaterialRgba(scene, prim.MaterialIndex);
                int vertCount = prim.Positions.Length / 3;
                for (int i = 0; i < vertCount; i++)
                {
                    string px = prim.Positions[i * 3].ToString("F6", CultureInfo.InvariantCulture);
                    string py = prim.Positions[i * 3 + 1].ToString("F6", CultureInfo.InvariantCulture);
                    string pz = prim.Positions[i * 3 + 2].ToString("F6", CultureInfo.InvariantCulture);

                    float nxVal = (i * 3 < prim.Normals.Length) ? prim.Normals[i * 3] : 0.0f;
                    float nyVal = (i * 3 + 1 < prim.Normals.Length) ? prim.Normals[i * 3 + 1] : 0.0f;
                    float nzVal = (i * 3 + 2 < prim.Normals.Length) ? prim.Normals[i * 3 + 2] : 0.0f;

                    string nx = nxVal.ToString("F6", CultureInfo.InvariantCulture);
                    string ny = nyVal.ToString("F6", CultureInfo.InvariantCulture);
                    string nz = nzVal.ToString("F6", CultureInfo.InvariantCulture);

                    float uVal = (i * 2 < prim.Uvs.Length) ? prim.Uvs[i * 2] : 0.0f;
                    float vVal = (i * 2 + 1 < prim.Uvs.Length) ? prim.Uvs[i * 2 + 1] : 0.0f;

                    string u = uVal.ToString("F6", CultureInfo.InvariantCulture);
                    string v = vVal.ToString("F6", CultureInfo.InvariantCulture);

                    sb.AppendLine($"{px} {py} {pz} {nx} {ny} {nz} {u} {v} {r} {g} {b} {a}");
                }
            }

            int vertOffset = 0;
            foreach (var prim in scene.GlbPrimitives)
            {
                int triCount = prim.Indices.Length / 3;
                for (int i = 0; i < triCount; i++)
                {
                    uint i0 = prim.Indices[i * 3] + (uint)vertOffset;
                    uint i1 = prim.Indices[i * 3 + 1] + (uint)vertOffset;
                    uint i2 = prim.Indices[i * 3 + 2] + (uint)vertOffset;
                    sb.AppendLine($"3 {i0} {i1} {i2}");
                }
                vertOffset += prim.Positions.Length / 3;
            }

            return sb.ToString();
        }

        /// <summary>
        /// Serialize a baked <see cref="Scene"/> into Little-Endian Binary PLY byte array format.
        /// </summary>
        /// <param name="scene">The baked scene returned by <see cref="SkpFile.BuildScene(string, SkpParseOptions?)"/>.</param>
        /// <returns>Packed Little-Endian Binary PLY byte array.</returns>
        public static byte[] ToPlyBinary(Scene scene)
        {
            if (scene == null) throw new ArgumentNullException(nameof(scene));

            int totalVertices = 0;
            int totalFaces = 0;
            foreach (var prim in scene.GlbPrimitives)
            {
                totalVertices += prim.Positions.Length / 3;
                totalFaces += prim.Indices.Length / 3;
            }

            string headerText =
                "ply\n" +
                "format binary_little_endian 1.0\n" +
                "comment Created by OpenSKP\n" +
                $"element vertex {totalVertices}\n" +
                "property float x\n" +
                "property float y\n" +
                "property float z\n" +
                "property float nx\n" +
                "property float ny\n" +
                "property float nz\n" +
                "property float u\n" +
                "property float v\n" +
                "property uchar red\n" +
                "property uchar green\n" +
                "property uchar blue\n" +
                "property uchar alpha\n" +
                $"element face {totalFaces}\n" +
                "property list uchar int vertex_indices\n" +
                "end_header\n";

            byte[] headerBytes = Encoding.ASCII.GetBytes(headerText);
            int vertexBytesSize = totalVertices * 36;
            int faceBytesSize = totalFaces * 13;

            byte[] bytes = new byte[headerBytes.Length + vertexBytesSize + faceBytesSize];
            Array.Copy(headerBytes, 0, bytes, 0, headerBytes.Length);

            int offset = headerBytes.Length;

            foreach (var prim in scene.GlbPrimitives)
            {
                var (r, g, b, a) = GetMaterialRgba(scene, prim.MaterialIndex);
                int vertCount = prim.Positions.Length / 3;
                for (int i = 0; i < vertCount; i++)
                {
                    float px = prim.Positions[i * 3];
                    float py = prim.Positions[i * 3 + 1];
                    float pz = prim.Positions[i * 3 + 2];

                    float nx = (i * 3 < prim.Normals.Length) ? prim.Normals[i * 3] : 0.0f;
                    float ny = (i * 3 + 1 < prim.Normals.Length) ? prim.Normals[i * 3 + 1] : 0.0f;
                    float nz = (i * 3 + 2 < prim.Normals.Length) ? prim.Normals[i * 3 + 2] : 0.0f;

                    float u = (i * 2 < prim.Uvs.Length) ? prim.Uvs[i * 2] : 0.0f;
                    float v = (i * 2 + 1 < prim.Uvs.Length) ? prim.Uvs[i * 2 + 1] : 0.0f;

                    WriteFloatLE(bytes, offset, px);
                    WriteFloatLE(bytes, offset + 4, py);
                    WriteFloatLE(bytes, offset + 8, pz);

                    WriteFloatLE(bytes, offset + 12, nx);
                    WriteFloatLE(bytes, offset + 16, ny);
                    WriteFloatLE(bytes, offset + 20, nz);

                    WriteFloatLE(bytes, offset + 24, u);
                    WriteFloatLE(bytes, offset + 28, v);

                    bytes[offset + 32] = r;
                    bytes[offset + 33] = g;
                    bytes[offset + 34] = b;
                    bytes[offset + 35] = a;

                    offset += 36;
                }
            }

            int vertOffset = 0;
            foreach (var prim in scene.GlbPrimitives)
            {
                int triCount = prim.Indices.Length / 3;
                for (int i = 0; i < triCount; i++)
                {
                    int i0 = (int)prim.Indices[i * 3] + vertOffset;
                    int i1 = (int)prim.Indices[i * 3 + 1] + vertOffset;
                    int i2 = (int)prim.Indices[i * 3 + 2] + vertOffset;

                    bytes[offset] = 3;
                    WriteInt32LE(bytes, offset + 1, i0);
                    WriteInt32LE(bytes, offset + 5, i1);
                    WriteInt32LE(bytes, offset + 9, i2);

                    offset += 13;
                }
                vertOffset += prim.Positions.Length / 3;
            }

            return bytes;
        }

        private static void WriteFloatLE(byte[] buffer, int offset, float value)
        {
            byte[] fBytes = BitConverter.GetBytes(value);
            if (!BitConverter.IsLittleEndian) Array.Reverse(fBytes);
            Array.Copy(fBytes, 0, buffer, offset, 4);
        }

        private static void WriteInt32LE(byte[] buffer, int offset, int value)
        {
            byte[] iBytes = BitConverter.GetBytes(value);
            if (!BitConverter.IsLittleEndian) Array.Reverse(iBytes);
            Array.Copy(iBytes, 0, buffer, offset, 4);
        }

        /// <summary>
        /// Export a baked <see cref="Scene"/> directly to a PLY file.
        /// </summary>
        /// <param name="scene">The baked scene returned by <see cref="SkpFile.BuildScene(string, SkpParseOptions?)"/>.</param>
        /// <param name="outputPath">Destination file path (.ply).</param>
        /// <param name="binary">If true, writes binary PLY. Otherwise writes ASCII PLY.</param>
        public static void ExportPly(Scene scene, string outputPath, bool binary = false)
        {
            if (string.IsNullOrEmpty(outputPath)) throw new ArgumentException("Output path cannot be null or empty", nameof(outputPath));

            string? dir = Path.GetDirectoryName(outputPath);
            if (!string.IsNullOrEmpty(dir) && !Directory.Exists(dir))
            {
                Directory.CreateDirectory(dir);
            }

            if (binary)
            {
                byte[] data = ToPlyBinary(scene);
                File.WriteAllBytes(outputPath, data);
            }
            else
            {
                string text = ToPlyAscii(scene);
                File.WriteAllText(outputPath, text, Encoding.UTF8);
            }
        }
    }
}
