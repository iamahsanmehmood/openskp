using System;
using System.Collections.Generic;
using System.Runtime.CompilerServices;
using System.Security.Cryptography;

namespace OpenSkp
{
    /// <summary>The distinct texture images a built scene places, deduplicated by content.
    ///
    /// Shared by the baked (<see cref="SceneBuilder"/>) and instanced
    /// (<see cref="InstancedSceneBuilder"/>) builders for the same reason
    /// <see cref="FaceGroups"/> is: one implementation makes the two paths agree on texture
    /// identity by construction rather than by parallel maintenance.</summary>
    internal sealed class TextureRegistry
    {
        private readonly Dictionary<string, int> _indexByContent = new Dictionary<string, int>(StringComparer.Ordinal);
        private readonly Dictionary<Geometry.RawTexture, int> _indexByInstance =
            new Dictionary<Geometry.RawTexture, int>(RawTextureReferenceComparer.Instance);

        public List<SceneTexture> Textures { get; } = new List<SceneTexture>();

        /// <summary>Index of this texture's image, adding it when unseen. Null for a texture
        /// with no data, and for an image format glTF cannot carry.</summary>
        public int? IndexFor(Geometry.RawTexture? tex)
        {
            if (tex?.Data == null || tex.Data.Length == 0) return null;
            if (_indexByInstance.TryGetValue(tex, out var seen)) return seen;

            var mimeType = SniffImageMime(tex.Data);
            if (mimeType == null) return null;

            // Keyed on the whole image, and memoized per texture instance so a large image is
            // hashed once rather than on every face that uses it. Keying on the length plus a
            // short prefix cannot work: every PNG opens with the same 16 bytes (signature, then
            // the IHDR chunk's length and type), so such a key collapses to the file length and
            // silently merges two different images that happen to be the same size.
            var key = ContentKey(tex.Data);
            if (!_indexByContent.TryGetValue(key, out var idx))
            {
                idx = Textures.Count;
                Textures.Add(new SceneTexture { Data = tex.Data, MimeType = mimeType, Filename = tex.Filename });
                _indexByContent[key] = idx;
            }

            _indexByInstance[tex] = idx;
            return idx;
        }

        private static string ContentKey(byte[] data)
        {
            using var sha = SHA256.Create();
            return BitConverter.ToString(sha.ComputeHash(data));
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

        private sealed class RawTextureReferenceComparer : IEqualityComparer<Geometry.RawTexture>
        {
            public static readonly RawTextureReferenceComparer Instance = new RawTextureReferenceComparer();

            public bool Equals(Geometry.RawTexture? x, Geometry.RawTexture? y) => ReferenceEquals(x, y);

            public int GetHashCode(Geometry.RawTexture obj) => RuntimeHelpers.GetHashCode(obj);
        }
    }
}
