/// Optional progress/log hooks for long-running parses.
///
/// openskp never logs or prints on its own - callers that want visibility
/// into a parse (progress through a large file, which stage is running,
/// when it completes) pass a [ParseOptions] with [ParseOptions.onProgress]
/// / [ParseOptions.onLog] callbacks that plug into whatever logging/
/// monitoring the host application already uses. Silent by default: with
/// no options, nothing is called.
library;

/// Mirrors the Python port's logging.DEBUG/logging.INFO/logging.WARNING
/// split: "debug" for fine-grained per-record/per-instance detail, "info" for
/// start/complete summaries, and "warning" for non-fatal partial-read or
/// export warnings.
enum SkpLogLevel { debug, info, warning }

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

  /// Colour of a face side with no paint on it or on any enclosing instance
  /// when building a scene: `'style'` (default) uses the model's front and
  /// back face colours (`SkpModel.faceFrontColor` / `faceBackColor`), while
  /// `'tag'` uses the colour of the face's tag as one double-sided primitive.
  final String unpaintedFaces;

  /// Bake loose edges (edges no face uses) into `Scene.curveSets` and
  /// `InstancedScene.curveResources`. Defaults to `true`; pass `false` to skip
  /// loose-edge chaining and arc solving.
  final bool includeCurveSets;

  /// Keep a texture that is not PNG or JPEG in `Scene.textures` and
  /// `InstancedScene.textures` in its own format (`image/tiff`, `image/gif`,
  /// `image/bmp`, `image/x-tga`, `image/webp`, `image/vnd.adobe.photoshop`,
  /// `image/vnd.ms-dds`, `image/jp2`) instead of leaving it out with a
  /// warning. Off by default because glTF carries only PNG and JPEG.
  final bool originalTextures;

  const ParseOptions({
    this.onProgress,
    this.onLog,
    this.unpaintedFaces = 'style',
    this.includeCurveSets = true,
    this.originalTextures = false,
  });
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
