import 'dart:convert';
import 'dart:typed_data';

import 'package:openskp/openskp.dart';
import 'package:openskp/src/tlv.dart';
import 'package:test/test.dart';

Uint8List _tlv(String tagHex, Uint8List payload) {
  final out = Uint8List(6 + payload.length);
  out[0] = int.parse(tagHex.substring(0, 2), radix: 16);
  out[1] = int.parse(tagHex.substring(2, 4), radix: 16);
  ByteData.sublistView(out, 2, 6).setUint32(0, payload.length, Endian.little);
  out.setRange(6, out.length, payload);
  return out;
}

Uint8List _concat(List<Uint8List> arrays) {
  final total = arrays.fold<int>(0, (sum, a) => sum + a.length);
  final result = Uint8List(total);
  var offset = 0;
  for (final a in arrays) {
    result.setRange(offset, offset + a.length, a);
    offset += a.length;
  }
  return result;
}

Uint8List _utf8Bytes(String s) => Uint8List.fromList(utf8.encode(s));

Uint8List _definition(int seed, String name) {
  final guid = Uint8List(16)..fillRange(0, 16, seed);
  return _tlv('7C15', _concat([_tlv('7D15', guid), _tlv('7E15', _utf8Bytes(name))]));
}

final Uint8List _layers =
    _tlv('993A', _tlv('8C3C', _tlv('8D3C', _utf8Bytes('Layer0'))));

Uint8List _model(List<Uint8List> defs) {
  return _concat([
    _layers,
    _tlv('F901', _tlv('7017', _tlv('7117', _concat(defs)))),
    _tlv('0802', Uint8List.fromList([1, 2, 3])),
  ]);
}

List<(int, int, TlvNode)> _collect(Uint8List data) =>
    Tlv.iterTopLevelLazy(data, 0, data.length).toList();

List<TlvNode> _definitionsFromFullParse(Uint8List data) {
  final found = <TlvNode>[];
  void walk(List<TlvNode> nodes) {
    for (final n in nodes) {
      if (n.tag == '7C15') {
        found.add(n);
      } else {
        walk(n.children);
      }
    }
  }

  walk(Tlv.parseRecursive(data, 0, data.length));
  return found;
}

void _expectNodesEqual(TlvNode a, TlvNode b) {
  expect(a.offset, equals(b.offset));
  expect(a.tag, equals(b.tag));
  expect(a.size, equals(b.size));
  expect(a.payload, equals(b.payload));
  expect(a.children.length, equals(b.children.length));
  for (var i = 0; i < a.children.length; i++) {
    _expectNodesEqual(a.children[i], b.children[i]);
  }
}

void main() {
  group('Tlv.iterTopLevelLazy - F901 > 7017 > 7117 definitions streamed one at a time', () {
    test('yields each 7C15 as its own record, in file order, between its siblings', () {
      final data = _model([
        _definition(1, 'Porta'),
        _definition(2, 'Gaveta'),
        _definition(3, 'Prateleira'),
      ]);
      final records = _collect(data);

      expect(records.map((r) => r.$3.tag).toList(),
          equals(['993A', '7C15', '7C15', '7C15', '0802']));
      expect(records.map((r) => r.$1).toList(), equals([0, 1, 2, 3, 4]));
      expect(records.every((r) => r.$2 == 5), isTrue);
    });

    test('never yields one of the pass-through wrappers', () {
      final records = _collect(_model([_definition(1, 'A'), _definition(2, 'B')]));
      final tags = <String>{};
      void walk(TlvNode n) {
        tags.add(n.tag);
        for (final c in n.children) {
          walk(c);
        }
      }

      for (final r in records) {
        walk(r.$3);
      }

      expect(tags.contains('F901'), isFalse);
      expect(tags.contains('7017'), isFalse);
      expect(tags.contains('7117'), isFalse);
    });

    test('each streamed definition matches the one a full parse builds', () {
      final data = _model([
        _definition(1, 'Porta'),
        _definition(2, 'Gaveta'),
        _definition(3, 'Prateleira'),
      ]);
      final streamed =
          _collect(data).map((r) => r.$3).where((n) => n.tag == '7C15').toList();
      final full = _definitionsFromFullParse(data);

      expect(streamed.length, equals(full.length));
      for (var i = 0; i < full.length; i++) {
        _expectNodesEqual(streamed[i], full[i]);
      }
    });

    test('leaves every other container whole', () {
      final first = _collect(_model([_definition(1, 'A')])).first;
      expect(first.$3.tag, equals('993A'));
      expect(first.$3.children.map((c) => c.tag).toList(), equals(['8C3C']));
      expect(first.$3.children[0].children.map((c) => c.tag).toList(), equals(['8D3C']));
    });

    test('still unwraps a lone F401 before streaming definitions', () {
      final inner = _model([_definition(1, 'A'), _definition(2, 'B')]);
      final records = _collect(_tlv('F401', inner));

      expect(records.map((r) => r.$3.tag).toList(),
          equals(['993A', '7C15', '7C15', '0802']));
    });

    test('an empty definitions chain yields nothing for it', () {
      final records = _collect(_model([]));
      expect(records.map((r) => r.$3.tag).toList(), equals(['993A', '0802']));
    });

    test('rejects deeply nested pass-through wrappers', () {
      var payload = Uint8List(0);
      for (var i = 0; i < 100; i++) {
        payload = _tlv('F901', payload);
      }
      expect(
        () => _collect(_tlv('F401', payload)),
        throwsA(isA<SkpParseException>().having((e) => e.stage, 'stage', 'tlv_walk')),
      );
    });
  });
}
