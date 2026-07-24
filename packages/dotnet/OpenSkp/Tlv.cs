using System;
using System.Collections.Generic;

namespace OpenSkp
{
    /// <summary>A single Tag-Length-Value node in the binary parse tree.</summary>
    internal sealed class TlvNode
    {
        public int Offset;
        public string Tag = "";
        public int Size;
        public List<TlvNode> Children = new List<TlvNode>();
        public byte[] Payload = Array.Empty<byte>();
    }

    /// <summary>Low-level TLV (Tag-Length-Value) binary parsing helpers.
    /// Ported from Python's _core.py; all multi-byte reads are explicitly
    /// little-endian regardless of host platform endianness.</summary>
    internal static class Tlv
    {
        public static readonly HashSet<string> ContainerTags = new HashSet<string>
        {
            "F401", "F701", "D430", "D530", "C832",
            "7C15", "8813", "8913", "8A13", "8B13", "8C13", "8D13", "4C1D", "6419",
            "F901", "7017", "7117", "D007", "C409", "9411", "9511", "0F01",
            "384A", "B80B", "9713", "2C4C", "AC0D", "AE0D", "F601", "F801",
            "983A", "993A", "8C3C", "8D3C",
            // Image-entity placement: an Image placed in the model wraps a
            // standard 6419 instance node inside 9013 -> 401F. Without these
            // two containers, that inner instance stays buried in an opaque
            // payload and the image definition looks "never placed".
            "9013", "401F",
        };

        public static ushort ReadU16(byte[] data, int offset)
        {
            return (ushort)(data[offset] | (data[offset + 1] << 8));
        }

        public static uint ReadU32(byte[] data, int offset)
        {
            return (uint)(data[offset] | (data[offset + 1] << 8) | (data[offset + 2] << 16) | (data[offset + 3] << 24));
        }

        public static int ReadI32(byte[] data, int offset)
        {
            return unchecked((int)ReadU32(data, offset));
        }

        public static double ReadF64(byte[] data, int offset)
        {
            long bits = 0;
            for (int i = 0; i < 8; i++)
            {
                bits |= (long)data[offset + i] << (8 * i);
            }
            return BitConverter.Int64BitsToDouble(bits);
        }

        public static long ParseVarInt(byte[] data, int offset, int length)
        {
            long val = 0;
            for (int i = 0; i < length; i++)
            {
                val |= (long)data[offset + i] << (8 * i);
            }
            return val;
        }

        public static string ToHexUpper(byte[] data)
        {
            var chars = new char[data.Length * 2];
            for (int i = 0; i < data.Length; i++)
            {
                string h = data[i].ToString("X2");
                chars[i * 2] = h[0];
                chars[i * 2 + 1] = h[1];
            }
            return new string(chars);
        }

        private static string TagHex(byte[] data, int offset)
        {
            return data[offset].ToString("X2") + data[offset + 1].ToString("X2");
        }

        public static List<TlvNode> ParseRecursive(byte[] data, int start, int end, HashSet<string>? containerTags = null)
        {
            containerTags ??= ContainerTags;
            var elements = new List<TlvNode>();
            int pos = start;
            while (pos < end - 6)
            {
                string tagHex = TagHex(data, pos);
                uint size = ReadU32(data, pos + 2);
                if (pos + 6 + size > end)
                {
                    break;
                }
                bool isContainer = containerTags.Contains(tagHex);
                List<TlvNode> children = new List<TlvNode>();
                if (isContainer && size > 0)
                {
                    children = ParseRecursive(data, pos + 6, (int)(pos + 6 + size), containerTags);
                }
                byte[] payload = Array.Empty<byte>();
                if (children.Count == 0 && size > 0)
                {
                    payload = new byte[size];
                    Array.Copy(data, pos + 6, payload, 0, (int)size);
                }
                elements.Add(new TlvNode
                {
                    Offset = pos,
                    Tag = tagHex,
                    Size = (int)size,
                    Children = children,
                    Payload = payload,
                });
                pos += 6 + (int)size;
            }
            return elements;
        }

        /// <summary>Walk a raw payload as a flat TLV sequence (no container-tag
        /// awareness); returns [(tag, body)] pairs.</summary>
        public static List<(string Tag, byte[] Body)> ParseFlat(byte[] payload)
        {
            var result = new List<(string, byte[])>();
            int pos = 0;
            while (pos <= payload.Length - 6)
            {
                string tag = TagHex(payload, pos);
                uint size = ReadU32(payload, pos + 2);
                if (pos + 6 + size > payload.Length) break;
                byte[] body = new byte[size];
                Array.Copy(payload, pos + 6, body, 0, (int)size);
                result.Add((tag, body));
                pos += 6 + (int)size;
            }
            return result;
        }

        public static byte[]? FindFlat(List<(string Tag, byte[] Body)> seq, string tag)
        {
            foreach (var (t, body) in seq)
            {
                if (t == tag) return body;
            }
            return null;
        }

        /// <summary>Scan [start, end) for direct-child (tag, offset, size)
        /// headers only, without recursing into any container - O(sibling
        /// count), not O(total node count). Used by IterTopLevelLazy to
        /// locate top-level records one at a time.</summary>
        private static List<(string Tag, int Offset, int Size)> FlatHeaders(byte[] data, int start, int end)
        {
            var headers = new List<(string, int, int)>();
            int pos = start;
            while (pos < end - 6)
            {
                string tagHex = TagHex(data, pos);
                uint size = ReadU32(data, pos + 2);
                if (pos + 6 + size > end) break;
                headers.Add((tagHex, pos, (int)size));
                pos += 6 + (int)size;
            }
            return headers;
        }

        /// <summary>Yield each top-level TLV record's fully-recursed node one
        /// at a time, transparently unwrapping a lone "F401" wrapper -
        /// without ever materializing more than one top-level subtree
        /// simultaneously. Each yielded node is safe to discard (drop all
        /// references) once the caller is done with it, before the next one
        /// is produced - that's what keeps peak memory bounded by the size of
        /// the single largest top-level record instead of the whole file
        /// (real production files can have 100k+ separate definitions).</summary>
        public static IEnumerable<TlvNode> IterTopLevelLazy(byte[] data, int start, int end, HashSet<string>? containerTags = null)
        {
            containerTags ??= ContainerTags;

            var headers = FlatHeaders(data, start, end);
            if (headers.Count == 1 && headers[0].Tag == "F401")
            {
                var (_, f401Offset, f401Size) = headers[0];
                headers = FlatHeaders(data, f401Offset + 6, f401Offset + 6 + f401Size);
            }

            foreach (var (tag, offset, size) in headers)
            {
                int recordEnd = offset + 6 + size;
                var nodes = ParseRecursive(data, offset, recordEnd, containerTags);
                if (nodes.Count > 0)
                {
                    yield return nodes[0];
                }
            }
        }
    }
}
