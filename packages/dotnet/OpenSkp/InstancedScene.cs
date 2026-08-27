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
        public float[] Positions { get; set; } = Array.Empty<float>();
        public float[] Normals { get; set; } = Array.Empty<float>();
        public float[] Uvs { get; set; } = Array.Empty<float>();
        public uint[] Indices { get; set; } = Array.Empty<uint>();
        public int MaterialIndex { get; set; }
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
        public string Id { get; set; } = "";
        public long? DefinitionId { get; set; }
        public string DefinitionName { get; set; } = "";
        public string VariantKey { get; set; } = "";
        public List<LocalPrimitive> Primitives { get; set; } = new List<LocalPrimitive>();
    }

    /// <summary>One placed node in the instanced scene graph.
    ///
    /// Carries the transform that places its <see cref="MeshResourceId"/>
    /// (and its whole subtree) into the scene, instead of that transform
    /// having been baked into vertex data.</summary>
    public sealed class InstancedNode
    {
        public string Name { get; set; } = "";
        public string DefinitionName { get; set; } = "";
        public string Layer { get; set; } = "";

        /// <summary>This node's transform RELATIVE TO ITS PARENT, as a
        /// 16-element column-major glTF matrix (metres, Y-up) - directly
        /// usable as a glTF node "matrix". The root node's matrix is the
        /// identity.</summary>
        public double[] Matrix { get; set; } = InstancedSceneBuilder.IdentityGltf;

        public (double X, double Y, double Z) PositionMm { get; set; }
        public Dictionary<string, string> Properties { get; set; } = new Dictionary<string, string>();
        public string? MeshResourceId { get; set; }
        public List<InstancedNode> Children { get; set; } = new List<InstancedNode>();
    }

    /// <summary>Axis-aligned bounds of the scene as PLACED, metres and
    /// Y-up.</summary>
    public sealed class SceneBounds
    {
        public (double X, double Y, double Z) Min { get; set; }
        public (double X, double Y, double Z) Max { get; set; }
        public (double X, double Y, double Z) Size { get; set; }
        public (double X, double Y, double Z) Center { get; set; }
    }

    /// <summary>The result of <see cref="InstancedSceneBuilder.Build"/>.</summary>
    public sealed class InstancedScene
    {
        public SceneBounds? Bounds { get; set; }
        public InstancedNode SceneHierarchy { get; set; } = new InstancedNode();
        public List<InstancedMeshResource> MeshResources { get; set; } = new List<InstancedMeshResource>();
        public List<object> GltfMaterials { get; set; } = new List<object>();

        /// <summary>Distinct texture images the placed materials use,
        /// deduplicated by source bytes - same as Scene.Textures.</summary>
        public List<SceneTexture> Textures { get; set; } = new List<SceneTexture>();
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
        private static double[] ToGltfMatrix(List<double> m)
        {
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

            var textures = new List<SceneTexture>();
            var textureIndexByKey = new Dictionary<string, int>();

            int? TextureIndexFor(Geometry.RawTexture? tex)
            {
                if (tex?.Data == null || tex.Data.Length == 0) return null;
                var mimeType = SniffImageMime(tex.Data);
                if (mimeType == null) return null;
                var head = BitConverter.ToString(tex.Data, 0, Math.Min(16, tex.Data.Length));
                var key = $"{tex.Data.Length}:{head}";
                if (textureIndexByKey.TryGetValue(key, out var hit)) return hit;
                var idx = textures.Count;
                textures.Add(new SceneTexture { Data = tex.Data, MimeType = mimeType, Filename = tex.Filename });
                textureIndexByKey[key] = idx;
                return idx;
            }

            var colorToMaterialIndex = new Dictionary<((int, int, int) Color, bool DoubleSided, int? TextureIndex, double Transparency), int>();
            var gltfMaterials = new List<object>();

            int GetMaterialIndex((int R, int G, int B) color, bool doubleSided, int? textureIndex, double transparency = 1.0)
            {
                var key = (color, doubleSided, textureIndex, transparency);
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

            // Identity of a mesh resource: (definition, effective fallback
            // color) - the ONLY inputs that can change what
            // FaceGroups.BuildLocalFaceGroups produces for this definition,
            // since (faithfully to the baked path this was extracted from -
            // see FaceGroups.cs's own docs) it resolves each face's
            // material from the face's OWN material id only, never from an
            // instance's painted material. Caching on the definition id
            // alone would still be wrong: the same definition renders a
            // different fallback color depending on the layer/paint
            // context it's placed in, and merging those would silently
            // repaint geometry.
            string? MeshResourceForBuilder(
                GeometryBuilder builder, string defName, long? defId,
                (int R, int G, int B)? inheritedColor, string layer)
            {
                if (builder.Faces.Count == 0) return null;

                var fallbackColor = inheritedColor ?? GetLayerColor(layer);
                var key = $"{(defId.HasValue ? defId.Value.ToString() : "ROOT")}|{fallbackColor.R},{fallbackColor.G},{fallbackColor.B}";
                if (resourceIdByKey.TryGetValue(key, out var hit)) return hit;

                var faceGroups = FaceGroups.BuildLocalFaceGroups(builder, new FaceGroups.Context
                {
                    ResolveMaterial = ResolveMaterial,
                    TextureIndexFor = TextureIndexFor,
                    FallbackColor = fallbackColor,
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
                        MaterialIndex = GetMaterialIndex(color, group.DoubleSided, texIndex, group.Transparency),
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

            string? MeshResourceFor(long defId, (int R, int G, int B)? inheritedColor, string layer)
            {
                if (!defsDict.TryGetValue(defId, out var d)) return null;
                return MeshResourceForBuilder(d.Builder, d.Name ?? "", defId, inheritedColor, layer);
            }

            List<InstancedNode> Walk(
                long defId, List<double> currentMatrix, string parentLayer, (int R, int G, int B)? inheritedColor)
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
                    return WalkBuilder(d.Builder, currentMatrix, parentLayer, inheritedColor);
                }
                finally
                {
                    activeDefinitions.Remove(defId);
                }
            }

            List<InstancedNode> WalkBuilder(
                GeometryBuilder builder, List<double> currentMatrix, string parentLayer, (int R, int G, int B)? inheritedColor)
            {
                var nodes = new List<InstancedNode>();
                foreach (var inst in builder.Instances)
                {
                    long? refIdx = inst.RefIdx;
                    var newMatrix = Transforms.MultiplyMatrices(currentMatrix, inst.Matrix);

                    string lName = parentLayer;
                    (int R, int G, int B)? instColor = inheritedColor;
                    var properties = inst.Properties != null
                        ? new Dictionary<string, string>(inst.Properties)
                        : new Dictionary<string, string>();

                    var d007 = inst.Children.FirstOrDefault(c => c.Tag == "D007");
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
                                if (mat != null) instColor = (mat.R, mat.G, mat.B);
                            }
                        }
                        try
                        {
                            properties = Geometry.ExtractDynamicProperties(d007);
                        }
                        catch (Exception e)
                        {
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
                        ? Walk(refIdx.Value, newMatrix, lName, instColor)
                        : new List<InstancedNode>();

                    double itx = newMatrix.Count > 9 ? newMatrix[9] * InchesToMm : 0.0;
                    double ity = newMatrix.Count > 10 ? newMatrix[10] * InchesToMm : 0.0;
                    double itz = newMatrix.Count > 11 ? newMatrix[11] * InchesToMm : 0.0;

                    string childDefName = refIdx.HasValue && defsDict.TryGetValue(refIdx.Value, out var childDef)
                        ? (childDef.Name ?? "") : "";

                    nodes.Add(new InstancedNode
                    {
                        Name = inst.Name ?? "",
                        DefinitionName = childDefName,
                        Layer = lName,
                        Matrix = ToGltfMatrix(inst.Matrix),
                        PositionMm = (Math.Round(itx, 2), Math.Round(ity, 2), Math.Round(itz, 2)),
                        Properties = properties,
                        MeshResourceId = refIdx.HasValue ? MeshResourceFor(refIdx.Value, instColor, lName) : null,
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

            var sceneHierarchy = new InstancedNode
            {
                Name = "ROOT",
                DefinitionName = "ROOT_MODEL",
                Layer = "Layer0",
                Matrix = (double[])IdentityGltf.Clone(),
                PositionMm = (0, 0, 0),
                Properties = new Dictionary<string, string>(),
                MeshResourceId = rootMeshResourceId,
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
                GltfMaterials = gltfMaterials,
                Textures = textures,
            };
        }

        private static string? SniffImageMime(byte[] data)
        {
            if (data.Length >= 3 && data[0] == 0xFF && data[1] == 0xD8 && data[2] == 0xFF)
            {
                return "image/jpeg";
            }
            if (data.Length >= 8 &&
                data[0] == 0x89 && data[1] == 0x50 && data[2] == 0x4E && data[3] == 0x47 &&
                data[4] == 0x0D && data[5] == 0x0A && data[6] == 0x1A && data[7] == 0x0A)
            {
                return "image/png";
            }
            return null;
        }
    }
}
