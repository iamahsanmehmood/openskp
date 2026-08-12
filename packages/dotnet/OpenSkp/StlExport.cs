using System;
using System.Globalization;
using System.IO;
using System.Text;

namespace OpenSkp
{
    /// <summary>
    /// STL (Standard Triangle Language) exporter for baked <see cref="Scene"/> objects.
    /// Supports both ASCII and Binary STL formats.
    /// </summary>
    public static class StlExport
    {
        private static (float nx, float ny, float nz) CalculateNormal(
            (float x, float y, float z) v0,
            (float x, float y, float z) v1,
            (float x, float y, float z) v2)
        {
            float e1x = v1.x - v0.x;
            float e1y = v1.y - v0.y;
            float e1z = v1.z - v0.z;

            float e2x = v2.x - v0.x;
            float e2y = v2.y - v0.y;
            float e2z = v2.z - v0.z;

            float nx = e1y * e2z - e1z * e2y;
            float ny = e1z * e2x - e1x * e2z;
            float nz = e1x * e2y - e1y * e2x;

            float len = (float)Math.Sqrt(nx * nx + ny * ny + nz * nz);
            if (len > 1e-12f)
            {
                return (nx / len, ny / len, nz / len);
            }
            return (0.0f, 0.0f, 0.0f);
        }

        /// <summary>
        /// Serialize a baked <see cref="Scene"/> into ASCII STL text format.
        /// </summary>
        /// <param name="scene">The baked scene returned by <see cref="SkpFile.BuildScene(string, SkpParseOptions?)"/>.</param>
        /// <param name="scale">Scale factor (e.g. 1000.0f to convert metres to millimetres for 3D slicers).</param>
        /// <returns>The formatted ASCII STL text string.</returns>
        public static string ToStlAscii(Scene scene, float scale = 1.0f)
        {
            if (scene == null) throw new ArgumentNullException(nameof(scene));

            var sb = new StringBuilder();
            sb.AppendLine("solid OpenSKP_Model");

            foreach (var prim in scene.GlbPrimitives)
            {
                int triCount = prim.Indices.Length / 3;
                for (int i = 0; i < triCount; i++)
                {
                    uint i0 = prim.Indices[i * 3];
                    uint i1 = prim.Indices[i * 3 + 1];
                    uint i2 = prim.Indices[i * 3 + 2];

                    var v0 = (
                        x: prim.Positions[i0 * 3] * scale,
                        y: prim.Positions[i0 * 3 + 1] * scale,
                        z: prim.Positions[i0 * 3 + 2] * scale
                    );
                    var v1 = (
                        x: prim.Positions[i1 * 3] * scale,
                        y: prim.Positions[i1 * 3 + 1] * scale,
                        z: prim.Positions[i1 * 3 + 2] * scale
                    );
                    var v2 = (
                        x: prim.Positions[i2 * 3] * scale,
                        y: prim.Positions[i2 * 3 + 1] * scale,
                        z: prim.Positions[i2 * 3 + 2] * scale
                    );

                    var n = CalculateNormal(v0, v1, v2);

                    string nx = n.nx.ToString("F6", CultureInfo.InvariantCulture);
                    string ny = n.ny.ToString("F6", CultureInfo.InvariantCulture);
                    string nz = n.nz.ToString("F6", CultureInfo.InvariantCulture);

                    string v0x = v0.x.ToString("F6", CultureInfo.InvariantCulture);
                    string v0y = v0.y.ToString("F6", CultureInfo.InvariantCulture);
                    string v0z = v0.z.ToString("F6", CultureInfo.InvariantCulture);

                    string v1x = v1.x.ToString("F6", CultureInfo.InvariantCulture);
                    string v1y = v1.y.ToString("F6", CultureInfo.InvariantCulture);
                    string v1z = v1.z.ToString("F6", CultureInfo.InvariantCulture);

                    string v2x = v2.x.ToString("F6", CultureInfo.InvariantCulture);
                    string v2y = v2.y.ToString("F6", CultureInfo.InvariantCulture);
                    string v2z = v2.z.ToString("F6", CultureInfo.InvariantCulture);

                    sb.AppendLine($"  facet normal {nx} {ny} {nz}");
                    sb.AppendLine("    outer loop");
                    sb.AppendLine($"      vertex {v0x} {v0y} {v0z}");
                    sb.AppendLine($"      vertex {v1x} {v1y} {v1z}");
                    sb.AppendLine($"      vertex {v2x} {v2y} {v2z}");
                    sb.AppendLine("    endloop");
                    sb.AppendLine("  endfacet");
                }
            }

            sb.AppendLine("endsolid OpenSKP_Model");
            return sb.ToString();
        }

        /// <summary>
        /// Serialize a baked <see cref="Scene"/> into Little-Endian Binary STL format.
        /// </summary>
        /// <param name="scene">The baked scene returned by <see cref="SkpFile.BuildScene(string, SkpParseOptions?)"/>.</param>
        /// <param name="scale">Scale factor (e.g. 1000.0f for mm).</param>
        /// <returns>Packed Little-Endian Binary STL byte array.</returns>
        public static byte[] ToStlBinary(Scene scene, float scale = 1.0f)
        {
            if (scene == null) throw new ArgumentNullException(nameof(scene));

            int totalTriangles = 0;
            foreach (var prim in scene.GlbPrimitives)
            {
                totalTriangles += prim.Indices.Length / 3;
            }

            int bufferSize = 80 + 4 + totalTriangles * 50;
            byte[] bytes = new byte[bufferSize];

            // Header (80 bytes)
            byte[] header = Encoding.ASCII.GetBytes("# OpenSKP Binary STL Export");
            Array.Copy(header, bytes, Math.Min(header.Length, 80));

            // Triangle Count (uint32 Little-Endian)
            byte[] countBytes = BitConverter.GetBytes((uint)totalTriangles);
            if (!BitConverter.IsLittleEndian) Array.Reverse(countBytes);
            Array.Copy(countBytes, 0, bytes, 80, 4);

            int offset = 84;
            foreach (var prim in scene.GlbPrimitives)
            {
                int triCount = prim.Indices.Length / 3;
                for (int i = 0; i < triCount; i++)
                {
                    uint i0 = prim.Indices[i * 3];
                    uint i1 = prim.Indices[i * 3 + 1];
                    uint i2 = prim.Indices[i * 3 + 2];

                    var v0 = (
                        x: prim.Positions[i0 * 3] * scale,
                        y: prim.Positions[i0 * 3 + 1] * scale,
                        z: prim.Positions[i0 * 3 + 2] * scale
                    );
                    var v1 = (
                        x: prim.Positions[i1 * 3] * scale,
                        y: prim.Positions[i1 * 3 + 1] * scale,
                        z: prim.Positions[i1 * 3 + 2] * scale
                    );
                    var v2 = (
                        x: prim.Positions[i2 * 3] * scale,
                        y: prim.Positions[i2 * 3 + 1] * scale,
                        z: prim.Positions[i2 * 3 + 2] * scale
                    );

                    var n = CalculateNormal(v0, v1, v2);

                    // Normal (3x Float32)
                    WriteFloatLE(bytes, offset, n.nx);
                    WriteFloatLE(bytes, offset + 4, n.ny);
                    WriteFloatLE(bytes, offset + 8, n.nz);

                    // Vertices (9x Float32)
                    WriteFloatLE(bytes, offset + 12, v0.x);
                    WriteFloatLE(bytes, offset + 16, v0.y);
                    WriteFloatLE(bytes, offset + 20, v0.z);

                    WriteFloatLE(bytes, offset + 24, v1.x);
                    WriteFloatLE(bytes, offset + 28, v1.y);
                    WriteFloatLE(bytes, offset + 32, v1.z);

                    WriteFloatLE(bytes, offset + 36, v2.x);
                    WriteFloatLE(bytes, offset + 40, v2.y);
                    WriteFloatLE(bytes, offset + 44, v2.z);

                    // Attribute byte count (Uint16)
                    bytes[offset + 48] = 0;
                    bytes[offset + 49] = 0;

                    offset += 50;
                }
            }

            return bytes;
        }

        private static void WriteFloatLE(byte[] buffer, int offset, float value)
        {
            byte[] fBytes = BitConverter.GetBytes(value);
            if (!BitConverter.IsLittleEndian) Array.Reverse(fBytes);
            Array.Copy(fBytes, 0, buffer, offset, 4);
        }

        /// <summary>
        /// Export a baked <see cref="Scene"/> directly to an STL file.
        /// </summary>
        /// <param name="scene">The baked scene returned by <see cref="SkpFile.BuildScene(string, SkpParseOptions?)"/>.</param>
        /// <param name="outputPath">Destination file path (.stl).</param>
        /// <param name="binary">If true, writes binary STL. Otherwise writes ASCII STL.</param>
        /// <param name="scale">Scale factor (e.g. 1000.0f for mm).</param>
        public static void ExportStl(Scene scene, string outputPath, bool binary = false, float scale = 1.0f)
        {
            if (string.IsNullOrEmpty(outputPath)) throw new ArgumentException("Output path cannot be null or empty", nameof(outputPath));

            string? dir = Path.GetDirectoryName(outputPath);
            if (!string.IsNullOrEmpty(dir) && !Directory.Exists(dir))
            {
                Directory.CreateDirectory(dir);
            }

            if (binary)
            {
                byte[] data = ToStlBinary(scene, scale);
                File.WriteAllBytes(outputPath, data);
            }
            else
            {
                string text = ToStlAscii(scene, scale);
                File.WriteAllText(outputPath, text, Encoding.UTF8);
            }
        }
    }
}
