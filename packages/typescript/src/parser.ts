export interface TlvNode {
  offset: number;
  tag: string;
  size: number;
  children: TlvNode[];
  payload: Uint8Array;
}

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

export function parseVarInt(data: Uint8Array, offset: number, length: number): number {
  let val = 0;
  for (let i = 0; i < length; i++) {
    val += data[offset + i] * Math.pow(256, i);
  }
  return val;
}

export function parseTlvRecursive(
  data: Uint8Array,
  start: number,
  end: number,
  containerTags: Set<string> = CONTAINER_TAGS,
  depth: number = 0
): TlvNode[] {
  let pos = start;
  const elements: TlvNode[] = [];

  while (pos <= end - 6) {
    const tagBytes = data.subarray(pos, pos + 2);
    const size = readU32(data, pos + 2);

    if (pos + 6 + size > end) {
      break;
    }

    // Convert tagBytes to uppercase hex string
    let tagHex = '';
    for (let i = 0; i < 2; i++) {
      const hex = tagBytes[i].toString(16).toUpperCase();
      tagHex += hex.length === 1 ? '0' + hex : hex;
    }

    let children: TlvNode[] = [];
    const isContainer = containerTags.has(tagHex);

    if (isContainer && size > 0) {
      children = parseTlvRecursive(
        data,
        pos + 6,
        pos + 6 + size,
        containerTags,
        depth + 1
      );
    }

    const payload = children.length > 0 ? new Uint8Array(0) : data.subarray(pos + 6, pos + 6 + size);

    elements.push({
      offset: pos,
      tag: tagHex,
      size,
      children,
      payload,
    });

    pos += 6 + size;
  }

  return elements;
}

/** Scan [start, end) for direct-child (tag, offset, size) headers only,
 * without recursing into any container - O(sibling count), not O(total
 * node count). Used by iterTopLevelLazy to locate top-level records one at
 * a time. */
function flatHeaders(data: Uint8Array, start: number, end: number): [string, number, number][] {
  const headers: [string, number, number][] = [];
  let pos = start;
  while (pos <= end - 6) {
    const tagBytes = data.subarray(pos, pos + 2);
    const size = readU32(data, pos + 2);
    if (pos + 6 + size > end) break;
    let tagHex = '';
    for (let i = 0; i < 2; i++) {
      const hex = tagBytes[i].toString(16).toUpperCase();
      tagHex += hex.length === 1 ? '0' + hex : hex;
    }
    headers.push([tagHex, pos, size]);
    pos += 6 + size;
  }
  return headers;
}

/** Yield each top-level TLV record's fully-recursed node one at a time,
 * transparently unwrapping a lone "F401" wrapper - without ever
 * materializing more than one top-level subtree simultaneously. Each
 * yielded node is safe to discard (drop all references) once the caller is
 * done with it, before the next one is produced - that's what keeps peak
 * memory bounded by the size of the single largest top-level record
 * instead of the whole file (real production files can have 100k+ separate
 * definitions). */
export function* iterTopLevelLazy(
  data: Uint8Array,
  start: number,
  end: number,
  containerTags: Set<string> = CONTAINER_TAGS
): Generator<TlvNode> {
  let headers = flatHeaders(data, start, end);
  if (headers.length === 1 && headers[0][0] === 'F401') {
    const [, f401Offset, f401Size] = headers[0];
    headers = flatHeaders(data, f401Offset + 6, f401Offset + 6 + f401Size);
  }

  for (const [, offset, size] of headers) {
    const recordEnd = offset + 6 + size;
    const nodes = parseTlvRecursive(data, offset, recordEnd, containerTags);
    if (nodes.length > 0) {
      yield nodes[0];
    }
  }
}
