import { describe, it, expect } from 'vitest';
import {
  DenseEdgeStore,
  MapEdgeStore,
  SortedEdgeStore,
  type EdgeEnds,
  type EdgeStore,
} from '../src/edge-store';
import { reconstructLoopVertices } from '../src/geometry';

/**
 * The stores replace a `Map<number, [v1, v2]>` and must be observationally
 * identical to it for every access pattern the parsers produce: ids out of
 * order (the sorted store inserts in place, the dense one re-bases), `null`
 * endpoints, and sparse ids (the dense one falls back to a Map).
 */

const IMPLS: [string, new () => EdgeStore][] = [
  ['MapEdgeStore', MapEdgeStore],
  ['SortedEdgeStore', SortedEdgeStore],
  ['DenseEdgeStore', DenseEdgeStore],
];

/** Writes the same sequence to a Map and a store and checks every read. */
function sameAsMap(Impl: new () => EdgeStore, writes: [number, EdgeEnds][], probes: number[] = []): void {
  const m = new Map<number, EdgeEnds>();
  const s = new Impl();
  for (const [id, ends] of writes) {
    m.set(id, ends);
    s.set(id, ends);
  }
  expect(s.size).toBe(m.size);
  expect(Array.from(s.entries())).toEqual(Array.from(m.entries()));
  for (const id of [...m.keys(), ...probes]) {
    expect(s.get(id)).toEqual(m.get(id));
    expect(s.has(id)).toBe(m.has(id));
    expect(s.v1(id)).toBe(m.get(id)?.[0]);
    expect(s.v2(id)).toBe(m.get(id)?.[1]);
  }
}

for (const [name, Impl] of IMPLS) {
  describe(name, () => {
    it('stores and returns endpoints', () => {
      const s = new Impl();
      s.set(11, [1, 2]);
      expect(s.get(11)).toEqual([1, 2]);
      expect(s.v1(11)).toBe(1);
      expect(s.v2(11)).toBe(2);
      expect(s.has(11)).toBe(true);
      expect(s.size).toBe(1);
    });

    it('returns undefined for an absent id, like Map.get', () => {
      const s = new Impl();
      expect(s.get(42)).toBeUndefined();
      expect(s.v1(42)).toBeUndefined();
      expect(s.has(42)).toBe(false);
      s.set(1, [0, 0]);
      expect(s.get(999)).toBeUndefined();
      expect(s.get(0)).toBeUndefined();
      expect(s.v2(999)).toBeUndefined();
    });

    it('keeps null endpoints distinct from vertex 0', () => {
      // The 2021+ reader stores null when B90B/BA0B is missing; vertex id 0
      // is a real id. buildDefinition maps null to 0 later, the scene skips it.
      sameAsMap(Impl, [
        [1, [null, 0]],
        [2, [0, null]],
        [3, [null, null]],
      ]);
    });

    it('overwrites in place without growing size or moving the entry', () => {
      sameAsMap(Impl, [
        [7, [1, 2]],
        [8, [3, 4]],
        [7, [null, 9]],
      ]);
    });

    it('keeps the largest u32 vertex ids and values outside u32 exactly', () => {
      sameAsMap(Impl, [
        [1, [0xfffffffd, 0xfffffffe]],
        [2, [0xffffffff, 2 ** 40]],
        [3, [-1, 1.5]],
        [3, [5, 6]], // overwriting a side-stored value drops it
      ]);
    });

    it('accepts the u32 range of ids and rejects anything else', () => {
      sameAsMap(Impl, [
        [0, [1, 2]],
        [0xffffffff, [3, 4]],
      ]);
      const s = new Impl();
      expect(() => s.set(-1, [1, 2])).toThrow(RangeError);
      expect(() => s.set(2 ** 32, [1, 2])).toThrow(RangeError);
      expect(() => s.set(1.5, [1, 2])).toThrow(RangeError);
    });

    it('handles sparse ids', () => {
      sameAsMap(Impl, [
        [5, [1, 1]],
        [100_000, [2, 2]],
        [37, [3, 3]],
        [3_000_000_000, [4, 4]],
      ], [6, 36, 99_999, 4]);
    });

    it('handles descending and interleaved ids', () => {
      const writes: [number, EdgeEnds][] = [];
      for (let id = 5000; id > 0; id -= 7) writes.push([id, [id + 1, id + 2]]);
      for (let id = 5003; id < 9000; id += 5) writes.push([id, [id, null]]);
      sameAsMap(Impl, writes, [1, 4, 5002, 9001]);
    });

    it('preserves insertion order past the initial capacity', () => {
      const writes: [number, EdgeEnds][] = [];
      let seed = 12345;
      for (let i = 0; i < 5000; i++) {
        seed = (seed * 1103515245 + 12345) & 0x7fffffff;
        writes.push([seed % 20000, [i, i % 3 === 0 ? null : i * 2]]);
      }
      sameAsMap(Impl, writes);
    });

    it('feeds reconstructLoopVertices exactly as the Map does', () => {
      const m = new Map<number, EdgeEnds>([
        [11, [1, 2]],
        [12, [2, 3]],
        [13, [3, null]],
        [14, [4, 1]],
      ]);
      const s = new Impl();
      for (const [id, ends] of m) s.set(id, ends);
      const loop = [
        { edgeId: 11, orientation: 1 },
        { edgeId: 99, orientation: 1 }, // absent edge: skipped
        { edgeId: 12, orientation: -1 },
        { edgeId: 13, orientation: -1 }, // null start: skipped
        { edgeId: 14, orientation: 1 },
      ];
      expect(reconstructLoopVertices(loop, s)).toEqual(reconstructLoopVertices(loop, m));
    });
  });
}

describe('DenseEdgeStore index', () => {
  it('falls back to a Map when ids are too sparse for a span-sized array', () => {
    // 1000 ids spread over 4e9: a span-sized index would be 16 GB.
    const writes: [number, EdgeEnds][] = [];
    for (let i = 0; i < 1000; i++) writes.push([i * 4_000_000, [i, i + 1]]);
    writes.push([3, [7, 8]]);
    sameAsMap(DenseEdgeStore, writes, [1, 4_000_001]);
  });

  it('falls back on a re-base that would make the index too sparse', () => {
    sameAsMap(DenseEdgeStore, [
      [4_000_000_000, [1, 2]],
      [4_000_000_001, [3, 4]],
      [10, [5, 6]],
      [4_000_000_002, [7, 8]],
    ], [11, 3_999_999_999]);
  });
});
