import { describe, it, expect } from 'vitest';
import * as fs from 'fs';
import * as path from 'path';
import { parseSkp } from '../src/index';
import { findEmptyRootAfterV20Filler } from '../src/legacy';

function load(): ArrayBuffer {
  const buf = fs.readFileSync(path.join(__dirname, 'fixtures', 'legacy_empty_2020.skp'));
  return buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength) as ArrayBuffer;
}
/**
 * A SketchUp 2020 model with nothing in it (no entities, no component definitions) opens.
 *
 * Fixture legacy_empty_2020.skp: an empty model saved as a SketchUp 2020 file (SUModelVersion 13) by
 * SketchUp's own library, the blank-template case. SketchUp 2020 writes an empty string record and zero
 * padding where the definition count would be; with nothing in the model the only count behind it is the
 * root entity count, which is 0, and the filler search skipped it, so the whole file was rejected with
 * "implausible definition count". No private content.
 */
describe('an empty SketchUp 2020 model', () => {
  it('parses to an empty model', () => {
    const model = parseSkp(load());
    expect(model.layers.map((l) => l.name)).toEqual(['Layer0']);
    expect(model.definitions.size).toBe(0);
    expect(model.materials.length).toBe(0);
    expect(model.root.faces.length).toBe(0);
    expect(model.root.instances.length).toBe(0);
  });
});

describe('findEmptyRootAfterV20Filler', () => {
  const filler = (zeros: number, tail: number[] = [0x58, 0xe7, 0x37, 0x6a]) =>
    new Uint8Array([0, 0, 0, 0xff, 0xfe, 0xff, 0, ...new Array(zeros).fill(0), ...tail]);

  it('returns the root count offset, 13 bytes of padding behind the string length byte', () => {
    expect(findEmptyRootAfterV20Filler(filler(26), 0)).toBe(3 + 4 + 13);
  });

  it('treats a non-empty string as real data', () => {
    const data = filler(30);
    data[6] = 5;
    expect(findEmptyRootAfterV20Filler(data, 0)).toBeNull();
  });

  it('rejects a non-zero byte in the padding or the count', () => {
    for (let at = 4; at < 4 + 13 + 4; at++) {
      const data = filler(26);
      data[3 + at] = 1;
      expect(findEmptyRootAfterV20Filler(data, 0), `byte ${at}`).toBeNull();
    }
  });

  it('rejects data with no filler nearby and a truncated filler', () => {
    expect(findEmptyRootAfterV20Filler(new Uint8Array(64), 0)).toBeNull();
    expect(findEmptyRootAfterV20Filler(filler(5, []), 0)).toBeNull();
  });
});
