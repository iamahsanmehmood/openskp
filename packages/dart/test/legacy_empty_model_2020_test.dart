import 'dart:io';
import 'dart:typed_data';

import 'package:openskp/openskp.dart';
import 'package:openskp/src/legacy.dart';
import 'package:test/test.dart';

/// A SketchUp 2020 model with nothing in it (no entities, no component
/// definitions) opens.
///
/// Fixture legacy_empty_2020.skp: an empty model saved as a SketchUp 2020 file
/// (SUModelVersion 13) by SketchUp's own library, the blank-template case.
/// SketchUp 2020 writes an empty string record and zero padding where the
/// definition count would be; with nothing in the model the only count behind
/// it is the root entity count, which is 0, and the filler search skipped it,
/// so the whole file was rejected with "implausible definition count". No
/// private content.
Uint8List _filler(int zeros, [List<int> tail = const [0x58, 0xE7, 0x37, 0x6A]]) =>
    Uint8List.fromList([0, 0, 0, 0xFF, 0xFE, 0xFF, 0, ...List.filled(zeros, 0), ...tail]);

void main() {
  test('an empty 2020 model parses to an empty model', () {
    final model = SkpFile.open(
            '${Directory.current.path}/test/fixtures/legacy_empty_2020.skp')
        .parse();
    expect(model.layers.map((l) => l.name).toList(), ['Layer0']);
    expect(model.definitions, isEmpty);
    expect(model.materials, isEmpty);
    expect(model.root.faces, isEmpty);
    expect(model.root.instances, isEmpty);
  });

  group('findEmptyRootAfterV20Filler', () {
    test('returns the root count offset, 13 bytes of padding behind the '
        'string length byte', () {
      expect(findEmptyRootAfterV20Filler(_filler(26), 0), 3 + 4 + 13);
    });

    test('treats a non-empty string as real data', () {
      final data = _filler(30);
      data[6] = 5;
      expect(findEmptyRootAfterV20Filler(data, 0), isNull);
    });

    test('rejects a non-zero byte in the padding or the count', () {
      for (var at = 4; at < 4 + 13 + 4; at++) {
        final data = _filler(26);
        data[3 + at] = 1;
        expect(findEmptyRootAfterV20Filler(data, 0), isNull, reason: 'byte $at');
      }
    });

    test('rejects data with no filler nearby and a truncated filler', () {
      expect(findEmptyRootAfterV20Filler(Uint8List(64), 0), isNull);
      expect(findEmptyRootAfterV20Filler(_filler(5, const []), 0), isNull);
    });
  });
}
