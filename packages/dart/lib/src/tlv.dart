import 'dart:convert';
import 'dart:typed_data';

/// A single Tag-Length-Value node in the binary parse tree.
class TlvNode {
  final int offset;
  final String tag;
  final int size;
  final List<TlvNode> children;
  final Uint8List payload;

  TlvNode({
    required this.offset,
    required this.tag,
    required this.size,
    List<TlvNode>? children,
    Uint8List? payload,
  })  : children = children ?? const [],
        payload = payload ?? Uint8List(0);
}

/// Low-level TLV (Tag-Length-Value) binary parsing helpers, ported from
/// Python's _core.py. All multi-byte reads are explicitly little-endian.
class Tlv {
  static const Set<String> containerTags = {
    'F401', 'F701', 'D430', 'D530', 'C832',
    '7C15', '8813', '8913', '8A13', '8B13', '8C13', '8D13', '4C1D', '6419',
    'F901', '7017', '7117', 'D007', 'C409', '9411', '9511', '0F01',
    '384A', 'B80B', '9713', '2C4C', 'AC0D', 'AE0D', 'F601', 'F801',
    '983A', '993A', '8C3C', '8D3C',
    // Image-entity placement: an Image placed in the model wraps a standard
    // 6419 instance node inside 9013 -> 401F. Without these two containers,
    // that inner instance stays buried in an opaque payload and the image
    // definition looks "never placed".
    '9013', '401F',
  };

  static int readU16(Uint8List data, int offset) {
    return ByteData.sublistView(data, offset, offset + 2)
        .getUint16(0, Endian.little);
  }

  static int readU32(Uint8List data, int offset) {
    return ByteData.sublistView(data, offset, offset + 4)
        .getUint32(0, Endian.little);
  }

  static int readI32(Uint8List data, int offset) {
    return ByteData.sublistView(data, offset, offset + 4)
        .getInt32(0, Endian.little);
  }

  static double readF64(Uint8List data, int offset) {
    return ByteData.sublistView(data, offset, offset + 8)
        .getFloat64(0, Endian.little);
  }

  static int parseVarInt(Uint8List data, int offset, int length) {
    int val = 0;
    for (int i = 0; i < length; i++) {
      val |= data[offset + i] << (8 * i);
    }
    return val;
  }

  static String toHexUpper(Uint8List data) {
    final buf = StringBuffer();
    for (final b in data) {
      buf.write(b.toRadixString(16).padLeft(2, '0').toUpperCase());
    }
    return buf.toString();
  }

  static String _tagHex(Uint8List data, int offset) {
    return data[offset].toRadixString(16).padLeft(2, '0').toUpperCase() +
        data[offset + 1].toRadixString(16).padLeft(2, '0').toUpperCase();
  }

  static List<TlvNode> parseRecursive(Uint8List data, int start, int end,
      [Set<String>? tags]) {
    final containerTagsSet = tags ?? containerTags;
    final elements = <TlvNode>[];
    int pos = start;
    while (pos < end - 6) {
      final tagHex = _tagHex(data, pos);
      final size = readU32(data, pos + 2);
      if (pos + 6 + size > end) {
        break;
      }
      final isContainer = containerTagsSet.contains(tagHex);
      List<TlvNode> children = const [];
      if (isContainer && size > 0) {
        children =
            parseRecursive(data, pos + 6, pos + 6 + size, containerTagsSet);
      }
      Uint8List payload = Uint8List(0);
      if (children.isEmpty && size > 0) {
        payload = Uint8List.sublistView(data, pos + 6, pos + 6 + size);
      }
      elements.add(TlvNode(
          offset: pos,
          tag: tagHex,
          size: size,
          children: children,
          payload: payload));
      pos += 6 + size;
    }
    return elements;
  }

  /// Walk a raw payload as a flat TLV sequence (no container-tag awareness);
  /// returns (tag, body) pairs.
  static List<(String, Uint8List)> parseFlat(Uint8List payload) {
    final result = <(String, Uint8List)>[];
    int pos = 0;
    while (pos <= payload.length - 6) {
      final tag = _tagHex(payload, pos);
      final size = readU32(payload, pos + 2);
      if (pos + 6 + size > payload.length) break;
      final body = Uint8List.sublistView(payload, pos + 6, pos + 6 + size);
      result.add((tag, body));
      pos += 6 + size;
    }
    return result;
  }

  static Uint8List? findFlat(List<(String, Uint8List)> seq, String tag) {
    for (final (t, body) in seq) {
      if (t == tag) return body;
    }
    return null;
  }

  static String decodeUtf8(Uint8List bytes) {
    return utf8.decode(bytes, allowMalformed: true);
  }

  /// Scan [start, end) for direct-child (tag, offset, size) headers only,
  /// without recursing into any container - O(sibling count), not O(total
  /// node count). Used by iterTopLevelLazy to locate top-level records one
  /// at a time.
  static List<(String, int, int)> _flatHeaders(Uint8List data, int start, int end) {
    final headers = <(String, int, int)>[];
    int pos = start;
    while (pos < end - 6) {
      final tagHex = _tagHex(data, pos);
      final size = readU32(data, pos + 2);
      if (pos + 6 + size > end) break;
      headers.add((tagHex, pos, size));
      pos += 6 + size;
    }
    return headers;
  }

  /// Yield each top-level TLV record's fully-recursed node one at a time,
  /// transparently unwrapping a lone "F401" wrapper - without ever
  /// materializing more than one top-level subtree simultaneously. Each
  /// yielded node is safe to discard (drop all references) once the caller
  /// is done with it, before the next one is produced - that's what keeps
  /// peak memory bounded by the size of the single largest top-level record
  /// instead of the whole file (real production files can have 100k+
  /// separate definitions).
  static Iterable<TlvNode> iterTopLevelLazy(Uint8List data, int start, int end, [Set<String>? tags]) sync* {
    final containerTagsSet = tags ?? containerTags;

    var headers = _flatHeaders(data, start, end);
    if (headers.length == 1 && headers[0].$1 == 'F401') {
      final (_, f401Offset, f401Size) = headers[0];
      headers = _flatHeaders(data, f401Offset + 6, f401Offset + 6 + f401Size);
    }

    for (final (_, offset, size) in headers) {
      final recordEnd = offset + 6 + size;
      final nodes = parseRecursive(data, offset, recordEnd, containerTagsSet);
      if (nodes.isNotEmpty) {
        yield nodes[0];
      }
    }
  }
}
