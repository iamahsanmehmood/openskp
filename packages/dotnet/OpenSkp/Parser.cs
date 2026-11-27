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
        /// <summary>Parses a <c>.skp</c> byte buffer (VFF 2021+ or classic pre-2021 MFC) into raw definitions, layers, materials, styles, and pages without baking the placed scene graph.</summary>
        public static SkpModel Parse(byte[] buffer, SkpParseOptions? options = null)
        {
            if (buffer == null)
            {
                throw new ArgumentNullException(nameof(buffer), "Buffer cannot be null.");
            }

            var parsed = Core.FullParse(buffer, options);

            try
            {
                return BuildModel(parsed);
            }
            catch (Exception e) when (!(e is SkpParseException) && !(e is OutOfMemoryException))
            {
                throw new SkpParseException($"Failed while assembling model: {e.Message}", stage: "tlv_walk", innerException: e);
            }
        }

        private static SkpModel BuildModel(Core.RawParsed parsed)
        {
            var model = new SkpModel
            {
                Version = parsed.Version,
                Units = parsed.Units,
                UnreadRootEntities = parsed.UnreadRootEntities,
            };

            foreach (var kv in parsed.DefsDict)
            {
                model.Definitions[kv.Key] = BuildDefinition(kv.Key, kv.Value, parsed.LayerIdToName);
            }
            model.Root = BuildDefinition(0, parsed.Root, parsed.LayerIdToName);

            foreach (var kv in parsed.LayerColors)
            {
                bool hidden = parsed.LayerHidden.TryGetValue(kv.Key, out var h) && h;
                string lineStyle = parsed.LayerLineStyles.TryGetValue(kv.Key, out var ls) ? ls : "";
                model.Layers.Add(new Layer
                {
                    Name = kv.Key,
                    ColorR = kv.Value.R,
                    ColorG = kv.Value.G,
                    ColorB = kv.Value.B,
                    Hidden = hidden,
                    LineStyle = lineStyle,
                });
            }

            // Convert pages (saved scenes) - hidden layer ids resolve to
            // names; unknown ids (stale refs) are dropped.
            model.Pages.AddRange(BuildPages(parsed));

            // Convert model-level linear dimensions (VFF; world space).
            foreach (var dm in parsed.Dimensions)
            {
                model.Dimensions.Add(new Dimension
                {
                    A = dm.A,
                    B = dm.B,
                    Offset = dm.Offset,
                    PlaneX = dm.PlaneX,
                    Normal = dm.Normal,
                    OffsetVector = dm.OffsetVector,
                    Text = dm.Text,
                    Hidden = dm.Hidden,
                    Layer = ResolveAnnotationLayer(dm.Layer, parsed.LayerIdToName),
                });
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
                    Color = (rawMat.R, rawMat.G, rawMat.B, rawMat.A),
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

            if (parsed.FaceColors.HasValue)
            {
                model.FaceFrontColor = parsed.FaceColors.Value.Front;
                model.FaceBackColor = parsed.FaceColors.Value.Back;
            }
            model.ColorByTag = parsed.ColorByTag;
            model.AttributeDictionaries = parsed.ModelAttributeDictionaries;

            return model;
        }

        internal static List<Page> BuildPages(Core.RawParsed parsed)
        {
            var pages = new List<Page>(parsed.Pages.Count);
            foreach (var pg in parsed.Pages)
            {
                var hiddenLayers = new List<string>();
                foreach (var id in pg.HiddenLayerIds)
                {
                    if (parsed.LayerIdToName.TryGetValue(id, out var layerName))
                    {
                        hiddenLayers.Add(layerName);
                    }
                }
                pages.Add(new Page
                {
                    Name = pg.Name,
                    Eye = pg.Eye,
                    Target = pg.Target,
                    Up = pg.Up,
                    Fov = pg.Fov,
                    Parallel = pg.Parallel,
                    OrthoHeight = pg.OrthoHeight,
                    HiddenLayers = hiddenLayers,
                    HiddenEntityIds = new List<long>(pg.HiddenEntityIds),
                    CaptureFlags = pg.CaptureFlags,
                });
            }
            return pages;
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
                IsGroup = d.IsGroup,
                AttributeDictionaries = d.AttributeDictionaries,
            };

            foreach (var kv in d.Builder.Vertices)
            {
                var (x, y, z) = kv.Value;
                defn.Vertices[kv.Key] = new Vertex { Id = kv.Key, X = x, Y = y, Z = z };
            }

            bool isLegacy = d.Builder.EdgePids != null;
            foreach (var kv in d.Builder.Edges)
            {
                var (v1, v2) = kv.Value;
                int flags = d.Builder.EdgeFlags.TryGetValue(kv.Key, out var f) ? f : 0;
                long pid = isLegacy
                    ? (d.Builder.EdgePids!.TryGetValue(kv.Key, out var ep) ? ep : 0L)
                    : kv.Key;
                string edgeLayer = "";
                if (d.Builder.EdgeLayers.TryGetValue(kv.Key, out var edgeLayerId))
                {
                    edgeLayer = layerIdToName.TryGetValue(edgeLayerId, out var elName) ? elName : edgeLayerId.ToString();
                }
                defn.Edges[kv.Key] = new Edge
                {
                    Id = kv.Key,
                    V1Id = v1 ?? 0,
                    V2Id = v2 ?? 0,
                    Soft = (flags & 0x08) != 0,
                    Smooth = (flags & 0x10) != 0,
                    Hidden = (flags & 0x01) != 0,
                    CurveId = d.Builder.EdgeCurves.TryGetValue(kv.Key, out var curveId) ? curveId : (long?)null,
                    PersistentId = pid,
                    Layer = edgeLayer,
                };
            }

            foreach (var kv in d.Builder.ArcCurves)
            {
                var f = kv.Value;
                defn.Arcs[kv.Key] = new ArcCurve
                {
                    Center = f.Center,
                    Normal = f.Normal,
                    XAxis = f.XAxis,
                    YAxis = f.YAxis,
                    StartAngle = f.StartAngle,
                    EndAngle = f.EndAngle,
                };
            }

            foreach (var kv in d.Builder.Faces)
            {
                var f = kv.Value;
                string faceLayer = "";
                if (f.LayerId is long faceLayerId)
                {
                    faceLayer = layerIdToName.TryGetValue(faceLayerId, out var flName) ? flName : faceLayerId.ToString();
                }
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
                    PersistentId = isLegacy ? f.PersistentId : kv.Key,
                    Layer = faceLayer,
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
                    Id = inst.Id,
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
            foreach (var txt in d.Builder.Texts)
            {
                defn.Texts.Add(new TextEntity
                {
                    Text = txt.Text,
                    Hidden = txt.Hidden,
                    Layer = ResolveAnnotationLayer(txt.Layer, layerIdToName),
                    Point = txt.Point,
                    LabelPoint = txt.LabelPoint,
                    ScreenPosition = txt.ScreenPosition,
                    LeaderType = txt.LeaderType,
                    ArrowType = txt.ArrowType,
                    DisplayLeader = txt.DisplayLeader,
                });
            }
            foreach (var dim in d.Builder.Dimensions)
            {
                defn.Dimensions.Add(new Dimension
                {
                    Text = dim.Text,
                    Hidden = dim.Hidden,
                    A = dim.A,
                    B = dim.B,
                    Offset = dim.Offset,
                    PlaneX = dim.PlaneX,
                    Normal = dim.Normal,
                    OffsetVector = dim.OffsetVector,
                    Layer = ResolveAnnotationLayer(dim.Layer, layerIdToName),
                });
            }
            foreach (var cl in d.Builder.ConstructionLines)
            {
                cl.Layer = cl.LayerId is long clLayerId && layerIdToName.TryGetValue(clLayerId, out var clLayerName)
                    ? clLayerName
                    : "";
                defn.ConstructionLines.Add(cl);
            }
            foreach (var cp in d.Builder.ConstructionPoints)
            {
                cp.Layer = cp.LayerId is long cpLayerId && layerIdToName.TryGetValue(cpLayerId, out var cpLayerName)
                    ? cpLayerName
                    : "";
                defn.ConstructionPoints.Add(cp);
            }

            return defn;
        }

        private static string ResolveAnnotationLayer(string? rawLayer, Dictionary<long, string> layerIdToName)
        {
            if (string.IsNullOrEmpty(rawLayer)) return "";
            if (long.TryParse(rawLayer, out long lid))
            {
                if (lid == 0) return "";
                if (layerIdToName.TryGetValue(lid, out var name) && name != null)
                {
                    return name;
                }
            }
            return rawLayer!;
        }

        /// <summary>Reads a <c>.skp</c> file from disk and parses it via <see cref="Parse"/>.</summary>
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
            FaceGroups.UnpaintedStyleColors(parsed, options?.UnpaintedFaces);
            try
            {
                return SceneBuilder.Build(parsed, options);
            }
            catch (Exception e) when (!(e is SkpParseException) && !(e is OutOfMemoryException))
            {
                throw new SkpParseException($"Failed while building scene: {e.Message}", stage: "build_scene", innerException: e);
            }
        }

        /// <summary>Same as <see cref="BuildScene(byte[], SkpParseOptions?)"/>, reading the
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
            FaceGroups.UnpaintedStyleColors(parsed, options?.UnpaintedFaces);
            try
            {
                return InstancedSceneBuilder.Build(parsed, options);
            }
            catch (Exception e) when (!(e is SkpParseException) && !(e is OutOfMemoryException))
            {
                throw new SkpParseException($"Failed while building scene: {e.Message}", stage: "build_scene", innerException: e);
            }
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
