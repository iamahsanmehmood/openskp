using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;

namespace OpenSkp
{
    /// <summary>High-level entry point for opening and parsing .skp files.
    ///
    /// <code>
    /// SkpModel model = SkpFile.Open("house.skp");
    /// Console.WriteLine(model.Version);
    /// foreach (var layer in model.Layers) Console.WriteLine(layer.Name);
    /// </code>
    /// </summary>
    public static class SkpFile
    {
        public static SkpModel Parse(byte[] buffer)
        {
            Core.RawParsed parsed;
            try
            {
                parsed = Core.FullParse(buffer);
            }
            catch (LegacyParseError e)
            {
                throw new ArgumentException($"legacy .skp parse failed: {e.Message}", e);
            }

            var model = new SkpModel { Version = parsed.Version };

            foreach (var kv in parsed.DefsDict)
            {
                model.Definitions[kv.Key] = BuildDefinition(kv.Key, kv.Value);
            }
            model.Root = BuildDefinition(0, parsed.Root);

            foreach (var kv in parsed.LayerColors)
            {
                model.Layers.Add(new Layer { Name = kv.Key, ColorR = kv.Value.R, ColorG = kv.Value.G, ColorB = kv.Value.B });
            }

            var matForData = new Dictionary<Geometry.RawMaterial, Material>(ReferenceEqualityComparer.Instance);
            foreach (var rawMat in parsed.Materials.Values)
            {
                Texture? texture = null;
                if (rawMat.Texture != null)
                {
                    texture = new Texture
                    {
                        Filename = rawMat.Texture.Filename,
                        Width = rawMat.Texture.XScale,
                        Height = rawMat.Texture.YScale,
                        Data = rawMat.Texture.Data,
                    };
                }
                var mat = new Material
                {
                    Name = rawMat.Name,
                    Color = (rawMat.R, rawMat.G, rawMat.B, 255),
                    Transparency = rawMat.Transparency,
                    Texture = texture,
                    Colorized = rawMat.Colorized,
                    ColorizeType = rawMat.ColorizeType,
                };
                model.Materials.Add(mat);
                matForData[rawMat] = mat;
            }

            foreach (var kv in parsed.MaterialIdToName)
            {
                long mId = kv.Key;
                string mName = kv.Value;
                Geometry.RawMaterial? rawMat = parsed.Materials.TryGetValue(mName, out var m1) ? m1
                    : parsed.MaterialsByFolder.TryGetValue(mName, out var m2) ? m2 : null;
                if (rawMat == null || !matForData.TryGetValue(rawMat, out var mat)) continue;
                if (mat.Id == null) mat.Id = mId;
                model.MaterialsById[mId] = mat;
            }

            foreach (var st in parsed.Styles)
            {
                model.Styles.Add(new Style { Name = st.Name, FrontColor = st.FrontColor, BackColor = st.BackColor });
            }

            return model;
        }

        private static Definition BuildDefinition(long defId, Geometry.RawDefinition d)
        {
            var defn = new Definition
            {
                Id = defId,
                Guid = d.Guid ?? "",
                Name = d.Name ?? "",
                AlwaysFacesCamera = d.AlwaysFacesCamera,
                IsImage = d.IsImage,
            };

            foreach (var kv in d.Builder.Vertices)
            {
                var (x, y, z) = kv.Value;
                defn.Vertices[kv.Key] = new Vertex { Id = kv.Key, X = x, Y = y, Z = z };
            }

            foreach (var kv in d.Builder.Edges)
            {
                var (v1, v2) = kv.Value;
                int flags = d.Builder.EdgeFlags.TryGetValue(kv.Key, out var f) ? f : 0;
                defn.Edges[kv.Key] = new Edge
                {
                    Id = kv.Key,
                    V1Id = v1 ?? 0,
                    V2Id = v2 ?? 0,
                    Soft = (flags & 0x08) != 0,
                    Smooth = (flags & 0x10) != 0,
                    Hidden = (flags & 0x01) != 0,
                };
            }

            foreach (var kv in d.Builder.Faces)
            {
                var f = kv.Value;
                defn.Faces[kv.Key] = new Face
                {
                    Id = kv.Key,
                    Loops = f.Loops,
                    Normal = f.Normal,
                    MaterialId = f.MaterialId,
                    BackMaterialId = f.BackMaterialId,
                    UvTransform = f.UvTransform,
                    UvTransformBack = f.UvTransformBack,
                };
            }

            foreach (var inst in d.Builder.Instances)
            {
                defn.Instances.Add(new Instance
                {
                    Name = inst.Name ?? "",
                    RefIdx = inst.RefIdx,
                    Guid = inst.RefGuid ?? "",
                    Matrix = inst.Matrix,
                    MaterialId = inst.MaterialId,
                });
            }

            return defn;
        }

        public static SkpModel Open(string filePath)
        {
            var p = Path.GetFullPath(filePath);
            if (!File.Exists(p))
            {
                throw new FileNotFoundException($"File not found: {p}");
            }
            if (!string.Equals(Path.GetExtension(p), ".skp", StringComparison.OrdinalIgnoreCase))
            {
                throw new ArgumentException($"Expected a .skp file, got: {Path.GetExtension(p)}");
            }
            var bytes = File.ReadAllBytes(p);
            return Parse(bytes);
        }
    }

    internal sealed class ReferenceEqualityComparer : IEqualityComparer<Geometry.RawMaterial>
    {
        public static readonly ReferenceEqualityComparer Instance = new ReferenceEqualityComparer();
        public bool Equals(Geometry.RawMaterial? x, Geometry.RawMaterial? y) => ReferenceEquals(x, y);
        public int GetHashCode(Geometry.RawMaterial obj) => System.Runtime.CompilerServices.RuntimeHelpers.GetHashCode(obj);
    }
}
