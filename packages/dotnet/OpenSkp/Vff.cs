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
    }
}
