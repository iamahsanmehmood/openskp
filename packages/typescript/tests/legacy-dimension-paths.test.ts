import { describe, it, expect } from 'vitest';
import { R, connectionPaths } from '../src/legacy';

/**
 * Legacy (MFC) linear dimensions anchored inside groups - port of the Python
 * fix (openskp#384) to this reader (openskp#412).
 *
 * Each connection ref of a CDimensionLinear is followed by an entity ref and
 * two lists of entity refs - the instance paths of the anchored entity. On
 * loose geometry they are a null ref and two empty lists (the zeros a fixed
 * 42/82-byte layout used to skip); anchored inside nested components they
 * carry a ref per component, and a fixed-size read slid off the record,
 * silently cutting the root entity list short. The first time an instance is
 * referenced MFC writes it in full, so a path can also hold a whole NEW
 * object.
 *
 * Synthetic bytes only: the real file that exposed this (a SketchUp 2017
 * cabinet, 21 of 96 root instances lost) is private and is not committed.
 */

/** A 6-byte big reference (0x7FFF escape) to an existing object. */
function big(slot: number): number[] {
  return [0xff, 0x7f, slot & 0xff, (slot >> 8) & 0xff, (slot >> 16) & 0xff, (slot >>> 24) & 0xff];
}
const u32 = (n: number): number[] => [n & 0xff, (n >> 8) & 0xff, (n >> 16) & 0xff, (n >>> 24) & 0xff];
const bytes = (...parts: number[][]): Uint8Array => new Uint8Array(parts.flat());
const TAIL = [0x54, 0x41, 0x49, 0x4c]; // "TAIL"

describe('connectionPaths', () => {
  it('loose geometry: paths are empty and ten bytes', () => {
    const data = bytes([0, 0], u32(0), u32(0), TAIL);
    const r = new R(data, 0);
    expect(connectionPaths(null as any, r)).toEqual([null, [], []]);
    expect(r.pos).toBe(10); // the old fixed layout's zeros
  });

  it('paths inside nested components are read ref by ref', () => {
    const data = bytes(
      big(0x0f69ca), u32(2), big(0x166157), big(0x165bbc),
      u32(2), big(0x166157), big(0x165bbd), TAIL
    );
    const r = new R(data, 0);
    const [extra, p1, p2] = connectionPaths(null as any, r);
    expect(extra).toBe(0x0f69ca);
    expect(p1).toEqual([0x166157, 0x165bbc]);
    expect(p2).toEqual([0x166157, 0x165bbd]);
    expect(Array.from(data.slice(r.pos))).toEqual(TAIL);
  });

  it('a path can hold a whole new object', () => {
    // MFC serializes an object in full the first time it is referenced.
    const newObjectTag = [0xff, 0x7f, 0x67, 0x9f, 0x00, 0x80];
    const data = bytes([0, 0], u32(1), newObjectTag, [0x4f, 0x42, 0x4a], u32(0), TAIL);
    const ar = {
      readObject(rd: R) {
        expect(Array.from(rd.data.slice(rd.pos, rd.pos + 6))).toEqual(newObjectTag);
        rd.pos += 6 + 3; // the object's own bytes
        return [4242, 'CGroup', {}];
      },
    };
    const r = new R(data, 0);
    const [extra, p1, p2] = connectionPaths(ar as any, r);
    expect([extra, p1, p2]).toEqual([null, [4242], []]);
    expect(Array.from(data.slice(r.pos))).toEqual(TAIL);
  });
});
