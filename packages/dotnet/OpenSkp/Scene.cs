using System;
using System.Collections.Generic;
using System.Linq;

namespace OpenSkp
{
    /// <summary>One node in the baked, world-space instance tree.</summary>
    public sealed class InstanceNode
    {
        public string Name { get; set; } = "";
        public string DefinitionName { get; set; } = "";
        public string Layer { get; set; } = "";
        public (double X, double Y, double Z) PositionMm { get; set; }
        public Dictionary<string, string> Properties { get; set; } = new Dictionary<string, string>();
        public List<InstanceNode> Children { get; set; } = new List<InstanceNode>();
    }

    /// <summary>Metadata for one baked mesh, keyed the same as its
    /// GlbPrimitive's GeomName in Scene.MeshIndex.</summary>
    public sealed class MeshMetadata
    {
        public string Name { get; set; } = "";
        public string DefinitionName { get; set; } = "";
        public string Layer { get; set; } = "";
        public (double X, double Y, double Z) PositionMm { get; set; }
        public Dictionary<string, string> Properties { get; set; } = new Dictionary<string, string>();
        public string Path { get; set; } = "";
    }

    /// <summary>One triangulated, world-space mesh: all faces sharing a
    /// single resolved color from one flattened scene-graph position.
    /// Ready to hand straight to a GLB/glTF exporter or any other
    /// renderer.</summary>
    public sealed class GlbPrimitive
    {
        /// <summary>Flat [x, y, z, x, y, z, ...] vertex positions, in
        /// metres, Y-up.</summary>
        public float[] Positions { get; set; } = Array.Empty<float>();

        /// <summary>Flat [x, y, z, ...] vertex normals, matching Positions
        /// 1:1.</summary>
        public float[] Normals { get; set; } = Array.Empty<float>();

        /// <summary>Triangle vertex indices into Positions/Normals (3 per
        /// triangle).</summary>
        public uint[] Indices { get; set; } = Array.Empty<uint>();

        /// <summary>Index into Scene.GltfMaterials for this primitive's
        /// resolved color.</summary>
        public int MaterialIndex { get; set; }

        /// <summary>Matches the corresponding key in Scene.MeshIndex.</summary>
        public string GeomName { get; set; } = "";
    }

    /// <summary>The result of baking a parsed file's placed instances into
    /// a flat, world-space 3D scene.</summary>
    public sealed class Scene
    {
        public InstanceNode SceneHierarchy { get; set; } = new InstanceNode();
        public Dictionary<string, MeshMetadata> MeshIndex { get; set; } = new Dictionary<string, MeshMetadata>();
        public List<GlbPrimitive> GlbPrimitives { get; set; } = new List<GlbPrimitive>();
        public List<object> GltfMaterials { get; set; } = new List<object>();
    }

    /// <summary>Bakes every instance actually placed in a parsed model into
    /// world-space, triangulated mesh data - SketchUp's own component/group
    /// nesting fully resolved and flattened. See SkpFile.BuildScene() for
    /// why this is a separate, opt-in step from Parse().
    ///
    /// Ported from the TypeScript reference implementation
    /// (model.ts's buildSceneFromParsed).</summary>
    internal static class SceneBuilder
    {
        private const double InchesToMm = 25.4;
        private const double InchesToM = 0.0254;

        private sealed class FaceGroup
        {
            public (int R, int G, int B) Color;
            public List<(double X, double Y, double Z)> LocalVerts = new List<(double, double, double)>();
            public List<long[]> LocalFaces = new List<long[]>();
            public Dictionary<long, int> LocalVMap = new Dictionary<long, int>();
            public List<(long FId, GeometryBuilderFace FData)> FaceList = new List<(long, GeometryBuilderFace)>();
        }

        public static Scene Build(Core.RawParsed parsed, SkpParseOptions? options = null)
        {
            var sw = System.Diagnostics.Stopwatch.StartNew();
            var defsDict = parsed.DefsDict;
            var layerColors = parsed.LayerColors;
            var layerIdToName = parsed.LayerIdToName;
            var materialIdToName = parsed.MaterialIdToName;
            var materials = parsed.Materials;
            var materialsByFolder = parsed.MaterialsByFolder;

            Observability.Log(options, SkpLogLevel.Information, $"Building scene: {defsDict.Count} definitions available");
            long instanceCounter = 0;

            int meshCounter = 0;
            var meshIndex = new Dictionary<string, MeshMetadata>();
            var glbPrimitives = new List<GlbPrimitive>();

            var colorToMaterialIndex = new Dictionary<(int, int, int), int>();
            var gltfMaterials = new List<object>();

            (int R, int G, int B) GetLayerColor(string name)
            {
                return layerColors.TryGetValue(name, out var c) ? c : (136, 136, 136);
            }

            int GetMaterialIndex((int R, int G, int B) color)
            {
                if (colorToMaterialIndex.TryGetValue(color, out var existing)) return existing;
                int idx = gltfMaterials.Count;
                gltfMaterials.Add(new
                {
                    pbrMetallicRoughness = new
                    {
                        baseColorFactor = new[] { color.R / 255.0, color.G / 255.0, color.B / 255.0, 1.0 },
                        metallicFactor = 0.0,
                        roughnessFactor = 0.8,
                    },
                });
                colorToMaterialIndex[color] = idx;
                return idx;
            }

            List<InstanceNode> Instantiate(
                long defId, bool isRoot, List<double> currentMatrix,
                string parentLayer, string pathName, (int R, int G, int B)? inheritedColor)
            {
                if (!defsDict.TryGetValue(defId, out var d))
                {
                    return new List<InstanceNode>();
                }
                return InstantiateBuilder(d.Builder, d.Name ?? "", defId, currentMatrix, parentLayer, pathName, inheritedColor);
            }

            List<InstanceNode> InstantiateRoot(GeometryBuilder rootBuilder, List<double> currentMatrix)
            {
                return InstantiateBuilder(rootBuilder, "ROOT_MODEL", null, currentMatrix, "Layer0", "ROOT", null);
            }

            List<InstanceNode> InstantiateBuilder(
                GeometryBuilder builder, string defName, long? defId, List<double> currentMatrix,
                string parentLayer, string pathName, (int R, int G, int B)? inheritedColor)
            {
                if (builder.Faces.Count > 0)
                {
                    var faceGroups = new Dictionary<(int, int, int), FaceGroup>();

                    foreach (var faceKv in builder.Faces)
                    {
                        long fId = faceKv.Key;
                        var fData = faceKv.Value;
                        (int R, int G, int B)? faceColor = inheritedColor;
                        if (fData.MaterialId is long faceMatId)
                        {
                            if (materialIdToName.TryGetValue(faceMatId, out var matName))
                            {
                                var mat = materials.TryGetValue(matName, out var m1) ? m1
                                    : materialsByFolder.TryGetValue(matName, out var m2) ? m2 : null;
                                if (mat != null) faceColor = (mat.R, mat.G, mat.B);
                            }
                        }
                        var resolvedColor = faceColor ?? GetLayerColor(parentLayer);

                        if (!faceGroups.TryGetValue(resolvedColor, out var group))
                        {
                            group = new FaceGroup { Color = resolvedColor };
                            faceGroups[resolvedColor] = group;
                        }

                        var loops = new List<List<long>>();
                        foreach (var loop in fData.Loops)
                        {
                            var loopVerts = ReconstructLoopVertices(loop, builder.Edges);
                            if (loopVerts.Count > 0) loops.Add(loopVerts);
                        }
                        if (loops.Count == 0) continue;

                        List<long[]> triangles;
                        try
                        {
                            triangles = Triangulator.TriangulateFace3D(builder.Vertices, loops, fData.Normal);
                        }
                        catch (Exception e) when (!(e is SkpParseException))
                        {
                            throw new SkpParseException(
                                $"Failed to triangulate face: {e.Message}",
                                stage: "build_scene", definitionId: defId, innerException: e);
                        }
                        foreach (var tri in triangles)
                        {
                            var faceIndices = new List<int>();
                            foreach (var vId in tri)
                            {
                                if (builder.Vertices.ContainsKey(vId))
                                {
                                    if (!group.LocalVMap.TryGetValue(vId, out int idx))
                                    {
                                        group.LocalVerts.Add(builder.Vertices[vId]);
                                        idx = group.LocalVerts.Count - 1;
                                        group.LocalVMap[vId] = idx;
                                    }
                                    faceIndices.Add(idx);
                                }
                            }
                            if (faceIndices.Count == 3)
                            {
                                group.LocalFaces.Add(new long[] { faceIndices[0], faceIndices[1], faceIndices[2] });
                            }
                        }
                        group.FaceList.Add((fId, fData));
                    }

                    bool isRootPath = pathName == "ROOT";
                    bool multiGroup = faceGroups.Count > 1;

                    foreach (var groupKv in faceGroups)
                    {
                        var color = groupKv.Key;
                        var group = groupKv.Value;
                        if (group.LocalFaces.Count == 0) continue;

                        double tx = isRootPath ? 0.0 : (currentMatrix.Count > 9 ? currentMatrix[9] : 0.0) * InchesToMm;
                        double ty = isRootPath ? 0.0 : (currentMatrix.Count > 10 ? currentMatrix[10] : 0.0) * InchesToMm;
                        double tz = isRootPath ? 0.0 : (currentMatrix.Count > 11 ? currentMatrix[11] : 0.0) * InchesToMm;

                        string safePath = pathName.Replace(" / ", "__").Replace(" ", "_");
                        if (safePath.Length > 80) safePath = safePath.Substring(0, 80);
                        string colorSuffix = multiGroup ? $"_{color.Item1}_{color.Item2}_{color.Item3}" : "";
                        string geomName = $"mesh_{meshCounter}_{safePath}_{parentLayer}{colorSuffix}";
                        meshCounter++;

                        meshIndex[geomName] = new MeshMetadata
                        {
                            Name = isRootPath ? "ROOT" : (pathName.Split(new[] { " / " }, StringSplitOptions.None).LastOrDefault() ?? ""),
                            DefinitionName = defName ?? "",
                            Layer = parentLayer,
                            PositionMm = (Math.Round(tx, 2), Math.Round(ty, 2), Math.Round(tz, 2)),
                            Properties = new Dictionary<string, string>(),
                            Path = pathName,
                        };

                        int vertCount = group.LocalVerts.Count;
                        var positions = new float[vertCount * 3];
                        var normals = new float[vertCount * 3];
                        var vertexNormalsAccum = new double[vertCount][];
                        for (int i = 0; i < vertCount; i++) vertexNormalsAccum[i] = new double[3];

                        foreach (var faceEntry in group.FaceList)
                        {
                            var fData = faceEntry.FData;
                            var loops = new List<List<long>>();
                            foreach (var loop in fData.Loops)
                            {
                                var loopVerts = ReconstructLoopVertices(loop, builder.Edges);
                                if (loopVerts.Count > 0) loops.Add(loopVerts);
                            }
                            if (loops.Count == 0) continue;
                            var fn = fData.Normal;
                            foreach (var loop in loops)
                            {
                                foreach (var vId in loop)
                                {
                                    if (group.LocalVMap.TryGetValue(vId, out int idx))
                                    {
                                        vertexNormalsAccum[idx][0] += fn.X;
                                        vertexNormalsAccum[idx][1] += fn.Y;
                                        vertexNormalsAccum[idx][2] += fn.Z;
                                    }
                                }
                            }
                        }

                        for (int i = 0; i < vertCount; i++)
                        {
                            var v = group.LocalVerts[i];
                            var pt = Transforms.TransformPoint(currentMatrix.ToArray(), v);
                            positions[i * 3] = (float)(pt.X * InchesToM);
                            positions[i * 3 + 1] = (float)(pt.Z * InchesToM);
                            positions[i * 3 + 2] = (float)(-pt.Y * InchesToM);

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

                            double m0 = currentMatrix.Count > 0 ? currentMatrix[0] : 1, m1 = currentMatrix.Count > 1 ? currentMatrix[1] : 0, m2 = currentMatrix.Count > 2 ? currentMatrix[2] : 0;
                            double m3 = currentMatrix.Count > 3 ? currentMatrix[3] : 0, m4 = currentMatrix.Count > 4 ? currentMatrix[4] : 1, m5 = currentMatrix.Count > 5 ? currentMatrix[5] : 0;
                            double m6 = currentMatrix.Count > 6 ? currentMatrix[6] : 0, m7 = currentMatrix.Count > 7 ? currentMatrix[7] : 0, m8 = currentMatrix.Count > 8 ? currentMatrix[8] : 1;

                            double nx = m0 * nx0 + m1 * ny0 + m2 * nz0;
                            double ny = m3 * nx0 + m4 * ny0 + m5 * nz0;
                            double nz = m6 * nx0 + m7 * ny0 + m8 * nz0;
                            double length = Math.Sqrt(nx * nx + ny * ny + nz * nz);
                            if (length > 1e-6)
                            {
                                normals[i * 3] = (float)(nx / length);
                                normals[i * 3 + 1] = (float)(nz / length);
                                normals[i * 3 + 2] = (float)(-ny / length);
                            }
                            else
                            {
                                normals[i * 3] = 0; normals[i * 3 + 1] = 1; normals[i * 3 + 2] = 0;
                            }
                        }

                        var indices = new uint[group.LocalFaces.Count * 3];
                        for (int i = 0; i < group.LocalFaces.Count; i++)
                        {
                            indices[i * 3] = (uint)group.LocalFaces[i][0];
                            indices[i * 3 + 1] = (uint)group.LocalFaces[i][1];
                            indices[i * 3 + 2] = (uint)group.LocalFaces[i][2];
                        }

                        int materialIndex = GetMaterialIndex(color);
                        glbPrimitives.Add(new GlbPrimitive
                        {
                            Positions = positions,
                            Normals = normals,
                            Indices = indices,
                            MaterialIndex = materialIndex,
                            GeomName = geomName,
                        });
                    }
                }

                var childInstancesInfo = new List<InstanceNode>();
                foreach (var inst in builder.Instances)
                {
                    long? refIdx = inst.RefIdx;
                    var newMatrix = Transforms.MultiplyMatrices(currentMatrix, inst.Matrix);

                    string lName = parentLayer;
                    (int R, int G, int B)? instColor = inheritedColor;
                    var properties = new Dictionary<string, string>();

                    // Layer/material/dynamic-properties resolution mirrors
                    // Geometry.ExtractGeometryFromNodes's D007 handling;
                    // re-derived here (from inst.Children) to match the
                    // Python/TS reference exactly rather than needing a
                    // new field threaded through GeometryBuilderInstance.
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
                        // Dynamic properties (attribute dictionaries under
                        // D007) are not yet ported for .NET; left empty.
                    }

                    string instName = !string.IsNullOrEmpty(inst.Name) ? inst.Name! : $"Component_{refIdx}";
                    string fullPathName = $"{pathName} / {instName}";
                    instanceCounter++;
                    if (instanceCounter % ParseTuning.ProgressInterval == 0)
                    {
                        Observability.Progress(options, "build_scene", instanceCounter, instanceCounter);
                        Observability.Log(options, SkpLogLevel.Debug, $"Processed {instanceCounter} placed instances");
                    }
                    var childNodes = refIdx.HasValue
                        ? Instantiate(refIdx.Value, false, newMatrix, lName, fullPathName, instColor)
                        : new List<InstanceNode>();

                    double itx = newMatrix.Count > 9 ? newMatrix[9] * InchesToMm : 0.0;
                    double ity = newMatrix.Count > 10 ? newMatrix[10] * InchesToMm : 0.0;
                    double itz = newMatrix.Count > 11 ? newMatrix[11] * InchesToMm : 0.0;

                    string childDefName = "";
                    if (refIdx.HasValue && defsDict.TryGetValue(refIdx.Value, out var childDef))
                    {
                        childDefName = childDef.Name ?? "";
                    }

                    var instInfo = new InstanceNode
                    {
                        Name = inst.Name ?? "",
                        DefinitionName = childDefName,
                        Layer = lName,
                        PositionMm = (Math.Round(itx, 2), Math.Round(ity, 2), Math.Round(itz, 2)),
                        Properties = properties,
                        Children = childNodes,
                    };
                    childInstancesInfo.Add(instInfo);

                    string safeChildPath = fullPathName.Replace(" / ", "__").Replace(" ", "_");
                    if (safeChildPath.Length > 80) safeChildPath = safeChildPath.Substring(0, 80);
                    foreach (var meshKv in meshIndex)
                    {
                        if (meshKv.Key.Contains(safeChildPath))
                        {
                            meshKv.Value.Properties = properties;
                            meshKv.Value.Name = inst.Name ?? "";
                        }
                    }
                }

                return childInstancesInfo;
            }

            var identityMat = new List<double> { 1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1.0 };
            var rootChildren = InstantiateRoot(parsed.Root.Builder, identityMat);

            foreach (var meshKv2 in meshIndex)
            {
                var existing = meshKv2.Value;
                if (existing.Path == "ROOT")
                {
                    existing.Name = "ROOT";
                    existing.DefinitionName = "ROOT_MODEL";
                    existing.Layer = "Layer0";
                    existing.PositionMm = (0, 0, 0);
                    existing.Properties = new Dictionary<string, string>();
                }
            }

            var sceneHierarchy = new InstanceNode
            {
                Name = "ROOT",
                DefinitionName = "ROOT_MODEL",
                Layer = "Layer0",
                PositionMm = (0, 0, 0),
                Properties = new Dictionary<string, string>(),
                Children = rootChildren,
            };

            Observability.Log(
                options, SkpLogLevel.Information,
                $"Scene build complete: {instanceCounter} instances, {meshIndex.Count} meshes, " +
                $"{glbPrimitives.Count} primitives ({sw.Elapsed.TotalSeconds:F2}s)");

            return new Scene
            {
                SceneHierarchy = sceneHierarchy,
                MeshIndex = meshIndex,
                GlbPrimitives = glbPrimitives,
                GltfMaterials = gltfMaterials,
            };
        }

        private static List<long> ReconstructLoopVertices(List<(long EdgeId, long Orientation)> loop, Dictionary<long, (long? V1, long? V2)> edges)
        {
            var loopVerts = new List<long>();
            foreach (var (edgeId, orient) in loop)
            {
                if (edges.TryGetValue(edgeId, out var ends))
                {
                    long? vStart = orient == 1 ? ends.V1 : ends.V2;
                    if (vStart.HasValue && (loopVerts.Count == 0 || loopVerts[loopVerts.Count - 1] != vStart.Value))
                    {
                        loopVerts.Add(vStart.Value);
                    }
                }
            }
            if (loopVerts.Count > 1 && loopVerts[0] == loopVerts[loopVerts.Count - 1])
            {
                loopVerts.RemoveAt(loopVerts.Count - 1);
            }
            return loopVerts;
        }
    }
}
