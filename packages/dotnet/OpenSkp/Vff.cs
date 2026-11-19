using System;
using System.IO;
using System.IO.Compression;
using System.Text;

namespace OpenSkp
{
    /// <summary>Container-level helpers for the modern (2021+) VFF/ZIP .skp
    /// format: header validation, version-string extraction, and locating the
    /// embedded ZIP payload. Ported from Python's _core.full_parse() steps
    /// that run before TLV parsing begins.</summary>
    internal static class Vff
    {
        private static readonly byte[] HeaderMagic = { 0xFF, 0xFE, 0xFF, 0x0E };
        private static readonly byte[] SecondMarker = { 0xFF, 0xFE, 0xFF };
        private static readonly byte[] ZipMagic = { (byte)'P', (byte)'K', 0x03, 0x04 };

        public static bool HasValidHeader(byte[] data)
        {
            if (data.Length < 4) return false;
            for (int i = 0; i < 4; i++)
            {
                if (data[i] != HeaderMagic[i]) return false;
            }
            return true;
        }

        private static int FindBytes(byte[] data, byte[] pattern, int start)
        {
            if (pattern.Length == 0 || start < 0) return -1;
            int limit = data.Length - pattern.Length;
            for (int i = Math.Max(start, 0); i <= limit; i++)
            {
                bool match = true;
                for (int j = 0; j < pattern.Length; j++)
                {
                    if (data[i + j] != pattern[j]) { match = false; break; }
                }
                if (match) return i;
            }
            return -1;
        }

        /// <summary>Extract the "{17.0.18899}"-style version string from the
        /// UTF-16LE-encoded header region, or "unknown" if not found.</summary>
        public static string ExtractVersion(byte[] header)
        {
            int secondMarker = FindBytes(header, SecondMarker, 4);
            if (secondMarker <= 0) return "unknown";

            int verStart = secondMarker + 4;
            if (verStart >= header.Length) return "unknown";

            string verText;
            try
            {
                verText = Encoding.Unicode.GetString(header, verStart, header.Length - verStart);
            }
            catch (ArgumentException)
            {
                return "unknown";
            }

            int braceStart = verText.IndexOf('{');
            int braceEnd = verText.IndexOf('}');
            if (braceStart >= 0 && braceEnd > braceStart)
            {
                return verText.Substring(braceStart, braceEnd - braceStart + 1);
            }
            return "unknown";
        }

        /// <summary>Locate the byte offset of the embedded ZIP container
        /// ("PK\x03\x04"), searching only the first `searchLimit` bytes
        /// (matching Python's header-then-4096-byte-chunk search), or -1 if
        /// not found.</summary>
        public static int FindZipOffset(byte[] data, int searchLimit = 4096)
        {
            int limit = Math.Min(searchLimit, data.Length);
            return FindBytes(data, ZipMagic, 0, limit);
        }

        private static int FindBytes(byte[] data, byte[] pattern, int start, int end)
        {
            if (pattern.Length == 0 || start < 0) return -1;
            int limit = Math.Min(end, data.Length) - pattern.Length;
            for (int i = Math.Max(start, 0); i <= limit; i++)
            {
                bool match = true;
                for (int j = 0; j < pattern.Length; j++)
                {
                    if (data[i + j] != pattern[j]) { match = false; break; }
                }
                if (match) return i;
            }
            return -1;
        }

        public static ZipArchive OpenZip(byte[] data, int zipOffset)
        {
            var stream = new MemoryStream(data, zipOffset, data.Length - zipOffset, writable: false);
            return new ZipArchive(stream, ZipArchiveMode.Read, leaveOpen: false);
        }

        // A ZIP entry's declared uncompressed size (ZipArchiveEntry.Length)
        // is untrusted central-directory metadata - it does not have to
        // match what the compressed stream actually decompresses to. Code
        // that pre-sizes an allocation off it (e.g. ChunkedBuffer.FromStream
        // for model.dat) can be made to allocate an arbitrary amount of
        // memory from a tiny malicious file before a single byte is read.
        // Real production model.dat entries are observed at ~10x
        // compression, so both limits below leave generous headroom for
        // legitimate files while rejecting the kind of declared-size lie a
        // genuine file would never need.
        private const long MaxUncompressedEntryBytes = 16L * 1024 * 1024 * 1024; // 16 GB
        private const long MaxCompressionRatio = 1000;
        private const long RatioCheckThresholdBytes = 1024 * 1024; // 1 MB

        // meta/meta.dat uses the exact same low-level TLV framing as
        // model.dat (2-byte tag + 4-byte little-endian length + payload),
        // but as one flat, non-recursive record list wrapped in a single
        // outer record (tag 0x6400/"6400"). Tag 0x6D00/"6D00" carries the
        // model's unit-system string ("Millimeter" etc.) as plain text.
        // Confirmed byte-for-byte against a real fixture - never opened by
        // any parser in this codebase before.
        private const string MetaWrapperTag = "6400";
        private const string MetaUnitsTag = "6D00";

        /// <summary>Extract the model's unit-system string from a VFF
        /// file's meta/meta.dat contents, or null if the expected tags
        /// aren't found.</summary>
        public static string? ReadMetaUnits(byte[] metaBytes)
        {
            // Real files have one wrapper record around the units. Wrappers
            // are followed in a loop over (start, end) offsets, not by
            // recursing on copies of the body: a file with thousands of
            // nested wrappers would otherwise overflow the stack, or copy
            // almost the whole payload at every level (3.4 s and 5.9 GB for
            // 100,000 levels). More than Tlv.MaxDepth wrappers is not a real
            // file; the units are then unknown, which is not an error.
            int start = 0;
            int end = metaBytes.Length;
            for (int level = 0; level <= Tlv.MaxDepth; level++)
            {
                int pos = start;
                int innerStart = -1;
                int innerEnd = -1;
                while (pos <= end - 6)
                {
                    string tag = metaBytes[pos].ToString("X2") + metaBytes[pos + 1].ToString("X2");
                    long size = Tlv.ReadU32(metaBytes, pos + 2);
                    if (pos + 6 + size > end) break;
                    if (tag == MetaWrapperTag)
                    {
                        innerStart = pos + 6;
                        innerEnd = pos + 6 + (int)size;
                        break;
                    }
                    if (tag == MetaUnitsTag)
                    {
                        return Encoding.UTF8.GetString(metaBytes, pos + 6, (int)size);
                    }
                    pos += 6 + (int)size;
                }
                if (innerStart < 0) return null;
                start = innerStart;
                end = innerEnd;
            }
            return null;
        }

        /// <summary>Reject a ZIP entry whose declared uncompressed size is
        /// implausible, before any code allocates memory sized off it.</summary>
        public static void ValidateEntrySize(ZipArchiveEntry entry)
        {
            long declared = entry.Length;
            if (declared <= 0) return;

            if (declared > MaxUncompressedEntryBytes)
            {
                throw new SkpParseException(
                    $"ZIP entry '{entry.FullName}' declares {declared} bytes uncompressed, exceeding the {MaxUncompressedEntryBytes}-byte safety ceiling",
                    stage: "zip_extract");
            }

            if (declared >= RatioCheckThresholdBytes)
            {
                long compressed = entry.CompressedLength;
                if (compressed <= 0 || declared / compressed > MaxCompressionRatio)
                {
                    throw new SkpParseException(
                        $"ZIP entry '{entry.FullName}' declares an implausible compression ratio ({declared} bytes from {compressed} bytes compressed) - likely a decompression bomb",
                        stage: "zip_extract");
                }
            }
        }
    }
}
