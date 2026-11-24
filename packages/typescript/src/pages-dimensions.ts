/**
 * VFF (2021+) scenes ("pages") and linear dimensions. Ported from Python's
 * _core.py (PR #190) - see that module's _scan_vertex_positions /
 * _scan_instance_transforms / _parse_dimensions / _find_page_node /
 * _parse_pages for the byte-format details this file mirrors.
 */
import { TlvNode, readU32, readF64, parseVarInt } from './parser';
import { findChildTag, GeometryBuilder, ParsedDefinition } from './geometry';
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
  hiddenEntityIds: number[];
}

export interface RawDimension {
  a: [number, number, number];
  b: [number, number, number];
  offset: number;
  planeX: [number, number, number] | null;
  normal: [number, number, number] | null;
  offsetVector?: [number, number, number] | null;
  layer?: string;
  hidden?: boolean;
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
 * - 5BCF - the dimension plane's NORMAL (SketchUp's SUDimensionLinearGetNormal
 *   equals it); 5BD0 - its X axis. Kept in the planeX and normal fields, whose
 *   names say the opposite.
 * - 5BD2 - the offset distance (inches): how far the dimension line sits
 *   from the measured segment, along the in-plane perpendicular.
 *
 * The measured value is auto-computed from the two points (no cached text
 * on the samples seen), so callers format it themselves. Endpoints come out
 * in WORLD space (inches). A connection point that cannot be resolved drops
 * the whole dimension (fail-safe).
 */
function lengthPrefixedIds(payload: Uint8Array): string[] {
  const ids: string[] = [];
  let off = 0;
  while (off < payload.length) {
    const ln = payload[off];
    if (ln === 0 || off + 1 + ln > payload.length) break;
    ids.push(toHex(payload.subarray(off + 1, off + 1 + ln)));
    off += 1 + ln;
  }
  return ids;
}

export const MAX_EXPANDED_INSTANCES = 50_000_000;

type PathResolverFn = (path: string[]) => number[] | null;

function mapPathResolver(instWorld: Map<string, number[] | null>): PathResolverFn {
  return (path: string[]) => {
    for (let k = path.length - 1; k >= 0; k--) {
      const w = instWorld.get(path[k]);
      if (w && w.length > 0) {
        return w;
      }
    }
    return null;
  };
}

function dimensionConnectionPoint(
  blockPayload: Uint8Array | null,
  id2pos: Map<string, [number, number, number]>,
  resolvePath: PathResolverFn
): [number, number, number] | null {
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
  const refB = tlvFindInt(sub, 0x520b);
  const f53fc = refB !== null ? tlvFindInt(tlvItemsInt(refB), 0x53fc) : null;
  const fi = f53fc !== null ? tlvItemsInt(f53fc) : null;
  const vid = tlvFindInt(fi, 0x53fd);
  const iid = tlvFindInt(fi, 0x53fe);
  if (vid === null) return null;
  const local = id2pos.get(toHex(vid));
  if (local === undefined) return null;
  if (iid !== null && iid.length > 0) {
    const path = lengthPrefixedIds(iid);
    if (path.length === 0) path.push(toHex(iid));
    const w = resolvePath(path);
    if (w && w.length > 0) {
      return transformPoint(w, local);
    }
  }
  return local;
}

function flatTlvHasFields(
  buf: Uint8Array,
  begin: number,
  end: number,
  first = 0x5bcd,
  second = 0x5bce
): boolean {
  let hasFirst = false;
  let hasSecond = false;
  let off = begin;
  while (off < end) {
    if (off + 6 > end) return false;
    const tag = buf[off] | (buf[off + 1] << 8);
    const ln = readU32(buf, off + 2);
    if (tag === 0 || ln > end - off - 6) return false;
    if (tag === first) hasFirst = true;
    if (tag === second) hasSecond = true;
    off += 6 + ln;
  }
  return hasFirst && hasSecond;
}

function parseOne5Bcc(
  modelData: Uint8Array,
  j: number,
  id2pos: Map<string, [number, number, number]>,
  resolvePath: PathResolverFn
): RawDimension | null {
  const n = modelData.length;
  if (j + 6 > n) return null;
  const ln = readU32(modelData, j + 2);
  if (ln < 40 || j + 6 + ln > n) return null;
  if (!flatTlvHasFields(modelData, j + 6, j + 6 + ln, 0x5bcd, 0x5bce)) return null;
  const body = tlvItemsInt(modelData.subarray(j + 6, j + 6 + ln));
  if (body === null || body.length === 0) return null;
  let has5Bcd = false;
  let has5Bce = false;
  for (const it of body) {
    if (it.tag === 0x5bcd) has5Bcd = true;
    if (it.tag === 0x5bce) has5Bce = true;
  }
  if (!has5Bcd || !has5Bce) return null;

  const a = dimensionConnectionPoint(tlvFindInt(body, 0x5bcd), id2pos, resolvePath);
  const b = dimensionConnectionPoint(tlvFindInt(body, 0x5bce), id2pos, resolvePath);
  if (a === null || b === null) return null;

  let layer = '';
  let hidden = false;
  const baseB = tlvFindInt(body, 0x59d8);
  if (baseB !== null) {
    const drawB = tlvFindInt(tlvItemsInt(baseB), 0x07d0);
    if (drawB !== null) {
      const props = tlvItemsInt(drawB);
      const layerB = tlvFindInt(props, 0x07d2);
      if (layerB !== null && layerB.length > 0) {
        layer = String(parseVarInt(layerB, 0, layerB.length));
      }
      const flagsB = tlvFindInt(props, 0x07d3);
      if (flagsB !== null && flagsB.length > 0) {
        hidden = (flagsB[0] & 0x01) !== 0;
      }
    }
  }

  const xaxisB = tlvFindInt(body, 0x5bcf);
  const normalB = tlvFindInt(body, 0x5bd0);
  const axisB = tlvFindInt(body, 0x5bd1);
  const offB = tlvFindInt(body, 0x5bd2);

  const planeX: [number, number, number] | null =
    xaxisB !== null && xaxisB.length === 24
      ? [readF64(xaxisB, 0), readF64(xaxisB, 8), readF64(xaxisB, 16)]
      : null;
  const normal: [number, number, number] | null =
    normalB !== null && normalB.length === 24
      ? [readF64(normalB, 0), readF64(normalB, 8), readF64(normalB, 16)]
      : null;
  const offset = offB !== null && offB.length === 8 ? readF64(offB, 0) : 0.0;
  let offsetVector: [number, number, number] | null = null;
  if (axisB !== null && axisB.length === 4 && planeX !== null && normal !== null) {
    const axis = readU32(axisB, 0);
    const pNorm = planeX;
    const x = normal;
    if (axis === 1) {
      offsetVector = [
        (pNorm[1] * x[2] - pNorm[2] * x[1]) * offset + 0,
        (pNorm[2] * x[0] - pNorm[0] * x[2]) * offset + 0,
        (pNorm[0] * x[1] - pNorm[1] * x[0]) * offset + 0,
      ];
    } else if (axis === 2) {
      offsetVector = [
        -x[0] * offset + 0,
        -x[1] * offset + 0,
        -x[2] * offset + 0,
      ];
    }
  }

  return {
    a,
    b,
    planeX,
    normal,
    offset,
    offsetVector,
    layer,
    hidden,
    text: '',
  };
}

function parseVffText(
  modelData: Uint8Array,
  j: number,
  id2pos: Map<string, [number, number, number]>,
  resolvePath: PathResolverFn
): GeometryBuilder['texts'][number] | null {
  const size = readU32(modelData, j + 2);
  const fields = tlvItemsInt(modelData.subarray(j + 6, j + 6 + size));
  if (fields === null) return null;
  const valueB = tlvFindInt(fields, 0x55f1);
  const connB = tlvFindInt(fields, 0x55f4);
  if (valueB === null || connB === null) return null;
  const decoder = new TextDecoder('utf-8');
  const textStr = decoder.decode(valueB);
  const point = dimensionConnectionPoint(connB, id2pos, resolvePath);
  const lpB = tlvFindInt(fields, 0x55f5);
  const labelPoint: [number, number, number] | null =
    lpB !== null && lpB.length === 24
      ? [readF64(lpB, 0), readF64(lpB, 8), readF64(lpB, 16)]
      : null;
  const xB = tlvFindInt(fields, 0x55f2);
  const yB = tlvFindInt(fields, 0x55f3);
  const screenPosition: [number, number] | null =
    xB !== null && yB !== null && xB.length === 8 && yB.length === 8
      ? [readF64(xB, 0), readF64(yB, 0)]
      : null;
  const ltB = tlvFindInt(fields, 0x55fd);
  const leaderType = ltB !== null && ltB.length === 4 ? readU32(ltB, 0) : 2;
  const atB = tlvFindInt(fields, 0x55fa);
  const arrowType = atB !== null && atB.length === 4 ? readU32(atB, 0) : 3;
  const dlB = tlvFindInt(fields, 0x55fc);
  const displayLeader = dlB !== null && dlB.length === 1 ? dlB[0] !== 0 : true;
  let layer = '';
  let hidden = false;
  const drawB = tlvFindInt(fields, 0x07d0);
  if (drawB !== null) {
    const props = tlvItemsInt(drawB);
    const layerB = tlvFindInt(props, 0x07d2);
    if (layerB !== null && layerB.length > 0) {
      layer = String(parseVarInt(layerB, 0, layerB.length));
    }
    const flagsB = tlvFindInt(props, 0x07d3);
    if (flagsB !== null && flagsB.length > 0) {
      hidden = (flagsB[0] & 0x01) !== 0;
    }
  }
  return {
    text: textStr,
    hidden,
    layer,
    point,
    labelPoint,
    screenPosition,
    leaderType,
    arrowType,
    displayLeader,
  };
}

interface TlvHeaderSpan {
  offset: number;
  size: number;
}

function tlvHeadersRange(md: Uint8Array, start: number, end: number): TlvHeaderSpan[] {
  const out: TlvHeaderSpan[] = [];
  for (let p = start; p + 6 <= end; ) {
    const n = readU32(md, p + 2);
    if (n > end - p - 6) break;
    out.push({ offset: p, size: n });
    p += 6 + n;
  }
  return out;
}

function tagHexAt(md: Uint8Array, off: number): string {
  const a = md[off].toString(16).toUpperCase().padStart(2, '0');
  const b = md[off + 1].toString(16).toUpperCase().padStart(2, '0');
  return a + b;
}

const DEF_ID_CONTAINERS = new Set(['8813', 'D007', '8913', '8A13', '8B13', '8C13', '8D13']);

function entityIdFromTlvSpan(md: Uint8Array, rec: number, recEnd: number, depth = 0): number | null {
  if (depth > 24 || rec + 6 > recEnd) return null;
  const kids = tlvHeadersRange(md, rec + 6, recEnd);
  for (const k of kids) {
    const t = tagHexAt(md, k.offset);
    try {
      if (t === 'DE05' && k.size > 0 && k.size <= 8) {
        return parseVarInt(md, k.offset + 6, k.size);
      }
      if (t === 'DC05' && k.size >= 6 && md[k.offset + 6] === 0xde && md[k.offset + 7] === 0x05) {
        const z = readU32(md, k.offset + 8);
        if (z > 0 && z <= 8 && z <= k.size - 6) {
          return parseVarInt(md, k.offset + 12, z);
        }
      }
    } catch {
      continue;
    }
  }
  for (const k of kids) {
    const t = tagHexAt(md, k.offset);
    if (!DEF_ID_CONTAINERS.has(t)) continue;
    const id = entityIdFromTlvSpan(md, k.offset, k.offset + 6 + k.size, depth + 1);
    if (id !== null) return id;
  }
  return null;
}

interface DefSpan {
  begin: number;
  end: number;
  id: number;
}

function scanDefinitionSpans(md: Uint8Array, defs: Map<number | string, ParsedDefinition>): DefSpan[] {
  const spans: DefSpan[] = [];
  if (md.length < 6) return spans;
  let hs = tlvHeadersRange(md, 0, md.length);
  if (hs.length === 1 && md[hs[0].offset] === 0xf4 && md[hs[0].offset + 1] === 0x01) {
    hs = tlvHeadersRange(md, hs[0].offset + 6, hs[0].offset + 6 + hs[0].size);
  }
  for (const h of hs) {
    if (tagHexAt(md, h.offset) !== 'F901') continue;
    let level = tlvHeadersRange(md, h.offset + 6, h.offset + 6 + h.size);
    let ok = true;
    for (const expected of ['7017', '7117']) {
      const match = level.find((rec) => tagHexAt(md, rec.offset) === expected);
      if (!match) {
        ok = false;
        break;
      }
      level = tlvHeadersRange(md, match.offset + 6, match.offset + 6 + match.size);
    }
    if (!ok) continue;
    for (const rec of level) {
      if (tagHexAt(md, rec.offset) !== '7C15') continue;
      const id = entityIdFromTlvSpan(md, rec.offset, rec.offset + 6 + rec.size);
      if (id === null || !defs.has(id)) continue;
      spans.push({ begin: rec.offset, end: rec.offset + 6 + rec.size, id });
    }
  }
  return spans;
}

function owningDefinition(spans: DefSpan[], off: number): number | null {
  let best: number | null = null;
  let bestLen = Infinity;
  for (const s of spans) {
    if (off < s.begin || off >= s.end) continue;
    const len = s.end - s.begin;
    if (len < bestLen) {
      bestLen = len;
      best = s.id;
    }
  }
  return best;
}

function tlvHasNonEmptyTag(buf: Uint8Array, begin: number, end: number, tag: number, depth = 0): boolean {
  if (depth > 64) return false;
  let off = begin;
  while (off < end) {
    if (off + 6 > end) return false;
    const t = buf[off] | (buf[off + 1] << 8);
    const ln = readU32(buf, off + 2);
    if (t === 0 || ln > end - off - 6) return false;
    off += 6 + ln;
  }
  for (off = begin; off < end; ) {
    const t = buf[off] | (buf[off + 1] << 8);
    const ln = readU32(buf, off + 2);
    if ((t === tag && ln > 0) || tlvHasNonEmptyTag(buf, off + 6, off + 6 + ln, tag, depth + 1)) return true;
    off += 6 + ln;
  }
  return false;
}

export function composeInstanceWorld(
  rootBuilder: GeometryBuilder,
  definitions: Map<number | string, ParsedDefinition>,
  world: Map<string, number[] | null>,
  maxVisits = MAX_EXPANDED_INSTANCES
): number {
  let visits = 0;
  function walk(builder: GeometryBuilder, parent: number[] | null, depth: number) {
    if (depth > 64 || visits >= maxVisits) return;
    for (const inst of builder.instances) {
      if (visits >= maxVisits) return;
      visits++;
      const here: number[] | null =
        inst.matrix && inst.matrix.length > 0
          ? multiplyMatrices(parent as number[], inst.matrix)
          : parent;
      if (inst.persistentId) {
        world.set(inst.persistentId, here);
      }
      if (inst.refIdx == null || inst.refIdx < 0) continue;
      const childDef = definitions.get(inst.refIdx);
      if (childDef) walk(childDef.builder, here, depth + 1);
    }
  }
  walk(rootBuilder, null, 0);
  return visits;
}

class InstancePathResolver {
  visits = 0;
  private readonly byPid = new Map<number | 'ROOT', Map<string, GeometryBuilder['instances'][number]>>();
  private readonly resolved = new Map<string, { matched: boolean; xf: number[] | null }>();
  private indexed = false;
  private readonly holdersOfPid = new Map<string, Array<number | null>>();
  private readonly parentsOfDef = new Map<number, Array<number | null>>();

  constructor(
    private readonly rootBuilder: GeometryBuilder,
    private readonly defsDict: Map<number | string, ParsedDefinition>,
    private readonly maxVisits: number
  ) {}

  private builderFor(owner: number | null): GeometryBuilder | null {
    if (owner === null) return this.rootBuilder;
    const d = this.defsDict.get(owner);
    return d ? d.builder : null;
  }

  private pidMap(owner: number | null, builder: GeometryBuilder): Map<string, GeometryBuilder['instances'][number]> {
    const key = owner === null ? 'ROOT' : owner;
    let map = this.byPid.get(key);
    if (map !== undefined) return map;
    map = new Map();
    for (const inst of builder.instances) {
      if (inst.persistentId) {
        map.set(inst.persistentId.toLowerCase(), inst);
      }
    }
    this.byPid.set(key, map);
    return map;
  }

  private ensureIndex(): void {
    if (this.indexed) return;
    this.indexed = true;
    const owners: Array<number | null> = [null];
    for (const k of this.defsDict.keys()) {
      if (typeof k === 'number') owners.push(k);
    }
    for (const defOwner of owners) {
      const builder = this.builderFor(defOwner);
      if (builder === null) continue;
      const seenPids = new Set<string>();
      const seenRefs = new Set<number>();
      for (const inst of builder.instances) {
        if (inst.persistentId) {
          const normPid = inst.persistentId.toLowerCase();
          if (!seenPids.has(normPid)) {
            seenPids.add(normPid);
            let list = this.holdersOfPid.get(normPid);
            if (list === undefined) {
              list = [];
              this.holdersOfPid.set(normPid, list);
            }
            list.push(defOwner);
          }
        }
        if (inst.refIdx != null && inst.refIdx >= 0 && this.defsDict.has(inst.refIdx) && !seenRefs.has(inst.refIdx)) {
          seenRefs.add(inst.refIdx);
          let parents = this.parentsOfDef.get(inst.refIdx);
          if (parents === undefined) {
            parents = [];
            this.parentsOfDef.set(inst.refIdx, parents);
          }
          parents.push(defOwner);
        }
      }
    }
  }

  private walkExact(
    owner: number | null,
    builder: GeometryBuilder | null,
    path: string[],
    idx: number,
    parentXf: number[] | null,
    depth: number,
    queryState: { visits: number }
  ): { matched: boolean; xf: number[] | null } {
    if (depth > 64 || builder === null || queryState.visits >= this.maxVisits) {
      return { matched: false, xf: null };
    }
    const inst = this.pidMap(owner, builder).get(path[idx]);
    if (inst === undefined) return { matched: false, xf: null };
    this.visits++;
    queryState.visits++;
    const here: number[] | null =
      inst.matrix && inst.matrix.length > 0
        ? multiplyMatrices(parentXf as number[], inst.matrix)
        : parentXf;
    if (idx + 1 === path.length) {
      return { matched: true, xf: here };
    }
    if (inst.refIdx == null || inst.refIdx < 0) return { matched: false, xf: null };
    const childBuilder = this.builderFor(inst.refIdx);
    if (childBuilder === null) return { matched: false, xf: null };
    return this.walkExact(inst.refIdx, childBuilder, path, idx + 1, here, depth + 1, queryState);
  }

  resolvePath(owner: number | null, rawPath: string[], fallbackOwner: number | null = null): number[] | null {
    if (rawPath.length === 0) return null;
    const norm = rawPath.map((p) => p.toLowerCase());
    const cacheKey = `${owner === null ? 'ROOT' : owner}:${fallbackOwner === null ? 'NONE' : fallbackOwner}:${norm.join('/')}`;
    const cached = this.resolved.get(cacheKey);
    if (cached !== undefined) {
      return cached.matched ? cached.xf : null;
    }
    const queryState = { visits: 0 };
    const startBuilder = this.builderFor(owner);
    let res = this.walkExact(owner, startBuilder, norm, 0, null, 0, queryState);
    if (res.matched) {
      this.resolved.set(cacheKey, res);
      return res.xf;
    }
    if (queryState.visits >= this.maxVisits) {
      return null;
    }

    this.ensureIndex();
    const validHolders = new Set<number | null>();
    let fbResult: { matched: boolean; xf: number[] | null } | null = null;
    const holders = this.holdersOfPid.get(norm[0]) ?? [];
    for (const holder of holders) {
      if (holder === owner) continue;
      if (queryState.visits >= this.maxVisits) break;
      const hRes = this.walkExact(holder, this.builderFor(holder), norm, 0, null, 0, queryState);
      if (hRes.matched) {
        validHolders.add(holder);
        if (fallbackOwner !== null && holder === fallbackOwner) {
          fbResult = hRes;
        }
      }
    }

    if (validHolders.size > 0 && queryState.visits < this.maxVisits) {
      const canReach = new Set<number | null>(validHolders);
      const queue: number[] = [];
      for (const h of validHolders) {
        if (h !== null) queue.push(h);
      }
      for (let qi = 0; qi < queue.length; qi++) {
        const cur = queue[qi];
        const parents = this.parentsOfDef.get(cur) ?? [];
        for (const parentOwner of parents) {
          if (!canReach.has(parentOwner)) {
            canReach.add(parentOwner);
            if (parentOwner !== null) queue.push(parentOwner);
          }
        }
      }

      if (canReach.has(owner)) {
        const activeDefs = new Set<number>();
        if (owner !== null) activeDefs.add(owner);
        const failedMinDepth = new Map<number, number>();

        const walkToHolder = (
          curOwner: number | null,
          curBuilder: GeometryBuilder | null,
          parentXf: number[] | null,
          depth: number
        ): { matched: boolean; xf: number[] | null } => {
          if (depth > 64 || curBuilder === null || queryState.visits >= this.maxVisits) {
            return { matched: false, xf: null };
          }
          if (depth > 0 && validHolders.has(curOwner) && depth + norm.length <= 65) {
            const tail = this.walkExact(curOwner, curBuilder, norm, 0, parentXf, depth, queryState);
            if (tail.matched || queryState.visits >= this.maxVisits) return tail;
          }
          for (const inst of curBuilder.instances) {
            if (queryState.visits >= this.maxVisits) return { matched: false, xf: null };
            const refIdx = inst.refIdx;
            if (refIdx == null || refIdx < 0 || !canReach.has(refIdx) || activeDefs.has(refIdx)) {
              continue;
            }
            const prevFail = failedMinDepth.get(refIdx);
            if (prevFail !== undefined && depth + 1 >= prevFail) continue;
            const childBuilder = this.builderFor(refIdx);
            if (childBuilder === null) continue;
            this.visits++;
            queryState.visits++;
            const here: number[] | null =
              inst.matrix && inst.matrix.length > 0
                ? multiplyMatrices(parentXf as number[], inst.matrix)
                : parentXf;
            activeDefs.add(refIdx);
            const found = walkToHolder(refIdx, childBuilder, here, depth + 1);
            activeDefs.delete(refIdx);
            if (found.matched) return found;
            if (queryState.visits >= this.maxVisits) return { matched: false, xf: null };
          }
          if (curOwner !== null) {
            const prev = failedMinDepth.get(curOwner);
            if (prev === undefined || depth < prev) {
              failedMinDepth.set(curOwner, depth);
            }
          }
          return { matched: false, xf: null };
        };

        res = walkToHolder(owner, startBuilder, null, 0);
      }
    }

    if (!res.matched && fallbackOwner !== null && fallbackOwner !== owner) {
      if (fbResult !== null) {
        res = fbResult;
      } else if (queryState.visits < this.maxVisits && validHolders.has(fallbackOwner)) {
        res = this.walkExact(fallbackOwner, this.builderFor(fallbackOwner), norm, 0, null, 0, queryState);
      }
    }

    if (queryState.visits < this.maxVisits) {
      this.resolved.set(cacheKey, res);
    }
    return res.matched ? res.xf : null;
  }
}

export function assignVffAnnotations(
  modelData: Uint8Array,
  id2pos: Map<string, [number, number, number]>,
  rootBuilder: GeometryBuilder,
  defsDict: Map<number | string, ParsedDefinition>,
  worldDimensions?: RawDimension[],
  maxVisits = MAX_EXPANDED_INSTANCES
): number {
  let spans: DefSpan[] | null = null;
  const resolver = new InstancePathResolver(rootBuilder, defsDict, maxVisits);
  const n = modelData.length;
  for (let j = 0; j + 6 <= n; j++) {
    const b0 = modelData[j];
    const b1 = modelData[j + 1];
    if (!((b0 === 0xcc && b1 === 0x5b) || (b0 === 0xf0 && b1 === 0x55))) continue;
    const ln = readU32(modelData, j + 2);
    if (ln < 40 || j + 6 + ln > n) continue;
    const isText = b0 === 0xf0;
    if (!flatTlvHasFields(modelData, j + 6, j + 6 + ln, isText ? 0x55f1 : 0x5bcd, isText ? 0x55f4 : 0x5bce)) {
      continue;
    }
    if (spans === null) {
      spans = scanDefinitionSpans(modelData, defsDict);
    }
    const owner = owningDefinition(spans, j);
    const ownerResolver: PathResolverFn = (path) => resolver.resolvePath(owner, path);
    const targetBuilder = owner !== null ? defsDict.get(owner)!.builder : rootBuilder;
    if (isText) {
      const txt = parseVffText(modelData, j, id2pos, ownerResolver);
      if (txt !== null) targetBuilder.texts.push(txt);
      continue;
    }
    const dim = parseOne5Bcc(modelData, j, id2pos, ownerResolver);
    if (dim !== null) {
      targetBuilder.dimensions.push({
        text: dim.text,
        hidden: dim.hidden ?? false,
        layer: dim.layer ?? '',
        a: dim.a,
        b: dim.b,
        offset: dim.offset,
        planeX: dim.planeX,
        normal: dim.normal,
        offsetVector: dim.offsetVector ?? null,
      });
    }
    if (worldDimensions !== undefined) {
      if (owner === null || !tlvHasNonEmptyTag(modelData, j + 6, j + 6 + ln, 0x53fe)) {
        if (dim !== null) {
          worldDimensions.push({ ...dim });
        }
      } else {
        const wdim = parseOne5Bcc(modelData, j, id2pos, (path) => resolver.resolvePath(null, path, owner));
        if (wdim !== null) {
          worldDimensions.push(wdim);
        }
      }
    }
  }
  return resolver.visits;
}

export function parseDimensions(
  modelData: Uint8Array,
  id2pos: Map<string, [number, number, number]>,
  instWorld: Map<string, number[] | null>
): RawDimension[] {
  const dims: RawDimension[] = [];
  const needle = new Uint8Array([0xcc, 0x5b]);
  const resolvePath = mapPathResolver(instWorld);
  let i = 0;
  while (true) {
    const j = findBytes(modelData, needle, i);
    if (j < 0) break;
    i = j + 1;
    const d = parseOne5Bcc(modelData, j, id2pos, resolvePath);
    if (d !== null) dims.push(d);
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

  function parseIdRuns(runs: Uint8Array | null): number[] {
    const ids: number[] = [];
    let off = 0;
    while (runs !== null && off + 1 <= runs.length) {
      const ln = runs[off];
      if (ln === 0 || off + 1 + ln > runs.length) break;
      ids.push(parseVarInt(runs, off + 1, ln));
      off += 1 + ln;
    }
    return ids;
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
          hiddenEntityIds: [],
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

        page.hiddenLayerIds = parseIdRuns(tlvFindInt(items, 0x7150));
        page.hiddenEntityIds = parseIdRuns(tlvFindInt(items, 0x714b));

        // A scene that doesn't save its camera ("Camera Location" unticked) is
        // still a scene: it keeps its name and hidden layers, eye/target/up null.
        pages.push(page);
      }
    }
  }
  return pages;
}
