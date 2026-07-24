/// Optional progress/log hooks for long-running parses.
///
/// openskp never logs or prints on its own - callers that want visibility
/// into a parse (progress through a large file, which stage is running,
/// when it completes) pass a [ParseOptions] with [ParseOptions.onProgress]
/// / [ParseOptions.onLog] callbacks that plug into whatever logging/
/// monitoring the host application already uses. Silent by default: with
/// no options, nothing is called.
library;

/// Mirrors the Python port's logging.DEBUG/logging.INFO split: "debug" for
/// fine-grained per-record/per-instance detail, "info" for start/complete
/// summaries.
enum SkpLogLevel { debug, info }

/// One progress update during a long parse/scene-bake walk.
class ParseProgress {
  /// Which stage is reporting, e.g. "tlv_walk", "legacy_defs",
  /// "build_scene".
  final String stage;

  /// Units completed so far (records, definitions, or instances,
  /// depending on [stage]).
  final int current;

  /// Total units expected for this stage.
  final int total;

  const ParseProgress(this.stage, this.current, this.total);
}

/// Optional progress/log hooks for a parse or scene-bake call.
class ParseOptions {
  /// Called periodically (every [progressInterval] units) during a long
  /// walk, so a caller can report "N of M processed" without any extra
  /// pass over the data.
  final void Function(ParseProgress info)? onProgress;

  /// Called for start/stage/completion messages.
  final void Function(SkpLogLevel level, String message)? onLog;

  const ParseOptions({this.onProgress, this.onLog});
}

/// How often (in records/definitions/instances) to call onProgress during a
/// long walk - coarse enough that it costs nothing on a 300k-definition
/// file. Mirrors the Python port's `_PROGRESS_INTERVAL`.
const int progressInterval = 500;

void emitLog(ParseOptions? options, SkpLogLevel level, String message) {
  options?.onLog?.call(level, message);
}

void emitProgress(ParseOptions? options, String stage, int current, int total) {
  options?.onProgress?.call(ParseProgress(stage, current, total));
}
