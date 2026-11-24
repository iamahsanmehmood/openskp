import { describe, expect, it } from 'vitest';
import { SkpParseError } from '../src/errors';
import {
  iterTopLevelLazy,
  parseTlvRecursive,
  TlvHeader,
  tlvHeaders,
  tlvHeadersBuffer,
  wrappedModelRecords,
} from '../src/parser';

const WRAP = 0x100000000;
const UINT32_MAX = 0xffffffff;

class SparseRecords {
  lengths = new Map<number, number>();
  containers = new Set<number>();

  constructor(lengths: [number, number][] = [], containers: number[] = []) {
    for (const [k, v] of lengths) this.lengths.set(k, v);
    for (const c of containers) this.containers.add(c);
  }

  scan(end: number): TlvHeader[] {
    return tlvHeaders(
      0,
      end,
      (p) => this.lengths.get(p) ?? UINT32_MAX,
      (p) => this.containers.has(p)
    );
  }
}

function referencePartition(d: SparseRecords, start: number, end: number): TlvHeader[] | null {
  const ordinary: TlvHeader[] = [];
  let p = start;
  while (end - p >= 6) {
    const n = d.lengths.get(p) ?? UINT32_MAX;
    if (n > end - p - 6) break;
    ordinary.push({ offset: p, size: n });
    p += 6 + n;
  }
  if (p === end) return ordinary;
  for (let i = 0; i < ordinary.length; i++) {
    const h = ordinary[i];
    if (!d.containers.has(h.offset)) continue;
    const available = end - h.offset - 6;
    if (available - h.size < WRAP) continue;
    let n = h.size + WRAP;
    while (n <= available) {
      const next = h.offset + 6 + n;
      const suffix = referencePartition(d, next, end);
      if (suffix !== null && referencePartition(d, h.offset + 6, next) !== null) {
        return [...ordinary.slice(0, i), { offset: h.offset, size: n }, ...suffix];
      }
      if (available - n < WRAP) break;
      n += WRAP;
    }
  }
  return null;
}

describe('WrappedTlv (#520, #535)', () => {
  it('ModelCatalogDisambiguatesTwoExactPartitions', () => {
    const entities = WRAP + 48;
    const end = entities + 6;
    const d = new SparseRecords(
      [
        [0, 48],
        [6, 36],
        [12, 30],
        [18, 24],
        [24, UINT32_MAX],
        [WRAP + 29, 13],
        [entities, 0],
        [54, UINT32_MAX - 5],
      ],
      [0, 6, 12, 18]
    );
    const ordinary = d.scan(end);
    expect(ordinary.length).toBe(2);
    expect(ordinary[ordinary.length - 1].offset).toBe(54);
    const tags = new Map<number, number>([
      [0, 0x01f4],
      [6, 0x01f9],
      [12, 0x1770],
      [18, 0x1771],
      [24, 0x157c],
      [WRAP + 29, 0x157c],
      [entities, 0x01f6],
    ]);
    const read = () =>
      wrappedModelRecords(
        end,
        (p) => tags.get(p)!,
        (p) => d.lengths.get(p)!
      );
    const records = read();
    expect(records).not.toBeNull();
    expect(records!.map((r) => r.offset)).toEqual([24, WRAP + 29, entities]);
    d.lengths.set(12, d.lengths.get(12)! + 1);
    expect(read()).toBeNull();
    d.lengths.set(12, d.lengths.get(12)! - 1);
    tags.set(entities, 0x157e);
    expect(read()).toBeNull();
  });

  it('SmallCatalogInsideWrappedModel', () => {
    const entities = WRAP - 16;
    const end = entities + 70;
    const d = new SparseRecords(
      [
        [0, 48],
        [6, (entities - 12) % WRAP],
        [12, (entities - 18) % WRAP],
        [18, (entities - 24) % WRAP],
        [24, (entities - 30) % WRAP],
        [entities, 64],
        [54, UINT32_MAX - 5],
      ],
      [0, 6, 12, 18]
    );
    const tags = new Map<number, number>([
      [0, 0x01f4],
      [6, 0x01f9],
      [12, 0x1770],
      [18, 0x1771],
      [24, 0x157c],
      [entities, 0x01f6],
    ]);
    const ordinary = d.scan(end);
    expect(ordinary.length).toBe(2);
    expect(ordinary[1].offset).toBe(54);
    const records = wrappedModelRecords(
      end,
      (p) => tags.get(p)!,
      (p) => d.lengths.get(p)!
    );
    expect(records).not.toBeNull();
    expect(records!.map((r) => r.offset)).toEqual([24, entities]);
  });

  it('PreservesCompleteShortModelFollowedByTopLevelRecords', () => {
    const end = WRAP + 54;
    const d = new SparseRecords(
      [
        [0, 48],
        [6, 42],
        [12, 36],
        [18, 30],
        [24, 24],
        [54, UINT32_MAX - 5],
      ],
      [0, 6, 12, 18]
    );
    const tags = new Map<number, number>([
      [0, 0x01f4],
      [6, 0x01f9],
      [12, 0x1770],
      [18, 0x1771],
      [24, 0x157c],
      [54, 0x01f6],
    ]);
    const ordinary = d.scan(end);
    expect(ordinary.length).toBe(2);
    expect(ordinary[1].offset).toBe(54);
    expect(
      wrappedModelRecords(
        end,
        (p) => tags.get(p)!,
        (p) => d.lengths.get(p)!
      )
    ).toBeNull();
  });

  it('ExtraTopLevelDefinitionsDoNotExtendACompleteCatalog', () => {
    const entities = WRAP + 48;
    const end = entities + 6;
    const d = new SparseRecords(
      [
        [0, 48],
        [6, 36],
        [12, 30],
        [18, 24],
        [24, 18],
        [48, 0],
        [54, UINT32_MAX - 11],
        [entities, 0],
      ],
      [0, 6, 12, 18]
    );
    const tags = new Map<number, number>([
      [0, 0x01f4],
      [6, 0x01f9],
      [12, 0x1770],
      [18, 0x1771],
      [24, 0x157c],
      [48, 0x157c],
      [54, 0x157c],
      [entities, 0x01f6],
    ]);
    const ordinary = d.scan(end);
    expect(ordinary.length).toBe(3);
    expect(ordinary[1].offset).toBe(54);
    expect(
      wrappedModelRecords(
        end,
        (p) => tags.get(p)!,
        (p) => d.lengths.get(p)!
      )
    ).toBeNull();
  });

  it('NestedContainersAndFollowingSibling', () => {
    const d = new SparseRecords();
    for (let p = 0; p < 24; p += 6) {
      d.lengths.set(p, (WRAP + 30 - p - 6) % WRAP);
      d.containers.add(p);
    }
    d.lengths.set(24, UINT32_MAX);
    d.lengths.set(WRAP + 29, 1);
    for (let p = 0; p < 24; p += 6) d.lengths.set(p, d.lengths.get(p)! + 6);
    d.lengths.set(WRAP + 36, 0);
    const records = d.scan(WRAP + 42);
    expect(records.length).toBe(2);
    expect(records[0].size).toBe(WRAP + 30);
    expect(records[1].offset).toBe(WRAP + 36);
  });

  it('MultipleWraps', () => {
    const d = new SparseRecords(
      [
        [0, 10],
        [6, UINT32_MAX],
        [WRAP + 11, UINT32_MAX],
      ],
      [0]
    );
    const records = d.scan(2 * WRAP + 16);
    expect(records).toEqual([{ offset: 0, size: 2 * WRAP + 10 }]);
  });

  it('RejectsInvalidChildrenAndSuffixAndOpaqueLeaves', () => {
    const d = new SparseRecords(
      [
        [0, 5],
        [6, UINT32_MAX],
      ],
      [0]
    );
    let records = d.scan(WRAP + 11);
    expect(records).toEqual([{ offset: 0, size: WRAP + 5 }]);
    d.lengths.set(6, UINT32_MAX - 1);
    records = d.scan(WRAP + 11);
    expect(records[0].size).toBe(5);
    d.lengths.set(6, UINT32_MAX);
    records = d.scan(WRAP + 12);
    expect(records[0].size).toBe(5);
    d.containers.clear();
    records = d.scan(WRAP + 11);
    expect(records[0].size).toBe(5);
  });

  it('KeepsAnExactLargePartitionAndNonzeroStart', () => {
    const d = new SparseRecords(
      [
        [0, UINT32_MAX],
        [WRAP + 5, 0],
      ],
      [0]
    );
    const records = d.scan(WRAP + 11);
    expect(records.length).toBe(2);
    expect(records[0].size).toBe(UINT32_MAX);
    expect(records[1].offset).toBe(WRAP + 5);
    const nested = tlvHeaders(
      7,
      WRAP + 18,
      (p) => (p === 7 ? 5 : UINT32_MAX),
      (p) => p === 7
    );
    expect(nested).toEqual([{ offset: 7, size: WRAP + 5 }]);
  });

  it('ZeroFilledPayloadDoesNotExhaustRecovery', () => {
    const paddingHeaders = 1000005;
    for (const nested of [false, true]) {
      let reads = 0;
      const leaf = nested ? 12 : 6;
      const records = tlvHeaders(
        0,
        WRAP + leaf + 5,
        (p) => {
          reads++;
          if (p === 0) return leaf - 1;
          if (nested && p === 6) return 5;
          if (p === leaf) return UINT32_MAX;
          if (p >= leaf + 5 && p < leaf + 5 + 6 * paddingHeaders) return 0;
          return UINT32_MAX;
        },
        (p) => p === 0 || (nested && p === 6)
      );
      expect(records).toEqual([{ offset: 0, size: WRAP + leaf - 1 }]);
      expect(reads).toBeLessThan((nested ? 2 : 1) * paddingHeaders + 20);
    }
  });

  it('ReportsAttemptExhaustionInsteadOfTruncation', () => {
    let reads = 0;
    expect(() =>
      tlvHeaders(
        0,
        WRAP + 1801,
        (p) => {
          reads++;
          return p < 1800 ? 0 : UINT32_MAX;
        },
        () => true
      )
    ).toThrow(/recovery exceeded/);
    expect(reads).toBeLessThan(1000);
  });

  it('ReusesSuccessfulAndFailedSuffixesAcrossParentEnds', () => {
    for (const count of [10000, 100000]) {
      for (let trailing = 0; trailing < 6; trailing++) {
        for (const start of [0, 7]) {
          let reads = 0;
          const records = tlvHeaders(
            start,
            start + WRAP + 6 * count + trailing,
            (p) => {
              reads++;
              const rel = p - start;
              if (rel < 1536 || rel >= WRAP) return 0;
              return UINT32_MAX;
            },
            (p) => p - start < 1536
          );
          expect(records.length).toBe(count + (trailing === 5 ? 1 : 0));
          expect(records[0].size).toBe(0);
          expect(records[256].size).toBe(UINT32_MAX);
          expect(reads).toBeLessThan(2 * count + 4096);
        }
      }
    }
  });

  it('ReusesLongChildrenAsWellAsLongSuffixes', () => {
    const count = 100000;
    let reads = 0;
    const records = tlvHeaders(
      0,
      WRAP + 12 * count + 1542,
      (p) => {
        reads++;
        if (p < 6 * count + 1536 || p >= WRAP) return 0;
        return UINT32_MAX;
      },
      (p) => p < 1536
    );
    expect(records.length).toBeGreaterThan(0);
    expect(records[0].size).toBe(0);
    expect(reads).toBeLessThan(4 * count + 4096);
  });

  it('RecoveryNearSizeTMaximum', () => {
    const end = Number.MAX_SAFE_INTEGER;
    const start = end - WRAP - 11;
    const records = tlvHeaders(
      start,
      end,
      (p) => (p === start ? 5 : UINT32_MAX),
      (p) => p === start
    );
    expect(records).toEqual([{ offset: start, size: WRAP + 5 }]);
  });

  it('RecoveryDepthBoundaryIsExplicit', () => {
    for (const depth of [64, 65]) {
      const end = WRAP + 6 * depth + 5;
      const scan = () =>
        tlvHeaders(
          0,
          end,
          (p) => (p < 6 * depth && p % 6 === 0 ? (end - p - 6) % WRAP : UINT32_MAX),
          (p) => p < 6 * depth && p % 6 === 0
        );
      if (depth === 64) {
        expect(scan()).toEqual([{ offset: 0, size: end - 6 }]);
      } else {
        expect(scan).toThrow(SkpParseError);
      }
    }
  });

  it('MatchesUncachedReferenceAcrossNestedAndCorruptRanges', () => {
    for (let depth = 1; depth <= 3; depth++) {
      for (let wraps = 1; wraps <= 3; wraps++) {
        for (const sibling of [false, true]) {
          for (const corruption of [-1, 0, 1]) {
            const d = new SparseRecords();
            let p = 6 * depth;
            for (let i = 0; i < wraps; i++) {
              d.lengths.set(p, UINT32_MAX);
              p += WRAP + 5;
            }
            d.lengths.set(p, 7);
            const innerEnd = p + 13;
            for (let i = 0; i < depth; i++) {
              d.lengths.set(6 * i, (innerEnd - 6 * i - 6) % WRAP);
              d.containers.add(6 * i);
            }
            if (sibling) d.lengths.set(innerEnd, 3);
            d.lengths.set(p, 7 + corruption);
            const end = innerEnd + (sibling ? 9 : 0);
            let expected = referencePartition(d, 0, end);
            if (expected === null) {
              expected = [];
              for (let q = 0; end - q >= 6; ) {
                const n = d.lengths.get(q) ?? UINT32_MAX;
                if (n > end - q - 6) break;
                expected.push({ offset: q, size: n });
                q += 6 + n;
              }
            }
            expect(d.scan(end)).toEqual(expected);
          }
        }
      }
    }
  });

  it('CachedFailureDoesNotHideAnExpandableContainer', () => {
    const d = new SparseRecords(
      [
        [0, 11],
        [6, 5],
        [12, UINT32_MAX],
      ],
      [0, 6]
    );
    expect(d.scan(WRAP + 17)).toEqual([{ offset: 0, size: WRAP + 11 }]);
  });

  it('PreservesExactAndSmallLenientRanges', () => {
    const d = new SparseRecords(
      [
        [0, 3],
        [9, 0],
      ],
      [0]
    );
    expect(d.scan(15).length).toBe(2);
    expect(d.scan(16).length).toBe(2);
    expect(d.scan(8).length).toBe(0);
    expect(d.scan(0)).toEqual([]);
    const bytes = new Uint8Array([0xf4, 0x01, 6, 0, 0, 0, 0xf6, 0x01, 0, 0, 0, 0]);
    const nodes = parseTlvRecursive(bytes, 0, bytes.length);
    expect(nodes.length).toBe(1);
    expect(nodes[0].children.length).toBe(1);
    expect(nodes[0].children[0].tag).toBe('F601');
    expect(() => tlvHeadersBuffer(bytes, 0, bytes.length + 1)).toThrow(RangeError);
    expect([...iterTopLevelLazy(bytes, 0, bytes.length)].length).toBe(1);
  });
});
