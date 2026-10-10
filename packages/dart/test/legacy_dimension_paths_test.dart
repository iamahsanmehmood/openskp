import 'dart:typed_data';

import 'package:openskp/src/legacy.dart';
import 'package:test/test.dart';

/// Legacy (MFC) linear dimensions anchored inside groups - port of the
/// Python fix (openskp#384) to this reader (openskp#412).
///
/// Each connection ref of a CDimensionLinear is followed by an entity ref and
/// two lists of entity refs - the instance paths of the anchored entity. On
/// loose geometry they are a null ref and two empty lists (the zeros a fixed
/// 42/82-byte layout used to skip); anchored inside nested components they
/// carry a ref per component, and a fixed-size read slid off the record,
/// silently cutting the root entity list short. The first time an instance is
/// referenced MFC writes it in full, so a path can also hold a whole NEW
/// object.
///
/// Synthetic bytes only: the real file that exposed this (a SketchUp 2017
/// cabinet, 21 of 96 root instances lost) is private and is not committed.
void main() {
  /// A 6-byte big reference (0x7FFF escape) to an existing object.
  List<int> big(int slot) => [
        0xFF,
        0x7F,
        slot & 0xFF,
        (slot >> 8) & 0xFF,
        (slot >> 16) & 0xFF,
        (slot >> 24) & 0xFF
      ];
  List<int> u32(int n) =>
      [n & 0xFF, (n >> 8) & 0xFF, (n >> 16) & 0xFF, (n >> 24) & 0xFF];
  Uint8List cat(List<List<int>> parts) =>
      Uint8List.fromList([for (final p in parts) ...p]);
  const tail = [0x54, 0x41, 0x49, 0x4C]; // "TAIL"
  int? neverNew(LR r) => throw StateError('unexpected new object');

  test('loose geometry: paths are empty and ten bytes', () {
    final data = cat([
      [0, 0],
      u32(0),
      u32(0),
      tail
    ]);
    final r = LR(data, 0);
    final (extra, first, second) = LegacyReaders.connectionPaths(neverNew, r);
    expect(extra, isNull);
    expect(first, isEmpty);
    expect(second, isEmpty);
    expect(r.pos, 10); // the old fixed layout's zeros
  });

  test('paths inside nested components are read ref by ref', () {
    final data = cat([
      big(0x0F69CA),
      u32(2),
      big(0x166157),
      big(0x165BBC),
      u32(2),
      big(0x166157),
      big(0x165BBD),
      tail
    ]);
    final r = LR(data, 0);
    final (extra, first, second) = LegacyReaders.connectionPaths(neverNew, r);
    expect(extra, 0x0F69CA);
    expect(first, [0x166157, 0x165BBC]);
    expect(second, [0x166157, 0x165BBD]);
    expect(data.sublist(r.pos), tail);
  });

  test('a path can hold a whole new object', () {
    // MFC serializes an object in full the first time it is referenced.
    const newTag = [0xFF, 0x7F, 0x67, 0x9F, 0x00, 0x80];
    final data = cat([
      [0, 0],
      u32(1),
      newTag,
      [0x4F, 0x42, 0x4A],
      u32(0),
      tail
    ]);
    final r = LR(data, 0);
    final (extra, first, second) = LegacyReaders.connectionPaths((rr) {
      expect(data.sublist(rr.pos, rr.pos + 6), newTag);
      rr.pos += 6 + 3; // the object's own bytes
      return 4242;
    }, r);
    expect(extra, isNull);
    expect(first, [4242]);
    expect(second, isEmpty);
    expect(data.sublist(r.pos), tail);
  });

  test('an implausible path length is rejected', () {
    final data = cat([
      [0, 0],
      u32(100000)
    ]);
    expect(() => LegacyReaders.connectionPaths(neverNew, LR(data, 0)),
        throwsA(isA<LegacyParseError>()));
  });
}
