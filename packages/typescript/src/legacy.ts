/**
 * Legacy (classic MFC) SketchUp .skp parser - SketchUp 2013-2020 era.
 *
 * Pre-2021 .skp files are not VFF/ZIP containers: after the same UTF-16
 * header records, the body is one uncompressed MFC CArchive object stream
 * with a single global 1-based store map. This module walks that stream and
 * adapts the result to the same ParsedRawData shape that index.ts's VFF path
 * produces, so parseSkp() handles both eras transparently.
 *
 * Ported line-for-line from the Python implementation (openskp/legacy.py),
 * which was established by clean-room reverse engineering of real SketchUp
 * 2016/2017/2018 files, cross-validated against the same models re-saved as
 * VFF by SketchUp (exact face/edge counts, total area and bounding box
 * parity). See that module's docstring for the full list of format details
 * that differ from the public 2017 format notes.
 */

import { GeometryBuilderFace, GeometryBuilderInstance, ParsedDefinition } from './geometry';
import { Material, Texture, ParsedRawData } from './model';

export class LegacyParseError extends Error {}

const STR_MARKER = new Uint8Array([0xff, 0xfe, 0xff]);

function bytesEqual(a: Uint8Array, b: Uint8Array): boolean {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return false;
  return true;
}

/** Search for `needle` (exact bytes) within [start, end). Returns -1 if absent. */
function findBytes(data: Uint8Array, needle: Uint8Array, start = 0, end = data.length): number {
  const nlen = needle.length;
  const limit = end - nlen;
  outer: for (let i = start; i <= limit; i++) {
    for (let j = 0; j < nlen; j++) {
      if (data[i + j] !== needle[j]) continue outer;
    }
    return i;
  }
  return -1;
}

/** Search for a byte pattern where `null` entries are wildcards. */
function findPattern(data: Uint8Array, pattern: (number | null)[], start = 0, end = data.length): number {
  const patLen = pattern.length;
  const limit = end - patLen;
  outer: for (let i = start; i <= limit; i++) {
    for (let j = 0; j < patLen; j++) {
      const want = pattern[j];
      if (want !== null && data[i + j] !== want) continue outer;
    }
    return i;
  }
  return -1;
}

function asciiBytes(s: string): number[] {
  return s.split('').map((c) => c.charCodeAt(0));
}

function matchesAscii(data: Uint8Array, offset: number, str: string): boolean {
  if (offset + str.length > data.length) return false;
  for (let i = 0; i < str.length; i++) {
    if (data[offset + i] !== str.charCodeAt(i)) return false;
  }
  return true;
}

function toHex(data: Uint8Array): string {
  let s = '';
  for (let i = 0; i < data.length; i++) {
    const h = data[i].toString(16).toUpperCase();
    s += h.length === 1 ? '0' + h : h;
  }
  return s;
}

/** Byte cursor, matching Python's `_R`. */
class R {
  data: Uint8Array;
  pos: number;
  private view: DataView;

  constructor(data: Uint8Array, pos = 0) {
    this.data = data;
    this.pos = pos;
    this.view = new DataView(data.buffer, data.byteOffset, data.byteLength);
  }

  u8(): number {
    const v = this.data[this.pos];
    this.pos += 1;
    return v;
  }

  u16(): number {
    const v = this.view.getUint16(this.pos, true);
    this.pos += 2;
    return v;
  }

  u32(): number {
    const v = this.view.getUint32(this.pos, true);
    this.pos += 4;
    return v;
  }

  i32(): number {
    const v = this.view.getInt32(this.pos, true);
    this.pos += 4;
    return v;
  }

  f64(): number {
    const v = this.view.getFloat64(this.pos, true);
    this.pos += 8;
    return v;
  }

  f64s(n: number): number[] {
    const out: number[] = [];
    for (let i = 0; i < n; i++) out.push(this.f64());
    return out;
  }

  raw(n: number): Uint8Array {
    const v = this.data.subarray(this.pos, this.pos + n);
    this.pos += n;
    return v;
  }

  peek(n: number): Uint8Array {
    return this.data.subarray(this.pos, this.pos + n);
  }

  peekU16(): number {
    return this.view.getUint16(this.pos, true);
  }

  utf16(): string {
    if (!bytesEqual(this.peek(3), STR_MARKER)) {
      throw new LegacyParseError(`expected a string record ${this.ctx()}`);
    }
    this.pos += 3;
    let n = this.u8();
    if (n === 0xff) {
      n = this.u16();
      if (n === 0xffff) {
        n = this.u32();
      }
    }
    const bytes = this.raw(2 * n);
    return new TextDecoder('utf-16le').decode(bytes);
  }

  ctx(back = 16, fwd = 32): string {
    const p = this.pos;
    const before = toHex(this.data.subarray(Math.max(0, p - back), p));
    const after = toHex(this.data.subarray(p, p + fwd));
    return `@0x${p.toString(16)}: ...${before} | ${after}...`;
  }
}

type SlotEntry = ['class', string, number | null] | ['obj', string | null, any];

/** MFC CArchive store-map bookkeeping and object-graph walk, matching Python's `_Archive`. */
class Archive {
  data: Uint8Array;
  ver: number;
  hasPid: boolean;
  r: R;
  slots = new Map<number, SlotEntry>();
  classSlot = new Map<string, number>();
  nextSlot = 0;
  walkBase = 0;
  readers: Record<string, (ar: Archive, r: R) => any> = {};
  currentLoop: number | null = null;
  inEntityList = false;

  constructor(data: Uint8Array, ver: number) {
    this.data = data;
    this.ver = ver;
    this.hasPid = ver >= 17;
    this.r = new R(data);
  }

  alloc(entry: SlotEntry): number {
    const s = this.nextSlot;
    this.slots.set(s, entry);
    this.nextSlot += 1;
    return s;
  }

  readObject(r: R, expect: string | null = null): [number | null, string | null, any] {
    const tag = r.u16();
    if (tag === 0) {
      return [null, null, null];
    }
    if (tag === 0x7fff) {
      const big = r.u32();
      if (big & 0x80000000) {
        return this.newOfClass(r, big & 0x7fffffff, expect);
      }
      return this.backref(big, r);
    }
    if (tag === 0xffff) {
      const schema = r.u16();
      const namelen = r.u16();
      if (namelen > 40) {
        throw new LegacyParseError(`implausible class name length ${r.ctx()}`);
      }
      const nameBytes = r.raw(namelen);
      const name = new TextDecoder('utf-8').decode(nameBytes);
      this.alloc(['class', name, schema]);
      this.classSlot.set(name, this.nextSlot - 1);
      return this.newObj(r, name);
    }
    if (tag & 0x8000) {
      return this.newOfClass(r, tag & 0x7fff, expect);
    }
    return this.backref(tag, r);
  }

  private newOfClass(r: R, cslot: number, expect: string | null): [number, string, any] {
    let ent = this.slots.get(cslot);
    if (ent === undefined) {
      if (expect === null) {
        throw new LegacyParseError(`class-ref to unknown slot ${cslot} ${r.ctx()}`);
      }
      const newEnt: SlotEntry = ['class', expect, null];
      this.slots.set(cslot, newEnt);
      this.classSlot.set(expect, cslot);
      ent = newEnt;
    }
    if (ent[0] !== 'class') {
      throw new LegacyParseError(`class-ref to non-class slot ${cslot} (${ent[1]}) ${r.ctx()}`);
    }
    return this.newObj(r, ent[1]);
  }

  private newObj(r: R, name: string): [number, string, any] {
    this.inEntityList = false;
    const slot = this.alloc(['obj', name, null]);
    const reader = this.readers[name];
    if (reader === undefined) {
      throw new LegacyParseError(`no reader for class ${name} ${r.ctx()}`);
    }
    const value = reader(this, r);
    this.slots.set(slot, ['obj', name, value]);
    return [slot, name, value];
  }

  private backref(slot: number, r: R): [number, string | null, any] {
    const ent = this.slots.get(slot);
    if (ent === undefined) {
      if (slot < this.walkBase) {
        return [slot, 'premodel', null];
      }
      throw new LegacyParseError(`back-ref to unwalked slot ${slot} ${r.ctx()}`);
    }
    if (ent[0] === 'class') {
      throw new LegacyParseError(`back-ref to class slot ${slot} ${r.ctx()}`);
    }
    return [slot, ent[1], ent[2]];
  }
}

// ── shared record blocks ─────────────────────────────────────────────────

function preamble(ar: Archive, r: R): { attrs: any; pid: number } {
  const [, , attrs] = ar.readObject(r, 'CAttributeContainer');
  let pid = 0;
  if (ar.hasPid) {
    const mask = r.u8();
    for (let bit = 0; bit < 8; bit++) {
      if (mask & (1 << bit)) {
        pid |= r.u8() << (8 * bit);
      }
    }
  }
  return { attrs, pid };
}

function drawbase(ar: Archive, r: R): { mat: number; hidden: number; soft: number; smooth: number; layer: number } {
  const b = r.raw(10);
  const view = new DataView(b.buffer, b.byteOffset, b.byteLength);
  return {
    mat: view.getUint16(0, true),
    hidden: b[2],
    soft: b[5],
    smooth: b[6],
    layer: view.getUint16(8, true),
  };
}

// ── entity readers ───────────────────────────────────────────────────────

function readVertex(ar: Archive, r: R): any {
  preamble(ar, r);
  return { k: 'vertex', xyz: r.f64s(3) };
}

function readEdge(ar: Archive, r: R): any {
  preamble(ar, r);
  const db = drawbase(ar, r);
  const [s1] = ar.readObject(r, 'CVertex');
  const [s2] = ar.readObject(r, 'CVertex');
  const [cs, cn] = ar.readObject(r);
  if (cn !== null && cn !== 'CCurve' && cn !== 'CArcCurve') {
    throw new LegacyParseError(`edge curve pointer resolved to ${cn} ${r.ctx()}`);
  }
  return { k: 'edge', db, curve: cs, v1: s1, v2: s2 };
}

function readCurve(ar: Archive, r: R): any {
  preamble(ar, r);
  r.u8();
  const n = r.u32();
  return { k: 'curve', n };
}

function readArcCurve(ar: Archive, r: R): any {
  preamble(ar, r);
  r.raw(5);
  r.f64s(14); // arc frame (center, axes, radius, sweep)
  return { k: 'arccurve' };
}

function readEdgeUse(ar: Archive, r: R): any {
  preamble(ar, r);
  const [es] = ar.readObject(r, 'CEdge');
  const sense = r.u8();
  const [ps] = ar.readObject(r); // parent-loop back-ref: alignment oracle
  if (ps !== ar.currentLoop) {
    throw new LegacyParseError(`edge-use parent slot ${ps} != current loop ${ar.currentLoop} ${r.ctx()}`);
  }
  return { k: 'edgeuse', edge: es, sense };
}

function readLoop(ar: Archive, r: R): any {
  const mySlot = ar.nextSlot - 1;
  const prev = ar.currentLoop;
  ar.currentLoop = mySlot;
  preamble(ar, r);
  r.raw(2); // 2 flag bytes
  const uses: any[] = [];
  while (true) {
    if (r.peekU16() === 0) {
      r.pos += 2;
      break;
    }
    const [, , v] = ar.readObject(r, 'CEdgeUse');
    uses.push(v);
  }
  ar.currentLoop = prev;
  return { k: 'loop', uses };
}

function readFace(ar: Archive, r: R): any {
  const pre = preamble(ar, r);
  const db = drawbase(ar, r);
  const plane = r.f64s(4);
  const nloops = r.u32();
  if (nloops > 10000) {
    throw new LegacyParseError(`implausible loop count ${nloops} ${r.ctx()}`);
  }
  const loops: any[] = [];
  for (let i = 0; i < nloops; i++) {
    const [, , v] = ar.readObject(r, 'CLoop');
    loops.push(v);
  }
  // NOTE: edges first inlined inside this face's loops appear right after
  // the back-material word as redundant back-ref LIST ITEMS - the list loop
  // consumes them (handled by the CLoop/CEdgeUse readers themselves).
  const backMat = r.u16();
  return { k: 'face', db, plane, loops, back_mat: backMat, attrs: pre.attrs };
}

function readAttrContainer(ar: Archive, r: R): any {
  preamble(ar, r);
  const children: [string | null, any][] = [];
  while (true) {
    if (r.peekU16() === 0) {
      r.pos += 2;
      break;
    }
    const [, n, v] = ar.readObject(r, 'CAttributeNamed');
    children.push([n, v]);
  }
  return { k: 'attrs', children };
}

function readAttrNamed(ar: Archive, r: R): any {
  preamble(ar, r);
  r.raw(4);
  const dictname = r.utf16();

  function readTyped(t: number): any {
    if (t === 0x00) return null;
    if (t === 0x04) return r.i32();
    if (t === 0x06) return r.f64();
    if (t === 0x07) return r.u8();
    if (t === 0x09) return r.u32(); // time_t
    if (t === 0x0a) return r.utf16();
    if (t === 0x0b) {
      const n = r.u32();
      if (n > 100000) {
        throw new LegacyParseError(`implausible attr array count ${r.ctx()}`);
      }
      const arr: any[] = [];
      for (let i = 0; i < n; i++) arr.push(readTyped(r.u8()));
      return arr;
    }
    if (t === 0x12) return r.f64s(3); // 3D vector
    throw new LegacyParseError(`unknown attribute value type 0x${t.toString(16)} ${r.ctx()}`);
  }

  const entries: Record<string, any> = {};
  while (true) {
    const key = r.utf16();
    if (key === '') break;
    entries[key] = readTyped(r.u8());
  }
  r.u32();
  return { k: 'dict', name: dictname, entries };
}

function readLayer(ar: Archive, r: R): any {
  preamble(ar, r);
  const name = r.utf16();
  const mid: number[] = [];
  while (mid.length < 8 && !bytesEqual(r.peek(3), STR_MARKER)) {
    mid.push(r.raw(1)[0]); // flags: 3 bytes on v16, 4 on v17+
  }
  r.utf16(); // internal name ("Layer_<name>")
  r.u16();
  const rgba = r.raw(4);
  r.utf16();
  r.raw(21);
  return { k: 'layer', name, hidden: mid.length ? mid[0] : 0, rgba: Array.from(rgba) };
}

function readMaterial(ar: Archive, r: R): any {
  preamble(ar, r);
  const name = r.utf16();
  const texflag = r.u16();
  const out: Record<string, any> = { k: 'material', name };
  if (texflag === 0) {
    const rgba = r.raw(4);
    r.utf16(); // texture path (empty)
    r.raw(8);
    const opacity = r.f64();
    const useOp = r.u8();
    out.rgba = Array.from(rgba);
    out.opacity = opacity;
    out.use_opacity = useOp;
  } else {
    r.raw(ar.ver >= 17 ? 2 : 1); // texture flag pad
    const [s, , dib] = ar.readObject(r, 'CDib');
    if (!(dib && typeof dib === 'object' && dib.k === 'dib')) {
      throw new LegacyParseError(`texture object is not a dib ${r.ctx()}`);
    }
    // optional u32 between the dib and the 2 x f64 applied size
    const marker = findBytes(r.data, STR_MARKER, r.pos, r.pos + 28);
    if (marker - r.pos === 20) {
      r.u32();
    } else if (marker - r.pos !== 16) {
      throw new LegacyParseError(`texture size block misaligned ${r.ctx()}`);
    }
    const w = r.f64();
    const h = r.f64();
    const fname = r.utf16();
    const avg = r.raw(9); // RGBA + 00 + RGBA (colour stored twice)
    r.utf16();
    const blob = r.raw(8); // u32 + u32 colorized flag
    const opacity = r.f64();
    const useOp = r.u8();
    // A colourized (re-tinted) texture stores the ORIGINAL image plus the
    // tint as the average colour; flagged by the second blob u32 or by
    // alpha 0xFF on the stored colour.
    const colorized = Boolean(blob[4]) || avg[3] === 0xff;
    out.rgba = Array.from(avg.subarray(0, 4));
    out.opacity = opacity;
    out.use_opacity = useOp;
    out.tex_dib = s;
    out.tex_w = w;
    out.tex_h = h;
    out.tex_file = fname;
    out.colorized = colorized;
  }
  return out;
}

function readDib(ar: Archive, r: R): any {
  const subtype = r.u32();
  const length = r.u32();
  if (length > r.data.length) {
    throw new LegacyParseError(`implausible dib length ${length} ${r.ctx()}`);
  }
  const data = r.raw(length);
  return { k: 'dib', subtype, data };
}

/** CFaceTextureCoords: texture-mapping matrices + pins. The two trailing
 * u32s are per-side flags: bit 0 = side painted/positioned, bit 1 = texture
 * PROJECTED (e.g. the Add Location terrain drape). */
function readFtc(ar: Archive, r: R): any {
  preamble(ar, r);
  r.u32();
  const ks = r.f64s(24);
  const frontPinsCount = r.u32();
  const frontPins: number[][] = [];
  for (let i = 0; i < frontPinsCount; i++) frontPins.push(r.f64s(4));
  const backPinsCount = r.u32();
  const backPins: number[][] = [];
  for (let i = 0; i < backPinsCount; i++) backPins.push(r.f64s(4));
  const fflags = r.u32();
  const bflags = r.u32();
  return {
    k: 'ftc',
    front: ks.slice(0, 9),
    back: ks.slice(12, 21),
    front_pins: frontPins,
    back_pins: backPins,
    front_projected: Boolean(fflags & 2),
    back_projected: Boolean(bflags & 2),
  };
}

function readCamera(ar: Archive, r: R): any {
  r.raw(137);
  r.u16();
  r.utf16();
  r.raw(33);
  return { k: 'camera' };
}

function readThumbnail(ar: Archive, r: R): any {
  preamble(ar, r);
  ar.readObject(r, 'CCamera');
  const [, , dib] = ar.readObject(r, 'CDib');
  return { k: 'thumbnail', dib };
}

function readRelationship(ar: Archive, r: R): any {
  preamble(ar, r);
  ar.readObject(r);
  ar.readObject(r);
  return { k: 'relationship' };
}

function readConstructionLine(ar: Archive, r: R): any {
  preamble(ar, r);
  drawbase(ar, r);
  r.f64s(3);
  r.f64s(3);
  r.f64s(2);
  r.raw(ar.ver >= 17 ? 7 : 4);
  return { k: 'cline' };
}

function readConstructionPoint(ar: Archive, r: R): any {
  preamble(ar, r);
  const db = drawbase(ar, r);
  const pos = r.f64s(3);
  r.f64s(3);
  r.u8();
  return { k: 'cpoint', db, pos };
}

function readSectionPlane(ar: Archive, r: R): any {
  preamble(ar, r);
  drawbase(ar, r);
  // optional object pointer before the plane; a real plane starts with a
  // unit-normal component (|x| <= 1) - a tag word does not decode as one
  const view = new DataView(r.data.buffer, r.data.byteOffset, r.data.byteLength);
  const first = view.getFloat64(r.pos, true);
  if (!(Math.abs(first) <= 1.0001)) {
    ar.readObject(r);
  }
  r.f64s(4);
  if (bytesEqual(r.peek(3), STR_MARKER)) {
    // v18: name + short label
    r.utf16();
    r.utf16();
  }
  return { k: 'sectionplane' };
}

function readSkFont(ar: Archive, r: R): any {
  ar.readObject(r, 'CAttributeContainer');
  if (ar.hasPid) {
    r.u8();
  }
  r.utf16();
  r.raw(15);
  return { k: 'font' };
}

function readDimLinear(ar: Archive, r: R): any {
  preamble(ar, r);
  const db = drawbase(ar, r);
  const text = r.utf16();
  ar.readObject(r, 'CSkFont');
  r.raw(165);
  return { k: 'dimension', db, text };
}

function readText(ar: Archive, r: R): any {
  preamble(ar, r);
  drawbase(ar, r);
  ar.readObject(r, 'CSkFont');
  // variable-length variant middle, delimited by an 11-byte block
  // `01 00 00 00 ?? 00 03 00 00 00 01` right before the text string
  let p = r.pos;
  let idx = -1;
  while (true) {
    idx = findBytes(r.data, STR_MARKER, p, r.pos + 512);
    if (idx < 0) {
      throw new LegacyParseError(`text delimiter not found ${r.ctx()}`);
    }
    const blk = r.data.subarray(idx - 11, idx);
    if (
      blk[0] === 0x01 && blk[1] === 0x00 && blk[2] === 0x00 && blk[3] === 0x00 &&
      blk[6] === 0x03 && blk[7] === 0x00 && blk[8] === 0x00 && blk[9] === 0x00 &&
      blk[10] === 1
    ) {
      break;
    }
    p = idx + 3;
  }
  r.raw(idx - r.pos);
  const text = r.utf16();
  r.raw(5);
  return { k: 'text', text };
}

function readEntityList(ar: Archive, r: R, count: number, owner: string): [number, string | null, any][] {
  const ents: [number, string | null, any][] = [];
  while (ents.length < count) {
    const p = r.pos;
    const prevFlag = ar.inEntityList;
    ar.inEntityList = true;
    let s: number | null = null;
    let n: string | null = null;
    let v: any = null;
    try {
      [s, n, v] = ar.readObject(r);
    } catch (e) {
      ar.inEntityList = prevFlag;
      if (!(e instanceof LegacyParseError) || owner !== 'root') {
        throw e;
      }
      r.pos = p;
      break;
    }
    ar.inEntityList = prevFlag;
    ents.push([s as number, n, v]);
  }
  return ents;
}

function readDefinition(ar: Archive, r: R): any {
  preamble(ar, r);
  r.raw(ar.ver >= 17 ? 22 : 20); // undecoded base block
  const nlayers = r.u32();
  if (nlayers > 10000) {
    throw new LegacyParseError(`implausible def layer count ${r.ctx()}`);
  }
  for (let i = 0; i < nlayers; i++) {
    ar.readObject(r, 'CLayer');
  }
  let decl = r.u16();
  if (decl === 0x7fff) {
    decl = r.u32();
  }
  r.u32();
  const count = r.u32();
  if (count > 5_000_000) {
    throw new LegacyParseError(`implausible def entity count ${r.ctx()}`);
  }
  const ents = readEntityList(ar, r, count, 'def');
  const nrel = r.u32();
  if (nrel > 100000) {
    throw new LegacyParseError(`definition list misaligned ${r.ctx()}`);
  }
  for (let i = 0; i < nrel; i++) {
    ar.readObject(r, 'CRelationship');
  }
  r.u16();
  const guid = r.raw(16);
  const name = r.utf16();
  r.utf16();
  r.utf16();
  r.u32(); // timestamp

  // undecoded block (~39-47 bytes), then the CThumbnail object
  let tpos: number | null = null;
  const view = new DataView(r.data.buffer, r.data.byteOffset, r.data.byteLength);
  for (let off = 0; off < 96; off++) {
    const p = r.pos + off;
    if (
      r.data[p] === 0xff && r.data[p + 1] === 0xff &&
      r.data[p + 4] === 0x0a && r.data[p + 5] === 0x00 &&
      matchesAscii(r.data, p + 6, 'CThumbnail')
    ) {
      tpos = p;
      break;
    }
    const thumbSlot = ar.classSlot.get('CThumbnail');
    if (thumbSlot !== undefined) {
      if (view.getUint16(p, true) === (0x8000 | thumbSlot)) {
        tpos = p;
        break;
      }
    }
  }
  if (tpos === null) {
    throw new LegacyParseError(`definition tail: thumbnail not found ${r.ctx()}`);
  }
  const gap = r.raw(tpos - r.pos);
  // component-behavior flags sit 9 bytes before the thumbnail:
  // bit 0 = always-faces-camera, bit 1 = shadows-face-sun
  const behavior = gap.length >= 9 ? gap[gap.length - 9] : 0;
  ar.readObject(r, 'CThumbnail');
  return {
    k: 'definition',
    name,
    guid: toHex(guid),
    ents,
    faces_camera: Boolean(behavior & 1),
  };
}

function readInstance(ar: Archive, r: R): any {
  preamble(ar, r);
  const db = drawbase(ar, r);
  const [ds, dn] = ar.readObject(r, 'CComponentDefinition');
  if (dn !== 'CComponentDefinition') {
    throw new LegacyParseError(`instance definition ref is ${dn} ${r.ctx()}`);
  }
  const xf = r.f64s(13);
  const name = r.utf16();
  const guid = r.raw(16);
  return { k: 'instance', db, def: ds, xf, name, guid: toHex(guid) };
}

const READERS: Record<string, (ar: Archive, r: R) => any> = {
  CVertex: readVertex,
  CEdge: readEdge,
  CCurve: readCurve,
  CArcCurve: readArcCurve,
  CEdgeUse: readEdgeUse,
  CLoop: readLoop,
  CFace: readFace,
  CLayer: readLayer,
  CMaterial: readMaterial,
  CDib: readDib,
  CAttributeContainer: readAttrContainer,
  CAttributeNamed: readAttrNamed,
  CCamera: readCamera,
  CThumbnail: readThumbnail,
  CRelationship: readRelationship,
  CComponentDefinition: readDefinition,
  CComponentInstance: readInstance,
  CGroup: readInstance,
  CFaceTextureCoords: readFtc,
  CConstructionLine: readConstructionLine,
  CConstructionPoint: readConstructionPoint,
  CSectionPlane: readSectionPlane,
  CSkFont: readSkFont,
  CDimensionLinear: readDimLinear,
  CText: readText,
};

// ── walk driver ──────────────────────────────────────────────────────────

/** True when `data` is a classic (pre-2021) MFC-container .skp. */
export function isLegacy(data: Uint8Array): boolean {
  if (!(data.length >= 4 && data[0] === 0xff && data[1] === 0xfe && data[2] === 0xff && data[3] === 0x0e)) {
    return false;
  }
  const head100 = data.subarray(0, Math.min(0x100, data.length));
  if (findBytes(head100, new Uint8Array([0x50, 0x4b, 0x03, 0x04])) >= 0) {
    return false;
  }
  const head200 = data.subarray(0, Math.min(0x200, data.length));
  return findBytes(head200, new Uint8Array(asciiBytes('CVersionMap'))) >= 0;
}

const CMATERIAL_PATTERN: (number | null)[] = [
  0xff, 0xff, null, null, 0x09, 0x00,
  ...asciiBytes('CMaterial'),
];

function findVersionMajor(data: Uint8Array): number | null {
  const head = data.subarray(0, Math.min(0x60, data.length));
  // strip all 0x00 bytes (UTF-16LE ASCII text becomes plain ASCII-like)
  const stripped: number[] = [];
  for (let i = 0; i < head.length; i++) {
    if (head[i] !== 0x00) stripped.push(head[i]);
  }
  const text = new TextDecoder('latin1').decode(new Uint8Array(stripped));
  const m = text.match(/\{(\d+)\./);
  if (!m) return null;
  return parseInt(m[1], 10);
}

interface WalkResult {
  ar: Archive;
  root: [number, string | null, any][];
  layers: [number, any][];
  materials: [number, any][];
}

function walk(data: Uint8Array): WalkResult {
  const ver = findVersionMajor(data);
  if (ver === null) {
    throw new LegacyParseError('no version string in header');
  }
  const ar = new Archive(data, ver);
  Object.assign(ar.readers, READERS);
  const r = ar.r;

  // anchor: the material manager (u32 count right before the first
  // CMaterial new-class record)
  const matHdr = findPattern(data, CMATERIAL_PATTERN);
  if (matHdr < 0) {
    throw new LegacyParseError('no CMaterial class record found');
  }
  const headerView = new DataView(data.buffer, data.byteOffset, data.byteLength);
  const matCount = headerView.getUint32(matHdr - 4, true);
  if (matCount > 100000) {
    throw new LegacyParseError('implausible material count');
  }

  // bootstrap the absolute slot base: parse material 1 with a throwaway
  // archive; material 2's class-ref tag names CMaterial's true slot
  if (matCount < 2) {
    throw new LegacyParseError('single-material bootstrap not implemented for this file');
  }
  const boot = new Archive(data, ver);
  Object.assign(boot.readers, READERS);
  boot.nextSlot = 1 << 20;
  boot.walkBase = 1 << 20;
  boot.r.pos = matHdr;
  boot.readObject(boot.r, 'CMaterial');
  const bootTag = boot.r.peekU16();
  if (bootTag === 0xffff || !(bootTag & 0x8000)) {
    throw new LegacyParseError('cannot bootstrap the slot base');
  }
  ar.nextSlot = bootTag & 0x7fff;
  ar.walkBase = ar.nextSlot;

  // material manager
  r.pos = matHdr;
  const materials: [number, any][] = [];
  for (let i = 0; i < matCount; i++) {
    const [s, , v] = ar.readObject(r, 'CMaterial');
    materials.push([s as number, v]);
  }

  // layer list marker: v16 <u32 X><u32 count>, v17+ <u32 X><u8 0><u32 count>
  r.u32();
  if (ver >= 17) {
    r.u8();
  }
  const layerCount = r.u32();
  if (layerCount > 100000) {
    throw new LegacyParseError('implausible layer count');
  }
  const layers: [number, any][] = [];
  for (let i = 0; i < layerCount; i++) {
    const [s, , v] = ar.readObject(r, 'CLayer');
    layers.push([s as number, v]);
  }

  // definition list: object pointer to the ACTIVE layer, then count
  const [, dn] = ar.readObject(r);
  if (dn !== 'CLayer') {
    throw new LegacyParseError(`definition-list anchor is ${dn}, not a layer`);
  }
  const defCount = r.u32();
  if (defCount > 1_000_000) {
    throw new LegacyParseError('implausible definition count');
  }
  for (let i = 0; i < defCount; i++) {
    ar.readObject(r, 'CComponentDefinition');
  }

  // trailing definitions, back-to-back
  const defCls = ar.classSlot.get('CComponentDefinition');
  while (true) {
    const tag = r.peekU16();
    let isDef = defCls !== undefined && tag === (0x8000 | defCls);
    if (!isDef && tag === 0xffff && matchesAscii(r.peek(26), 6, 'CComponentDefinition')) {
      isDef = true;
    }
    if (!isDef) break;
    ar.readObject(r);
  }

  // root entity list
  const rootCount = r.u32();
  if (rootCount > 5_000_000) {
    throw new LegacyParseError('implausible root entity count');
  }
  const root = readEntityList(ar, r, rootCount, 'root');

  return { ar, root, layers, materials };
}

// ── adapter to the shared ParsedRawData shape ───────────────────────────

/** Mirror of geometry.ts's GeometryBuilder (structurally compatible, kept
 * dependency-free from the VFF-specific TLV machinery). */
class LegacyBuilder {
  vertices = new Map<number, [number, number, number]>();
  edges = new Map<number, [number | null, number | null]>();
  edgeFlags = new Map<number, number>();
  faces = new Map<number, GeometryBuilderFace>();
  instances: GeometryBuilderInstance[] = [];
}

function addEdge(builder: LegacyBuilder, slot: number, e: any, slots: Map<number, SlotEntry>): void {
  if (builder.edges.has(slot)) return;
  const v1 = e.v1;
  const v2 = e.v2;
  for (const vs of [v1, v2]) {
    if (vs === null || vs === undefined) continue;
    const ent = slots.get(vs);
    if (ent !== undefined && ent[2] !== null && !builder.vertices.has(vs)) {
      const xyz = ent[2].xyz as number[];
      builder.vertices.set(vs, [xyz[0], xyz[1], xyz[2]]);
    }
  }
  builder.edges.set(slot, [v1, v2]);
  const db = e.db || {};
  const flags = (db.soft ? 0x08 : 0) | (db.smooth ? 0x10 : 0) | (db.hidden ? 0x01 : 0);
  if (flags) {
    builder.edgeFlags.set(slot, flags);
  }
}

function fillBuilder(builder: LegacyBuilder, ents: [number, string | null, any][], slots: Map<number, SlotEntry>): void {
  for (const [s, , v] of ents) {
    if (v === null || typeof v !== 'object') continue;
    const k = v.k;
    if (k === 'edge') {
      addEdge(builder, s, v, slots);
    } else if (k === 'face') {
      const loops: { edgeId: number; orientation: number }[][] = [];
      for (const lp of v.loops) {
        const loop: { edgeId: number; orientation: number }[] = [];
        for (const u of lp.uses) {
          const es = u.edge;
          const ent = slots.get(es);
          if (ent === undefined || ent[2] === null) continue;
          addEdge(builder, es, ent[2], slots);
          loop.push({ edgeId: es, orientation: u.sense ? 1 : 0 });
        }
        loops.push(loop);
      }
      const face: GeometryBuilderFace = {
        loops,
        normal: [v.plane[0], v.plane[1], v.plane[2]],
        materialId: v.db.mat || null,
        backMaterialId: v.back_mat || null,
        uvTransform: null,
        uvTransformBack: null,
      };
      const attrs = v.attrs;
      if (attrs && typeof attrs === 'object') {
        for (const [, cv] of attrs.children || []) {
          if (cv && typeof cv === 'object' && cv.k === 'ftc') {
            face.uvTransform = [...cv.front];
            face.uvTransformBack = [...cv.back];
          }
        }
      }
      builder.faces.set(s, face);
    } else if (k === 'instance') {
      builder.instances.push({
        offset: 0,
        name: v.name,
        refIdx: v.def,
        refGuid: '',
        matrix: [...v.xf],
        materialId: v.db.mat || null,
        layerId: v.db.layer || null,
        children: [],
      });
    }
  }
}

/** Parse a classic MFC .skp into the shared raw-parse shape, which both
 * parseSkp() and buildScene() convert onward from exactly like the VFF
 * path. */
export function parseLegacyToRaw(data: Uint8Array): ParsedRawData {
  let version = 'unknown';
  const second = findBytes(data, STR_MARKER, 4);
  if (second > 0) {
    const textBytes = data.subarray(second + 4, Math.min(second + 100, data.length));
    const text = new TextDecoder('utf-16le', { fatal: false }).decode(textBytes);
    const braceStart = text.indexOf('{');
    const braceEnd = text.indexOf('}');
    if (braceStart >= 0 && braceEnd >= 0) {
      version = text.slice(braceStart, braceEnd + 1);
    }
  }

  let walkResult: WalkResult;
  try {
    walkResult = walk(data);
  } catch (e) {
    if (e instanceof LegacyParseError || e instanceof RangeError) {
      throw new Error(`legacy .skp parse failed: ${(e as Error).message}`);
    }
    throw e;
  }

  const { ar, root, layers, materials } = walkResult;
  const slots = ar.slots;

  // materials - keyed by name like the VFF path
  const materialsMap = new Map<string, Material>();
  const materialIdToName = new Map<number, string>();
  for (const [s, v] of materials) {
    const rgba: number[] = v.rgba || [128, 128, 128, 255];
    // the stored f64 is a TRANSPARENCY (0 = opaque), gated by the trailing
    // use-flag byte; expose opacity like the VFF path
    let trans: number;
    if (v.use_opacity) {
      trans = Math.min(Math.max(1.0 - v.opacity, 0.0), 1.0);
    } else {
      trans = 1.0;
    }
    const colorized: boolean = v.colorized || false;
    let texture: Texture | null = null;
    if ('tex_dib' in v) {
      const dib = slots.get(v.tex_dib);
      const texData: Uint8Array | null = dib && dib[2] ? (dib[2].data as Uint8Array) : null;
      const isPng = texData && texData.length >= 4 &&
        texData[0] === 0x89 && texData[1] === 0x50 && texData[2] === 0x4e && texData[3] === 0x47;
      const ext = isPng ? '.png' : '.jpg';
      const fname = v.tex_file || `${v.name}${ext}`;
      texture = { filename: fname, width: v.tex_w, height: v.tex_h, data: texData };
    }
    const matObj: Material = {
      name: v.name,
      color: { r: rgba[0], g: rgba[1], b: rgba[2] },
      transparency: trans,
      id: null,
      texture,
      // colourize type is not decoded in the legacy record; tint (1) is
      // the correct rendering for the grey base textures observed.
      colorized,
      colorizeType: colorized ? 1 : 0,
    };
    materialsMap.set(v.name, matObj);
    materialIdToName.set(s, v.name);
  }

  // layers
  const layerColors = new Map<string, [number, number, number]>();
  const layerIdToName = new Map<number, string>();
  for (const [s, v] of layers) {
    const rgba: number[] = v.rgba || [136, 136, 136, 255];
    layerColors.set(v.name, [rgba[0], rgba[1], rgba[2]]);
    layerIdToName.set(s, v.name);
  }
  if (!layerColors.has('Layer0')) {
    layerColors.set('Layer0', [136, 136, 136]);
  }

  // definitions
  const defsDict = new Map<number | string, ParsedDefinition>();
  for (const [s, ent] of slots.entries()) {
    if (ent[0] === 'obj' && ent[1] === 'CComponentDefinition' && ent[2]) {
      const d = ent[2];
      const b = new LegacyBuilder();
      fillBuilder(b, d.ents, slots);
      defsDict.set(s, {
        guid: d.guid,
        name: d.name,
        isImage: false,
        alwaysFacesCamera: d.faces_camera || false,
        builder: b,
      });
    }
  }

  const rootBuilder = new LegacyBuilder();
  fillBuilder(rootBuilder, root, slots);
  defsDict.set('ROOT', {
    guid: 'ROOT',
    name: 'ROOT_MODEL',
    isImage: false,
    alwaysFacesCamera: false,
    builder: rootBuilder,
  });

  return {
    version,
    layerColors,
    layerIdToName,
    materialIdToName,
    materialsMap,
    materialsByFolder: new Map(),
    styles: [],
    defsDict,
  };
}
