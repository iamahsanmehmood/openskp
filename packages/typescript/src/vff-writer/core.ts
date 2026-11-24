/**
 * Record-tree and VFF-container primitives, ported from Python's
 * `openskp/_vff_writer/tlv.py` and `archive.py`.
 *
 * A VFF `model.dat` is a single record tree: 16-bit little-endian tag,
 * 32-bit little-endian payload length, payload.  Unknown records stay opaque
 * bytes so a template round-trips unchanged.  The container is a ZIP whose
 * header carries the clean-room integrity word; only `fflate` is used for
 * DEFLATE.  The integrity-word description was taken from skpwright's
 * `docs/SKP_FORMAT.md` §16.15 (https://github.com/hew3d/skpwright, by Kurt
 * Granroth, GPL-3.0-only); this implementation is openskp's own and was
 * verified against genuine SketchUp 2025 saves.  skpwright's docs also
 * pointed to the ZIP layout and `meta/meta.dat` fields, confirmed from those
 * saves; no skpwright code is used or included.
 */
import { deflateSync, inflateSync } from 'fflate';

export class Record {
  readonly tag: number;
  payload: Uint8Array;
  children: Record[] | null;

  constructor(tag: number, payload: Uint8Array | number[] = new Uint8Array(0), children?: Record[] | null) {
    if (tag < 0 || tag > 0xffff) throw new Error(`record tag out of range: ${tag}`);
    this.tag = tag;
    if (children !== undefined && children !== null) {
      this.payload = new Uint8Array(0);
      this.children = children;
    } else {
      this.payload = payload instanceof Uint8Array ? payload : Uint8Array.from(payload);
      this.children = null;
    }
  }

  get isContainer(): boolean {
    return this.children !== null;
  }

  value(): Uint8Array {
    if (this.children === null) return this.payload;
    return concat(this.children.map((child) => child.encode()));
  }

  encode(): Uint8Array {
    const body = this.value();
    const out = new Uint8Array(6 + body.length);
    const view = new DataView(out.buffer);
    view.setUint16(0, this.tag, true);
    view.setUint32(2, body.length, true);
    out.set(body, 6);
    return out;
  }

  child(tag: number): Record | null {
    if (!this.children) return null;
    for (const candidate of this.children) if (candidate.tag === tag) return candidate;
    return null;
  }

  childrenWith(tag: number): Record[] {
    return this.children ? this.children.filter((child) => child.tag === tag) : [];
  }
}

export function record(tag: number, payload: Uint8Array | number[] = new Uint8Array(0)): Record {
  return new Record(tag, payload instanceof Uint8Array ? payload : Uint8Array.from(payload));
}

export function concat(parts: Uint8Array[]): Uint8Array {
  let length = 0;
  for (const part of parts) length += part.length;
  const out = new Uint8Array(length);
  let offset = 0;
  for (const part of parts) {
    out.set(part, offset);
    offset += part.length;
  }
  return out;
}

export function u8(value: number): Uint8Array {
  return new Uint8Array([value & 0xff]);
}

export function u16(value: number): Uint8Array {
  const out = new Uint8Array(2);
  new DataView(out.buffer).setUint16(0, value & 0xffff, true);
  return out;
}

export function u32(value: number): Uint8Array {
  const out = new Uint8Array(4);
  new DataView(out.buffer).setUint32(0, value >>> 0, true);
  return out;
}

export function i32(value: number): Uint8Array {
  const out = new Uint8Array(4);
  new DataView(out.buffer).setInt32(0, value | 0, true);
  return out;
}

export function f64(value: number): Uint8Array {
  const out = new Uint8Array(8);
  new DataView(out.buffer).setFloat64(0, value, true);
  return out;
}

export function f64s(values: ArrayLike<number>): Uint8Array {
  const out = new Uint8Array(values.length * 8);
  const view = new DataView(out.buffer);
  for (let i = 0; i < values.length; i++) view.setFloat64(i * 8, values[i], true);
  return out;
}

export function readF64s(payload: Uint8Array): number[] {
  const view = new DataView(payload.buffer, payload.byteOffset, payload.byteLength);
  const out: number[] = [];
  for (let offset = 0; offset + 8 <= payload.length; offset += 8) out.push(view.getFloat64(offset, true));
  return out;
}

export function varint(value: number): Uint8Array {
  if (!Number.isInteger(value) || value < 0) throw new Error('ids are unsigned integers');
  let width = 1;
  while (value >= 2 ** (8 * width) && width < 6) width++;
  const out = new Uint8Array(width);
  let remaining = value;
  for (let i = 0; i < width; i++) {
    out[i] = remaining % 256;
    remaining = Math.floor(remaining / 256);
  }
  return out;
}

export function readVarint(payload: Uint8Array): number {
  let value = 0;
  for (let i = payload.length - 1; i >= 0; i--) value = value * 256 + payload[i];
  return value;
}

export function idList(ids: number[]): Uint8Array {
  const parts: Uint8Array[] = [];
  for (const id of ids) {
    const encoded = varint(id);
    parts.push(u8(encoded.length), encoded);
  }
  return concat(parts);
}

export function decodeIdList(payload: Uint8Array): number[] {
  const ids: number[] = [];
  let pos = 0;
  while (pos < payload.length) {
    const width = payload[pos];
    if (width < 1 || width > 8 || pos + 1 + width > payload.length) throw new Error('malformed entity order list');
    ids.push(readVarint(payload.subarray(pos + 1, pos + 1 + width)));
    pos += 1 + width;
  }
  return ids;
}

export function newellNormal(points: ArrayLike<number>[]): [number, number, number] {
  let nx = 0;
  let ny = 0;
  let nz = 0;
  const count = points.length;
  for (let i = 0; i < count; i++) {
    const a = points[i];
    const b = points[(i + 1) % count];
    const x1 = a[0], y1 = a[1], z1 = a[2];
    const x2 = b[0], y2 = b[1], z2 = b[2];
    nx += (y1 - y2) * (z1 + z2);
    ny += (z1 - z2) * (x1 + x2);
    nz += (x1 - x2) * (y1 + y2);
  }
  return [nx, ny, nz];
}

export function unitNormal(normal: ArrayLike<number>): [number, number, number] {
  const length = Math.sqrt(normal[0] * normal[0] + normal[1] * normal[1] + normal[2] * normal[2]);
  if (length === 0) throw new Error('degenerate polygon: zero-area normal');
  return [normal[0] / length, normal[1] / length, normal[2] / length];
}

const HEADER_SIZE = 6;

function tryParse(
  data: Uint8Array,
  start: number,
  end: number,
  containers: Set<number>,
  depth: number,
): Record[] | null {
  if (depth > 64) return null;
  const records: Record[] = [];
  let pos = start;
  while (pos < end) {
    if (end - pos < HEADER_SIZE) return null;
    const view = new DataView(data.buffer, data.byteOffset + pos, HEADER_SIZE);
    const tag = view.getUint16(0, true);
    const size = view.getUint32(2, true);
    const bodyStart = pos + HEADER_SIZE;
    const bodyEnd = bodyStart + size;
    if (bodyEnd > end) return null;
    let children: Record[] | null = null;
    if (size > 0 && containers.has(tag)) children = tryParse(data, bodyStart, bodyEnd, containers, depth + 1);
    records.push(children === null ? record(tag, data.subarray(bodyStart, bodyEnd)) : new Record(tag, undefined, children));
    pos = bodyEnd;
  }
  return records;
}

export function parseRecords(data: Uint8Array, containers: Iterable<number> = [], strict = true): Record[] {
  const parsed = tryParse(data, 0, data.length, new Set(containers), 0);
  if (parsed === null) {
    if (strict) throw new Error('data does not decode as VFF records');
    return [record(0, data)];
  }
  return parsed;
}

export function encodeRecords(records: Record[]): Uint8Array {
  return concat(records.map((value) => value.encode()));
}

// ── ZIP ─────────────────────────────────────────────────────────────────

const ZIP_LOCAL = new Uint8Array([0x50, 0x4b, 0x03, 0x04]);
const VERSION_NEEDED = 45;
const CENTRAL_VERSION_NEEDED = 20;
const DOS_DATE = 0x0020;
const ZIP64_EXTRA = new Uint8Array([0x01, 0x00, 0x10, 0x00, ...new Array(16).fill(0)]);

export interface ZipEntry {
  name: string;
  data: Uint8Array;
  flag: number;
  internalAttr: number;
}

export function makeZipEntry(name: string, data: Uint8Array, flag = 0x0006): ZipEntry {
  return { name, data, flag, internalAttr: flag !== 0 && name.endsWith('.xml') ? 1 : 0 };
}

export function findSequence(data: Uint8Array, sequence: number[], start = 0): number {
  const limit = data.length - sequence.length;
  outer: for (let i = start; i <= limit; i++) {
    for (let j = 0; j < sequence.length; j++) if (data[i + j] !== sequence[j]) continue outer;
    return i;
  }
  return -1;
}

export function zipOffset(data: Uint8Array): number {
  const offset = findSequence(data, Array.from(ZIP_LOCAL));
  if (offset < 0) throw new Error('no embedded ZIP archive');
  return offset;
}

export function readZip(data: Uint8Array, offset: number): ZipEntry[] {
  const entries: ZipEntry[] = [];
  let pos = offset;
  while (pos + 4 <= data.length && findSequence(data, Array.from(ZIP_LOCAL), pos) === pos) {
    if (pos + 30 > data.length) throw new Error('truncated ZIP local header');
    const flag = readLe16(data, pos + 6);
    const method = readLe16(data, pos + 8);
    const crc = readLe32(data, pos + 14);
    const compressedSize = readLe32(data, pos + 18);
    const nameLength = readLe16(data, pos + 26);
    const extraLength = readLe16(data, pos + 28);
    const nameStart = pos + 30;
    const name = new TextDecoder('utf-8').decode(data.subarray(nameStart, nameStart + nameLength));
    const bodyStart = nameStart + nameLength + extraLength;
    const bodyEnd = bodyStart + compressedSize;
    if (bodyEnd > data.length) throw new Error(`truncated ZIP entry ${name}`);
    const raw = data.subarray(bodyStart, bodyEnd);
    const body = method === 0 ? raw : method === 8 ? inflateSync(raw) : null;
    if (body === null) throw new Error(`unsupported ZIP compression method ${method}`);
    if (crc32(body) !== crc) throw new Error(`CRC mismatch in ZIP entry ${name}`);
    entries.push({ name, data: body, flag, internalAttr: flag !== 0 && name.endsWith('.xml') ? 1 : 0 });
    pos = bodyEnd;
  }
  if (entries.length === 0) throw new Error('no ZIP archive found');
  return entries;
}

function readLe16(data: Uint8Array, offset: number): number {
  return data[offset] | (data[offset + 1] << 8);
}

function readLe32(data: Uint8Array, offset: number): number {
  return (data[offset] | (data[offset + 1] << 8) | (data[offset + 2] << 16) | (data[offset + 3] << 24)) >>> 0;
}

function writeLe16(view: DataView, offset: number, value: number): void {
  view.setUint16(offset, value & 0xffff, true);
}

function writeLe32(view: DataView, offset: number, value: number): void {
  view.setUint32(offset, value >>> 0, true);
}

export function buildZip(entries: ZipEntry[]): Uint8Array {
  const locals: Uint8Array[] = [];
  const centrals: Uint8Array[] = [];
  let offset = 0;
  for (const entry of entries) {
    const name = new TextEncoder().encode(entry.name);
    const level = entry.flag === 0x0000 ? 0 : entry.flag === 0x0002 ? 9 : 1;
    const compressed = deflateSync(entry.data, { level });
    const crc = crc32(entry.data);
    const local = new Uint8Array(30 + name.length + ZIP64_EXTRA.length + compressed.length);
    const view = new DataView(local.buffer);
    writeLe32(view, 0, 0x04034b50);
    writeLe16(view, 4, VERSION_NEEDED);
    writeLe16(view, 6, entry.flag);
    writeLe16(view, 8, 8);
    writeLe16(view, 10, 0);
    writeLe16(view, 12, DOS_DATE);
    writeLe32(view, 14, crc);
    writeLe32(view, 18, compressed.length);
    writeLe32(view, 22, entry.data.length);
    writeLe16(view, 26, name.length);
    writeLe16(view, 28, ZIP64_EXTRA.length);
    local.set(name, 30);
    local.set(ZIP64_EXTRA, 30 + name.length);
    local.set(compressed, 30 + name.length + ZIP64_EXTRA.length);
    locals.push(local);
    const central = new Uint8Array(46 + name.length);
    const cv = new DataView(central.buffer);
    writeLe32(cv, 0, 0x02014b50);
    writeLe16(cv, 4, 0);
    writeLe16(cv, 6, CENTRAL_VERSION_NEEDED);
    writeLe16(cv, 8, entry.flag);
    writeLe16(cv, 10, 8);
    writeLe16(cv, 12, 0);
    writeLe16(cv, 14, DOS_DATE);
    writeLe32(cv, 16, crc);
    writeLe32(cv, 20, compressed.length);
    writeLe32(cv, 24, entry.data.length);
    writeLe16(cv, 28, name.length);
    writeLe16(cv, 30, 0);
    writeLe16(cv, 32, 0);
    writeLe16(cv, 34, 0);
    writeLe16(cv, 36, entry.internalAttr);
    writeLe32(cv, 38, 0);
    writeLe32(cv, 42, offset);
    central.set(name, 46);
    centrals.push(central);
    offset += local.length;
  }
  const centralBytes = concat(centrals);
  const end = new Uint8Array(22);
  const ev = new DataView(end.buffer);
  writeLe32(ev, 0, 0x06054b50);
  writeLe16(ev, 4, 0);
  writeLe16(ev, 6, 0);
  writeLe16(ev, 8, entries.length);
  writeLe16(ev, 10, entries.length);
  writeLe32(ev, 12, centralBytes.length);
  writeLe32(ev, 16, offset);
  writeLe16(ev, 20, 0);
  return concat([...locals, centralBytes, end]);
}

// ── Integrity word ──────────────────────────────────────────────────────

const CRC_TABLE = (() => {
  const table = new Uint32Array(256);
  for (let index = 0; index < 256; index++) {
    let value = index;
    for (let bit = 0; bit < 8; bit++) value = value & 1 ? 0xedb88320 ^ (value >>> 1) : value >>> 1;
    table[index] = value >>> 0;
  }
  return table;
})();

/** Standard CRC-32 (reflected, pre/post-inverted), as zlib.crc32 computes. */
export function crc32(data: Uint8Array, start = 0): number {
  return (crcRegister(data, start ^ 0xffffffff) ^ 0xffffffff) >>> 0;
}

/** Reflected CRC-32 register after *data*, with no inversions - the
 * `_crc_register` helper the integrity-word derivation needs. */
export function crcRegister(data: Uint8Array, start = 0): number {
  let register = start >>> 0;
  for (const byte of data) register = (register >>> 8) ^ CRC_TABLE[(register ^ byte) & 0xff];
  return register >>> 0;
}

function rewindBit(register: number, bit: number): number {
  const flag = register >>> 31;
  const shifted = ((register ^ (flag ? 0xedb88320 : 0)) << 1) >>> 0;
  return ((shifted | (flag ^ bit)) >>> 0) >>> 0;
}

function rewindByte(register: number, byte: number): number {
  for (let shift = 7; shift >= 0; shift--) register = rewindBit(register, (byte >> shift) & 1);
  return register >>> 0;
}

function historyConstant(k: number): number {
  if (k < 1) throw new Error('k must be positive');
  if (k === 1) return 0xffffffff;
  let value = 0;
  let remaining = k - 2;
  while (remaining > 0) {
    value = rewindByte(rewindByte(rewindByte(rewindByte((value ^ 0xffffffff) >>> 0, 0), 0), 0), 0);
    remaining--;
  }
  return value >>> 0;
}

function metaSlotValue(meta: Uint8Array, k: number): number {
  return (historyConstant(k) ^ crcRegister(meta.subarray(meta.length - 4))) >>> 0;
}

/** The u32 integrity word for *entries*, in archive order.
 * Implemented from skpwright `SKP_FORMAT.md` §16.15's description and
 * verified against genuine SketchUp 2025 saves; no skpwright code is used. */
export function integrityWord(entries: ZipEntry[]): number {
  const metaEntry = entries.find((entry) => entry.name === 'meta/meta.dat');
  if (!metaEntry) throw new Error('archive has no meta/meta.dat');
  const metaIndex = entries.indexOf(metaEntry);
  const n = entries.length;
  const words = entries.map((entry, index) => {
    const crc = crc32(entry.data);
    return index % 2 === 0 ? crc : (crc - 42) >>> 0;
  });
  words[metaIndex] = metaSlotValue(metaEntry.data, Math.ceil(n / 2));
  const ordered = [...words.filter((_, i) => i % 2 === 0), ...words.filter((_, i) => i % 2 === 1)];
  const table = new Uint8Array(ordered.length * 4);
  const view = new DataView(table.buffer);
  ordered.forEach((word, index) => view.setUint32(index * 4, word >>> 0, true));
  return crc32(concat([metaEntry.data.subarray(0, metaEntry.data.length - 4), table]));
}

export function headerOffset(data: Uint8Array): number {
  const start = findSequence(data, [0x56, 0x46, 0x46, 0x08, 0x00, 0x01, 0x00]);
  if (start < 0) throw new Error('not a VFF container (no 13-byte header)');
  return start;
}

export function storedIntegrityWord(data: Uint8Array): number {
  return readLe32(data, headerOffset(data) + 9);
}

export function verifyIntegrity(data: Uint8Array): boolean {
  const offset = zipOffset(data);
  return storedIntegrityWord(data) === integrityWord(readZip(data, offset));
}

export function buildSkp(header: Uint8Array, entries: ZipEntry[]): Uint8Array {
  if (!entries.length || entries[entries.length - 1].name !== 'model.dat') throw new Error('model.dat must be the last archive entry');
  const word = integrityWord(entries);
  const out = new Uint8Array(header.length);
  out.set(header);
  new DataView(out.buffer).setUint32(headerOffset(out) + 9, word >>> 0, true);
  const zip = buildZip(entries);
  const combined = new Uint8Array(out.length + zip.length);
  combined.set(out, 0);
  combined.set(zip, out.length);
  return combined;
}

export function entriesOf(data: Uint8Array): ZipEntry[] {
  return readZip(data, zipOffset(data));
}
