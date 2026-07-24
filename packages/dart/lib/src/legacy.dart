import 'dart:convert';
import 'dart:typed_data';

import 'core.dart';
import 'errors.dart';
import 'geometry.dart';
import 'observability.dart';
import 'tlv.dart';

/// Legacy (classic MFC) SketchUp .skp parser - SketchUp 2013-2020 era.
///
/// Pre-2021 .skp files are not VFF/ZIP containers: after the same UTF-16
/// header records, the body is one uncompressed MFC CArchive object stream
/// with a single global 1-based store map. This module walks that stream
/// and adapts the result to the same RawParsed shape the VFF path produces
/// (core.dart), so Parser handles both eras transparently.
///
/// Ported line-for-line from openskp/legacy.py (via the already-verified
/// TypeScript and C# ports). See that module's docstring for the full list
/// of format details that differ from the public 2017 format notes.
class LegacyParseError implements Exception {
  final String message;
  LegacyParseError(this.message);
  @override
  String toString() => 'LegacyParseError: $message';
}

const List<int> _strMarker = [0xFF, 0xFE, 0xFF];

bool _bytesEqualAt(Uint8List a, int aOff, List<int> b) {
  if (aOff + b.length > a.length) return false;
  for (int i = 0; i < b.length; i++) {
    if (a[aOff + i] != b[i]) return false;
  }
  return true;
}

int _findBytes(Uint8List data, List<int> needle, [int start = 0, int? end]) {
  final e = end ?? data.length;
  final limit = e - needle.length;
  for (int i = start < 0 ? 0 : start; i <= limit; i++) {
    bool ok = true;
    for (int j = 0; j < needle.length; j++) {
      if (data[i + j] != needle[j]) {
        ok = false;
        break;
      }
    }
    if (ok) return i;
  }
  return -1;
}

/// Search for a byte pattern where null entries are wildcards.
int _findPattern(Uint8List data, List<int?> pattern,
    [int start = 0, int? end]) {
  final e = end ?? data.length;
  final limit = e - pattern.length;
  for (int i = start < 0 ? 0 : start; i <= limit; i++) {
    bool ok = true;
    for (int j = 0; j < pattern.length; j++) {
      final want = pattern[j];
      if (want != null && data[i + j] != want) {
        ok = false;
        break;
      }
    }
    if (ok) return i;
  }
  return -1;
}

bool _matchesAscii(Uint8List data, int offset, String str) {
  if (offset + str.length > data.length) return false;
  for (int i = 0; i < str.length; i++) {
    if (data[offset + i] != str.codeUnitAt(i)) return false;
  }
  return true;
}

/// Byte cursor, matching Python's _R.
class LR {
  final Uint8List data;
  int pos;

  LR(this.data, [this.pos = 0]);

  int u8() {
    final v = data[pos];
    pos += 1;
    return v;
  }

  int u16() {
    final v = Tlv.readU16(data, pos);
    pos += 2;
    return v;
  }

  int u32() {
    final v = Tlv.readU32(data, pos);
    pos += 4;
    return v;
  }

  int i32() {
    final v = Tlv.readI32(data, pos);
    pos += 4;
    return v;
  }

  double f64() {
    final v = Tlv.readF64(data, pos);
    pos += 8;
    return v;
  }

  List<double> f64s(int n) {
    return [for (int i = 0; i < n; i++) f64()];
  }

  Uint8List raw(int n) {
    final v = Uint8List.sublistView(data, pos, pos + n);
    pos += n;
    return v;
  }

  Uint8List peek(int n) {
    final len = (pos + n <= data.length) ? n : (data.length - pos);
    return Uint8List.sublistView(data, pos, pos + (len < 0 ? 0 : len));
  }

  int peekU16() => Tlv.readU16(data, pos);

  String utf16() {
    if (!_bytesEqualAt(data, pos, _strMarker)) {
      throw LegacyParseError('expected a string record ${ctx()}');
    }
    pos += 3;
    int n = u8();
    if (n == 0xFF) {
      n = u16();
      if (n == 0xFFFF) {
        n = u32();
      }
    }
    final bytes = raw(2 * n);
    final codeUnits = <int>[];
    for (int i = 0; i + 1 < bytes.length; i += 2) {
      codeUnits.add(bytes[i] | (bytes[i + 1] << 8));
    }
    return String.fromCharCodes(codeUnits);
  }

  String ctx([int back = 16, int fwd = 32]) {
    final p = pos;
    final bstart = p - back < 0 ? 0 : p - back;
    final before = Uint8List.sublistView(data, bstart, p);
    final flen = (p + fwd <= data.length) ? fwd : (data.length - p);
    final after = Uint8List.sublistView(data, p, p + (flen < 0 ? 0 : flen));
    return '@0x${p.toRadixString(16)}: ...${Tlv.toHexUpper(before)} | ${Tlv.toHexUpper(after)}...';
  }
}

class SlotEntry {
  final String kind; // 'class' or 'obj'
  final String? name;
  final Object? value; // int? schema for 'class'; reader result for 'obj'
  SlotEntry({required this.kind, this.name, this.value});
}

typedef LegacyReader = Object? Function(Archive ar, LR r);

/// MFC CArchive store-map bookkeeping and object-graph walk, matching
/// Python's _Archive.
class Archive {
  final Uint8List data;
  final int ver;
  final bool hasPid;
  late final LR r;
  final Map<int, SlotEntry> slots = {};
  final Map<String, int> classSlot = {};
  int nextSlot = 0;
  int walkBase = 0;
  final Map<String, LegacyReader> readers = {};
  int? currentLoop;
  bool inEntityList = false;

  Archive(this.data, this.ver) : hasPid = ver >= 17 {
    r = LR(data);
  }

  int alloc(SlotEntry entry) {
    final s = nextSlot;
    slots[s] = entry;
    nextSlot += 1;
    return s;
  }

  (int?, String?, Object?) readObject(LR r, [String? expect]) {
    final tag = r.u16();
    if (tag == 0) {
      return (null, null, null);
    }
    if (tag == 0x7FFF) {
      final big = r.u32();
      if ((big & 0x80000000) != 0) {
        return _newOfClass(r, big & 0x7FFFFFFF, expect);
      }
      return _backref(big, r);
    }
    if (tag == 0xFFFF) {
      final schema = r.u16();
      final namelen = r.u16();
      if (namelen > 40) {
        throw LegacyParseError('implausible class name length ${r.ctx()}');
      }
      final nameBytes = r.raw(namelen);
      final name = ascii.decode(nameBytes);
      alloc(SlotEntry(kind: 'class', name: name, value: schema));
      classSlot[name] = nextSlot - 1;
      return _newObj(r, name);
    }
    if ((tag & 0x8000) != 0) {
      return _newOfClass(r, tag & 0x7FFF, expect);
    }
    return _backref(tag, r);
  }

  (int, String, Object?) _newOfClass(LR r, int cslot, String? expect) {
    var ent = slots[cslot];
    if (ent == null) {
      if (expect == null) {
        throw LegacyParseError('class-ref to unknown slot $cslot ${r.ctx()}');
      }
      ent = SlotEntry(kind: 'class', name: expect, value: null);
      slots[cslot] = ent;
      classSlot[expect] = cslot;
    }
    if (ent.kind != 'class') {
      throw LegacyParseError(
          'class-ref to non-class slot $cslot (${ent.name}) ${r.ctx()}');
    }
    return _newObj(r, ent.name!);
  }

  (int, String, Object?) _newObj(LR r, String name) {
    inEntityList = false;
    final slot = alloc(SlotEntry(kind: 'obj', name: name, value: null));
    final reader = readers[name];
    if (reader == null) {
      throw LegacyParseError('no reader for class $name ${r.ctx()}');
    }
    final value = reader(this, r);
    slots[slot] = SlotEntry(kind: 'obj', name: name, value: value);
    return (slot, name, value);
  }

  (int?, String?, Object?) _backref(int slot, LR r) {
    final ent = slots[slot];
    if (ent == null) {
      if (slot < walkBase) {
        return (slot, 'premodel', null);
      }
      throw LegacyParseError('back-ref to unwalked slot $slot ${r.ctx()}');
    }
    if (ent.kind == 'class') {
      throw LegacyParseError('back-ref to class slot $slot ${r.ctx()}');
    }
    return (slot, ent.name, ent.value);
  }
}

// ── shared record blocks ───────────────────────────────────────────────────

class DrawBase {
  int mat = 0, hidden = 0, soft = 0, smooth = 0, layer = 0;
}

class PreambleResult {
  final Object? attrs;
  final int pid;
  PreambleResult(this.attrs, this.pid);
}

class VertexRec {
  List<double> xyz;
  VertexRec(this.xyz);
}

class EdgeRec {
  DrawBase db;
  int? curve, v1, v2;
  EdgeRec({required this.db, this.curve, this.v1, this.v2});
}

class CurveRec {
  int n;
  CurveRec(this.n);
}

class ArcCurveRec {}

class EdgeUseRec {
  int? edge;
  int sense;
  EdgeUseRec({this.edge, required this.sense});
}

class LoopRec {
  List<EdgeUseRec> uses;
  LoopRec(this.uses);
}

class FaceRec {
  DrawBase db;
  List<double> plane;
  List<LoopRec> loops;
  int backMat;
  AttrsRec? attrs;
  FaceRec(
      {required this.db,
      required this.plane,
      required this.loops,
      required this.backMat,
      this.attrs});
}

class AttrsRec {
  List<(String?, Object?)> children;
  AttrsRec(this.children);
}

class DictRec {
  String name;
  Map<String, Object?> entries;
  DictRec(this.name, this.entries);
}

class LayerRec {
  String name;
  int hidden;
  Uint8List rgba;
  LayerRec({required this.name, required this.hidden, required this.rgba});
}

class MaterialRec {
  String name;
  Uint8List rgba;
  double opacity = 0;
  int useOpacity = 0;
  int? texDib;
  double texW = 0, texH = 0;
  String texFile = '';
  bool colorized = false;
  bool hasTexture = false;
  MaterialRec({required this.name, required this.rgba});
}

class DibRec {
  int subtype;
  Uint8List data;
  DibRec(this.subtype, this.data);
}

class FtcRec {
  List<double> front, back;
  List<List<double>> frontPins, backPins;
  bool frontProjected, backProjected;
  FtcRec({
    required this.front,
    required this.back,
    required this.frontPins,
    required this.backPins,
    required this.frontProjected,
    required this.backProjected,
  });
}

class CameraRec {}

class ThumbnailRec {
  int? dib;
  ThumbnailRec(this.dib);
}

class RelationshipRec {}

class ConstructionLineRec {}

class ConstructionPointRec {
  DrawBase db;
  List<double> pos;
  ConstructionPointRec(this.db, this.pos);
}

class SectionPlaneRec {}

class FontRec {}

class DimLinearRec {
  DrawBase db;
  String text;
  DimLinearRec(this.db, this.text);
}

class TextRec {
  String text;
  TextRec(this.text);
}

class DefinitionRec {
  String name;
  String guid;
  List<(int, String?, Object?)> ents;
  bool facesCamera;
  DefinitionRec(
      {required this.name,
      required this.guid,
      required this.ents,
      required this.facesCamera});
}

class InstanceRec {
  DrawBase db;
  int? def;
  List<double> xf;
  String name;
  String guid;
  InstanceRec(
      {required this.db,
      this.def,
      required this.xf,
      required this.name,
      required this.guid});
}

class LegacyReaders {
  static PreambleResult preamble(Archive ar, LR r) {
    final (_, __, attrs) = ar.readObject(r, 'CAttributeContainer');
    int pid = 0;
    if (ar.hasPid) {
      final mask = r.u8();
      for (int bit = 0; bit < 8; bit++) {
        if ((mask & (1 << bit)) != 0) {
          pid |= r.u8() << (8 * bit);
        }
      }
    }
    return PreambleResult(attrs, pid);
  }

  static DrawBase drawbase(Archive ar, LR r) {
    final b = r.raw(10);
    return DrawBase()
      ..mat = Tlv.readU16(b, 0)
      ..hidden = b[2]
      ..soft = b[5]
      ..smooth = b[6]
      ..layer = Tlv.readU16(b, 8);
  }

  static Object readVertex(Archive ar, LR r) {
    preamble(ar, r);
    return VertexRec(r.f64s(3));
  }

  static Object readEdge(Archive ar, LR r) {
    preamble(ar, r);
    final db = drawbase(ar, r);
    final (s1, _, __) = ar.readObject(r, 'CVertex');
    final (s2, _2, __2) = ar.readObject(r, 'CVertex');
    final (cs, cn, _3) = ar.readObject(r);
    if (cn != null && cn != 'CCurve' && cn != 'CArcCurve') {
      throw LegacyParseError('edge curve pointer resolved to $cn ${r.ctx()}');
    }
    return EdgeRec(db: db, curve: cs, v1: s1, v2: s2);
  }

  static Object readCurve(Archive ar, LR r) {
    preamble(ar, r);
    r.u8();
    final n = r.u32();
    return CurveRec(n);
  }

  static Object readArcCurve(Archive ar, LR r) {
    preamble(ar, r);
    r.raw(5);
    r.f64s(14);
    return ArcCurveRec();
  }

  static Object readEdgeUse(Archive ar, LR r) {
    preamble(ar, r);
    final (es, _, __) = ar.readObject(r, 'CEdge');
    final sense = r.u8();
    final (ps, _2, __2) = ar.readObject(r);
    if (ps != ar.currentLoop) {
      throw LegacyParseError(
          'edge-use parent slot $ps != current loop ${ar.currentLoop} ${r.ctx()}');
    }
    return EdgeUseRec(edge: es, sense: sense);
  }

  static Object readLoop(Archive ar, LR r) {
    final mySlot = ar.nextSlot - 1;
    final prev = ar.currentLoop;
    ar.currentLoop = mySlot;
    preamble(ar, r);
    r.raw(2);
    final uses = <EdgeUseRec>[];
    while (true) {
      if (r.peekU16() == 0) {
        r.pos += 2;
        break;
      }
      final (_, __, v) = ar.readObject(r, 'CEdgeUse');
      uses.add(v as EdgeUseRec);
    }
    ar.currentLoop = prev;
    return LoopRec(uses);
  }

  static Object readFace(Archive ar, LR r) {
    final pre = preamble(ar, r);
    final db = drawbase(ar, r);
    final plane = r.f64s(4);
    final nloops = r.u32();
    if (nloops > 10000) {
      throw LegacyParseError('implausible loop count $nloops ${r.ctx()}');
    }
    final loops = <LoopRec>[];
    for (int i = 0; i < nloops; i++) {
      final (_, __, v) = ar.readObject(r, 'CLoop');
      loops.add(v as LoopRec);
    }
    final backMat = r.u16();
    return FaceRec(
        db: db,
        plane: plane,
        loops: loops,
        backMat: backMat,
        attrs: pre.attrs as AttrsRec?);
  }

  static Object readAttrContainer(Archive ar, LR r) {
    preamble(ar, r);
    final children = <(String?, Object?)>[];
    while (true) {
      if (r.peekU16() == 0) {
        r.pos += 2;
        break;
      }
      final (_, n, v) = ar.readObject(r, 'CAttributeNamed');
      children.add((n, v));
    }
    return AttrsRec(children);
  }

  static Object? _readTyped(LR r, int t) {
    if (t == 0x00) return null;
    if (t == 0x04) return r.i32();
    if (t == 0x06) return r.f64();
    if (t == 0x07) return r.u8();
    if (t == 0x09) return r.u32();
    if (t == 0x0a) return r.utf16();
    if (t == 0x0b) {
      final n = r.u32();
      if (n > 100000) {
        throw LegacyParseError('implausible attr array count ${r.ctx()}');
      }
      return [for (int i = 0; i < n; i++) _readTyped(r, r.u8())];
    }
    if (t == 0x12) return r.f64s(3);
    throw LegacyParseError(
        'unknown attribute value type 0x${t.toRadixString(16)} ${r.ctx()}');
  }

  static Object readAttrNamed(Archive ar, LR r) {
    preamble(ar, r);
    r.raw(4);
    final dictname = r.utf16();
    final entries = <String, Object?>{};
    while (true) {
      final key = r.utf16();
      if (key == '') break;
      entries[key] = _readTyped(r, r.u8());
    }
    r.u32();
    return DictRec(dictname, entries);
  }

  static Object readLayer(Archive ar, LR r) {
    preamble(ar, r);
    final name = r.utf16();
    final mid = <int>[];
    while (mid.length < 8 && !_bytesEqualAt(r.peek(3), 0, _strMarker)) {
      mid.add(r.raw(1)[0]);
    }
    r.utf16();
    r.u16();
    final rgba = r.raw(4);
    r.utf16();
    r.raw(21);
    return LayerRec(
        name: name, hidden: mid.isNotEmpty ? mid[0] : 0, rgba: rgba);
  }

  static Object readMaterial(Archive ar, LR r) {
    preamble(ar, r);
    final name = r.utf16();
    final texflag = r.u16();
    final out =
        MaterialRec(name: name, rgba: Uint8List.fromList([128, 128, 128, 255]));
    if (texflag == 0) {
      final rgba = r.raw(4);
      r.utf16();
      r.raw(8);
      final opacity = r.f64();
      final useOp = r.u8();
      out.rgba = rgba;
      out.opacity = opacity;
      out.useOpacity = useOp;
    } else {
      r.raw(ar.ver >= 17 ? 2 : 1);
      final (s, _, dib) = ar.readObject(r, 'CDib');
      if (dib is! DibRec) {
        throw LegacyParseError('texture object is not a dib ${r.ctx()}');
      }
      final marker = _findBytes(r.data, _strMarker, r.pos, r.pos + 28);
      if (marker - r.pos == 20) {
        r.u32();
      } else if (marker - r.pos != 16) {
        throw LegacyParseError('texture size block misaligned ${r.ctx()}');
      }
      final w = r.f64();
      final h = r.f64();
      final fname = r.utf16();
      final avg = r.raw(9);
      r.utf16();
      final blob = r.raw(8);
      final opacity = r.f64();
      final useOp = r.u8();
      final colorized = blob[4] != 0 || avg[3] == 0xFF;
      out.rgba = Uint8List.sublistView(avg, 0, 4);
      out.opacity = opacity;
      out.useOpacity = useOp;
      out.texDib = s;
      out.texW = w;
      out.texH = h;
      out.texFile = fname;
      out.colorized = colorized;
      out.hasTexture = true;
    }
    return out;
  }

  static Object readDib(Archive ar, LR r) {
    final subtype = r.u32();
    final length = r.u32();
    if (length > r.data.length) {
      throw LegacyParseError('implausible dib length $length ${r.ctx()}');
    }
    final data = r.raw(length);
    return DibRec(subtype, data);
  }

  static Object readFtc(Archive ar, LR r) {
    preamble(ar, r);
    r.u32();
    final ks = r.f64s(24);
    final frontPinsCount = r.u32();
    final frontPins = <List<double>>[];
    for (int i = 0; i < frontPinsCount; i++) frontPins.add(r.f64s(4));
    final backPinsCount = r.u32();
    final backPins = <List<double>>[];
    for (int i = 0; i < backPinsCount; i++) backPins.add(r.f64s(4));
    final fflags = r.u32();
    final bflags = r.u32();
    return FtcRec(
      front: ks.sublist(0, 9),
      back: ks.sublist(12, 21),
      frontPins: frontPins,
      backPins: backPins,
      frontProjected: (fflags & 2) != 0,
      backProjected: (bflags & 2) != 0,
    );
  }

  static Object readCamera(Archive ar, LR r) {
    r.raw(137);
    r.u16();
    r.utf16();
    r.raw(33);
    return CameraRec();
  }

  static Object readThumbnail(Archive ar, LR r) {
    preamble(ar, r);
    ar.readObject(r, 'CCamera');
    final (dibSlot, _, __) = ar.readObject(r, 'CDib');
    return ThumbnailRec(dibSlot);
  }

  static Object readRelationship(Archive ar, LR r) {
    preamble(ar, r);
    ar.readObject(r);
    ar.readObject(r);
    return RelationshipRec();
  }

  static Object readConstructionLine(Archive ar, LR r) {
    preamble(ar, r);
    drawbase(ar, r);
    r.f64s(3);
    r.f64s(3);
    r.f64s(2);
    r.raw(ar.ver >= 17 ? 7 : 4);
    return ConstructionLineRec();
  }

  static Object readConstructionPoint(Archive ar, LR r) {
    preamble(ar, r);
    final db = drawbase(ar, r);
    final pos = r.f64s(3);
    r.f64s(3);
    r.u8();
    return ConstructionPointRec(db, pos);
  }

  static Object readSectionPlane(Archive ar, LR r) {
    preamble(ar, r);
    drawbase(ar, r);
    final first = Tlv.readF64(r.data, r.pos);
    if (!(first.abs() <= 1.0001)) {
      ar.readObject(r);
    }
    r.f64s(4);
    if (_bytesEqualAt(r.peek(3), 0, _strMarker)) {
      r.utf16();
      r.utf16();
    }
    return SectionPlaneRec();
  }

  static Object readSkFont(Archive ar, LR r) {
    ar.readObject(r, 'CAttributeContainer');
    if (ar.hasPid) {
      r.u8();
    }
    r.utf16();
    r.raw(15);
    return FontRec();
  }

  static Object readDimLinear(Archive ar, LR r) {
    preamble(ar, r);
    final db = drawbase(ar, r);
    final text = r.utf16();
    ar.readObject(r, 'CSkFont');
    r.raw(165);
    return DimLinearRec(db, text);
  }

  static Object readText(Archive ar, LR r) {
    preamble(ar, r);
    drawbase(ar, r);
    ar.readObject(r, 'CSkFont');
    int p = r.pos;
    int idx;
    while (true) {
      idx = _findBytes(r.data, _strMarker, p, r.pos + 512);
      if (idx < 0) {
        throw LegacyParseError('text delimiter not found ${r.ctx()}');
      }
      final blk = Uint8List.sublistView(r.data, idx - 11, idx);
      if (blk[0] == 0x01 &&
          blk[1] == 0x00 &&
          blk[2] == 0x00 &&
          blk[3] == 0x00 &&
          blk[6] == 0x03 &&
          blk[7] == 0x00 &&
          blk[8] == 0x00 &&
          blk[9] == 0x00 &&
          blk[10] == 1) {
        break;
      }
      p = idx + 3;
    }
    r.raw(idx - r.pos);
    final text = r.utf16();
    r.raw(5);
    return TextRec(text);
  }

  static List<(int, String?, Object?)> readEntityList(
      Archive ar, LR r, int count, String owner) {
    final ents = <(int, String?, Object?)>[];
    while (ents.length < count) {
      final p = r.pos;
      final prevFlag = ar.inEntityList;
      ar.inEntityList = true;
      int? s;
      String? n;
      Object? v;
      try {
        (s, n, v) = ar.readObject(r);
      } on LegacyParseError {
        ar.inEntityList = prevFlag;
        if (owner != 'root') rethrow;
        r.pos = p;
        break;
      }
      ar.inEntityList = prevFlag;
      ents.add((s!, n, v));
    }
    return ents;
  }

  static Object readDefinition(Archive ar, LR r) {
    preamble(ar, r);
    r.raw(ar.ver >= 17 ? 22 : 20);
    final nlayers = r.u32();
    if (nlayers > 10000) {
      throw LegacyParseError('implausible def layer count ${r.ctx()}');
    }
    for (int i = 0; i < nlayers; i++) {
      ar.readObject(r, 'CLayer');
    }
    var decl = r.u16();
    if (decl == 0x7FFF) {
      decl = r.u32();
    }
    r.u32();
    final count = r.u32();
    if (count > 5000000) {
      throw LegacyParseError('implausible def entity count ${r.ctx()}');
    }
    final ents = readEntityList(ar, r, count, 'def');
    final nrel = r.u32();
    if (nrel > 100000) {
      throw LegacyParseError('definition list misaligned ${r.ctx()}');
    }
    for (int i = 0; i < nrel; i++) {
      ar.readObject(r, 'CRelationship');
    }
    r.u16();
    final guid = r.raw(16);
    final name = r.utf16();
    r.utf16();
    r.utf16();
    r.u32();

    int? tpos;
    for (int off = 0; off < 96; off++) {
      final p = r.pos + off;
      if (p + 16 <= r.data.length &&
          r.data[p] == 0xFF &&
          r.data[p + 1] == 0xFF &&
          r.data[p + 4] == 0x0a &&
          r.data[p + 5] == 0x00 &&
          _matchesAscii(r.data, p + 6, 'CThumbnail')) {
        tpos = p;
        break;
      }
      final thumbSlot = ar.classSlot['CThumbnail'];
      if (thumbSlot != null) {
        if (p + 2 <= r.data.length &&
            Tlv.readU16(r.data, p) == (0x8000 | thumbSlot)) {
          tpos = p;
          break;
        }
      }
    }
    if (tpos == null) {
      throw LegacyParseError('definition tail: thumbnail not found ${r.ctx()}');
    }
    final gap = r.raw(tpos - r.pos);
    final behavior = gap.length >= 9 ? gap[gap.length - 9] : 0;
    ar.readObject(r, 'CThumbnail');
    return DefinitionRec(
      name: name,
      guid: Tlv.toHexUpper(guid),
      ents: ents,
      facesCamera: (behavior & 1) != 0,
    );
  }

  static Object readInstance(Archive ar, LR r) {
    preamble(ar, r);
    final db = drawbase(ar, r);
    final (ds, dn, _) = ar.readObject(r, 'CComponentDefinition');
    if (dn != 'CComponentDefinition') {
      throw LegacyParseError('instance definition ref is $dn ${r.ctx()}');
    }
    final xf = r.f64s(13);
    final name = r.utf16();
    final guid = r.raw(16);
    return InstanceRec(
        db: db, def: ds, xf: xf, name: name, guid: Tlv.toHexUpper(guid));
  }

  static final Map<String, LegacyReader> readers = {
    'CVertex': readVertex,
    'CEdge': readEdge,
    'CCurve': readCurve,
    'CArcCurve': readArcCurve,
    'CEdgeUse': readEdgeUse,
    'CLoop': readLoop,
    'CFace': readFace,
    'CLayer': readLayer,
    'CMaterial': readMaterial,
    'CDib': readDib,
    'CAttributeContainer': readAttrContainer,
    'CAttributeNamed': readAttrNamed,
    'CCamera': readCamera,
    'CThumbnail': readThumbnail,
    'CRelationship': readRelationship,
    'CComponentDefinition': readDefinition,
    'CComponentInstance': readInstance,
    'CGroup': readInstance,
    'CFaceTextureCoords': readFtc,
    'CConstructionLine': readConstructionLine,
    'CConstructionPoint': readConstructionPoint,
    'CSectionPlane': readSectionPlane,
    'CSkFont': readSkFont,
    'CDimensionLinear': readDimLinear,
    'CText': readText,
  };
}

class Legacy {
  /// True when [data] is a classic (pre-2021) MFC-container .skp.
  static bool isLegacy(Uint8List data) {
    if (!(data.length >= 4 &&
        data[0] == 0xFF &&
        data[1] == 0xFE &&
        data[2] == 0xFF &&
        data[3] == 0x0E)) {
      return false;
    }
    final head100Len = data.length < 0x100 ? data.length : 0x100;
    if (_findBytes(data, [0x50, 0x4B, 0x03, 0x04], 0, head100Len) >= 0) {
      return false;
    }
    final head200Len = data.length < 0x200 ? data.length : 0x200;
    return _findBytes(data, ascii.encode('CVersionMap'), 0, head200Len) >= 0;
  }

  static final List<int?> _cMaterialPattern = [
    0xFF,
    0xFF,
    null,
    null,
    0x09,
    0x00,
    ...ascii.encode('CMaterial'),
  ];

  static int? _findVersionMajor(Uint8List data) {
    final headLen = data.length < 0x60 ? data.length : 0x60;
    final stripped = <int>[];
    for (int i = 0; i < headLen; i++) {
      if (data[i] != 0x00) stripped.add(data[i]);
    }
    final text = latin1.decode(stripped);
    final m = RegExp(r'\{(\d+)\.').firstMatch(text);
    if (m == null) return null;
    return int.parse(m.group(1)!);
  }

  static ({
    Archive ar,
    List<(int, String?, Object?)> root,
    List<(int, Object?)> layers,
    List<(int, Object?)> materials
  }) _walk(Uint8List data) {
    final ver = _findVersionMajor(data);
    if (ver == null) {
      throw LegacyParseError('no version string in header');
    }
    final ar = Archive(data, ver);
    ar.readers.addAll(LegacyReaders.readers);
    final r = ar.r;

    final matHdr = _findPattern(data, _cMaterialPattern);
    if (matHdr < 0) {
      throw LegacyParseError('no CMaterial class record found');
    }
    final matCount = Tlv.readU32(data, matHdr - 4);
    if (matCount > 100000) {
      throw LegacyParseError('implausible material count');
    }

    if (matCount < 2) {
      throw LegacyParseError(
          'single-material bootstrap not implemented for this file');
    }
    final boot = Archive(data, ver);
    boot.readers.addAll(LegacyReaders.readers);
    boot.nextSlot = 1 << 20;
    boot.walkBase = 1 << 20;
    boot.r.pos = matHdr;
    boot.readObject(boot.r, 'CMaterial');
    final bootTag = boot.r.peekU16();
    if (bootTag == 0xFFFF || (bootTag & 0x8000) == 0) {
      throw LegacyParseError('cannot bootstrap the slot base');
    }
    ar.nextSlot = bootTag & 0x7FFF;
    ar.walkBase = ar.nextSlot;

    r.pos = matHdr;
    final materials = <(int, Object?)>[];
    for (int i = 0; i < matCount; i++) {
      final (s, _, v) = ar.readObject(r, 'CMaterial');
      materials.add((s!, v));
    }

    r.u32();
    if (ver >= 17) {
      r.u8();
    }
    final layerCount = r.u32();
    if (layerCount > 100000) {
      throw LegacyParseError('implausible layer count');
    }
    final layers = <(int, Object?)>[];
    for (int i = 0; i < layerCount; i++) {
      final (s, _, v) = ar.readObject(r, 'CLayer');
      layers.add((s!, v));
    }

    final (_, dn, __) = ar.readObject(r);
    if (dn != 'CLayer') {
      throw LegacyParseError('definition-list anchor is $dn, not a layer');
    }
    final defCount = r.u32();
    if (defCount > 1000000) {
      throw LegacyParseError('implausible definition count');
    }
    for (int i = 0; i < defCount; i++) {
      ar.readObject(r, 'CComponentDefinition');
    }

    final defCls = ar.classSlot['CComponentDefinition'];
    while (true) {
      final tag = r.peekU16();
      var isDef = defCls != null && tag == (0x8000 | defCls);
      if (!isDef &&
          tag == 0xFFFF &&
          _matchesAscii(r.peek(26), 6, 'CComponentDefinition')) {
        isDef = true;
      }
      if (!isDef) break;
      ar.readObject(r);
    }

    final rootCount = r.u32();
    if (rootCount > 5000000) {
      throw LegacyParseError('implausible root entity count');
    }
    final root = LegacyReaders.readEntityList(ar, r, rootCount, 'root');

    return (ar: ar, root: root, layers: layers, materials: materials);
  }

  /// Mirror of Geometry's GeometryBuilder, kept dependency-free from the
  /// VFF-specific TLV machinery.
  static void _addEdge(
      GeometryBuilder builder, int slot, EdgeRec e, Map<int, SlotEntry> slots) {
    if (builder.edges.containsKey(slot)) return;
    for (final vs in [e.v1, e.v2]) {
      if (vs == null) continue;
      final ent = slots[vs];
      if (ent != null &&
          ent.value != null &&
          !builder.vertices.containsKey(vs)) {
        final xyz = (ent.value as VertexRec).xyz;
        builder.vertices[vs] = (xyz[0], xyz[1], xyz[2]);
      }
    }
    builder.edges[slot] = (e.v1, e.v2);
    final db = e.db;
    final flags = (db.soft != 0 ? 0x08 : 0) |
        (db.smooth != 0 ? 0x10 : 0) |
        (db.hidden != 0 ? 0x01 : 0);
    if (flags != 0) {
      builder.edgeFlags[slot] = flags;
    }
  }

  static void _fillBuilder(GeometryBuilder builder,
      List<(int, String?, Object?)> ents, Map<int, SlotEntry> slots) {
    for (final (s, _, v) in ents) {
      if (v == null) continue;
      if (v is EdgeRec) {
        _addEdge(builder, s, v, slots);
      } else if (v is FaceRec) {
        final loops = <List<(int, int)>>[];
        for (final lp in v.loops) {
          final loop = <(int, int)>[];
          for (final u in lp.uses) {
            final es = u.edge;
            if (es == null) continue;
            final ent = slots[es];
            if (ent == null || ent.value == null) continue;
            _addEdge(builder, es, ent.value as EdgeRec, slots);
            loop.add((es, u.sense != 0 ? 1 : 0));
          }
          loops.add(loop);
        }
        final face = GeometryBuilderFace()
          ..loops = loops
          ..normal = (v.plane[0], v.plane[1], v.plane[2])
          ..materialId = v.db.mat != 0 ? v.db.mat : null
          ..backMaterialId = v.backMat != 0 ? v.backMat : null
          ..uvTransform = null
          ..uvTransformBack = null;
        final attrs = v.attrs;
        if (attrs != null) {
          for (final (_, cv) in attrs.children) {
            if (cv is FtcRec) {
              face.uvTransform = List<double>.from(cv.front);
              face.uvTransformBack = List<double>.from(cv.back);
            }
          }
        }
        builder.faces[s] = face;
      } else if (v is InstanceRec) {
        builder.instances.add(GeometryBuilderInstance()
          ..offset = 0
          ..name = v.name
          ..refIdx = v.def
          ..refGuid = ''
          ..matrix = List<double>.from(v.xf)
          ..materialId = v.db.mat != 0 ? v.db.mat : null
          ..children = const []);
      }
    }
  }

  /// Parse a classic MFC .skp into the shared RawParsed shape, which
  /// Parser converts to the public SkpModel exactly like the VFF path.
  static RawParsed fullParseLegacy(Uint8List data, [ParseOptions? options]) {
    final sw = Stopwatch()..start();
    emitLog(options, SkpLogLevel.info, 'Parsing legacy buffer (${data.length} bytes)');

    var version = 'unknown';
    final second = _findBytes(data, _strMarker, 4);
    if (second > 0) {
      final start = second + 4;
      final len = (start + 100 <= data.length) ? 100 : (data.length - start);
      final codeUnits = <int>[];
      for (int i = start; i + 1 < start + len; i += 2) {
        codeUnits.add(data[i] | (data[i + 1] << 8));
      }
      final text = String.fromCharCodes(codeUnits);
      final braceStart = text.indexOf('{');
      final braceEnd = text.indexOf('}');
      if (braceStart >= 0 && braceEnd >= 0) {
        version = text.substring(braceStart, braceEnd + 1);
      }
    }
    emitLog(options, SkpLogLevel.debug, 'Detected legacy version $version');

    ({
      Archive ar,
      List<(int, String?, Object?)> root,
      List<(int, Object?)> layers,
      List<(int, Object?)> materials
    }) walkResult;
    try {
      walkResult = _walk(data);
    } on LegacyParseError catch (e) {
      throw SkpParseException('legacy .skp parse failed: ${e.message}', stage: 'legacy_walk', cause: e);
    }

    final ar = walkResult.ar;
    final slots = ar.slots;
    emitLog(
      options, SkpLogLevel.debug,
      'Legacy walk complete: ${walkResult.materials.length} materials, ${walkResult.layers.length} layers',
    );

    final materialsMap = <String, RawMaterial>{};
    final materialIdToName = <int, String>{};
    for (final (s, vObj) in walkResult.materials) {
      final v = vObj as MaterialRec;
      final rgba = v.rgba;
      final trans = v.useOpacity != 0 ? (1.0 - v.opacity).clamp(0.0, 1.0) : 1.0;
      final colorized = v.colorized;
      RawTexture? texture;
      if (v.hasTexture) {
        Uint8List? texData;
        final dibEnt = v.texDib != null ? slots[v.texDib] : null;
        if (dibEnt != null && dibEnt.value is DibRec) {
          texData = (dibEnt.value as DibRec).data;
        }
        final isPng = texData != null &&
            texData.length >= 4 &&
            texData[0] == 0x89 &&
            texData[1] == 0x50 &&
            texData[2] == 0x4E &&
            texData[3] == 0x47;
        final ext = isPng ? '.png' : '.jpg';
        final fname = v.texFile.isNotEmpty ? v.texFile : '${v.name}$ext';
        texture = RawTexture(
            filename: fname, xScale: v.texW, yScale: v.texH, data: texData);
      }
      final matObj = RawMaterial(
        name: v.name,
        r: rgba[0],
        g: rgba[1],
        b: rgba[2],
        transparency: trans,
        colorized: colorized,
        // colourize type is not decoded in the legacy record; tint (1) is
        // the correct rendering for the grey base textures observed.
        colorizeType: colorized ? 1 : 0,
        texture: texture,
      );
      materialsMap[v.name] = matObj;
      materialIdToName[s] = v.name;
    }

    final layerColors = <String, (int, int, int)>{};
    final layerIdToName = <int, String>{};
    for (final (s, vObj) in walkResult.layers) {
      final v = vObj as LayerRec;
      final rgba = v.rgba;
      layerColors[v.name] = (rgba[0], rgba[1], rgba[2]);
      layerIdToName[s] = v.name;
    }
    if (!layerColors.containsKey('Layer0')) {
      layerColors['Layer0'] = (136, 136, 136);
    }

    final defsDict = <int, RawDefinition>{};
    var processed = 0;
    var lastSlot = -1;
    try {
      for (final entry in slots.entries) {
        lastSlot = entry.key;
        final ent = entry.value;
        if (ent.kind == 'obj' &&
            ent.name == 'CComponentDefinition' &&
            ent.value != null) {
          final d = ent.value as DefinitionRec;
          final b = GeometryBuilder();
          _fillBuilder(b, d.ents, slots);
          defsDict[entry.key] = RawDefinition(
            guid: d.guid,
            name: d.name,
            isImage: false,
            alwaysFacesCamera: d.facesCamera,
            builder: b,
          );
          processed++;
          if (processed % progressInterval == 0) {
            emitProgress(options, 'legacy_defs', processed, processed);
            emitLog(options, SkpLogLevel.debug, 'Processed $processed component definitions');
          }
        }
      }
    } catch (e) {
      throw SkpParseException(
        'Failed while building component definitions: $e',
        stage: 'legacy_defs', definitionId: lastSlot, cause: e,
      );
    }

    final rootBuilder = GeometryBuilder();
    _fillBuilder(rootBuilder, walkResult.root, slots);

    emitLog(
      options, SkpLogLevel.info,
      'Parse complete: ${defsDict.length} defs (${(sw.elapsedMilliseconds / 1000).toStringAsFixed(2)}s)',
    );

    return RawParsed()
      ..version = version
      ..layerColors.addAll(layerColors)
      ..layerIdToName.addAll(layerIdToName)
      ..materialIdToName.addAll(materialIdToName)
      ..materials.addAll(materialsMap)
      ..styles.addAll(const [])
      ..defsDict.addAll(defsDict)
      ..root =
          RawDefinition(guid: 'ROOT', name: 'ROOT_MODEL', builder: rootBuilder);
  }
}
