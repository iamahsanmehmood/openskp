using System;
using System.Collections.Generic;
using System.Runtime.CompilerServices;

namespace OpenSkp
{
    /// <summary>Local-space face grouping, shared by the baked
    /// (<see cref="SceneBuilder"/>) and instanced
    /// (<see cref="InstancedSceneBuilder"/>) scene builders.
    ///
    /// Extracted from SceneBuilder unchanged (openskp#200, mirroring
    /// TypeScript's face-groups.ts): a definition's faces are grouped by
    /// resolved (color, doubleSided, texture) identity in
    /// DEFINITION-LOCAL space (inches, SketchUp Z-up) - exactly what the
    /// baked builder assembles just before applying an instance's world
    /// matrix, and exactly what the instanced builder keeps local and puts
    /// on the node instead. Keeping one implementation is what makes the
    /// two paths agree on triangulation, UV seams, normals and front/back
    /// handling by construction rather than by parallel maintenance.
    ///
    /// Faithful to the pre-existing baked behavior it was extracted from:
    /// an unpainted face falls back to the caller-supplied FallbackColor
    /// for color, but its material (and therefore texture tile size) is
    /// resolved from the face's OWN MaterialId/BackMaterialId only - an
    /// instance's painted material is not consulted for texture purposes
    /// here. That is an existing characteristic of this port (TypeScript's
    /// reference additionally falls back to the inherited material itself
    /// for texture tile size on unpainted faces), preserved rather than
    /// changed by this extraction.</summary>
    internal static class FaceGroups
    {
        /// <summary>The resolved material's overall opacity: 1.0 fully
        /// opaque, 0.0 fully invisible. Two independent SketchUp mechanisms
        /// can reduce it - the plain RGBA color record's alpha byte, and
        /// the newer XML material definition's own trans/useTrans
        /// attribute (already resolved into RawMaterial.Transparency). A
        /// real material only ever populates one of the two, but
        /// multiplying both is safe either way: the untouched one defaults
        /// to fully-opaque (255 or 1.0), so it never silently darkens a
        /// material that only used the other mechanism.</summary>
        public static double ResolveTransparency(Geometry.RawMaterial? mat) =>
            mat == null ? 1.0 : (mat.A / 255.0) * mat.Transparency;

        public sealed class FaceGroup
        {
            public (int R, int G, int B) Color;
            public double Transparency = 1.0;
            public bool DoubleSided;
            public int? TextureIndex;
            public bool UnpaintedBack;
            public bool Colorized;
            public int ColorizeType;
            public List<(double X, double Y, double Z)> LocalVerts = new List<(double, double, double)>();
            public List<(double U, double V)> LocalUvs = new List<(double, double)>();
            public List<double[]> NormalsAccum = new List<double[]>();
            public List<long[]> LocalFaces = new List<long[]>();
            public List<uint> BackTriangles = new List<uint>();
            public Dictionary<(long VId, double U, double V), int> LocalVMap = new Dictionary<(long, double, double), int>();
        }

        /// <summary>Everything BuildLocalFaceGroups needs from its caller
        /// that isn't the builder itself.</summary>
        public sealed class Context
        {
            public Func<long?, (Geometry.RawMaterial? Mat, (int R, int G, int B)? Color)> ResolveMaterial = _ => (null, null);
            public Func<Geometry.RawTexture?, string?, int?> TextureIndexFor = (_, _) => null;

            /// <summary>Color an unpainted face side falls back to (already
            /// resolved by the caller: the instance's inherited paint
            /// color, or, when nothing is inherited, the model's front face
            /// color or the layer's color).</summary>
            public (int R, int G, int B) FallbackColor;

            /// <summary>Same as <see cref="FallbackColor"/> for a face's back
            /// side when it differs (the model's back face color). <c>null</c>
            /// means <see cref="FallbackColor"/>.</summary>
            public (int R, int G, int B)? FallbackBackColor;

            /// <summary>Identifies the definition in a triangulation
            /// failure.</summary>
            public long? DefinitionId;
        }

        public static ((int R, int G, int B) Front, (int R, int G, int B) Back)? UnpaintedStyleColors(
            Core.RawParsed parsed, string? unpaintedFaces)
        {
            string mode = unpaintedFaces ?? "style";
            if (mode != "style" && mode != "tag")
            {
                throw new ArgumentException(
                    $"UnpaintedFaces must be \"style\" or \"tag\", not \"{mode}\".",
                    nameof(unpaintedFaces));
            }
            return mode == "style" ? parsed.FaceColors : null;
        }

        internal static string? SniffImageMime(byte[] data)
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

        private static bool AsciiAt(byte[] data, int offset, string text)
        {
            if (offset + text.Length > data.Length) return false;
            for (int i = 0; i < text.Length; i++)
            {
                if (data[offset + i] != (byte)text[i]) return false;
            }
            return true;
        }

        internal static string DescribeImageFormat(byte[] data, string filename = "")
        {
            if (data.Length >= 4 &&
                ((data[0] == 0x49 && data[1] == 0x49 && data[2] == 0x2A && data[3] == 0x00) ||
                 (data[0] == 0x4D && data[1] == 0x4D && data[2] == 0x00 && data[3] == 0x2A)))
            {
                return "TIFF";
            }
            if (AsciiAt(data, 0, "BM")) return "BMP";
            if (AsciiAt(data, 0, "GIF87a") || AsciiAt(data, 0, "GIF89a")) return "GIF";
            if (AsciiAt(data, 0, "RIFF") && AsciiAt(data, 8, "WEBP")) return "WebP";
            if (AsciiAt(data, 0, "8BPS")) return "PSD";
            if (AsciiAt(data, 0, "DDS ")) return "DDS";
            if (data.Length >= 8 &&
                data[0] == 0x00 && data[1] == 0x00 && data[2] == 0x00 && data[3] == 0x0C &&
                AsciiAt(data, 4, "jP  "))
            {
                return "JPEG 2000";
            }
            int tailStart = Math.Max(0, data.Length - 26);
            for (int i = tailStart; i + 16 <= data.Length; i++)
            {
                if (AsciiAt(data, i, "TRUEVISION-XFILE")) return "TGA";
            }
            string normalized = (filename ?? "").Replace('\\', '/');
            int slash = normalized.LastIndexOf('/');
            string baseName = slash >= 0 ? normalized.Substring(slash + 1) : normalized;
            int dot = baseName.LastIndexOf('.');
            string ext = dot >= 0 ? baseName.Substring(dot + 1) : "";
            return ext.Length > 0 && ext.Length <= 5 ? ext.ToUpperInvariant() : "an unrecognized format";
        }

        internal static string? OriginalImageMime(byte[] data)
        {
            switch (DescribeImageFormat(data))
            {
                case "TIFF": return "image/tiff";
                case "GIF": return "image/gif";
                case "BMP": return "image/bmp";
                case "TGA": return "image/x-tga";
                case "WebP": return "image/webp";
                case "PSD": return "image/vnd.adobe.photoshop";
                case "DDS": return "image/vnd.ms-dds";
                case "JPEG 2000": return "image/jp2";
                default: return null;
            }
        }

        internal static void WarnSkippedTexture(
            SkpParseOptions? options,
            HashSet<(string MaterialName, string Format)> warned,
            string? materialName,
            Geometry.RawTexture tex,
            byte[] data)
        {
            string filename = tex.Filename ?? "";
            string fmt = DescribeImageFormat(data, filename);
            if (!warned.Add((materialName ?? "", fmt))) return;
            string normalized = filename.Replace('\\', '/');
            int slash = normalized.LastIndexOf('/');
            string baseName = slash >= 0 ? normalized.Substring(slash + 1) : normalized;
            bool keepable = OriginalImageMime(data) != null;
            string label = !string.IsNullOrEmpty(materialName) ? $"'{materialName}'" : "'(unnamed)'";
            string suffix = baseName.Length > 0 ? $" ({baseName})" : "";
            string hint = keepable ? " (pass OriginalTextures = true to keep it in its own format)" : "";
            Observability.Log(
                options,
                SkpLogLevel.Warning,
                $"Texture of material {label}{suffix} is {fmt}, which a scene cannot carry (only PNG and JPEG): " +
                $"the material is built without its texture{hint}");
        }

        private sealed class ByteArrayIdentityComparer : IEqualityComparer<byte[]>
        {
            public static readonly ByteArrayIdentityComparer Instance = new ByteArrayIdentityComparer();
            public bool Equals(byte[]? x, byte[]? y) => ReferenceEquals(x, y);
            public int GetHashCode(byte[] obj) => RuntimeHelpers.GetHashCode(obj);
        }

        private sealed class ByteArrayContentComparer : IEqualityComparer<byte[]>
        {
            public static readonly ByteArrayContentComparer Instance = new ByteArrayContentComparer();

            public bool Equals(byte[]? x, byte[]? y)
            {
                if (ReferenceEquals(x, y)) return true;
                if (x == null || y == null || x.Length != y.Length) return false;
                for (int i = 0; i < x.Length; i++)
                {
                    if (x[i] != y[i]) return false;
                }
                return true;
            }

            public int GetHashCode(byte[] obj)
            {
                unchecked
                {
                    uint h = 2166136261u;
                    for (int i = 0; i < obj.Length; i++)
                    {
                        h = (h ^ obj[i]) * 16777619u;
                    }
                    return (int)h;
                }
            }
        }

        internal static Func<Geometry.RawTexture?, string?, int?> CreateTextureIndexFor(
            List<SceneTexture> textures,
            SkpParseOptions? options)
        {
            var indexByIdentity = new Dictionary<byte[], int>(ByteArrayIdentityComparer.Instance);
            var indexByContent = new Dictionary<byte[], int>(ByteArrayContentComparer.Instance);
            var skippedTextures = new HashSet<(string, string)>();

            return (Geometry.RawTexture? tex, string? materialName) =>
            {
                if (tex?.Data == null || tex.Data.Length == 0) return null;
                var data = tex.Data;
                if (indexByIdentity.TryGetValue(data, out var cached)) return cached;

                var mimeType = SniffImageMime(data);
                if (mimeType == null && options?.OriginalTextures == true)
                {
                    mimeType = OriginalImageMime(data);
                }
                if (mimeType == null)
                {
                    WarnSkippedTexture(options, skippedTextures, materialName, tex, data);
                    return null;
                }

                if (!indexByContent.TryGetValue(data, out var idx))
                {
                    idx = textures.Count;
                    textures.Add(new SceneTexture { Data = data, MimeType = mimeType, Filename = tex.Filename });
                    indexByContent[data] = idx;
                }
                indexByIdentity[data] = idx;
                return idx;
            };
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

        internal const double UvVerticalTolerance = 1e-3;

        /// <summary>Face-plane basis vectors (xr, yr) for UV projection,
        /// from a face normal. See Face.UvTransform's docs for the recipe
        /// this implements. Internal (not private) so Create.cs, Edit.cs
        /// and Codegen.cs reuse the identical basis.</summary>
        internal static ((double X, double Y, double Z) Xr, (double X, double Y, double Z) Yr) FaceUvBasis((double X, double Y, double Z) n)
        {
            double cx = -n.Y, cy = n.X;
            double clen = Math.Sqrt(cx * cx + cy * cy);
            (double, double, double) xr, yr;
            if (clen < UvVerticalTolerance)
            {
                xr = (n.Z >= 0 ? 1.0 : -1.0, 0.0, 0.0);
                yr = (0.0, 1.0, 0.0);
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

        /// <summary>Group a definition's faces by resolved material
        /// identity, in local space.
        ///
        /// A face whose front/back resolve to the SAME color is emitted
        /// once with DoubleSided set; a face whose sides genuinely differ
        /// is emitted as two single-sided triangle sets (one normal-wound
        /// front, one reverse-wound back) so each side keeps its own
        /// color.</summary>
        public static Dictionary<((int R, int G, int B) Color, bool DoubleSided, int? TextureIndex, double Transparency, bool UnpaintedBack, bool Colorized, int ColorizeType), FaceGroup> BuildLocalFaceGroups(GeometryBuilder builder, Context ctx)
        {
            var faceGroups = new Dictionary<((int R, int G, int B) Color, bool DoubleSided, int? TextureIndex, double Transparency, bool UnpaintedBack, bool Colorized, int ColorizeType), FaceGroup>();
            var fallbackBackColor = ctx.FallbackBackColor ?? ctx.FallbackColor;

            void AddSide(
                List<long[]> triangles, (double X, double Y, double Z) fn,
                (int R, int G, int B) color, bool doubleSided, bool reverse,
                Geometry.RawMaterial? mat, double[]? uvTransform,
                (double X, double Y, double Z) xr, (double X, double Y, double Z) yr,
                bool unpaintedBack = false)
            {
                // faces are batched per emitted material, so the texture
                // has to be part of the key too - otherwise two
                // differently-textured faces with the same average color
                // end up in one group with one image
                int? texIndex = ctx.TextureIndexFor(mat?.Texture, mat?.Name);
                bool colorized = mat != null && texIndex.HasValue && mat.Colorized;
                int colorizeType = colorized ? mat!.ColorizeType : 0;
                double transparency = ResolveTransparency(mat);
                var key = (color, doubleSided, texIndex, transparency, unpaintedBack, colorized, colorizeType);
                if (!faceGroups.TryGetValue(key, out var group))
                {
                    group = new FaceGroup
                    {
                        Color = color,
                        Transparency = transparency,
                        DoubleSided = doubleSided,
                        TextureIndex = texIndex,
                        UnpaintedBack = unpaintedBack,
                        Colorized = colorized,
                        ColorizeType = colorizeType,
                    };
                    faceGroups[key] = group;
                }

                double? texW = mat?.Texture?.XScale;
                double? texH = mat?.Texture?.YScale;
                double tileW = (texW.HasValue && texW.Value > 1e-9) ? texW.Value : 1.0;
                double tileH = (texH.HasValue && texH.Value > 1e-9) ? texH.Value : 1.0;
                (double X, double Y, double Z) sideNormal = reverse ? (-fn.X, -fn.Y, -fn.Z) : fn;

                // Vertices are deduped per (vId, uv) rather than just vId:
                // UVs are inherently per-face, so a vertex position shared
                // by two faces that disagree on texture mapping must
                // become two distinct output vertices (glTF requires
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
                        if (reverse)
                        {
                            group.BackTriangles.Add((uint)group.LocalFaces.Count);
                        }
                        group.LocalFaces.Add(new long[] { faceIndices[0], faceIndices[1], faceIndices[2] });
                    }
                }
            }

            foreach (var faceKv in builder.Faces)
            {
                var fData = faceKv.Value;

                var (frontMat, frontMatColor) = ctx.ResolveMaterial(fData.MaterialId);
                var (backMat, backMatColor) = ctx.ResolveMaterial(fData.BackMaterialId);
                var frontColor = frontMatColor ?? ctx.FallbackColor;
                var backColor = backMatColor ?? fallbackBackColor;

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
                        stage: "build_scene", definitionId: ctx.DefinitionId, innerException: e);
                }
                var fn = fData.Normal;
                var (xr, yr) = FaceUvBasis(fn);
                var uvTransform = fData.UvTransform;
                var uvTransformBack = fData.UvTransformBack;

                bool frontCol = frontMat?.Colorized == true;
                bool backCol = backMat?.Colorized == true;
                int frontCType = frontCol ? frontMat!.ColorizeType : 0;
                int backCType = backCol ? backMat!.ColorizeType : 0;
                bool sameColorization = (frontMat != null && backMat != null)
                    ? (frontCol == backCol && frontCType == backCType)
                    : true;
                bool sameColor = frontColor.R == backColor.R && frontColor.G == backColor.G && frontColor.B == backColor.B && sameColorization;
                if (sameColor)
                {
                    AddSide(triangles, fn, frontColor, true, false, frontMat, uvTransform, xr, yr);
                }
                else
                {
                    AddSide(triangles, fn, frontColor, false, false, frontMat, uvTransform, xr, yr);
                    AddSide(triangles, fn, backColor, false, true, backMat, uvTransformBack, xr, yr,
                        unpaintedBack: frontMat == null && backMat == null);
                }
            }

            return faceGroups;
        }
    }
}
