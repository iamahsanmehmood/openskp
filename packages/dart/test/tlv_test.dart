import 'dart:typed_data';

import 'package:openskp/src/tlv.dart';
import 'package:test/test.dart';

/// Regression coverage for a real bug: `Tlv.parseRecursive`'s and the
/// private `_flatHeaders`' (exercised here via the public `iterTopLevelLazy`)
/// "is there room for one more 6-byte header" loop guard used
/// `pos < end - 6` instead of the correct `pos <= end - 6` (equivalently
/// `pos + 6 <= end`) - a header occupying exactly the last 6 bytes of
/// `[start, end)` is a real, valid record, not corrupt data, but the
/// off-by-one silently dropped it with no error. `_flatHeaders` backs
/// `iterTopLevelLazy`, the lazy scanner real 100k+-definition files depend
/// on, so this could silently drop a trailing top-level definition, not
/// just a nested child.

Uint8List tlv(String tagHex, List<int> payload) {
  final tag = [
    int.parse(tagHex.substring(0, 2), radix: 16),
    int.parse(tagHex.substring(2, 4), radix: 16),
  ];
  final size = ByteData(4)..setUint32(0, payload.length, Endian.little);
  return Uint8List.fromList([...tag, ...size.buffer.asUint8List(), ...payload]);
}

void main() {
  group('Tlv boundary', () {
    test('parseRecursive keeps a header that exactly fills the range', () {
      final data = tlv('0300', []);
      expect(data.length, 6);
      final nodes = Tlv.parseRecursive(data, 0, data.length);
      expect(nodes.length, 1);
      expect(nodes[0].tag, '0300');
    });

    test('iterTopLevelLazy keeps a top-level record that exactly fills the range', () {
      final data = tlv('0300', []);
      expect(data.length, 6);
      final items = Tlv.iterTopLevelLazy(data, 0, data.length).toList();
      expect(items.length, 1);
      final (index, total, node) = items[0];
      expect(index, 0);
      expect(total, 1);
      expect(node.tag, '0300');
    });
  });
}
