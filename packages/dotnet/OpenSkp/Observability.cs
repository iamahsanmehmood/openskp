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

        /// <summary>Leaves geometry the file marks hidden out of a built
        /// scene - a face carrying SketchUp's Hide flag, and a group or
        /// component instance carrying it.
        ///
        /// Off by default, matching TypeScript's respectEdgeVisibility: what
        /// SketchUp draws is a display policy, not a parsing fact, and some
        /// consumers legitimately want every face regardless. The raw parse
        /// always reports the flags, whatever this is set to.
        ///
        /// A hidden instance takes its whole subtree with it, out of the
        /// instance hierarchy and the mesh index as well as out of the
        /// triangles - hiding a group in SketchUp hides everything it
        /// places, so a scene built this way holds only what is drawn. A
        /// caller that needs the hierarchy to stay complete leaves this off
        /// and filters downstream.</summary>
        public bool RespectVisibility { get; set; }

        /// <summary>Paints a face that carries no material, and inherits
        /// none, with the active style's front face color instead of the
        /// color of its tag (layer).
        ///
        /// A tag color exists so SketchUp can render a model in "Color by
        /// Tag" mode, where every object takes the color of the tag it is
        /// labelled with. It is a review mode. Normal display paints an
        /// unpainted face with the style's front face color, so a scene
        /// built with this off reproduces "Color by Tag" rather than what
        /// the modeller sees - the default tag is usually an arbitrary
        /// bright color, which then covers every unpainted surface.
        ///
        /// Off by default, matching TypeScript's fallbackLayerColor, which
        /// resolves the tag color the same way. A file records no active
        /// style, so this takes the first style that declares a front
        /// color, and white when none does.</summary>
        public bool UseStyleFaceColor { get; set; }
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
