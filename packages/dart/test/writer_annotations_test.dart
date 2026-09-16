import 'package:openskp/openskp.dart';
import 'package:test/test.dart';

/// Covers the writer side of openskp#285's "Writer: section planes" item -
/// addSectionPlane/addDimension/addText/addConstructionLine/
/// addConstructionPoint on SkpBuilder, ported byte-for-byte from
/// create.py's own methods of the same name (see that file's docstrings
/// for the real-SketchUp ground truth these record layouts were harvested
/// from).
///
/// SectionPlane/Text/Dimension round-trip fully since legacy.dart's own
/// readers for them already expose real data on model.root. Construction
/// Line/Point now round-trip fully too - legacy.dart's readers for those
/// two used to parse the geometry and then discard it rather than
/// exposing it on model.root at all, fixed alongside adding this writer
/// (openskp#285's "Writer + reader: construction lines/points").
void main() {
  const square = [
    (0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (10.0, 10.0, 0.0), (0.0, 10.0, 0.0),
  ];

  group('writer: section plane / text / dimension round trip', () {
    test('round-trips section plane, text, and dimension', () {
      final builder = create();
      builder.addFace(square);
      builder.addSectionPlane((10.0, 20.0, 30.0), (0.0, 0.0, 1.0));
      builder.addText('Hello', (5.0, 5.0, 5.0));
      builder.addDimension((0.0, 0.0, 0.0), (10.0, 0.0, 0.0));

      final model = SkpFile.fromBuffer(builder.toBytes()).parse();

      expect(model.root.sectionPlanes, hasLength(1));
      final sp = model.root.sectionPlanes[0];
      expect(sp.plane[0], closeTo(0.0, 1e-6));
      expect(sp.plane[1], closeTo(0.0, 1e-6));
      expect(sp.plane[2], closeTo(1.0, 1e-6));
      expect(sp.plane[3], closeTo(-30.0, 1e-6));
      expect(sp.hidden, isFalse);

      expect(model.root.texts, hasLength(1));
      expect(model.root.texts[0].text, 'Hello');
      expect(model.root.texts[0].hidden, isFalse);

      expect(model.root.dimensions, hasLength(1));
    });

    test('normalizes a non-unit normal', () {
      final builder = create();
      builder.addFace(square);
      builder.addSectionPlane((0.0, 0.0, 5.0), (0.0, 0.0, 2.0));

      final model = SkpFile.fromBuffer(builder.toBytes()).parse();
      final sp = model.root.sectionPlanes[0];
      expect(sp.plane[2], closeTo(1.0, 1e-6));
      expect(sp.plane[3], closeTo(-5.0, 1e-6));
    });

    test('shares one embedded font across two dimensions', () {
      // addDimension/addText only ever embed the CSkFont payload inline on
      // the FIRST call and back-ref it afterwards - exercise that shared-
      // state path across a call pair without asserting on font bytes
      // directly (the point here is that a second dimension doesn't
      // corrupt the archive's slot numbering).
      final builder = create();
      builder.addFace(square);
      builder.addDimension((0.0, 0.0, 0.0), (10.0, 0.0, 0.0));
      builder.addDimension((0.0, 5.0, 0.0), (10.0, 5.0, 0.0));
      builder.addText('First', (1.0, 1.0, 1.0));

      final model = SkpFile.fromBuffer(builder.toBytes()).parse();
      expect(model.root.dimensions, hasLength(2));
      expect(model.root.texts, hasLength(1));
    });

    test('construction line and point round trip', () {
      // legacy.dart's readConstructionLine used to parse point/direction/
      // start/end into locals and then discard all of them (the same
      // shape as the SectionPlane/Text/Dimension bugs fixed in a prior
      // session) - fixed alongside adding this writer, matching C++'s own
      // reader, which already exposed this correctly.
      final builder = create();
      builder.addFace(square);
      builder.addConstructionPoint((1.0, 2.0, 3.0));
      builder.addConstructionLine((0.0, 0.0, 0.0), point2: (10.0, 0.0, 0.0));
      builder.addConstructionLine((0.0, 0.0, 0.0), direction: (0.0, 0.0, 1.0));
      builder.addSectionPlane((10.0, 20.0, 30.0), (0.0, 0.0, 1.0)); // still readable afterwards

      final model = SkpFile.fromBuffer(builder.toBytes()).parse();

      expect(model.root.constructionPoints, hasLength(1));
      expect(model.root.constructionPoints[0].position, (1.0, 2.0, 3.0));

      expect(model.root.constructionLines, hasLength(2));
      final bounded = model.root.constructionLines[0];
      expect(bounded.point, (0.0, 0.0, 0.0));
      expect(bounded.direction, (1.0, 0.0, 0.0));
      expect(bounded.start, (0.0, 0.0, 0.0));
      expect(bounded.end, (10.0, 0.0, 0.0));

      final unbounded = model.root.constructionLines[1];
      expect(unbounded.direction, (0.0, 0.0, 1.0));
      expect(unbounded.start, isNull);
      expect(unbounded.end, isNull);

      expect(model.root.sectionPlanes[0].plane[3], closeTo(-30.0, 1e-6));
    });

    test('rejects a zero normal', () {
      final builder = create();
      builder.addFace(square);
      expect(
        () => builder.addSectionPlane((0.0, 0.0, 0.0), (0.0, 0.0, 0.0)),
        throwsA(isA<SkpWriteError>()),
      );
    });

    test('requires exactly one of point2 or direction', () {
      final builder = create();
      builder.addFace(square);
      expect(
        () => builder.addConstructionLine((0.0, 0.0, 0.0)),
        throwsA(isA<SkpWriteError>()),
      );
      expect(
        () => builder.addConstructionLine(
          (0.0, 0.0, 0.0),
          point2: (1.0, 0.0, 0.0),
          direction: (0.0, 1.0, 0.0),
        ),
        throwsA(isA<SkpWriteError>()),
      );
    });
  });
}
