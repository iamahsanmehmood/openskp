import { describe, it, expect } from 'vitest';
import { create, parseSkp, SkpParseError } from '../src/index';

const MAX_LEGACY_DEPTH = 64;

function u16(x: number): number[] {
  return [x & 0xff, (x >>> 8) & 0xff];
}

function u32(x: number): number[] {
  return [x & 0xff, (x >>> 8) & 0xff, (x >>> 16) & 0xff, (x >>> 24) & 0xff];
}

function wstr(s: string): number[] {
  const out = [0xff, 0xfe, 0xff, s.length];
  for (let i = 0; i < s.length; i++) {
    const code = s.charCodeAt(i);
    out.push(code & 0xff, (code >>> 8) & 0xff);
  }
  return out;
}

function cdecl(name: string, schema = 0): number[] {
  const out = [...u16(0xffff), ...u16(schema), ...u16(name.length)];
  for (let i = 0; i < name.length; i++) out.push(name.charCodeAt(i));
  return out;
}

function findAscii(buf: Uint8Array, needle: string): number {
  outer: for (let i = 0; i + needle.length <= buf.length; i++) {
    for (let j = 0; j < needle.length; j++) {
      if (buf[i + j] !== needle.charCodeAt(j)) continue outer;
    }
    return i;
  }
  throw new Error(`needle ${needle} not found`);
}

function spliceAt(raw: Uint8Array, pos: number, replaceLen: number, mid: number[]): ArrayBuffer {
  const out = new Uint8Array(raw.length - replaceLen + mid.length);
  out.set(raw.subarray(0, pos), 0);
  out.set(mid, pos);
  out.set(raw.subarray(pos + replaceLen), pos + mid.length);
  return out.buffer;
}

function makeNestedObjectSkp(totalDepth: number, onLayer = false): ArrayBuffer {
  const b = create();
  b.addConstructionPoint([1, 2, 3]);
  const raw = b.toBytes();
  const tag = onLayer ? 'CLayer' : 'CConstructionPoint';
  const attrClsSlot = onLayer ? 11 : 13;
  const pos = findAscii(raw, tag) + tag.length;
  if (totalDepth <= 1) {
    return raw.buffer.slice(raw.byteOffset, raw.byteOffset + raw.byteLength) as ArrayBuffer;
  }
  const k = totalDepth - 1;
  const chain: number[] = [...cdecl('CAttributeContainer')];
  for (let i = 0; i < k - 1; i++) chain.push(...u16(0x8000 | attrClsSlot));
  chain.push(...u16(0));
  for (let i = 0; i < k; i++) chain.push(0x00, ...u16(0));
  return spliceAt(raw, pos, 2, chain);
}

function makeNestedAttrArraySkp(arrayDepth: number): ArrayBuffer {
  const b = create();
  b.addConstructionPoint([1, 2, 3]);
  const raw = b.toBytes();
  const pos = findAscii(raw, 'CConstructionPoint') + 'CConstructionPoint'.length;
  const payload: number[] = [
    ...cdecl('CAttributeContainer'), ...u16(0), 0x00,
    ...cdecl('CAttributeNamed'), ...u16(0), 0x00, 0, 0, 0, 0, ...wstr('Dict'),
    ...wstr('k'),
  ];
  for (let i = 0; i < arrayDepth; i++) payload.push(0x0b, ...u32(1));
  payload.push(0x04, ...u32(7), ...wstr(''), ...u32(0), ...u16(0));
  return spliceAt(raw, pos, 2, payload);
}

describe('Classic MFC archive recursion limits (#557)', () => {
  for (const onLayer of [false, true]) {
    for (const depth of [1, 10, MAX_LEGACY_DEPTH]) {
      it(`parses ${depth} nested archive objects (onLayer=${onLayer})`, () => {
        const model = parseSkp(makeNestedObjectSkp(depth, onLayer));
        expect(model.unreadRootEntities).toBe(0);
        expect(model.root.constructionPoints.length).toBe(1);
      });
    }

    for (const depth of [MAX_LEGACY_DEPTH + 1, 5000]) {
      it(`rejects ${depth} nested archive objects (onLayer=${onLayer}) with SkpParseError`, () => {
        let caught: unknown = null;
        try {
          parseSkp(makeNestedObjectSkp(depth, onLayer));
        } catch (e) {
          caught = e;
        }
        expect(caught).toBeInstanceOf(SkpParseError);
        expect((caught as SkpParseError).stage).toBe('legacy_walk');
        expect((caught as SkpParseError).message).toMatch(/depth/i);
      });
    }
  }

  for (const depth of [1, 10, MAX_LEGACY_DEPTH]) {
    it(`parses ${depth} nested CAttributeNamed arrays`, () => {
      const model = parseSkp(makeNestedAttrArraySkp(depth));
      expect(model.unreadRootEntities).toBe(0);
      expect(model.root.constructionPoints.length).toBe(1);
    });
  }

  for (const depth of [MAX_LEGACY_DEPTH + 1, 5000]) {
    it(`rejects ${depth} nested CAttributeNamed arrays with SkpParseError`, () => {
      let caught: unknown = null;
      try {
        parseSkp(makeNestedAttrArraySkp(depth));
      } catch (e) {
        caught = e;
      }
      expect(caught).toBeInstanceOf(SkpParseError);
      expect((caught as SkpParseError).stage).toBe('legacy_walk');
      expect((caught as SkpParseError).message).toMatch(/depth/i);
    });
  }
});
