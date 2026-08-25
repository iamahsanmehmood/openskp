import 'dart:typed_data';

import 'package:openskp/src/legacy.dart';
import 'package:test/test.dart';

/// The v20 filler probe, exercised on synthetic records.
///
/// Ported from the TypeScript fix (openskp#192, following the review on
/// openskp#155): the original implementation walked forward to the first
/// non-zero byte and treated it as the count's low byte. That cannot
/// represent a count which is an exact multiple of 256 - its low byte IS
/// 0x00, so the scan walks straight into the count and misaligns every read
/// after it. Probing whole u32s at 4-byte strides fixes that, since no
/// individual byte is ever inspected.
///
/// Layout (see findCountAfterV20Filler):
///   <ff fe ff> <u8 0>   empty UTF-16 string
///   <zero padding>      length varies per call site, always pad % 4 == 1
///   <u32 count>

/// Builds a filler record followed by [count], then a class-record header.
Uint8List filler(int count, int pad) {
  final bytes = <int>[0xFF, 0xFE, 0xFF, 0x00];
  for (int i = 0; i < pad; i++) bytes.add(0x00);
  bytes.add(count & 0xFF);
  bytes.add((count >> 8) & 0xFF);
  bytes.add((count >> 16) & 0xFF);
  bytes.add((count >> 24) & 0xFF);
  bytes.addAll([0xFF, 0xFF, 0x0B, 0x00]); // whatever record comes next
  return Uint8List.fromList(bytes);
}

void main() {
  group('findCountAfterV20Filler', () {
    test('reads the counts and paddings seen in real v20 files', () {
      // both padding lengths observed in gondola_v20.skp and a second v20 model
      expect(findCountAfterV20Filler(filler(20, 9), 0, 1000000),
          equals((count: 20, next: 17)));
      expect(findCountAfterV20Filler(filler(5425, 13), 0, 5000000),
          equals((count: 5425, next: 21)));
    });

    test('reads a count that is an exact multiple of 256', () {
      // the regression this test exists for: a 0x00 low byte is
      // indistinguishable from padding to a byte-at-a-time scan
      for (final count in [256, 512, 1024, 65536, 16777216 ~/ 16]) {
        for (final pad in [9, 13]) {
          final hit = findCountAfterV20Filler(filler(count, pad), 0, 5000000);
          expect(hit, isNotNull, reason: 'count=$count pad=$pad');
          expect(hit!.count, equals(count), reason: 'count=$count pad=$pad');
        }
      }
    });

    test('reports where to resume reading', () {
      final hit = findCountAfterV20Filler(filler(256, 13), 0, 5000000)!;
      // 4 (marker+len) + 13 (padding) + 4 (the count itself)
      expect(hit.next, equals(21));
    });

    test('ignores a non-empty string record', () {
      // a real string here is genuine data; moving the cursor past it
      // would corrupt the parse
      final bytes = Uint8List.fromList(
          [0xFF, 0xFE, 0xFF, 0x05, 0, 0, 0, 0, 0, 0, 0, 0, 20, 0, 0, 0]);
      expect(findCountAfterV20Filler(bytes, 0, 1000000), isNull);
    });

    test('returns null when there is no marker ahead', () {
      final bytes = Uint8List(32); // all zeros, no ff fe ff
      expect(findCountAfterV20Filler(bytes, 0, 1000000), isNull);
    });

    test("respects the caller's plausibility limit", () {
      // nrel's limit is 100_000: a value above it is not the count we want
      expect(findCountAfterV20Filler(filler(200000, 13), 0, 100000), isNull);
      expect(findCountAfterV20Filler(filler(200000, 13), 0, 5000000)?.count,
          equals(200000));
    });

    test('does not run past the end of the buffer', () {
      final bytes = Uint8List.fromList([0xFF, 0xFE, 0xFF, 0x00, 0, 0]);
      expect(findCountAfterV20Filler(bytes, 0, 1000000), isNull);
    });
  });
}
