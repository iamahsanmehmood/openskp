/// Structured parse errors.
///
/// [SkpParseException] carries *where* a parse failed - which stage, which
/// top-level record (and how many total), which TLV tag, which definition -
/// so a stuck or failed model in a production pipeline can be traced back to
/// an exact location instead of a bare stack trace.
///
/// The original error is always preserved as [cause], so inspecting the
/// failure never loses information, it only adds context.
class SkpParseException implements Exception {
  /// Which pipeline stage was running, e.g. "header", "zip_extract",
  /// "tlv_walk", "legacy_walk", "legacy_defs", "build_scene".
  final String? stage;

  /// Index of the top-level record being processed when the failure
  /// happened (0-based), or null outside the per-record walk.
  final int? recordIndex;

  /// Total top-level record count for the file, paired with [recordIndex]
  /// for an "N of M" position.
  final int? totalRecords;

  /// The TLV tag hex string of the record being processed, if known.
  final String? tag;

  /// Byte offset into model.dat (or the legacy archive stream) of the
  /// record being processed, if known.
  final int? offset;

  /// The component definition id being built when the failure happened,
  /// if applicable.
  final int? definitionId;

  /// The original error that triggered this one, if any.
  final Object? cause;

  final String message;

  SkpParseException(
    this.message, {
    this.stage,
    this.recordIndex,
    this.totalRecords,
    this.tag,
    this.offset,
    this.definitionId,
    this.cause,
  });

  @override
  String toString() {
    final parts = <String>[message];
    if (stage != null) parts.add('stage=$stage');
    if (recordIndex != null && totalRecords != null) {
      parts.add('record=$recordIndex/$totalRecords');
    }
    if (tag != null) parts.add('tag=$tag');
    if (offset != null) parts.add('offset=0x${offset!.toRadixString(16).toUpperCase()}');
    if (definitionId != null) parts.add('definitionId=$definitionId');
    return parts.join(' | ');
  }
}
