import { describe, it, expect } from 'vitest';
import * as fs from 'fs';
import * as path from 'path';
import { zipSync } from 'fflate';
import { parseSkp, SkpParseError } from '../src/index';
import { parseTlvRecursive, MAX_TLV_DEPTH } from '../src/parser';
import { extractSkpContents, readMetaUnits } from '../src/vff';

// Hostile-input limits: a model.dat that nests TLV containers tens of
// thousands of levels deep used to overflow the call stack, a meta.dat
// wrapped thousands of times did the same, and a truncated model.dat parsed
// as a model with no geometry and no error.

function record(tag: [number, number], payload: Uint8Array): Uint8Array {
  const out = new Uint8Array(6 + payload.length);
  out.set(tag, 0);
  new DataView(out.buffer).setUint32(2, payload.length, true);
  out.set(payload, 6);
  return out;
}

/** `levels` records of `tag`, each inside the previous, around `leaf`.
 * Built in one pass: wrapping level by level would copy the payload each
 * time and take quadratic time for the 100,000-level cases. */
function nested(tag: [number, number], levels: number, leaf = new Uint8Array(0)): Uint8Array {
  const out = new Uint8Array(6 * levels + leaf.length);
  const view = new DataView(out.buffer);
  for (let i = 0; i < levels; i++) {
    out.set(tag, 6 * i);
    view.setUint32(6 * i + 2, leaf.length + 6 * (levels - 1 - i), true);
  }
  out.set(leaf, 6 * levels);
  return out;
}

function skp(modelDat: Uint8Array, metaDat?: Uint8Array): ArrayBuffer {
  const entries: Record<string, Uint8Array> = { 'model.dat': modelDat };
  if (metaDat) entries['meta/meta.dat'] = metaDat;
  const zip = zipSync(entries);
  const header = new Uint8Array(32);
  header.set([0xff, 0xfe, 0xff, 0x0e], 0);
  const out = new Uint8Array(header.length + zip.length);
  out.set(header, 0);
  out.set(zip, header.length);
  return out.buffer.slice(out.byteOffset, out.byteOffset + out.byteLength) as ArrayBuffer;
}

function fixtureModelDat(name: string): Uint8Array {
  const file = fs.readFileSync(path.join(__dirname, 'fixtures', name));
  return extractSkpContents(new Uint8Array(file)).modelData;
}

const F401: [number, number] = [0xf4, 0x01];
const WRAPPER: [number, number] = [0x64, 0x00];
const UNITS: [number, number] = [0x6d, 0x00];
const inches = record(UNITS, new TextEncoder().encode('Inches'));

describe('TLV nesting depth', () => {
  it('follows the deepest nesting the limit allows', () => {
    const data = nested(F401, MAX_TLV_DEPTH);
    expect(parseTlvRecursive(data, 0, data.length)).toHaveLength(1);
  });

  it.each([MAX_TLV_DEPTH + 2, 5000, 200000])('rejects %i levels with a parse error', (levels) => {
    const data = nested(F401, levels);
    let error: unknown;
    try {
      parseTlvRecursive(data, 0, data.length);
    } catch (e) {
      error = e;
    }
    expect(error).toBeInstanceOf(SkpParseError);
    expect((error as SkpParseError).stage).toBe('tlv_walk');
    expect((error as SkpParseError).tag).toBe('F401');
  });

  it.each([MAX_TLV_DEPTH + 2, 5000, 200000])('parseSkp rejects %i levels', (levels) => {
    const model = record(F401, nested(F401, levels));
    expect(() => parseSkp(skp(model))).toThrow(/nest deeper/);
  });

  it('rejects nested definition wrappers (F901) too', () => {
    const model = record(F401, nested([0xf9, 0x01], 200000));
    expect(() => parseSkp(skp(model))).toThrow(SkpParseError);
  });

  it('parses moderate nesting', () => {
    const model = record(F401, nested(F401, 40));
    expect(parseSkp(skp(model)).units).toBeNull();
  });
});

describe('meta.dat wrappers', () => {
  it('reads the units from inside a few wrappers', () => {
    let meta = inches;
    for (let i = 0; i < 3; i++) meta = record(WRAPPER, meta);
    expect(readMetaUnits(meta)).toBe('Inches');
  });

  it.each([MAX_TLV_DEPTH + 5, 100000])('gives unknown units for %i wrappers', (levels) => {
    expect(readMetaUnits(nested(WRAPPER, levels, inches))).toBeNull();
  });

  it('does not fail the whole parse', () => {
    const meta = nested(WRAPPER, 100000, inches);
    expect(parseSkp(skp(record(F401, new Uint8Array(0)), meta)).units).toBeNull();
  });
});

describe('truncated model.dat', () => {
  it.each(['SU_File.skp', 'Untitled.skp'])('%s ends exactly on its top-level record', (name) => {
    const model = fixtureModelDat(name);
    expect(6 + new DataView(model.buffer, model.byteOffset).getUint32(2, true)).toBe(model.length);
    const file = fs.readFileSync(path.join(__dirname, 'fixtures', name));
    expect(() => parseSkp(new Uint8Array(file).buffer as ArrayBuffer)).not.toThrow();
  });

  it.each([0.5, 0.99])('a model.dat cut to %d of its length is an error', (keep) => {
    const model = fixtureModelDat('SU_File.skp');
    const cut = model.subarray(0, Math.floor(model.length * keep));
    let error: unknown;
    try {
      parseSkp(skp(cut));
    } catch (e) {
      error = e;
    }
    expect(error).toBeInstanceOf(SkpParseError);
    expect((error as SkpParseError).message).toMatch(/truncated/);
    expect((error as SkpParseError).stage).toBe('tlv_walk');
    expect((error as SkpParseError).tag).toBe('F401');
    expect((error as SkpParseError).offset).toBe(0);
  });

  it('ignores trailing bytes shorter than a record header', () => {
    const model = fixtureModelDat('SU_File.skp');
    const padded = new Uint8Array(model.length + 3);
    padded.set(model, 0);
    expect(() => parseSkp(skp(padded))).not.toThrow();
  });

  it('still parses an empty model.dat', () => {
    expect(() => parseSkp(skp(new Uint8Array(0)))).not.toThrow();
  });
});
