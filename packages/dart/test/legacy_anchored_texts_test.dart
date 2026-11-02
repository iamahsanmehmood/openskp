import 'dart:typed_data';

import 'package:openskp/src/legacy.dart';
import 'package:test/test.dart';

/// Legacy (MFC) text labels anchored to an entity - port of the Python fix.
///
/// 16 bytes after its font, a CText carries the connection block dimensions
/// use, `[u32 type][u32 4][point3d]`: type 0 or 1 when the label is anchored
/// to nothing (1 for a leader pointing at a free point stored inline), other
/// types (2, 4, 5 and 7 seen) for a label anchored to an entity. As in a
/// dimension, the block is followed by the anchored entity's ref and its
/// instance paths, null and empty for types 0 and 1. MFC writes an object in
/// full the first time it is referenced, so a path can hold a whole NEW
/// instance: the component a label is anchored to, when the label comes first
/// in its entity list. The reader skipped the block as bytes, so that instance
/// never got its store-map slots and every later reference was off by them
/// ("class-ref to non-class slot"): the auditorium (CR4) models of the BRAS
/// database, whose loudspeaker labels are anchored to the loudspeakers, did
/// not open, and two of its coupled-room (CR1) models lost root entities (one
/// read 5 of its 318). The entity list's declared count holds such an
/// instance, but SketchUp does not always list it again with a back-reference.
void main() {
  const text = 1, fontClass = 2, font = 3, thing = 4; // pre-registered slots
  const firstFree = 5;

  List<int> u16(int n) => [n & 0xFF, (n >> 8) & 0xFF];
  List<int> u32(int n) =>
      [n & 0xFF, (n >> 8) & 0xFF, (n >> 16) & 0xFF, (n >> 24) & 0xFF];
  List<int> zeros(int n) => List.filled(n, 0);
  List<int> f64s(List<double> xs) {
    final b = ByteData(8 * xs.length);
    for (var i = 0; i < xs.length; i++) {
      b.setFloat64(8 * i, xs[i], Endian.little);
    }
    return b.buffer.asUint8List();
  }

  List<int> utf16(String s) =>
      [0xFF, 0xFE, 0xFF, s.length, for (final c in s.codeUnits) ...u16(c)];
  List<int> cat(List<List<int>> parts) => [for (final p in parts) ...p];
  const tail = [0x54, 0x41, 0x49, 0x4C]; // "TAIL"

  /// A SketchUp 2015 archive that knows CText, a font and a stand-in class
  /// for the anchored instance, whose objects are 3 bytes long.
  Archive archive(List<int> data) {
    final ar = Archive(Uint8List.fromList(data), 15);
    ar.readers.addAll(LegacyReaders.readers);
    ar.readers['CThing'] = (ar, r) => r.raw(3);
    ar.slots[text] = SlotEntry(kind: 'class', name: 'CText');
    ar.slots[fontClass] = SlotEntry(kind: 'class', name: 'CSkFont');
    ar.slots[font] = SlotEntry(kind: 'obj', name: 'CSkFont');
    ar.slots[thing] = SlotEntry(kind: 'class', name: 'CThing');
    ar.classSlot['CText'] = text;
    ar.classSlot['CSkFont'] = fontClass;
    ar.classSlot['CThing'] = thing;
    ar.nextSlot = firstFree;
    return ar;
  }

  /// A CText's bytes after its class tag. [refs] is what follows the
  /// connection block: the anchored entity's ref and its instance paths.
  List<int> textRecord(int kind,
          {List<double> point = const [0, 0, 0],
          List<int>? refs,
          List<double> label = const [0, 0, 0]}) =>
      cat([
        zeros(2), // no attribute container
        zeros(8), zeros(2), // draw block, layer 0
        u16(font), // the font, a back-reference
        zeros(16),
        u32(kind), u32(4), f64s(point),
        refs ?? zeros(12),
        f64s(label),
        f64s([-0.577, -0.577, -0.577]),
        u32(2),
        [0x01, 0x00, 0x00, 0x00, 0x01, 0x00, 0x03, 0x00, 0x00, 0x00, 0x01],
        utf16('LS1'),
        zeros(5),
      ]);

  List<int> newThing() => [...u16(0x8000 | thing), 0x4F, 0x42, 0x4A]; // OBJ
  // Anchored to entity 9 inside an instance written here in full.
  List<int> pathsWithNewInstance() =>
      cat([u16(9), zeros(2), u32(1), newThing(), u32(0)]);
  // No relationships, then a GUID and the definition's name.
  List<int> definitionTail() =>
      cat([zeros(4), zeros(2), zeros(16), utf16('LS')]);
  List<(int, String?)> items(List<(int, String?, Object?)> ents) =>
      [for (final e in ents) (e.$1, e.$2)];

  group('a legacy text label anchored to an entity', () {
    for (final (kind, point) in [
      (2, [0.0, 0.0, 0.0]),
      (4, [0.0, 0.0, 0.0]),
      (5, [12.5, -3.0, 40.0]),
      (7, [1.0, 2.0, 3.0]),
    ]) {
      test('type $kind reads the instance written in its path', () {
        final data = cat([
          textRecord(kind, point: point, refs: pathsWithNewInstance()),
          tail
        ]);
        final ar = archive(data);
        final r = LR(Uint8List.fromList(data), 0);
        final out = LegacyReaders.readText(ar, r) as TextRec;
        expect(out.text, 'LS1');
        expect(ar.slots[firstFree]!.kind, 'obj');
        expect(ar.slots[firstFree]!.name, 'CThing');
        expect(data.sublist(r.pos), tail);
      });
    }

    test('a free label reads as before', () {
      final data = cat([
        textRecord(1, point: [1, 2, 3], label: [4, 5, 6]),
        tail
      ]);
      final r = LR(Uint8List.fromList(data), 0);
      final out = LegacyReaders.readText(archive(data), r) as TextRec;
      expect(out.text, 'LS1');
      expect(data.sublist(r.pos), tail);
    });
  });

  group('an entity list holding an anchored label', () {
    test('keeps an instance written in a label and never listed again', () {
      final data = cat([
        u16(0x8000 | text),
        textRecord(2, refs: pathsWithNewInstance()),
        definitionTail(),
      ]);
      final ar = archive(data);
      final r = LR(Uint8List.fromList(data), 0);
      final ents = LegacyReaders.readEntityList(ar, r, 2, 'def');
      expect(items(ents), [(firstFree, 'CText'), (firstFree + 1, 'CThing')]);
      expect(data.sublist(r.pos), definitionTail());
    });

    test('lists an instance written in a label and listed again once', () {
      final data = cat([
        u16(0x8000 | text),
        textRecord(2, refs: pathsWithNewInstance()),
        u16(firstFree + 1), // its back-reference
        definitionTail(),
      ]);
      final ar = archive(data);
      final r = LR(Uint8List.fromList(data), 0);
      final ents = LegacyReaders.readEntityList(ar, r, 2, 'def');
      expect(items(ents), [(firstFree, 'CText'), (firstFree + 1, 'CThing')]);
      expect(data.sublist(r.pos), definitionTail());
    });

    test("only the outermost instance of a path is one of the list's items",
        () {
      // A path runs from the outermost instance in; the inner ones live in
      // other definitions.
      final refs =
          cat([u16(9), zeros(2), u32(2), newThing(), newThing(), u32(0)]);
      final data = cat([
        u16(0x8000 | text),
        textRecord(2, refs: refs),
        definitionTail(),
      ]);
      final ar = archive(data);
      final r = LR(Uint8List.fromList(data), 0);
      final ents = LegacyReaders.readEntityList(ar, r, 2, 'def');
      expect(items(ents), [(firstFree, 'CText'), (firstFree + 1, 'CThing')]);
      expect(ar.slots[firstFree + 2]!.name, 'CThing'); // read, not listed
      expect(data.sublist(r.pos), definitionTail());
    });
  });
}
