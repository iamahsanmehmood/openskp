import 'dart:typed_data';

import 'package:openskp/openskp.dart';
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
      expect(out.point, (1.0, 2.0, 3.0));
      expect(out.labelPoint, (4.0, 5.0, 6.0));
      expect(data.sublist(r.pos), tail);
    });

    test('a vertex-anchored label resolves only without extra or paths', () {
      final directRefs = cat([u16(9), zeros(2), zeros(8)]);
      final direct = cat([textRecord(2, refs: directRefs), tail]);
      final r1 = LR(Uint8List.fromList(direct), 0);
      final out1 = LegacyReaders.readText(archive(direct), r1) as TextRec;
      expect(out1.vertex, 9);
      expect(direct.sublist(r1.pos), tail);

      final extraRefs = cat([u16(9), u16(8), zeros(8)]);
      final withExtra = cat([textRecord(2, refs: extraRefs), tail]);
      final r2 = LR(Uint8List.fromList(withExtra), 0);
      final out2 = LegacyReaders.readText(archive(withExtra), r2) as TextRec;
      expect(out2.vertex, isNull);
      expect(withExtra.sublist(r2.pos), tail);
    });

    test('text delimiter and unanchored vertex scan respect record bounds', () {
      final inlineFont = [
        ...u16(0x8000 | fontClass),
        0x01, 0x00, 0x00, 0x00, 0x01, 0x00, 0x03, 0x00, 0x00, 0x00, 0x01,
      ];
      final earlyMarker = [0xFF, 0xFE, 0xFF, 0x00, ...zeros(12)];
      final rec1 = cat([
        zeros(2), zeros(8), zeros(2), inlineFont, earlyMarker,
        u32(1), u32(4), f64s([1, 2, 3]), zeros(12),
        f64s([4, 5, 6]), f64s([-0.577, -0.577, -0.577]), u32(2),
        [0x01, 0x00, 0x00, 0x00, 0x01, 0x00, 0x03, 0x00, 0x00, 0x00, 0x01],
        utf16('LS1'), zeros(5), tail,
      ]);
      final ar1 = archive(rec1);
      ar1.readers['CSkFont'] = (_, rr) => rr.raw(11);
      final r1 = LR(Uint8List.fromList(rec1), 0);
      final out1 = LegacyReaders.readText(ar1, r1) as TextRec;
      expect(out1.text, 'LS1');
      expect(out1.point, (1.0, 2.0, 3.0));
      expect(rec1.sublist(r1.pos), tail);

      final craftedBytes =
          Uint8List.fromList([0x09, 0x00, 0x00, 0x00, 0x00, 0x00, 0x10, 0x40]);
      final craftedX =
          ByteData.sublistView(craftedBytes).getFloat64(0, Endian.little);
      final rec2 = cat([
        zeros(2), zeros(8), zeros(2), u16(font),
        zeros(6), u32(2), u32(4), f64s([0, 0, 0]),
        craftedBytes, f64s([5, 6]),
        f64s([-0.577, -0.577, -0.577]), u32(2),
        [0x01, 0x00, 0x00, 0x00, 0x01, 0x00, 0x03, 0x00, 0x00, 0x00, 0x01],
        utf16('LS1'), zeros(5), tail,
      ]);
      final r2 = LR(Uint8List.fromList(rec2), 0);
      final out2 = LegacyReaders.readText(archive(rec2), r2) as TextRec;
      expect(out2.vertex, isNull);
      expect(out2.labelPoint, (craftedX, 5.0, 6.0));
      expect(rec2.sublist(r2.pos), tail);
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

  group('classic leader and screen text fixtures (#559)', () {
    test('matches SketchUp SDK anchors, label vectors, and screen positions',
        () {
      final model =
          SkpFile.open('test/fixtures/classic_text_anchors_2017.skp').parse();
      expect(
        model.root.texts.map((t) => t.text).toList(),
        ['Free callout', 'Vertex callout', 'View callout', 'Screen note'],
      );

      final free = model.root.texts[0];
      expect(free.point, (1.0, 2.0, 3.0));
      expect(free.labelPoint, (5.0, 7.0, 9.0));
      expect(free.screenPosition, (0.0, 0.0));
      expect(free.leaderType, 2);
      expect(free.arrowType, 3);
      expect(free.displayLeader, isTrue);
      expect(free.hidden, isFalse);
      expect(free.layer, '');

      final vertex = model.root.texts[1];
      expect(vertex.point, (20.0, 30.0, 0.0));
      expect(vertex.labelPoint, (10.0, 12.0, 14.0));
      expect(vertex.screenPosition, (0.5, 0.5));
      expect(vertex.leaderType, 2);
      expect(vertex.arrowType, 4);
      expect(vertex.displayLeader, isTrue);
      expect(vertex.hidden, isFalse);
      expect(vertex.layer, '');

      final view = model.root.texts[2];
      expect(view.point, (3.0, 4.0, 5.0));
      expect(view.labelPoint, (6.0, 7.0, 8.0));
      expect(view.screenPosition, (0.5, 0.5));
      expect(view.leaderType, 1);
      expect(view.arrowType, 1);
      expect(view.displayLeader, isTrue);
      expect(view.hidden, isFalse);
      expect(view.layer, '');

      final screen = model.root.texts[3];
      expect(screen.point, (0.0, 0.0, 0.0));
      expect(screen.labelPoint, (0.0, 0.0, 0.0));
      expect(screen.screenPosition!.$1, closeTo(0.35, 1e-9));
      expect(screen.screenPosition!.$2, closeTo(0.65, 1e-9));
      expect(screen.leaderType, 0);
      expect(screen.arrowType, 0);
      expect(screen.displayLeader, isTrue);
      expect(screen.hidden, isFalse);
      expect(screen.layer, '');

      final v20 = SkpFile.open(
        'test/fixtures/sdk_saved_leaderless_text_edges_2020.skp',
      ).parse();
      expect(v20.root.texts.length, 2);
      final title = v20.root.texts[0];
      expect(title.text, 'Screen title');
      expect(title.point, (0.0, 0.0, 0.0));
      expect(title.labelPoint, (0.0, 0.0, 0.0));
      expect(title.screenPosition, (0.0, 0.0));
      expect(title.leaderType, 0);
      expect(title.arrowType, 3);
      expect(title.displayLeader, isFalse);

      final callout = v20.root.texts[1];
      expect(callout.text, 'Callout');
      expect(callout.point, (5.0, 5.0, 0.0));
      expect(callout.labelPoint, (7.0, 7.0, 4.0));
      expect(callout.screenPosition, (0.0, 0.0));
      expect(callout.leaderType, 2);
      expect(callout.arrowType, 3);
      expect(callout.displayLeader, isTrue);
    });

    test('resolves face-anchored texts in classic and VFF fixtures', () {
      void expectPoint(
        (double, double, double)? actual,
        (double, double, double) expected,
      ) {
        expect(actual, isNotNull);
        expect(actual!.$1, closeTo(expected.$1, 1e-11));
        expect(actual.$2, closeTo(expected.$2, 1e-11));
        expect(actual.$3, closeTo(expected.$3, 1e-11));
      }

      for (final fixtureName in [
        'classic_face_text_anchors_2017.skp',
        'vff_face_text_anchors_2025.skp',
      ]) {
        final model = SkpFile.open('test/fixtures/$fixtureName').parse();
        expect(
          model.root.texts.map((t) => t.text).toList(),
          ['Root untextured', 'Root textured', 'On component', 'On group'],
        );
        expectPoint(model.root.texts[0].point, (9.0, 10.0, 2.0));
        expectPoint(model.root.texts[0].labelPoint, (3.0, 4.0, 5.0));
        expectPoint(model.root.texts[1].point, (64.5, 3.6, 0.0));
        expectPoint(model.root.texts[1].labelPoint, (4.0, 5.0, 6.0));
        expectPoint(model.root.texts[2].point, (90.0, 56.0, 12.0));
        expectPoint(model.root.texts[2].labelPoint, (5.0, 6.0, 7.0));
        expectPoint(model.root.texts[3].point, (56.0, 32.0, 11.0));
        expectPoint(model.root.texts[3].labelPoint, (6.0, 7.0, 8.0));

        final ownDefs = model.definitions.values
            .where((d) => d.name == 'OwnTextPanel')
            .toList();
        expect(ownDefs.length, 1);
        expect(ownDefs[0].texts.map((t) => t.text).toList(), ['In component']);
        expectPoint(ownDefs[0].texts[0].point, (3.0, 5.0, 1.0));
        expectPoint(ownDefs[0].texts[0].labelPoint, (2.0, 3.0, 4.0));
      }

      final capilla =
          SkpFile.open('test/fixtures/capilla_quiroz_v17.skp').parse();
      final areaTexts =
          capilla.root.texts.where((t) => t.text == '4.06 m²').toList();
      expect(areaTexts.length, 1);
      expectPoint(
        areaTexts[0].point,
        (19.346940925615975, 154.92910008345638, 112.92862002631385),
      );
    });
  });
}
