using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Text;
using Google.FlatBuffers;
using OpenSkp;
using Fb = openskp._fragments_fb;

namespace OpenSkp.Fragments
{
    /// <summary>Direct SKP -&gt; ThatOpen Fragments (.frag) export - EXPERIMENTAL.
    /// A port of Python's <c>openskp.export.fragments.to_fragments</c> (see
    /// that module's own docstring for the full rationale and real-loader
    /// verification status this port inherits) against the same vendored
    /// ThatOpen Fragments schema (<c>_fragments_fb/index.fbs</c>).
    ///
    /// EXPORT ONLY, matching the TypeScript port (#276) - not Python's or
    /// C++'s read side (TypeScript has no read side either; this is a
    /// smaller, still-open gap, not unique to .NET). GUID/name-is-generated/
    /// layer-hidden fidelity now matches Python's/C++'s in full:
    /// <see cref="InstancedNode"/>/<see cref="InstancedScene"/> carry real
    /// per-instance source GUIDs, a generated-name flag, and the source
    /// file's layer-hidden state (openskp#290's fix, ported here too - see
    /// InstancedScene.cs's own name-resolution and Geometry.cs's
    /// ExtractAttributeDictionaries in the core OpenSkp package).
    ///
    /// Ships as its own package (rather than living in the core OpenSkp
    /// package) because it needs Google.FlatBuffers - the core package has
    /// zero external dependencies today, and this mirrors Python's own
    /// opt-in `pip install openskp[fragments]` extra instead of forcing
    /// every OpenSkp.NET consumer to take a transitive dependency they
    /// don't need.</summary>
    public static class FragmentsExport
    {
        // Fragments' Shell uses `ushort` point indices by default; a shell
        // with more points than this must use the wide BigShell encoding
        // (`uint` indices) instead - confirmed against the real importer's
        // own `points.length > ushortMaxValue` check.
        private const int UshortMax = 65535;

        // Per-axis scale magnitudes within this of 1.0 are treated as
        // exactly unit scale for cache-key rounding purposes - matches
        // typical floating-point accumulation noise from matrix
        // composition, not a meaningful tolerance for an actually-intended
        // resize. Matches Python's own value (C++'s port too); TypeScript
        // independently chose 4, a harmless difference since the rounding
        // only affects shell-dedup cache keys, never the written geometry.
        private const int ScaleRoundNdigits = 6;

        private static readonly double[] IdentityMatrix =
        {
            1.0, 0.0, 0.0, 0.0,
            0.0, 1.0, 0.0, 0.0,
            0.0, 0.0, 1.0, 0.0,
            0.0, 0.0, 0.0, 1.0,
        };

        // Column-major 4x4 multiply, a*b - same convention as
        // InstancedScene.cs's own internal matrix composition.
        private static double[] Mat4Mul(double[] a, double[] b)
        {
            var outM = new double[16];
            for (int col = 0; col < 4; col++)
            {
                for (int row = 0; row < 4; row++)
                {
                    double s = 0.0;
                    for (int k = 0; k < 4; k++)
                        s += a[k * 4 + row] * b[col * 4 + k];
                    outM[col * 4 + row] = s;
                }
            }
            return outM;
        }

        private sealed class Leaf
        {
            public InstancedNode Node = null!;
            public double[] World = IdentityMatrix;
        }

        // Walk the instanced scene's tree, accumulating each node's GLOBAL
        // (world) transform, and return every node worth tracking as its
        // own item - a leaf carrying geometry, OR a real, named
        // organizational wrapper with no geometry of its own (e.g. a
        // SketchUp group like "W-2" that only exists to hold several
        // separately-meshed parts). Mirrors Python's/C++'s own
        // collect_leaves exactly, using the real NameIsGenerated flag - see
        // Python's docstring for the full rationale.
        private static void CollectLeaves(InstancedNode node, InstancedNode root, double[] parentMatrix, List<Leaf> outLeaves)
        {
            var world = Mat4Mul(parentMatrix, node.Matrix);
            bool isNamedWrapper = !ReferenceEquals(node, root) && !node.NameIsGenerated;
            if (node.MeshResourceId != null || isNamedWrapper)
            {
                outLeaves.Add(new Leaf { Node = node, World = world });
            }
            foreach (var child in node.Children)
            {
                CollectLeaves(child, root, world, outLeaves);
            }
        }

        /// <summary>Decompose a column-major 4x4 instance transform into
        /// (position, x_direction, y_direction, scale, mirrored) - a direct
        /// port of Python's <c>_decompose_trs</c>. See that function's own
        /// docstring for why mirroring is resolved this way.</summary>
        public readonly struct Trs
        {
            public readonly double[] Position;
            public readonly double[] XDir;
            public readonly double[] YDir;
            public readonly double[] Scale;
            public readonly bool Mirrored;

            public Trs(double[] position, double[] xDir, double[] yDir, double[] scale, bool mirrored)
            {
                Position = position;
                XDir = xDir;
                YDir = yDir;
                Scale = scale;
                Mirrored = mirrored;
            }
        }

        public static Trs DecomposeTrs(double[] m)
        {
            var xAxis = new[] { m[0], m[1], m[2] };
            var yAxis = new[] { m[4], m[5], m[6] };
            var zAxis = new[] { m[8], m[9], m[10] };
            var pos = new[] { m[12], m[13], m[14] };

            double Norm(double[] v)
            {
                var s = Math.Sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2]);
                return s > 0.0 ? s : 1.0;
            }
            double sx = Norm(xAxis), sy = Norm(yAxis), sz = Norm(zAxis);

            double det = xAxis[0] * (yAxis[1] * zAxis[2] - yAxis[2] * zAxis[1])
                       - xAxis[1] * (yAxis[0] * zAxis[2] - yAxis[2] * zAxis[0])
                       + xAxis[2] * (yAxis[0] * zAxis[1] - yAxis[1] * zAxis[0]);
            bool mirrored = det < 0.0;

            var xDir = new[] { xAxis[0] / sx, xAxis[1] / sx, xAxis[2] / sx };
            if (mirrored)
            {
                xDir[0] = -xDir[0];
                xDir[1] = -xDir[1];
                xDir[2] = -xDir[2];
            }
            var yDir = new[] { yAxis[0] / sy, yAxis[1] / sy, yAxis[2] / sy };

            return new Trs(pos, xDir, yDir, new[] { sx, sy, sz }, mirrored);
        }

        public readonly struct BakedGeometry
        {
            public readonly List<float[]> Points;
            public readonly List<uint[]> Triangles;

            public BakedGeometry(List<float[]> points, List<uint[]> triangles)
            {
                Points = points;
                Triangles = triangles;
            }
        }

        /// <summary>Apply an instance's scale/mirror directly to a copy of
        /// its resource's LOCAL points and triangle winding, so the
        /// resulting geometry is correct when placed by a purely rigid
        /// (rotation + translation, no scale) Transform - a direct port of
        /// Python's <c>_bake_primitive</c>.</summary>
        public static BakedGeometry BakePrimitive(LocalPrimitive prim, double[] scale, bool mirrored)
        {
            double sx = mirrored ? -scale[0] : scale[0];
            double sy = scale[1], sz = scale[2];

            int nVerts = prim.Positions.Length / 3;
            var points = new List<float[]>(nVerts);
            for (int i = 0; i < nVerts; i++)
            {
                points.Add(new[]
                {
                    (float)(prim.Positions[i * 3] * sx),
                    (float)(prim.Positions[i * 3 + 1] * sy),
                    (float)(prim.Positions[i * 3 + 2] * sz),
                });
            }

            int nTris = prim.Indices.Length / 3;
            var triangles = new List<uint[]>(nTris);
            for (int i = 0; i < nTris; i++)
            {
                var tri = new[] { prim.Indices[i * 3], prim.Indices[i * 3 + 1], prim.Indices[i * 3 + 2] };
                if (mirrored)
                {
                    (tri[1], tri[2]) = (tri[2], tri[1]);
                }
                triangles.Add(tri);
            }
            return new BakedGeometry(points, triangles);
        }

        /// <summary>Round a (mirrored, scale) triple to a stable cache key -
        /// the mirror flag folds into the X component's sign, since
        /// <see cref="BakePrimitive"/> only ever negates X for a mirrored
        /// instance. Matches Python's <c>_scale_cache_key</c>.</summary>
        public static (double, double, double) ScaleCacheKey(bool mirrored, double[] scale)
        {
            double mult = Math.Pow(10, ScaleRoundNdigits);
            double Rnd(double v) => Math.Round(v * mult) / mult;
            double sx = mirrored ? -scale[0] : scale[0];
            return (Rnd(sx), Rnd(scale[1]), Rnd(scale[2]));
        }

        private static byte ClampByte(double c) => (byte)Math.Min(255, Math.Max(0, (int)Math.Round(c * 255)));

        // scene.GltfMaterials is publicly just List<object> (built as
        // Dictionary<string,object> - see InstancedScene.cs's own
        // GetMaterialIndex) so a hand-built scene using some other object
        // shape doesn't crash export, just falls back to opaque white,
        // same courtesy Glb.cs's own StripTextureRefs extends elsewhere in
        // this project.
        private static (byte R, byte G, byte B, byte A) ExtractBaseColor(object? gltfMat)
        {
            if (gltfMat is IDictionary<string, object> dict &&
                dict.TryGetValue("pbrMetallicRoughness", out var pbrObj) &&
                pbrObj is IDictionary<string, object> pbr &&
                pbr.TryGetValue("baseColorFactor", out var bcfObj))
            {
                double[]? bcf = bcfObj switch
                {
                    double[] da => da,
                    float[] fa => Array.ConvertAll(fa, x => (double)x),
                    IEnumerable<object> en => en.Select(Convert.ToDouble).ToArray(),
                    _ => null,
                };
                if (bcf != null && bcf.Length >= 3)
                {
                    return (
                        ClampByte(bcf[0]),
                        ClampByte(bcf[1]),
                        ClampByte(bcf[2]),
                        bcf.Length > 3 ? ClampByte(bcf[3]) : (byte)255);
                }
            }
            return (255, 255, 255, 255);
        }

        // Minimal JSON string building for this module's own two small,
        // fixed shapes (the Model.metadata sidecar object and each
        // Attribute's `["Name", value, "STRING"]` triple) - not a general
        // JSON writer. Glb.cs's own MiniJson does the same job for the rest
        // of this project but is `internal` to the core OpenSkp assembly,
        // unreachable from this separate package.
        private static string JsonEscape(string s)
        {
            var sb = new StringBuilder(s.Length + 2);
            foreach (var c in s)
            {
                switch (c)
                {
                    case '"': sb.Append("\\\""); break;
                    case '\\': sb.Append("\\\\"); break;
                    case '\n': sb.Append("\\n"); break;
                    case '\r': sb.Append("\\r"); break;
                    case '\t': sb.Append("\\t"); break;
                    default:
                        if (c < 0x20) sb.Append("\\u").Append(((int)c).ToString("x4"));
                        else sb.Append(c);
                        break;
                }
            }
            return sb.ToString();
        }

        private static string JsonString(string s) => "\"" + JsonEscape(s) + "\"";

        private static string NameAttributeJson(string name) =>
            "[\"Name\"," + JsonString(name) + ",\"STRING\"]";

        // The source file's own per-layer visibility has no equivalent
        // field anywhere in the Fragments schema itself - visibility is a
        // runtime/viewer concern there, not a persisted one. metadata is
        // the schema's own general-purpose "JSON string for generic data
        // about the file" field - exactly the right place for a consuming
        // viewer to recover this and apply it on load. Matches Python's/
        // C++'s own metadata shape exactly.
        private static string MetadataJson(Dictionary<string, bool> layerHidden, List<string> generatedNameGuids)
        {
            var layerHiddenJson = string.Join(",", layerHidden.Select(kv =>
                JsonString(kv.Key) + ":" + (kv.Value ? "true" : "false")));
            var generatedGuidsJson = string.Join(",", generatedNameGuids.Select(JsonString));
            return "{\"layer_hidden\":{" + layerHiddenJson + "},\"generated_name_guids\":[" + generatedGuidsJson + "]}";
        }

        // Minimal recursive-descent JSON reader for the read side
        // (FromFragments) - the mirror of JsonEscape/JsonString/
        // MetadataJson above, needed because those only write JSON and
        // this package deliberately takes on no JSON library dependency
        // (see the class-level doc comment on why - Google.FlatBuffers is
        // this package's only one). Handles the full JSON value grammar
        // (null/bool/number/string/array/object) since a real .frag file's
        // Attribute.Data/Metadata strings aren't guaranteed to come from
        // this project's own writer - any real @thatopen/fragments-derived
        // file (e.g. a genuine IfcImporter export) is a valid input too.
        private static class MinimalJson
        {
            public static object? Parse(string s)
            {
                int i = 0;
                var value = ParseValue(s, ref i);
                return value;
            }

            private static void SkipWhitespace(string s, ref int i)
            {
                while (i < s.Length && char.IsWhiteSpace(s[i])) i++;
            }

            private static object? ParseValue(string s, ref int i)
            {
                SkipWhitespace(s, ref i);
                if (i >= s.Length) throw new FormatException("unexpected end of JSON");
                char c = s[i];
                if (c == '"') return ParseString(s, ref i);
                if (c == '{') return ParseObject(s, ref i);
                if (c == '[') return ParseArray(s, ref i);
                if (c == 't' && s.Substring(i, 4) == "true") { i += 4; return true; }
                if (c == 'f' && s.Substring(i, 5) == "false") { i += 5; return false; }
                if (c == 'n' && s.Substring(i, 4) == "null") { i += 4; return null; }
                return ParseNumber(s, ref i);
            }

            private static string ParseString(string s, ref int i)
            {
                i++; // opening quote
                var sb = new StringBuilder();
                while (s[i] != '"')
                {
                    char c = s[i];
                    if (c == '\\')
                    {
                        i++;
                        switch (s[i])
                        {
                            case '"': sb.Append('"'); break;
                            case '\\': sb.Append('\\'); break;
                            case '/': sb.Append('/'); break;
                            case 'n': sb.Append('\n'); break;
                            case 'r': sb.Append('\r'); break;
                            case 't': sb.Append('\t'); break;
                            case 'b': sb.Append('\b'); break;
                            case 'f': sb.Append('\f'); break;
                            case 'u':
                                sb.Append((char)Convert.ToInt32(s.Substring(i + 1, 4), 16));
                                i += 4;
                                break;
                        }
                        i++;
                    }
                    else
                    {
                        sb.Append(c);
                        i++;
                    }
                }
                i++; // closing quote
                return sb.ToString();
            }

            private static double ParseNumber(string s, ref int i)
            {
                int start = i;
                while (i < s.Length && (char.IsDigit(s[i]) || s[i] == '-' || s[i] == '+' || s[i] == '.' || s[i] == 'e' || s[i] == 'E')) i++;
                return double.Parse(s.Substring(start, i - start), CultureInfo.InvariantCulture);
            }

            private static List<object?> ParseArray(string s, ref int i)
            {
                var list = new List<object?>();
                i++; // '['
                SkipWhitespace(s, ref i);
                if (s[i] == ']') { i++; return list; }
                while (true)
                {
                    list.Add(ParseValue(s, ref i));
                    SkipWhitespace(s, ref i);
                    if (s[i] == ',') { i++; continue; }
                    if (s[i] == ']') { i++; break; }
                    throw new FormatException("malformed JSON array");
                }
                return list;
            }

            private static Dictionary<string, object?> ParseObject(string s, ref int i)
            {
                var dict = new Dictionary<string, object?>();
                i++; // '{'
                SkipWhitespace(s, ref i);
                if (s[i] == '}') { i++; return dict; }
                while (true)
                {
                    SkipWhitespace(s, ref i);
                    var key = ParseString(s, ref i);
                    SkipWhitespace(s, ref i);
                    i++; // ':'
                    dict[key] = ParseValue(s, ref i);
                    SkipWhitespace(s, ref i);
                    if (s[i] == ',') { i++; continue; }
                    if (s[i] == '}') { i++; break; }
                    throw new FormatException("malformed JSON object");
                }
                return dict;
            }
        }

        // RFC 1950 (zlib) wrapper around .NET's own raw DEFLATE
        // (RFC 1951) stream: a 2-byte header (0x78 0x9C - CM=8/deflate,
        // CINFO=7/32K window, FLEVEL=2/default, chosen so the 16-bit
        // header value is a multiple of 31 as the spec requires) plus a
        // big-endian Adler-32 trailer over the UNCOMPRESSED bytes. This is
        // exactly the wire format `pako.deflate()`/Python's
        // `zlib.compress()`/pako's own JS decoder (and the real Fragments
        // loader, which auto-detects it) expect - .NET's own
        // DeflateStream/GZipStream implement neither RFC 1950 directly,
        // and this package deliberately doesn't take on SharpZipLib/
        // System.IO.Compression.ZLibStream (net6.0+ only - the core
        // package targets netstandard2.0) just for this one call site.
        private static byte[] ZlibCompress(byte[] data)
        {
            using var output = new MemoryStream();
            output.WriteByte(0x78);
            output.WriteByte(0x9C);
            using (var deflate = new DeflateStream(output, CompressionLevel.Optimal, leaveOpen: true))
            {
                deflate.Write(data, 0, data.Length);
            }
            uint adler = Adler32(data);
            output.WriteByte((byte)(adler >> 24));
            output.WriteByte((byte)(adler >> 16));
            output.WriteByte((byte)(adler >> 8));
            output.WriteByte((byte)adler);
            return output.ToArray();
        }

        private static uint Adler32(byte[] data)
        {
            const uint Mod = 65521;
            uint a = 1, b = 0;
            foreach (var by in data)
            {
                a = (a + by) % Mod;
                b = (b + a) % Mod;
            }
            return (b << 16) | a;
        }

        /// <summary>Builds a <c>.frag</c> (FlatBuffers) buffer from an
        /// <see cref="InstancedScene"/>.</summary>
        /// <param name="scene">The instanced scene, e.g. from
        /// <c>InstancedSceneBuilder.Build</c>.</param>
        /// <param name="raw">If <c>false</c> (the default, matching
        /// ThatOpen's own <c>IfcImporter</c> convention), the output is
        /// zlib-deflate-compressed - the real Fragments loader
        /// auto-detects either form. Pass <c>true</c> for the uncompressed
        /// FlatBuffers bytes directly.</param>
        public static byte[] ToFragments(InstancedScene scene, bool raw = false)
        {
            if (scene == null) throw new ArgumentNullException(nameof(scene));

            var leaves = new List<Leaf>();
            CollectLeaves(scene.SceneHierarchy, scene.SceneHierarchy, IdentityMatrix, leaves);

            var resourceById = new Dictionary<string, InstancedMeshResource>();
            foreach (var r in scene.MeshResources) resourceById[r.Id] = r;

            var builder = new FlatBufferBuilder(1024 * 64);

            // ---- Shells/Representations/Materials, built lazily as leaves
            // are walked below: keyed by (resource, primitive, baked scale)
            // so every placement sharing the same definition AND the same
            // scale/mirror state dedupes onto one Shell. ----
            var shellKeyToIndex = new Dictionary<(string, int, double, double, double), List<int>>();
            var shellOffsets = new List<Offset<Fb.Shell>>();
            var representationBounds = new List<(float[] Min, float[] Max)>();
            var materialKeyToIndex = new Dictionary<int, int>();
            var materialRgba = new List<(byte R, byte G, byte B, byte A)>();

            int GetMaterialIndex(int materialIndex)
            {
                if (materialKeyToIndex.TryGetValue(materialIndex, out var existing)) return existing;
                object? gltfMat = materialIndex >= 0 && materialIndex < scene.GltfMaterials.Count
                    ? scene.GltfMaterials[materialIndex]
                    : null;
                var rgba = ExtractBaseColor(gltfMat);
                int idx = materialRgba.Count;
                materialRgba.Add(rgba);
                materialKeyToIndex[materialIndex] = idx;
                return idx;
            }

            int BakeOneShell(List<float[]> points, List<uint[]> triangles)
            {
                bool isBig = points.Count > UshortMax;

                var profileOffsets = new List<Offset<Fb.ShellProfile>>();
                var bigProfileOffsets = new List<Offset<Fb.BigShellProfile>>();
                foreach (var tri in triangles)
                {
                    if (isBig)
                    {
                        var idxVec = Fb.BigShellProfile.CreateIndicesVector(builder, new[] { tri[0], tri[1], tri[2] });
                        bigProfileOffsets.Add(Fb.BigShellProfile.CreateBigShellProfile(builder, idxVec));
                    }
                    else
                    {
                        var idxVec = Fb.ShellProfile.CreateIndicesVector(builder, new[]
                        {
                            (ushort)tri[0], (ushort)tri[1], (ushort)tri[2],
                        });
                        profileOffsets.Add(Fb.ShellProfile.CreateShellProfile(builder, idxVec));
                    }
                }

                // profiles/big_profiles are BOTH required fields on Shell,
                // but only one of the two encodings is ever real for a
                // given shell (the other is just an empty vector).
                VectorOffset profilesVec, bigProfilesVec;
                if (isBig)
                {
                    bigProfilesVec = Fb.Shell.CreateBigProfilesVector(builder, bigProfileOffsets.ToArray());
                    profilesVec = Fb.Shell.CreateProfilesVector(builder, profileOffsets.ToArray());
                }
                else
                {
                    profilesVec = Fb.Shell.CreateProfilesVector(builder, profileOffsets.ToArray());
                    bigProfilesVec = Fb.Shell.CreateBigProfilesVector(builder, bigProfileOffsets.ToArray());
                }

                var holesVec = Fb.Shell.CreateHolesVector(builder, Array.Empty<Offset<Fb.ShellHole>>());
                var bigHolesVec = Fb.Shell.CreateBigHolesVector(builder, Array.Empty<Offset<Fb.BigShellHole>>());

                Fb.Shell.StartPointsVector(builder, points.Count);
                for (int i = points.Count - 1; i >= 0; i--)
                {
                    var p = points[i];
                    builder.Prep(4, 12);
                    builder.PutFloat(p[2]);
                    builder.PutFloat(p[1]);
                    builder.PutFloat(p[0]);
                }
                var pointsVec = builder.EndVector();

                // Sequential per-shell profile ids (0..triangles.Count-1, not
                // tied to any upstream SketchUp face identity - see the
                // caller for how a too-large triangle list is chunked
                // before reaching here), so re-numbering from 0 per shell is
                // exactly consistent with the single-shell behavior this is
                // a straight extraction of.
                var faceIds = new ushort[triangles.Count];
                for (int i = 0; i < faceIds.Length; i++) faceIds[i] = (ushort)i;
                var faceIdsVec = Fb.Shell.CreateProfilesFaceIdsVector(builder, faceIds);

                Fb.Shell.StartShell(builder);
                Fb.Shell.AddProfiles(builder, profilesVec);
                Fb.Shell.AddBigProfiles(builder, bigProfilesVec);
                Fb.Shell.AddHoles(builder, holesVec);
                Fb.Shell.AddBigHoles(builder, bigHolesVec);
                Fb.Shell.AddPoints(builder, pointsVec);
                Fb.Shell.AddType(builder, isBig ? Fb.ShellType.BIG : Fb.ShellType.NONE);
                Fb.Shell.AddProfilesFaceIds(builder, faceIdsVec);

                int index = shellOffsets.Count;
                shellOffsets.Add(Fb.Shell.EndShell(builder));

                float minX = float.PositiveInfinity, minY = float.PositiveInfinity, minZ = float.PositiveInfinity;
                float maxX = float.NegativeInfinity, maxY = float.NegativeInfinity, maxZ = float.NegativeInfinity;
                foreach (var p in points)
                {
                    if (p[0] < minX) minX = p[0]; if (p[0] > maxX) maxX = p[0];
                    if (p[1] < minY) minY = p[1]; if (p[1] > maxY) maxY = p[1];
                    if (p[2] < minZ) minZ = p[2]; if (p[2] > maxZ) maxZ = p[2];
                }
                if (float.IsPositiveInfinity(minX)) { minX = minY = minZ = 0; maxX = maxY = maxZ = 0; }
                representationBounds.Add((new[] { minX, minY, minZ }, new[] { maxX, maxY, maxZ }));

                return index;
            }

            List<int> GetOrBakeShell(string resourceId, int primIdx, LocalPrimitive prim, double[] scale, bool mirrored)
            {
                var sk = ScaleCacheKey(mirrored, scale);
                var key = (resourceId, primIdx, sk.Item1, sk.Item2, sk.Item3);
                if (shellKeyToIndex.TryGetValue(key, out var existing)) return existing;

                var baked = BakePrimitive(prim, scale, mirrored);

                // `profiles_face_ids` (written above) has no "big"/uint32
                // counterpart anywhere in the real Fragments schema
                // (index.fbs only declares `profiles_face_ids: [ushort]` -
                // unlike points, which DO get a BigShellProfile/uint32-index
                // escape hatch past 65535 of them). A single shell genuinely
                // cannot represent more than 65535 triangles no matter how
                // points are encoded - confirmed the hard way against a real
                // production model with one 222,000+-triangle mesh (see
                // Python's own fix for the full incident writeup). Splitting
                // into multiple shells, each within the ushort limit, is the
                // only way to represent this - the format has no cap on
                // shell COUNT, just per-shell triangle count. Each sub-shell
                // duplicates the full (shared) points array rather than
                // remapping to a local subset: simpler and lower-risk than a
                // vertex-remapping pass, at the cost of some extra file size
                // in this rare oversized-mesh case.
                List<int> indices;
                if (baked.Triangles.Count > UshortMax)
                {
                    indices = new List<int>();
                    for (int i = 0; i < baked.Triangles.Count; i += UshortMax)
                    {
                        int count = Math.Min(UshortMax, baked.Triangles.Count - i);
                        indices.Add(BakeOneShell(baked.Points, baked.Triangles.GetRange(i, count)));
                    }
                }
                else
                {
                    indices = new List<int> { BakeOneShell(baked.Points, baked.Triangles) };
                }

                shellKeyToIndex[key] = indices;
                return indices;
            }

            // ---- Model-level items + geometry samples: one item per leaf
            // placement, one sample per (leaf, primitive-of-its-resource)
            // pair. ----
            var localIds = new List<uint>();
            var categories = new List<string>();
            var names = new List<string>();
            var guids = new List<string>();
            // GUIDs of items whose Name is a fallback this project generated
            // (no real name anywhere in the source file), not something a
            // person or plugin actually named - see
            // InstancedNode.NameIsGenerated. Carried in Model.metadata below,
            // same mechanism as layer_hidden, since the public Fragments
            // schema has no field for this either.
            var generatedNameGuids = new List<string>();
            var sampleMaterial = new List<int>();
            var sampleRepresentation = new List<int>();
            var meshesItems = new List<uint>();
            var globalTransformData = new List<Trs>();
            var itemIndexByNode = new Dictionary<InstancedNode, uint>();

            // Real-world SketchUp files can carry a non-unique per-instance
            // GUID: an engineer authors one instance (a framing plugin
            // writes its own identity into that instance's attribute
            // dictionary), then duplicates it 10-20 times via SketchUp's own
            // native Copy/Move+Copy/Array tools instead of re-running the
            // plugin per placement. A plain SketchUp entity duplication
            // carries the source instance's attribute dictionaries - and
            // whatever GUID field a plugin wrote into one - to every copy
            // verbatim; each copy gets its own distinct transform but not
            // its own distinct identity (openskp#290). The first instance to
            // claim a real GUID keeps it; every later instance sharing that
            // same value falls back to a synthetic one instead. Mirrors
            // Python's/C++'s own seen_guids handling exactly.
            var seenGuids = new HashSet<string>();

            for (int itemIndex = 0; itemIndex < leaves.Count; itemIndex++)
            {
                var leaf = leaves[itemIndex];
                var node = leaf.Node;
                InstancedMeshResource? res = null;
                if (node.MeshResourceId != null)
                {
                    if (!resourceById.TryGetValue(node.MeshResourceId, out res))
                    {
                        // Real error case: a leaf declared a resource that
                        // never got baked - skip it rather than silently
                        // tracking a nameless, geometry-less item, same as
                        // Python/C++/TypeScript.
                        continue;
                    }
                }

                localIds.Add((uint)itemIndex);
                categories.Add(string.IsNullOrEmpty(node.Layer) ? "Layer0" : node.Layer);
                names.Add(node.Name ?? "");
                string rawGuid = node.Guid ?? "";
                string itemGuid = (!string.IsNullOrEmpty(rawGuid) && !seenGuids.Contains(rawGuid))
                    ? rawGuid
                    : $"openskp-{itemIndex}";
                seenGuids.Add(itemGuid);
                guids.Add(itemGuid);
                if (node.NameIsGenerated) generatedNameGuids.Add(itemGuid);
                itemIndexByNode[node] = (uint)itemIndex;

                if (res != null)
                {
                    var trs = DecomposeTrs(leaf.World);
                    for (int primIdx = 0; primIdx < res.Primitives.Count; primIdx++)
                    {
                        var prim = res.Primitives[primIdx];
                        int materialIndex = GetMaterialIndex(prim.MaterialIndex);
                        // Normally exactly one shell; more than one only
                        // when the primitive's own triangle count exceeded
                        // what a single shell can represent (see
                        // GetOrBakeShell) - each extra shell becomes its own
                        // additional Sample of the same item/material/
                        // transform, the same pattern this loop already
                        // uses for multiple primitives of one item.
                        foreach (int shellIndex in GetOrBakeShell(node.MeshResourceId!, primIdx, prim, trs.Scale, trs.Mirrored))
                        {
                            sampleMaterial.Add(materialIndex);
                            sampleRepresentation.Add(shellIndex);
                            meshesItems.Add((uint)itemIndex);
                            globalTransformData.Add(trs);
                        }
                    }
                }
            }

            int nSamples = sampleMaterial.Count;

            var shellsVec = Fb.Meshes.CreateShellsVector(builder, shellOffsets.ToArray());

            Fb.Meshes.StartMaterialsVector(builder, materialRgba.Count);
            for (int i = materialRgba.Count - 1; i >= 0; i--)
            {
                var rgba = materialRgba[i];
                Fb.Material.CreateMaterial(builder, rgba.R, rgba.G, rgba.B, rgba.A, Fb.RenderedFaces.ONE, Fb.Stroke.DEFAULT);
            }
            var materialsVec = builder.EndVector();

            Fb.Meshes.StartRepresentationsVector(builder, representationBounds.Count);
            for (int i = representationBounds.Count - 1; i >= 0; i--)
            {
                var (min, max) = representationBounds[i];
                Fb.Representation.CreateRepresentation(
                    builder, (uint)i, min[0], min[1], min[2], max[0], max[1], max[2],
                    Fb.RepresentationClass.SHELL);
            }
            var representationsVec = builder.EndVector();

            Fb.Meshes.StartSamplesVector(builder, nSamples);
            for (int i = nSamples - 1; i >= 0; i--)
            {
                Fb.Sample.CreateSample(builder, (uint)i, (uint)sampleMaterial[i], (uint)sampleRepresentation[i], 0u);
            }
            var samplesVec = builder.EndVector();

            var meshesItemsVec = Fb.Meshes.CreateMeshesItemsVector(builder, meshesItems.ToArray());

            Fb.Meshes.StartGlobalTransformsVector(builder, nSamples);
            for (int i = nSamples - 1; i >= 0; i--)
            {
                var t = globalTransformData[i];
                Fb.Transform.CreateTransform(
                    builder, t.Position[0], t.Position[1], t.Position[2],
                    (float)t.XDir[0], (float)t.XDir[1], (float)t.XDir[2],
                    (float)t.YDir[0], (float)t.YDir[1], (float)t.YDir[2]);
            }
            var globalTransformsVec = builder.EndVector();

            // One shared identity local transform - no per-geometry
            // sub-offset is needed since every primitive's points are
            // already in the resource's own local space (now with
            // scale/mirror already baked in).
            Fb.Meshes.StartLocalTransformsVector(builder, 1);
            Fb.Transform.CreateTransform(builder, 0.0, 0.0, 0.0, 1f, 0f, 0f, 0f, 1f, 0f);
            var localTransformsVec = builder.EndVector();

            var circleExtrusionsVec = Fb.Meshes.CreateCircleExtrusionsVector(builder, Array.Empty<Offset<Fb.CircleExtrusion>>());

            var coordinates = Fb.Transform.CreateTransform(builder, 0.0, 0.0, 0.0, 1f, 0f, 0f, 0f, 1f, 0f);

            Fb.Meshes.StartMeshes(builder);
            Fb.Meshes.AddCoordinates(builder, coordinates);
            Fb.Meshes.AddMeshesItems(builder, meshesItemsVec);
            Fb.Meshes.AddSamples(builder, samplesVec);
            Fb.Meshes.AddRepresentations(builder, representationsVec);
            Fb.Meshes.AddMaterials(builder, materialsVec);
            Fb.Meshes.AddCircleExtrusions(builder, circleExtrusionsVec);
            Fb.Meshes.AddShells(builder, shellsVec);
            Fb.Meshes.AddLocalTransforms(builder, localTransformsVec);
            Fb.Meshes.AddGlobalTransforms(builder, globalTransformsVec);
            var meshesOff = Fb.Meshes.EndMeshes(builder);

            var catOffsets = categories.Select(builder.CreateString).ToArray();
            var categoriesVec = Fb.Model.CreateCategoriesVector(builder, catOffsets);

            var localIdsVec = Fb.Model.CreateLocalIdsVector(builder, localIds.ToArray());

            var guidStr = builder.CreateString("00000000-0000-0000-0000-000000000000");

            // Per-item GUIDs: guids[i] is that item's identifier,
            // guidsItems[i] is which local_id it belongs to - a real
            // consumer's own id-bridge zips Model.getGuids() against
            // Model.getLocalIds() index-for-index, so both vectors must be
            // the same length, in the same order, one entry per tracked
            // item (a zero-length guids vector, this export's behavior
            // before this fix existed in Python, zips to an empty map
            // regardless of how many real items exist).
            var guidOffsets = guids.Select(builder.CreateString).ToArray();
            var guidsVec = Fb.Model.CreateGuidsVector(builder, guidOffsets);
            var guidsItemsVec = Fb.Model.CreateGuidsItemsVector(builder, localIds.ToArray());

            // One Attribute per tracked item (same order as
            // local_ids/categories), carrying the item's real display name
            // encoded as a `["Name", value, "STRING"]` JSON triple - the
            // exact convention the real IfcImporter uses for its own
            // "Name" attribute (confirmed against real ground-truth .frag
            // output, matching Python/C++/TypeScript exactly).
            var attributeOffsets = new List<Offset<Fb.Attribute>>();
            foreach (var name in names)
            {
                var dataOffsets = new List<StringOffset>();
                if (!string.IsNullOrEmpty(name))
                {
                    dataOffsets.Add(builder.CreateString(NameAttributeJson(name)));
                }
                var dataVec = Fb.Attribute.CreateDataVector(builder, dataOffsets.ToArray());
                Fb.Attribute.StartAttribute(builder);
                Fb.Attribute.AddData(builder, dataVec);
                attributeOffsets.Add(Fb.Attribute.EndAttribute(builder));
            }
            var attributesVec = Fb.Model.CreateAttributesVector(builder, attributeOffsets.ToArray());

            var metadataOff = builder.CreateString(MetadataJson(scene.LayerHidden, generatedNameGuids));

            // Spatial structure: one SpatialStructure node per
            // InstancedNode in the ORIGINAL tree (not just leaves), so real
            // component nesting comes through, not just a flat list. A
            // node gets a local_id only when it's one of the
            // geometry-bearing/named-wrapper items tracked above; a pure
            // organizational container just wraps its children. Matches
            // Python's/C++'s/TypeScript's own build_spatial_node/
            // buildSpatialNode exactly.
            Offset<Fb.SpatialStructure> BuildSpatialNode(InstancedNode node)
            {
                var childOffsets = node.Children.Select(BuildSpatialNode).ToArray();
                var childrenVec = Fb.SpatialStructure.CreateChildrenVector(builder, childOffsets);

                StringOffset categoryOff = default;
                bool hasCategory = !string.IsNullOrEmpty(node.Layer);
                if (hasCategory) categoryOff = builder.CreateString(node.Layer);

                uint? localId = itemIndexByNode.TryGetValue(node, out var idx) ? idx : (uint?)null;

                Fb.SpatialStructure.StartSpatialStructure(builder);
                if (localId.HasValue) Fb.SpatialStructure.AddLocalId(builder, localId);
                if (hasCategory) Fb.SpatialStructure.AddCategory(builder, categoryOff);
                Fb.SpatialStructure.AddChildren(builder, childrenVec);
                return Fb.SpatialStructure.EndSpatialStructure(builder);
            }
            var rootSpatial = BuildSpatialNode(scene.SceneHierarchy);

            var modelOff = Fb.Model.CreateModel(
                builder,
                metadataOffset: metadataOff,
                guidsOffset: guidsVec,
                guids_itemsOffset: guidsItemsVec,
                max_local_id: (uint)localIds.Count,
                local_idsOffset: localIdsVec,
                categoriesOffset: categoriesVec,
                meshesOffset: meshesOff,
                attributesOffset: attributesVec,
                guidOffset: guidStr,
                spatial_structureOffset: rootSpatial);

            builder.Finish(modelOff.Value, "0001");
            var rawBytes = builder.SizedByteArray();

            return raw ? rawBytes : ZlibCompress(rawBytes);
        }

        /// <summary>Exports an instanced scene to a <c>.frag</c> file.</summary>
        public static void ExportFragments(InstancedScene scene, string outputPath, bool raw = false)
        {
            var dir = Path.GetDirectoryName(outputPath);
            if (!string.IsNullOrEmpty(dir)) Directory.CreateDirectory(dir!);
            File.WriteAllBytes(outputPath, ToFragments(scene, raw));
        }

        // RepresentationClass values this module can read geometry for
        // today - mirrors Python's own _SUPPORTED_REPRESENTATION_CLASSES
        // and its note on why (only SHELL is written by any of this
        // project's own exporters yet).
        private static readonly HashSet<Fb.RepresentationClass> SupportedRepresentationClasses =
            new HashSet<Fb.RepresentationClass> { Fb.RepresentationClass.SHELL };

        private static (double, double, double) Cross3((double, double, double) a, (double, double, double) b) =>
            (a.Item2 * b.Item3 - a.Item3 * b.Item2,
             a.Item3 * b.Item1 - a.Item1 * b.Item3,
             a.Item1 * b.Item2 - a.Item2 * b.Item1);

        /// <summary>One normal per vertex, flat-shaded: each triangle's own
        /// face normal, duplicated across its 3 vertices - the most a Shell
        /// (points + indices, nothing else) can ever give back. Mirrors
        /// Python's <c>_compute_flat_normals</c>.</summary>
        private static (double, double, double)[] ComputeFlatNormals(
            (double, double, double)[] points, (uint, uint, uint)[] triangles)
        {
            var normals = new (double, double, double)[points.Length];
            for (int i = 0; i < normals.Length; i++) normals[i] = (0.0, 0.0, 1.0);
            foreach (var (a, b, c) in triangles)
            {
                var pa = points[a]; var pb = points[b]; var pc = points[c];
                var u = (pb.Item1 - pa.Item1, pb.Item2 - pa.Item2, pb.Item3 - pa.Item3);
                var v = (pc.Item1 - pa.Item1, pc.Item2 - pa.Item2, pc.Item3 - pa.Item3);
                var n = Cross3(u, v);
                double length = Math.Sqrt(n.Item1 * n.Item1 + n.Item2 * n.Item2 + n.Item3 * n.Item3);
                if (length > 1e-12) n = (n.Item1 / length, n.Item2 / length, n.Item3 / length);
                normals[a] = n; normals[b] = n; normals[c] = n;
            }
            return normals;
        }

        /// <summary>Reassemble a glTF-style column-major 4x4 matrix from a
        /// Fragments <c>Transform</c> struct (position + x/y direction unit
        /// vectors). The struct never stores a Z direction - reconstructed
        /// here as <c>xDir cross yDir</c>, matching the same right-handed
        /// orthonormal frame <see cref="DecomposeTrs"/> produces on export.
        /// Mirrors Python's <c>_transform_to_matrix16</c>.</summary>
        private static double[] TransformToMatrix16(Fb.Transform transform)
        {
            var pos = transform.Position;
            var xDirS = transform.XDirection;
            var yDirS = transform.YDirection;
            var xDir = ((double)xDirS.X, (double)xDirS.Y, (double)xDirS.Z);
            var yDir = ((double)yDirS.X, (double)yDirS.Y, (double)yDirS.Z);
            var zDir = Cross3(xDir, yDir);
            return new[]
            {
                xDir.Item1, xDir.Item2, xDir.Item3, 0.0,
                yDir.Item1, yDir.Item2, yDir.Item3, 0.0,
                zDir.Item1, zDir.Item2, zDir.Item3, 0.0,
                pos.X, pos.Y, pos.Z, 1.0,
            };
        }

        /// <summary>Pull the <c>["Name", value, "STRING"]</c> entry out of
        /// an item's <c>Attribute.Data</c> strings, matching the exact
        /// convention <see cref="ToFragments"/> (and the real
        /// <c>IfcImporter</c>) writes. Mirrors Python's
        /// <c>_extract_name_attribute</c>.</summary>
        private static string ExtractNameAttribute(Fb.Attribute? attribute)
        {
            if (attribute == null) return "";
            var attr = attribute.Value;
            for (int i = 0; i < attr.DataLength; i++)
            {
                var raw = attr.Data(i);
                if (raw == null) continue;
                object? parsed;
                try { parsed = MinimalJson.Parse(raw); }
                catch { continue; }
                if (parsed is List<object?> triple && triple.Count >= 2 && (triple[0] as string) == "Name")
                {
                    return triple[1]?.ToString() ?? "";
                }
            }
            return "";
        }

        // RFC 1950 (zlib) inflate: skip the 2-byte header, run the rest
        // through .NET's own raw-DEFLATE DeflateStream, ignore the trailing
        // 4-byte Adler-32 (DeflateStream stops at the deflate stream's own
        // end marker regardless of what follows). Mirror of ZlibCompress
        // above, for the same netstandard2.0-compatibility reason.
        private static byte[] ZlibDecompress(byte[] data)
        {
            using var input = new MemoryStream(data, 2, data.Length - 2);
            using var deflate = new DeflateStream(input, CompressionMode.Decompress);
            using var output = new MemoryStream();
            deflate.CopyTo(output);
            return output.ToArray();
        }

        /// <summary>Parses a real <c>.frag</c> file's bytes into an
        /// <see cref="InstancedScene"/> - the mirror of
        /// <see cref="ToFragments"/>. Accepts either the zlib-compressed
        /// wire format (the default <see cref="ToFragments"/>/real loader
        /// convention) or raw, uncompressed FlatBuffers bytes; detected
        /// automatically the same way the real <c>@thatopen/fragments</c>
        /// loader does, by attempting zlib inflation first.
        ///
        /// Known gaps, matching Python's own read side exactly (not
        /// independently improved on here - see openskp#285):
        /// <see cref="InstancedNode.PositionMm"/>/<see cref="InstancedNode.Properties"/>/
        /// <see cref="InstancedNode.AttributeDictionaries"/> are left at
        /// their defaults, since the Fragments format doesn't carry them
        /// separately from the <c>Name</c> attribute this does extract.</summary>
        public static InstancedScene FromFragments(byte[] data)
        {
            byte[] rawBytes;
            try { rawBytes = data.Length > 2 ? ZlibDecompress(data) : data; }
            catch { rawBytes = data; }

            var bb = new ByteBuffer(rawBytes);
            var model = Fb.Model.GetRootAsModel(bb);
            var meshes = model.Meshes;

            // ---- Materials (flat RGBA - no texture concept exists in this
            // format at all). ----
            var gltfMaterials = new List<object>();
            if (meshes != null)
            {
                var m = meshes.Value;
                for (int i = 0; i < m.MaterialsLength; i++)
                {
                    var mat = m.Materials(i)!.Value;
                    gltfMaterials.Add(new Dictionary<string, object>
                    {
                        ["pbrMetallicRoughness"] = new Dictionary<string, object>
                        {
                            ["baseColorFactor"] = new[] { mat.R / 255.0, mat.G / 255.0, mat.B / 255.0, mat.A / 255.0 },
                            ["metallicFactor"] = 0.0,
                            ["roughnessFactor"] = 1.0,
                        },
                        ["doubleSided"] = mat.RenderedFaces != Fb.RenderedFaces.ONE,
                    });
                }
            }
            if (gltfMaterials.Count == 0)
            {
                gltfMaterials.Add(new Dictionary<string, object>
                {
                    ["pbrMetallicRoughness"] = new Dictionary<string, object>
                    {
                        ["baseColorFactor"] = new[] { 0.8, 0.8, 0.8, 1.0 },
                        ["metallicFactor"] = 0.0,
                        ["roughnessFactor"] = 1.0,
                    },
                });
            }

            // ---- Shells -> (points, triangles), decoded once per shell
            // index, reused by every sample referencing it. ----
            int nShells = meshes?.ShellsLength ?? 0;
            var shellPoints = new (double, double, double)[nShells][];
            var shellTriangles = new (uint, uint, uint)[nShells][];
            var shellDecoded = new bool[nShells];

            ((double, double, double)[] points, (uint, uint, uint)[] triangles) DecodeShell(int shellIdx)
            {
                if (shellDecoded[shellIdx]) return (shellPoints[shellIdx], shellTriangles[shellIdx]);
                var shell = meshes!.Value.Shells(shellIdx)!.Value;
                int nPoints = shell.PointsLength;
                var pts = new (double, double, double)[nPoints];
                for (int j = 0; j < nPoints; j++)
                {
                    var p = shell.Points(j)!.Value;
                    pts[j] = (p.X, p.Y, p.Z);
                }

                bool isBig = shell.Type == Fb.ShellType.BIG;
                var tris = new List<(uint, uint, uint)>();
                if (isBig)
                {
                    for (int j = 0; j < shell.BigProfilesLength; j++)
                    {
                        var profile = shell.BigProfiles(j)!.Value;
                        if (profile.IndicesLength >= 3)
                            tris.Add((profile.Indices(0), profile.Indices(1), profile.Indices(2)));
                    }
                }
                else
                {
                    for (int j = 0; j < shell.ProfilesLength; j++)
                    {
                        var profile = shell.Profiles(j)!.Value;
                        if (profile.IndicesLength >= 3)
                            tris.Add((profile.Indices(0), profile.Indices(1), profile.Indices(2)));
                    }
                }

                shellPoints[shellIdx] = pts;
                shellTriangles[shellIdx] = tris.ToArray();
                shellDecoded[shellIdx] = true;
                return (pts, shellTriangles[shellIdx]);
            }

            // ---- Group samples by item (Meshes.MeshesItems[k] -> item
            // index, NOT Sample.Item() - see ToFragments's own comment on
            // why the two differ; Sample.Item() is just the sample's own
            // position, always). ----
            int nSamples = meshes?.SamplesLength ?? 0;
            var samplesByItem = new Dictionary<uint, List<int>>();
            for (int k = 0; k < nSamples; k++)
            {
                uint itemIdx = meshes!.Value.MeshesItems(k);
                if (!samplesByItem.TryGetValue(itemIdx, out var list))
                {
                    list = new List<int>();
                    samplesByItem[itemIdx] = list;
                }
                list.Add(k);
            }

            // ---- Per-item metadata: name, guid, category, world
            // transform. localIds[i] IS the item index space - see
            // ToFragments's own localIds.Add(itemIndex). ----
            int nItems = model.LocalIdsLength;
            var localIdToItemIndex = new Dictionary<uint, int>();
            for (int i = 0; i < nItems; i++) localIdToItemIndex[model.LocalIds(i)] = i;

            var guidByItem = new Dictionary<int, string>();
            int nGuidItems = model.GuidsItemsLength;
            for (int i = 0; i < nGuidItems; i++)
            {
                uint lid = model.GuidsItems(i);
                if (localIdToItemIndex.TryGetValue(lid, out var itemIdx) && i < model.GuidsLength)
                {
                    guidByItem[itemIdx] = model.Guids(i) ?? "";
                }
            }

            var layerHidden = new Dictionary<string, bool>();
            var generatedNameGuids = new HashSet<string>();
            var metadataRaw = model.Metadata;
            if (!string.IsNullOrEmpty(metadataRaw))
            {
                try
                {
                    if (MinimalJson.Parse(metadataRaw) is Dictionary<string, object?> meta)
                    {
                        if (meta.TryGetValue("layer_hidden", out var lhObj) && lhObj is Dictionary<string, object?> lh)
                        {
                            foreach (var kv in lh)
                                if (kv.Value is bool b) layerHidden[kv.Key] = b;
                        }
                        if (meta.TryGetValue("generated_name_guids", out var gnObj) && gnObj is List<object?> gn)
                        {
                            foreach (var v in gn)
                                if (v is string s) generatedNameGuids.Add(s);
                        }
                    }
                }
                catch { /* leave layerHidden/generatedNameGuids empty */ }
            }

            var meshResources = new List<InstancedMeshResource>();
            var resourceBySignature = new Dictionary<string, string>();
            bool warnedUnsupported = false;

            string BuildResourceForItem(int itemIdx, string itemName)
            {
                var sampleIndices = samplesByItem.TryGetValue((uint)itemIdx, out var l) ? l : new List<int>();
                var signature = new List<(uint, uint)>();
                var primitives = new List<LocalPrimitive>();
                foreach (var k in sampleIndices)
                {
                    var sample = meshes!.Value.Samples(k)!.Value;
                    uint repIdx = sample.Representation;
                    uint matIdx = sample.Material;
                    Fb.Representation? representation = repIdx < meshes.Value.RepresentationsLength
                        ? meshes.Value.Representations((int)repIdx) : null;
                    var repClass = representation?.RepresentationClass ?? Fb.RepresentationClass.SHELL;
                    if (!SupportedRepresentationClasses.Contains(repClass))
                    {
                        if (!warnedUnsupported)
                        {
                            Console.Error.WriteLine(
                                $"OpenSkp.Fragments.FromFragments: skipping a sample with unsupported " +
                                $"RepresentationClass={repClass} (only SHELL is read today).");
                            warnedUnsupported = true;
                        }
                        continue;
                    }
                    signature.Add((repIdx, matIdx));
                    // Representation.Id is the index into Meshes.Shells -
                    // NOT the representation's own position in the
                    // Representations vector. See Python's
                    // from_fragments's own comment on why this must follow
                    // Id, matching the real reader's own fetch-functions.ts.
                    uint shellIdx = representation!.Value.Id;
                    if (shellIdx >= nShells) continue;
                    var (points, triangles) = DecodeShell((int)shellIdx);
                    var normals = ComputeFlatNormals(points, triangles);
                    var positions = new float[points.Length * 3];
                    var normalsArr = new float[points.Length * 3];
                    for (int i = 0; i < points.Length; i++)
                    {
                        positions[i * 3] = (float)points[i].Item1;
                        positions[i * 3 + 1] = (float)points[i].Item2;
                        positions[i * 3 + 2] = (float)points[i].Item3;
                        normalsArr[i * 3] = (float)normals[i].Item1;
                        normalsArr[i * 3 + 1] = (float)normals[i].Item2;
                        normalsArr[i * 3 + 2] = (float)normals[i].Item3;
                    }
                    var uvs = new float[points.Length * 2];
                    var indices = new uint[triangles.Length * 3];
                    for (int i = 0; i < triangles.Length; i++)
                    {
                        indices[i * 3] = triangles[i].Item1;
                        indices[i * 3 + 1] = triangles[i].Item2;
                        indices[i * 3 + 2] = triangles[i].Item3;
                    }
                    primitives.Add(new LocalPrimitive
                    {
                        Positions = positions,
                        Normals = normalsArr,
                        Uvs = uvs,
                        Indices = indices,
                        MaterialIndex = matIdx < gltfMaterials.Count ? (int)matIdx : 0,
                    });
                }

                string sigKey = string.Join(",", signature.Select(s => $"{s.Item1}:{s.Item2}"));
                if (sigKey.Length > 0 && resourceBySignature.TryGetValue(sigKey, out var existing))
                    return existing;

                string resourceId = $"frag-{itemIdx}";
                meshResources.Add(new InstancedMeshResource
                {
                    Id = resourceId,
                    DefinitionId = itemIdx,
                    DefinitionName = string.IsNullOrEmpty(itemName) ? resourceId : itemName,
                    VariantKey = "default",
                    Primitives = primitives,
                });
                if (sigKey.Length > 0) resourceBySignature[sigKey] = resourceId;
                return resourceId;
            }

            // ---- Spatial structure -> InstancedNode tree. ----
            InstancedNode BuildNode(Fb.SpatialStructure spatial)
            {
                uint? localId = spatial.LocalId;
                string category = spatial.Category ?? "";

                string name = "";
                string guid = "";
                string? meshResourceId = null;
                double[] matrix = IdentityMatrix;
                bool nameIsGenerated = false;

                if (localId.HasValue && localIdToItemIndex.TryGetValue(localId.Value, out var itemIdx))
                {
                    Fb.Attribute? attribute = itemIdx < model.AttributesLength ? model.Attributes(itemIdx) : null;
                    name = ExtractNameAttribute(attribute);
                    guid = guidByItem.TryGetValue(itemIdx, out var g) ? g : "";
                    nameIsGenerated = !string.IsNullOrEmpty(guid) && generatedNameGuids.Contains(guid);
                    if (samplesByItem.TryGetValue((uint)itemIdx, out var sIndices))
                    {
                        meshResourceId = BuildResourceForItem(itemIdx, string.IsNullOrEmpty(name) ? category : name);
                        var transform = meshes!.Value.GlobalTransforms(sIndices[0])!.Value;
                        matrix = TransformToMatrix16(transform);
                    }
                }

                var children = new List<InstancedNode>();
                for (int i = 0; i < spatial.ChildrenLength; i++) children.Add(BuildNode(spatial.Children(i)!.Value));

                return new InstancedNode
                {
                    Name = name,
                    NameIsGenerated = nameIsGenerated,
                    DefinitionName = category,
                    Layer = category,
                    Matrix = matrix,
                    Guid = guid,
                    MeshResourceId = meshResourceId,
                    Children = children,
                };
            }

            Fb.SpatialStructure? rootSpatial = model.SpatialStructure;
            InstancedNode sceneHierarchy = rootSpatial != null
                ? BuildNode(rootSpatial.Value)
                : new InstancedNode { Name = "ROOT", DefinitionName = "ROOT" };

            return new InstancedScene
            {
                Bounds = null,
                SceneHierarchy = sceneHierarchy,
                MeshResources = meshResources,
                GltfMaterials = gltfMaterials,
                Textures = new List<SceneTexture>(),
                LayerHidden = layerHidden,
            };
        }

        /// <summary>Reads a <c>.frag</c> file from disk into an
        /// <see cref="InstancedScene"/>. See <see cref="FromFragments"/> for
        /// the full contract and known gaps.</summary>
        public static InstancedScene ReadFragments(string path) => FromFragments(File.ReadAllBytes(path));
    }
}
