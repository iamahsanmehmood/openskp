import 'dart:convert';
import 'dart:typed_data';

import 'geometry.dart';
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
}

class RawDimension {
  (double, double, double) a;
  (double, double, double) b;
  double offset = 0.0;
  (double, double, double)? planeX;
  (double, double, double)? normal;
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
/// * 5BCF - the dimension plane's x-axis; 5BD0 - its normal.
/// * 5BD2 - the offset distance (inches): how far the dimension line sits
///   from the measured segment, along the in-plane perpendicular.
///
/// The measured value is auto-computed from the two points (no cached text
/// on the samples seen), so callers format it themselves. Endpoints come
/// out in WORLD space (inches). A connection point that cannot be resolved
/// drops the whole dimension (fail-safe).
List<RawDimension> parseDimensions(
  Uint8List modelDat,
  Map<String, (double, double, double)> id2pos,
  Map<String, List<double>> instWorld,
) {
  final dims = <RawDimension>[];
  final needle = Uint8List.fromList([0xCC, 0x5B]);
  int i = 0;
  final n = modelDat.length;

  (double, double, double)? point(Uint8List? blockPayload) {
    if (blockPayload == null) return null;
    final blk = _tlvFindInt(_tlvItemsInt(blockPayload), 0x5208);
    if (blk == null) return null;
    final sub = _tlvItemsInt(blk);
    final typB = _tlvFindInt(sub, 0x5209);
    final typ =
        (typB != null && typB.length == 4) ? Tlv.readU32(typB, 0) : null;
    if (typ == 1) {
      final pos = _tlvFindInt(sub, 0x520A);
      if (pos == null || pos.length != 24) return null;
      return (Tlv.readF64(pos, 0), Tlv.readF64(pos, 8), Tlv.readF64(pos, 16));
    }
    // type 2: resolve the geometry reference (vertex + instance).
    final refB = _tlvFindInt(sub, 0x520B);
    final f53fc = refB != null ? _tlvFindInt(_tlvItemsInt(refB), 0x53FC) : null;
    final fi = f53fc != null ? _tlvItemsInt(f53fc) : null;
    final vid = _tlvFindInt(fi, 0x53FD);
    final iid = _tlvFindInt(fi, 0x53FE);
    if (vid == null) return null;
    final local = id2pos[Tlv.toHexUpper(vid)];
    if (local == null) return null;
    if (iid != null &&
        iid.isNotEmpty &&
        iid[0] > 0 &&
        1 + iid[0] <= iid.length) {
      final idBytes = Uint8List.sublistView(iid, 1, 1 + iid[0]);
      final w = instWorld[Tlv.toHexUpper(idBytes)];
      if (w != null && w.isNotEmpty) {
        return Transforms.transformPoint(w, local);
      }
    }
    return local; // model-root vertex - already world
  }

  while (true) {
    final j = _findBytes(modelDat, needle, i);
    if (j < 0) break;
    i = j + 1;
    if (j + 6 > n) continue;
    final ln = Tlv.readU32(modelDat, j + 2);
    if (ln < 40 || j + 6 + ln > n) continue;
    final bodyBytes = Uint8List.sublistView(modelDat, j + 6, j + 6 + ln);
    final body = _tlvItemsInt(bodyBytes);
    if (body == null) continue;
    bool has5Bcd = false, has5Bce = false;
    for (final it in body) {
      if (it.tag == 0x5BCD) has5Bcd = true;
      if (it.tag == 0x5BCE) has5Bce = true;
    }
    if (!has5Bcd || !has5Bce) continue;

    final a = point(_tlvFindInt(body, 0x5BCD));
    final b = point(_tlvFindInt(body, 0x5BCE));
    if (a == null || b == null) continue;

    final xaxisB = _tlvFindInt(body, 0x5BCF);
    final normalB = _tlvFindInt(body, 0x5BD0);
    final offB = _tlvFindInt(body, 0x5BD2);

    dims.add(RawDimension(a: a, b: b)
      ..planeX = (xaxisB != null && xaxisB.length == 24)
          ? (
              Tlv.readF64(xaxisB, 0),
              Tlv.readF64(xaxisB, 8),
              Tlv.readF64(xaxisB, 16)
            )
          : null
      ..normal = (normalB != null && normalB.length == 24)
          ? (
              Tlv.readF64(normalB, 0),
              Tlv.readF64(normalB, 8),
              Tlv.readF64(normalB, 16)
            )
          : null
      ..offset =
          (offB != null && offB.length == 8) ? Tlv.readF64(offB, 0) : 0.0);
  }
  return dims;
}

/// Return the "0702" scenes node inside top's subtree, or null. Called per
/// top-level record; retaining the (small) 0702 subtree is the only thing
/// kept alive past the streaming loop.
TlvNode? findPageNode(TlvNode top) {
  return Geometry.findChildTag([top], '0702');
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

        final hidden = _tlvFindInt(items, 0x7150);
        int off = 0;
        while (hidden != null && off + 1 <= hidden.length) {
          final ln = hidden[off];
          if (ln == 0 || off + 1 + ln > hidden.length) break;
          page.hiddenLayerIds.add(Tlv.parseVarInt(hidden, off + 1, ln));
          off += 1 + ln;
        }

        if (page.eye != null && page.target != null) pages.add(page);
      }
    }
  }
  return pages;
}
