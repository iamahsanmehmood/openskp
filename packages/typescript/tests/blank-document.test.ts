import { describe, it, expect } from 'vitest';
import * as crypto from 'crypto';
import * as fs from 'fs';
import * as path from 'path';
import { parseSkp } from '../src/index';
import { buildBlankV17, SCAFFOLD_SHA256, VERSION_MAP_V17 } from '../src/blank-document';

function collectFiles(dir: string): string[] {
  const out: string[] = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      out.push(...collectFiles(full));
    } else if (entry.isFile()) {
      out.push(full);
    }
  }
  return out;
}

function extractHexByteTokens(text: string): Buffer {
  const bytes: number[] = [];
  const hexVal = (c: number): number => {
    if (c >= 0x30 && c <= 0x39) return c - 0x30;
    if (c >= 0x61 && c <= 0x66) return c - 0x61 + 10;
    if (c >= 0x41 && c <= 0x46) return c - 0x41 + 10;
    return -1;
  };
  for (let i = 0; i + 3 < text.length; i++) {
    if (text.charCodeAt(i) === 0x30 && (text.charCodeAt(i + 1) === 0x78 || text.charCodeAt(i + 1) === 0x58)) {
      const hi = hexVal(text.charCodeAt(i + 2));
      const lo = hexVal(text.charCodeAt(i + 3));
      const nextIsHex = i + 4 < text.length && hexVal(text.charCodeAt(i + 4)) >= 0;
      if (hi >= 0 && lo >= 0 && !nextIsHex) {
        bytes.push((hi << 4) | lo);
        i += 3;
      }
    }
  }
  return Buffer.from(bytes);
}

describe('Blank v17 document builder', () => {
  const fixturePath = path.join(__dirname, 'fixtures', 'blank_v17.skp');
  const srcDir = path.resolve(__dirname, '..', 'src');

  it('matches blank_v17.skp byte-for-byte and has expected SHA-256', () => {
    const doc = buildBlankV17();
    const expected = fs.readFileSync(fixturePath);
    expect(doc.data.length).toBe(5796);
    const digest = crypto.createHash('sha256').update(doc.data).digest('hex');
    expect(digest).toBe(SCAFFOLD_SHA256);
    expect(Buffer.from(doc.data).equals(expected)).toBe(true);

    expect(doc.modelGuidOffset).toBe(52);
    expect(doc.pidCounterOffset).toBe(1987);
    expect(doc.nextPid).toBe(18);
    expect(doc.noScaleMaskOffset).toBe(2008);
    expect(doc.tailPos).toBe(3515);
    expect(doc.scaffoldNextSlot).toBe(11);
    expect(VERSION_MAP_V17.length).toBe(58);
  });

  it('reads back cleanly in the classic reader', () => {
    const doc = buildBlankV17();
    const view = new DataView(doc.data.buffer, doc.data.byteOffset, doc.data.byteLength);
    let pos = 0;
    const readUtf16 = (): string => {
      expect(doc.data[pos]).toBe(0xff);
      expect(doc.data[pos + 1]).toBe(0xfe);
      expect(doc.data[pos + 2]).toBe(0xff);
      const n = doc.data[pos + 3];
      pos += 4;
      let s = '';
      for (let i = 0; i < n; i++) {
        s += String.fromCharCode(view.getUint16(pos + i * 2, true));
      }
      pos += n * 2;
      return s;
    };

    expect(readUtf16()).toBe('SketchUp Model');
    expect(readUtf16()).toBe('{17.0.1}');
    pos += 16; // model GUID
    expect(readUtf16()).toBe('');
    pos += 4; // save timestamp
    expect(view.getUint16(pos, true)).toBe(0xffff);
    expect(view.getUint16(pos + 2, true)).toBe(0);
    const vmapNameLen = view.getUint16(pos + 4, true);
    pos += 6;
    expect(Buffer.from(doc.data.subarray(pos, pos + vmapNameLen)).toString('ascii')).toBe('CVersionMap');
    pos += vmapNameLen;

    const parsedVmap: Array<readonly [string, number]> = [];
    for (;;) {
      const cls = readUtf16();
      if (cls === 'End-Of-Version-Map') break;
      const schema = view.getUint32(pos, true);
      pos += 4;
      parsedVmap.push([cls, schema]);
    }
    expect(parsedVmap).toEqual(VERSION_MAP_V17);

    const ab = doc.data.buffer.slice(doc.data.byteOffset, doc.data.byteOffset + doc.data.byteLength) as ArrayBuffer;
    const model = parseSkp(ab);
    expect(model.version).toBe('{17.0.1}');
    expect(model.layers.map((l) => l.name)).toEqual(['Layer0']);
    expect(model.layers[0].hidden).toBe(false);
    expect(model.layers[0].color).toEqual({ r: 255, g: 84, b: 84 });
    expect(model.materials).toEqual([]);
    expect(model.definitions.size).toBe(0);
    expect(model.root.faces).toEqual([]);
    expect(model.root.edges).toEqual([]);
    expect(model.root.vertices).toEqual([]);
    expect(model.root.instances).toEqual([]);
    expect(model.pages).toEqual([]);
    expect(model.unreadRootEntities).toBe(0);
    expect(model.faceFrontColor).toEqual([225, 225, 200]);
    expect(model.faceBackColor).toEqual([128, 128, 200]);
    expect(model.colorByTag).toBe(false);
    expect(model.attributeDictionaries).toEqual({});
  });

  it('src/ contains no .skp files and no embedded template bytes', () => {
    const templateBuf = Buffer.from(buildBlankV17().data);
    const templateHex = templateBuf.toString('hex');
    const templateBase64 = templateBuf.toString('base64');

    const files = collectFiles(srcDir);
    expect(files.length).toBeGreaterThan(0);

    for (const file of files) {
      expect(path.extname(file).toLowerCase()).not.toBe('.skp');
      const raw = fs.readFileSync(file);
      expect(raw.indexOf(templateBuf)).toBe(-1);

      const text = raw.toString('utf8');
      const compact = text.replace(/[^0-9A-Za-z+/=]/g, '');
      expect(compact.includes(templateBase64)).toBe(false);
      expect(compact.toLowerCase().includes(templateHex)).toBe(false);
      expect(extractHexByteTokens(text).indexOf(templateBuf)).toBe(-1);
    }
  });
});
