import 'dart:typed_data';

import 'package:openskp/openskp.dart';
import 'package:openskp/src/geometry.dart';
import 'package:openskp/src/pages_dimensions.dart';
import 'package:openskp/src/transforms.dart';
import 'package:test/test.dart';

Uint8List _tlv(int tag, List<int> payload) {
  final out = Uint8List(6 + payload.length);
  final view = ByteData.sublistView(out);
  view.setUint16(0, tag, Endian.little);
  view.setUint32(2, payload.length, Endian.little);
  out.setRange(6, 6 + payload.length, payload);
  return out;
}

Uint8List _u32(int v) {
  final out = Uint8List(4);
  ByteData.sublistView(out).setUint32(0, v, Endian.little);
  return out;
}

Uint8List _f64s(List<double> vals) {
  final out = Uint8List(vals.length * 8);
  final view = ByteData.sublistView(out);
  for (int i = 0; i < vals.length; i++) {
    view.setFloat64(i * 8, vals[i], Endian.little);
  }
  return out;
}

Uint8List _concat(List<List<int>> parts) {
  int total = 0;
  for (final p in parts) {
    total += p.length;
  }
  final out = Uint8List(total);
  int off = 0;
  for (final p in parts) {
    out.setRange(off, off + p.length, p);
    off += p.length;
  }
  return out;
}

void main() {
  group('annotation tags and visibility (#488)', () {
    test('composeInstanceWorld nests parent times local', () {
      final panel = GeometryBuilder();
      final cabinet = GeometryBuilder()
        ..instances.add(GeometryBuilderInstance()
          ..persistentId = 'AA'
          ..refIdx = 2
          ..matrix = [1, 0, 0, 0, 1, 0, 0, 0, 1, 1.0, 2.0, 3.0, 1.0]);
      final root = GeometryBuilder()
        ..instances.add(GeometryBuilderInstance()
          ..persistentId = 'BB'
          ..refIdx = 1
          ..matrix = [1, 0, 0, 0, 1, 0, 0, 0, 1, 10.0, 20.0, 30.0, 1.0]);
      final defs = <int, RawDefinition>{
        1: RawDefinition(builder: cabinet),
        2: RawDefinition(builder: panel),
      };
      final world = <String, List<double>>{};
      composeInstanceWorld(root, defs, world);
      expect(Transforms.transformPoint(world['BB']!, (0.0, 0.0, 0.0)),
          equals((10.0, 20.0, 30.0)));
      expect(Transforms.transformPoint(world['AA']!, (0.0, 0.0, 0.0)),
          equals((11.0, 22.0, 33.0)));
    });

    test('dimension connected point uses composed nested world', () {
      final vid = [0xAA, 0xBB, 0x01];
      final iid = [0xCC, 0xDD, 0x02];
      final idLenPrefixed = [iid.length, ...iid];
      final refTlv =
          _tlv(0x53FC, _concat([_tlv(0x53FD, vid), _tlv(0x53FE, idLenPrefixed)]));
      final connA = _tlv(0x5BCD,
          _tlv(0x5208, _concat([_tlv(0x5209, _u32(2)), _tlv(0x520B, refTlv)])));
      final connB = _tlv(
          0x5BCE,
          _tlv(
              0x5208,
              _concat(
                  [_tlv(0x5209, _u32(1)), _tlv(0x520A, _f64s([0, 0, 0]))])));
      final blob =
          _tlv(0x5BCC, _concat([connA, connB, _tlv(0x5BD2, _f64s([0]))]));

      final leaf = GeometryBuilder();
      final parentDef = GeometryBuilder()
        ..instances.add(GeometryBuilderInstance()
          ..persistentId = 'CCDD02'
          ..refIdx = 2
          ..matrix = [1, 0, 0, 0, 1, 0, 0, 0, 1, 1.0, 2.0, 3.0, 1.0]);
      final root = GeometryBuilder()
        ..instances.add(GeometryBuilderInstance()
          ..persistentId = 'PARENT'
          ..refIdx = 1
          ..matrix = [1, 0, 0, 0, 1, 0, 0, 0, 1, 10.0, 20.0, 30.0, 1.0]);
      final defs = <int, RawDefinition>{
        1: RawDefinition(builder: parentDef),
        2: RawDefinition(builder: leaf),
      };
      final world = <String, List<double>>{};
      composeInstanceWorld(root, defs, world);
      final dims =
          parseDimensions(blob, {'AABB01': (0.0, 0.0, 0.0)}, world);
      expect(dims, hasLength(1));
      expect(dims[0].a, equals((11.0, 22.0, 33.0)));
    });

    test('dimension connected point uses innermost instance in path', () {
      final vid = [0xAA, 0xBB, 0x01];
      final outerId = [0x11, 0x01];
      final innerId = [0x22, 0x02];
      final instPath = [outerId.length, ...outerId, innerId.length, ...innerId];
      final refTlv =
          _tlv(0x53FC, _concat([_tlv(0x53FD, vid), _tlv(0x53FE, instPath)]));
      final connA = _tlv(0x5BCD,
          _tlv(0x5208, _concat([_tlv(0x5209, _u32(2)), _tlv(0x520B, refTlv)])));
      final connB = _tlv(
          0x5BCE,
          _tlv(
              0x5208,
              _concat(
                  [_tlv(0x5209, _u32(1)), _tlv(0x520A, _f64s([0, 0, 0]))])));
      final blob =
          _tlv(0x5BCC, _concat([connA, connB, _tlv(0x5BD2, _f64s([0]))]));

      final leaf = GeometryBuilder();
      final parentDef = GeometryBuilder()
        ..instances.add(GeometryBuilderInstance()
          ..persistentId = '2202'
          ..refIdx = 2
          ..matrix = [1, 0, 0, 0, 1, 0, 0, 0, 1, 100.0, 0.0, 0.0, 1.0]);
      final root = GeometryBuilder()
        ..instances.add(GeometryBuilderInstance()
          ..persistentId = '1101'
          ..refIdx = 1
          ..matrix = [1, 0, 0, 0, 1, 0, 0, 0, 1, 10.0, 0.0, 0.0, 1.0]);
      final defs = <int, RawDefinition>{
        1: RawDefinition(builder: parentDef),
        2: RawDefinition(builder: leaf),
      };
      final world = <String, List<double>>{};
      composeInstanceWorld(root, defs, world);
      final dims =
          parseDimensions(blob, {'AABB01': (0.0, 0.0, 0.0)}, world);
      expect(dims, hasLength(1));
      expect(dims[0].a, equals((110.0, 0.0, 0.0)));
    });

    for (final name in const [
      'annotation_layers_legacy.skp',
      'annotation_layers_vff.skp'
    ]) {
      test('SDK annotation tags retain owner and visibility ($name)', () {
        final model = SkpFile.open('test/fixtures/$name').parse();

        void verify(Definition owner, bool nested) {
          expect(owner.dimensions, hasLength(2));
          for (final d in owner.dimensions) {
            expect(d.layer, equals('尺寸标注'));
          }
          expect(owner.dimensions.where((d) => d.hidden), hasLength(1));

          expect(owner.constructionPoints, hasLength(2));
          expect(owner.constructionLines, hasLength(2));

          expect(owner.texts, hasLength(nested ? 1 : 3));
          for (final t in owner.texts) {
            expect(t.layer, equals('引注文字'));
          }
          expect(
              owner.texts.where((t) => t.hidden), hasLength(nested ? 0 : 1));

          for (final txt in owner.texts) {
            expect(txt.displayLeader, equals(txt.text != 'Dot'));
            if (nested) {
              expect(txt.leaderType, equals(0));
              expect(txt.screenPosition, isNotNull);
              expect(txt.screenPosition!.$1, closeTo(0.23, 1e-9));
              expect(txt.screenPosition!.$2, closeTo(0.67, 1e-9));
            } else {
              expect(txt.point, isNotNull);
              expect(txt.labelPoint, isNotNull);
              expect(txt.labelPoint!.$1 - txt.point!.$1, closeTo(0.0, 1e-9));
              expect(txt.labelPoint!.$2 - txt.point!.$2, closeTo(0.0, 1e-9));
              expect(txt.labelPoint!.$3 - txt.point!.$3, closeTo(10.0, 1e-9));
            }
          }
        }

        verify(model.root, false);
        int annotated = 0;
        for (final defn in model.definitions.values) {
          if (defn.texts.isEmpty) continue;
          verify(defn, true);
          annotated++;
        }
        expect(annotated, equals(1));
        if (name == 'annotation_layers_vff.skp') {
          expect(model.dimensions, hasLength(4));
          for (final d in model.dimensions) {
            expect(d.layer, equals('尺寸标注'));
          }
        }
      });
    }

    test('drawing properties come from dimension not its connections', () {
      Uint8List freePoint(double x, double y, double z) => _tlv(
            0x5208,
            _concat([
              _tlv(0x5209, _u32(1)),
              _tlv(0x520A, _f64s([x, y, z])),
              _tlv(0x07D0, _tlv(0x07D2, [99])),
            ]),
          );

      final body = _concat([
        _tlv(0x59D8,
            _tlv(0x07D0, _concat([_tlv(0x07D2, [12]), _tlv(0x07D3, [7])]))),
        _tlv(0x5BCD, freePoint(0, 0, 0)),
        _tlv(0x5BCE, freePoint(10, 0, 0)),
        _tlv(0x5BD2, _f64s([3])),
      ]);
      final dims = parseDimensions(_tlv(0x5BCC, body), const {}, const {});
      expect(dims, hasLength(1));
      expect(dims[0].layer, equals('12'));
      expect(dims[0].hidden, isTrue);
    });

    test('annotation drawing properties round trip', () {
      final builder = create();
      final layer = builder.addLayer('Annotations');
      final nested = builder.addComponentDefinition('TextOnly', (def) {
        def.addText(
          'Screen label',
          (0, 0, 0),
          leader: (0, 0, 0),
          layer: layer,
          hidden: true,
          screenPosition: (0.23, 0.67),
        );
      });
      builder.addInstance(nested);
      builder.addText(
        'Root label',
        (2, 3, 4),
        leader: (0, 0, 5),
        layer: layer,
        hidden: true,
      );
      builder.addText('Default label', (1, 1, 1), leader: (0, 0, 2));
      builder.addDimension(
        (0, 0, 0),
        (10, 0, 0),
        offsetVector: (0, 3, 0),
        layer: layer,
        hidden: true,
      );
      builder.addDimension((0, 5, 0), (10, 5, 0), offsetVector: (0, 3, 0));

      final model = SkpFile.fromBuffer(builder.toBytes()).parse();
      expect(model.root.dimensions, hasLength(2));
      expect(model.root.dimensions[0].layer, equals('Annotations'));
      expect(model.root.dimensions[0].hidden, isTrue);
      expect(model.root.dimensions[1].layer, isEmpty);
      expect(model.root.dimensions[1].hidden, isFalse);

      expect(model.root.texts, hasLength(2));
      expect(model.root.texts[0].layer, equals('Annotations'));
      expect(model.root.texts[0].hidden, isTrue);
      expect(model.root.texts[0].point, equals((2.0, 3.0, 4.0)));
      expect(model.root.texts[0].labelPoint, equals((2.0, 3.0, 9.0)));
      expect(model.root.texts[1].layer, isEmpty);
      expect(model.root.texts[1].hidden, isFalse);

      final defn =
          model.definitions.values.singleWhere((d) => d.name == 'TextOnly');
      expect(defn.texts, hasLength(1));
      expect(defn.texts[0].layer, equals('Annotations'));
      expect(defn.texts[0].hidden, isTrue);
      expect(defn.texts[0].leaderType, equals(0));
      expect(defn.texts[0].screenPosition, equals((0.23, 0.67)));
    });

    test('leaderless model text retains both points and visibility', () {
      final builder = create();
      final nested =
          builder.addComponentDefinition('Leader visibility', (def) {
        for (final lt in const [0, 1, 2]) {
          for (final disp in const [false, true]) {
            def.addText(
              'Model label',
              (5, 6, 7),
              leader: (2, 3, 4),
              leaderType: lt,
              displayLeader: disp,
            );
          }
        }
        def.addText(
          'Screen label',
          (0, 0, 0),
          leader: (0, 0, 0),
          screenPosition: (0.23, 0.67),
        );
      });
      builder.addInstance(nested);
      for (final lt in const [0, 1, 2]) {
        for (final disp in const [false, true]) {
          builder.addText(
            'Model label',
            (5, 6, 7),
            leader: (2, 3, 4),
            leaderType: lt,
            displayLeader: disp,
          );
        }
      }
      builder.addText(
        'Screen label',
        (0, 0, 0),
        leader: (0, 0, 0),
        screenPosition: (0.23, 0.67),
      );

      final model = SkpFile.fromBuffer(builder.toBytes()).parse();
      void verify(Definition owner) {
        expect(owner.texts, hasLength(7));
        for (int i = 0; i < 6; i++) {
          final txt = owner.texts[i];
          expect(txt.point, equals((5.0, 6.0, 7.0)));
          expect(txt.labelPoint, equals((7.0, 9.0, 11.0)));
          expect(txt.leaderType, isNot(equals(0)));
          expect(txt.displayLeader, equals(i == 3 || i == 5));
        }
        expect(owner.texts[6].leaderType, equals(0));
        expect(owner.texts[6].screenPosition, equals((0.23, 0.67)));
      }

      verify(model.root);
      expect(model.definitions, hasLength(1));
      verify(model.definitions.values.single);
    });

    test('invalid annotation options leave archive usable', () {
      final builder = create();
      expect(
        () => builder.addDimension((0, 0, 0), (10, 0, 0),
            offsetVector: (0, 3, 0), layer: 999),
        throwsA(isA<SkpWriteError>()),
      );
      expect(
        () => builder.addText('Bad layer', (0, 0, 0),
            leader: (1, 0, 0), layer: 999),
        throwsA(isA<SkpWriteError>()),
      );
      expect(
        () => builder.addText('Bad position', (0, 0, 0),
            leader: (1, 0, 0), screenPosition: (-1.0, 0.5)),
        throwsA(isA<SkpWriteError>()),
      );
      builder.addText('Valid', (0, 0, 0), leader: (1, 0, 0));

      final model = SkpFile.fromBuffer(builder.toBytes()).parse();
      expect(model.root.dimensions, isEmpty);
      expect(model.root.texts, hasLength(1));
      expect(model.root.texts[0].text, equals('Valid'));
    });
  });
}
