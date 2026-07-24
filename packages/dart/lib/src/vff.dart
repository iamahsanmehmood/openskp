import 'dart:typed_data';

import 'package:archive/archive.dart';

/// Container-level helpers for the modern (2021+) VFF/ZIP .skp format:
/// header validation, version-string extraction, and locating the embedded
/// ZIP payload. Ported from the steps in Python's _core.full_parse() that
/// run before TLV parsing begins.
class Vff {
  static final Uint8List _headerMagic =
      Uint8List.fromList([0xFF, 0xFE, 0xFF, 0x0E]);
  static final Uint8List _secondMarker = Uint8List.fromList([0xFF, 0xFE, 0xFF]);
  static final Uint8List _zipMagic =
      Uint8List.fromList([0x50, 0x4B, 0x03, 0x04]);

  static bool hasValidHeader(Uint8List data) {
    if (data.length < 4) return false;
    for (int i = 0; i < 4; i++) {
      if (data[i] != _headerMagic[i]) return false;
    }
    return true;
  }

  static int _findBytes(Uint8List data, Uint8List pattern, int start, int end) {
    if (pattern.isEmpty || start < 0) return -1;
    final limit = (end < data.length ? end : data.length) - pattern.length;
    for (int i = start < 0 ? 0 : start; i <= limit; i++) {
      bool match = true;
      for (int j = 0; j < pattern.length; j++) {
        if (data[i + j] != pattern[j]) {
          match = false;
          break;
        }
      }
      if (match) return i;
    }
    return -1;
  }

  /// Extract the "{17.0.18899}"-style version string from the
  /// UTF-16LE-encoded header region, or "unknown" if not found.
  static String extractVersion(Uint8List header) {
    final secondMarker = _findBytes(header, _secondMarker, 4, header.length);
    if (secondMarker <= 0) return 'unknown';

    final verStart = secondMarker + 4;
    if (verStart >= header.length) return 'unknown';

    String verText;
    try {
      final codeUnits = <int>[];
      for (int i = verStart; i + 1 < header.length; i += 2) {
        codeUnits.add(header[i] | (header[i + 1] << 8));
      }
      verText = String.fromCharCodes(codeUnits);
    } catch (_) {
      return 'unknown';
    }

    final braceStart = verText.indexOf('{');
    final braceEnd = verText.indexOf('}');
    if (braceStart >= 0 && braceEnd > braceStart) {
      return verText.substring(braceStart, braceEnd + 1);
    }
    return 'unknown';
  }

  /// Locate the byte offset of the embedded ZIP container ("PK\x03\x04"),
  /// searching only the first [searchLimit] bytes (matching Python's
  /// header-then-4096-byte-chunk search), or -1 if not found.
  static int findZipOffset(Uint8List data, [int searchLimit = 4096]) {
    final limit = searchLimit < data.length ? searchLimit : data.length;
    return _findBytes(data, _zipMagic, 0, limit);
  }

  static Archive openZip(Uint8List data, int zipOffset) {
    final zipBytes = Uint8List.sublistView(data, zipOffset);
    return ZipDecoder().decodeBytes(zipBytes);
  }
}
