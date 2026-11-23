import 'dart:typed_data';

import 'package:openskp/openskp.dart';
import 'package:test/test.dart';

const int _maxLegacyDepth = 64;

void _addU16(BytesBuilder b, int x) {
  b.addByte(x & 0xFF);
  b.addByte((x >> 8) & 0xFF);
}

void _addU32(BytesBuilder b, int x) {
  b.addByte(x & 0xFF);
  b.addByte((x >> 8) & 0xFF);
  b.addByte((x >> 16) & 0xFF);
  b.addByte((x >> 24) & 0xFF);
}

void _addWStr(BytesBuilder b, String s) {
  b.add(const [0xFF, 0xFE, 0xFF]);
  b.addByte(s.length);
  for (final c in s.codeUnits) {
    _addU16(b, c);
  }
}

void _addClassDecl(BytesBuilder b, String name, [int schema = 0]) {
  _addU16(b, 0xFFFF);
  _addU16(b, schema);
  _addU16(b, name.length);
  b.add(name.codeUnits);
}

int _findAscii(Uint8List data, String needle) {
  final units = needle.codeUnits;
  outer:
  for (var i = 0; i + units.length <= data.length; i++) {
    for (var j = 0; j < units.length; j++) {
      if (data[i + j] != units[j]) continue outer;
    }
    return i;
  }
  throw StateError('Needle $needle not found');
}

Uint8List _splice(Uint8List raw, int pos, int replaceLen, Uint8List mid) {
  final out = BytesBuilder(copy: false)
    ..add(Uint8List.sublistView(raw, 0, pos))
    ..add(mid)
    ..add(Uint8List.sublistView(raw, pos + replaceLen));
  return out.toBytes();
}

Uint8List _makeNestedObjectSkp(int totalDepth, {required bool onLayer}) {
  final builder = create()..addConstructionPoint((1.0, 2.0, 3.0));
  final raw = builder.toBytes();
  final tag = onLayer ? 'CLayer' : 'CConstructionPoint';
  final attrClsSlot = onLayer ? 11 : 13;
  final pos = _findAscii(raw, tag) + tag.length;
  if (totalDepth <= 1) return raw;
  final k = totalDepth - 1;
  final chain = BytesBuilder(copy: false);
  _addClassDecl(chain, 'CAttributeContainer');
  for (var i = 0; i < k - 1; i++) {
    _addU16(chain, 0x8000 | attrClsSlot);
  }
  _addU16(chain, 0);
  for (var i = 0; i < k; i++) {
    chain.addByte(0);
    _addU16(chain, 0);
  }
  return _splice(raw, pos, 2, chain.toBytes());
}

Uint8List _makeNestedAttrArraySkp(int arrayDepth) {
  final builder = create()..addConstructionPoint((1.0, 2.0, 3.0));
  final raw = builder.toBytes();
  final pos = _findAscii(raw, 'CConstructionPoint') + 'CConstructionPoint'.length;
  final payload = BytesBuilder(copy: false);
  _addClassDecl(payload, 'CAttributeContainer');
  _addU16(payload, 0);
  payload.addByte(0);
  _addClassDecl(payload, 'CAttributeNamed');
  _addU16(payload, 0);
  payload.addByte(0);
  _addU32(payload, 0);
  _addWStr(payload, 'Dict');
  _addWStr(payload, 'k');
  for (var i = 0; i < arrayDepth; i++) {
    payload.addByte(0x0B);
    _addU32(payload, 1);
  }
  payload.addByte(0x04);
  _addU32(payload, 7);
  _addWStr(payload, '');
  _addU32(payload, 0);
  _addU16(payload, 0);
  return _splice(raw, pos, 2, payload.toBytes());
}

void main() {
  group('Classic MFC archive recursion limits (#557)', () {
    for (final onLayer in [false, true]) {
      for (final depth in [1, 10, _maxLegacyDepth]) {
        test('parses $depth nested archive objects (onLayer=$onLayer)', () {
          final model = SkpFile.fromBuffer(
            _makeNestedObjectSkp(depth, onLayer: onLayer),
          ).parse();
          expect(model.unreadRootEntities, 0);
          expect(model.root.constructionPoints, hasLength(1));
        });
      }

      for (final depth in [_maxLegacyDepth + 1, 5000]) {
        test('rejects $depth nested archive objects (onLayer=$onLayer)', () {
          expect(
            () => SkpFile.fromBuffer(
              _makeNestedObjectSkp(depth, onLayer: onLayer),
            ).parse(),
            throwsA(
              isA<SkpParseException>()
                  .having((e) => e.stage, 'stage', 'legacy_walk')
                  .having((e) => e.message.toLowerCase(), 'message', contains('depth')),
            ),
          );
        });
      }
    }

    for (final depth in [1, 10, _maxLegacyDepth]) {
      test('parses $depth nested CAttributeNamed arrays', () {
        final model = SkpFile.fromBuffer(_makeNestedAttrArraySkp(depth)).parse();
        expect(model.unreadRootEntities, 0);
        expect(model.root.constructionPoints, hasLength(1));
      });
    }

    for (final depth in [_maxLegacyDepth + 1, 5000]) {
      test('rejects $depth nested CAttributeNamed arrays', () {
        expect(
          () => SkpFile.fromBuffer(_makeNestedAttrArraySkp(depth)).parse(),
          throwsA(
            isA<SkpParseException>()
                .having((e) => e.stage, 'stage', 'legacy_walk')
                .having((e) => e.message.toLowerCase(), 'message', contains('depth')),
          ),
        );
      });
    }
  });
}
