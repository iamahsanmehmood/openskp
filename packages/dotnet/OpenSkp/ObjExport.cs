using System;
using System.IO;
using System.Text;

namespace OpenSkp
{
    /// <summary>
    /// Wavefront OBJ text exporter for baked <see cref="Scene"/> objects.
    /// </summary>
    public static class ObjExport
    {
        /// <summary>
        /// Convert a baked <see cref="Scene"/> into Wavefront OBJ text format.
        /// </summary>
        /// <param name="scene">The baked scene returned by <see cref="SkpFile.BuildScene(string, SkpParseOptions?)"/>.</param>
        /// <returns>The formatted Wavefront OBJ text string.</returns>
        public static string ToObj(Scene scene)
        {
            if (scene == null) throw new ArgumentNullException(nameof(scene));

            var sb = new StringBuilder();
            sb.AppendLine("# OpenSKP OBJ Export");
            sb.AppendLine($"# Primitives: {scene.GlbPrimitives.Count}");
            sb.AppendLine();

            int vertOffset = 1; // OBJ indices are 1-based
            foreach (var prim in scene.GlbPrimitives)
            {
                sb.AppendLine($"o {prim.GeomName}");

                int vertCount = prim.Positions.Length / 3;
                for (int i = 0; i < vertCount; i++)
                {
                    string x = prim.Positions[i * 3].ToString("F6", System.Globalization.CultureInfo.InvariantCulture);
                    string y = prim.Positions[i * 3 + 1].ToString("F6", System.Globalization.CultureInfo.InvariantCulture);
                    string z = prim.Positions[i * 3 + 2].ToString("F6", System.Globalization.CultureInfo.InvariantCulture);
                    sb.AppendLine($"v {x} {y} {z}");
                }

                int triCount = prim.Indices.Length / 3;
                for (int i = 0; i < triCount; i++)
                {
                    uint i0 = prim.Indices[i * 3] + (uint)vertOffset;
                    uint i1 = prim.Indices[i * 3 + 1] + (uint)vertOffset;
                    uint i2 = prim.Indices[i * 3 + 2] + (uint)vertOffset;
                    sb.AppendLine($"f {i0} {i1} {i2}");
                }

                vertOffset += vertCount;
                sb.AppendLine();
            }

            return sb.ToString();
        }

        /// <summary>
        /// Export a baked <see cref="Scene"/> directly to a Wavefront OBJ file at <paramref name="outputPath"/>.
        /// </summary>
        /// <param name="scene">The baked scene returned by <see cref="SkpFile.BuildScene(string, SkpParseOptions?)"/>.</param>
        /// <param name="outputPath">Destination file path (.obj).</param>
        public static void ExportObj(Scene scene, string outputPath)
        {
            if (string.IsNullOrEmpty(outputPath)) throw new ArgumentException("Output path cannot be null or empty", nameof(outputPath));

            string text = ToObj(scene);
            string? dir = Path.GetDirectoryName(outputPath);
            if (!string.IsNullOrEmpty(dir) && !Directory.Exists(dir))
            {
                Directory.CreateDirectory(dir);
            }
            File.WriteAllText(outputPath, text, Encoding.UTF8);
        }
    }
}
