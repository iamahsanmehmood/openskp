using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Text;
using System.Text.RegularExpressions;

namespace OpenSkp
{
    /// <summary>
    /// Wavefront OBJ and MTL material library text exporter for baked <see cref="Scene"/> objects.
    /// </summary>
    public static class ObjExport
    {
        private static string SanitizeMaterialName(string name)
        {
            if (string.IsNullOrWhiteSpace(name)) return "default_material";
            string clean = Regex.Replace(name.Trim(), @"[^\w\.-]", "_");
            return string.IsNullOrEmpty(clean) ? "default_material" : clean;
        }

        private static List<string> MaterialNames(Scene scene)
        {
            var bases = new List<string>();
            for (int i = 0; i < scene.GltfMaterials.Count; i++)
            {
                var mat = scene.GltfMaterials[i] as Dictionary<string, object>;
                string rawName = mat != null && mat.TryGetValue("name", out object? nameObj) && nameObj != null
                    ? nameObj.ToString()! : $"Material_{i}";
                bases.Add(SanitizeMaterialName(rawName));
            }

            var reserved = new HashSet<string>(bases, StringComparer.Ordinal);
            var used = new HashSet<string>(StringComparer.Ordinal);
            var names = new List<string>();
            for (int i = 0; i < bases.Count; i++)
            {
                string name = bases[i];
                if (used.Contains(name))
                {
                    string stem = $"{name}_{i + 1}";
                    name = stem;
                    int suffix = 1;
                    while (used.Contains(name) || reserved.Contains(name))
                    {
                        name = $"{stem}_{suffix++}";
                    }
                }
                used.Add(name);
                names.Add(name);
            }
            return names;
        }

        /// <summary>
        /// Convert a baked <see cref="Scene"/>'s materials into Wavefront MTL text format.
        /// Identifiers are case-sensitive; collisions gain _&lt;1-based material index&gt;,
        /// then an additional _&lt;n&gt; if necessary. Other names remain unchanged.
        /// </summary>
        /// <param name="scene">The baked scene returned by <see cref="SkpFile.BuildScene(string, SkpParseOptions?)"/>.</param>
        /// <returns>The formatted Wavefront MTL text string.</returns>
        public static string ToMtl(Scene scene)
        {
            if (scene == null) throw new ArgumentNullException(nameof(scene));

            var sb = new StringBuilder();
            sb.Append("# OpenSKP MTL Material Library Export").Append('\n');
            sb.Append($"# Materials: {scene.GltfMaterials.Count}").Append('\n');
            sb.Append('\n');

            var names = MaterialNames(scene);
            for (int idx = 0; idx < scene.GltfMaterials.Count; idx++)
            {
                var mat = scene.GltfMaterials[idx] as Dictionary<string, object>;
                if (mat == null) continue;

                string matName = names[idx];

                float r = 0.8f, g = 0.8f, b = 0.8f, a = 1.0f;
                if (mat.TryGetValue("pbrMetallicRoughness", out object? pbrObj) && pbrObj is Dictionary<string, object> pbr)
                {
                    if (pbr.TryGetValue("baseColorFactor", out object? colObj))
                    {
                        if (colObj is System.Collections.IEnumerable values && colObj is not string)
                        {
                            // SceneBuilder creates double[] baseColorFactor values.
                            // Also accept float[] and lists supplied by API callers.
                            // Unsupported nonnumeric elements still fail conversion, as they
                            // did in the previous List<object> handling.
                            var channels = new List<float>();
                            foreach (var value in values)
                            {
                                channels.Add(Convert.ToSingle(value, CultureInfo.InvariantCulture));
                            }
                            if (channels.Count >= 3)
                            {
                                r = channels[0]; g = channels[1]; b = channels[2];
                                if (channels.Count >= 4) a = channels[3];
                            }
                        }
                    }
                }

                sb.Append($"newmtl {matName}").Append('\n');
                sb.Append("Ka 1.000000 1.000000 1.000000").Append('\n');
                sb.Append(string.Format(CultureInfo.InvariantCulture, "Kd {0:F6} {1:F6} {2:F6}", r, g, b)).Append('\n');
                sb.Append("Ks 0.200000 0.200000 0.200000").Append('\n');
                sb.Append("Ns 32.000000").Append('\n');
                sb.Append(string.Format(CultureInfo.InvariantCulture, "d {0:F6}", a)).Append('\n');
                sb.Append("illum 2").Append('\n');

                if (mat.TryGetValue("texture_path", out object? texPathObj) && texPathObj != null)
                {
                    string texName = Path.GetFileName(texPathObj.ToString()!);
                    sb.Append($"map_Kd {texName}").Append('\n');
                }

                sb.Append('\n');
            }

            return sb.ToString();
        }

        /// <summary>
        /// Convert a baked <see cref="Scene"/> into Wavefront OBJ text format.
        /// Material references use the same unique identifiers as the MTL library.
        /// </summary>
        /// <param name="scene">The baked scene returned by <see cref="SkpFile.BuildScene(string, SkpParseOptions?)"/>.</param>
        /// <param name="mtlFilename">Optional companion .mtl filename to reference.</param>
        /// <returns>The formatted Wavefront OBJ text string.</returns>
        public static string ToObj(Scene scene, string? mtlFilename = null)
        {
            if (scene == null) throw new ArgumentNullException(nameof(scene));

            var surfacePrimitives = scene.SurfacePrimitives;
            var sb = new StringBuilder();
            sb.Append("# OpenSKP OBJ Export").Append('\n');
            sb.Append($"# Primitives: {surfacePrimitives.Count}").Append('\n');

            if (!string.IsNullOrEmpty(mtlFilename))
            {
                sb.Append($"mtllib {mtlFilename}").Append('\n');
            }

            sb.Append('\n');

            var names = MaterialNames(scene);
            int vertOffset = 1;
            int uvOffset = 1;
            int normOffset = 1;

            foreach (var prim in surfacePrimitives)
            {
                sb.Append($"o {prim.GeomName}").Append('\n');

                int vertCount = prim.Positions.Length / 3;
                for (int i = 0; i < vertCount; i++)
                {
                    string x = prim.Positions[i * 3].ToString("F6", CultureInfo.InvariantCulture);
                    string y = prim.Positions[i * 3 + 1].ToString("F6", CultureInfo.InvariantCulture);
                    string z = prim.Positions[i * 3 + 2].ToString("F6", CultureInfo.InvariantCulture);
                    sb.Append($"v {x} {y} {z}").Append('\n');
                }

                int uvCount = prim.Uvs != null ? prim.Uvs.Length / 2 : 0;
                for (int i = 0; i < uvCount; i++)
                {
                    string u = prim.Uvs![i * 2].ToString("F6", CultureInfo.InvariantCulture);
                    string v = prim.Uvs![i * 2 + 1].ToString("F6", CultureInfo.InvariantCulture);
                    sb.Append($"vt {u} {v}").Append('\n');
                }

                int normCount = prim.Normals != null ? prim.Normals.Length / 3 : 0;
                for (int i = 0; i < normCount; i++)
                {
                    string nx = prim.Normals![i * 3].ToString("F6", CultureInfo.InvariantCulture);
                    string ny = prim.Normals![i * 3 + 1].ToString("F6", CultureInfo.InvariantCulture);
                    string nz = prim.Normals![i * 3 + 2].ToString("F6", CultureInfo.InvariantCulture);
                    sb.Append($"vn {nx} {ny} {nz}").Append('\n');
                }

                int matIdx = prim.MaterialIndex;
                if (matIdx >= 0 && matIdx < scene.GltfMaterials.Count)
                {
                    var mat = scene.GltfMaterials[matIdx] as Dictionary<string, object>;
                    if (mat != null)
                    {
                        sb.Append($"usemtl {names[matIdx]}").Append('\n');
                    }
                }

                int triCount = prim.Indices.Length / 3;
                bool hasUvs = uvCount == vertCount;
                bool hasNormals = normCount == vertCount;

                for (int i = 0; i < triCount; i++)
                {
                    uint i0 = prim.Indices[i * 3];
                    uint i1 = prim.Indices[i * 3 + 1];
                    uint i2 = prim.Indices[i * 3 + 2];

                    long v0 = i0 + vertOffset;
                    long v1 = i1 + vertOffset;
                    long v2 = i2 + vertOffset;

                    if (hasUvs && hasNormals)
                    {
                        long vt0 = i0 + uvOffset;
                        long vt1 = i1 + uvOffset;
                        long vt2 = i2 + uvOffset;
                        long vn0 = i0 + normOffset;
                        long vn1 = i1 + normOffset;
                        long vn2 = i2 + normOffset;
                        sb.Append($"f {v0}/{vt0}/{vn0} {v1}/{vt1}/{vn1} {v2}/{vt2}/{vn2}").Append('\n');
                    }
                    else if (hasUvs)
                    {
                        long vt0 = i0 + uvOffset;
                        long vt1 = i1 + uvOffset;
                        long vt2 = i2 + uvOffset;
                        sb.Append($"f {v0}/{vt0} {v1}/{vt1} {v2}/{vt2}").Append('\n');
                    }
                    else if (hasNormals)
                    {
                        long vn0 = i0 + normOffset;
                        long vn1 = i1 + normOffset;
                        long vn2 = i2 + normOffset;
                        sb.Append($"f {v0}//{vn0} {v1}//{vn1} {v2}//{vn2}").Append('\n');
                    }
                    else
                    {
                        sb.Append($"f {v0} {v1} {v2}").Append('\n');
                    }
                }

                vertOffset += vertCount;
                if (hasUvs) uvOffset += uvCount;
                if (hasNormals) normOffset += normCount;

                sb.Append('\n');
            }

            return sb.ToString();
        }

        /// <summary>
        /// Export a baked <see cref="Scene"/> directly to a Wavefront OBJ file at <paramref name="outputPath"/> and optional companion MTL file.
        /// </summary>
        /// <param name="scene">The baked scene returned by <see cref="SkpFile.BuildScene(string, SkpParseOptions?)"/>.</param>
        /// <param name="outputPath">Destination file path (.obj).</param>
        /// <param name="exportMtl">Whether to export companion .mtl file alongside .obj.</param>
        public static void ExportObj(Scene scene, string outputPath, bool exportMtl = true)
        {
            if (string.IsNullOrEmpty(outputPath)) throw new ArgumentException("Output path cannot be null or empty", nameof(outputPath));

            string? mtlName = exportMtl ? $"{Path.GetFileNameWithoutExtension(outputPath)}.mtl" : null;
            string text = ToObj(scene, mtlName);
            string? dir = Path.GetDirectoryName(outputPath);
            if (!string.IsNullOrEmpty(dir) && !Directory.Exists(dir))
            {
                Directory.CreateDirectory(dir);
            }
            File.WriteAllText(outputPath, text, new UTF8Encoding(false));

            if (exportMtl && !string.IsNullOrEmpty(mtlName))
            {
                string mtlPath = Path.Combine(dir ?? ".", mtlName);
                File.WriteAllText(mtlPath, ToMtl(scene), new UTF8Encoding(false));
            }
        }
    }
}
