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
        public static SkpModel Parse(byte[] buffer, SkpParseOptions? options = null)
        {
            if (buffer == null)
            {
                throw new ArgumentNullException(nameof(buffer), "Buffer cannot be null.");
            }

            var parsed = Core.FullParse(buffer, options);

            var model = new SkpModel { Version = parsed.Version, Units = parsed.Units };

            foreach (var kv in parsed.DefsDict)
            {
                model.Definitions[kv.Key] = BuildDefinition(kv.Key, kv.Value, parsed.LayerIdToName);
            }
            model.Root = BuildDefinition(0, parsed.Root, parsed.LayerIdToName);

            foreach (var kv in parsed.LayerColors)
            {
                bool hidden = parsed.LayerHidden.TryGetValue(kv.Key, out var h) && h;
                model.Layers.Add(new Layer { Name = kv.Key, ColorR = kv.Value.R, ColorG = kv.Value.G, ColorB = kv.Value.B, Hidden = hidden });
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

        private static Definition BuildDefinition(long defId, Geometry.RawDefinition d, Dictionary<long, string> layerIdToName)
        {
            var defn = new Definition
            {
                Id = defId,
                Guid = d.Guid ?? "",
                Name = d.Name ?? "",
                AlwaysFacesCamera = d.AlwaysFacesCamera,
                ShadowsFaceSun = d.ShadowsFaceSun,
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
                    UvProjected = f.UvProjected,
                    UvProjectedBack = f.UvProjectedBack,
                    Hidden = f.Hidden,
                };
            }

            foreach (var inst in d.Builder.Instances)
            {
                string layer = "";
                if (inst.LayerId is long layerId)
                {
                    layerIdToName.TryGetValue(layerId, out var layerName);
                    layer = layerName ?? "";
                }
                defn.Instances.Add(new Instance
                {
                    Name = inst.Name ?? "",
                    RefIdx = inst.RefIdx,
                    Guid = inst.RefGuid ?? "",
                    Matrix = inst.Matrix,
                    MaterialId = inst.MaterialId,
                    Hidden = inst.Hidden,
                    Layer = layer,
                    Properties = inst.Properties ?? new Dictionary<string, string>(),
                });
            }

            defn.SectionPlanes.AddRange(d.Builder.SectionPlanes);
            defn.Texts.AddRange(d.Builder.Texts);
            defn.Dimensions.AddRange(d.Builder.Dimensions);

            return defn;
        }

        public static SkpModel Open(string filePath, SkpParseOptions? options = null)
        {
            return Parse(ReadValidatedBytes(filePath), options);
        }

        /// <summary>Bake every instance actually placed in the model into
        /// world-space, triangulated mesh data - SketchUp's own
        /// component/group nesting fully resolved and flattened, ready for
        /// a GLB export or any other renderer.
        ///
        /// A separate, opt-in step from <see cref="Parse"/>/<see cref="Open"/>:
        /// it re-runs the underlying parse independently rather than
        /// reusing a prior call's data, so a plain Parse()/Open() call
        /// never pays for the heavier instancing + triangulation work
        /// here. For a file that reuses a handful of definitions across
        /// many thousands of instances, the baked output can be far
        /// larger than the file's raw geometry - that's the reason this
        /// isn't part of Parse().</summary>
        public static Scene BuildScene(byte[] buffer, SkpParseOptions? options = null)
        {
            if (buffer == null)
            {
                throw new ArgumentNullException(nameof(buffer), "Buffer cannot be null.");
            }

            var parsed = Core.FullParse(buffer, options);
            return SceneBuilder.Build(parsed, options);
        }

        /// <summary>Same as <see cref="BuildScene(byte[])"/>, reading the
        /// file from disk first.</summary>
        public static Scene BuildScene(string filePath, SkpParseOptions? options = null)
        {
            return BuildScene(ReadValidatedBytes(filePath), options);
        }

        /// <summary>Build the placed scene graph with SketchUp's
        /// component/group instancing PRESERVED, instead of baked into
        /// world-space vertex data.
        ///
        /// Use this instead of <see cref="BuildScene(byte[], SkpParseOptions?)"/>
        /// when the model reuses components: that grows with `definition
        /// geometry x placement count`, while this grows with `unique
        /// geometry + instance transforms`. A component placed 1,000 times
        /// costs one copy of its geometry here.
        ///
        /// Same separate, opt-in re-parse as <see cref="BuildScene(byte[], SkpParseOptions?)"/> -
        /// see that method's docs.</summary>
        public static InstancedScene BuildInstancedScene(byte[] buffer, SkpParseOptions? options = null)
        {
            if (buffer == null)
            {
                throw new ArgumentNullException(nameof(buffer), "Buffer cannot be null.");
            }

            var parsed = Core.FullParse(buffer, options);
            return InstancedSceneBuilder.Build(parsed, options);
        }

        /// <summary>Same as <see cref="BuildInstancedScene(byte[], SkpParseOptions?)"/>,
        /// reading the file from disk first.</summary>
        public static InstancedScene BuildInstancedScene(string filePath, SkpParseOptions? options = null)
        {
            return BuildInstancedScene(ReadValidatedBytes(filePath), options);
        }

        private static byte[] ReadValidatedBytes(string filePath)
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
            return File.ReadAllBytes(p);
        }
    }

    internal sealed class ReferenceEqualityComparer : IEqualityComparer<Geometry.RawMaterial>
    {
        public static readonly ReferenceEqualityComparer Instance = new ReferenceEqualityComparer();
        public bool Equals(Geometry.RawMaterial? x, Geometry.RawMaterial? y) => ReferenceEquals(x, y);
        public int GetHashCode(Geometry.RawMaterial obj) => System.Runtime.CompilerServices.RuntimeHelpers.GetHashCode(obj);
    }
}
