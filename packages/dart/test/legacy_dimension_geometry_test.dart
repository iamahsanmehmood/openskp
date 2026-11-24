import 'dart:typed_data';

import 'package:openskp/openskp.dart';
import 'package:test/test.dart';

const Point3 kA = (1.0, 2.0, 3.0);
const Point3 kB = (4.0, 6.0, 3.0);
const Point3 kC = (11.0, 12.0, 13.0);
const Point3 kD = (14.0, 16.0, 13.0);

final Uint8List kVertexClass = Uint8List.fromList([
  0xFF, 0xFF, 0, 0, 7, 0,
  0x43, 0x56, 0x65, 0x72, 0x74, 0x65, 0x78, // "CVertex"
]);

Uint8List u32(int n) {
  final bd = ByteData(4)..setUint32(0, n, Endian.little);
  return bd.buffer.asUint8List();
}

Uint8List f64s(List<double> vals) {
  final bd = ByteData(vals.length * 8);
  for (var i = 0; i < vals.length; i++) {
    bd.setFloat64(i * 8, vals[i], Endian.little);
  }
  return bd.buffer.asUint8List();
}

Uint8List concatBytes(List<List<int>> parts) {
  final b = BytesBuilder(copy: false);
  for (final p in parts) {
    b.add(p);
  }
  return b.toBytes();
}

Uint8List refBytes(int slot, {bool large = false}) {
  if (large) return concatBytes([[0xFF, 0x7F], u32(slot)]);
  return Uint8List.fromList([slot & 0xFF, (slot >> 8) & 0xFF]);
}

Uint8List pointBytes(Point3 p) => f64s([p.$1, p.$2, p.$3]);

Uint8List connectionBytes(Point3 p) => concatBytes([u32(1), u32(4), pointBytes(p)]);

int locate(List<int> bytes, List<int> needle) {
  for (var i = 0; i + needle.length <= bytes.length; i++) {
    var match = true;
    for (var j = 0; j < needle.length; j++) {
      if (bytes[i + j] != needle[j]) {
        match = false;
        break;
      }
    }
    if (match) return i;
  }
  throw StateError('dimension test record not found');
}

List<int> fixtureBytes() {
  final builder = create();
  builder.addDimension(kA, kB, offset: -2.0);
  builder.addDimension(kC, kD, offset: 3.0);
  builder.addConstructionPoint((101.0, 102.0, 103.0));
  return builder.toBytes().toList();
}

int fontSlot(List<int> bytes) {
  final at = locate(bytes, connectionBytes(kC)) - 7;
  return bytes[at] | (bytes[at + 1] << 8);
}

Uint8List vertexBytes(Point3 p, List<int> tag) =>
    concatBytes([tag, [0, 0, 0], pointBytes(p)]);

void attach(List<int> bytes, Point3 original, List<int> objectBytes) {
  final at = locate(bytes, connectionBytes(original));
  bytes[at] = 2;
  for (var i = at + 8; i < at + 32; i++) {
    bytes[i] = 0;
  }
  bytes.removeRange(at + 32, at + 34);
  bytes.insertAll(at + 32, objectBytes);
}

void expectPoint((double, double, double)? actual, Point3 expected) {
  expect(actual, isNotNull);
  expect(actual!.$1, closeTo(expected.$1, 1e-9));
  expect(actual.$2, closeTo(expected.$2, 1e-9));
  expect(actual.$3, closeTo(expected.$3, 1e-9));
}

void expectTail(SkpModel model) {
  expect(model.root.constructionPoints, hasLength(1));
  expect(model.root.constructionPoints[0].position, (101.0, 102.0, 103.0));
}

void main() {
  group('legacy dimension geometry and vector offsets (#481)', () {
    test('free endpoints and frame survive parsing', () {
      final model = SkpFile.fromBuffer(Uint8List.fromList(fixtureBytes())).parse();
      expect(model.root.dimensions, hasLength(2));
      final first = model.root.dimensions[0];
      expectPoint(first.a, kA);
      expectPoint(first.b, kB);
      expect(first.offset, -2.0);
      expectPoint(first.planeX, (1.0, 0.0, 0.0));
      expectPoint(first.normal, (0.0, 0.0, 1.0));
      expect(first.offsetVector, isNull);
      expectPoint(model.root.dimensions[1].a, kC);
      expectPoint(model.root.dimensions[1].b, kD);
      expectTail(model);
    });

    test('inline vertices and later backrefs keep archive aligned', () {
      for (final large in [false, true]) {
        final bytes = fixtureBytes();
        final font = fontSlot(bytes);
        attach(bytes, kD, refBytes(font + 3, large: large));
        attach(bytes, kC, refBytes(font + 2, large: large));
        attach(
          bytes,
          kB,
          vertexBytes(
            kB,
            refBytes((large ? 0x80000000 : 0x8000) | (font + 1), large: large),
          ),
        );
        attach(bytes, kA, vertexBytes(kA, kVertexClass));
        final model = SkpFile.fromBuffer(Uint8List.fromList(bytes)).parse();
        expect(model.root.dimensions, hasLength(2));
        for (final dim in model.root.dimensions) {
          expectPoint(dim.a, kA);
          expectPoint(dim.b, kB);
        }
        expectTail(model);
      }
    });

    test('forward vertex references resolve after archive walk', () {
      final bytes = fixtureBytes();
      final font = fontSlot(bytes);
      attach(bytes, kC, vertexBytes(kC, refBytes(0x8000 | (font + 1))));
      attach(bytes, kB, vertexBytes(kB, kVertexClass));
      attach(bytes, kA, refBytes(font + 4, large: true));
      final model = SkpFile.fromBuffer(Uint8List.fromList(bytes)).parse();
      expect(model.root.dimensions, hasLength(2));
      expectPoint(model.root.dimensions[0].a, kC);
      expectPoint(model.root.dimensions[0].b, kB);
      expectPoint(model.root.dimensions[1].a, kC);
      expectTail(model);
    });

    test('plane axes preserve signed offset with reversed endpoints', () {
      for (final axis in [1, 2, 3]) {
        for (final reversed in [false, true]) {
          final builder = create();
          final p1 = reversed ? kB : kA;
          final p2 = reversed ? kA : kB;
          builder.addDimension(p1, p2, offset: -2.0);
          final bytes = builder.toBytes().toList();
          final at = locate(bytes, connectionBytes(p2)) + 32 + 2 + 10;
          final frame = concatBytes([f64s([0, -1, 0, 1, 0, 0]), u32(axis)]);
          bytes.replaceRange(at, at + frame.length, frame);
          final model = SkpFile.fromBuffer(Uint8List.fromList(bytes)).parse();
          expect(model.root.dimensions, hasLength(1));
          final dim = model.root.dimensions[0];
          expectPoint(dim.normal, (0.0, -1.0, 0.0));
          expectPoint(dim.planeX, (1.0, 0.0, 0.0));
          if (axis == 1) {
            expectPoint(dim.offsetVector, (0.0, 0.0, -2.0));
          } else if (axis == 2) {
            expectPoint(dim.offsetVector, (2.0, 0.0, 0.0));
          } else {
            expect(dim.offsetVector, isNull);
          }
        }
      }
    });

    test('nested paths are consumed without publishing local coordinates', () {
      final bytes = fixtureBytes();
      final at = locate(bytes, connectionBytes(kA)) + 32 + 2 + 2;
      bytes[at] = 1;
      bytes.insertAll(at + 4, refBytes(1234, large: true));
      final model = SkpFile.fromBuffer(Uint8List.fromList(bytes)).parse();
      expect(model.root.dimensions, hasLength(2));
      expect(model.root.dimensions[0].a, isNull);
      expectPoint(model.root.dimensions[0].b, kB);
      expectPoint(model.root.dimensions[1].a, kC);
      expectTail(model);
    });

    test('vector offsets survive root and nested round trips', () {
      const samples = <(Point3, Point3, Point3, Point3)>[
        ((1, 2, 3), (11, 2, 3), (0, 0, 4), (0, 0, 4)),
        ((11, 2, 3), (1, 2, 3), (0, 0, -4), (0, 0, -4)),
        ((1, 2, 3), (1, 2, 13), (-4, 0, 0), (-4, 0, 0)),
        ((1, 2, 3), (4, 6, 8), (-4, 3, 0), (-4, 3, 0)),
        ((1, 2, 3), (11, 2, 3), (7, -4, 0), (0, -4, 0)),
        ((1, 2, 3), (1, 2, 13), (0, 0, 0), (0, 0, 0)),
        ((1, 2, 3), (11, 2, 3), (7, 0, 0), (0, 0, 0)),
        ((1, 2, 3), (4, 6, 8), (3, 4, 5), (0, 0, 0)),
        ((1, 2, 3), (11, 2, 3), (0, 0, 1e-12), (0, 0, 1e-12)),
      ];
      final builder = create();
      final defn = builder.addComponentDefinition('Dimensions', (def) {
        for (final s in samples) {
          def.addDimension(s.$1, s.$2, offsetVector: s.$3);
        }
      });
      builder.addInstance(defn);
      for (final s in samples) {
        builder.addDimension(s.$1, s.$2, offsetVector: s.$3);
      }

      final model = SkpFile.fromBuffer(builder.toBytes()).parse();
      expect(model.root.dimensions, hasLength(samples.length));
      expect(model.definitions, hasLength(1));
      final defDims = model.definitions.values.first.dimensions;
      expect(defDims, hasLength(samples.length));
      for (final dims in [model.root.dimensions, defDims]) {
        for (var i = 0; i < samples.length; i++) {
          expectPoint(dims[i].a, samples[i].$1);
          expectPoint(dims[i].b, samples[i].$2);
          expectPoint(dims[i].offsetVector, samples[i].$4);
        }
      }
    });

    test('invalid vector dimension does not corrupt following records', () {
      final builder = create();
      final defn = builder.addComponentDefinition('Dimensions', (def) {
        for (final bad in [double.infinity, double.nan]) {
          expect(
            () => def.addDimension(kA, kB, offsetVector: (bad, 0.0, 0.0)),
            throwsA(isA<SkpWriteError>()),
          );
          expect(
            () => builder.addDimension((bad, 0.0, 0.0), kB, offsetVector: (0.0, 0.0, 1.0)),
            throwsA(isA<SkpWriteError>()),
          );
        }
        expect(
          () => def.addDimension(kA, kA, offsetVector: (0.0, 0.0, 1.0)),
          throwsA(isA<SkpWriteError>()),
        );
        def.addDimension(kA, kB, offsetVector: (0.0, 0.0, 2.0));
      });
      builder.addInstance(defn);
      for (final bad in [double.infinity, double.nan]) {
        expect(
          () => builder.addDimension((bad, 0.0, 0.0), kB, offsetVector: (0.0, 0.0, 1.0)),
          throwsA(isA<SkpWriteError>()),
        );
      }
      builder.addDimension(kA, kB, offsetVector: (0.0, 0.0, 3.0));

      final model = SkpFile.fromBuffer(builder.toBytes()).parse();
      expect(model.root.dimensions, hasLength(1));
      expect(model.definitions, hasLength(1));
      final defDims = model.definitions.values.first.dimensions;
      expect(defDims, hasLength(1));
      expectPoint(model.root.dimensions[0].offsetVector, (0.0, 0.0, 3.0));
      expectPoint(defDims[0].offsetVector, (0.0, 0.0, 2.0));
    });
  });
}
