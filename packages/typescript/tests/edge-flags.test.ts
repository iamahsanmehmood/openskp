import { describe, it, expect } from 'vitest';
import { EdgeFlagStore } from '../src/edge-flags';

/**
 * EdgeFlagStore replaces a `Map<number, number>` that held one byte per
 * edge. It has to be observationally identical to that Map for every
 * access pattern the parsers actually produce - including the one the
 * legacy reader depends on, where an edge with zero flags is never written
 * at all and must stay distinguishable from one written as zero.
 */

describe('EdgeFlagStore', () => {
  it('stores and returns a flag byte', () => {
    const s = new EdgeFlagStore();
    s.set(1, 0x06);
    expect(s.get(1)).toBe(0x06);
    expect(s.has(1)).toBe(true);
    expect(s.size).toBe(1);
  });

  it('returns undefined for an id never written, like Map.get', () => {
    const s = new EdgeFlagStore();
    expect(s.get(42)).toBeUndefined();
    expect(s.has(42)).toBe(false);
    s.set(1, 0x06);
    expect(s.get(999)).toBeUndefined();
    expect(s.get(0)).toBeUndefined();
  });

  it('distinguishes a stored ZERO from an absent id', () => {
    // The legacy reader only writes an edge when its flags are non-zero,
    // so conflating these two would change parsed output.
    const s = new EdgeFlagStore();
    s.set(5, 0);
    expect(s.get(5)).toBe(0);
    expect(s.has(5)).toBe(true);
    expect(s.get(6)).toBeUndefined();
    expect(s.has(6)).toBe(false);
  });

  it('overwrites without double-counting size', () => {
    const s = new EdgeFlagStore();
    s.set(3, 0x06);
    s.set(3, 0x1e);
    expect(s.get(3)).toBe(0x1e);
    expect(s.size).toBe(1);
  });

  it('handles ids far above the first one written (growth)', () => {
    const s = new EdgeFlagStore();
    s.set(1, 0x06);
    s.set(100_000, 0x11);
    expect(s.get(1)).toBe(0x06);
    expect(s.get(100_000)).toBe(0x11);
    expect(s.get(50_000)).toBeUndefined();
    expect(s.size).toBe(2);
  });

  it('handles an id BELOW the first one written (re-base)', () => {
    // Nothing guarantees ids arrive in ascending order.
    const s = new EdgeFlagStore();
    s.set(500, 0x08);
    s.set(10, 0x10);
    s.set(1, 0x01);
    expect(s.get(500)).toBe(0x08);
    expect(s.get(10)).toBe(0x10);
    expect(s.get(1)).toBe(0x01);
    expect(s.get(250)).toBeUndefined();
    expect(s.size).toBe(3);
  });

  it('preserves every entry across repeated re-basing and growth', () => {
    const s = new EdgeFlagStore();
    const written = new Map<number, number>();
    // Deliberately jumps below and above the current range repeatedly.
    const ids = [1000, 5, 2000, 1, 1500, 700, 3, 9999, 250];
    ids.forEach((id, i) => {
      const flags = (i * 7 + 6) & 0xff;
      s.set(id, flags);
      written.set(id, flags);
    });
    for (const [id, flags] of written) {
      expect(s.get(id)).toBe(flags);
    }
    expect(s.size).toBe(written.size);
    // ids never written stay absent
    for (const id of [0, 2, 4, 999, 1001, 10_000]) {
      if (!written.has(id)) expect(s.get(id)).toBeUndefined();
    }
  });

  it('masks a value to one byte, as the Map-held D307 byte always was', () => {
    const s = new EdgeFlagStore();
    s.set(1, 0x1ff);
    expect(s.get(1)).toBe(0xff);
  });

  it('matches a Map exactly on a randomised access pattern', () => {
    // Deterministic pseudo-random ids; no Math.random, so a failure is
    // reproducible. ~14,000 assertions genuinely takes longer than
    // vitest's 5s default on a loaded CI runner - not a regression, just
    // more headroom than the default budget.
    const s = new EdgeFlagStore();
    const ref = new Map<number, number>();
    let seed = 12345;
    const next = () => {
      seed = (seed * 1103515245 + 12345) & 0x7fffffff;
      return seed;
    };
    for (let i = 0; i < 2000; i++) {
      const id = next() % 50_000;
      const flags = next() % 256;
      s.set(id, flags);
      ref.set(id, flags);
    }
    for (let id = 0; id < 50_000; id += 7) {
      expect(s.get(id)).toBe(ref.get(id));
      expect(s.has(id)).toBe(ref.has(id));
    }
    expect(s.size).toBe(ref.size);
  }, 15000);
});
