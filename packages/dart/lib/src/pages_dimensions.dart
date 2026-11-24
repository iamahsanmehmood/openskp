import 'dart:convert';
import 'dart:typed_data';

import 'geometry.dart';
import 'model.dart';
import 'tlv.dart';
import 'transforms.dart';

/// VFF (2021+) scenes ("pages") and linear dimensions. Ported from Python's
/// _core.py (PR #190) - see that module's _scan_vertex_positions /
/// _scan_instance_transforms / _parse_dimensions / _find_page_node /
/// _parse_pages for the byte-format details this file mirrors.

class RawPage {
  String name = '';
  (double, double, double)? eye;
  (double, double, double)? target;
  (double, double, double)? up;
  double fov = 35.0;
  bool parallel = false;
  double orthoHeight = 0.0;
  final List<int> hiddenLayerIds = [];
  final List<int> hiddenEntityIds = [];
}

class RawDimension {
  (double, double, double) a;
  (double, double, double) b;
  double offset = 0.0;
  (double, double, double)? planeX;
  (double, double, double)? normal;
  (double, double, double)? offsetVector;
  String layer = '';
  bool hidden = false;
  String text = '';

  RawDimension({required this.a, required this.b});
}

// ── flat TLV, integer tags ────────────────────────────────────────────────
// A second, deliberately separate flat-TLV reader from Tlv.parseFlat/
// findFlat: those use the STRING byte-order tag convention the main tree
// already relies on (e.g. "C409"), while the sub-records this feature reads
// (5208, 520A, 53FC, 5BCD, ...) are most directly and safely ported from
// Python's own _tlv_items/_tlv_find (which read the tag as a little-endian
// uint16) by keeping the SAME integer convention here, copying Python's
// numeric constants byte-for-byte rather than hand-converting each one to
// the swapped string form.

class _FlatTlvItem {
  final int tag;
  final Uint8List payload;
  _FlatTlvItem(this.tag, this.payload);
}

List<_FlatTlvItem>? _tlvItemsInt(Uint8List? buf) {
  if (buf == null) return null;
  final items = <_FlatTlvItem>[];
  int off = 0;
  final n = buf.length;
  while (off < n) {
    if (off + 6 > n) return null;
    final tag = buf[off] | (buf[off + 1] << 8);
    final ln = Tlv.readU32(buf, off + 2);
    if (tag == 0 || off + 6 + ln > n) return null;
    items.add(
        _FlatTlvItem(tag, Uint8List.sublistView(buf, off + 6, off + 6 + ln)));
    off += 6 + ln;
  }
  return items;
}

Uint8List? _tlvFindInt(List<_FlatTlvItem>? items, int tag) {
  if (items == null) return null;
  for (final it in items) {
    if (it.tag == tag) return it.payload;
  }
  return null;
}

Uint8List _stripDe05(Uint8List p) {
  if (p.length >= 2 && p[0] == 0xDE && p[1] == 0x05) {
    final idlen = Tlv.readU32(p, 2);
    return Uint8List.sublistView(p, 6, 6 + idlen);
  }
  return p;
}

int _findBytes(Uint8List data, Uint8List needle, int start) {
  final nlen = needle.length;
  final limit = data.length - nlen;
  outer:
  for (int i = start; i <= limit; i++) {
    for (int j = 0; j < nlen; j++) {
      if (data[i + j] != needle[j]) continue outer;
    }
    return i;
  }
  return -1;
}

/// Accumulate every vertex's persistent id (hex) -> (x, y, z) inches. A
/// vertex is a "C409" record: "DC05" holds its persistent id (the "DE05"
/// var-int payload), "C509" its 3xf64 position. Dimension connection points
/// reference geometry by this id. Called once per top-level record -
/// [Core.fullParse] streams the TLV tree and never holds it whole.
void scanVertexPositions(
    TlvNode top, Map<String, (double, double, double)> id2pos) {
  void walk(List<TlvNode> nodes) {
    for (final el in nodes) {
      if (el.tag == 'C409') {
        final dc05 = Geometry.findChildTag(el.children, 'DC05');
        final c509 = Geometry.findChildTag(el.children, 'C509');
        if (dc05 != null && c509 != null && c509.payload.length == 24) {
          final idb = _stripDe05(dc05.payload);
          id2pos[Tlv.toHexUpper(idb)] = (
            Tlv.readF64(c509.payload, 0),
            Tlv.readF64(c509.payload, 8),
            Tlv.readF64(c509.payload, 16),
          );
        }
      }
      if (el.children.isNotEmpty) walk(el.children);
    }
  }

  walk([top]);
}

/// Accumulate each instance's persistent id (hex) -> its WORLD transform (a
/// 13-double matrix, or an empty list for the identity/no-transform case),
/// walking the instance tree and composing parent x local at every "6419".
/// Per top-level record, like [scanVertexPositions] - an instance chain
/// never crosses top-level records.
///
/// A dimension connects to geometry INSIDE a placed component; its
/// connection reference names the vertex AND the instance holding it. The
/// vertex position is definition-local, so it must be lifted to world by
/// the instance's transform for the dimension to land where the author
/// drew it.
void scanInstanceTransforms(TlvNode top, Map<String, List<double>> world) {
  void walk(List<TlvNode> nodes, List<double> parent) {
    for (final el in nodes) {
      if (el.tag == '6419') {
        final d007 = Geometry.findChildTag(el.children, 'D007');
        final dc05 =
            d007 != null ? Geometry.findChildTag(d007.children, 'DC05') : null;
        final iid =
            dc05 != null ? Tlv.toHexUpper(_stripDe05(dc05.payload)) : null;

        final m = Geometry.findChildTag(el.children, '6619');
        List<double>? mat;
        if (m != null && m.payload.length == 104) {
          mat = [for (int i = 0; i < 13; i++) Tlv.readF64(m.payload, i * 8)];
        }
        final here =
            mat != null ? Transforms.multiplyMatrices(parent, mat) : parent;
        if (iid != null) world[iid] = here;
        walk(el.children, here);
      } else if (el.children.isNotEmpty) {
        walk(el.children, parent);
      }
    }
  }

  walk([top], const []);
}

/// Linear dimensions (SketchUp's Dimension tool).
///
/// A dimension entity is a "5BCC" record (raw bytes cc 5b) holding:
///
/// * 5BCD / 5BCE - the two connection points. Each wraps a 5208 whose 5209
///   is the connection TYPE (1 = a free explicit point in 520A, already
///   world space; 2 = connected to geometry, 520A is zero and 520B -> 53FC
///   names the target: 53FD = the vertex by persistent id, 53FE = a
///   length-prefixed persistent id of the INSTANCE holding it - the vertex
///   position is definition-local, so it is lifted to world by that
///   instance's transform).
/// * 5BCF - the dimension plane's NORMAL (SketchUp's SUDimensionLinearGetNormal
///   equals it); 5BD0 - its X axis. Kept in the planeX and normal fields, whose
///   names say the opposite.
/// * 5BD2 - the offset distance (inches): how far the dimension line sits
///   from the measured segment, along the in-plane perpendicular.
///
/// The measured value is auto-computed from the two points (no cached text
/// on the samples seen), so callers format it themselves. Endpoints come
/// out in WORLD space (inches). A connection point that cannot be resolved
/// drops the whole dimension (fail-safe).
List<String> _lengthPrefixedIds(Uint8List p) {
  final ids = <String>[];
  int off = 0;
  while (off < p.length) {
    final ln = p[off];
    if (ln == 0 || off + 1 + ln > p.length) break;
    ids.add(Tlv.toHexUpper(Uint8List.sublistView(p, off + 1, off + 1 + ln)));
    off += 1 + ln;
  }
  return ids;
}

const int maxExpandedInstances = 50000000;

typedef _PathResolverFn = List<double>? Function(List<String> path);

_PathResolverFn _mapPathResolver(Map<String, List<double>> instWorld) {
  return (List<String> path) {
    for (int k = path.length - 1; k >= 0; k--) {
      final w = instWorld[path[k]];
      if (w != null && w.isNotEmpty) {
        return w;
      }
    }
    return null;
  };
}

(double, double, double)? _dimensionConnectionPoint(
  Uint8List? blockPayload,
  Map<String, (double, double, double)> id2pos,
  _PathResolverFn resolvePath,
) {
  if (blockPayload == null) return null;
  final blk = _tlvFindInt(_tlvItemsInt(blockPayload), 0x5208);
  if (blk == null) return null;
  final sub = _tlvItemsInt(blk);
  final typB = _tlvFindInt(sub, 0x5209);
  final typ = (typB != null && typB.length == 4) ? Tlv.readU32(typB, 0) : null;
  if (typ == 1) {
    final pos = _tlvFindInt(sub, 0x520A);
    if (pos == null || pos.length != 24) return null;
    return (Tlv.readF64(pos, 0), Tlv.readF64(pos, 8), Tlv.readF64(pos, 16));
  }
  final refB = _tlvFindInt(sub, 0x520B);
  final f53fc = refB != null ? _tlvFindInt(_tlvItemsInt(refB), 0x53FC) : null;
  final fi = f53fc != null ? _tlvItemsInt(f53fc) : null;
  final vid = _tlvFindInt(fi, 0x53FD);
  final iid = _tlvFindInt(fi, 0x53FE);
  if (vid == null) return null;
  final local = id2pos[Tlv.toHexUpper(vid)];
  if (local == null) return null;
  if (iid != null && iid.isNotEmpty) {
    final path = _lengthPrefixedIds(iid);
    if (path.isEmpty) path.add(Tlv.toHexUpper(iid));
    final w = resolvePath(path);
    if (w != null && w.isNotEmpty) {
      return Transforms.transformPoint(w, local);
    }
  }
  return local;
}

bool _flatTlvHasFields(Uint8List buf, int begin, int end,
    [int first = 0x5BCD, int second = 0x5BCE]) {
  bool hasFirst = false, hasSecond = false;
  int off = begin;
  while (off < end) {
    if (off + 6 > end) return false;
    final tag = buf[off] | (buf[off + 1] << 8);
    final ln = Tlv.readU32(buf, off + 2);
    if (tag == 0 || ln > end - off - 6) return false;
    if (tag == first) hasFirst = true;
    if (tag == second) hasSecond = true;
    off += 6 + ln;
  }
  return hasFirst && hasSecond;
}

RawDimension? _parseOne5Bcc(
  Uint8List modelDat,
  int j,
  Map<String, (double, double, double)> id2pos,
  _PathResolverFn resolvePath,
) {
  final n = modelDat.length;
  if (j + 6 > n) return null;
  final ln = Tlv.readU32(modelDat, j + 2);
  if (ln < 40 || j + 6 + ln > n) return null;
  if (!_flatTlvHasFields(modelDat, j + 6, j + 6 + ln, 0x5BCD, 0x5BCE)) {
    return null;
  }
  final body =
      _tlvItemsInt(Uint8List.sublistView(modelDat, j + 6, j + 6 + ln));
  if (body == null || body.isEmpty) return null;
  bool has5Bcd = false, has5Bce = false;
  for (final it in body) {
    if (it.tag == 0x5BCD) has5Bcd = true;
    if (it.tag == 0x5BCE) has5Bce = true;
  }
  if (!has5Bcd || !has5Bce) return null;

  final a =
      _dimensionConnectionPoint(_tlvFindInt(body, 0x5BCD), id2pos, resolvePath);
  final b =
      _dimensionConnectionPoint(_tlvFindInt(body, 0x5BCE), id2pos, resolvePath);
  if (a == null || b == null) return null;

  var layer = '';
  var hidden = false;
  final baseB = _tlvFindInt(body, 0x59D8);
  if (baseB != null) {
    final drawB = _tlvFindInt(_tlvItemsInt(baseB), 0x07D0);
    if (drawB != null) {
      final props = _tlvItemsInt(drawB);
      final layerB = _tlvFindInt(props, 0x07D2);
      if (layerB != null && layerB.isNotEmpty) {
        layer = Tlv.parseVarInt(layerB, 0, layerB.length).toString();
      }
      final flagsB = _tlvFindInt(props, 0x07D3);
      if (flagsB != null && flagsB.isNotEmpty) {
        hidden = (flagsB[0] & 0x01) != 0;
      }
    }
  }

  final xaxisB = _tlvFindInt(body, 0x5BCF);
  final normalB = _tlvFindInt(body, 0x5BD0);
  final axisB = _tlvFindInt(body, 0x5BD1);
  final offB = _tlvFindInt(body, 0x5BD2);

  final planeX = (xaxisB != null && xaxisB.length == 24)
      ? (
          Tlv.readF64(xaxisB, 0),
          Tlv.readF64(xaxisB, 8),
          Tlv.readF64(xaxisB, 16)
        )
      : null;
  final normal = (normalB != null && normalB.length == 24)
      ? (
          Tlv.readF64(normalB, 0),
          Tlv.readF64(normalB, 8),
          Tlv.readF64(normalB, 16)
        )
      : null;
  final offset = (offB != null && offB.length == 8) ? Tlv.readF64(offB, 0) : 0.0;
  (double, double, double)? offsetVector;
  if (axisB != null && axisB.length == 4 && planeX != null && normal != null) {
    final axis = Tlv.readU32(axisB, 0);
    final pNorm = planeX;
    final pX = normal;
    if (axis == 1) {
      offsetVector = (
        (pNorm.$2 * pX.$3 - pNorm.$3 * pX.$2) * offset + 0.0,
        (pNorm.$3 * pX.$1 - pNorm.$1 * pX.$3) * offset + 0.0,
        (pNorm.$1 * pX.$2 - pNorm.$2 * pX.$1) * offset + 0.0,
      );
    } else if (axis == 2) {
      offsetVector = (
        -pX.$1 * offset + 0.0,
        -pX.$2 * offset + 0.0,
        -pX.$3 * offset + 0.0,
      );
    }
  }

  return RawDimension(a: a, b: b)
    ..planeX = planeX
    ..normal = normal
    ..offset = offset
    ..offsetVector = offsetVector
    ..layer = layer
    ..hidden = hidden;
}

TextEntity? _parseVffText(
  Uint8List modelDat,
  int j,
  Map<String, (double, double, double)> id2pos,
  _PathResolverFn resolvePath,
) {
  final size = Tlv.readU32(modelDat, j + 2);
  final fields =
      _tlvItemsInt(Uint8List.sublistView(modelDat, j + 6, j + 6 + size));
  if (fields == null) return null;
  final valB = _tlvFindInt(fields, 0x55F1);
  final connB = _tlvFindInt(fields, 0x55F4);
  if (valB == null || connB == null) return null;
  final textStr = utf8.decode(valB, allowMalformed: true);
  final point = _dimensionConnectionPoint(connB, id2pos, resolvePath);
  final lpB = _tlvFindInt(fields, 0x55F5);
  final labelPoint = (lpB != null && lpB.length == 24)
      ? (Tlv.readF64(lpB, 0), Tlv.readF64(lpB, 8), Tlv.readF64(lpB, 16))
      : null;
  final xB = _tlvFindInt(fields, 0x55F2);
  final yB = _tlvFindInt(fields, 0x55F3);
  final screenPos =
      (xB != null && yB != null && xB.length == 8 && yB.length == 8)
          ? (Tlv.readF64(xB, 0), Tlv.readF64(yB, 0))
          : null;
  final ltB = _tlvFindInt(fields, 0x55FD);
  final leaderType =
      (ltB != null && ltB.length == 4) ? Tlv.readU32(ltB, 0) : 2;
  final atB = _tlvFindInt(fields, 0x55FA);
  final arrowType =
      (atB != null && atB.length == 4) ? Tlv.readU32(atB, 0) : 3;
  final dlB = _tlvFindInt(fields, 0x55FC);
  final displayLeader =
      (dlB != null && dlB.length == 1) ? dlB[0] != 0 : true;
  var layer = '';
  var hidden = false;
  final drawB = _tlvFindInt(fields, 0x07D0);
  if (drawB != null) {
    final props = _tlvItemsInt(drawB);
    final layerB = _tlvFindInt(props, 0x07D2);
    if (layerB != null && layerB.isNotEmpty) {
      layer = Tlv.parseVarInt(layerB, 0, layerB.length).toString();
    }
    final flagsB = _tlvFindInt(props, 0x07D3);
    if (flagsB != null && flagsB.isNotEmpty) {
      hidden = (flagsB[0] & 0x01) != 0;
    }
  }
  return TextEntity(
    text: textStr,
    hidden: hidden,
    layer: layer,
    point: point,
    labelPoint: labelPoint,
    screenPosition: screenPos,
    leaderType: leaderType,
    arrowType: arrowType,
    displayLeader: displayLeader,
  );
}

List<(int, int)> _tlvHeadersRange(Uint8List md, int start, int end) {
  final out = <(int, int)>[];
  for (int p = start; p + 6 <= end;) {
    final n = Tlv.readU32(md, p + 2);
    if (n > end - p - 6) break;
    out.add((p, n));
    p += 6 + n;
  }
  return out;
}

String _tagHexAt(Uint8List md, int off) {
  final a = md[off].toRadixString(16).toUpperCase().padLeft(2, '0');
  final b = md[off + 1].toRadixString(16).toUpperCase().padLeft(2, '0');
  return '$a$b';
}

const _defIdContainers = <String>{
  '8813',
  'D007',
  '8913',
  '8A13',
  '8B13',
  '8C13',
  '8D13',
};

int? _entityIdFromTlvSpan(Uint8List md, int rec, int recEnd, [int depth = 0]) {
  if (depth > 24 || rec + 6 > recEnd) return null;
  final kids = _tlvHeadersRange(md, rec + 6, recEnd);
  for (final (kOff, kSz) in kids) {
    final t = _tagHexAt(md, kOff);
    try {
      if (t == 'DE05' && kSz > 0 && kSz <= 8) {
        return Tlv.parseVarInt(md, kOff + 6, kSz);
      }
      if (t == 'DC05' &&
          kSz >= 6 &&
          md[kOff + 6] == 0xDE &&
          md[kOff + 7] == 0x05) {
        final z = Tlv.readU32(md, kOff + 8);
        if (z > 0 && z <= 8 && z <= kSz - 6) {
          return Tlv.parseVarInt(md, kOff + 12, z);
        }
      }
    } catch (_) {
      continue;
    }
  }
  for (final (kOff, kSz) in kids) {
    final t = _tagHexAt(md, kOff);
    if (!_defIdContainers.contains(t)) continue;
    final id = _entityIdFromTlvSpan(md, kOff, kOff + 6 + kSz, depth + 1);
    if (id != null) return id;
  }
  return null;
}

List<(int, int, int)> _scanDefinitionSpans(
    Uint8List md, Map<int, RawDefinition> defs) {
  final spans = <(int, int, int)>[];
  if (md.length < 6) return spans;
  var hs = _tlvHeadersRange(md, 0, md.length);
  if (hs.length == 1 && md[hs[0].$1] == 0xF4 && md[hs[0].$1 + 1] == 0x01) {
    hs = _tlvHeadersRange(md, hs[0].$1 + 6, hs[0].$1 + 6 + hs[0].$2);
  }
  for (final (hOff, hSz) in hs) {
    if (_tagHexAt(md, hOff) != 'F901') continue;
    var level = _tlvHeadersRange(md, hOff + 6, hOff + 6 + hSz);
    var ok = true;
    for (final expected in const ['7017', '7117']) {
      (int, int)? match;
      for (final rec in level) {
        if (_tagHexAt(md, rec.$1) == expected) {
          match = rec;
          break;
        }
      }
      if (match == null) {
        ok = false;
        break;
      }
      level = _tlvHeadersRange(md, match.$1 + 6, match.$1 + 6 + match.$2);
    }
    if (!ok) continue;
    for (final (recOff, recSz) in level) {
      if (_tagHexAt(md, recOff) != '7C15') continue;
      final id = _entityIdFromTlvSpan(md, recOff, recOff + 6 + recSz);
      if (id == null || !defs.containsKey(id)) continue;
      spans.add((recOff, recOff + 6 + recSz, id));
    }
  }
  return spans;
}

int? _owningDefinition(List<(int, int, int)> spans, int off) {
  int? best;
  int? bestLen;
  for (final (begin, end, id) in spans) {
    if (off < begin || off >= end) continue;
    final len = end - begin;
    if (bestLen == null || len < bestLen) {
      bestLen = len;
      best = id;
    }
  }
  return best;
}

bool _tlvHasNonEmptyTag(Uint8List buf, int begin, int end, int tag,
    [int depth = 0]) {
  if (depth > 64) return false;
  int off = begin;
  while (off < end) {
    if (off + 6 > end) return false;
    final t = buf[off] | (buf[off + 1] << 8);
    final ln = Tlv.readU32(buf, off + 2);
    if (t == 0 || ln > end - off - 6) return false;
    off += 6 + ln;
  }
  for (off = begin; off < end;) {
    final t = buf[off] | (buf[off + 1] << 8);
    final ln = Tlv.readU32(buf, off + 2);
    if ((t == tag && ln > 0) ||
        _tlvHasNonEmptyTag(buf, off + 6, off + 6 + ln, tag, depth + 1)) {
      return true;
    }
    off += 6 + ln;
  }
  return false;
}

int composeInstanceWorld(
  GeometryBuilder rootBuilder,
  Map<int, RawDefinition> definitions,
  Map<String, List<double>> world, {
  int maxVisits = maxExpandedInstances,
}) {
  var visits = 0;
  void walk(GeometryBuilder builder, List<double> parent, int depth) {
    if (depth > 64 || visits >= maxVisits) return;
    for (final inst in builder.instances) {
      if (visits >= maxVisits) return;
      visits++;
      final here = inst.matrix.isNotEmpty
          ? Transforms.multiplyMatrices(parent, inst.matrix)
          : parent;
      if (inst.persistentId.isNotEmpty) {
        world[inst.persistentId] = here;
      }
      final refIdx = inst.refIdx;
      if (refIdx == null) continue;
      final childDef = definitions[refIdx];
      if (childDef != null) walk(childDef.builder, here, depth + 1);
    }
  }

  walk(rootBuilder, const [], 0);
  return visits;
}

class _InstancePathResolver {
  int visits = 0;
  final GeometryBuilder rootBuilder;
  final Map<int, RawDefinition> defsDict;
  final int maxVisits;
  final Map<int?, Map<String, GeometryBuilderInstance>> _byPid = {};
  final Map<String, (bool, List<double>)> _resolved = {};
  bool _indexed = false;
  final Map<String, List<int?>> _holdersOfPid = {};
  final Map<int, List<int?>> _parentsOfDef = {};

  _InstancePathResolver(this.rootBuilder, this.defsDict, this.maxVisits);

  GeometryBuilder? _builderFor(int? owner) {
    if (owner == null) return rootBuilder;
    return defsDict[owner]?.builder;
  }

  Map<String, GeometryBuilderInstance> _pidMap(
      int? owner, GeometryBuilder builder) {
    final existing = _byPid[owner];
    if (existing != null) return existing;
    final map = <String, GeometryBuilderInstance>{};
    for (final inst in builder.instances) {
      if (inst.persistentId.isNotEmpty) {
        map[inst.persistentId.toUpperCase()] = inst;
      }
    }
    _byPid[owner] = map;
    return map;
  }

  void _ensureIndex() {
    if (_indexed) return;
    _indexed = true;
    final owners = <int?>[null, ...defsDict.keys];
    for (final defOwner in owners) {
      final builder = _builderFor(defOwner);
      if (builder == null) continue;
      final seenPids = <String>{};
      final seenRefs = <int>{};
      for (final inst in builder.instances) {
        if (inst.persistentId.isNotEmpty) {
          final normPid = inst.persistentId.toUpperCase();
          if (seenPids.add(normPid)) {
            (_holdersOfPid[normPid] ??= <int?>[]).add(defOwner);
          }
        }
        final refIdx = inst.refIdx;
        if (refIdx != null &&
            defsDict.containsKey(refIdx) &&
            seenRefs.add(refIdx)) {
          (_parentsOfDef[refIdx] ??= <int?>[]).add(defOwner);
        }
      }
    }
  }

  (bool, List<double>) _walkExact(
    int? owner,
    GeometryBuilder? builder,
    List<String> path,
    int idx,
    List<double> parentXf,
    int depth,
    List<int> queryState,
  ) {
    if (depth > 64 || builder == null || queryState[0] >= maxVisits) {
      return (false, const []);
    }
    final inst = _pidMap(owner, builder)[path[idx]];
    if (inst == null) return (false, const []);
    visits++;
    queryState[0]++;
    final here = inst.matrix.isNotEmpty
        ? Transforms.multiplyMatrices(parentXf, inst.matrix)
        : parentXf;
    if (idx + 1 == path.length) return (true, here);
    final refIdx = inst.refIdx;
    if (refIdx == null) return (false, const []);
    final childBuilder = _builderFor(refIdx);
    if (childBuilder == null) return (false, const []);
    return _walkExact(
        refIdx, childBuilder, path, idx + 1, here, depth + 1, queryState);
  }

  List<double>? resolvePath(int? owner, List<String> rawPath,
      [int? fallbackOwner]) {
    if (rawPath.isEmpty) return null;
    final norm = [for (final p in rawPath) p.toUpperCase()];
    final cacheKey =
        '${owner ?? 'ROOT'}:${fallbackOwner ?? 'NONE'}:${norm.join('/')}';
    final cached = _resolved[cacheKey];
    if (cached != null) return cached.$1 ? cached.$2 : null;

    final queryState = <int>[0];
    final startBuilder = _builderFor(owner);
    var res = _walkExact(owner, startBuilder, norm, 0, const [], 0, queryState);
    if (res.$1) {
      _resolved[cacheKey] = res;
      return res.$2;
    }
    if (queryState[0] >= maxVisits) return null;

    _ensureIndex();
    final validHolders = <int?>{};
    (bool, List<double>)? fbResult;
    final holders = _holdersOfPid[norm[0]] ?? const <int?>[];
    for (final holder in holders) {
      if (holder == owner) continue;
      if (queryState[0] >= maxVisits) break;
      final hRes = _walkExact(
          holder, _builderFor(holder), norm, 0, const [], 0, queryState);
      if (hRes.$1) {
        validHolders.add(holder);
        if (fallbackOwner != null && holder == fallbackOwner) {
          fbResult = hRes;
        }
      }
    }

    if (validHolders.isNotEmpty && queryState[0] < maxVisits) {
      final canReach = <int?>{...validHolders};
      final queue = <int>[
        for (final h in validHolders)
          if (h != null) h,
      ];
      for (var qi = 0; qi < queue.length; qi++) {
        final cur = queue[qi];
        final parents = _parentsOfDef[cur] ?? const <int?>[];
        for (final parentOwner in parents) {
          if (canReach.add(parentOwner) && parentOwner != null) {
            queue.add(parentOwner);
          }
        }
      }

      if (canReach.contains(owner)) {
        final activeDefs = <int>{
          if (owner != null) owner,
        };
        final failedMinDepth = <int, int>{};

        (bool, List<double>) walkToHolder(
          int? curOwner,
          GeometryBuilder? curBuilder,
          List<double> parentXf,
          int depth,
        ) {
          if (depth > 64 || curBuilder == null || queryState[0] >= maxVisits) {
            return (false, const []);
          }
          if (depth > 0 &&
              validHolders.contains(curOwner) &&
              depth + norm.length <= 65) {
            final tail = _walkExact(
                curOwner, curBuilder, norm, 0, parentXf, depth, queryState);
            if (tail.$1 || queryState[0] >= maxVisits) return tail;
          }
          for (final inst in curBuilder.instances) {
            if (queryState[0] >= maxVisits) return (false, const []);
            final refIdx = inst.refIdx;
            if (refIdx == null ||
                !canReach.contains(refIdx) ||
                activeDefs.contains(refIdx)) {
              continue;
            }
            final prevFail = failedMinDepth[refIdx];
            if (prevFail != null && depth + 1 >= prevFail) continue;
            final childBuilder = _builderFor(refIdx);
            if (childBuilder == null) continue;
            visits++;
            queryState[0]++;
            final here = inst.matrix.isNotEmpty
                ? Transforms.multiplyMatrices(parentXf, inst.matrix)
                : parentXf;
            activeDefs.add(refIdx);
            final found = walkToHolder(refIdx, childBuilder, here, depth + 1);
            activeDefs.remove(refIdx);
            if (found.$1) return found;
            if (queryState[0] >= maxVisits) return (false, const []);
          }
          if (curOwner != null) {
            final prev = failedMinDepth[curOwner];
            if (prev == null || depth < prev) {
              failedMinDepth[curOwner] = depth;
            }
          }
          return (false, const []);
        }

        res = walkToHolder(owner, startBuilder, const [], 0);
      }
    }

    if (!res.$1 && fallbackOwner != null && fallbackOwner != owner) {
      if (fbResult != null) {
        res = fbResult;
      } else if (queryState[0] < maxVisits &&
          validHolders.contains(fallbackOwner)) {
        res = _walkExact(fallbackOwner, _builderFor(fallbackOwner), norm, 0,
            const [], 0, queryState);
      }
    }

    if (queryState[0] < maxVisits) {
      _resolved[cacheKey] = res;
    }
    return res.$1 ? res.$2 : null;
  }
}

int assignVffAnnotations(
  Uint8List modelDat,
  Map<String, (double, double, double)> id2pos,
  GeometryBuilder rootBuilder,
  Map<int, RawDefinition> defsDict, {
  List<RawDimension>? worldDimensions,
  int maxVisits = maxExpandedInstances,
}) {
  List<(int, int, int)>? spans;
  final resolver = _InstancePathResolver(rootBuilder, defsDict, maxVisits);
  final n = modelDat.length;
  for (int j = 0; j + 6 <= n; j++) {
    final b0 = modelDat[j];
    final b1 = modelDat[j + 1];
    if (!((b0 == 0xCC && b1 == 0x5B) || (b0 == 0xF0 && b1 == 0x55))) continue;
    final ln = Tlv.readU32(modelDat, j + 2);
    if (ln < 40 || j + 6 + ln > n) continue;
    final isText = b0 == 0xF0;
    if (!_flatTlvHasFields(modelDat, j + 6, j + 6 + ln,
        isText ? 0x55F1 : 0x5BCD, isText ? 0x55F4 : 0x5BCE)) {
      continue;
    }
    spans ??= _scanDefinitionSpans(modelDat, defsDict);
    final owner = _owningDefinition(spans, j);
    List<double>? ownerResolver(List<String> path) =>
        resolver.resolvePath(owner, path);
    final targetBuilder =
        owner != null ? defsDict[owner]!.builder : rootBuilder;
    if (isText) {
      final txt = _parseVffText(modelDat, j, id2pos, ownerResolver);
      if (txt != null) targetBuilder.texts.add(txt);
      continue;
    }
    final dim = _parseOne5Bcc(modelDat, j, id2pos, ownerResolver);
    if (dim != null) {
      targetBuilder.dimensions.add(Dimension(
        text: dim.text,
        hidden: dim.hidden,
        layer: dim.layer,
        a: dim.a,
        b: dim.b,
        offset: dim.offset,
        planeX: dim.planeX,
        normal: dim.normal,
        offsetVector: dim.offsetVector,
      ));
    }
    if (worldDimensions != null) {
      if (owner == null ||
          !_tlvHasNonEmptyTag(modelDat, j + 6, j + 6 + ln, 0x53FE)) {
        if (dim != null) worldDimensions.add(dim);
      } else {
        final wdim = _parseOne5Bcc(modelDat, j, id2pos,
            (path) => resolver.resolvePath(null, path, owner));
        if (wdim != null) worldDimensions.add(wdim);
      }
    }
  }
  return resolver.visits;
}

List<RawDimension> parseDimensions(
  Uint8List modelDat,
  Map<String, (double, double, double)> id2pos,
  Map<String, List<double>> instWorld,
) {
  final dims = <RawDimension>[];
  final needle = Uint8List.fromList([0xCC, 0x5B]);
  final resolvePath = _mapPathResolver(instWorld);
  int i = 0;
  while (true) {
    final j = _findBytes(modelDat, needle, i);
    if (j < 0) break;
    i = j + 1;
    final d = _parseOne5Bcc(modelDat, j, id2pos, resolvePath);
    if (d != null) dims.add(d);
  }
  return dims;
}

/// Return the "0702" scenes node inside top's subtree, or null. Called per
/// top-level record; retaining the (small) 0702 subtree is the only thing
/// kept alive past the streaming loop.
TlvNode? findPageNode(TlvNode top) {
  return Geometry.findChildTag([top], '0702');
}

void _parseIdRuns(Uint8List? buf, List<int> out) {
  if (buf == null) return;
  var off = 0;
  while (off + 1 <= buf.length) {
    final ln = buf[off];
    if (ln == 0 || off + 1 + ln > buf.length) break;
    out.add(Tlv.parseVarInt(buf, off + 1, ln));
    off += 1 + ln;
  }
}

/// Scenes ("pages"). The 0702 node's payload nests 6D60 > 6D61 > one 7148
/// record per page:
///
/// * 6F54 > 6F55 - page name (UTF-8)
/// * 714A > 34BC - camera: 34BD eye, 34BE target, 34BF up (3xf64, inches),
///   34C4 field of view (degrees), 34C2 u8 = PERSPECTIVE flag (00 =
///   parallel projection - calibrated against the bundled scene
///   thumbnails: parallel plans/elevations carry 00 and their 34C3 visible
///   height matches the thumbnail framing exactly, while perspective
///   scenes carry 01 with a stale 34C3), 34C3 f64 = visible height when
///   parallel (inches)
/// * 714B - entities hidden in this page: (u8 length, var-int entity id) runs
/// * 7150 - layers hidden in this page: (u8 length, var-int layer id) runs
List<RawPage> parsePages(TlvNode? node) {
  final pages = <RawPage>[];
  if (node == null) return pages;

  (double, double, double)? vec3(Uint8List? p) => (p != null && p.length == 24)
      ? (Tlv.readF64(p, 0), Tlv.readF64(p, 8), Tlv.readF64(p, 16))
      : null;

  final t60Items = _tlvItemsInt(node.payload);
  if (t60Items == null) return pages;
  for (final it60 in t60Items) {
    if (it60.tag != 0x6D60) continue;
    final t61Items = _tlvItemsInt(it60.payload);
    if (t61Items == null) continue;
    for (final it61 in t61Items) {
      if (it61.tag != 0x6D61) continue;
      final t48Items = _tlvItemsInt(it61.payload);
      if (t48Items == null) continue;
      for (final it48 in t48Items) {
        if (it48.tag != 0x7148) continue;
        final items = _tlvItemsInt(it48.payload);
        if (items == null) continue;

        final page = RawPage();

        final head = _tlvItemsInt(_tlvFindInt(items, 0x6F54));
        final name = _tlvFindInt(head, 0x6F55);
        if (name != null && name.isNotEmpty) {
          page.name = utf8.decode(name, allowMalformed: true);
        }

        final camWrap = _tlvItemsInt(_tlvFindInt(items, 0x714A));
        final cam =
            camWrap != null ? _tlvItemsInt(_tlvFindInt(camWrap, 0x34BC)) : null;
        if (cam != null) {
          page.eye = vec3(_tlvFindInt(cam, 0x34BD));
          page.target = vec3(_tlvFindInt(cam, 0x34BE));
          page.up = vec3(_tlvFindInt(cam, 0x34BF));
          final fov = _tlvFindInt(cam, 0x34C4);
          if (fov != null && fov.length == 8) page.fov = Tlv.readF64(fov, 0);
          final flag = _tlvFindInt(cam, 0x34C2);
          page.parallel = flag != null && flag.isNotEmpty && flag[0] == 0;
          final height = _tlvFindInt(cam, 0x34C3);
          if (height != null && height.length == 8)
            page.orthoHeight = Tlv.readF64(height, 0);
        }

        _parseIdRuns(_tlvFindInt(items, 0x7150), page.hiddenLayerIds);
        _parseIdRuns(_tlvFindInt(items, 0x714B), page.hiddenEntityIds);

        // A scene that doesn't save its camera ("Camera Location" unticked) is
        // still a scene: it keeps its name and hidden layers, eye/target/up null.
        pages.add(page);
      }
    }
  }
  return pages;
}
