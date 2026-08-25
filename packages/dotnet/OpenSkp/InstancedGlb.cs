using System;
using System.Collections.Generic;
using System.IO;
using System.Text;

namespace OpenSkp
{
    /// <summary>Options for <see cref="InstancedGlbExport.ToInstancedGlb"/>/
    /// <see cref="InstancedGlbExport.ExportInstancedGlb"/>.</summary>
    public sealed class InstancedGlbOptions
    {
        /// <summary>Embed the scene's texture images in the GLB and point
        /// each textured material's baseColorTexture at them. Off by
        /// default, matching <see cref="GlbOptions.Textures"/>: photographic
        /// textures can multiply the file size, and the geometry alone is
        /// what most callers are after.</summary>
        public bool Textures { get; set; }
    }

    /// <summary>Binary glTF (.glb) export for an <see cref="InstancedScene"/>,
    /// PRESERVING instancing: each mesh resource is written to the binary
    /// buffer exactly once, and every placement is a glTF node whose
    /// "mesh" points at it.
    ///
    /// This is what <see cref="GlbExport"/> cannot do from a baked
    /// <see cref="Scene"/>, whose primitives already have the world
    /// transform folded into their vertex data - there is nothing left to
    /// share. Here, a component placed 1,000 times contributes one copy of
    /// its vertex/index buffers plus 1,000 node transforms.
    ///
    /// A definition that resolves to several materials becomes ONE glTF
    /// mesh with several primitives (the normal glTF representation), not
    /// several nodes - matching the TypeScript reference implementation
    /// (instanced-glb.ts, openskp#200). <see cref="GlbExport"/> is untouched
    /// and still produces exactly what it always has.</summary>
    public static class InstancedGlbExport
    {
        private const long GlbSizeLimit = uint.MaxValue;

        /// <summary>Serializes an InstancedScene to binary glTF 2.0 (GLB)
        /// bytes.</summary>
        public static byte[] ToInstancedGlb(InstancedScene scene, InstancedGlbOptions? options = null)
        {
            if (scene == null) throw new ArgumentNullException(nameof(scene));
            var resources = scene.MeshResources ?? new List<InstancedMeshResource>();
            var rawMaterials = scene.GltfMaterials ?? new List<object>();
            bool embedTextures = options?.Textures == true;
            var sceneTextures = embedTextures ? (scene.Textures ?? new List<SceneTexture>()) : new List<SceneTexture>();
            var materials = embedTextures ? rawMaterials : StripTextureRefs(rawMaterials);

            long totalBinaryLength = 0;
            foreach (var res in resources)
            {
                foreach (var prim in res.Primitives)
                {
                    totalBinaryLength += (long)prim.Positions.Length * 4;
                    totalBinaryLength += (long)prim.Normals.Length * 4;
                    totalBinaryLength += (long)prim.Uvs.Length * 4;
                    totalBinaryLength += (long)prim.Indices.Length * 4;
                }
            }

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
            var gltfMeshes = new List<object>();
            var meshIndexById = new Dictionary<string, int>();

            var byteOffset = 0;
            foreach (var res in resources)
            {
                var gltfPrimitives = new List<object>();
                foreach (var prim in res.Primitives)
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
                        ["target"] = 34962,
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
                        ["target"] = 34963,
                    });

                    float minX = float.PositiveInfinity, minY = float.PositiveInfinity, minZ = float.PositiveInfinity;
                    float maxX = float.NegativeInfinity, maxY = float.NegativeInfinity, maxZ = float.NegativeInfinity;
                    for (var i = 0; i < prim.Positions.Length; i += 3)
                    {
                        var x = prim.Positions[i]; var y = prim.Positions[i + 1]; var z = prim.Positions[i + 2];
                        if (x < minX) minX = x; if (x > maxX) maxX = x;
                        if (y < minY) minY = y; if (y > maxY) maxY = y;
                        if (z < minZ) minZ = z; if (z > maxZ) maxZ = z;
                    }
                    if (float.IsPositiveInfinity(minX)) { minX = minY = minZ = 0; maxX = maxY = maxZ = 0; }

                    var posAccessorIdx = accessors.Count;
                    accessors.Add(new Dictionary<string, object>
                    {
                        ["bufferView"] = posBufferViewIdx,
                        ["byteOffset"] = 0,
                        ["componentType"] = 5126,
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
                        ["componentType"] = 5125,
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

                if (gltfPrimitives.Count == 0) continue;
                meshIndexById[res.Id] = gltfMeshes.Count;
                gltfMeshes.Add(new Dictionary<string, object>
                {
                    ["name"] = string.IsNullOrEmpty(res.DefinitionName) ? res.Id : res.DefinitionName,
                    ["primitives"] = gltfPrimitives,
                });
            }

            // Flatten the instance tree into glTF nodes. Node transforms
            // are already parent-relative glTF matrices, so the hierarchy
            // maps across directly and each node keeps pointing at the ONE
            // shared mesh.
            var gltfNodes = new List<Dictionary<string, object>>();

            int EmitNode(InstancedNode node)
            {
                var idx = gltfNodes.Count;
                var gltfNode = new Dictionary<string, object>();
                if (!string.IsNullOrEmpty(node.Name)) gltfNode["name"] = node.Name;
                else if (!string.IsNullOrEmpty(node.DefinitionName)) gltfNode["name"] = node.DefinitionName;

                // glTF treats an omitted matrix as the identity; writing it
                // out anyway just costs bytes on every node of a large
                // scene.
                if (!IsIdentity(node.Matrix)) gltfNode["matrix"] = node.Matrix;

                if (node.MeshResourceId != null && meshIndexById.TryGetValue(node.MeshResourceId, out var meshIdx))
                {
                    gltfNode["mesh"] = meshIdx;
                }

                gltfNodes.Add(gltfNode);

                if (node.Children.Count > 0)
                {
                    var childIndices = new List<object>();
                    foreach (var child in node.Children) childIndices.Add(EmitNode(child));
                    gltfNodes[idx]["children"] = childIndices;
                }
                return idx;
            }

            var rootIdx = EmitNode(scene.SceneHierarchy);

            var gltfImages = new List<object>();
            var gltfTextures = new List<object>();
            for (var i = 0; i < sceneTextures.Count; i++)
            {
                var tex = sceneTextures[i];
                var (offset, length) = imagePlacements[i];
                Buffer.BlockCopy(tex.Data, 0, binaryBuffer, (int)offset, length);

                var imgBufferViewIdx = bufferViews.Count;
                bufferViews.Add(new Dictionary<string, object> { ["buffer"] = 0, ["byteOffset"] = offset, ["byteLength"] = length });
                var imageIdx = gltfImages.Count;
                gltfImages.Add(new Dictionary<string, object> { ["bufferView"] = imgBufferViewIdx, ["mimeType"] = tex.MimeType });
                gltfTextures.Add(new Dictionary<string, object> { ["sampler"] = 0, ["source"] = imageIdx });
            }

            var gltfJson = new Dictionary<string, object>
            {
                ["asset"] = new Dictionary<string, object> { ["version"] = "2.0", ["generator"] = "OpenSKP .NET Instanced Exporter" },
                ["scene"] = 0,
                ["scenes"] = new object[] { new Dictionary<string, object> { ["nodes"] = new object[] { rootIdx } } },
                ["nodes"] = gltfNodes,
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
                gltfJson["samplers"] = new object[] { new Dictionary<string, object> { ["wrapS"] = 10497, ["wrapT"] = 10497 } };
            }

            return CreateGlb(gltfJson, binaryBuffer);
        }

        private static bool IsIdentity(double[] m)
        {
            if (m.Length != 16) return false;
            for (var i = 0; i < 16; i++)
            {
                if (Math.Abs(m[i] - InstancedSceneBuilder.IdentityGltf[i]) > 1e-12) return false;
            }
            return true;
        }

        /// <summary>Materials with every baseColorTexture reference removed
        /// - a copy, never mutating the input. Mirrors
        /// GlbExport.StripTextureRefs exactly (duplicated rather than
        /// shared, matching the TypeScript reference's own
        /// instanced-glb.ts, which duplicates this from index.ts too).</summary>
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

        /// <summary>Serializes an InstancedScene to GLB and writes it to
        /// <paramref name="path"/>.</summary>
        public static void ExportInstancedGlb(InstancedScene scene, string path, InstancedGlbOptions? options = null)
        {
            var bytes = ToInstancedGlb(scene, options);
            File.WriteAllBytes(path, bytes);
        }

        private static byte[] CreateGlb(object json, byte[] binaryBuffer)
        {
            var jsonBytes = Encoding.UTF8.GetBytes(MiniJson.Serialize(json));
            var jsonPad = (4 - jsonBytes.Length % 4) % 4;
            if (jsonPad > 0)
            {
                var padded = new byte[jsonBytes.Length + jsonPad];
                Buffer.BlockCopy(jsonBytes, 0, padded, 0, jsonBytes.Length);
                for (var i = jsonBytes.Length; i < padded.Length; i++) padded[i] = 0x20;
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
            WriteU32(glb, ref p, 0x46546C67);
            WriteU32(glb, ref p, 2);
            WriteU32(glb, ref p, (uint)totalLength);

            WriteU32(glb, ref p, (uint)jsonBytes.Length);
            WriteU32(glb, ref p, 0x4E4F534A);
            Buffer.BlockCopy(jsonBytes, 0, glb, p, jsonBytes.Length);
            p += jsonBytes.Length;

            WriteU32(glb, ref p, (uint)paddedBinary.Length);
            WriteU32(glb, ref p, 0x004E4942);
            Buffer.BlockCopy(paddedBinary, 0, glb, p, paddedBinary.Length);
            p += paddedBinary.Length;

            return glb;
        }

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
