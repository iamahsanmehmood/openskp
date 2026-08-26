import { describe, it, expect } from 'vitest';
import { readMetaUnits } from '../src/vff';
import { buildModelFromParsed, ParsedRawData } from '../src/model';

/**
 * SkpModel.units - the model's unit-system string, read from meta/meta.dat
 * in VFF files. Never opened by any parser before this (zero references to
 * the filename anywhere in the codebase). Confirmed plaintext payload in a
 * real fixture (Untitled.skp): meta.dat uses the same low-level TLV framing
 * as model.dat (2-byte tag + 4-byte little-endian length + payload), one
 * flat record list wrapped in a single outer record (tag 0x6400); tag
 * 0x6D00 carries the units string as plain text, alongside sibling tags for
 * the SketchUp version, save path, and thumbnail references that no parser
 * surfaces either.
 */

function tlv(tag: number[], payload: Uint8Array): Uint8Array {
  const out = new Uint8Array(6 + payload.length);
  out[0] = tag[0];
  out[1] = tag[1];
  new DataView(out.buffer).setUint32(2, payload.length, true);
  out.set(payload, 6);
  return out;
}

describe('readMetaUnits', () => {
  it('extracts units from the exact real fixture bytes', () => {
    // The exact 388-byte meta/meta.dat payload from a real VFF fixture
    // (Untitled.skp, SketchUp 25.0.575) - byte-for-byte, not hand-crafted.
    const hex =
      '6400' + '7e010000' +
      '7500' + '08000000' + Buffer.from('25.0.575').toString('hex') +
      '7600' + '02000000' + '1800' +
      '7700' + '02000000' + '0200' +
      '7300' + '02000000' + '0100' +
      '7400' + '02000000' + '1100' +
      '6600' + '10000000' + 'dcd4752a383d724783022fa29cda3224' +
      '6700' + '2e000000' + '2823' + '28000000' + '2923' + '04000000' + '04000000' + '2a23' + '18000000' +
        Buffer.from('meta/model_thumbnail.png').toString('hex') +
      '6800' + '30000000' + '2823' + '2a000000' + '2923' + '04000000' + '04000000' + '2a23' + '1a000000' +
        Buffer.from('meta/preview_thumbnail.png').toString('hex') +
      '6900' + '01000000' + '01' +
      '6a00' + '00000000' +
      '6b00' + '00000000' +
      '6c00' + '00000000' +
      '6e00' + '00000000' +
      '7100' + '01000000' + '00' +
      '7900' + '01000000' + '00' +
      '7200' + '01000000' + '00' +
      '6d00' + '0a000000' + Buffer.from('Millimeter').toString('hex') +
      '7000' + '01000000' + '01' +
      '6f00' + '27000000' + Buffer.from("E:/Devs/TEst/Skp Test/ref2/Untitled.skp").toString('hex') +
      '7800' + '52000000' +
        'c800' + '4c000000' +
        'c900' + '46000000' +
        'ca00' + '40000000' +
        'cb00' + '22000000' + Buffer.from('SketchUp Client (Windows) 25.0.575').toString('hex') +
      'cc00' + '04000000' + '23c5326a' +
      'cd00' + '08000000' + 'ec443dc9b4db9877';
    const bytes = new Uint8Array(Buffer.from(hex, 'hex'));

    expect(readMetaUnits(bytes)).toBe('Millimeter');
  });

  it('extracts units from a minimal synthetic record', () => {
    const inner = tlv([0x6d, 0x00], new TextEncoder().encode('Inches'));
    const outer = tlv([0x64, 0x00], inner);
    expect(readMetaUnits(outer)).toBe('Inches');
  });

  it('returns null when the units tag is absent', () => {
    const inner = tlv([0x75, 0x00], new TextEncoder().encode('25.0.575'));
    const outer = tlv([0x64, 0x00], inner);
    expect(readMetaUnits(outer)).toBeNull();
  });

  it('returns null for empty or truncated bytes', () => {
    expect(readMetaUnits(new Uint8Array(0))).toBeNull();
    expect(readMetaUnits(new Uint8Array([1, 2, 3]))).toBeNull();
  });
});

function parsed(overrides: Partial<ParsedRawData>): ParsedRawData {
  return {
    version: 'test',
    units: null,
    layerColors: new Map(),
    layerHidden: new Map(),
    layerIdToName: new Map(),
    pages: [],
    dimensions: [],
    materialIdToName: new Map(),
    materialsMap: new Map(),
    materialsByFolder: new Map(),
    styles: [],
    defsDict: new Map(),
    ...overrides,
  };
}

describe('buildModelFromParsed units', () => {
  it('carries units through to the model', () => {
    const model = buildModelFromParsed(parsed({ units: 'Millimeter' }));
    expect(model.units).toBe('Millimeter');
  });

  it('defaults to null when absent', () => {
    const model = buildModelFromParsed(parsed({}));
    expect(model.units).toBeNull();
  });
});
