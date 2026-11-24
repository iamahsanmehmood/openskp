import 'dart:typed_data';

import 'package:openskp/openskp.dart';
import 'package:openskp/src/tlv.dart';
import 'package:test/test.dart';

const int wrap = 0x100000000;
const int uint32Max = 0xFFFFFFFF;

class SparseRecords {
  final Map<int, int> lengths;
  final Set<int> containers;

  SparseRecords([Map<int, int>? lengths, Set<int>? containers])
      : lengths = lengths ?? <int, int>{},
        containers = containers ?? <int>{};

  List<TlvHeader> scan(int end) {
    return Tlv.tlvHeaders(
      0,
      end,
      (p) => lengths[p] ?? uint32Max,
      (p) => containers.contains(p),
    );
  }
}

List<TlvHeader>? referencePartition(SparseRecords d, int start, int end) {
  final ordinary = <TlvHeader>[];
  var p = start;
  while (end - p >= 6) {
    final n = d.lengths[p] ?? uint32Max;
    if (n > end - p - 6) break;
    ordinary.add(TlvHeader(p, n));
    p += 6 + n;
  }
  if (p == end) return ordinary;
  for (var i = 0; i < ordinary.length; i++) {
    final h = ordinary[i];
    if (!d.containers.contains(h.offset)) continue;
    final available = end - h.offset - 6;
    if (available - h.size < wrap) continue;
    var n = h.size + wrap;
    while (n <= available) {
      final next = h.offset + 6 + n;
      final suffix = referencePartition(d, next, end);
      if (suffix != null && referencePartition(d, h.offset + 6, next) != null) {
        return <TlvHeader>[
          ...ordinary.sublist(0, i),
          TlvHeader(h.offset, n),
          ...suffix,
        ];
      }
      if (available - n < wrap) break;
      n += wrap;
    }
  }
  return null;
}

void main() {
  group('WrappedTlv (#520, #535)', () {
    test('ModelCatalogDisambiguatesTwoExactPartitions', () {
      const entities = wrap + 48;
      const end = entities + 6;
      final d = SparseRecords(
        {
          0: 48,
          6: 36,
          12: 30,
          18: 24,
          24: uint32Max,
          wrap + 29: 13,
          entities: 0,
          54: uint32Max - 5,
        },
        {0, 6, 12, 18},
      );
      final ordinary = d.scan(end);
      expect(ordinary.length, 2);
      expect(ordinary.last.offset, 54);
      final tags = <int, int>{
        0: 0x01f4,
        6: 0x01f9,
        12: 0x1770,
        18: 0x1771,
        24: 0x157c,
        wrap + 29: 0x157c,
        entities: 0x01f6,
      };
      List<TlvHeader>? read() =>
          Tlv.wrappedModelRecords(end, (p) => tags[p]!, (p) => d.lengths[p]!);
      final records = read();
      expect(records, isNotNull);
      expect(records!.map((r) => r.offset).toList(), [24, wrap + 29, entities]);
      d.lengths[12] = d.lengths[12]! + 1;
      expect(read(), isNull);
      d.lengths[12] = d.lengths[12]! - 1;
      tags[entities] = 0x157e;
      expect(read(), isNull);
    });

    test('SmallCatalogInsideWrappedModel', () {
      const entities = wrap - 16;
      const end = entities + 70;
      final d = SparseRecords(
        {
          0: 48,
          6: (entities - 12) & uint32Max,
          12: (entities - 18) & uint32Max,
          18: (entities - 24) & uint32Max,
          24: (entities - 30) & uint32Max,
          entities: 64,
          54: uint32Max - 5,
        },
        {0, 6, 12, 18},
      );
      final tags = <int, int>{
        0: 0x01f4,
        6: 0x01f9,
        12: 0x1770,
        18: 0x1771,
        24: 0x157c,
        entities: 0x01f6,
      };
      final ordinary = d.scan(end);
      expect(ordinary.length, 2);
      expect(ordinary.last.offset, 54);
      final records =
          Tlv.wrappedModelRecords(end, (p) => tags[p]!, (p) => d.lengths[p]!);
      expect(records, isNotNull);
      expect(records!.map((r) => r.offset).toList(), [24, entities]);
    });

    test('PreservesCompleteShortModelFollowedByTopLevelRecords', () {
      const end = wrap + 54;
      final d = SparseRecords(
        {0: 48, 6: 42, 12: 36, 18: 30, 24: 24, 54: uint32Max - 5},
        {0, 6, 12, 18},
      );
      final tags = <int, int>{
        0: 0x01f4,
        6: 0x01f9,
        12: 0x1770,
        18: 0x1771,
        24: 0x157c,
        54: 0x01f6,
      };
      final ordinary = d.scan(end);
      expect(ordinary.length, 2);
      expect(ordinary.last.offset, 54);
      expect(
        Tlv.wrappedModelRecords(end, (p) => tags[p]!, (p) => d.lengths[p]!),
        isNull,
      );
    });

    test('ExtraTopLevelDefinitionsDoNotExtendACompleteCatalog', () {
      const entities = wrap + 48;
      const end = entities + 6;
      final d = SparseRecords(
        {
          0: 48,
          6: 36,
          12: 30,
          18: 24,
          24: 18,
          48: 0,
          54: uint32Max - 11,
          entities: 0,
        },
        {0, 6, 12, 18},
      );
      final tags = <int, int>{
        0: 0x01f4,
        6: 0x01f9,
        12: 0x1770,
        18: 0x1771,
        24: 0x157c,
        48: 0x157c,
        54: 0x157c,
        entities: 0x01f6,
      };
      final ordinary = d.scan(end);
      expect(ordinary.length, 3);
      expect(ordinary[1].offset, 54);
      expect(
        Tlv.wrappedModelRecords(end, (p) => tags[p]!, (p) => d.lengths[p]!),
        isNull,
      );
    });

    test('NestedContainersAndFollowingSibling', () {
      final d = SparseRecords();
      for (var p = 0; p < 24; p += 6) {
        d.lengths[p] = (wrap + 30 - p - 6) & uint32Max;
        d.containers.add(p);
      }
      d.lengths[24] = uint32Max;
      d.lengths[wrap + 29] = 1;
      for (var p = 0; p < 24; p += 6) {
        d.lengths[p] = d.lengths[p]! + 6;
      }
      d.lengths[wrap + 36] = 0;
      final records = d.scan(wrap + 42);
      expect(records.length, 2);
      expect(records[0].size, wrap + 30);
      expect(records[1].offset, wrap + 36);
    });

    test('MultipleWraps', () {
      final d = SparseRecords(
        {0: 10, 6: uint32Max, wrap + 11: uint32Max},
        {0},
      );
      final records = d.scan(2 * wrap + 16);
      expect(records, [const TlvHeader(0, 2 * wrap + 10)]);
    });

    test('RejectsInvalidChildrenAndSuffixAndOpaqueLeaves', () {
      final d = SparseRecords({0: 5, 6: uint32Max}, {0});
      var records = d.scan(wrap + 11);
      expect(records, [const TlvHeader(0, wrap + 5)]);
      d.lengths[6] = uint32Max - 1;
      records = d.scan(wrap + 11);
      expect(records.first.size, 5);
      d.lengths[6] = uint32Max;
      records = d.scan(wrap + 12);
      expect(records.first.size, 5);
      d.containers.clear();
      records = d.scan(wrap + 11);
      expect(records.first.size, 5);
    });

    test('KeepsAnExactLargePartitionAndNonzeroStart', () {
      final d = SparseRecords({0: uint32Max, wrap + 5: 0}, {0});
      final records = d.scan(wrap + 11);
      expect(records.length, 2);
      expect(records.first.size, uint32Max);
      expect(records.last.offset, wrap + 5);
      final nested = Tlv.tlvHeaders(
        7,
        wrap + 18,
        (p) => p == 7 ? 5 : uint32Max,
        (p) => p == 7,
      );
      expect(nested, [const TlvHeader(7, wrap + 5)]);
    });

    test('ZeroFilledPayloadDoesNotExhaustRecovery', () {
      const paddingHeaders = 1000005;
      for (final nested in [false, true]) {
        var reads = 0;
        final leaf = nested ? 12 : 6;
        final records = Tlv.tlvHeaders(
          0,
          wrap + leaf + 5,
          (p) {
            reads++;
            if (p == 0) return leaf - 1;
            if (nested && p == 6) return 5;
            if (p == leaf) return uint32Max;
            if (p >= leaf + 5 && p < leaf + 5 + 6 * paddingHeaders) return 0;
            return uint32Max;
          },
          (p) => p == 0 || (nested && p == 6),
        );
        expect(records, [TlvHeader(0, wrap + leaf - 1)]);
        expect(reads, lessThan((nested ? 2 : 1) * paddingHeaders + 20));
      }
    });

    test('ReportsAttemptExhaustionInsteadOfTruncation', () {
      var reads = 0;
      expect(
        () => Tlv.tlvHeaders(
          0,
          wrap + 1801,
          (p) {
            reads++;
            return p < 1800 ? 0 : uint32Max;
          },
          (_) => true,
        ),
        throwsA(isA<SkpParseException>().having(
          (e) => e.message,
          'message',
          contains('recovery exceeded'),
        )),
      );
      expect(reads, lessThan(1000));
    });

    test('ReusesSuccessfulAndFailedSuffixesAcrossParentEnds', () {
      for (final count in [10000, 100000]) {
        for (var trailing = 0; trailing < 6; trailing++) {
          for (final start in [0, 7]) {
            var reads = 0;
            final records = Tlv.tlvHeaders(
              start,
              start + wrap + 6 * count + trailing,
              (p) {
                reads++;
                final rel = p - start;
                if (rel < 1536 || rel >= wrap) return 0;
                return uint32Max;
              },
              (p) => p - start < 1536,
            );
            expect(records.length, count + (trailing == 5 ? 1 : 0));
            expect(records.first.size, 0);
            expect(records[256].size, uint32Max);
            expect(reads, lessThan(2 * count + 4096));
          }
        }
      }
    });

    test('ReusesLongChildrenAsWellAsLongSuffixes', () {
      const count = 100000;
      var reads = 0;
      final records = Tlv.tlvHeaders(
        0,
        wrap + 12 * count + 1542,
        (p) {
          reads++;
          if (p < 6 * count + 1536 || p >= wrap) return 0;
          return uint32Max;
        },
        (p) => p < 1536,
      );
      expect(records, isNotEmpty);
      expect(records.first.size, 0);
      expect(reads, lessThan(4 * count + 4096));
    });

    test('RecoveryNearSizeTMaximum', () {
      const end = 0x7FFFFFFFFFFFFFFF;
      const start = end - wrap - 11;
      final records = Tlv.tlvHeaders(
        start,
        end,
        (p) => p == start ? 5 : uint32Max,
        (p) => p == start,
      );
      expect(records, [const TlvHeader(start, wrap + 5)]);
    });

    test('RecoveryDepthBoundaryIsExplicit', () {
      for (final depth in [64, 65]) {
        final end = wrap + 6 * depth + 5;
        List<TlvHeader> scan() => Tlv.tlvHeaders(
              0,
              end,
              (p) => (p < 6 * depth && p % 6 == 0)
                  ? ((end - p - 6) & uint32Max)
                  : uint32Max,
              (p) => p < 6 * depth && p % 6 == 0,
            );
        if (depth == 64) {
          expect(scan(), [TlvHeader(0, end - 6)]);
        } else {
          expect(scan, throwsA(isA<SkpParseException>()));
        }
      }
    });

    test('MatchesUncachedReferenceAcrossNestedAndCorruptRanges', () {
      for (var depth = 1; depth <= 3; depth++) {
        for (var wraps = 1; wraps <= 3; wraps++) {
          for (final sibling in [false, true]) {
            for (final corruption in [-1, 0, 1]) {
              final d = SparseRecords();
              var p = 6 * depth;
              for (var i = 0; i < wraps; i++) {
                d.lengths[p] = uint32Max;
                p += wrap + 5;
              }
              d.lengths[p] = 7;
              final innerEnd = p + 13;
              for (var i = 0; i < depth; i++) {
                d.lengths[6 * i] = (innerEnd - 6 * i - 6) & uint32Max;
                d.containers.add(6 * i);
              }
              if (sibling) d.lengths[innerEnd] = 3;
              d.lengths[p] = (7 + corruption) & uint32Max;
              final end = innerEnd + (sibling ? 9 : 0);
              var expected = referencePartition(d, 0, end);
              if (expected == null) {
                expected = <TlvHeader>[];
                for (var q = 0; end - q >= 6;) {
                  final n = d.lengths[q] ?? uint32Max;
                  if (n > end - q - 6) break;
                  expected.add(TlvHeader(q, n));
                  q += 6 + n;
                }
              }
              expect(d.scan(end), expected);
            }
          }
        }
      }
    });

    test('CachedFailureDoesNotHideAnExpandableContainer', () {
      final d = SparseRecords({0: 11, 6: 5, 12: uint32Max}, {0, 6});
      expect(d.scan(wrap + 17), [const TlvHeader(0, wrap + 11)]);
    });

    test('PreservesExactAndSmallLenientRanges', () {
      final d = SparseRecords({0: 3, 9: 0}, {0});
      expect(d.scan(15).length, 2);
      expect(d.scan(16).length, 2);
      expect(d.scan(8).length, 0);
      expect(d.scan(0), isEmpty);
      final bytes =
          Uint8List.fromList([0xF4, 0x01, 6, 0, 0, 0, 0xF6, 0x01, 0, 0, 0, 0]);
      final nodes = Tlv.parseRecursive(bytes, 0, bytes.length);
      expect(nodes.length, 1);
      expect(nodes[0].children.length, 1);
      expect(nodes[0].children[0].tag, 'F601');
      expect(() => Tlv.tlvHeadersBuffer(bytes, 0, bytes.length + 1),
          throwsRangeError);
      expect(Tlv.iterTopLevelLazy(bytes, 0, bytes.length).toList().length, 1);
    });
  });
}
