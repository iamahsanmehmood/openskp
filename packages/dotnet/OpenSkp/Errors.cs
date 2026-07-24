using System;

namespace OpenSkp
{
    /// <summary>Structured parse failure. Carries *where* a parse failed -
    /// which stage, which top-level record (and how many total), which TLV
    /// tag, which definition - so a stuck or failed model in a production
    /// pipeline can be traced back to an exact location instead of a bare
    /// stack trace. The original exception is always preserved as
    /// <see cref="Exception.InnerException"/>, so inspecting the failure
    /// never loses information, it only adds context.</summary>
    public sealed class SkpParseException : Exception
    {
        /// <summary>Which pipeline stage was running, e.g. "header",
        /// "zip_extract", "tlv_walk", "legacy_walk", "legacy_defs",
        /// "build_scene".</summary>
        public string? Stage { get; }

        /// <summary>Index of the top-level record being processed when the
        /// failure happened (0-based), or null outside the per-record
        /// walk.</summary>
        public long? RecordIndex { get; }

        /// <summary>Total top-level record count for the file, paired with
        /// RecordIndex for an "N of M" position.</summary>
        public long? TotalRecords { get; }

        /// <summary>The TLV tag hex string of the record being processed,
        /// if known.</summary>
        public string? Tag { get; }

        /// <summary>Byte offset into model.dat (or the legacy archive
        /// stream) of the record being processed, if known.</summary>
        public long? Offset { get; }

        /// <summary>The component definition id being built when the
        /// failure happened, if applicable.</summary>
        public long? DefinitionId { get; }

        public SkpParseException(
            string message,
            string? stage = null,
            long? recordIndex = null,
            long? totalRecords = null,
            string? tag = null,
            long? offset = null,
            long? definitionId = null,
            Exception? innerException = null)
            : base(FormatMessage(message, stage, recordIndex, totalRecords, tag, offset, definitionId), innerException)
        {
            Stage = stage;
            RecordIndex = recordIndex;
            TotalRecords = totalRecords;
            Tag = tag;
            Offset = offset;
            DefinitionId = definitionId;
        }

        private static string FormatMessage(
            string message, string? stage, long? recordIndex, long? totalRecords,
            string? tag, long? offset, long? definitionId)
        {
            var parts = new System.Collections.Generic.List<string> { message };
            if (stage != null) parts.Add($"stage={stage}");
            if (recordIndex.HasValue && totalRecords.HasValue) parts.Add($"record={recordIndex}/{totalRecords}");
            if (tag != null) parts.Add($"tag={tag}");
            if (offset.HasValue) parts.Add($"offset=0x{offset.Value:X}");
            if (definitionId.HasValue) parts.Add($"definitionId={definitionId}");
            return string.Join(" | ", parts);
        }
    }
}
