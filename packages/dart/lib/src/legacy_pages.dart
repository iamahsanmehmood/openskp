import 'dart:convert';
import 'dart:typed_data';

import 'legacy.dart';
import 'pages_dimensions.dart';

// ── full scene records ───────────────────────────────────────────────────
//
// Ground truth, all read byte-exactly: the gondola_v20.skp fixture (SketchUp
// 2020, one scene, flags 0x1FF); SketchUp's own 3D Warehouse model "Skill
// Builder: Creating Section Animations with Scenes" in its SketchUp 2017
// format (eight scenes, 0x7F) and SketchUp 2015 saves (nine scenes, 0x7F) -
// not redistributable, so not committed; files written by openskp.create,
// which SketchUp Free opens with working scenes; and files built and saved
// through SketchUp's C API as 2017 to 2020 (schema 15), whose names, cameras
// and hidden layers SketchUp's own library reads back identically.
//
// The scene list sits in the document tail as a u32 count right before the
// first record (which declares the CViewPage class), then one record per
// scene, then a ref to the selected scene. A record is: the class tag | a null
// attribute ref | (schema 13+, SketchUp 2016+, only) a pid | name |
// description | u32 capture flags | the camera (CCamera, or null) | the
// thumbnail (CDib, or null) | (schema 13) a byte | then one section per
// capture flag that is set:
//
//   0x02 style       the rendering options (length by file version, see
//                    _renderingOptionsLen) + a CSkpStyle ref or object
//   0x04 shadows     2 bytes (schema 13+: 3) + the shadow info
//   0x08 axes        (schema 13+: a flag byte +) 110 bytes
//   0x10 hidden      a byte, u32 n, n entity refs
//   0x20 layers      u32 n, n refs to the layers (tags) the scene hides
//   0x40 sections    u32 n, n section-plane refs
//
// | 22 bytes (transition and delay time, flags) | u32 n + an n-byte PNG.
//
// Schema 15 (a file saved through the C API as 2017 to 2020) differs: flag
// 0x01 is the camera, which is only there when the flag is set, with no CDib
// tag, no byte after it and no thumbnail; the rendering options are 3 bytes
// longer; the style options have two more value types (3: 2 bytes, 5: 4
// bytes); and the trailer is 21 bytes with no PNG after it.
//
// A record this reader can't fully read, or flags outside 0x1FF, make
// readPageList give up (it returns null). Python then falls back to a narrow
// scan of the file tail that finds scene names and hidden layers only; that
// fallback is not ported, so these readers return no scenes there.
const int _knownFlags = 0x1FF;
const int _axesLen = 110;
const int _trailerLen = 22;
const int _shadowInfoFixedLen = 60; // after the city and country strings

// The rendering options' length by SketchUp major version, measured on the
// model-level copy of the same block in every fixture from 2014 to 2020 (and
// in the 2020 scene); other versions aren't known, so give up there.
const Map<int, int> _renderingOptionsLen = {
  14: 206,
  15: 206,
  16: 206,
  17: 206,
  18: 211,
  19: 212,
  20: 213,
};

// CSkpStyle option values: type tag -> byte size
const Map<int, int> _styleValueSizes = {1: 1, 3: 2, 4: 4, 5: 4, 6: 4, 7: 8};

final List<int> _className = ascii.encode('CViewPage');

/// The first CViewPage declaration at or after [from]:
/// `ff ff <schema 12|13|15> 00 09 00 CViewPage`.
int _findDecl(Uint8List data, int from) {
  for (var i = from; i + 6 + _className.length <= data.length; i++) {
    if (data[i] != 0xFF || data[i + 1] != 0xFF) continue;
    final schema = data[i + 2];
    if (schema != 0x0C && schema != 0x0D && schema != 0x0F) continue;
    if (data[i + 3] != 0x00 || data[i + 4] != 0x09 || data[i + 5] != 0x00) {
      continue;
    }
    var ok = true;
    for (var j = 0; j < _className.length; j++) {
      if (data[i + 6 + j] != _className[j]) {
        ok = false;
        break;
      }
    }
    if (ok) return i;
  }
  return -1;
}

enum _TagKind { nullTag, decl, classRef, obj }

/// One archive tag, with the 0x7FFF escape to 32 bits resolved.
(_TagKind, int) _readTag(LR r) {
  final v = r.u16();
  if (v == 0) return (_TagKind.nullTag, 0);
  if (v == 0xFFFF) return (_TagKind.decl, 0);
  if (v == 0x7FFF) {
    final w = r.u32();
    return (w & 0x80000000) != 0
        ? (_TagKind.classRef, w & 0x7FFFFFFF)
        : (_TagKind.obj, w);
  }
  return (v & 0x8000) != 0 ? (_TagKind.classRef, v & 0x7FFF) : (_TagKind.obj, v);
}

int _popcount(int mask) {
  var n = 0;
  for (var bit = 0; bit < 8; bit++) {
    if ((mask & (1 << bit)) != 0) n++;
  }
  return n;
}

void _skipPid(LR r) => r.raw(_popcount(r.u8()));

/// The classes the scene records declare or refer to: the schema each was
/// declared with and, once seen, the slot its class-refs use - every class-ref
/// to one class must carry the same slot, which catches a record read from the
/// wrong offset early.
class _PageClasses {
  // CCamera and CDib are declared in the document prefix; the scene records
  // only refer to them, at the schemas every v6+ file uses.
  final Map<String, int> _schema = {'CCamera': 5, 'CDib': 3};
  final Map<String, int> _slot = {};

  /// After a decl or class tag: the declaration's header (if any); returns the
  /// schema the object was written with.
  int object(LR r, _TagKind kind, int slot, String name) {
    if (kind == _TagKind.decl) {
      final schema = r.u16();
      final n = r.u16();
      final got = ascii.decode(r.raw(n), allowInvalid: true);
      if (n != name.length || got != name) {
        throw LegacyParseError('expected a $name declaration ${r.ctx()}');
      }
      _schema[name] = schema;
    } else {
      final have = _slot[name];
      if (have == null) {
        _slot[name] = slot;
      } else if (have != slot) {
        throw LegacyParseError('$name class-ref to another slot ${r.ctx()}');
      }
    }
    final schema = _schema[name];
    if (schema == null) {
      throw LegacyParseError('$name object of an unknown schema ${r.ctx()}');
    }
    return schema;
  }
}

void _readCamera(LR r, RawPage page) {
  final eye = r.f64s(3);
  final target = r.f64s(3);
  final up = r.f64s(3);
  r.f64s(2); // constants 1.0, 1000.0
  final perspective = r.u8();
  final fov = r.f64();
  final orthoHeight = r.f64();
  r.raw(137 - 105);
  r.u16();
  r.utf16();
  r.raw(33);
  page.eye = (eye[0], eye[1], eye[2]);
  page.target = (target[0], target[1], target[2]);
  page.up = (up[0], up[1], up[2]);
  page.fov = fov;
  page.parallel = perspective == 0;
  page.orthoHeight = orthoHeight;
}

/// A CSkpStyle object's body: no attributes, a pid (schema 2+), GUID, a
/// string, u32, name, description, then its options - each a u32 key and a u32
/// count of (u32 type, value) pairs.
void _skipStyle(LR r, int schema) {
  if (r.u16() != 0) throw LegacyParseError('style with attributes ${r.ctx()}');
  if (schema >= 2) _skipPid(r);
  r.raw(16);
  r.utf16();
  r.u32();
  r.utf16();
  r.utf16();
  final n = r.u32();
  for (var i = 0; i < n; i++) {
    r.u32();
    final m = r.u32();
    for (var j = 0; j < m; j++) {
      final size = _styleValueSizes[r.u32()];
      if (size == null) {
        throw LegacyParseError('unknown style value type ${r.ctx()}');
      }
      r.raw(size);
    }
  }
}

List<int> _readRefs(LR r, int n) {
  if (n > (r.data.length - r.pos) ~/ 2) {
    throw LegacyParseError('implausible ref count $n ${r.ctx()}');
  }
  final refs = <int>[];
  for (var i = 0; i < n; i++) {
    final (kind, v) = _readTag(r);
    if (kind != _TagKind.obj) {
      throw LegacyParseError('expected a back-ref ${r.ctx()}');
    }
    refs.add(v);
  }
  return refs;
}

RawPage _readRecord(Archive ar, LR r, _PageClasses classes) {
  var (kind, slot) = _readTag(r);
  if (kind != _TagKind.decl && kind != _TagKind.classRef) {
    throw LegacyParseError('expected a CViewPage record ${r.ctx()}');
  }
  final schema = classes.object(r, kind, slot, 'CViewPage');
  if (r.u16() != 0) throw LegacyParseError('scene with attributes ${r.ctx()}');
  if (schema >= 13) _skipPid(r);
  final page = RawPage()..name = r.utf16();
  r.utf16(); // description
  final flags = r.u32();
  if ((flags & ~_knownFlags) != 0) {
    throw LegacyParseError(
        'scene capture flags 0x${flags.toRadixString(16)} ${r.ctx()}');
  }
  if (schema >= 15) {
    // saved through SketchUp's C API: the camera is there when its flag is, and
    // there is no thumbnail
    if ((flags & 0x01) != 0) {
      (kind, slot) = _readTag(r);
      if (kind != _TagKind.decl && kind != _TagKind.classRef) {
        throw LegacyParseError('expected a camera ${r.ctx()}');
      }
      classes.object(r, kind, slot, 'CCamera');
      _readCamera(r, page);
    }
  } else {
    (kind, slot) = _readTag(r);
    if (kind == _TagKind.decl || kind == _TagKind.classRef) {
      classes.object(r, kind, slot, 'CCamera');
      _readCamera(r, page);
    }
    (kind, slot) = _readTag(r);
    if (kind == _TagKind.decl || kind == _TagKind.classRef) {
      classes.object(r, kind, slot, 'CDib');
      LegacyReaders.readDib(ar, r);
    }
    if (schema >= 13) r.u8();
  }
  if ((flags & 0x02) != 0) {
    final base = _renderingOptionsLen[ar.ver];
    if (base == null) {
      throw LegacyParseError('rendering options of a v${ar.ver} scene');
    }
    r.raw(base + (schema >= 15 ? 3 : 0));
    (kind, slot) = _readTag(r);
    if (kind == _TagKind.decl || kind == _TagKind.classRef) {
      _skipStyle(r, classes.object(r, kind, slot, 'CSkpStyle'));
    }
  }
  if ((flags & 0x04) != 0) {
    r.raw(schema >= 13 ? 3 : 2);
    r.u32(); // time
    r.u8();
    r.utf16(); // city
    r.utf16(); // country
    r.raw(_shadowInfoFixedLen);
  }
  if ((flags & 0x08) != 0) r.raw(_axesLen + (schema >= 13 ? 1 : 0));
  if ((flags & 0x10) != 0) {
    r.u8();
    _readRefs(r, r.u32());
  }
  if ((flags & 0x20) != 0) page.hiddenLayerIds.addAll(_readRefs(r, r.u32()));
  if ((flags & 0x40) != 0) _readRefs(r, r.u32());
  if (schema >= 15) {
    r.raw(_trailerLen - 1);
    return page;
  }
  r.raw(_trailerLen);
  final png = r.u32();
  if (png > r.data.length - r.pos) {
    throw LegacyParseError('implausible thumbnail length $png ${r.ctx()}');
  }
  r.raw(png);
  return page;
}

/// Every scene, with its camera and hidden layers, read as a sequence from the
/// scene list in the file tail. `null` when there is a scene list this reader
/// can't fully read; an empty list when there is none.
List<RawPage>? readPageList(Uint8List data, Archive ar) {
  final start = _findDecl(data, ar.r.pos);
  if (start < 0) {
    // a scene list in a schema this reader doesn't know
    final found = _findBytesIn(data, _className, ar.r.pos);
    return found >= 0 ? null : <RawPage>[];
  }
  try {
    final count = ByteData.sublistView(data).getUint32(start - 4, Endian.little);
    if (count < 1 || count > 10000) {
      throw LegacyParseError('implausible scene count $count');
    }
    final r = LR(data, start);
    final classes = _PageClasses();
    final pages = <RawPage>[];
    for (var i = 0; i < count; i++) {
      pages.add(_readRecord(ar, r, classes));
    }
    // the list ends with a ref to the selected scene (or a null ref)
    final (kind, _) = _readTag(r);
    if (kind != _TagKind.obj && kind != _TagKind.nullTag) {
      throw LegacyParseError(
          'no selected-scene ref after the scenes ${r.ctx()}');
    }
    for (final page in pages) {
      final kept = page.hiddenLayerIds.where((id) {
        final ent = ar.slots[id];
        return ent != null && ent.kind == 'obj' && ent.name == 'CLayer';
      }).toList();
      page.hiddenLayerIds
        ..clear()
        ..addAll(kept);
    }
    return pages;
  } on LegacyParseError {
    return null;
  } on RangeError {
    return null;
  }
}

int _findBytesIn(Uint8List data, List<int> needle, int start) {
  for (var i = start; i + needle.length <= data.length; i++) {
    var ok = true;
    for (var j = 0; j < needle.length; j++) {
      if (data[i + j] != needle[j]) {
        ok = false;
        break;
      }
    }
    if (ok) return i;
  }
  return -1;
}
