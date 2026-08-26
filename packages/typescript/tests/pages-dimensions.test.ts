import { describe, it, expect } from 'vitest';
import * as fs from 'fs';
import * as path from 'path';
import { parseSkp } from '../src/index';
import { parsePages, parseDimensions } from '../src/pages-dimensions';
import { TlvNode } from '../src/parser';

/**
 * VFF scenes ("pages") and linear dimensions - ported from Python's
 * test_pages_dimensions.py (PR #190).
 *
 * Dimensions are exercised against the repository's own Untitled.skp
 * fixture (drawn in SketchUp 2025, it carries 13 linear dimensions); scenes
 * have no fixture yet, so their parser is exercised on a synthetic "0702"
 * record byte-for-byte shaped like the real ones (the layout was decoded
 * from production survey files and calibrated against the scene thumbnails
 * SketchUp embeds in the .skp itself).
 */

// ── helpers: build TLV runs in the flat (u16-LE tag, u32 len) form ────────

function tlv(tag: number, payload: Uint8Array): Uint8Array {
  const result = new Uint8Array(6 + payload.length);
  result[0] = tag & 0xff;
  result[1] = (tag >> 8) & 0xff;
  const len = payload.length;
  result[2] = len & 0xff;
  result[3] = (len >> 8) & 0xff;
  result[4] = (len >> 16) & 0xff;
  result[5] = (len >> 24) & 0xff;
  result.set(payload, 6);
  return result;
}

function f64le(v: number): Uint8Array {
  const buf = new Uint8Array(8);
  new DataView(buf.buffer).setFloat64(0, v, true);
  return buf;
}

function u32le(v: number): Uint8Array {
  const buf = new Uint8Array(4);
  new DataView(buf.buffer).setUint32(0, v, true);
  return buf;
}

function vec3(x: number, y: number, z: number): Uint8Array {
  return concat(f64le(x), f64le(y), f64le(z));
}

function concat(...parts: Uint8Array[]): Uint8Array {
  const total = parts.reduce((s, p) => s + p.length, 0);
  const result = new Uint8Array(total);
  let off = 0;
  for (const p of parts) {
    result.set(p, off);
    off += p.length;
  }
  return result;
}

function toHex(data: Uint8Array): string {
  let s = '';
  for (let i = 0; i < data.length; i++) {
    const h = data[i].toString(16).toUpperCase();
    s += h.length === 1 ? '0' + h : h;
  }
  return s;
}

const FIXTURES = path.join(__dirname, 'fixtures');

// ── linear dimensions ──────────────────────────────────────────────────

describe('linear dimensions', () => {
  it('the Untitled.skp fixture has 13 dimensions', () => {
    const buf = fs.readFileSync(path.join(FIXTURES, 'Untitled.skp'));
    const arrayBuffer = buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength);
    const model = parseSkp(arrayBuffer);
    expect(model.dimensions.length).toBe(13);
    for (const d of model.dimensions) {
      expect(d.a).not.toBeNull();
      expect(d.b).not.toBeNull();
      const [ax, ay, az] = d.a!;
      const [bx, by, bz] = d.b!;
      const dist = Math.hypot(ax - bx, ay - by, az - bz);
      expect(dist).toBeGreaterThan(0.0); // a real measured segment
      expect(d.normal).not.toBeNull();
      expect(d.planeX).not.toBeNull();
    }
  });

  it('parses two free (world-space) connection points', () => {
    // A 5BCC record with two type-1 (free, world-space) connection points.
    function pointBlock(wrapTag: number, x: number, y: number, z: number): Uint8Array {
      const inner = concat(tlv(0x5209, u32le(1)), tlv(0x520a, vec3(x, y, z)));
      return tlv(wrapTag, tlv(0x5208, inner));
    }

    const body = concat(
      pointBlock(0x5bcd, 0.0, 0.0, 0.0),
      pointBlock(0x5bce, 100.0, 0.0, 0.0),
      tlv(0x5bcf, vec3(1.0, 0.0, 0.0)), // plane x-axis
      tlv(0x5bd0, vec3(0.0, 0.0, 1.0)), // plane normal
      tlv(0x5bd2, f64le(15.5)) // offset
    );
    const blob = concat(new Uint8Array(8), tlv(0x5bcc, body), new Uint8Array(8));

    const dims = parseDimensions(blob, new Map(), new Map());
    expect(dims.length).toBe(1);
    const d = dims[0];
    expect(d.a).toEqual([0.0, 0.0, 0.0]);
    expect(d.b).toEqual([100.0, 0.0, 0.0]);
    expect(d.offset).toBe(15.5);
    expect(d.planeX).toEqual([1.0, 0.0, 0.0]);
    expect(d.normal).toEqual([0.0, 0.0, 1.0]);
  });

  it('resolves a connected point through its instance transform, and drops an unresolvable one', () => {
    // A type-2 connection (vertex id + instance id): the vertex position is
    // definition-local and must be lifted to world by the instance's
    // transform. An unresolvable reference drops the dimension (fail-safe).
    const vid = new Uint8Array([0xaa, 0xbb, 0x01]);
    const iid = new Uint8Array([0xcc, 0xdd, 0x02]);

    function connected(wrapTag: number): Uint8Array {
      const idLenPrefixed = concat(new Uint8Array([iid.length]), iid);
      const refTlv = tlv(0x53fc, concat(tlv(0x53fd, vid), tlv(0x53fe, idLenPrefixed)));
      const inner = concat(tlv(0x5209, u32le(2)), tlv(0x520b, refTlv));
      return tlv(wrapTag, tlv(0x5208, inner));
    }

    function free(wrapTag: number): Uint8Array {
      const inner = concat(tlv(0x5209, u32le(1)), tlv(0x520a, vec3(0.0, 0.0, 0.0)));
      return tlv(wrapTag, tlv(0x5208, inner));
    }

    const body = concat(connected(0x5bcd), free(0x5bce), tlv(0x5bd2, f64le(0.0)));
    const blob = tlv(0x5bcc, body);

    // Identity-ish transform that translates by (10, 20, 30).
    const world = [1, 0, 0, 0, 1, 0, 0, 0, 1, 10.0, 20.0, 30.0, 1.0];
    const id2pos = new Map<string, [number, number, number]>([[toHex(vid), [1.0, 2.0, 3.0]]]);
    const instWorld = new Map<string, number[] | null>([[toHex(iid), world]]);

    const dims = parseDimensions(blob, id2pos, instWorld);
    expect(dims.length).toBe(1);
    expect(dims[0].a).toEqual([11.0, 22.0, 33.0]); // local + translation

    // Same record, but the vertex id is unknown: the dimension is dropped.
    const emptyDims = parseDimensions(blob, new Map(), new Map());
    expect(emptyDims.length).toBe(0);
  });
});

// ── scenes (pages) ─────────────────────────────────────────────────────

function pageRecord(name: string, parallel: boolean, hiddenIds: number[] = []): Uint8Array {
  const cam = concat(
    tlv(0x34bd, vec3(100.0, -200.0, 50.0)), // eye
    tlv(0x34be, vec3(0.0, 0.0, 0.0)), // target
    tlv(0x34bf, vec3(0.0, 0.0, 1.0)), // up
    tlv(0x34c4, f64le(35.0)), // fov
    tlv(0x34c2, new Uint8Array([parallel ? 0 : 1])),
    tlv(0x34c3, f64le(240.0)) // ortho height
  );
  const hiddenParts = hiddenIds.map((i) => new Uint8Array([1, i]));
  const hidden = concat(...hiddenParts);
  const body = concat(
    tlv(0x6f54, tlv(0x6f55, new TextEncoder().encode(name))),
    tlv(0x714a, tlv(0x34bc, cam)),
    tlv(0x7150, hidden)
  );
  return tlv(0x7148, body);
}

describe('scenes (pages)', () => {
  it('parses a synthetic 0702 record', () => {
    const payload = tlv(
      0x6d60,
      tlv(0x6d61, concat(pageRecord('Planta', true, [2]), pageRecord('Vista 3D', false)))
    );
    const node: TlvNode = { offset: 0, tag: '0702', size: payload.length, children: [], payload };
    const pages = parsePages(node);

    expect(pages.map((p) => p.name)).toEqual(['Planta', 'Vista 3D']);
    const planta = pages[0];
    expect(planta.parallel).toBe(true);
    expect(planta.orthoHeight).toBe(240.0);
    expect(planta.eye).toEqual([100.0, -200.0, 50.0]);
    expect(planta.up).toEqual([0.0, 0.0, 1.0]);
    expect(planta.hiddenLayerIds).toEqual([2]);
    expect(pages[1].parallel).toBe(false);
    expect(pages[1].fov).toBe(35.0);
  });

  it('is empty when absent', () => {
    expect(parsePages(null)).toEqual([]);
  });

  it('a file with no pages parses with an empty pages list', () => {
    const buf = fs.readFileSync(path.join(FIXTURES, 'SU_File.skp'));
    const arrayBuffer = buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength);
    const model = parseSkp(arrayBuffer);
    expect(model.pages).toEqual([]);
  });
});
