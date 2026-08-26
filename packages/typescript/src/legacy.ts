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
import { EdgeFlagStore } from './edge-flags';
import { DefaultVertexStore, type VertexStore } from './vertex-store';
import { Material, Texture, ParsedRawData } from './model';
import { SkpParseError } from './errors';
import { ParseOptions, PROGRESS_INTERVAL, emitLog, emitProgress } from './observability';

export class LegacyParseError extends Error {}

const STR_MARKER = new Uint8Array([0xff, 0xfe, 0xff]);

/** Widest zero padding seen between the v20 filler's empty string and the
 * count that follows it (9 and 13 bytes occur in real files; the ceiling
 * leaves room without letting the probe wander into unrelated records). */
const MAX_V20_FILLER_PAD = 29;

function bytesEqual(a: Uint8Array, b: Uint8Array): boolean {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return false;
  return true;
}

/**
 * Locates the count that follows a v20 filler record, given the offset the
 * bad count was read from. Pure byte logic, exported for tests; see
 * {@link retryCountAfterV20Filler} for how it is used.
 *
 * Returns the count and the offset just past it, or null when the bytes do
 * not match the filler layout.
 */
export function findCountAfterV20Filler(
  data: Uint8Array,
  countPos: number,
  limit: number,
  ar?: Archive
): { count: number; next: number } | null {
  // the marker sits within a handful of bytes of the bad read; the window is
  // deliberately tight so a coincidental ff-fe-ff further out cannot match
  let markerAt = -1;
  for (let i = countPos; i < countPos + 12 && i + 4 <= data.length; i++) {
    if (data[i] === 0xff && data[i + 1] === 0xfe && data[i + 2] === 0xff) {
      markerAt = i;
      break;
    }
  }
  if (markerAt < 0) return null;
  if (data[markerAt + 3] !== 0) return null; // non-empty string: real data

  // The count sits past a run of zero padding whose length varies per call
  // site (9 and 13 bytes both occur in real files), but always lands at
  // `markerAt + 4 + pad` with `pad % 4 === 1`. Step through those candidate
  // offsets and take the first plausible u32.
  //
  // Deliberately NOT "scan forward to the first non-zero byte": a count that
  // is an exact multiple of 256 has a 0x00 low byte, which such a scan cannot
  // tell apart from padding, so it would skip into the count and misalign
  // every later read. Probing whole u32s at 4-byte strides never inspects an
  // individual byte, so those counts round-trip correctly.
  const view = new DataView(data.buffer, data.byteOffset, data.byteLength);
  for (let pad = 1; pad <= MAX_V20_FILLER_PAD; pad += 4) {
    const at = markerAt + 4 + pad;
    if (at + 4 > data.length) break;
    const count = view.getUint32(at, true);
    if (count > 0 && count <= limit && (ar === undefined || plausibleListTag(ar, data, at + 4))) {
      return { count, next: at + 4 };
    }
  }
  return null;
}

/**
 * SketchUp 2020 (v20) writes an extra, undocumented record ahead of some
 * counts that v17 does not have, which leaves the reader a few bytes early and
 * makes it read garbage as the count. The filler is an empty UTF-16 string
 * record followed by zero padding:
 *
 *   <ff fe ff> <u8 0>        empty string
 *   <zero padding>           runs up to the real count
 *
 * Rather than hard-code an offset (the number of bytes before the marker
 * differs per call site), locate the marker in the short window ahead, then
 * take the first non-zero u32 that follows the padding. Only the EMPTY-string
 * form counts as filler: a real string here would mean genuine data, and
 * moving the cursor past it would corrupt the parse.
 *
 * This only ever runs after a count came back implausible, so files that were
 * already parsing (v17, and the VFF path) never reach it.
 *
 * `countPos` is the offset the count was read FROM (i.e. r.pos - 4).
 * Returns the corrected count, or null when this is not the v20 layout.
 */
function retryCountAfterV20Filler(r: R, countPos: number, limit: number, ar?: Archive): number | null {
  const hit = findCountAfterV20Filler(r.data, countPos, limit, ar);
  if (hit === null) return null;
  r.pos = hit.next;
  return hit.count;
}

/** True when the u16 at `at` can legally start an object read: a null, an
 * escape, a class definition, a class-ref to a KNOWN class, or an object
 * back-ref within the allocated range. */
function plausibleListTag(ar: Archive, data: Uint8Array, at: number): boolean {
  if (at + 2 > data.length) return false;
  const view = new DataView(data.buffer, data.byteOffset, data.byteLength);
  const t = view.getUint16(at, true);
  if (t === 0x0000 || t === 0x7fff || t === 0xffff) return true;
  if (t & 0x8000) {
    const ent = ar.slots.get(t & 0x7fff);
    return ent !== undefined && ent[0] === 'class';
  }
  return t < ar.nextSlot;
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

/**
 * True when the bytes at `p` are an MFC class-ref to class `slot`. Mirrors
 * both encodings Archive.readObject decodes: the short 16-bit form
 * (0x8000|slot) and, for slots past 0x7fff, the big-tag escape (0x7fff
 * followed by a u32 of 0x80000000|slot).
 */
export function isClassRef(data: Uint8Array, p: number, slot: number): boolean {
  const view = new DataView(data.buffer, data.byteOffset, data.byteLength);
  if (slot <= 0x7fff) {
    return p + 2 <= data.length && view.getUint16(p, true) === (0x8000 | slot);
  }
  return (
    p + 6 <= data.length &&
    view.getUint16(p, true) === 0x7fff &&
    view.getUint32(p + 2, true) === (0x80000000 | slot) >>> 0
  );
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
  classSchema = new Map<string, number>();
  currentClass: string | null = null;
  nextSlot = 0;
  walkBase = 0;
  readers: Record<string, (ar: Archive, r: R) => any> = {};
  currentLoop: number | null = null;
  inEntityList = false;

  // Burned store-map indices (see readEdgeUse): the writer maps an
  // annotation's connection points into the store map WITHOUT writing
  // bytes, so file back-references beyond each burn run ahead of the
  // walker's numbering. Registrations always stay at WALKER indices - no
  // captured slot ever goes stale - and backref translates file
  // references through the burn bands instead. `burns` holds
  // [fileBandStart, width] per event; `cumDelta` their total;
  // `annotWatermark` the walker slot right after the last annotation
  // record - the only place a band can start.
  burns: [number, number][] = [];
  cumDelta = 0;
  annotWatermark: number | null = null;
  burnStack: number[] = []; // per-entity-list burned-item credits
  clineTail: number | null = null;

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
      this.classSchema.set(name, schema);
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
    const prevClass = this.currentClass;
    this.currentClass = name;
    let value: any;
    try {
      value = reader(this, r);
    } finally {
      this.currentClass = prevClass;
    }
    this.slots.set(slot, ['obj', name, value]);
    if (name === 'CDimensionLinear' || name === 'CText') {
      this.annotWatermark = this.nextSlot;
    }
    return [slot, name, value];
  }

  /** Map a FILE store-map index to the walker's numbering through the burn
   * bands. Returns the walker slot, or `null` when the reference points
   * INTO a band (a phantom, never-serialized connection point). */
  private translateRef(slot: number): number | null {
    let offset = 0;
    for (const [start, width] of this.burns) {
      if (slot < start) break;
      if (slot < start + width) return null;
      offset += width;
    }
    return slot - offset;
  }

  private backref(slot: number, r: R): [number, string | null, any] {
    if (this.burns.length > 0 && slot >= this.burns[0][0]) {
      const walker = this.translateRef(slot);
      if (walker === null) {
        // a phantom (burned) connection-point index - annotation metadata
        // only; nothing was ever serialized for it
        return [slot, 'reserved', null];
      }
      slot = walker;
    }
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
  const b = r.raw(8);
  const view = new DataView(b.buffer, b.byteOffset, b.byteLength);
  // The layer field is normally a u16 id, but an entity can carry the
  // layer BY OBJECT instead (seen on real 2018 instances): a full inline
  // CLayer record on first use, an escaped back-ref to it on later
  // siblings. Layer ids never have the 0x8000 bit and never equal 0x7FFF,
  // so both object forms are unambiguous.
  const layCls = ar.classSlot.get('CLayer');
  const tag = r.peekU16();
  let layer: number;
  if (layCls !== undefined && tag === (0x8000 | layCls)) {
    ar.readObject(r, 'CLayer');
    layer = 0; // by-object layer: keep the default id
  } else if (tag === 0x7fff) {
    r.u16();
    const big = r.u32();
    if (big & 0x80000000) {
      throw new LegacyParseError(`drawbase layer: unexpected class ${r.ctx()}`);
    }
    layer = 0; // by-object layer (back-ref)
  } else {
    layer = r.u16();
  }
  return {
    mat: view.getUint16(0, true),
    hidden: b[2],
    soft: b[5],
    smooth: b[6],
    layer,
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

/** Record that the writer burned `delta` store-map indices without
 * serializing any bytes for them.
 *
 * SketchUp maps an annotation's connection-point objects into the MFC
 * store map (CArchive::MapObject) when a dimension or leader text is
 * attached to geometry - each mapping consumes an index, but nothing is
 * written to the stream, so the file's later back-references run ahead of
 * a byte-exact walk. The band starts right after the last annotation
 * record (in FILE numbering); registrations never move - backref
 * translates file references through the recorded bands instead, so no
 * slot value captured anywhere can go stale. */
function registerBurn(ar: Archive, delta: number): void {
  ar.burns.push([(ar.annotWatermark as number) + ar.cumDelta, delta]);
  ar.cumDelta += delta;
  ar.annotWatermark = null;
  // each burn event corresponds to ONE phantom top-level entity that the
  // entity list's declared count includes but the stream never carries -
  // credit it so the list doesn't run past its real end
  if (ar.burnStack.length > 0) {
    ar.burnStack[ar.burnStack.length - 1] += 1;
  }
}

function readEdgeUse(ar: Archive, r: R): any {
  preamble(ar, r);
  const [es] = ar.readObject(r, 'CEdge');
  const sense = r.u8();
  // parent-loop back-ref: the alignment oracle. Read as a RAW file index -
  // after annotations the claimed index can sit AHEAD of the walker's
  // numbering (burned MapObject indices, see registerBurn), which is a
  // correction signal, not a mis-parse.
  const p0 = r.pos;
  const tag = r.u16();
  let ps: number | null;
  if (tag === 0x7fff) {
    const big = r.u32();
    if (big & 0x80000000) {
      throw new LegacyParseError(`edge-use parent is a new object ${r.ctx()}`);
    }
    ps = big;
  } else if (tag === 0xffff || tag & 0x8000) {
    throw new LegacyParseError(`edge-use parent is a new object ${r.ctx()}`);
  } else {
    ps = tag !== 0 ? tag : null;
  }
  const expected = ar.currentLoop !== null ? ar.currentLoop + ar.cumDelta : null;
  if (ps !== expected) {
    const delta = typeof ps === 'number' && expected !== null ? ps - expected : 0;
    if (delta > 0 && delta <= 4096 && ar.annotWatermark !== null) {
      registerBurn(ar, delta);
    } else {
      r.pos = p0;
      throw new LegacyParseError(`edge-use parent slot ${ps} != current loop ${expected} ${r.ctx()}`);
    }
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
    if (t === 0x0c) return r.f64(); // Length (a double, inches)
    if (t === 0x0b) {
      const n = r.u32();
      if (n > 100000) {
        throw new LegacyParseError(`implausible attr array count ${r.ctx()}`);
      }
      const arr: any[] = [];
      for (let i = 0; i < n; i++) arr.push(readTyped(r.u8()));
      return arr;
    }
    if (t === 0x11) return r.f64s(3); // 3D point (Geom::Point3d)
    if (t === 0x12) return r.f64s(3); // 3D vector (Geom::Vector3d)
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

// SketchUp's Dynamic Components extension stores its data in an attribute
// dictionary literally named "dynamic_attributes" - a stable, publicly
// documented part of the SketchUp Ruby API
// (Entity#attribute_dictionary("dynamic_attributes")). readAttrContainer/
// readAttrNamed above already fully decode an entity's
// CAttributeContainer into typed (dict-name, {key: value}) pairs for
// other purposes (CFaceTextureCoords lookup on faces) - this just looks
// up that one dictionary by name, mirroring what the VFF path's
// extractDynamicProperties() (geometry.ts) does for D007/DC05 TLV data.
const DYNAMIC_ATTRIBUTES_DICT_NAME = 'dynamic_attributes';

/** Render an already-typed legacy attribute value (number, string, array,
 * or null) as a string, matching the string-valued Record<string, string>
 * contract the VFF path's extractDynamicProperties() produces. */
export function stringifyAttrValue(value: any): string {
  if (value === null || value === undefined) return '';
  if (Array.isArray(value)) return value.map(stringifyAttrValue).join(',');
  return String(value);
}

/** Extract Dynamic Component attribute key/value pairs from a legacy
 * entity's already-parsed CAttributeContainer, or {} when the entity
 * carries no attribute container or no dynamic_attributes dictionary. */
export function extractLegacyDynamicProperties(attrs: any): Record<string, string> {
  if (!attrs || typeof attrs !== 'object') return {};
  for (const [name, value] of attrs.children || []) {
    if (name === DYNAMIC_ATTRIBUTES_DICT_NAME && value && typeof value === 'object') {
      const entries = value.entries || {};
      const properties: Record<string, string> = {};
      for (const key of Object.keys(entries)) {
        properties[key] = stringifyAttrValue(entries[key]);
      }
      return properties;
    }
  }
  return {};
}

function readLayer(ar: Archive, r: R): any {
  preamble(ar, r);
  const name = r.utf16();
  const mid: number[] = [];
  while (mid.length < 8 && !bytesEqual(r.peek(3), STR_MARKER)) {
    mid.push(r.raw(1)[0]); // flags: 3 bytes on v16, 4 on v17+
  }
  r.utf16(); // internal name ("Layer_<name>")
  const flags = r.u16();
  if (flags & 0x00ff) {
    // Colour-by-layer with a TEXTURED material: instead of the flat RGBA,
    // the layer embeds the same texture block a CMaterial carries
    // (SketchUp Pro assigns full materials to layers). Low byte of the
    // flag word set = textured; a plain colour layer has 0 there (its high
    // byte carries an unrelated flag, so the word as a whole is non-zero
    // either way).
    const tex = textureBlock(ar, r);
    r.raw(4); // trailing u32
    return { k: 'layer', name, hidden: mid.length ? mid[0] : 0, rgba: tex.rgba };
  }
  const rgba = r.raw(4);
  r.utf16();
  r.raw(21);
  return { k: 'layer', name, hidden: mid.length ? mid[0] : 0, rgba: Array.from(rgba) };
}

/** The textured-material payload: an embedded CDib plus applied size,
 * source file name, average colour, and opacity. Shared verbatim between a
 * CMaterial with a texture and a colour-by-layer CLayer that carries a
 * textured material. */
function textureBlock(
  ar: Archive,
  r: R
): {
  rgba: number[];
  opacity: number;
  use_opacity: number;
  tex_dib: number;
  tex_w: number;
  tex_h: number;
  tex_file: string;
  colorized: boolean;
} {
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
  // tint as the average colour; flagged by the second blob u32 or by alpha
  // 0xFF on the stored colour.
  const colorized = Boolean(blob[4]) || avg[3] === 0xff;
  return {
    rgba: Array.from(avg.subarray(0, 4)),
    opacity,
    use_opacity: useOp,
    tex_dib: s as number,
    tex_w: w,
    tex_h: h,
    tex_file: fname,
    colorized,
  };
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
    const tex = textureBlock(ar, r);
    out.rgba = tex.rgba;
    out.opacity = tex.opacity;
    out.use_opacity = tex.use_opacity;
    out.tex_dib = tex.tex_dib;
    out.tex_w = tex.tex_w;
    out.tex_h = tex.tex_h;
    out.tex_file = tex.tex_file;
    out.colorized = tex.colorized;
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

/** CImage: an Image entity - instance-shaped: a back-ref to the (already
 * walked) CComponentDefinition holding the image's face and texture, a 3x4
 * placement, a constant 1.0, the source path string (empty in every
 * sample), and a 16-byte GUID. It appears as a normal entity-list item
 * inside the definition that owns the image (typically a face-me/photo
 * definition), whose own tail the ordinary definition reader then
 * consumes. */
function readImage(ar: Archive, r: R): any {
  preamble(ar, r);
  const db = drawbase(ar, r);
  const [ds] = ar.readObject(r); // the image's definition
  const xform = r.f64s(12);
  r.f64(); // constant 1.0
  r.utf16(); // source path
  const guid = r.raw(16);
  return { k: 'image', db, def: ds, xform, guid: toHex(guid) };
}

/** A reference-to-entity tag: dimension connection points and text leader
 * attachments. Unlike `readObject`'s back-ref path, this tolerates a slot
 * the walk has not reached yet - SketchUp serializes a label/dimension
 * BEFORE the entity it anchors to when both live in the same entity list,
 * so the reference can legitimately point forward. Returns the slot
 * number, or `null` for a null reference. */
function entityRef(ar: Archive, r: R): number | null {
  const tag = r.u16();
  if (tag === 0) return null;
  if (tag === 0x7fff) {
    const big = r.u32();
    if (big & 0x80000000) {
      throw new LegacyParseError(`entity ref is a new object ${r.ctx()}`);
    }
    return big;
  }
  if (tag === 0xffff || tag & 0x8000) {
    throw new LegacyParseError(`entity ref is a new object ${r.ctx()}`);
  }
  return tag;
}

function readRelationship(ar: Archive, r: R): any {
  // two object pointers (small maps: two u16 back-refs - which read like
  // the "u32" of the public notes; big maps escalate them to big-tags).
  // They bind an annotation to the entity it labels, and the annotation
  // side is routinely serialized BEFORE the geometry side - so these can
  // point forward, past the walk cursor; entityRef tolerates that where
  // readObject's back-ref path (rightly) does not.
  preamble(ar, r);
  const a = entityRef(ar, r);
  const b = entityRef(ar, r);
  return { k: 'relationship', refs: [a, b] };
}

/** True when the u16 at `at` starts an object read in one of the
 * UNAMBIGUOUS forms: null, escape, class definition, or a class-ref to a
 * class already known. Plain object back-refs are excluded on purpose -
 * any 2-byte junk below 0x8000 would qualify, which is exactly the
 * ambiguity this check exists to avoid. */
function strictNextTag(ar: Archive, data: Uint8Array, at: number, allowNull = true): boolean {
  if (at + 2 > data.length) return false;
  const view = new DataView(data.buffer, data.byteOffset, data.byteLength);
  const t = view.getUint16(at, true);
  if (t === 0x0000) return allowNull;
  if (t === 0x7fff || t === 0xffff) return true;
  if (t & 0x8000) {
    const ent = ar.slots.get(t & 0x7fff);
    return ent !== undefined && ent[0] === 'class';
  }
  return false;
}

function readConstructionLine(ar: Archive, r: R): any {
  preamble(ar, r);
  drawbase(ar, r);
  r.f64s(3);
  r.f64s(3);
  r.f64s(2); // line params (+-~4.4e29 = infinite)
  // The trailing block varies by the WRITING BUILD, not cleanly by
  // version: 7 bytes on the v17 calibration corpus, 4 on v16 and on a real
  // v18, 0 on another real v17. Self-calibrate on the first guide line of
  // the file - the length that lands on a legitimate next tag (strict
  // forms only) - and cache it for the rest of the file.
  let k = ar.clineTail;
  if (k === null) {
    const dflt = ar.ver === 17 ? 7 : 4;
    const order = [dflt, ...[0, 4, 7].filter((c) => c !== dflt)];
    // two passes: a zero tail full of padding can mimic a null tag, so
    // only accept a null-anchored candidate when no candidate lands on a
    // STRONG form (escape / known class / class definition)
    outer: for (const allowNull of [false, true]) {
      for (const cand of order) {
        if (strictNextTag(ar, r.data, r.pos + cand, allowNull)) {
          k = cand;
          break outer;
        }
      }
    }
    if (k === null) k = dflt;
    ar.clineTail = k;
  }
  r.raw(k);
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
  // The tail is NOT a fixed 165-byte blob: it embeds two object
  // references (the dimension's connection points into the geometry).
  // Each is a normal MFC tag - 2 bytes in small files, but 6 bytes once
  // the archive holds more than 0x7FFE objects and the 0x7FFF big-tag
  // escape kicks in - so a fixed-size skip walks off the rails exactly on
  // large models.
  r.raw(37);
  const c1 = entityRef(ar, r); // connection point 1 (may be null)
  r.raw(42);
  const c2 = entityRef(ar, r); // connection point 2 (may be null)
  r.raw(82);
  return { k: 'dimension', db, text, connect: [c1, c2] };
}

function readText(ar: Archive, r: R): any {
  preamble(ar, r);
  const db = drawbase(ar, r);
  ar.readObject(r, 'CSkFont');
  // variable-length variant middle, delimited by an 11-byte block
  // `01 00 00 00 ?? 00 03 00 00 00 01` right before the text string
  let p = r.pos;
  let idx: number;
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
  // Optional leader-attachment refs follow the fixed tail (a text label
  // anchored to geometry stores the anchored entities here; they can
  // point FORWARD - see entityRef). Only the escaped 6-byte form is
  // recognisable without risk: a 2-byte back-ref here would be
  // indistinguishable from the next list item's tag, and every known
  // sample either has no attachments or lives in a >0x7FFE-object file
  // where the escape is mandatory anyway.
  const attach: number[] = [];
  while (true) {
    const head = r.peek(2);
    if (!(head.length === 2 && head[0] === 0xff && head[1] === 0x7f)) break;
    const full = r.peek(6);
    if (full.length < 6) break;
    const dv = new DataView(full.buffer, full.byteOffset, full.byteLength);
    const val = dv.getUint32(2, true);
    if (val & 0x80000000) break; // new-object tag - the next entity
    r.raw(6);
    attach.push(val);
  }
  return { k: 'text', text, db, attach };
}

function readEntityList(ar: Archive, r: R, count: number, owner: string): [number, string | null, any][] {
  const ents: [number, string | null, any][] = [];
  ar.burnStack.push(0);
  try {
    return readEntityListInner(ar, r, count, owner, ents);
  } finally {
    ar.burnStack.pop();
  }
}

function readEntityListInner(
  ar: Archive,
  r: R,
  count: number,
  owner: string,
  ents: [number, string | null, any][]
): [number, string | null, any][] {
  const view = new DataView(r.data.buffer, r.data.byteOffset, r.data.byteLength);
  while (ents.length < count) {
    const p = r.pos;
    const hasBurnCredit = owner === 'def' && ar.burnStack.length > 0 && ar.burnStack[ar.burnStack.length - 1] > 0;
    if (
      hasBurnCredit &&
      p + 25 <= r.data.length &&
      view.getUint32(p, true) === 0 &&
      bytesEqual(r.data.subarray(p + 22, p + 25), STR_MARKER)
    ) {
      // burned MapObject indices (see registerBurn) mean the declared
      // count includes phantom entities the stream never carries; the
      // definition tail signature (nrel=0 + pad + 16-byte GUID + name
      // marker at +22) marks the list's REAL end
      break;
    }
    const prevFlag = ar.inEntityList;
    ar.inEntityList = true;
    try {
      const [s, n, v] = ar.readObject(r);
      ar.inEntityList = prevFlag;
      ents.push([s as number, n, v]);
    } catch (e) {
      ar.inEntityList = prevFlag;
      if (!(e instanceof LegacyParseError)) {
        throw e;
      }
      if (owner === 'root') {
        // over-declared root counts run into the document tail - stop
        r.pos = p;
        break;
      }
      if (hasBurnCredit) {
        // this list had burned MapObject indices (see registerBurn): the
        // phantom connection points were also counted as items, so the
        // declared count overshoots the real records. Stop at the failed
        // item - the definition tail that follows (nrel, GUID anchor,
        // thumbnail scan) validates the cut.
        r.pos = p;
        break;
      }
      throw e;
    }
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
  // like the model-level layer list, the count is REAL layers (new
  // records or back-refs); SketchUp 2020 interleaves null separators
  // between them
  let got = 0;
  while (got < nlayers) {
    if (r.peekU16() === 0) {
      r.pos += 2;
      continue;
    }
    ar.readObject(r, 'CLayer');
    got += 1;
  }
  const decl = r.u16();
  if (decl === 0x7fff) {
    r.u32();
  }
  // v20 can drop its undocumented filler right here, swallowing the u32
  // field (and, behind a layer-separator null, even the decl itself): if
  // the empty-string marker sits in the next few bytes, the real count is
  // the first non-zero u32 after its padding.
  let count: number | null = null;
  if (ar.ver >= 20) {
    count = retryCountAfterV20Filler(r, r.pos, 5_000_000, ar);
  }
  if (count === null) {
    r.u32();
    count = r.u32();
  }
  // A zero count is as much a symptom of the v20 filler as an implausibly
  // large one: the reader lands on the leading zero bytes of the filler
  // instead of the count. A genuinely empty definition reads zero with no
  // filler ahead, and retryCountAfterV20Filler leaves those alone.
  if (count > 5_000_000 || count === 0) {
    const retry = retryCountAfterV20Filler(r, r.pos - 4, 5_000_000, ar);
    if (retry !== null) count = retry;
  }
  if (count > 5_000_000) {
    throw new LegacyParseError(`implausible def entity count ${r.ctx()}`);
  }
  const ents = readEntityList(ar, r, count, 'def');
  let nrel = r.u32();
  if (nrel > 100000) {
    const retry = retryCountAfterV20Filler(r, r.pos - 4, 100_000, ar);
    if (retry !== null) nrel = retry;
  }
  if (nrel > 100000) {
    throw new LegacyParseError(`definition list misaligned ${r.ctx()}`);
  }
  for (let i = 0; i < nrel; i++) {
    ar.readObject(r, 'CRelationship');
  }
  r.u16();
  // The GUID is followed immediately by the name string. Some files (SketchUp
  // 2020) carry two extra bytes ahead of the GUID, which would shift this read
  // and leave the cursor mid-record. Anchor on the string marker that must
  // follow the 16 GUID bytes instead of trusting the fixed prefix width.
  if (!bytesEqual(r.peek(19).subarray(16, 19), STR_MARKER)) {
    for (let skip = 1; skip <= 4; skip++) {
      const at = r.pos + skip;
      if (bytesEqual(r.data.subarray(at + 16, at + 19), STR_MARKER)) {
        r.pos = at;
        break;
      }
    }
  }
  const guid = r.raw(16);
  const name = r.utf16();
  r.utf16();
  r.utf16();
  r.u32(); // timestamp

  // undecoded block (~39-47 bytes), then the CThumbnail object
  let tpos: number | null = null;
  const thumbSlot = ar.classSlot.get('CThumbnail');
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
    if (thumbSlot !== undefined && isClassRef(r.data, p, thumbSlot)) {
      tpos = p;
      break;
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
    shadows_face_sun: Boolean(behavior & 2),
  };
}

function readInstance(ar: Archive, r: R): any {
  const cls = ar.currentClass;
  const pre = preamble(ar, r);
  const db = drawbase(ar, r);
  const [ds, dn] = ar.readObject(r, 'CComponentDefinition');
  if (dn !== 'CComponentDefinition') {
    throw new LegacyParseError(`instance definition ref is ${dn} ${r.ctx()}`);
  }
  const xf = r.f64s(13);
  const name = r.utf16();

  // The trailing instance GUID arrives with CComponentInstance schema 5 /
  // CGroup schema 1; SketchUp 2013 writes CComponentInstance schema 4,
  // whose record ends at the name (see openskp#38 / #40).
  const minSchema = cls === 'CGroup' ? 1 : 5;
  const schema = cls !== null ? ar.classSchema.get(cls) : undefined;
  const guid = schema === undefined || schema >= minSchema ? r.raw(16) : new Uint8Array(0);

  return { k: 'instance', db, def: ds, xf, name, guid: toHex(guid), attrs: pre.attrs };
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
  CImage: readImage,
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

const CLAYER_PATTERN: (number | null)[] = [
  0xff, 0xff, null, null, 0x06, 0x00,
  ...asciiBytes('CLayer'),
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

/** Bootstrap the absolute slot base: parse material 1 with a throwaway
 * archive; material 2's class-ref tag names CMaterial's true slot. */
function bootstrapTwoMaterials(data: Uint8Array, ver: number, matHdr: number): number {
  const boot = new Archive(data, ver);
  Object.assign(boot.readers, READERS);
  boot.nextSlot = 1 << 20;
  boot.walkBase = 1 << 20;
  boot.r.pos = matHdr;
  boot.readObject(boot.r, 'CMaterial');
  const tag = boot.r.peekU16();
  if (tag === 0xffff || !(tag & 0x8000)) {
    throw new LegacyParseError('cannot bootstrap the slot base');
  }
  return tag & 0x7fff;
}

/** Slot-base candidates for files where the two-material trick is
 * unavailable (0 or 1 materials).
 *
 * Parse the model prefix (materials, layer list) with a throwaway base;
 * the object right after the layer list is the definition-list anchor - an
 * ABSOLUTE back-ref to the active layer, an object we just allocated
 * relatively. Each walked layer yields one candidate base; with a single
 * layer (the common case) the answer is exact. */
function probeLayerAnchorBases(data: Uint8Array, ver: number, start: number, matCount: number): number[] {
  const boot = new Archive(data, ver);
  Object.assign(boot.readers, READERS);
  const b0 = 1 << 20;
  boot.nextSlot = b0;
  boot.walkBase = b0;
  boot.r.pos = start;
  for (let i = 0; i < matCount; i++) {
    boot.readObject(boot.r, 'CMaterial');
  }
  boot.r.u32();
  if (ver >= 17) {
    boot.r.u8();
  }
  const layerCount = boot.r.u32();
  if (!(layerCount >= 1 && layerCount <= 100000)) {
    throw new LegacyParseError('implausible layer count in base probe');
  }
  const layerSlots: number[] = [];
  for (let i = 0; i < layerCount; i++) {
    const [s] = boot.readObject(boot.r, 'CLayer');
    layerSlots.push(s as number);
  }
  const [s, n] = boot.readObject(boot.r);
  if (n !== 'premodel') {
    // under the throwaway base every absolute back-ref classifies as
    // premodel; anything else means the prefix did not parse
    throw new LegacyParseError(`base probe: anchor resolved to ${n}`);
  }
  return layerSlots
    .map((rel) => (s as number) - (rel - b0))
    .filter((cand) => cand > 0 && cand < b0);
}

function walk(data: Uint8Array): WalkResult {
  const ver = findVersionMajor(data);
  if (ver === null) {
    throw new LegacyParseError('no version string in header');
  }

  // anchor: the material manager (u32 count right before the first
  // CMaterial new-class record); zero-material files have no CMaterial
  // record anywhere, so fall back to the first CLayer class record and
  // start at the layer-list marker just before it
  const headerView = new DataView(data.buffer, data.byteOffset, data.byteLength);
  const matHdr = findPattern(data, CMATERIAL_PATTERN);
  let start: number;
  let matCount: number;
  if (matHdr >= 0) {
    start = matHdr;
    matCount = headerView.getUint32(matHdr - 4, true);
    if (matCount > 100000) {
      throw new LegacyParseError('implausible material count');
    }
  } else {
    const layerHdr = findPattern(data, CLAYER_PATTERN);
    if (layerHdr < 0) {
      throw new LegacyParseError('no CMaterial or CLayer class record found');
    }
    matCount = 0;
    start = layerHdr - (ver >= 17 ? 9 : 8);
  }

  const bases =
    matCount >= 2 ? [bootstrapTwoMaterials(data, ver, start)] : probeLayerAnchorBases(data, ver, start, matCount);

  let lastExc: unknown = null;
  for (const base of bases) {
    try {
      return walkModel(data, ver, start, matCount, base);
    } catch (exc) {
      if (!(exc instanceof LegacyParseError)) throw exc;
      lastExc = exc;
    }
  }
  if (lastExc !== null) throw lastExc;
  throw new LegacyParseError('no viable slot base candidate');
}

function walkModel(data: Uint8Array, ver: number, start: number, matCount: number, base: number): WalkResult {
  const ar = new Archive(data, ver);
  Object.assign(ar.readers, READERS);
  ar.nextSlot = base;
  ar.walkBase = base;
  const r = ar.r;

  // material manager
  r.pos = start;
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
  // layerCount counts REAL layers. SketchUp 2020 interleaves a null
  // object-ref after each layer record (a separator, not a layer), so
  // counting reads walks off mid-list on files with several layers; count
  // parsed layers instead, skip the separators, and stop early if the
  // next tag is a back-ref (the definition-list anchor) - a v20 variant
  // where the count over-includes separators.
  const layers: [number, any][] = [];
  while (layers.length < layerCount) {
    const tag = r.peekU16();
    if (tag === 0) {
      r.pos += 2;
      continue;
    }
    if (tag !== 0xffff && !(tag & 0x8000)) {
      break;
    }
    const [s, , v] = ar.readObject(r, 'CLayer');
    if (v === null) continue;
    layers.push([s as number, v]);
  }
  // trailing separators (and any layer records past the declared count)
  const layCls = ar.classSlot.get('CLayer');
  while (true) {
    const tag = r.peekU16();
    if (tag === 0) {
      r.pos += 2;
      continue;
    }
    if (layCls !== undefined && tag === (0x8000 | layCls)) {
      const [s, , v] = ar.readObject(r, 'CLayer');
      if (v !== null) layers.push([s as number, v]);
      continue;
    }
    break;
  }

  // definition list: object pointer to the ACTIVE layer, then count
  const [, dn] = ar.readObject(r);
  if (dn !== 'CLayer') {
    throw new LegacyParseError(`definition-list anchor is ${dn}, not a layer`);
  }
  let defCount = r.u32();
  if (defCount > 1_000_000) {
    const retry = retryCountAfterV20Filler(r, r.pos - 4, 1_000_000, ar);
    if (retry !== null) defCount = retry;
  }
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
  let rootCount = r.u32();
  if (rootCount > 5_000_000) {
    const retry = retryCountAfterV20Filler(r, r.pos - 4, 5_000_000, ar);
    if (retry !== null) rootCount = retry;
  }
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
  vertices: VertexStore = new DefaultVertexStore();
  edges = new Map<number, [number | null, number | null]>();
  edgeFlags = new EdgeFlagStore();
  faces = new Map<number, GeometryBuilderFace>();
  instances: GeometryBuilderInstance[] = [];
  sectionPlanes: { plane: [number, number, number, number]; name: string; label: string; hidden: boolean }[] = [];
  texts: { text: string; hidden: boolean }[] = [];
  dimensions: { text: string; hidden: boolean }[] = [];
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
          // Normalize to the documented CoEdge contract (+1 = same
          // direction as the edge, -1 = reversed) - u.sense is the raw
          // SketchUp bit (0 = forward, 1 = reversed).
          loop.push({ edgeId: es, orientation: u.sense ? -1 : 1 });
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
        uvProjected: false,
        uvProjectedBack: false,
        hidden: Boolean(v.db.hidden),
      };
      const attrs = v.attrs;
      if (attrs && typeof attrs === 'object') {
        for (const [, cv] of attrs.children || []) {
          if (cv && typeof cv === 'object' && cv.k === 'ftc') {
            face.uvTransform = [...cv.front];
            face.uvTransformBack = [...cv.back];
            face.uvProjected = cv.front_projected;
            face.uvProjectedBack = cv.back_projected;
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
        hidden: Boolean(v.db.hidden),
        children: [],
        properties: extractLegacyDynamicProperties(v.attrs),
      });
    } else if (k === 'sectionplane') {
      builder.sectionPlanes.push({
        plane: v.plane || [0, 0, 1, 0],
        name: v.name || '',
        label: v.label || '',
        hidden: Boolean(v.db && v.db.hidden),
      });
    } else if (k === 'text') {
      builder.texts.push({
        text: v.text || '',
        hidden: Boolean(v.db && v.db.hidden),
      });
    } else if (k === 'dimension') {
      builder.dimensions.push({
        text: v.text || '',
        hidden: Boolean(v.db && v.db.hidden),
      });
    }
  }
}

/** Parse a classic MFC .skp into the shared raw-parse shape, which both
 * parseSkp() and buildScene() convert onward from exactly like the VFF
 * path. */
export function parseLegacyToRaw(data: Uint8Array, options?: ParseOptions): ParsedRawData {
  const t0 = Date.now();
  emitLog(options, 'info', `Parsing legacy buffer (${data.length} bytes)`);

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
  emitLog(options, 'debug', `Detected legacy version ${version}`);

  let walkResult: WalkResult;
  try {
    walkResult = walk(data);
  } catch (e) {
    if (e instanceof LegacyParseError || e instanceof RangeError) {
      throw new SkpParseError(`legacy .skp parse failed: ${(e as Error).message}`, {
        stage: 'legacy_walk',
        cause: e,
      });
    }
    throw e;
  }

  const { ar, root, layers, materials } = walkResult;
  const slots = ar.slots;
  emitLog(options, 'debug', `Legacy walk complete: ${materials.length} materials, ${layers.length} layers`);

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
      color: { r: rgba[0], g: rgba[1], b: rgba[2], a: rgba[3] },
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
  const layerHidden = new Map<string, boolean>();
  const layerIdToName = new Map<number, string>();
  for (const [s, v] of layers) {
    const rgba: number[] = v.rgba || [136, 136, 136, 255];
    layerColors.set(v.name, [rgba[0], rgba[1], rgba[2]]);
    layerHidden.set(v.name, Boolean(v.hidden));
    layerIdToName.set(s, v.name);
  }
  if (!layerColors.has('Layer0')) {
    layerColors.set('Layer0', [136, 136, 136]);
  }
  if (!layerHidden.has('Layer0')) {
    layerHidden.set('Layer0', false);
  }

  // definitions
  const defsDict = new Map<number | string, ParsedDefinition>();
  let processed = 0;
  let lastSlot: number | undefined;
  try {
    for (const [s, ent] of slots.entries()) {
      lastSlot = s;
      if (ent[0] === 'obj' && ent[1] === 'CComponentDefinition' && ent[2]) {
        const d = ent[2];
        const b = new LegacyBuilder();
        fillBuilder(b, d.ents, slots);
        defsDict.set(s, {
          guid: d.guid,
          name: d.name,
          isImage: false,
          alwaysFacesCamera: d.faces_camera || false,
          shadowsFaceSun: d.shadows_face_sun || false,
          builder: b,
        });
        processed++;
        if (processed % PROGRESS_INTERVAL === 0) {
          emitProgress(options, 'legacy_defs', processed, processed);
          emitLog(options, 'debug', `Processed ${processed} component definitions`);
        }
      }
    }
  } catch (e) {
    throw new SkpParseError(`Failed while building component definitions: ${(e as Error).message}`, {
      stage: 'legacy_defs',
      definitionId: lastSlot,
      cause: e,
    });
  }

  const rootBuilder = new LegacyBuilder();
  fillBuilder(rootBuilder, root, slots);
  defsDict.set('ROOT', {
    guid: 'ROOT',
    name: 'ROOT_MODEL',
    isImage: false,
    alwaysFacesCamera: false,
    shadowsFaceSun: false,
    builder: rootBuilder,
  });

  emitLog(
    options,
    'info',
    `Parse complete: ${defsDict.size} defs (${((Date.now() - t0) / 1000).toFixed(2)}s)`
  );

  return {
    version,
    // Legacy (pre-2021 MFC) files carry no meta/meta.dat container -
    // that's a VFF/ZIP-only construct - so there is no known source for
    // the model's unit-system string here.
    units: null,
    layerColors,
    layerHidden,
    layerIdToName,
    // Classic (pre-2021) files carry no equivalent VFF entity families -
    // the legacy CArchive walker never surfaces scenes or model-level
    // linear dimensions (legacy files still surface text-only dimensions
    // per definition, via each entity list's own CDimensionLinear reads).
    pages: [],
    dimensions: [],
    materialIdToName,
    materialsMap,
    materialsByFolder: new Map(),
    styles: [],
    defsDict,
  };
}
