import 'dart:convert';
import 'dart:typed_data';

import 'package:openskp/openskp.dart';
import 'package:openskp/src/geometry.dart';
import 'package:openskp/src/tlv.dart';
import 'package:test/test.dart';

const List<Point3> _squarePts = [
  (0.0, 0.0, 0.0),
  (10.0, 0.0, 0.0),
  (10.0, 10.0, 0.0),
  (0.0, 10.0, 0.0),
];

Uint8List _tlv(String tagHex, List<int> payload) {
  final out = Uint8List(6 + payload.length);
  out[0] = int.parse(tagHex.substring(0, 2), radix: 16);
  out[1] = int.parse(tagHex.substring(2, 4), radix: 16);
  ByteData.sublistView(out).setUint32(2, payload.length, Endian.little);
  out.setRange(6, out.length, payload);
  return out;
}

Uint8List _concat(List<List<int>> parts) {
  final total = parts.fold<int>(0, (acc, p) => acc + p.length);
  final out = Uint8List(total);
  var pos = 0;
  for (final p in parts) {
    out.setRange(pos, pos + p.length, p);
    pos += p.length;
  }
  return out;
}

Uint8List _i32Bytes(int v) {
  final out = Uint8List(4);
  ByteData.sublistView(out).setInt32(0, v, Endian.little);
  return out;
}

Uint8List _f64Bytes(double v) {
  final out = Uint8List(8);
  ByteData.sublistView(out).setFloat64(0, v, Endian.little);
  return out;
}

Uint8List _entryValue(List<int> inner) => _tlv('A438', inner);

Uint8List _entry(String key, List<int> inner) =>
    _concat([_tlv('B636', utf8.encode(key)), _entryValue(inner)]);

Uint8List _namedDict(String name, List<int> entries) =>
    _concat([_tlv('B436', utf8.encode(name)), _tlv('B536', entries)]);

bool _containsSubarray(Uint8List haystack, List<int> needle) {
  for (var i = 0; i <= haystack.length - needle.length; i++) {
    var ok = true;
    for (var j = 0; j < needle.length; j++) {
      if (haystack[i + j] != needle[j]) {
        ok = false;
        break;
      }
    }
    if (ok) return true;
  }
  return false;
}

void main() {
  group('component identities and typed definition attributes (#478)', () {
    test('component GUID matches across classic and VFF containers', () {
      for (final filename in ['nesting_2017.skp', 'nesting_2020.skp', 'nesting_2021.skp']) {
        final model = SkpFile.open('test/fixtures/$filename').parse();
        final shelves = model.definitions.values.where((d) => d.name == 'Shelf').toList();
        expect(shelves, hasLength(1));
        expect(shelves.first.guid, equals('1614E9C58C0B40338881DDE62E6E2D80'));
        var references = 0;
        for (final defn in [model.root, ...model.definitions.values]) {
          for (final inst in defn.instances) {
            if (inst.refIdx == null) continue;
            expect(model.definitions.containsKey(inst.refIdx), isTrue);
            references++;
          }
        }
        expect(references, greaterThan(0));
        if (filename == 'nesting_2021.skp') {
          expect(model.root.instances, hasLength(1));
          expect(model.root.instances.first.guid, equals('0BFF75653E97458E9B7238A6E9CF1A99'));
        }
      }
    });

    test('VFF definition and instance GUIDs use canonical UUID order', () {
      final native = Uint8List.fromList([
        0xC5, 0xE9, 0x14, 0x16, 0x0B, 0x8C, 0x33, 0x40,
        0x88, 0x81, 0xDD, 0xE6, 0x2E, 0x6E, 0x2D, 0x80,
      ]);
      const canonical = '1614E9C58C0B40338881DDE62E6E2D80';
      final defBytes = _tlv(
        '7C15',
        _concat([
          _tlv('DE05', [1]),
          _tlv('7D15', native),
          _tlv('7E15', utf8.encode('Shelf')),
        ]),
      );
      final defsDict = <int, RawDefinition>{};
      Geometry.collectDefs(
        Tlv.parseRecursive(defBytes, 0, defBytes.length, Tlv.containerTags),
        defsDict,
      );
      expect(defsDict[1]?.guid, equals(canonical));

      final instBytes = _tlv('6419', _tlv('6819', native));
      final builder = GeometryBuilder();
      Geometry.extractGeometryFromNodes(
        Tlv.parseRecursive(instBytes, 0, instBytes.length, Tlv.containerTags),
        builder,
      );
      expect(builder.instances, hasLength(1));
      expect(builder.instances.first.refGuid, equals(canonical));
      expect(builder.instances.first.refIdx, isNull);
    });

    test('definition dictionaries keep native types without child attributes', () {
      final values = _concat([
        _entry('count', _tlv('A738', _i32Bytes(-7))),
        _entry('flag', _tlv('AA38', [1])),
        _entry('disabled', _tlv('AA38', [0])),
        _entry('time', _tlv('AC38', [0x00, 0xF1, 0x53, 0x65])),
        _entry('late', _tlv('AC38', [0xFF, 0xFF, 0xFF, 0xFF])),
        _entry('short', _tlv('AC38', [1, 2, 3])),
        _entry('nothing', const []),
        _entry(
          'values',
          _tlv(
            'AE38',
            _concat([
              _entryValue(_tlv('A938', _f64Bytes(2.5))),
              _entryValue(_tlv('AD38', utf8.encode('abc'))),
            ]),
          ),
        ),
      ]);
      final own = _tlv('8813', _tlv('D007', _tlv('DC05', _namedDict('Plugin', values))));
      final child = _tlv(
        '6419',
        _concat([
          _tlv('6719', [2]),
          _tlv('D007', _tlv('DC05', _namedDict('Plugin', _entry('count', _tlv('A738', _i32Bytes(99)))))),
        ]),
      );
      final rawBytes = _concat([
        _tlv('7C15', _concat([_tlv('DE05', [1]), own, child])),
        _tlv('7C15', _concat([_tlv('DE05', [2]), child])),
      ]);
      final defsDict = <int, RawDefinition>{};
      Geometry.collectDefs(
        Tlv.parseRecursive(rawBytes, 0, rawBytes.length, Tlv.containerTags),
        defsDict,
      );
      expect(defsDict.length, equals(2));
      expect(
        defsDict[1]?.attributeDictionaries,
        equals({
          'Plugin': {
            'count': -7,
            'flag': true,
            'disabled': false,
            'time': 1700000000,
            'late': 4294967295,
            'short': null,
            'nothing': null,
            'values': [2.5, 'abc'],
          },
        }),
      );
      expect(Geometry.stringifyVffAttrValue(true), equals('1'));
      expect(Geometry.stringifyVffAttrValue(false), equals('0'));
      expect(defsDict[2]?.attributeDictionaries, isEmpty);
    });

    test('explicit model GUID uses native Windows byte order', () {
      final builder = create();
      builder.addFace(_squarePts);
      const native = [
        0x33, 0x22, 0x11, 0x00, 0x55, 0x44, 0x77, 0x46,
        0x88, 0x99, 0xAA, 0xBB, 0xCC, 0xDD, 0xEE, 0xFF,
      ];
      for (final guid in [
        '00112233-4455-4677-8899-aabbccddeeff',
        '00112233445546778899AABBCCDDEEFF',
      ]) {
        builder.setGuid(guid);
        final data = builder.toBytes();
        expect(data.length, greaterThanOrEqualTo(68));
        expect(data.sublist(52, 68), equals(native));
      }
      expect(() => builder.setGuid('not-a-guid'), throwsA(isA<SkpWriteError>()));
      expect(
        () => builder.setGuid('00112233445546778899AABBCCDDEEFZ'),
        throwsA(isA<SkpWriteError>()),
      );
      expect(
        () => builder.setGuid('00112233-4455-4677-8899a-abbccddeeff'),
        throwsA(isA<SkpWriteError>()),
      );
      expect(builder.toBytes().sublist(52, 68), equals(native));

      builder.setGuid('');
      final reset = builder.toBytes();
      expect(reset.sublist(52, 68), isNot(equals(native)));
      expect(reset.sublist(52, 68), equals(builder.toBytes().sublist(52, 68)));
      expect(reset[52 + 7] & 0xF0, equals(0x40));
      expect(reset[52 + 8] & 0xC0, equals(0x80));
    });

    test('definition GUID preserved in archive and public model', () {
      const native = [
        0x33, 0x22, 0x11, 0x00, 0x55, 0x44, 0x77, 0x46,
        0x88, 0x99, 0xAA, 0xBB, 0xCC, 0xDD, 0xEE, 0xFF,
      ];
      for (final guid in [
        '00112233-4455-4677-8899-aabbccddeeff',
        '00112233445546778899AABBCCDDEEFF',
      ]) {
        final builder = create();
        final def = builder.addComponentDefinition(
          'Identified',
          (d) => d.addFace(_squarePts),
          guid: guid,
        );
        builder.addInstance(def);
        final data = builder.toBytes();
        expect(_containsSubarray(data, native), isTrue);
        final model = SkpFile.fromBuffer(data).parse();
        expect(model.definitions, hasLength(1));
        expect(model.definitions.values.first.guid, equals('00112233445546778899AABBCCDDEEFF'));
        expect(model.root.instances, hasLength(1));
        final refIdx = model.root.instances.first.refIdx;
        expect(refIdx, isNotNull);
        expect(model.definitions[refIdx]!.guid, equals('00112233445546778899AABBCCDDEEFF'));
      }
    });

    test('invalid definition GUID does not partially add definition', () {
      final builder = create();
      expect(
        () => builder.addComponentDefinition(
          'Invalid',
          (d) => d.addFace(_squarePts),
          guid: 'not-a-guid',
        ),
        throwsA(isA<SkpWriteError>()),
      );
      final good = builder.addComponentDefinition('Valid', (d) => d.addFace(_squarePts));
      builder.addInstance(good);
      final model = SkpFile.fromBuffer(builder.toBytes()).parse();
      expect(model.definitions, hasLength(1));
      expect(model.definitions.values.first.name, equals('Valid'));
    });

    test('default definition GUIDs are distinct and stable', () {
      final builder = create();
      final a = builder.addComponentDefinition('Same name', (d) => d.addFace(_squarePts));
      final b = builder.addComponentDefinition('Same name', (d) => d.addFace(_squarePts));
      builder.addInstance(a);
      builder.addInstance(b);
      final first = SkpFile.fromBuffer(builder.toBytes()).parse();
      final second = SkpFile.fromBuffer(builder.toBytes()).parse();
      expect(first.definitions, hasLength(2));
      final defs = first.definitions.values.toList();
      expect(defs[0].guid, isNot(equals(defs[1].guid)));
      for (final entry in first.definitions.entries) {
        expect(entry.value.guid, equals(second.definitions[entry.key]?.guid));
      }
    });
  });
}
