import 'dart:convert';
import 'dart:typed_data';

import 'errors.dart';

/// Offset and payload size of a single TLV record header.
class TlvHeader {
  final int offset;
  final int size;

  const TlvHeader(this.offset, this.size);

  @override
  bool operator ==(Object other) =>
      other is TlvHeader && offset == other.offset && size == other.size;

  @override
  int get hashCode => Object.hash(offset, size);
}

const int _tlvWrap = 0x100000000;

class _Run {
  final int offset;
  final int size;
  int count;

  _Run(this.offset, this.size, this.count);

  int get step => size + 6;
  int get end => offset + count * (size + 6);
}

class _Chain {
  final List<_Run> runs = <_Run>[];
  final List<TlvHeader> candidates = <TlvHeader>[];
  bool indexed = false;

  int get end => runs.last.end;

  int runAt(int p) {
    var lo = 0;
    var hi = runs.length;
    while (lo < hi) {
      final mid = lo + ((hi - lo) >> 1);
      if (p < runs[mid].offset) {
        hi = mid;
      } else {
        lo = mid + 1;
      }
    }
    return lo - 1;
  }

  bool contains(int p) {
    if (p < runs.first.offset || p >= end) return false;
    final r = runs[runAt(p)];
    return (p - r.offset) % r.step == 0;
  }

  int clip(int limit) {
    final e = end;
    if (limit >= e) return e;
    final r = runs[runAt(limit)];
    final step = r.step;
    return r.offset + ((limit - r.offset) ~/ step) * step;
  }

  void append(int p, int size) {
    if (runs.isNotEmpty && runs.last.size == size && end == p) {
      runs.last.count += 1;
    } else {
      runs.add(_Run(p, size, 1));
    }
  }
}

class _View {
  final _Chain chain;
  final int begin;
  final int end;
  const _View(this.chain, this.begin, this.end);
}

class _Plan {
  final List<_View> views;
  final int stop;
  const _Plan(this.views, this.stop);
}

class _HeaderResolver {
  final int Function(int) _length;
  final bool Function(int) _container;
  final int _limit;
  int _attempts = 256;
  final List<_Chain> _chains = <_Chain>[];
  final Map<(int, int, int), _Plan?> _decisions = <(int, int, int), _Plan?>{};

  _HeaderResolver(this._length, this._container, this._limit);

  _Chain? _chainAt(int p) {
    for (var i = _chains.length - 1; i >= 0; i--) {
      if (_chains[i].contains(p)) return _chains[i];
    }
    return null;
  }

  _Plan _ordinary(int start, int end) {
    final views = <_View>[];
    var p = start;
    while (end - p >= 6) {
      final cached = _chainAt(p);
      if (cached != null) {
        final next = cached.clip(end);
        if (next > p) views.add(_View(cached, p, next));
        p = next;
        if (p < cached.end) return _Plan(views, p);
        continue;
      }
      final chain = _Chain();
      final begin = p;
      var probes = 0;
      var joined = false;
      while (end - p >= 6) {
        if (probes != 0 && (probes & 1023) == 0 && _chainAt(p) != null) {
          joined = true;
          break;
        }
        final size = _length(p);
        if (size > end - p - 6) break;
        chain.append(p, size);
        p += 6 + size;
        probes++;
      }
      if (p > begin) {
        _chains.add(chain);
        views.add(_View(chain, begin, p));
      }
      if (!joined) return _Plan(views, p);
    }
    return _Plan(views, p);
  }

  void _index(_Chain chain) {
    if (chain.indexed) return;
    chain.indexed = true;
    for (final r in chain.runs) {
      final step = r.step;
      for (var k = 0; k < r.count; k++) {
        final p = r.offset + k * step;
        if ((_limit - p) - step < _tlvWrap) return;
        if (_container(p)) chain.candidates.add(TlvHeader(p, r.size));
      }
    }
  }

  static int _lowerBound(List<TlvHeader> candidates, int begin) {
    var lo = 0;
    var hi = candidates.length;
    while (lo < hi) {
      final mid = lo + ((hi - lo) >> 1);
      if (candidates[mid].offset < begin) {
        lo = mid + 1;
      } else {
        hi = mid;
      }
    }
    return lo;
  }

  static _Plan _replace(_Plan original, TlvHeader h, int size, _Plan suffix, int end) {
    final views = <_View>[];
    for (final v in original.views) {
      if (v.begin >= h.offset) break;
      views.add(_View(v.chain, v.begin, v.end < h.offset ? v.end : h.offset));
      if (v.end >= h.offset) break;
    }
    final extended = _Chain()..append(h.offset, size);
    views.add(_View(extended, h.offset, h.offset + 6 + size));
    views.addAll(suffix.views);
    return _Plan(views, end);
  }

  _Plan? _solve(int start, int end, int depth) {
    final normal = _ordinary(start, end);
    if (normal.stop == end) return normal;
    final key = (start, end, depth);
    if (_decisions.containsKey(key)) return _decisions[key];
    for (final v in normal.views) {
      _index(v.chain);
      final candidates = v.chain.candidates;
      for (var idx = _lowerBound(candidates, v.begin);
          idx < candidates.length && candidates[idx].offset < v.end;
          idx++) {
        final h = candidates[idx];
        final available = end - h.offset - 6;
        if (available - h.size < _tlvWrap) break;
        var n = h.size + _tlvWrap;
        while (n <= available) {
          if (depth >= Tlv.maxDepth || _attempts == 0) {
            throw SkpParseException(
              'TLV length recovery exceeded its depth or attempt limit',
              stage: 'tlv_walk',
              offset: h.offset,
            );
          }
          _attempts--;
          final next = h.offset + 6 + n;
          final suffix = _solve(next, end, depth + 1);
          if (suffix != null && _solve(h.offset + 6, next, depth + 1) != null) {
            final result = _replace(normal, h, n, suffix, end);
            _decisions[key] = result;
            return result;
          }
          if (available - n < _tlvWrap) break;
          n += _tlvWrap;
        }
      }
    }
    _decisions[key] = null;
    return null;
  }

  static List<TlvHeader> _materialize(_Plan plan) {
    final out = <TlvHeader>[];
    for (final v in plan.views) {
      var i = v.chain.runAt(v.begin);
      while (i < v.chain.runs.length && v.chain.runs[i].offset < v.end) {
        final r = v.chain.runs[i++];
        final step = r.step;
        final rEnd = r.end;
        final stop = v.end < rEnd ? v.end : rEnd;
        final k0 = v.begin <= r.offset ? 0 : (v.begin - r.offset) ~/ step;
        final k1 = stop <= r.offset ? 0 : (stop - r.offset) ~/ step;
        for (var k = k0; k < k1; k++) {
          out.add(TlvHeader(r.offset + k * step, r.size));
        }
      }
    }
    return out;
  }

  List<TlvHeader> headers(int start, int end) {
    final plan = _solve(start, end, 0);
    if (plan != null) return _materialize(plan);
    return _materialize(_ordinary(start, end));
  }
}

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
  /// VFF (2021+) model.dat binary structure uses Type-Length-Value (TLV) records.
  /// Most TLV tags carry raw leaf binary/string payloads, but container tags contain
  /// nested sub-TLV nodes (e.g. definitions, drawing elements, component instances).
  /// [containerTags] lists every tag hex ID that the TLV parser must recursively traverse.
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
    // VFF construction guide lists, entity nodes and drawbase wrappers.
    '9113', '9213', '6942', '6C42', '6842',
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

  /// Deepest container nesting the parser follows. A real model.dat nests
  /// about a dozen levels (the deepest file checked reaches 11). A hostile
  /// file can nest tens of thousands, which would overflow the stack before
  /// anything could be reported, so deeper input throws a
  /// [SkpParseException] instead.
  static const int maxDepth = 64;

  static List<TlvHeader>? wrappedModelRecords(
      int end, int Function(int) tag, int Function(int) length) {
    if (end < 6 ||
        end - 6 < _tlvWrap ||
        tag(0) != 0x01f4 ||
        length(0) != ((end - 6) & 0xFFFFFFFF)) {
      return null;
    }

    final storedRootEnd = 6 + length(0);
    var storedParentEnd = storedRootEnd;
    var contradictsStoredLengths = false;
    var pos = 6;

    int checkedLength() {
      final size = length(pos);
      bool crosses(int limit) =>
          pos <= limit && limit - pos >= 6 && size > limit - pos - 6;
      if (crosses(storedRootEnd) || crosses(storedParentEnd)) {
        contradictsStoredLengths = true;
      }
      return size;
    }

    bool has(int expected) => end - pos >= 6 && tag(pos) == expected;

    bool advance() {
      if (end - pos < 6) return false;
      final size = checkedLength();
      if (size > end - pos - 6) return false;
      pos += 6 + size;
      return true;
    }

    while (!has(0x01f9)) {
      if (!advance()) return null;
    }
    final catalog = pos;
    final wrappers = <int>[];
    const path = <int>[0x01f9, 0x1770, 0x1771];
    for (var i = 0; i < path.length; i++) {
      if (!has(path[i])) return null;
      wrappers.add(pos);
      final size = checkedLength();
      if (pos <= storedParentEnd &&
          storedParentEnd - pos >= 6 &&
          size <= storedParentEnd - pos - 6) {
        storedParentEnd = pos + 6 + size;
      }
      pos += 6;
      if (has(0x0063) && !advance()) return null;
    }
    final definitions = pos;
    while (has(0x157c)) {
      if (!advance()) return null;
    }
    final entities = pos;
    if (definitions == entities || !has(0x01f6)) return null;
    for (final offset in wrappers) {
      if (length(offset) != ((entities - offset - 6) & 0xFFFFFFFF)) {
        return null;
      }
    }
    storedParentEnd = storedRootEnd;
    while (pos < end) {
      if (!advance()) return null;
    }
    if (!contradictsStoredLengths) return null;

    final records = <TlvHeader>[];
    void append(int begin, int stop) {
      for (var p = begin; p < stop;) {
        final size = length(p);
        records.add(TlvHeader(p, size));
        p += 6 + size;
      }
    }

    append(6, catalog);
    append(definitions, entities);
    append(entities, end);
    return records;
  }

  static List<TlvHeader> tlvHeaders(int start, int end,
      int Function(int) length, bool Function(int) container) {
    if (start < 0 || start > end) throw RangeError('invalid TLV range');
    if (end - start >= _tlvWrap) {
      return _HeaderResolver(length, container, end).headers(start, end);
    }
    final out = <TlvHeader>[];
    var p = start;
    while (end - p >= 6) {
      final n = length(p);
      if (n > end - p - 6) break;
      out.add(TlvHeader(p, n));
      p += 6 + n;
    }
    return out;
  }

  static List<TlvHeader> tlvHeadersBuffer(Uint8List data, int start, int end,
      [Set<String>? tags]) {
    if (start < 0 || start > end || end > data.length) {
      throw RangeError('invalid TLV range');
    }
    final containerTagsSet = tags ?? containerTags;
    return tlvHeaders(
      start,
      end,
      (p) => readU32(data, p + 2),
      (p) => containerTagsSet.contains(_tagHex(data, p)),
    );
  }

  static List<TlvNode> parseRecursive(Uint8List data, int start, int end,
      [Set<String>? tags, int depth = 0]) {
    if (start < 0 || start > end || end > data.length) {
      throw RangeError('invalid TLV range');
    }
    final containerTagsSet = tags ?? containerTags;
    final elements = <TlvNode>[];
    for (final header in tlvHeadersBuffer(data, start, end, containerTagsSet)) {
      final pos = header.offset;
      final size = header.size;
      final tagHex = _tagHex(data, pos);
      final isContainer = containerTagsSet.contains(tagHex);
      List<TlvNode> children = const [];
      if (isContainer && size > 0) {
        if (depth >= maxDepth) {
          throw SkpParseException(
            'TLV records nest deeper than $maxDepth levels',
            stage: 'tlv_walk',
            tag: tagHex,
            offset: pos,
          );
        }
        children = parseRecursive(
            data, pos + 6, pos + 6 + size, containerTagsSet, depth + 1);
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
    }
    return elements;
  }

  /// Walk a raw payload as a flat TLV sequence (no container-tag awareness);
  /// returns (tag, body) pairs.
  static List<(String, Uint8List)> parseFlat(Uint8List payload) {
    final result = <(String, Uint8List)>[];
    int pos = 0;
    while (payload.length - pos >= 6) {
      final tag = _tagHex(payload, pos);
      final size = readU32(payload, pos + 2);
      if (size > payload.length - pos - 6) break;
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
  static List<(String, int, int)> _flatHeaders(Uint8List data, int start, int end,
      [Set<String>? tags]) {
    if (end - start >= _tlvWrap) {
      return tlvHeadersBuffer(data, start, end, tags)
          .map((h) => (_tagHex(data, h.offset), h.offset, h.size))
          .toList();
    }
    final headers = <(String, int, int)>[];
    int pos = start;
    while (end - pos >= 6) {
      final tagHex = _tagHex(data, pos);
      final size = readU32(data, pos + 2);
      if (size > end - pos - 6) break;
      headers.add((tagHex, pos, size));
      pos += 6 + size;
    }
    return headers;
  }

  /// Throw if a top-level record declares more bytes than the data holds.
  ///
  /// [_flatHeaders] stops quietly at such a record, so a model.dat cut short
  /// anywhere inside its single F401 record would parse as a model with no
  /// geometry and no error. Real files end exactly where their last
  /// top-level record does. Records nested inside it are deliberately left
  /// lenient: in real files some container payloads end with bytes that are
  /// not records, and a strict check there would reject valid models.
  static void _requireCompleteTopLevel(
      Uint8List data, int start, int end, List<(String, int, int)> headers) {
    final pos =
        headers.isEmpty ? start : headers.last.$2 + 6 + headers.last.$3;
    if (end - pos >= 6) {
      final size = readU32(data, pos + 2);
      throw SkpParseException(
        'model.dat is truncated: the top-level record declares $size bytes '
        'but only ${end - pos - 6} remain',
        stage: 'tlv_walk',
        tag: _tagHex(data, pos),
        offset: pos,
      );
    }
  }

  static List<(String, int, int)>? _unwrapDefinitionsContainer(
      Uint8List data, int offset, int size, Set<String> containerTagsSet) {
    var level = _flatHeaders(data, offset + 6, offset + 6 + size, containerTagsSet);
    for (final expected in const ['7017', '7117']) {
      (String, int, int)? found;
      for (final h in level) {
        if (h.$1 == expected) {
          found = h;
          break;
        }
      }
      if (found == null) return null;
      level = _flatHeaders(
          data, found.$2 + 6, found.$2 + 6 + found.$3, containerTagsSet);
    }
    final defs = level.where((h) => h.$1 == '7C15').toList();
    return defs.isNotEmpty ? defs : null;
  }

  /// Yield `(index, total, node)` for each top-level TLV record's
  /// fully-recursed node one at a time, transparently unwrapping a lone
  /// "F401" wrapper - without ever materializing more than one top-level
  /// subtree simultaneously. `total` (the top-level sibling count) comes
  /// for free from the same cheap header scan that drives the loop, so
  /// callers can report "N of total" progress with no extra pass over the
  /// file. Each yielded node is safe to discard (drop all references) once
  /// the caller is done with it, before the next one is produced - that's
  /// what keeps peak memory bounded by the size of the single largest
  /// top-level record instead of the whole file (real production files can
  /// have 100k+ separate definitions).
  static Iterable<(int, int, TlvNode)> iterTopLevelLazy(Uint8List data, int start, int end, [Set<String>? tags]) sync* {
    final containerTagsSet = tags ?? containerTags;

    List<(String, int, int)> headers;
    List<TlvHeader>? wrapped;
    if (start == 0 &&
        end == data.length &&
        containerTagsSet.contains('F401') &&
        end >= 6 &&
        end - 6 >= _tlvWrap) {
      wrapped = wrappedModelRecords(
        end,
        (p) => readU16(data, p),
        (p) => readU32(data, p + 2),
      );
    }
    if (wrapped != null) {
      headers = wrapped
          .map((h) => (_tagHex(data, h.offset), h.offset, h.size))
          .toList();
    } else {
      headers = _flatHeaders(data, start, end, containerTagsSet);
      _requireCompleteTopLevel(data, start, end, headers);
      if (headers.length == 1 && headers[0].$1 == 'F401') {
        final (_, f401Offset, f401Size) = headers[0];
        headers = _flatHeaders(
            data, f401Offset + 6, f401Offset + 6 + f401Size, containerTagsSet);
      }
      if (containerTagsSet.contains('F901')) {
        final expanded = <(String, int, int)>[];
        for (final h in headers) {
          if (h.$1 == 'F901') {
            final defs =
                _unwrapDefinitionsContainer(data, h.$2, h.$3, containerTagsSet);
            if (defs != null) {
              expanded.addAll(defs);
              continue;
            }
          }
          expanded.add(h);
        }
        headers = expanded;
      }
    }

    final total = headers.length;
    var index = 0;
    for (final (_, offset, size) in headers) {
      final recordEnd = offset + 6 + size;
      final nodes = parseRecursive(data, offset, recordEnd, containerTagsSet);
      if (nodes.isNotEmpty) {
        yield (index, total, nodes[0]);
      }
      index++;
    }
  }
}
