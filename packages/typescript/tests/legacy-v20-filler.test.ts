import { describe, it, expect } from 'vitest';
import { findCountAfterV20Filler } from '../src/legacy';

/**
 * The v20 filler probe, exercised on synthetic records.
 *
 * Follow-up to the review on openskp#155: the original implementation walked
 * forward to the first non-zero byte and treated it as the count's low byte.
 * That cannot represent a count which is an exact multiple of 256 - its low
 * byte IS 0x00, so the scan walks straight into the count and misaligns every
 * read after it. Probing whole u32s at 4-byte strides fixes that, since no
 * individual byte is ever inspected.
 *
 * Layout (see findCountAfterV20Filler):
 *   <ff fe ff> <u8 0>   empty UTF-16 string
 *   <zero padding>      length varies per call site, always pad % 4 === 1
 *   <u32 count>
 */

/** Builds a filler record followed by `count`, then a class-record header. */
function filler(count: number, pad: number): Uint8Array {
  const bytes = [0xff, 0xfe, 0xff, 0x00];
  for (let i = 0; i < pad; i++) bytes.push(0x00);
  bytes.push(count & 0xff, (count >> 8) & 0xff, (count >> 16) & 0xff, (count >>> 24) & 0xff);
  bytes.push(0xff, 0xff, 0x0b, 0x00); // whatever record comes next
  return new Uint8Array(bytes);
}

describe('findCountAfterV20Filler', () => {
  it('reads the counts and paddings seen in real v20 files', () => {
    // both padding lengths observed in gondola_v20.skp and a second v20 model
    expect(findCountAfterV20Filler(filler(20, 9), 0, 1_000_000)).toEqual({ count: 20, next: 17 });
    expect(findCountAfterV20Filler(filler(5425, 13), 0, 5_000_000)).toEqual({ count: 5425, next: 21 });
  });

  it('reads a count that is an exact multiple of 256', () => {
    // the regression this test exists for: a 0x00 low byte is indistinguishable
    // from padding to a byte-at-a-time scan
    for (const count of [256, 512, 1024, 65536, 16_777_216 / 16]) {
      for (const pad of [9, 13]) {
        const hit = findCountAfterV20Filler(filler(count, pad), 0, 5_000_000);
        expect(hit, `count=${count} pad=${pad}`).not.toBeNull();
        expect(hit!.count, `count=${count} pad=${pad}`).toBe(count);
      }
    }
  });

  it('reports where to resume reading', () => {
    const hit = findCountAfterV20Filler(filler(256, 13), 0, 5_000_000)!;
    // 4 (marker+len) + 13 (padding) + 4 (the count itself)
    expect(hit.next).toBe(21);
  });

  it('ignores a non-empty string record', () => {
    // a real string here is genuine data; moving the cursor past it would
    // corrupt the parse
    const bytes = new Uint8Array([0xff, 0xfe, 0xff, 0x05, 0, 0, 0, 0, 0, 0, 0, 0, 20, 0, 0, 0]);
    expect(findCountAfterV20Filler(bytes, 0, 1_000_000)).toBeNull();
  });

  it('returns null when there is no marker ahead', () => {
    const bytes = new Uint8Array(32); // all zeros, no ff fe ff
    expect(findCountAfterV20Filler(bytes, 0, 1_000_000)).toBeNull();
  });

  it('respects the caller\'s plausibility limit', () => {
    // nrel's limit is 100_000: a value above it is not the count we want
    expect(findCountAfterV20Filler(filler(200_000, 13), 0, 100_000)).toBeNull();
    expect(findCountAfterV20Filler(filler(200_000, 13), 0, 5_000_000)?.count).toBe(200_000);
  });

  it('does not run past the end of the buffer', () => {
    const bytes = new Uint8Array([0xff, 0xfe, 0xff, 0x00, 0, 0]);
    expect(findCountAfterV20Filler(bytes, 0, 1_000_000)).toBeNull();
  });
});
