import { describe, it, expect } from 'vitest';
import { _internal } from '../src/create';

/**
 * ArchiveWriter accumulates the archive in a GrowableBytes (a Uint8Array
 * that doubles on demand) instead of a number[]. A number[] costs ~9 bytes
 * of heap per file byte and toBytes() used to copy the whole file twice
 * more, so a 60 MB write needed ~1.7 GB of transient heap.
 */
describe('GrowableBytes', () => {
  const { GrowableBytes } = _internal;

  it('push appends in order across a capacity doubling', () => {
    const b = new GrowableBytes(4);
    b.push(1, 2, 3);
    b.push(4, 5); // crosses the initial 4-byte capacity
    expect(b.length).toBe(5);
    expect(Array.from(b.view())).toEqual([1, 2, 3, 4, 5]);
    expect(b.buf.length).toBeGreaterThanOrEqual(5);
  });

  it('append takes a Uint8Array or a plain array and keeps earlier bytes', () => {
    const b = new GrowableBytes(2);
    b.push(9);
    b.append(new Uint8Array([1, 2, 3]));
    b.append([4, 5]);
    expect(Array.from(b.view())).toEqual([9, 1, 2, 3, 4, 5]);
  });

  it('view is a window on the live buffer, not a copy', () => {
    const b = new GrowableBytes(8);
    b.push(1, 2);
    const v = b.view();
    expect(v.length).toBe(2);
    b.buf[0] = 7; // in-place patch, as writeU32At does through .buf
    expect(v[0]).toBe(7);
  });

  it('grows past 1 MB without losing bytes', () => {
    const b = new GrowableBytes(16);
    const n = 1_500_000;
    for (let i = 0; i < n; i++) b.push(i & 0xff);
    expect(b.length).toBe(n);
    const v = b.view();
    expect(v[0]).toBe(0);
    expect(v[255]).toBe(255);
    expect(v[n - 1]).toBe((n - 1) & 0xff);
  });
});
