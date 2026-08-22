import { describe, it, expect } from 'vitest';
import { MapVertexStore, SortedVertexStore, type VertexStore } from '../src/vertex-store';

/**
 * Both stores replace a `Map<number, [x, y, z]>` and must be
 * observationally identical to it for every access pattern the parsers
 * produce - including ids arriving out of order, which the sorted store
 * handles by inserting in place and is the easiest thing to get subtly
 * wrong.
 */

const IMPLS: [string, new () => VertexStore][] = [
  ['MapVertexStore', MapVertexStore],
  ['SortedVertexStore', SortedVertexStore],
];

for (const [name, Impl] of IMPLS) {
  describe(name, () => {
    it('stores and returns coordinates', () => {
      const s = new Impl();
      s.set(1, [1.5, -2.5, 3.25]);
      expect(s.get(1)).toEqual([1.5, -2.5, 3.25]);
      expect(s.has(1)).toBe(true);
      expect(s.size).toBe(1);
    });

    it('returns undefined for an absent id, like Map.get', () => {
      const s = new Impl();
      expect(s.get(42)).toBeUndefined();
      expect(s.has(42)).toBe(false);
      s.set(1, [0, 0, 0]);
      expect(s.get(999)).toBeUndefined();
      expect(s.get(0)).toBeUndefined();
    });

    it('overwrites in place without growing size', () => {
      const s = new Impl();
      s.set(7, [1, 2, 3]);
      s.set(7, [9, 8, 7]);
      expect(s.get(7)).toEqual([9, 8, 7]);
      expect(s.size).toBe(1);
    });

    it('preserves f64 precision', () => {
      // The file stores doubles; narrowing to f32 would change geometry.
      const s = new Impl();
      const precise = 1234.5678901234567;
      s.set(1, [precise, -precise, precise / 3]);
      expect(s.get(1)![0]).toBe(precise);
      expect(s.get(1)![1]).toBe(-precise);
      expect(s.get(1)![2]).toBe(precise / 3);
    });

    it('handles sparse ids', () => {
      // Real files are ~33% dense, as low as 7.6% within one definition.
      const s = new Impl();
      s.set(5, [1, 1, 1]);
      s.set(100_000, [2, 2, 2]);
      s.set(37, [3, 3, 3]);
      expect(s.get(5)).toEqual([1, 1, 1]);
      expect(s.get(100_000)).toEqual([2, 2, 2]);
      expect(s.get(37)).toEqual([3, 3, 3]);
      expect(s.get(50_000)).toBeUndefined();
      expect(s.size).toBe(3);
    });

    it('handles ids arriving OUT OF ORDER', () => {
      // The sorted store inserts in place here; nothing guarantees the
      // parser emits ascending ids.
      const s = new Impl();
      const ids = [500, 5, 2000, 1, 1500, 700, 3, 9999, 250];
      ids.forEach((id, i) => s.set(id, [i, i * 2, i * 3]));
      ids.forEach((id, i) => expect(s.get(id)).toEqual([i, i * 2, i * 3]));
      expect(s.size).toBe(ids.length);
    });

    it('grows past its initial capacity', () => {
      const s = new Impl();
      const N = 5000; // well past INITIAL_CAPACITY
      for (let i = 0; i < N; i++) s.set(i * 3, [i, i + 0.5, -i]);
      expect(s.size).toBe(N);
      for (let i = 0; i < N; i += 137) {
        expect(s.get(i * 3)).toEqual([i, i + 0.5, -i]);
      }
      expect(s.get(1)).toBeUndefined();
    });

    it('iterates entries in INSERTION order', () => {
      // buildDefinition() and the JSON export depend on this ordering.
      const s = new Impl();
      const ids = [900, 10, 500, 1];
      ids.forEach((id, i) => s.set(id, [i, 0, 0]));
      expect([...s.entries()].map(([id]) => id)).toEqual(ids);
      expect([...s.ids()]).toEqual(ids);
    });

    it('matches a reference Map on a randomised pattern', () => {
      // Deterministic LCG - a failure is reproducible.
      const s = new Impl();
      const ref = new Map<number, [number, number, number]>();
      let seed = 987654321;
      const next = () => {
        seed = (seed * 1103515245 + 12345) & 0x7fffffff;
        return seed;
      };
      for (let i = 0; i < 3000; i++) {
        const id = next() % 40_000;
        const xyz: [number, number, number] = [next() % 1000, next() % 1000, next() % 1000];
        s.set(id, xyz);
        ref.set(id, xyz);
      }
      expect(s.size).toBe(ref.size);
      for (let id = 0; id < 40_000; id += 11) {
        expect(s.get(id)).toEqual(ref.get(id));
        expect(s.has(id)).toBe(ref.has(id));
      }
      // insertion order must match the Map's too
      expect([...s.ids()]).toEqual([...ref.keys()]);
    });
  });
}

describe('the two stores agree with each other', () => {
  it('produces identical results for the same write sequence', () => {
    const a = new MapVertexStore();
    const b = new SortedVertexStore();
    let seed = 24680;
    const next = () => {
      seed = (seed * 1103515245 + 12345) & 0x7fffffff;
      return seed;
    };
    for (let i = 0; i < 2000; i++) {
      const id = next() % 20_000;
      const xyz: [number, number, number] = [next() % 100, next() % 100, next() % 100];
      a.set(id, xyz);
      b.set(id, xyz);
    }
    expect(a.size).toBe(b.size);
    expect([...a.ids()]).toEqual([...b.ids()]);
    for (const id of a.ids()) {
      expect(a.get(id)).toEqual(b.get(id));
    }
  });
});
