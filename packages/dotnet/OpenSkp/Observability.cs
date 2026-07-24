using System;

namespace OpenSkp
{
    /// <summary>Mirrors Microsoft.Extensions.Logging's LogLevel split without
    /// taking a dependency on it: "Debug" for fine-grained per-record/
    /// per-instance detail, "Information" for start/stage/completion
    /// summaries.</summary>
    public enum SkpLogLevel
    {
        Debug,
        Information,
    }

    /// <summary>One progress update during a long parse/scene-bake walk.</summary>
    public readonly struct SkpParseProgress
    {
        /// <summary>Which stage is reporting, e.g. "tlv_walk",
        /// "legacy_defs", "build_scene".</summary>
        public string Stage { get; }

        /// <summary>Units completed so far (records, definitions, or
        /// instances, depending on Stage).</summary>
        public long Current { get; }

        /// <summary>Total units expected for this stage.</summary>
        public long Total { get; }

        public SkpParseProgress(string stage, long current, long total)
        {
            Stage = stage;
            Current = current;
            Total = total;
        }
    }

    /// <summary>Optional progress/log hooks for a parse or scene-bake call.
    /// openskp never logs or prints on its own - callers that want
    /// visibility into a parse (progress through a large file, which stage
    /// is running, when it completes) pass a <see cref="SkpParseOptions"/>
    /// with <see cref="Progress"/>/<see cref="OnLog"/> set, wiring into
    /// whatever logging/monitoring the host application already uses.
    /// Silent by default: with no options, nothing is called.</summary>
    public sealed class SkpParseOptions
    {
        /// <summary>Reports periodically (every <see cref="ParseTuning.ProgressInterval"/>
        /// units) during a long walk, using the BCL's standard IProgress&lt;T&gt;
        /// convention - so a caller can report "N of M processed" without any
        /// extra pass over the data.</summary>
        public IProgress<SkpParseProgress>? Progress { get; set; }

        /// <summary>Called for start/stage/completion messages.</summary>
        public Action<SkpLogLevel, string>? OnLog { get; set; }
    }

    /// <summary>Shared tuning constants for progress reporting.</summary>
    public static class ParseTuning
    {
        /// <summary>How often (in records/definitions/instances) to report
        /// progress during a long walk - coarse enough that it costs
        /// nothing on a 300k-definition file. Mirrors the Python port's
        /// _PROGRESS_INTERVAL.</summary>
        public const int ProgressInterval = 500;
    }

    internal static class Observability
    {
        public static void Log(SkpParseOptions? options, SkpLogLevel level, string message)
        {
            options?.OnLog?.Invoke(level, message);
        }

        public static void Progress(SkpParseOptions? options, string stage, long current, long total)
        {
            options?.Progress?.Report(new SkpParseProgress(stage, current, total));
        }
    }
}
