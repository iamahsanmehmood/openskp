using System;
using System.Collections.Generic;
using System.Linq;

namespace OpenSkp
{
    /// <summary>One reusable, DEFINITION-LOCAL triangulated mesh: the
    /// instanced counterpart of <see cref="GlbPrimitive"/>, minus the world
    /// transform.
    ///
    /// Positions and normals stay in the definition's own local frame
    /// (metres, glTF Y-up - already converted, same as GlbPrimitive), so N
    /// placements of the same definition share this one buffer set instead
    /// of getting N transformed copies of it. Normal transformation is
    /// deferred to the consumer/renderer's node transform (glTF's own
    /// inverse-transpose rule), which is what keeps mirrored/non-uniform-
    /// scale placements correct without a per-instance normal copy.</summary>
    public sealed class LocalPrimitive
    {
        /// <summary>Flat <c>[x, y, z, ...]</c> vertex positions in definition-local metres (glTF Y-up).</summary>
        public float[] Positions { get; set; } = Array.Empty<float>();
        /// <summary>Flat <c>[nx, ny, nz, ...]</c> local vertex normals matching <see cref="Positions"/> 1:1.</summary>
        public float[] Normals { get; set; } = Array.Empty<float>();
        /// <summary>Flat <c>[u, v, ...]</c> texture coordinates matching <see cref="Positions"/> 1:1.</summary>
        public float[] Uvs { get; set; } = Array.Empty<float>();
        /// <summary>Triangle vertex indices into <see cref="Positions"/>/<see cref="Normals"/>/<see cref="Uvs"/> (3 per triangle).</summary>
        public uint[] Indices { get; set; } = Array.Empty<uint>();

        /// <summary>Triangle offsets for separately emitted back sides.</summary>
        public uint[] BackTriangles { get; set; } = Array.Empty<uint>();

        /// <summary>Index into <see cref="InstancedScene.GltfMaterials"/> for this primitive's resolved material.</summary>
        public int MaterialIndex { get; set; }

        /// <summary>The back copies of faces with no paint on either side; see
        /// <see cref="GlbPrimitive.UnpaintedBack"/>.</summary>
        public bool UnpaintedBack { get; set; }
    }

    /// <summary>A definition's geometry, resolved for one specific
    /// rendering context and ready to be referenced by any number of
    /// <see cref="InstancedNode"/>s.
    ///
    /// One SketchUp definition can yield MORE than one resource: the same
    /// component painted with two different colors renders differently and
    /// therefore needs a separate variant - see <see cref="VariantKey"/>.</summary>
    public sealed class InstancedMeshResource
    {
        /// <summary>Stable resource identifier (for example <c>"mesh_0"</c>) referenced by <see cref="InstancedNode.MeshResourceId"/>.</summary>
        public string Id { get; set; } = "";
        /// <summary>Source SketchUp definition entity ID, or <c>null</c> for root-level geometry.</summary>
        public long? DefinitionId { get; set; }
        /// <summary>Name of the source component or group definition.</summary>
        public string DefinitionName { get; set; } = "";
        /// <summary>Key describing the inherited material/layer/style context that produced this variant.</summary>
        public string VariantKey { get; set; } = "";
        /// <summary>Definition-local triangulated primitives grouped by resolved material.</summary>
        public List<LocalPrimitive> Primitives { get; set; } = new List<LocalPrimitive>();
    }

    /// <summary>One loose-edge run in definition-local space (metres, glTF Y-up).</summary>
    public sealed class LocalCurve
    {
        /// <summary>Ordered definition-local polyline vertices in metres on glTF's Y-up axes.</summary>
        public List<(double X, double Y, double Z)> PointsM { get; set; } = new List<(double X, double Y, double Z)>();
        /// <summary>Whether the curve run forms a closed loop.</summary>
        public bool Closed { get; set; }
        /// <summary>Explicit layer/tag name assigned to the curve's edges, or <c>""</c> to inherit the placement's layer.</summary>
        public string Layer { get; set; } = "";
        /// <summary>Analytic circular arc segmentation when the run is a whole circular arc, or <c>null</c>.</summary>
        public CurveArc? Arc { get; set; }
    }

    /// <summary>A definition's loose edges, ready to be referenced by any
    /// number of <see cref="InstancedNode"/>s - the loose-edge counterpart of
    /// <see cref="InstancedMeshResource"/>.</summary>
    public sealed class InstancedCurveResource
    {
        /// <summary>Stable curve-resource identifier referenced by <see cref="InstancedNode.CurveResourceId"/>.</summary>
        public string Id { get; set; } = "";
        /// <summary>Source SketchUp definition entity ID, or <c>null</c> for root-level loose edges.</summary>
        public long? DefinitionId { get; set; }
        /// <summary>Name of the source component or group definition.</summary>
        public string DefinitionName { get; set; } = "";
        /// <summary>Definition-local loose-edge runs belonging to this resource.</summary>
        public List<LocalCurve> Curves { get; set; } = new List<LocalCurve>();
    }

    /// <summary>One placed node in the instanced scene graph.
    ///
    /// Carries the transform that places its <see cref="MeshResourceId"/>
    /// (and its whole subtree) into the scene, instead of that transform
    /// having been baked into vertex data.</summary>
    public sealed class InstancedNode
    {
        /// <summary>Resolved display name for this placed node.</summary>
        public string Name { get; set; } = "";

        /// <summary>Whether <see cref="Name"/> is a synthetic fallback
        /// (SketchUp's own internal index, e.g. "Component_5") rather than a
        /// real name from the source file - no attribute-dictionary
        /// name/label/code override, no explicit instance name, and no
        /// non-generic definition name were found. Lets a consumer (e.g.
        /// Fragments export) avoid presenting a placeholder as if it were
        /// real data. Mirrors Python's/C++'s own field of the same name.</summary>
        public bool NameIsGenerated { get; set; }

        /// <summary>Name of the source component or group definition.</summary>
        public string DefinitionName { get; set; } = "";
        /// <summary>Effective SketchUp layer/tag name resolved down the placement chain.</summary>
        public string Layer { get; set; } = "";

        /// <summary>This node's transform RELATIVE TO ITS PARENT, as a
        /// 16-element column-major glTF matrix (metres, Y-up) - directly
        /// usable as a glTF node "matrix". The root node's matrix is the
        /// identity.</summary>
        public double[] Matrix { get; set; } = InstancedSceneBuilder.IdentityGltf;

        /// <summary>Absolute world-space origin in millimetres on SketchUp's Z-up axes, matching <see cref="InstanceNode.PositionMm"/>.</summary>
        public (double X, double Y, double Z) PositionMm { get; set; }
        /// <summary>Dynamic Component key/value properties (<c>dynamic_attributes</c>) on this placement.</summary>
        public Dictionary<string, string> Properties { get; set; } = new Dictionary<string, string>();

        /// <summary>See Scene.cs's InstanceNode.AttributeDictionaries -
        /// every OTHER attribute dictionary this instance carries, keyed by
        /// the dictionary's own name (openskp#285).</summary>
        public Dictionary<string, Dictionary<string, string>> AttributeDictionaries { get; set; } = new Dictionary<string, Dictionary<string, string>>();

        /// <summary>Real SketchUp instance GUID (VFF/2021+ files only -
        /// legacy pre-2021 files carry no per-instance GUID here), or ""
        /// when the source file has none. Mirrors Python's/C++'s own field;
        /// a consumer keying on this (e.g. Fragments export) is responsible
        /// for its own collision handling - see openskp#290's rationale in
        /// export/fragments.py for why a duplicated real GUID needs the
        /// same synthetic-fallback treatment as a missing one.</summary>
        public string Guid { get; set; } = "";

        /// <summary>ID of the <see cref="InstancedMeshResource"/> placed by this node, or <c>null</c> when it places no faces.</summary>
        public string? MeshResourceId { get; set; }
        /// <summary>ID of the <see cref="InstancedCurveResource"/> placed by this node, or <c>null</c> when it places no loose edges.</summary>
        public string? CurveResourceId { get; set; }
        /// <summary>Child nodes nested inside this placement.</summary>
        public List<InstancedNode> Children { get; set; } = new List<InstancedNode>();
    }

    /// <summary>Axis-aligned bounds of the scene as PLACED, metres and
    /// Y-up.</summary>
    public sealed class SceneBounds
    {
        /// <summary>Minimum corner <c>(X, Y, Z)</c> in metres (glTF Y-up).</summary>
        public (double X, double Y, double Z) Min { get; set; }
        /// <summary>Maximum corner <c>(X, Y, Z)</c> in metres (glTF Y-up).</summary>
        public (double X, double Y, double Z) Max { get; set; }
        /// <summary>Extent <c>Max - Min</c> in metres along each axis.</summary>
        public (double X, double Y, double Z) Size { get; set; }
        /// <summary>Midpoint <c>(Min + Max) / 2</c> in metres.</summary>
        public (double X, double Y, double Z) Center { get; set; }
    }

    /// <summary>The result of <see cref="InstancedSceneBuilder.Build"/>.</summary>
    public sealed class InstancedScene
    {
        /// <summary>Placed axis-aligned scene bounds in metres (Y-up), or <c>null</c> when the scene has no geometry.</summary>
        public SceneBounds? Bounds { get; set; }
        /// <summary>Root node of the instanced scene graph.</summary>
        public InstancedNode SceneHierarchy { get; set; } = new InstancedNode();
        /// <summary>Deduplicated definition-local triangulated mesh resources.</summary>
        public List<InstancedMeshResource> MeshResources { get; set; } = new List<InstancedMeshResource>();
        /// <summary>Deduplicated definition-local loose-edge curve resources.</summary>
        public List<InstancedCurveResource> CurveResources { get; set; } = new List<InstancedCurveResource>();
        /// <summary>glTF PBR material definitions indexed by <see cref="LocalPrimitive.MaterialIndex"/>.</summary>
        public List<object> GltfMaterials { get; set; } = new List<object>();

        /// <summary>Distinct texture images the placed materials use,
        /// deduplicated by source bytes - same as Scene.Textures.</summary>
        public List<SceneTexture> Textures { get; set; } = new List<SceneTexture>();

        /// <summary>The source file's own per-layer visibility (VFF/2021+
        /// only - see Core.RawParsed.LayerHidden's own comment on why
        /// legacy files default every layer to visible), read straight from
        /// the raw parse. Mirrors Python's/C++'s own field of the same
        /// name.</summary>
        public Dictionary<string, bool> LayerHidden { get; set; } = new Dictionary<string, bool>();

        /// <summary>Number of top-level entities dropped when a classic
        /// (pre-2021) root entity walk stopped early (0 for complete classic
        /// files, VFF files, and scenes read back from .frag).</summary>
        public int UnreadRootEntities { get; set; }

        /// <summary>Saved scenes (SketchUp's "Scenes" tabs), identical to
        /// <see cref="SkpModel.Pages"/>. Camera Eye/Target/Up and OrthoHeight
        /// stay in SketchUp's native Z-up frame and inches (unlike
        /// MeshResources and node matrices, which are Y-up metres). Empty on
        /// scenes read back from .frag.</summary>
        public List<Page> Pages { get; set; } = new List<Page>();
    }

    /// <summary>Builds the placed scene graph with SketchUp's
    /// component/group instancing PRESERVED rather than baked out.
    ///
    /// Where SceneBuilder emits one world-space vertex buffer per
    /// placement, this triangulates each distinct definition (in its own
    /// rendering context) ONCE, in local space, and refers to it from every
    /// placement. Scene size therefore scales with unique geometry +
    /// instance transforms instead of definition geometry x placement
    /// count - the same value proposition for a furniture layout or a
    /// structural grid with many repeated components as the TypeScript
    /// reference (buildInstancedScene()/toInstancedGLB() in
    /// packages/typescript/src/{instanced,instanced-glb}.ts, openskp#200).
    ///
    /// Lossless: no decimation, quantisation or geometry approximation of
    /// any kind. The triangles are the same triangles SceneBuilder produces
    /// - via the SAME extracted FaceGroups.BuildLocalFaceGroups - just
    /// stored once and referenced N times instead of baked into N
    /// world-space copies.</summary>
    internal static class InstancedSceneBuilder
    {
        private const double InchesToMm = 25.4;
        private const double InchesToM = 0.0254;

        public static readonly double[] IdentityGltf =
        {
            1, 0, 0, 0,
            0, 1, 0, 0,
            0, 0, 1, 0,
            0, 0, 0, 1,
        };


        /// <summary>Convert one instance's 13-element SketchUp matrix
        /// (inches, Z-up) into a 16-element column-major glTF matrix
        /// (metres, Y-up).
        ///
        /// The axis change is the similarity transform C * M * C^-1 with
        /// C: (x, y, z) -> (x, z, -y), so it composes correctly through
        /// nesting: converting each level and multiplying gives the same
        /// result as converting the fully-composed SketchUp matrix.
        /// Translation is scaled to metres; the rotation/scale block is
        /// unitless and is not.</summary>
        private static double[] ToGltfMatrix(List<double> raw)
        {
            // Fold the matrix's scale term (index 12, SketchUp's homogeneous
            // 1/w) into the rotation and translation first, so an instance
            // scaled through it is placed and sized correctly (openskp#444).
            var m = new List<double>(Transforms.FoldScale(raw.ToArray()));
            double a = m[0], b = m[1], c = m[2];
            double d = m[3], e = m[4], f = m[5];
            double g = m[6], h = m[7], i = m[8];
            double tx = m.Count > 9 ? m[9] : 0.0;
            double ty = m.Count > 10 ? m[10] : 0.0;
            double tz = m.Count > 11 ? m[11] : 0.0;

            double r00 = a, r01 = c, r02 = -b;
            double r10 = g, r11 = i, r12 = -h;
            double r20 = -d, r21 = -f, r22 = e;

            return new double[]
            {
                r00, r10, r20, 0,
                r01, r11, r21, 0,
                r02, r12, r22, 0,
                tx * InchesToM, tz * InchesToM, -ty * InchesToM, 1,
            };
        }

        /// <summary>Multiply two 16-element column-major matrices
        /// (out = a * b).</summary>
        private static double[] Mul4(double[] a, double[] b)
        {
            var outM = new double[16];
            for (var col = 0; col < 4; col++)
            {
                for (var row = 0; row < 4; row++)
                {
                    double s = 0;
                    for (var k = 0; k < 4; k++) s += a[k * 4 + row] * b[col * 4 + k];
                    outM[col * 4 + row] = s;
                }
            }
            return outM;
        }

        public static InstancedScene Build(Core.RawParsed parsed, SkpParseOptions? options = null)
        {
            var sw = System.Diagnostics.Stopwatch.StartNew();
            var defsDict = parsed.DefsDict;
            var layerColors = parsed.LayerColors;
            var layerIdToName = parsed.LayerIdToName;
            var materialIdToName = parsed.MaterialIdToName;
            var materials = parsed.Materials;
            var materialsByFolder = parsed.MaterialsByFolder;

            Observability.Log(options, SkpLogLevel.Information, $"Building instanced scene: {defsDict.Count} definitions available");
            long instanceCounter = 0;
            var activeDefinitions = new HashSet<long>();

            (int R, int G, int B) GetLayerColor(string name) =>
                layerColors.TryGetValue(name, out var c) ? c : (136, 136, 136);

            var styleColors = FaceGroups.UnpaintedStyleColors(parsed, options?.UnpaintedFaces);

            var textures = new List<SceneTexture>();
            var textureIndexFor = FaceGroups.CreateTextureIndexFor(textures, options);

            var colorToMaterialIndex = new Dictionary<((int, int, int) Color, bool DoubleSided, int? TextureIndex, double Transparency, bool Colorized, int ColorizeType, string MaterialName), int>();
            var gltfMaterials = new List<object>();

            int GetMaterialIndex(
                (int R, int G, int B) color,
                bool doubleSided,
                int? textureIndex,
                double transparency = 1.0,
                bool colorized = false,
                int colorizeType = 0,
                string? materialName = null)
            {
                bool effectiveColorized = colorized && textureIndex.HasValue;
                int effectiveColorizeType = effectiveColorized ? colorizeType : 0;
                string matNameKey = !string.IsNullOrEmpty(materialName) ? materialName! : "";
                var key = (color, doubleSided, textureIndex, transparency, effectiveColorized, effectiveColorizeType, matNameKey);
                if (colorToMaterialIndex.TryGetValue(key, out var existing)) return existing;
                int idx = gltfMaterials.Count;
                var pbr = new Dictionary<string, object>
                {
                    ["baseColorFactor"] = new[] { color.R / 255.0, color.G / 255.0, color.B / 255.0, transparency },
                    ["metallicFactor"] = 0.0,
                    ["roughnessFactor"] = 0.8,
                };
                if (textureIndex.HasValue)
                {
                    pbr["baseColorTexture"] = new Dictionary<string, object> { ["index"] = textureIndex.Value };
                }
                var material = new Dictionary<string, object> { ["pbrMetallicRoughness"] = pbr };
                if (matNameKey.Length > 0)
                {
                    material["name"] = matNameKey;
                }
                if (effectiveColorized)
                {
                    material["extras"] = new Dictionary<string, object>
                    {
                        ["sketchup"] = new Dictionary<string, object>
                        {
                            ["colorized"] = true,
                            ["colorize_type"] = effectiveColorizeType,
                        },
                    };
                }
                if (doubleSided) material["doubleSided"] = true;
                // See Scene.cs's GetMaterialIndex for why: BLEND for
                // genuinely translucent materials, MASK (safe no-op on an
                // opaque-alpha texture) for textured ones, otherwise
                // glTF's default OPAQUE.
                if (transparency < 1.0)
                {
                    material["alphaMode"] = "BLEND";
                }
                else if (textureIndex.HasValue)
                {
                    material["alphaMode"] = "MASK";
                }
                gltfMaterials.Add(material);
                colorToMaterialIndex[key] = idx;
                return idx;
            }

            (Geometry.RawMaterial? Mat, (int R, int G, int B)? Color) ResolveMaterial(long? matId)
            {
                if (matId is long id && materialIdToName.TryGetValue(id, out var matName))
                {
                    var m = materials.TryGetValue(matName, out var m1) ? m1
                        : materialsByFolder.TryGetValue(matName, out var m2) ? m2 : null;
                    if (m != null) return (m, (m.R, m.G, m.B));
                }
                return (null, null);
            }

            var meshResources = new List<InstancedMeshResource>();
            var resourceIdByKey = new Dictionary<string, string>();
            var byteContentKey = FaceGroups.CreateByteContentKeyer();
            var curveResources = new List<InstancedCurveResource>();
            var curveResourceIdByKey = new Dictionary<string, string?>();
            var preparedCurvesCache = new Dictionary<long, PreparedDefCurves>();
            bool includeCurveSets = options?.IncludeCurveSets ?? true;
            (int R, int G, int B)? ResolveFaceTagColor(long? layerId)
            {
                if (layerId is long lid && lid != 0 && layerIdToName.TryGetValue(lid, out var fname) &&
                    !string.IsNullOrEmpty(fname) && fname != "Layer0")
                {
                    return GetLayerColor(fname);
                }
                return null;
            }

            var faceLayerCache = new Dictionary<long, (string? UniformLayer, bool HasUntaggedFace)>();
            (string? UniformLayer, bool HasUntaggedFace) GetFaceLayerSummary(long? defId, GeometryBuilder builder)
            {
                long key = defId ?? -1L;
                if (faceLayerCache.TryGetValue(key, out var hit)) return hit;
                hit = Geometry.SummarizeFaceLayers(builder, layerIdToName);
                faceLayerCache[key] = hit;
                return hit;
            }

            string? CurveResourceForBuilder(GeometryBuilder builder, string defName, long? defId, string layer)
            {
                if (!includeCurveSets)
                {
                    return null;
                }
                string effectiveLayer = layer == "Layer0"
                    ? (GetFaceLayerSummary(defId, builder).UniformLayer ?? layer)
                    : layer;
                string key = $"{(defId.HasValue ? defId.Value.ToString() : "ROOT")}|{effectiveLayer}";
                if (curveResourceIdByKey.TryGetValue(key, out var hit))
                {
                    return hit;
                }

                long prepKey = defId ?? -1L;
                if (!preparedCurvesCache.TryGetValue(prepKey, out var prepared))
                {
                    prepared = Curves.PrepareDefinitionCurves(builder, layerIdToName);
                    preparedCurvesCache[prepKey] = prepared;
                }
                if (prepared.Runs.Count == 0)
                {
                    curveResourceIdByKey[key] = null;
                    return null;
                }

                var localCurves = new List<LocalCurve>(prepared.Runs.Count);
                foreach (var run in prepared.Runs)
                {
                    var pts = new List<(double X, double Y, double Z)>(run.LocalVerts.Count);
                    foreach (var v in run.LocalVerts)
                    {
                        pts.Add((
                            Curves.Round6(v.X * Curves.InchesToM),
                            Curves.Round6(v.Z * Curves.InchesToM),
                            Curves.Round6(-v.Y * Curves.InchesToM)));
                    }

                    string runLayer = run.ExplicitLayer ?? effectiveLayer;
                    localCurves.Add(new LocalCurve
                    {
                        PointsM = pts,
                        Closed = run.Closed,
                        Layer = runLayer,
                        Arc = Curves.SolveRunArc(builder, run.EdgeIds, null, pts, run.Closed, prepared.CurveEdgeCounts),
                    });
                }
                if (localCurves.Count == 0)
                {
                    curveResourceIdByKey[key] = null;
                    return null;
                }

                string id = $"curve_{curveResources.Count}";
                curveResources.Add(new InstancedCurveResource
                {
                    Id = id,
                    DefinitionId = defId,
                    DefinitionName = defName ?? "",
                    Curves = localCurves,
                });
                curveResourceIdByKey[key] = id;
                return id;
            }

            string? CurveResourceFor(long defId, string layer)
            {
                if (!defsDict.TryGetValue(defId, out var d))
                {
                    return null;
                }
                return CurveResourceForBuilder(d.Builder, d.Name ?? "", defId, layer);
            }

            // Identity of a mesh resource: (definition, effective fallback
            // color, inherited material name/opacity/colorize/texture+tile) -
            // every input that can change what FaceGroups.BuildLocalFaceGroups
            // produces for this definition, including inherited texture image
            // content and tile scale on unpainted faces.
            string? MeshResourceForBuilder(
                GeometryBuilder builder, string defName, long? defId,
                (int R, int G, int B)? inheritedColor, string layer,
                Geometry.RawMaterial? inheritedMaterial = null)
            {
                if (builder.Faces.Count == 0) return null;

                (int R, int G, int B) fallbackColor, fallbackBackColor;
                Func<long?, (int R, int G, int B)?>? faceTagColor = null;
                string colorPart;
                if (inheritedColor.HasValue)
                {
                    fallbackColor = fallbackBackColor = inheritedColor.Value;
                    colorPart = $"{fallbackColor.R},{fallbackColor.G},{fallbackColor.B}";
                }
                else if (styleColors.HasValue)
                {
                    (fallbackColor, fallbackBackColor) = styleColors.Value;
                    colorPart = $"{fallbackColor.R},{fallbackColor.G},{fallbackColor.B}";
                }
                else
                {
                    var summary = GetFaceLayerSummary(defId, builder);
                    string colorLayer = summary.UniformLayer ?? layer;
                    fallbackColor = fallbackBackColor = GetLayerColor(colorLayer);
                    if (inheritedMaterial == null)
                    {
                        faceTagColor = ResolveFaceTagColor;
                    }
                    colorPart = (summary.HasUntaggedFace || summary.UniformLayer != null || inheritedMaterial != null)
                        ? $"{fallbackColor.R},{fallbackColor.G},{fallbackColor.B}"
                        : "tags";
                }
                string matPart = "";
                if (inheritedMaterial != null)
                {
                    var tex = inheritedMaterial.Texture;
                    string texPart;
                    if (tex != null)
                    {
                        string dataKey = tex.Data != null && tex.Data.Length > 0
                            ? byteContentKey(tex.Data).ToString(System.Globalization.CultureInfo.InvariantCulture)
                            : "-";
                        double tileW = tex.XScale > 1e-9 ? tex.XScale : 1.0;
                        double tileH = tex.YScale > 1e-9 ? tex.YScale : 1.0;
                        texPart = string.Format(
                            System.Globalization.CultureInfo.InvariantCulture,
                            "{0}:{1:R}x{2:R}",
                            dataKey, tileW, tileH);
                    }
                    else
                    {
                        texPart = "-";
                    }
                    matPart = string.Format(
                        System.Globalization.CultureInfo.InvariantCulture,
                        "|{0}:{1:R}:{2}:{3}:{4}",
                        inheritedMaterial.Name,
                        FaceGroups.ResolveTransparency(inheritedMaterial),
                        inheritedMaterial.Colorized ? 1 : 0,
                        inheritedMaterial.ColorizeType,
                        texPart);
                }
                var key = $"{(defId.HasValue ? defId.Value.ToString() : "ROOT")}|{colorPart}{matPart}";
                if (fallbackBackColor != fallbackColor)
                {
                    key += $"/{fallbackBackColor.R},{fallbackBackColor.G},{fallbackBackColor.B}";
                }
                if (resourceIdByKey.TryGetValue(key, out var hit)) return hit;

                var faceGroups = FaceGroups.BuildLocalFaceGroups(builder, new FaceGroups.Context
                {
                    ResolveMaterial = matId =>
                    {
                        var resolved = ResolveMaterial(matId);
                        return resolved.Mat != null || inheritedMaterial == null
                            ? resolved
                            : (inheritedMaterial, (inheritedMaterial.R, inheritedMaterial.G, inheritedMaterial.B));
                    },
                    TextureIndexFor = textureIndexFor,
                    FallbackColor = fallbackColor,
                    FallbackBackColor = fallbackBackColor,
                    FaceTagColor = faceTagColor,
                    DefinitionId = defId,
                });

                var primitives = new List<LocalPrimitive>();
                foreach (var groupKv in faceGroups)
                {
                    var color = groupKv.Key.Color;
                    var texIndex = groupKv.Key.TextureIndex;
                    var group = groupKv.Value;
                    if (group.LocalFaces.Count == 0) continue;

                    int vertCount = group.LocalVerts.Count;
                    var positions = new float[vertCount * 3];
                    var normals = new float[vertCount * 3];
                    var uvs = new float[vertCount * 2];
                    var vertexNormalsAccum = group.NormalsAccum;

                    for (var i = 0; i < vertCount; i++)
                    {
                        var v = group.LocalVerts[i];
                        // Local space, so no instance matrix is applied -
                        // only the inches->metres scale and SketchUp Z-up
                        // -> glTF Y-up axis swap, the same fixed
                        // conventions the baked path applies.
                        positions[i * 3] = (float)(v.X * InchesToM);
                        positions[i * 3 + 1] = (float)(v.Z * InchesToM);
                        positions[i * 3 + 2] = (float)(-v.Y * InchesToM);

                        uvs[i * 2] = (float)group.LocalUvs[i].U;
                        uvs[i * 2 + 1] = (float)group.LocalUvs[i].V;

                        var raw = vertexNormalsAccum[i];
                        double normLen = Math.Sqrt(raw[0] * raw[0] + raw[1] * raw[1] + raw[2] * raw[2]);
                        double nx0, ny0, nz0;
                        if (normLen > 1e-6)
                        {
                            nx0 = raw[0] / normLen; ny0 = raw[1] / normLen; nz0 = raw[2] / normLen;
                        }
                        else
                        {
                            nx0 = 0; ny0 = 0; nz0 = 1;
                        }
                        // Same axis swap as positions. No instance-matrix
                        // normal transform here: that belongs to the node,
                        // and deferring it is precisely what keeps
                        // mirrored/non-uniform scales correct per
                        // placement.
                        normals[i * 3] = (float)nx0;
                        normals[i * 3 + 1] = (float)nz0;
                        normals[i * 3 + 2] = (float)(-ny0);
                    }

                    var indices = new uint[group.LocalFaces.Count * 3];
                    for (var i = 0; i < group.LocalFaces.Count; i++)
                    {
                        indices[i * 3] = (uint)group.LocalFaces[i][0];
                        indices[i * 3 + 1] = (uint)group.LocalFaces[i][1];
                        indices[i * 3 + 2] = (uint)group.LocalFaces[i][2];
                    }

                    primitives.Add(new LocalPrimitive
                    {
                        Positions = positions,
                        Normals = normals,
                        Uvs = uvs,
                        Indices = indices,
                        BackTriangles = group.BackTriangles.Count == 0 ? Array.Empty<uint>() : group.BackTriangles.ToArray(),
                        MaterialIndex = GetMaterialIndex(
                            color,
                            group.DoubleSided,
                            texIndex,
                            group.Transparency,
                            group.Colorized,
                            group.ColorizeType,
                            group.MaterialName),
                        UnpaintedBack = group.UnpaintedBack,
                    });
                }

                if (primitives.Count == 0) return null;

                var resourceId = $"mesh_{meshResources.Count}";
                meshResources.Add(new InstancedMeshResource
                {
                    Id = resourceId,
                    DefinitionId = defId,
                    DefinitionName = defName,
                    VariantKey = key,
                    Primitives = primitives,
                });
                resourceIdByKey[key] = resourceId;
                return resourceId;
            }

            string? MeshResourceFor(long defId, (int R, int G, int B)? inheritedColor, string layer, Geometry.RawMaterial? inheritedMaterial = null)
            {
                if (!defsDict.TryGetValue(defId, out var d)) return null;
                return MeshResourceForBuilder(d.Builder, d.Name ?? "", defId, inheritedColor, layer, inheritedMaterial);
            }

            List<InstancedNode> Walk(
                long defId, List<double> currentMatrix, string parentLayer, (int R, int G, int B)? inheritedColor,
                Geometry.RawMaterial? inheritedMaterial = null)
            {
                if (!defsDict.TryGetValue(defId, out var d))
                {
                    return new List<InstancedNode>();
                }
                if (!activeDefinitions.Add(defId))
                {
                    throw new SkpParseException(
                        "Recursive component definition",
                        stage: "build_scene", definitionId: defId);
                }
                try
                {
                    return WalkBuilder(d.Builder, currentMatrix, parentLayer, inheritedColor, inheritedMaterial);
                }
                finally
                {
                    activeDefinitions.Remove(defId);
                }
            }

            List<InstancedNode> WalkBuilder(
                GeometryBuilder builder, List<double> currentMatrix, string parentLayer, (int R, int G, int B)? inheritedColor,
                Geometry.RawMaterial? inheritedMaterial = null)
            {
                var nodes = new List<InstancedNode>();
                foreach (var inst in builder.Instances)
                {
                    long? refIdx = inst.RefIdx;
                    var newMatrix = Transforms.MultiplyMatrices(currentMatrix, inst.Matrix);

                    string lName = parentLayer;
                    (int R, int G, int B)? instColor = inheritedColor;
                    Geometry.RawMaterial? instMaterial = inheritedMaterial;
                    var properties = inst.Properties != null
                        ? new Dictionary<string, string>(inst.Properties)
                        : new Dictionary<string, string>();

                    // A classic (pre-2021) instance has no D007: its own tag
                    // and paint were read with it.
                    if (inst.LayerId is long ownLayerId && layerIdToName.TryGetValue(ownLayerId, out var ownLayerName))
                    {
                        lName = ownLayerName;
                    }
                    var (paintedMat, paintedColor) = ResolveMaterial(inst.MaterialId);
                    if (paintedMat != null)
                    {
                        instColor = paintedColor;
                        instMaterial = paintedMat;
                    }

                    var d007 = inst.Children.FirstOrDefault(c => c.Tag == "D007");
                    var rawAttrDicts = inst.AttributeDicts;
                    if (d007 != null)
                    {
                        var d207 = d007.Children.FirstOrDefault(c => c.Tag == "D207");
                        if (d207 != null && d207.Payload.Length > 0)
                        {
                            var p = d207.Payload;
                            long lId = p.Length == 1 ? p[0] : Tlv.ParseVarInt(p, 0, p.Length);
                            lName = layerIdToName.TryGetValue(lId, out var ln) ? ln : parentLayer;
                        }
                        var d107 = d007.Children.FirstOrDefault(c => c.Tag == "D107");
                        if (d107 != null)
                        {
                            long instMatId = Tlv.ParseVarInt(d107.Payload, 0, d107.Payload.Length);
                            if (materialIdToName.TryGetValue(instMatId, out var matName))
                            {
                                var mat = materials.TryGetValue(matName, out var m1) ? m1
                                    : materialsByFolder.TryGetValue(matName, out var m2) ? m2 : null;
                                if (mat != null)
                                {
                                    instColor = (mat.R, mat.G, mat.B);
                                    instMaterial = mat;
                                }
                            }
                        }
                        try
                        {
                            rawAttrDicts ??= Geometry.ExtractAttributeDictionaries(d007);
                            properties = new Dictionary<string, string>();
                            if (rawAttrDicts.TryGetValue("dynamic_attributes", out var dynamicAttrs))
                            {
                                foreach (var entry in dynamicAttrs)
                                {
                                    properties[entry.Key] = Geometry.StringifyVffAttrValue(entry.Value);
                                }
                            }
                        }
                        catch (Exception e)
                        {
                            properties = new Dictionary<string, string>();
                            rawAttrDicts = null;
                            Observability.Log(
                                options, SkpLogLevel.Debug,
                                $"Failed to extract dynamic properties for instance {inst.Name} (refIdx={refIdx}): {e.Message}");
                        }
                    }

                    instanceCounter++;
                    if (instanceCounter % ParseTuning.ProgressInterval == 0)
                    {
                        Observability.Progress(options, "build_scene", instanceCounter, instanceCounter);
                        Observability.Log(options, SkpLogLevel.Debug, $"Processed {instanceCounter} placed instances");
                    }

                    var children = refIdx.HasValue
                        ? Walk(refIdx.Value, newMatrix, lName, instColor, instMaterial)
                        : new List<InstancedNode>();

                    double itx = newMatrix.Count > 9 ? newMatrix[9] * InchesToMm : 0.0;
                    double ity = newMatrix.Count > 10 ? newMatrix[10] * InchesToMm : 0.0;
                    double itz = newMatrix.Count > 11 ? newMatrix[11] * InchesToMm : 0.0;

                    string childDefName = refIdx.HasValue && defsDict.TryGetValue(refIdx.Value, out var childDef)
                        ? (childDef.Name ?? "") : "";

                    // Fallback order: an attribute-dict name/label/code
                    // override, then the instance's own explicit name, then
                    // the definition's own name IF it's not itself just
                    // SketchUp's auto-generated "Group#1"/"Component#12"
                    // placeholder, then finally the internal index - the
                    // only case with no real name anywhere in the source
                    // file. Mirrors Python's/C++'s own instanced_scene name
                    // resolution exactly.
                    bool defNameIsReal = !string.IsNullOrEmpty(childDefName) && !Geometry.IsGenericDefinitionName(childDefName);
                    string? nameOverride = Geometry.FindNameOverride(rawAttrDicts);
                    bool instNameNonEmpty = !string.IsNullOrEmpty(inst.Name);
                    string instName = instNameNonEmpty ? inst.Name! : (defNameIsReal ? childDefName : $"Component_{refIdx ?? 0}");
                    string displayName = nameOverride ?? instName;
                    bool nameIsGenerated = nameOverride == null && !instNameNonEmpty && !defNameIsReal;

                    // Every OTHER attribute dictionary this instance carries -
                    // dynamic_attributes is already surfaced separately as
                    // Properties above, and SU_InstanceSet is SketchUp's own
                    // always-present, always-empty Owner/Status boilerplate,
                    // not worth surfacing. Mirrors Scene.cs's InstanceNode
                    // (and Python's own attribute_dictionaries) exactly
                    // (openskp#285).
                    var attributeDictionaries = new Dictionary<string, Dictionary<string, string>>();
                    if (rawAttrDicts != null)
                    {
                        foreach (var kv in rawAttrDicts)
                        {
                            if (kv.Key == "dynamic_attributes" || kv.Key == "SU_InstanceSet") continue;
                            var stringified = new Dictionary<string, string>();
                            foreach (var entry in kv.Value) stringified[entry.Key] = Geometry.StringifyVffAttrValue(entry.Value);
                            attributeDictionaries[kv.Key] = stringified;
                        }
                    }

                    string nodeLayer = lName;
                    if (lName == "Layer0" && children.Count == 0 && refIdx.HasValue && defsDict.TryGetValue(refIdx.Value, out var leafDef))
                    {
                        nodeLayer = GetFaceLayerSummary(refIdx.Value, leafDef.Builder).UniformLayer ?? lName;
                    }

                    nodes.Add(new InstancedNode
                    {
                        Name = displayName,
                        NameIsGenerated = nameIsGenerated,
                        DefinitionName = childDefName,
                        Layer = nodeLayer,
                        Matrix = ToGltfMatrix(inst.Matrix),
                        PositionMm = (Math.Round(itx, 2), Math.Round(ity, 2), Math.Round(itz, 2)),
                        Properties = properties,
                        AttributeDictionaries = attributeDictionaries,
                        Guid = inst.RefGuid ?? "",
                        MeshResourceId = refIdx.HasValue ? MeshResourceFor(refIdx.Value, instColor, lName, instMaterial) : null,
                        CurveResourceId = refIdx.HasValue ? CurveResourceFor(refIdx.Value, lName) : null,
                        Children = children,
                    });
                }
                return nodes;
            }

            var identityMat = new List<double> { 1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1.0 };
            var rootChildren = WalkBuilder(parsed.Root.Builder, identityMat, "Layer0", null);

            // Loose geometry drawn straight into the model (not inside any
            // component/group) is kept, as the baked path keeps it: it
            // becomes the root node's own mesh resource.
            var rootMeshResourceId = MeshResourceForBuilder(parsed.Root.Builder, "ROOT_MODEL", null, null, "Layer0");
            var rootCurveResourceId = CurveResourceForBuilder(parsed.Root.Builder, "ROOT_MODEL", null, "Layer0");

            var sceneHierarchy = new InstancedNode
            {
                Name = "ROOT",
                DefinitionName = "ROOT_MODEL",
                Layer = "Layer0",
                Matrix = (double[])IdentityGltf.Clone(),
                PositionMm = (0, 0, 0),
                Properties = new Dictionary<string, string>(),
                MeshResourceId = rootMeshResourceId,
                CurveResourceId = rootCurveResourceId,
                Children = rootChildren,
            };

            // Bounds of the scene AS PLACED: walk the tree, transform each
            // resource's local corners by the accumulated node matrix. Only
            // the 8 corners of each resource's local box are transformed
            // rather than every vertex - an affine transform maps a box's
            // corners to the corners of the transformed box, so the result
            // is exact for the axis-aligned bounds, at a fraction of the
            // cost.
            var resourceById = meshResources.ToDictionary(r => r.Id);
            var localBoxCache = new Dictionary<string, (double[] Lo, double[] Hi)?>();

            (double[] Lo, double[] Hi)? LocalBox(string resourceId)
            {
                if (localBoxCache.TryGetValue(resourceId, out var cached)) return cached;
                (double[] Lo, double[] Hi)? box = null;
                if (resourceById.TryGetValue(resourceId, out var res))
                {
                    var lo = new double[] { double.PositiveInfinity, double.PositiveInfinity, double.PositiveInfinity };
                    var hi = new double[] { double.NegativeInfinity, double.NegativeInfinity, double.NegativeInfinity };
                    foreach (var prim in res.Primitives)
                    {
                        for (var i = 0; i < prim.Positions.Length; i += 3)
                        {
                            for (var k = 0; k < 3; k++)
                            {
                                var v = prim.Positions[i + k];
                                if (v < lo[k]) lo[k] = v;
                                if (v > hi[k]) hi[k] = v;
                            }
                        }
                    }
                    if (!double.IsPositiveInfinity(lo[0])) box = (lo, hi);
                }
                localBoxCache[resourceId] = box;
                return box;
            }

            var bMin = new double[] { double.PositiveInfinity, double.PositiveInfinity, double.PositiveInfinity };
            var bMax = new double[] { double.NegativeInfinity, double.NegativeInfinity, double.NegativeInfinity };

            void Accumulate(InstancedNode node, double[] parent)
            {
                var world = Mul4(parent, node.Matrix);
                if (node.MeshResourceId != null)
                {
                    var box = LocalBox(node.MeshResourceId);
                    if (box.HasValue)
                    {
                        var (lo, hi) = box.Value;
                        for (var c = 0; c < 8; c++)
                        {
                            double x = (c & 1) != 0 ? hi[0] : lo[0];
                            double y = (c & 2) != 0 ? hi[1] : lo[1];
                            double z = (c & 4) != 0 ? hi[2] : lo[2];
                            double wx = world[0] * x + world[4] * y + world[8] * z + world[12];
                            double wy = world[1] * x + world[5] * y + world[9] * z + world[13];
                            double wz = world[2] * x + world[6] * y + world[10] * z + world[14];
                            if (wx < bMin[0]) bMin[0] = wx;
                            if (wy < bMin[1]) bMin[1] = wy;
                            if (wz < bMin[2]) bMin[2] = wz;
                            if (wx > bMax[0]) bMax[0] = wx;
                            if (wy > bMax[1]) bMax[1] = wy;
                            if (wz > bMax[2]) bMax[2] = wz;
                        }
                    }
                }
                foreach (var child in node.Children) Accumulate(child, world);
            }
            Accumulate(sceneHierarchy, (double[])IdentityGltf.Clone());

            SceneBounds? bounds = null;
            if (!double.IsPositiveInfinity(bMin[0]))
            {
                bounds = new SceneBounds
                {
                    Min = (bMin[0], bMin[1], bMin[2]),
                    Max = (bMax[0], bMax[1], bMax[2]),
                    Size = (bMax[0] - bMin[0], bMax[1] - bMin[1], bMax[2] - bMin[2]),
                    Center = ((bMin[0] + bMax[0]) / 2, (bMin[1] + bMax[1]) / 2, (bMin[2] + bMax[2]) / 2),
                };
            }

            Observability.Log(
                options, SkpLogLevel.Information,
                $"Instanced scene build complete: {instanceCounter} instances, {meshResources.Count} mesh resources ({sw.Elapsed.TotalSeconds:F2}s)");

            return new InstancedScene
            {
                Bounds = bounds,
                SceneHierarchy = sceneHierarchy,
                MeshResources = meshResources,
                CurveResources = curveResources,
                GltfMaterials = gltfMaterials,
                Textures = textures,
                LayerHidden = new Dictionary<string, bool>(parsed.LayerHidden),
                UnreadRootEntities = parsed.UnreadRootEntities,
                Pages = SkpFile.BuildPages(parsed),
            };
        }
    }
}
