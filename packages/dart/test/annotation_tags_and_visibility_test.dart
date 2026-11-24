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
        () => builder.addDimension((0, 0, 0), (0, 0, 0),
            offsetVector: (0, 3, 0)),
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
      expect(
        () => builder.addText(String.fromCharCode(0xD800), (0, 0, 0),
            leader: (1, 0, 0)),
        throwsA(isA<SkpWriteError>()),
      );
      expect(
        () => builder.addText('NaN point', (double.nan, 0, 0),
            leader: (1, 0, 0)),
        throwsA(isA<SkpWriteError>()),
      );
      expect(
        () => builder.addText('Inf leader', (0, 0, 0),
            leader: (double.infinity, 0, 0)),
        throwsA(isA<SkpWriteError>()),
      );

      final afterLayer = builder.addLayer('AfterReject');
      final nested = builder.addComponentDefinition('DefAfterReject', (def) {
        expect(
          () => def.addText(String.fromCharCode(0xD800), (0, 0, 0),
              leader: (1, 0, 0)),
          throwsA(isA<SkpWriteError>()),
        );
        def.addText('Nested valid', (0, 0, 0),
            leader: (1, 0, 0), layer: afterLayer);
      });
      builder.addInstance(nested);
      builder.addText('Valid', (0, 0, 0),
          leader: (1, 0, 0), layer: afterLayer);

      final model = SkpFile.fromBuffer(builder.toBytes()).parse();
      expect(model.root.dimensions, isEmpty);
      expect(model.root.texts, hasLength(1));
      expect(model.root.texts[0].text, equals('Valid'));
      expect(model.root.texts[0].layer, equals('AfterReject'));
      final defn = model.definitions.values.single;
      expect(defn.texts, hasLength(1));
      expect(defn.texts[0].text, equals('Nested valid'));
    });

    test('vff doubling chain composes only referenced path and caps visits', () {
      final chainModel =
          SkpFile.open('test/fixtures/vff_doubling_chain.skp').parse();
      expect(chainModel.definitions, hasLength(22));
      expect(chainModel.root.instances, hasLength(2));

      const levels = 22;
      final defs = <int, RawDefinition>{
        0: RawDefinition(guid: '', name: '', builder: GeometryBuilder()),
      };
      for (int i = 1; i < levels; i++) {
        final b = GeometryBuilder();
        for (int j = 0; j < 2; j++) {
          final tx = j == 0 ? 0.0 : 10.0;
          final pid =
              '${i.toRadixString(16).padLeft(2, '0')}${(j + 1).toRadixString(16).padLeft(2, '0')}'
                  .toUpperCase();
          b.instances.add(GeometryBuilderInstance()
            ..persistentId = pid
            ..refIdx = i - 1
            ..matrix = [1, 0, 0, 0, 1, 0, 0, 0, 1, tx, 0, 0, 1]);
        }
        defs[i] = RawDefinition(guid: '', name: '', builder: b);
      }

      GeometryBuilder makeRoot() {
        final rb = GeometryBuilder();
        for (int j = 0; j < 2; j++) {
          final tx = j == 0 ? 0.0 : 10.0;
          final pid =
              '${levels.toRadixString(16).padLeft(2, '0')}${(j + 1).toRadixString(16).padLeft(2, '0')}'
                  .toUpperCase();
          rb.instances.add(GeometryBuilderInstance()
            ..persistentId = pid
            ..refIdx = levels - 1
            ..matrix = [1, 0, 0, 0, 1, 0, 0, 0, 1, tx, 0, 0, 1]);
        }
        return rb;
      }

      final pathBytes = <int>[];
      for (int i = levels; i >= 1; i--) {
        pathBytes.addAll([2, i, 2]);
      }

      final vidZero = [0xAA, 0xBB, 0x00];
      final vid = [0xAA, 0xBB, 0x01];
      final vidB = [0xAA, 0xBB, 0x02];
      final id2pos = <String, (double, double, double)>{
        'AABB00': (0.0, 0.0, 0.0),
        'AABB01': (4.0, 0.0, 0.0),
        'AABB02': (5.0, 0.0, 0.0),
      };

      Uint8List dimBlob(List<int> raw53fe) {
        final refTlv =
            _tlv(0x53FC, _concat([_tlv(0x53FD, vid), _tlv(0x53FE, raw53fe)]));
        final connA = _tlv(
            0x5BCD,
            _tlv(0x5208,
                _concat([_tlv(0x5209, _u32(2)), _tlv(0x520B, refTlv)])));
        final connB = _tlv(
            0x5BCE,
            _tlv(0x5208,
                _concat([_tlv(0x5209, _u32(1)), _tlv(0x520A, _f64s([0, 0, 0]))])));
        return _tlv(
            0x5BCC, _concat([connA, connB, _tlv(0x5BD2, _f64s([0.0]))]));
      }

      final rb0 = makeRoot();
      expect(
          assignVffAnnotations(dimBlob(const []), id2pos, rb0, defs,
              maxVisits: 5),
          equals(0));
      expect(rb0.dimensions[0].a, equals((4.0, 0.0, 0.0)));

      final rbOk = makeRoot();
      final worldOk = <RawDimension>[];
      expect(
          assignVffAnnotations(dimBlob(pathBytes), id2pos, rbOk, defs,
              worldDimensions: worldOk, maxVisits: 64),
          equals(levels));
      expect(rbOk.dimensions[0].a, equals((224.0, 0.0, 0.0)));
      expect(worldOk[0].a, equals((224.0, 0.0, 0.0)));

      final rbCap = makeRoot();
      expect(
          assignVffAnnotations(dimBlob(pathBytes), id2pos, rbCap, defs,
              maxVisits: 10),
          equals(10));
      expect(rbCap.dimensions[0].a, equals((4.0, 0.0, 0.0)));

      final rbMiss = makeRoot();
      expect(
          assignVffAnnotations(
              dimBlob(const [4, 0xDE, 0xAD, 0xBE, 0xEF]), id2pos, rbMiss, defs,
              maxVisits: 25),
          equals(0));

      (GeometryBuilder, Map<int, RawDefinition>) makeAfterChainGraph() {
        final gDefs = <int, RawDefinition>{};
        for (final entry in defs.entries) {
          final nb = GeometryBuilder()
            ..instances.addAll(entry.value.builder.instances);
          gDefs[entry.key] = RawDefinition(guid: '', name: '', builder: nb);
        }
        gDefs[101] =
            RawDefinition(guid: '', name: '', builder: GeometryBuilder());
        final afterB = GeometryBuilder()
          ..instances.add(GeometryBuilderInstance()
            ..persistentId = '6501'
            ..refIdx = 101
            ..matrix = [1, 0, 0, 0, 1, 0, 0, 0, 1, 3.0, 0, 0, 1]);
        gDefs[100] = RawDefinition(guid: '', name: '', builder: afterB);
        final rb = makeRoot()
          ..instances.add(GeometryBuilderInstance()
            ..persistentId = '6401'
            ..refIdx = 100
            ..matrix = [1, 0, 0, 0, 1, 0, 0, 0, 1, 5.0, 0, 0, 1]);
        return (rb, gDefs);
      }

      const subPath = [2, 0x65, 0x01];
      final refA =
          _tlv(0x53FC, _concat([_tlv(0x53FD, vidZero), _tlv(0x53FE, subPath)]));
      final refB =
          _tlv(0x53FC, _concat([_tlv(0x53FD, vidB), _tlv(0x53FE, subPath)]));
      final afterDim = _tlv(
          0x5BCC,
          _concat([
            _tlv(0x5BCD,
                _tlv(0x5208, _concat([_tlv(0x5209, _u32(2)), _tlv(0x520B, refA)]))),
            _tlv(0x5BCE,
                _tlv(0x5208, _concat([_tlv(0x5209, _u32(2)), _tlv(0x520B, refB)]))),
            _tlv(0x5BD2, _f64s([0.0])),
          ]));
      final def100Span = _tlv(
          0x01F9,
          _tlv(
              0x1770,
              _tlv(
                  0x1771,
                  _tlv(0x157C,
                      _concat([_tlv(0x05DC, _tlv(0x05DE, [100])), afterDim])))));

      final (rbAfter, defsAfter) = makeAfterChainGraph();
      final worldAfter = <RawDimension>[];
      final visitsAfter = assignVffAnnotations(
          def100Span, id2pos, rbAfter, defsAfter,
          worldDimensions: worldAfter, maxVisits: 16);
      expect(visitsAfter, lessThanOrEqualTo(10));
      expect(defsAfter[100]!.builder.dimensions[0].a, equals((3.0, 0.0, 0.0)));
      expect(defsAfter[100]!.builder.dimensions[0].b, equals((8.0, 0.0, 0.0)));
      expect(worldAfter[0].a, equals((8.0, 0.0, 0.0)));
      expect(worldAfter[0].b, equals((13.0, 0.0, 0.0)));

      final danglingMid = const [2, 10, 2, 4, 0xDE, 0xAD, 0xBE, 0xEF];
      final repeated = _concat([for (var r = 0; r < 20; r++) dimBlob(danglingMid)]);
      final rbRepeat = makeRoot();
      expect(
          assignVffAnnotations(repeated, id2pos, rbRepeat, defs, maxVisits: 16),
          lessThanOrEqualTo(5));

      final sixtyDangling = _concat([
        for (var k = 0; k < 60; k++) dimBlob([2, 10, 2, 2, 0xEE, k]),
        def100Span,
      ]);
      final (rbStarve, defsStarve) = makeAfterChainGraph();
      final worldStarve = <RawDimension>[];
      assignVffAnnotations(sixtyDangling, id2pos, rbStarve, defsStarve,
          worldDimensions: worldStarve, maxVisits: 16);
      expect(worldStarve.last.a, equals((8.0, 0.0, 0.0)));
      expect(worldStarve.last.b, equals((13.0, 0.0, 0.0)));
    });
  });
}
