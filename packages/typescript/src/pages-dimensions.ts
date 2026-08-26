/**
 * VFF (2021+) scenes ("pages") and linear dimensions. Ported from Python's
 * _core.py (PR #190) - see that module's _scan_vertex_positions /
 * _scan_instance_transforms / _parse_dimensions / _find_page_node /
 * _parse_pages for the byte-format details this file mirrors.
 */
import { TlvNode, readU32, readF64, parseVarInt } from './parser';
import { findChildTag } from './geometry';
import { multiplyMatrices, transformPoint } from './transforms';

export interface RawPage {
  name: string;
  eye: [number, number, number] | null;
  target: [number, number, number] | null;
  up: [number, number, number] | null;
  fov: number;
  parallel: boolean;
  orthoHeight: number;
  hiddenLayerIds: number[];
}

export interface RawDimension {
  a: [number, number, number];
  b: [number, number, number];
  offset: number;
  planeX: [number, number, number] | null;
  normal: [number, number, number] | null;
  text: string;
}

// ── flat TLV, integer tags ──────────────────────────────────────────────
// A second, deliberately separate flat-TLV reader from the string byte-order
// tag convention the main tree uses (TlvNode.tag, e.g. "C409"): the
// sub-records this feature reads (5208, 520A, 53FC, 5BCD, ...) are most
// directly and safely ported from Python's own _tlv_items/_tlv_find (which
// read the tag as a little-endian uint16) by keeping the SAME integer
// convention here, copying Python's numeric constants byte-for-byte rather
// than hand-converting each one to the swapped string form.

interface FlatTlvItem {
  tag: number;
  payload: Uint8Array;
}

function tlvItemsInt(buf: Uint8Array | null): FlatTlvItem[] | null {
  if (buf === null) return null;
  const items: FlatTlvItem[] = [];
  let off = 0;
  const n = buf.length;
  while (off < n) {
    if (off + 6 > n) return null;
    const tag = buf[off] | (buf[off + 1] << 8);
    const ln = readU32(buf, off + 2);
    if (tag === 0 || off + 6 + ln > n) return null;
    items.push({ tag, payload: buf.subarray(off + 6, off + 6 + ln) });
    off += 6 + ln;
  }
  return items;
}

function tlvFindInt(items: FlatTlvItem[] | null, tag: number): Uint8Array | null {
  if (items === null) return null;
  for (const it of items) {
    if (it.tag === tag) return it.payload;
  }
  return null;
}

function toHex(data: Uint8Array): string {
  let s = '';
  for (let i = 0; i < data.length; i++) {
    const h = data[i].toString(16).toUpperCase();
    s += h.length === 1 ? '0' + h : h;
  }
  return s;
}

function stripDe05(p: Uint8Array): Uint8Array {
  if (p.length >= 2 && p[0] === 0xde && p[1] === 0x05) {
    const idlen = readU32(p, 2);
    return p.subarray(6, 6 + idlen);
  }
  return p;
}

function findBytes(data: Uint8Array, needle: Uint8Array, start = 0): number {
  const nlen = needle.length;
  const limit = data.length - nlen;
  outer: for (let i = start; i <= limit; i++) {
    for (let j = 0; j < nlen; j++) {
      if (data[i + j] !== needle[j]) continue outer;
    }
    return i;
  }
  return -1;
}

/**
 * Accumulate every vertex's persistent id (hex) -> (x, y, z) inches. A
 * vertex is a "C409" record: "DC05" holds its persistent id (the "DE05"
 * var-int payload), "C509" its 3xf64 position. Dimension connection points
 * reference geometry by this id. Called once per top-level record -
 * parseSkp streams the TLV tree and never holds it whole.
 */
export function scanVertexPositions(top: TlvNode, id2pos: Map<string, [number, number, number]>): void {
  function walk(nodes: TlvNode[]) {
    for (const el of nodes) {
      if (el.tag === 'C409') {
        const dc05 = findChildTag(el.children, 'DC05');
        const c509 = findChildTag(el.children, 'C509');
        if (dc05 && c509 && c509.payload.length === 24) {
          const idb = stripDe05(dc05.payload);
          id2pos.set(toHex(idb), [
            readF64(c509.payload, 0),
            readF64(c509.payload, 8),
            readF64(c509.payload, 16),
          ]);
        }
      }
      if (el.children.length > 0) walk(el.children);
    }
  }
  walk([top]);
}

/**
 * Accumulate each instance's persistent id (hex) -> its WORLD transform (a
 * 13-number matrix), walking the instance tree and composing parent x local
 * at every "6419". Per top-level record, like scanVertexPositions - an
 * instance chain never crosses top-level records.
 *
 * A dimension connects to geometry INSIDE a placed component; its
 * connection reference names the vertex AND the instance holding it. The
 * vertex position is definition-local, so it must be lifted to world by the
 * instance's transform for the dimension to land where the author drew it.
 */
export function scanInstanceTransforms(top: TlvNode, world: Map<string, number[] | null>): void {
  function walk(nodes: TlvNode[], parent: number[] | null) {
    for (const el of nodes) {
      if (el.tag === '6419') {
        const d007 = findChildTag(el.children, 'D007');
        const dc05 = d007 ? findChildTag(d007.children, 'DC05') : null;
        const iid = dc05 ? toHex(stripDe05(dc05.payload)) : null;

        const m = findChildTag(el.children, '6619');
        let mat: number[] | null = null;
        if (m && m.payload.length === 104) {
          mat = [];
          for (let i = 0; i < 13; i++) mat.push(readF64(m.payload, i * 8));
        }
        const here: number[] | null = mat !== null ? multiplyMatrices(parent as number[], mat) : parent;
        if (iid !== null) world.set(iid, here);
        walk(el.children, here);
      } else if (el.children.length > 0) {
        walk(el.children, parent);
      }
    }
  }
  walk([top], null);
}

/**
 * Linear dimensions (SketchUp's Dimension tool).
 *
 * A dimension entity is a "5BCC" record (raw bytes cc 5b) holding:
 *
 * - 5BCD / 5BCE - the two connection points. Each wraps a 5208 whose 5209
 *   is the connection TYPE (1 = a free explicit point in 520A, already
 *   world space; 2 = connected to geometry, 520A is zero and 520B -> 53FC
 *   names the target: 53FD = the vertex by persistent id, 53FE = a
 *   length-prefixed persistent id of the INSTANCE holding it - the vertex
 *   position is definition-local, so it is lifted to world by that
 *   instance's transform).
 * - 5BCF - the dimension plane's x-axis; 5BD0 - its normal.
 * - 5BD2 - the offset distance (inches): how far the dimension line sits
 *   from the measured segment, along the in-plane perpendicular.
 *
 * The measured value is auto-computed from the two points (no cached text
 * on the samples seen), so callers format it themselves. Endpoints come out
 * in WORLD space (inches). A connection point that cannot be resolved drops
 * the whole dimension (fail-safe).
 */
export function parseDimensions(
  modelData: Uint8Array,
  id2pos: Map<string, [number, number, number]>,
  instWorld: Map<string, number[] | null>
): RawDimension[] {
  const dims: RawDimension[] = [];
  const needle = new Uint8Array([0xcc, 0x5b]);
  let i = 0;
  const n = modelData.length;

  function point(blockPayload: Uint8Array | null): [number, number, number] | null {
    if (blockPayload === null) return null;
    const blk = tlvFindInt(tlvItemsInt(blockPayload), 0x5208);
    if (blk === null) return null;
    const sub = tlvItemsInt(blk);
    const typB = tlvFindInt(sub, 0x5209);
    const typ = typB !== null && typB.length === 4 ? readU32(typB, 0) : null;
    if (typ === 1) {
      const pos = tlvFindInt(sub, 0x520a);
      if (pos === null || pos.length !== 24) return null;
      return [readF64(pos, 0), readF64(pos, 8), readF64(pos, 16)];
    }
    // type 2: resolve the geometry reference (vertex + instance).
    const refB = tlvFindInt(sub, 0x520b);
    const f53fc = refB !== null ? tlvFindInt(tlvItemsInt(refB), 0x53fc) : null;
    const fi = f53fc !== null ? tlvItemsInt(f53fc) : null;
    const vid = tlvFindInt(fi, 0x53fd);
    const iid = tlvFindInt(fi, 0x53fe);
    if (vid === null) return null;
    const local = id2pos.get(toHex(vid));
    if (local === undefined) return null;
    if (iid !== null && iid.length >= 1 && iid[0] > 0 && 1 + iid[0] <= iid.length) {
      const idBytes = iid.subarray(1, 1 + iid[0]);
      const w = instWorld.get(toHex(idBytes));
      if (w) {
        return transformPoint(w, local);
      }
    }
    return local; // model-root vertex - already world
  }

  while (true) {
    const j = findBytes(modelData, needle, i);
    if (j < 0) break;
    i = j + 1;
    if (j + 6 > n) continue;
    const ln = readU32(modelData, j + 2);
    if (ln < 40 || j + 6 + ln > n) continue;
    const bodyBytes = modelData.subarray(j + 6, j + 6 + ln);
    const body = tlvItemsInt(bodyBytes);
    if (body === null) continue;
    let has5Bcd = false;
    let has5Bce = false;
    for (const it of body) {
      if (it.tag === 0x5bcd) has5Bcd = true;
      if (it.tag === 0x5bce) has5Bce = true;
    }
    if (!has5Bcd || !has5Bce) continue;

    const a = point(tlvFindInt(body, 0x5bcd));
    const b = point(tlvFindInt(body, 0x5bce));
    if (a === null || b === null) continue;

    const xaxisB = tlvFindInt(body, 0x5bcf);
    const normalB = tlvFindInt(body, 0x5bd0);
    const offB = tlvFindInt(body, 0x5bd2);

    dims.push({
      a,
      b,
      planeX: xaxisB !== null && xaxisB.length === 24
        ? [readF64(xaxisB, 0), readF64(xaxisB, 8), readF64(xaxisB, 16)]
        : null,
      normal: normalB !== null && normalB.length === 24
        ? [readF64(normalB, 0), readF64(normalB, 8), readF64(normalB, 16)]
        : null,
      offset: offB !== null && offB.length === 8 ? readF64(offB, 0) : 0.0,
      text: '',
    });
  }
  return dims;
}

/**
 * Return the "0702" scenes node inside top's subtree, or null. Called per
 * top-level record; retaining the (small) 0702 subtree is the only thing
 * kept alive past the streaming loop.
 */
export function findPageNode(top: TlvNode): TlvNode | null {
  return findChildTag([top], '0702');
}

/**
 * Scenes ("pages"). The 0702 node's payload nests 6D60 > 6D61 > one 7148
 * record per page:
 *
 * - 6F54 > 6F55 - page name (UTF-8)
 * - 714A > 34BC - camera: 34BD eye, 34BE target, 34BF up (3xf64, inches),
 *   34C4 field of view (degrees), 34C2 u8 = PERSPECTIVE flag (00 = parallel
 *   projection - calibrated against the bundled scene thumbnails: parallel
 *   plans/elevations carry 00 and their 34C3 visible height matches the
 *   thumbnail framing exactly, while perspective scenes carry 01 with a
 *   stale 34C3), 34C3 f64 = visible height when parallel (inches)
 * - 7150 - layers hidden in this page: (u8 length, var-int layer id) runs
 */
export function parsePages(node: TlvNode | null): RawPage[] {
  const pages: RawPage[] = [];
  if (node === null) return pages;

  function vec3(p: Uint8Array | null): [number, number, number] | null {
    return p !== null && p.length === 24 ? [readF64(p, 0), readF64(p, 8), readF64(p, 16)] : null;
  }

  const t60Items = tlvItemsInt(node.payload);
  if (t60Items === null) return pages;
  for (const it60 of t60Items) {
    if (it60.tag !== 0x6d60) continue;
    const t61Items = tlvItemsInt(it60.payload);
    if (t61Items === null) continue;
    for (const it61 of t61Items) {
      if (it61.tag !== 0x6d61) continue;
      const t48Items = tlvItemsInt(it61.payload);
      if (t48Items === null) continue;
      for (const it48 of t48Items) {
        if (it48.tag !== 0x7148) continue;
        const items = tlvItemsInt(it48.payload);
        if (items === null) continue;

        const page: RawPage = {
          name: '',
          eye: null,
          target: null,
          up: null,
          fov: 35.0,
          parallel: false,
          orthoHeight: 0.0,
          hiddenLayerIds: [],
        };

        const head = tlvItemsInt(tlvFindInt(items, 0x6f54));
        const name = tlvFindInt(head, 0x6f55);
        if (name !== null && name.length > 0) {
          page.name = new TextDecoder('utf-8').decode(name);
        }

        const camWrap = tlvItemsInt(tlvFindInt(items, 0x714a));
        const cam = camWrap !== null ? tlvItemsInt(tlvFindInt(camWrap, 0x34bc)) : null;
        if (cam !== null) {
          page.eye = vec3(tlvFindInt(cam, 0x34bd));
          page.target = vec3(tlvFindInt(cam, 0x34be));
          page.up = vec3(tlvFindInt(cam, 0x34bf));
          const fov = tlvFindInt(cam, 0x34c4);
          if (fov !== null && fov.length === 8) page.fov = readF64(fov, 0);
          const flag = tlvFindInt(cam, 0x34c2);
          page.parallel = flag !== null && flag.length > 0 && flag[0] === 0;
          const height = tlvFindInt(cam, 0x34c3);
          if (height !== null && height.length === 8) page.orthoHeight = readF64(height, 0);
        }

        const hidden = tlvFindInt(items, 0x7150);
        let off = 0;
        while (hidden !== null && off + 1 <= hidden.length) {
          const ln = hidden[off];
          if (ln === 0 || off + 1 + ln > hidden.length) break;
          page.hiddenLayerIds.push(parseVarInt(hidden, off + 1, ln));
          off += 1 + ln;
        }

        if (page.eye !== null && page.target !== null) pages.push(page);
      }
    }
  }
  return pages;
}
