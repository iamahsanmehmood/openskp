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

        /// <summary>Flat [u, v, u, v, ...] texture coordinates, matching
        /// Positions 1:1. Computed from each source face's UvTransform (or
        /// the default face-plane projection when a face has none) - see
        /// Face.UvTransform's docs for the formula. A vertex shared by two
        /// faces that disagree on UV is split, since indexed glTF meshes
        /// need position/normal/uv aligned per vertex. Faces with
        /// UvProjected set (terrain drape textures) still use the
        /// face-plane formula here, since the real projection-plane basis
        /// isn't captured in the parsed data - their UVs will be
        /// approximate.</summary>
        public float[] Uvs { get; set; } = Array.Empty<float>();

        /// <summary>Triangle vertex indices into Positions/Normals/Uvs (3
        /// per triangle).</summary>
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
            public bool DoubleSided;
            public List<(double X, double Y, double Z)> LocalVerts = new List<(double, double, double)>();
            public List<(double U, double V)> LocalUvs = new List<(double, double)>();
            public List<double[]> NormalsAccum = new List<double[]>();
            public List<long[]> LocalFaces = new List<long[]>();
            public Dictionary<(long VId, double U, double V), int> LocalVMap = new Dictionary<(long, double, double), int>();
        }

        /// <summary>Inverse of a row-major 3x3 matrix, via the
        /// cofactor/adjugate method.</summary>
        private static double[] InvertMatrix3x3(double[] m)
        {
            double a = m[0], b = m[1], c = m[2];
            double d = m[3], e = m[4], f = m[5];
            double g = m[6], h = m[7], i = m[8];
            double det = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g);
            if (Math.Abs(det) < 1e-12)
            {
                return new double[] { 1, 0, 0, 0, 1, 0, 0, 0, 1 };
            }
            double invDet = 1.0 / det;
            return new double[]
            {
                (e * i - f * h) * invDet, (c * h - b * i) * invDet, (b * f - c * e) * invDet,
                (f * g - d * i) * invDet, (a * i - c * g) * invDet, (c * d - a * f) * invDet,
                (d * h - e * g) * invDet, (b * g - a * h) * invDet, (a * e - b * d) * invDet,
            };
        }

        /// <summary>Face-plane basis vectors (xr, yr) for UV projection,
        /// from a face normal. See Face.UvTransform's docs for the recipe
        /// this implements.</summary>
        /// <summary>Internal (not private) so Edit.cs can reuse this same
        /// read-side UV basis/sampling when replaying a source face's
        /// stored uv_transform into the 3-point correspondences
        /// SkpBuilder.AddFace's front_uv/back_uv expects.</summary>
        internal static ((double X, double Y, double Z) Xr, (double X, double Y, double Z) Yr) FaceUvBasis((double X, double Y, double Z) n)
        {
            double cx = -n.Y, cy = n.X;
            double clen = Math.Sqrt(cx * cx + cy * cy);
            (double, double, double) xr, yr;
            if (clen < 1e-9)
            {
                xr = (1.0, 0.0, 0.0);
                yr = (0.0, n.Z >= 0 ? 1.0 : -1.0, 0.0);
            }
            else
            {
                xr = (cx / clen, cy / clen, 0.0);
                yr = (
                    n.Y * xr.Item3 - n.Z * xr.Item2,
                    n.Z * xr.Item1 - n.X * xr.Item3,
                    n.X * xr.Item2 - n.Y * xr.Item1
                );
            }
            return (xr, yr);
        }

        /// <summary>UV of point p (inches, local/object space) on a face
        /// with the given plane basis, per-face uvTransform (or null for
        /// the default projection), and material tile size (inches).</summary>
        internal static (double U, double V) ComputeFaceUv(
            (double X, double Y, double Z) p,
            (double X, double Y, double Z) xr,
            (double X, double Y, double Z) yr,
            double[]? uvTransform,
            double tileW, double tileH)
        {
            double px = p.X * xr.X + p.Y * xr.Y + p.Z * xr.Z;
            double py = p.X * yr.X + p.Y * yr.Y + p.Z * yr.Z;
            if (uvTransform == null)
            {
                return (px / tileW, py / tileH);
            }
            var inv = InvertMatrix3x3(uvTransform);
            double u = px * inv[0] + py * inv[3] + inv[6];
            double v = px * inv[1] + py * inv[4] + inv[7];
            double q = px * inv[2] + py * inv[5] + inv[8];
            if (Math.Abs(q) < 1e-12) q = 1.0;
            return (u / q / tileW, v / q / tileH);
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

            // Instance path -> (Properties, Name) updates, collected in O(1) per
            // instance and applied once after instantiation (see the prefix-walk
            // loop below). Replaces the previous per-instance full meshIndex
            // scan, which was O(instances x meshes) and dominated BuildScene on
            // models with tens or hundreds of thousands of placed instances.
            var pathUpdates = new Dictionary<string, (Dictionary<string, string> Props, string Name)>();

            var colorToMaterialIndex = new Dictionary<((int, int, int) Color, bool DoubleSided), int>();
            var gltfMaterials = new List<object>();

            // Definitions currently being instantiated on the active
            // recursion path (not "ever visited" - the same definition
            // legitimately reused by sibling instances is fine). Guards
            // against a component that directly or transitively instances
            // itself, which would otherwise recurse until the stack
            // overflows.
            var activeDefinitions = new HashSet<long>();

            (int R, int G, int B) GetLayerColor(string name)
            {
                return layerColors.TryGetValue(name, out var c) ? c : (136, 136, 136);
            }

            int GetMaterialIndex((int R, int G, int B) color, bool doubleSided)
            {
                var key = (color, doubleSided);
                if (colorToMaterialIndex.TryGetValue(key, out var existing)) return existing;
                int idx = gltfMaterials.Count;
                var material = new Dictionary<string, object>
                {
                    ["pbrMetallicRoughness"] = new
                    {
                        baseColorFactor = new[] { color.R / 255.0, color.G / 255.0, color.B / 255.0, 1.0 },
                        metallicFactor = 0.0,
                        roughnessFactor = 0.8,
                    },
                };
                if (doubleSided) material["doubleSided"] = true;
                gltfMaterials.Add(material);
                colorToMaterialIndex[key] = idx;
                return idx;
            }

            (Geometry.RawMaterial? Mat, (int R, int G, int B)? Color) ResolveMaterial(long? matId) =>
                ResolveMaterialFromDicts(matId, materialIdToName, materials, materialsByFolder);

            List<InstanceNode> Instantiate(
                long defId, bool isRoot, List<double> currentMatrix,
                string parentLayer, string pathName, (int R, int G, int B)? inheritedColor)
            {
                if (!defsDict.TryGetValue(defId, out var d))
                {
                    return new List<InstanceNode>();
                }
                if (!activeDefinitions.Add(defId))
                {
                    throw new SkpParseException(
                        "Recursive component definition",
                        stage: "build_scene", definitionId: defId);
                }
                try
                {
                    return InstantiateBuilder(d.Builder, d.Name ?? "", defId, currentMatrix, parentLayer, pathName, inheritedColor);
                }
                finally
                {
                    activeDefinitions.Remove(defId);
                }
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
                    var faceGroups = new Dictionary<((int R, int G, int B) Color, bool DoubleSided), FaceGroup>();

                    void AddSide(
                        List<long[]> triangles, (double X, double Y, double Z) fn,
                        (int R, int G, int B) color, bool doubleSided, bool reverse,
                        Geometry.RawMaterial? mat, double[]? uvTransform,
                        (double X, double Y, double Z) xr, (double X, double Y, double Z) yr)
                    {
                        var key = (color, doubleSided);
                        if (!faceGroups.TryGetValue(key, out var group))
                        {
                            group = new FaceGroup { Color = color, DoubleSided = doubleSided };
                            faceGroups[key] = group;
                        }

                        double? texW = mat?.Texture?.XScale;
                        double? texH = mat?.Texture?.YScale;
                        double tileW = (texW.HasValue && texW.Value > 1e-9) ? texW.Value : 1.0;
                        double tileH = (texH.HasValue && texH.Value > 1e-9) ? texH.Value : 1.0;
                        (double X, double Y, double Z) sideNormal = reverse ? (-fn.X, -fn.Y, -fn.Z) : fn;

                        // Vertices are deduped per (vId, uv) rather than
                        // just vId: UVs are inherently per-face, so a
                        // vertex position shared by two faces that
                        // disagree on texture mapping must become two
                        // distinct output vertices (glTF requires
                        // position/normal/uv aligned per index).
                        var faceLocalMap = new Dictionary<long, int>();
                        foreach (var tri in triangles)
                        {
                            var triIds = reverse ? new long[] { tri[0], tri[2], tri[1] } : tri;
                            var faceIndices = new List<int>();
                            foreach (var vId in triIds)
                            {
                                if (!builder.Vertices.ContainsKey(vId)) continue;
                                if (!faceLocalMap.TryGetValue(vId, out int idx))
                                {
                                    var p = builder.Vertices[vId];
                                    var (u, v) = ComputeFaceUv(p, xr, yr, uvTransform, tileW, tileH);
                                    var vkey = (vId, u, v);
                                    if (!group.LocalVMap.TryGetValue(vkey, out idx))
                                    {
                                        group.LocalVerts.Add(p);
                                        group.LocalUvs.Add((u, v));
                                        group.NormalsAccum.Add(new double[] { sideNormal.X, sideNormal.Y, sideNormal.Z });
                                        idx = group.LocalVerts.Count - 1;
                                        group.LocalVMap[vkey] = idx;
                                    }
                                    else
                                    {
                                        var accum = group.NormalsAccum[idx];
                                        accum[0] += sideNormal.X; accum[1] += sideNormal.Y; accum[2] += sideNormal.Z;
                                    }
                                    faceLocalMap[vId] = idx;
                                }
                                faceIndices.Add(idx);
                            }
                            if (faceIndices.Count == 3)
                            {
                                group.LocalFaces.Add(new long[] { faceIndices[0], faceIndices[1], faceIndices[2] });
                            }
                        }
                    }

                    var fallbackColor = inheritedColor ?? GetLayerColor(parentLayer);

                    foreach (var faceKv in builder.Faces)
                    {
                        long fId = faceKv.Key;
                        var fData = faceKv.Value;

                        var (frontMat, frontMatColor) = ResolveMaterial(fData.MaterialId);
                        var (backMat, backMatColor) = ResolveMaterial(fData.BackMaterialId);
                        var frontColor = frontMatColor ?? fallbackColor;
                        var backColor = backMatColor ?? fallbackColor;

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
                        var fn = fData.Normal;
                        var (xr, yr) = FaceUvBasis(fn);
                        var uvTransform = fData.UvTransform;
                        var uvTransformBack = fData.UvTransformBack;

                        bool sameColor = frontColor.R == backColor.R && frontColor.G == backColor.G && frontColor.B == backColor.B;
                        if (sameColor)
                        {
                            AddSide(triangles, fn, frontColor, true, false, frontMat, uvTransform, xr, yr);
                        }
                        else
                        {
                            AddSide(triangles, fn, frontColor, false, false, frontMat, uvTransform, xr, yr);
                            AddSide(triangles, fn, backColor, false, true, backMat, uvTransformBack, xr, yr);
                        }
                    }

                    bool isRootPath = pathName == "ROOT";
                    bool multiGroup = faceGroups.Count > 1;

                    foreach (var groupKv in faceGroups)
                    {
                        var color = groupKv.Key.Color;
                        var group = groupKv.Value;
                        if (group.LocalFaces.Count == 0) continue;

                        double tx = isRootPath ? 0.0 : (currentMatrix.Count > 9 ? currentMatrix[9] : 0.0) * InchesToMm;
                        double ty = isRootPath ? 0.0 : (currentMatrix.Count > 10 ? currentMatrix[10] : 0.0) * InchesToMm;
                        double tz = isRootPath ? 0.0 : (currentMatrix.Count > 11 ? currentMatrix[11] : 0.0) * InchesToMm;

                        string safePath = pathName.Replace(" / ", "__").Replace(" ", "_");
                        if (safePath.Length > 80) safePath = safePath.Substring(0, 80);
                        string colorSuffix = multiGroup ? $"_{color.Item1}_{color.Item2}_{color.Item3}_{(group.DoubleSided ? "ds" : "ss")}" : "";
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
                        var uvs = new float[vertCount * 2];
                        var vertexNormalsAccum = group.NormalsAccum;

                        for (int i = 0; i < vertCount; i++)
                        {
                            var v = group.LocalVerts[i];
                            var pt = Transforms.TransformPoint(currentMatrix.ToArray(), v);
                            positions[i * 3] = (float)(pt.X * InchesToM);
                            positions[i * 3 + 1] = (float)(pt.Z * InchesToM);
                            positions[i * 3 + 2] = (float)(-pt.Y * InchesToM);

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

                        int materialIndex = GetMaterialIndex(color, group.DoubleSided);
                        glbPrimitives.Add(new GlbPrimitive
                        {
                            Positions = positions,
                            Normals = normals,
                            Uvs = uvs,
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
                    // Legacy (pre-2021 MFC) instances carry a precomputed
                    // Properties dict (see Legacy.ExtractLegacyDynamicProperties)
                    // - VFF instances don't set this, so this stays {} for
                    // them and gets overwritten below via the D007/DC05 TLV
                    // walk instead.
                    var properties = inst.Properties != null
                        ? new Dictionary<string, string>(inst.Properties)
                        : new Dictionary<string, string>();

                    // Layer/material resolution mirrors
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

                    // Record this instance's (Properties, Name) for the deferred
                    // mesh back-fill below. The scan this replaces iterated the
                    // entire meshIndex per placed instance (string Contains per
                    // mesh), i.e. O(instances x meshes); with hundreds of
                    // thousands of both this alone took tens of minutes on real
                    // production files. The final state is identical: an
                    // instance's update is applied to every mesh under its path,
                    // and the outermost (most shallow) ancestor's update wins.
                    pathUpdates[fullPathName] = (properties, inst.Name ?? "");
                }

                return childInstancesInfo;
            }

            var identityMat = new List<double> { 1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1.0 };
            var rootChildren = InstantiateRoot(parsed.Root.Builder, identityMat);

            // Deferred mesh back-fill: for each mesh, walk its Path from the
            // leaf up to the root and apply the shallowest recorded ancestor
            // update. Equivalent to the old scan's final state (the outermost
            // ancestor wrote last and won), O(meshes x path_depth) instead of
            // O(instances x meshes). This also fixes a latent over-match in the
            // old substring test: "ROOT / A_B" would previously match a mesh
            // whose GeomName merely contained the substring "A_B" (e.g. inside
            // "A_BC"), wrongly propagating the update to non-descendants.
            foreach (var meshKv2 in meshIndex)
            {
                var mesh = meshKv2.Value;
                var meshPath = mesh.Path ?? "";
                (Dictionary<string, string> Props, string Name)? found = null;
                string p = meshPath;
                while (p.Length > 0)
                {
                    if (pathUpdates.TryGetValue(p, out var u)) found = u;
                    int sep = p.LastIndexOf(" / ");
                    p = sep >= 0 ? p.Substring(0, sep) : "";
                }
                if (found.HasValue)
                {
                    mesh.Properties = found.Value.Props;
                    mesh.Name = found.Value.Name;
                }
            }

            foreach (var meshKv3 in meshIndex)
            {
                var existing = meshKv3.Value;
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

        /// <summary>Internal (not private) so Edit.cs can reuse this same
        /// loop-walk when replaying a source face's boundary/hole loops
        /// into the point lists SkpBuilder.AddFace expects.</summary>
        internal static List<long> ReconstructLoopVertices(List<(long EdgeId, long Orientation)> loop, Dictionary<long, (long? V1, long? V2)> edges)
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

        private static (Geometry.RawMaterial? Mat, (int R, int G, int B)? Color) ResolveMaterialFromDicts(
            long? matId,
            Dictionary<long, string> materialIdToName,
            Dictionary<string, Geometry.RawMaterial> materials,
            Dictionary<string, Geometry.RawMaterial> materialsByFolder)
        {
            if (matId is long id && materialIdToName.TryGetValue(id, out var matName))
            {
                var m = materials.TryGetValue(matName, out var m1) ? m1
                    : materialsByFolder.TryGetValue(matName, out var m2) ? m2 : null;
                if (m != null) return (m, (m.R, m.G, m.B));
            }
            return (null, null);
        }
    }
}
