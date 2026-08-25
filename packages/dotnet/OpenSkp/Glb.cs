using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Reflection;
using System.Text;

namespace OpenSkp
{
    /// <summary>Minimal JSON serializer for the plain object graphs
    /// (nested Dictionary/List/array/primitive, or anonymous types via
    /// reflection) GlbExport builds internally. Not a general-purpose
    /// JSON library - this project stays dependency-light everywhere
    /// except C++'s bundled TinyGLTF, and netstandard2.0 has no built-in
    /// JSON support, so this only needs to cover the shapes GlbExport
    /// actually produces.</summary>
    internal static class MiniJson
    {
        public static string Serialize(object? value)
        {
            var sb = new StringBuilder();
            Write(sb, value);
            return sb.ToString();
        }

        private static void Write(StringBuilder sb, object? value)
        {
            switch (value)
            {
                case null:
                    sb.Append("null");
                    return;
                case string s:
                    WriteString(sb, s);
                    return;
                case bool b:
                    sb.Append(b ? "true" : "false");
                    return;
                // "R" is unreliable for float round-tripping on some
                // netstandard2.0 runtimes (a known historical .NET
                // Framework bug) - G9/G17 are the documented
                // always-round-trips-safely precisions for float/double.
                case float f:
                    sb.Append(f.ToString("G9", CultureInfo.InvariantCulture));
                    return;
                case double d:
                    sb.Append(d.ToString("G17", CultureInfo.InvariantCulture));
                    return;
                case int or uint or long or ulong:
                    sb.Append(Convert.ToString(value, CultureInfo.InvariantCulture));
                    return;
                case IDictionary<string, object> dict:
                    WriteObject(sb, dict);
                    return;
                case IEnumerable enumerable:
                    WriteArray(sb, enumerable);
                    return;
                default:
                    WriteReflected(sb, value);
                    return;
            }
        }

        private static void WriteString(StringBuilder sb, string s)
        {
            sb.Append('"');
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
            sb.Append('"');
        }

        private static void WriteObject(StringBuilder sb, IDictionary<string, object> dict)
        {
            sb.Append('{');
            var first = true;
            foreach (var kv in dict)
            {
                if (!first) sb.Append(',');
                first = false;
                WriteString(sb, kv.Key);
                sb.Append(':');
                Write(sb, kv.Value);
            }
            sb.Append('}');
        }

        private static void WriteArray(StringBuilder sb, IEnumerable enumerable)
        {
            sb.Append('[');
            var first = true;
            foreach (var item in enumerable)
            {
                if (!first) sb.Append(',');
                first = false;
                Write(sb, item);
            }
            sb.Append(']');
        }

        // Anonymous types (used for Scene.GltfMaterials) have no
        // interface to pattern-match on - their property names match the
        // assigned identifiers exactly (e.g. `new { pbrMetallicRoughness
        // = ... }`), so reflecting them produces the correct camelCase
        // glTF keys with no name mapping needed.
        private static void WriteReflected(StringBuilder sb, object value)
        {
            sb.Append('{');
            var first = true;
            foreach (var prop in value.GetType().GetProperties(BindingFlags.Public | BindingFlags.Instance))
            {
                if (!first) sb.Append(',');
                first = false;
                WriteString(sb, prop.Name);
                sb.Append(':');
                Write(sb, prop.GetValue(value));
            }
            sb.Append('}');
        }
    }

    /// <summary>Binary glTF (.glb) export for a baked Scene - a
    /// from-scratch writer with no external dependency, matching how this
    /// project has stayed dependency-light everywhere except C++'s
    /// bundled TinyGLTF. Ported from the TypeScript reference
    /// implementation's toGLB(), with full TEXCOORD_0 UV support and the
    /// same validation rigor as the C++ port's glb.cpp.</summary>
    /// <summary>Options for <see cref="GlbExport.ToGlb"/>/<see cref="GlbExport.ExportGlb"/>.</summary>
    public sealed class GlbOptions
    {
        /// <summary>Embed the scene's texture images in the GLB and point
        /// each textured material's baseColorTexture at them. Off by
        /// default, matching every other language's exporter: photographic
        /// textures can multiply the file size, and the geometry alone is
        /// what most callers are after.</summary>
        public bool Textures { get; set; }
    }

    public static class GlbExport
    {
        // glTF's chunk-length fields are uint32 - a GLB file's total size
        // (and each individual chunk) is hard-capped at 4GB by the format
        // itself, not an arbitrary choice here.
        private const long GlbSizeLimit = uint.MaxValue;

        /// <summary>Serializes a baked Scene to binary glTF 2.0 (GLB) bytes.</summary>
        public static byte[] ToGlb(Scene scene, GlbOptions? options = null)
        {
            if (scene == null) throw new ArgumentNullException(nameof(scene));
            var prims = scene.GlbPrimitives ?? new List<GlbPrimitive>();
            var rawMaterials = scene.GltfMaterials ?? new List<object>();
            bool embedTextures = options?.Textures == true;
            var sceneTextures = embedTextures ? (scene.Textures ?? new List<SceneTexture>()) : new List<SceneTexture>();

            // Materials always reference textures by index (Scene.Build sets
            // that up unconditionally); embedding the actual image bytes is
            // the opt-in part. When not embedding, strip the reference so a
            // strict glTF reader never sees a baseColorTexture pointing at a
            // textures[] array that was never written - a copy, so the
            // Scene the caller owns is never mutated.
            var materials = embedTextures ? rawMaterials : StripTextureRefs(rawMaterials);

            ValidateScene(prims, materials);

            long totalBinaryLength = 0;
            foreach (var prim in prims)
            {
                totalBinaryLength += (long)prim.Positions.Length * 4;
                totalBinaryLength += (long)prim.Normals.Length * 4;
                totalBinaryLength += (long)prim.Uvs.Length * 4;
                totalBinaryLength += (long)prim.Indices.Length * 4;
            }

            // Each image is placed on its own 4-byte-aligned offset, as
            // glTF requires for bufferView data.
            var imagePlacements = new List<(long Offset, int Length)>();
            foreach (var tex in sceneTextures)
            {
                totalBinaryLength += (4 - (totalBinaryLength % 4)) % 4;
                imagePlacements.Add((totalBinaryLength, tex.Data.Length));
                totalBinaryLength += tex.Data.Length;
            }

            if (totalBinaryLength > GlbSizeLimit)
                throw new InvalidOperationException("scene geometry exceeds GLB's 32-bit binary-buffer limit");

            var binaryBuffer = new byte[totalBinaryLength];
            var bufferViews = new List<object>();
            var accessors = new List<object>();
            var gltfPrimitives = new List<object>();

            var byteOffset = 0;
            foreach (var prim in prims)
            {
                var posByteOffset = byteOffset;
                Buffer.BlockCopy(prim.Positions, 0, binaryBuffer, byteOffset, prim.Positions.Length * 4);
                byteOffset += prim.Positions.Length * 4;

                var normByteOffset = byteOffset;
                Buffer.BlockCopy(prim.Normals, 0, binaryBuffer, byteOffset, prim.Normals.Length * 4);
                byteOffset += prim.Normals.Length * 4;

                var uvByteOffset = byteOffset;
                Buffer.BlockCopy(prim.Uvs, 0, binaryBuffer, byteOffset, prim.Uvs.Length * 4);
                byteOffset += prim.Uvs.Length * 4;

                var indByteOffset = byteOffset;
                Buffer.BlockCopy(prim.Indices, 0, binaryBuffer, byteOffset, prim.Indices.Length * 4);
                byteOffset += prim.Indices.Length * 4;

                var posBufferViewIdx = bufferViews.Count;
                bufferViews.Add(new Dictionary<string, object>
                {
                    ["buffer"] = 0,
                    ["byteOffset"] = posByteOffset,
                    ["byteLength"] = prim.Positions.Length * 4,
                    ["target"] = 34962, // ARRAY_BUFFER
                });

                var normBufferViewIdx = bufferViews.Count;
                bufferViews.Add(new Dictionary<string, object>
                {
                    ["buffer"] = 0,
                    ["byteOffset"] = normByteOffset,
                    ["byteLength"] = prim.Normals.Length * 4,
                    ["target"] = 34962,
                });

                var uvBufferViewIdx = bufferViews.Count;
                bufferViews.Add(new Dictionary<string, object>
                {
                    ["buffer"] = 0,
                    ["byteOffset"] = uvByteOffset,
                    ["byteLength"] = prim.Uvs.Length * 4,
                    ["target"] = 34962,
                });

                var indBufferViewIdx = bufferViews.Count;
                bufferViews.Add(new Dictionary<string, object>
                {
                    ["buffer"] = 0,
                    ["byteOffset"] = indByteOffset,
                    ["byteLength"] = prim.Indices.Length * 4,
                    ["target"] = 34963, // ELEMENT_ARRAY_BUFFER
                });

                float minX = float.PositiveInfinity, minY = float.PositiveInfinity, minZ = float.PositiveInfinity;
                float maxX = float.NegativeInfinity, maxY = float.NegativeInfinity, maxZ = float.NegativeInfinity;
                for (var i = 0; i < prim.Positions.Length; i += 3)
                {
                    var x = prim.Positions[i];
                    var y = prim.Positions[i + 1];
                    var z = prim.Positions[i + 2];
                    if (x < minX) minX = x;
                    if (x > maxX) maxX = x;
                    if (y < minY) minY = y;
                    if (y > maxY) maxY = y;
                    if (z < minZ) minZ = z;
                    if (z > maxZ) maxZ = z;
                }

                var posAccessorIdx = accessors.Count;
                accessors.Add(new Dictionary<string, object>
                {
                    ["bufferView"] = posBufferViewIdx,
                    ["byteOffset"] = 0,
                    ["componentType"] = 5126, // FLOAT
                    ["count"] = prim.Positions.Length / 3,
                    ["type"] = "VEC3",
                    ["min"] = new object[] { minX, minY, minZ },
                    ["max"] = new object[] { maxX, maxY, maxZ },
                });

                var normAccessorIdx = accessors.Count;
                accessors.Add(new Dictionary<string, object>
                {
                    ["bufferView"] = normBufferViewIdx,
                    ["byteOffset"] = 0,
                    ["componentType"] = 5126,
                    ["count"] = prim.Normals.Length / 3,
                    ["type"] = "VEC3",
                });

                var uvAccessorIdx = accessors.Count;
                accessors.Add(new Dictionary<string, object>
                {
                    ["bufferView"] = uvBufferViewIdx,
                    ["byteOffset"] = 0,
                    ["componentType"] = 5126,
                    ["count"] = prim.Uvs.Length / 2,
                    ["type"] = "VEC2",
                });

                var indAccessorIdx = accessors.Count;
                accessors.Add(new Dictionary<string, object>
                {
                    ["bufferView"] = indBufferViewIdx,
                    ["byteOffset"] = 0,
                    ["componentType"] = 5125, // UNSIGNED_INT
                    ["count"] = prim.Indices.Length,
                    ["type"] = "SCALAR",
                });

                gltfPrimitives.Add(new Dictionary<string, object>
                {
                    ["attributes"] = new Dictionary<string, object>
                    {
                        ["POSITION"] = posAccessorIdx,
                        ["NORMAL"] = normAccessorIdx,
                        ["TEXCOORD_0"] = uvAccessorIdx,
                    },
                    ["indices"] = indAccessorIdx,
                    ["material"] = prim.MaterialIndex,
                });
            }

            var gltfImages = new List<object>();
            var gltfTextures = new List<object>();
            for (var i = 0; i < sceneTextures.Count; i++)
            {
                var tex = sceneTextures[i];
                var (offset, length) = imagePlacements[i];
                Buffer.BlockCopy(tex.Data, 0, binaryBuffer, (int)offset, length);

                var imgBufferViewIdx = bufferViews.Count;
                bufferViews.Add(new Dictionary<string, object>
                {
                    ["buffer"] = 0,
                    ["byteOffset"] = offset,
                    ["byteLength"] = length,
                });

                var imageIdx = gltfImages.Count;
                gltfImages.Add(new Dictionary<string, object>
                {
                    ["bufferView"] = imgBufferViewIdx,
                    ["mimeType"] = tex.MimeType,
                });
                gltfTextures.Add(new Dictionary<string, object> { ["sampler"] = 0, ["source"] = imageIdx });
            }

            var gltfMeshes = new List<object>();
            if (gltfPrimitives.Count > 0)
            {
                gltfMeshes.Add(new Dictionary<string, object> { ["primitives"] = gltfPrimitives });
            }

            var gltfJson = new Dictionary<string, object>
            {
                ["asset"] = new Dictionary<string, object> { ["version"] = "2.0", ["generator"] = "OpenSKP .NET Exporter" },
                ["scene"] = 0,
                ["scenes"] = new object[]
                {
                    new Dictionary<string, object> { ["nodes"] = gltfMeshes.Count > 0 ? new object[] { 0 } : Array.Empty<object>() },
                },
                ["nodes"] = gltfMeshes.Count > 0 ? new object[] { new Dictionary<string, object> { ["mesh"] = 0 } } : Array.Empty<object>(),
                ["meshes"] = gltfMeshes,
                ["materials"] = materials,
                ["buffers"] = new object[] { new Dictionary<string, object> { ["byteLength"] = totalBinaryLength } },
                ["bufferViews"] = bufferViews,
                ["accessors"] = accessors,
            };
            if (gltfImages.Count > 0)
            {
                gltfJson["images"] = gltfImages;
                gltfJson["textures"] = gltfTextures;
                gltfJson["samplers"] = new object[]
                {
                    new Dictionary<string, object> { ["wrapS"] = 10497, ["wrapT"] = 10497 }, // REPEAT / REPEAT
                };
            }

            return CreateGlb(gltfJson, binaryBuffer);
        }

        /// <summary>Materials with every baseColorTexture reference removed -
        /// a copy, never mutating the input. Used when not embedding images,
        /// so a strict glTF reader never sees a reference into a textures[]
        /// array that was never written.
        ///
        /// Only Dictionary-shaped materials (what SceneBuilder.Build
        /// produces) are inspected; anything else - e.g. the anonymous
        /// types a hand-built Scene can still use, since GltfMaterials is
        /// publicly just List&lt;object&gt; - passes through unchanged.
        /// Stripping is a courtesy for the auto-generated shape, not a
        /// contract on every possible material representation.</summary>
        private static List<object> StripTextureRefs(List<object> materials)
        {
            var needsCopy = false;
            foreach (var m in materials)
            {
                if (m is IDictionary<string, object> dict &&
                    dict.TryGetValue("pbrMetallicRoughness", out var pbrObj) &&
                    pbrObj is IDictionary<string, object> pbr &&
                    pbr.ContainsKey("baseColorTexture"))
                {
                    needsCopy = true;
                    break;
                }
            }
            if (!needsCopy) return materials;

            var result = new List<object>(materials.Count);
            foreach (var m in materials)
            {
                if (m is IDictionary<string, object> dict &&
                    dict.TryGetValue("pbrMetallicRoughness", out var pbrObj) &&
                    pbrObj is IDictionary<string, object> pbr &&
                    pbr.ContainsKey("baseColorTexture"))
                {
                    var newPbr = new Dictionary<string, object>(pbr);
                    newPbr.Remove("baseColorTexture");
                    var newMat = new Dictionary<string, object>(dict) { ["pbrMetallicRoughness"] = newPbr };
                    result.Add(newMat);
                }
                else
                {
                    result.Add(m);
                }
            }
            return result;
        }

        /// <summary>Serializes a baked Scene to GLB and writes it to
        /// <paramref name="path"/>. Does not create missing parent
        /// directories - matching the C++ port's export_glb, the other
        /// language with this same in-memory-bytes/file-write pair.</summary>
        public static void ExportGlb(Scene scene, string path, GlbOptions? options = null)
        {
            var bytes = ToGlb(scene, options);
            File.WriteAllBytes(path, bytes);
        }

        private static void ValidateScene(List<GlbPrimitive> prims, List<object> materials)
        {
            for (var i = 0; i < prims.Count; i++)
            {
                var prim = prims[i];
                var prefix = $"primitive {i} ";
                if (prim.Positions.Length == 0) throw new ArgumentException(prefix + "positions must not be empty");
                if (prim.Positions.Length % 3 != 0) throw new ArgumentException(prefix + "positions must contain complete vec3 values");
                if (prim.Normals.Length != prim.Positions.Length) throw new ArgumentException(prefix + "normals must match positions");
                if (prim.Uvs.Length != prim.Positions.Length / 3 * 2) throw new ArgumentException(prefix + "uvs must match positions");
                if (prim.Indices.Length == 0) throw new ArgumentException(prefix + "indices must not be empty");
                if (prim.Indices.Length % 3 != 0) throw new ArgumentException(prefix + "indices must contain complete triangles");
                if (prim.MaterialIndex < 0 || prim.MaterialIndex >= materials.Count) throw new ArgumentException(prefix + "references an invalid material");

                var vertexCount = (uint)(prim.Positions.Length / 3);
                foreach (var v in prim.Positions) CheckFinite(v, prefix + "position");
                foreach (var v in prim.Normals) CheckFinite(v, prefix + "normal");
                foreach (var v in prim.Uvs) CheckFinite(v, prefix + "uv");
                foreach (var idx in prim.Indices)
                    if (idx >= vertexCount) throw new ArgumentException(prefix + "index is out of range");
            }
        }

        private static void CheckFinite(float value, string field)
        {
            // netstandard2.0 has no float.IsFinite - IsNaN/IsInfinity
            // predate it and are available everywhere this targets.
            if (float.IsNaN(value) || float.IsInfinity(value))
                throw new ArgumentException(field + " must be finite");
        }

        private static byte[] CreateGlb(object json, byte[] binaryBuffer)
        {
            var jsonBytes = Encoding.UTF8.GetBytes(MiniJson.Serialize(json));
            var jsonPad = (4 - jsonBytes.Length % 4) % 4;
            if (jsonPad > 0)
            {
                var padded = new byte[jsonBytes.Length + jsonPad];
                Buffer.BlockCopy(jsonBytes, 0, padded, 0, jsonBytes.Length);
                for (var i = jsonBytes.Length; i < padded.Length; i++) padded[i] = 0x20; // space
                jsonBytes = padded;
            }

            var binPad = (4 - binaryBuffer.Length % 4) % 4;
            var paddedBinary = binaryBuffer;
            if (binPad > 0)
            {
                paddedBinary = new byte[binaryBuffer.Length + binPad];
                Buffer.BlockCopy(binaryBuffer, 0, paddedBinary, 0, binaryBuffer.Length);
            }

            long totalLength = 12 + 8 + jsonBytes.Length + 8 + paddedBinary.Length;
            if (totalLength > GlbSizeLimit)
                throw new InvalidOperationException("serialized GLB exceeds its 32-bit file-size limit");

            var glb = new byte[totalLength];
            var p = 0;
            WriteU32(glb, ref p, 0x46546C67); // magic 'glTF'
            WriteU32(glb, ref p, 2); // version
            WriteU32(glb, ref p, (uint)totalLength);

            WriteU32(glb, ref p, (uint)jsonBytes.Length);
            WriteU32(glb, ref p, 0x4E4F534A); // 'JSON'
            Buffer.BlockCopy(jsonBytes, 0, glb, p, jsonBytes.Length);
            p += jsonBytes.Length;

            WriteU32(glb, ref p, (uint)paddedBinary.Length);
            WriteU32(glb, ref p, 0x004E4942); // 'BIN\0'
            Buffer.BlockCopy(paddedBinary, 0, glb, p, paddedBinary.Length);
            p += paddedBinary.Length;

            return glb;
        }

        // Chunk headers are written byte-by-byte in explicit little-endian
        // order (matching the C++ port's approach) rather than relying on
        // host endianness, even though every realistic .NET deployment
        // target is little-endian anyway - cheap to be correct here.
        private static void WriteU32(byte[] buffer, ref int pos, uint value)
        {
            buffer[pos] = (byte)value;
            buffer[pos + 1] = (byte)(value >> 8);
            buffer[pos + 2] = (byte)(value >> 16);
            buffer[pos + 3] = (byte)(value >> 24);
            pos += 4;
        }
    }
}
