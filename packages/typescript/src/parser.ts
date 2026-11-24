import { SkpParseError } from './errors';

export interface TlvHeader {
  offset: number;
  size: number;
}

export interface TlvNode {
  offset: number;
  tag: string;
  size: number;
  children: TlvNode[];
  payload: Uint8Array;
}

/**
 * VFF (2021+) model.dat binary structure uses Type-Length-Value (TLV) records.
 * Most TLV tags carry raw leaf binary/string payloads, but container tags contain
 * nested sub-TLV nodes (e.g. definitions, drawing elements, component instances).
 * CONTAINER_TAGS lists every tag hex ID that the TLV parser must recursively traverse.
 */
export const CONTAINER_TAGS = new Set<string>([
  'F401', 'F701', 'D430', 'D530', 'C832',
  '7C15', '8813', '8913', '8A13', '8B13', '8C13', '8D13', '4C1D', '6419',
  'F901', '7017', '7117', 'D007', 'C409', '9411', '9511', '0F01',
  '384A', 'B80B', '9713', '2C4C', 'AC0D', 'AE0D', 'F601', 'F801',
  '983A', '993A', '8C3C', '8D3C',
  // Image-entity placement: an Image placed in the model wraps a standard
  // 6419 instance node inside 9013 -> 401F. Without these two containers,
  // that inner instance stays buried in an opaque payload and the image
  // definition looks "never placed".
  '9013', '401F',
  // VFF per-definition guide lines/points and their entity/drawbase wrappers.
  '9113', '9213', '6942', '6C42', '6842',
]);

export function readU32(data: Uint8Array, offset: number): number {
  if (offset + 4 > data.length) {
    throw new Error('Out of bounds readU32');
  }
  const view = new DataView(data.buffer, data.byteOffset + offset, 4);
  return view.getUint32(0, true);
}

export function readF64(data: Uint8Array, offset: number): number {
  if (offset + 8 > data.length) {
    throw new Error('Out of bounds readF64');
  }
  const view = new DataView(data.buffer, data.byteOffset + offset, 8);
  return view.getFloat64(0, true);
}

export function readI32(data: Uint8Array, offset: number): number {
  if (offset + 4 > data.length) {
    throw new Error('Out of bounds readI32');
  }
  const view = new DataView(data.buffer, data.byteOffset + offset, 4);
  return view.getInt32(0, true);
}

export function parseVarInt(data: Uint8Array, offset: number, length: number): number {
  let val = 0;
  for (let i = 0; i < length; i++) {
    val += data[offset + i] * Math.pow(256, i);
  }
  return val;
}

/**
 * Deepest container nesting the parser follows. A real model.dat nests about
 * a dozen levels (the deepest file checked reaches 11); a hostile file can
 * nest tens of thousands, which would overflow the call stack before any
 * error could be reported, so deeper input throws a {@link SkpParseError}.
 */
export const MAX_TLV_DEPTH = 64;

const TLV_WRAP = 0x100000000;
const HEX_BYTE: string[] = Array.from({ length: 256 }, (_, i) =>
  i.toString(16).toUpperCase().padStart(2, '0')
);

function tagHexAt(data: Uint8Array, pos: number): string {
  return HEX_BYTE[data[pos]] + HEX_BYTE[data[pos + 1]];
}

function readU16Le(data: Uint8Array, pos: number): number {
  return data[pos] | (data[pos + 1] << 8);
}

function nestingError(tag: string, offset: number): SkpParseError {
  return new SkpParseError(`TLV records nest deeper than ${MAX_TLV_DEPTH} levels`, {
    stage: 'tlv_walk',
    tag,
    offset,
  });
}

interface Run {
  offset: number;
  size: number;
  count: number;
}

class Chain {
  runs: Run[] = [];
  candidates: TlvHeader[] = [];
  indexed = false;

  end(): number {
    const r = this.runs[this.runs.length - 1];
    return r.offset + r.count * (r.size + 6);
  }

  runAt(p: number): number {
    let lo = 0;
    let hi = this.runs.length;
    while (lo < hi) {
      const mid = Math.floor((lo + hi) / 2);
      if (p < this.runs[mid].offset) {
        hi = mid;
      } else {
        lo = mid + 1;
      }
    }
    return lo - 1;
  }

  contains(p: number): boolean {
    if (p < this.runs[0].offset || p >= this.end()) return false;
    const r = this.runs[this.runAt(p)];
    return (p - r.offset) % (r.size + 6) === 0;
  }

  clip(limit: number): number {
    const end = this.end();
    if (limit >= end) return end;
    const r = this.runs[this.runAt(limit)];
    const step = r.size + 6;
    return r.offset + Math.floor((limit - r.offset) / step) * step;
  }

  append(p: number, size: number): void {
    const n = this.runs.length;
    if (n > 0 && this.runs[n - 1].size === size && this.end() === p) {
      this.runs[n - 1].count += 1;
    } else {
      this.runs.push({ offset: p, size, count: 1 });
    }
  }
}

interface View {
  chain: Chain;
  begin: number;
  end: number;
}

interface Plan {
  views: View[];
  stop: number;
}

function lowerBoundCandidate(candidates: TlvHeader[], targetOffset: number): number {
  let lo = 0;
  let hi = candidates.length;
  while (lo < hi) {
    const mid = Math.floor((lo + hi) / 2);
    if (candidates[mid].offset < targetOffset) {
      lo = mid + 1;
    } else {
      hi = mid;
    }
  }
  return lo;
}

class HeaderResolver {
  private attempts = 256;
  private chains: Chain[] = [];
  private decisions = new Map<string, Plan | null>();

  constructor(
    private readonly lengthFn: (offset: number) => number,
    private readonly containerFn: (offset: number) => boolean,
    private readonly limit: number
  ) {}

  private chainAt(p: number): Chain | undefined {
    for (let i = this.chains.length - 1; i >= 0; i--) {
      if (this.chains[i].contains(p)) return this.chains[i];
    }
    return undefined;
  }

  private ordinary(start: number, end: number): Plan {
    const views: View[] = [];
    let p = start;
    while (end - p >= 6) {
      const cached = this.chainAt(p);
      if (cached !== undefined) {
        const next = cached.clip(end);
        if (next > p) views.push({ chain: cached, begin: p, end: next });
        p = next;
        if (p < cached.end()) return { views, stop: p };
        continue;
      }
      const chain = new Chain();
      const begin = p;
      let probes = 0;
      let joined = false;
      while (end - p >= 6) {
        if (probes > 0 && probes % 1024 === 0 && this.chainAt(p) !== undefined) {
          joined = true;
          break;
        }
        const size = this.lengthFn(p);
        if (size > end - p - 6) break;
        chain.append(p, size);
        p += 6 + size;
        probes += 1;
      }
      if (p > begin) {
        this.chains.push(chain);
        views.push({ chain, begin, end: p });
      }
      if (!joined) return { views, stop: p };
    }
    return { views, stop: p };
  }

  private index(chain: Chain): void {
    if (chain.indexed) return;
    chain.indexed = true;
    for (const r of chain.runs) {
      const step = r.size + 6;
      for (let k = 0; k < r.count; k++) {
        const p = r.offset + k * step;
        if (this.limit - p - step < TLV_WRAP) return;
        if (this.containerFn(p)) chain.candidates.push({ offset: p, size: r.size });
      }
    }
  }

  private static replace(
    original: Plan,
    h: TlvHeader,
    size: number,
    suffix: Plan,
    end: number
  ): Plan {
    const views: View[] = [];
    for (const v of original.views) {
      if (v.begin >= h.offset) break;
      views.push({ chain: v.chain, begin: v.begin, end: Math.min(v.end, h.offset) });
      if (v.end >= h.offset) break;
    }
    const extended = new Chain();
    extended.append(h.offset, size);
    views.push({ chain: extended, begin: h.offset, end: h.offset + 6 + size });
    for (const v of suffix.views) views.push(v);
    return { views, stop: end };
  }

  private solve(start: number, end: number, depth: number): Plan | null {
    const normal = this.ordinary(start, end);
    if (normal.stop === end) return normal;
    const key = `${start}:${end}:${depth}`;
    if (this.decisions.has(key)) return this.decisions.get(key)!;
    for (const v of normal.views) {
      this.index(v.chain);
      const candidates = v.chain.candidates;
      for (
        let idx = lowerBoundCandidate(candidates, v.begin);
        idx < candidates.length && candidates[idx].offset < v.end;
        idx++
      ) {
        const h = candidates[idx];
        const available = end - h.offset - 6;
        if (available - h.size < TLV_WRAP) break;
        let n = h.size + TLV_WRAP;
        while (n <= available) {
          if (depth >= MAX_TLV_DEPTH || this.attempts === 0) {
            throw new SkpParseError('TLV length recovery exceeded its depth or attempt limit', {
              stage: 'tlv_walk',
              offset: h.offset,
            });
          }
          this.attempts -= 1;
          const next = h.offset + 6 + n;
          const suffix = this.solve(next, end, depth + 1);
          if (suffix !== null && this.solve(h.offset + 6, next, depth + 1) !== null) {
            const result = HeaderResolver.replace(normal, h, n, suffix, end);
            this.decisions.set(key, result);
            return result;
          }
          if (available - n < TLV_WRAP) break;
          n += TLV_WRAP;
        }
      }
    }
    this.decisions.set(key, null);
    return null;
  }

  private static materialize(plan: Plan): TlvHeader[] {
    const out: TlvHeader[] = [];
    for (const v of plan.views) {
      let i = v.chain.runAt(v.begin);
      while (i < v.chain.runs.length && v.chain.runs[i].offset < v.end) {
        const r = v.chain.runs[i++];
        const step = r.size + 6;
        const rEnd = r.offset + r.count * step;
        const stop = Math.min(v.end, rEnd);
        const k0 = v.begin <= r.offset ? 0 : Math.floor((v.begin - r.offset) / step);
        const k1 = stop <= r.offset ? 0 : Math.floor((stop - r.offset) / step);
        for (let k = k0; k < k1; k++) {
          out.push({ offset: r.offset + k * step, size: r.size });
        }
      }
    }
    return out;
  }

  headers(start: number, end: number): TlvHeader[] {
    const plan = this.solve(start, end, 0);
    if (plan !== null) return HeaderResolver.materialize(plan);
    return HeaderResolver.materialize(this.ordinary(start, end));
  }
}

export function wrappedModelRecords(
  end: number,
  tag: (offset: number) => number,
  length: (offset: number) => number
): TlvHeader[] | null {
  if (end < 6 || end - 6 < TLV_WRAP || tag(0) !== 0x01f4 || length(0) !== (end - 6) % TLV_WRAP) {
    return null;
  }

  const storedRootEnd = 6 + length(0);
  let storedParentEnd = storedRootEnd;
  let contradictsStoredLengths = false;
  let pos = 6;

  const checkedLength = (): number => {
    const size = length(pos);
    const crosses = (limit: number): boolean =>
      pos <= limit && limit - pos >= 6 && size > limit - pos - 6;
    if (crosses(storedRootEnd) || crosses(storedParentEnd)) {
      contradictsStoredLengths = true;
    }
    return size;
  };
  const has = (expected: number): boolean => end - pos >= 6 && tag(pos) === expected;
  const advance = (): boolean => {
    if (end - pos < 6) return false;
    const size = checkedLength();
    if (size > end - pos - 6) return false;
    pos += 6 + size;
    return true;
  };

  while (!has(0x01f9)) {
    if (!advance()) return null;
  }
  const catalog = pos;
  const wrappers: number[] = [];
  const path = [0x01f9, 0x1770, 0x1771];
  for (let i = 0; i < path.length; i++) {
    if (!has(path[i])) return null;
    wrappers.push(pos);
    const size = checkedLength();
    if (pos <= storedParentEnd && storedParentEnd - pos >= 6 && size <= storedParentEnd - pos - 6) {
      storedParentEnd = pos + 6 + size;
    }
    pos += 6;
    if (has(0x0063) && !advance()) return null;
  }
  const definitions = pos;
  while (has(0x157c)) {
    if (!advance()) return null;
  }
  const entities = pos;
  if (definitions === entities || !has(0x01f6)) return null;
  for (const offset of wrappers) {
    if (length(offset) !== (entities - offset - 6) % TLV_WRAP) return null;
  }
  storedParentEnd = storedRootEnd;
  while (pos < end) {
    if (!advance()) return null;
  }
  if (!contradictsStoredLengths) return null;

  const records: TlvHeader[] = [];
  const append = (begin: number, stop: number): void => {
    for (let p = begin; p < stop; ) {
      const size = length(p);
      records.push({ offset: p, size });
      p += 6 + size;
    }
  };
  append(6, catalog);
  append(definitions, entities);
  append(entities, end);
  return records;
}

export function tlvHeaders(
  start: number,
  end: number,
  length: (offset: number) => number,
  container: (offset: number) => boolean
): TlvHeader[] {
  if (start < 0 || start > end) throw new RangeError('invalid TLV range');
  if (end - start >= TLV_WRAP) {
    return new HeaderResolver(length, container, end).headers(start, end);
  }
  const out: TlvHeader[] = [];
  let p = start;
  while (end - p >= 6) {
    const n = length(p);
    if (n > end - p - 6) break;
    out.push({ offset: p, size: n });
    p += 6 + n;
  }
  return out;
}

export function tlvHeadersBuffer(
  data: Uint8Array,
  start: number,
  end: number,
  containerTags: Set<string> = CONTAINER_TAGS
): TlvHeader[] {
  if (start < 0 || start > end || end > data.length) {
    throw new RangeError('invalid TLV range');
  }
  return tlvHeaders(
    start,
    end,
    (p) => readU32(data, p + 2),
    (p) => containerTags.has(tagHexAt(data, p))
  );
}

export function parseTlvRecursive(
  data: Uint8Array,
  start: number,
  end: number,
  containerTags: Set<string> = CONTAINER_TAGS,
  depth: number = 0
): TlvNode[] {
  if (start < 0 || start > end || end > data.length) {
    throw new RangeError('invalid TLV range');
  }
  const elements: TlvNode[] = [];

  for (const header of tlvHeadersBuffer(data, start, end, containerTags)) {
    const pos = header.offset;
    const size = header.size;
    const tagHex = tagHexAt(data, pos);

    let children: TlvNode[] = [];
    const isContainer = containerTags.has(tagHex);

    if (isContainer && size > 0) {
      if (depth >= MAX_TLV_DEPTH) throw nestingError(tagHex, pos);
      children = parseTlvRecursive(data, pos + 6, pos + 6 + size, containerTags, depth + 1);
    }

    const payload =
      children.length > 0 ? new Uint8Array(0) : data.subarray(pos + 6, pos + 6 + size);

    elements.push({
      offset: pos,
      tag: tagHex,
      size,
      children,
      payload,
    });
  }

  return elements;
}

/** Scan [start, end) for direct-child (tag, offset, size) headers only,
 * without recursing into any container - O(sibling count), not O(total
 * node count). Used by iterTopLevelLazy to locate top-level records one at
 * a time. */
function flatHeaders(
  data: Uint8Array,
  start: number,
  end: number,
  containerTags: Set<string> = CONTAINER_TAGS
): [string, number, number][] {
  if (end - start >= TLV_WRAP) {
    return tlvHeadersBuffer(data, start, end, containerTags).map((h) => [
      tagHexAt(data, h.offset),
      h.offset,
      h.size,
    ]);
  }
  const headers: [string, number, number][] = [];
  let pos = start;
  while (pos <= end - 6) {
    const size = readU32(data, pos + 2);
    if (pos + 6 + size > end) break;
    headers.push([tagHexAt(data, pos), pos, size]);
    pos += 6 + size;
  }
  return headers;
}

/** Throw if a top-level record declares more bytes than the data holds.
 *
 * `flatHeaders` stops quietly at such a record, so a model.dat cut short
 * anywhere inside its single F401 record would parse as a model with no
 * geometry and no error. Real files end exactly where their last top-level
 * record does. Records nested inside it are deliberately left lenient: in
 * real files some container payloads end with bytes that are not records,
 * and a strict check there would reject valid models. */
function requireCompleteTopLevel(
  data: Uint8Array,
  start: number,
  end: number,
  headers: [string, number, number][]
): void {
  const pos =
    headers.length === 0
      ? start
      : headers[headers.length - 1][1] + 6 + headers[headers.length - 1][2];
  if (end - pos >= 6) {
    const size = readU32(data, pos + 2);
    const tag = tagHexAt(data, pos);
    throw new SkpParseError(
      `model.dat is truncated: the top-level record declares ${size} bytes but only ${end - pos - 6} remain`,
      { stage: 'tlv_walk', tag, offset: pos }
    );
  }
}

export interface TopLevelRecord {
  index: number;
  total: number;
  node: TlvNode;
}

/** Pure wrapper containers on the path to the component definitions:
 * "F901" > "7017" > "7117" > one "7C15" per definition. In real files this
 * chain holds nearly the whole model (a 297 MB model.dat had 100% of its
 * bytes under a single F901), so yielding F901 as one record built the
 * entire definitions tree at once. No consumer of iterTopLevelLazy matches
 * these three tags - every walker searches by tag at any depth - so
 * yielding their children one at a time instead is equivalent. */
const PASS_THROUGH_WRAPPERS = new Set<string>(['F901', '7017', '7117']);

/** Replace each pass-through wrapper header with its children's headers,
 * recursively, keeping file order. Header scans only - no subtree is
 * built. */
function expandWrappers(
  data: Uint8Array,
  headers: [string, number, number][],
  containerTags: Set<string>,
  depth: number = 0
): [string, number, number][] {
  const out: [string, number, number][] = [];
  for (const header of headers) {
    const [tag, offset, size] = header;
    if (PASS_THROUGH_WRAPPERS.has(tag) && containerTags.has(tag)) {
      if (depth >= MAX_TLV_DEPTH) throw nestingError(tag, offset);
      out.push(
        ...expandWrappers(
          data,
          flatHeaders(data, offset + 6, offset + 6 + size, containerTags),
          containerTags,
          depth + 1
        )
      );
    } else {
      out.push(header);
    }
  }
  return out;
}

/** Yield `{index, total, node}` for each top-level TLV record's
 * fully-recursed node one at a time, transparently unwrapping a lone "F401"
 * wrapper and the F901 > 7017 > 7117 definitions chain (so each "7C15"
 * definition is its own record) - without ever materializing more than one
 * record subtree simultaneously. `total` (the record count) comes for free
 * from the same cheap header scan that drives the loop, so callers can
 * report "N of total" progress with no extra pass over the file. Each
 * yielded node is safe to discard (drop all references) once the caller is
 * done with it, before the next one is produced - that's what keeps peak
 * memory bounded by the size of the single largest record instead of the
 * whole file (real production files can have 100k+ separate definitions). */
export function* iterTopLevelLazy(
  data: Uint8Array,
  start: number,
  end: number,
  containerTags: Set<string> = CONTAINER_TAGS
): Generator<TopLevelRecord> {
  let headers: [string, number, number][];
  const wrapped =
    start === 0 && end === data.length && containerTags.has('F401') && end >= 6 && end - 6 >= TLV_WRAP
      ? wrappedModelRecords(
          end,
          (p) => readU16Le(data, p),
          (p) => readU32(data, p + 2)
        )
      : null;
  if (wrapped !== null) {
    headers = wrapped.map((h) => [tagHexAt(data, h.offset), h.offset, h.size]);
  } else {
    headers = flatHeaders(data, start, end, containerTags);
    requireCompleteTopLevel(data, start, end, headers);
    if (headers.length === 1 && headers[0][0] === 'F401') {
      const [, f401Offset, f401Size] = headers[0];
      headers = flatHeaders(data, f401Offset + 6, f401Offset + 6 + f401Size, containerTags);
    }
    headers = expandWrappers(data, headers, containerTags);
  }

  const total = headers.length;
  let index = 0;
  for (const [, offset, size] of headers) {
    const recordEnd = offset + 6 + size;
    const nodes = parseTlvRecursive(data, offset, recordEnd, containerTags);
    if (nodes.length > 0) {
      yield { index, total, node: nodes[0] };
    }
    index++;
  }
}
