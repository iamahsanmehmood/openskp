import { describe, it, expect } from 'vitest';
import { validateHeader, readVersion } from '../src/vff';
import { readU32, readF64, parseVarInt, parseTlvRecursive } from '../src/parser';
import { transformPoint, multiplyMatrices, isIdentity } from '../src/transforms';
import { computeFaceNormal, triangulateFace3D } from '../src/triangulator';
import { GeometryBuilder, extractGeometryFromNodes, extractUvTransforms, collectDefs } from '../src/geometry';

/** Build a single TLV element: 2-byte tag (hex) + 4-byte LE size + payload. */
function tlv(tagHex: string, payload: Uint8Array): Uint8Array {
  const tagBytes = new Uint8Array(tagHex.match(/.{2}/g)!.map((h) => parseInt(h, 16)));
  const sizeBytes = new Uint8Array(4);
  new DataView(sizeBytes.buffer).setUint32(0, payload.length, true);
  const result = new Uint8Array(2 + 4 + payload.length);
  result.set(tagBytes, 0);
  result.set(sizeBytes, 2);
  result.set(payload, 6);
  return result;
}

function concatBytes(...arrays: Uint8Array[]): Uint8Array {
  const total = arrays.reduce((sum, a) => sum + a.length, 0);
  const result = new Uint8Array(total);
  let offset = 0;
  for (const a of arrays) {
    result.set(a, offset);
    offset += a.length;
  }
  return result;
}

describe('VFF Header and Version Parsing', () => {
  it('should validate VFF header', () => {
    const validHeader = new Uint8Array([0xFF, 0xFE, 0xFF, 0x0E, 0x01, 0x02]);
    const invalidHeader = new Uint8Array([0xFF, 0xFF, 0xFF, 0x0E]);
    expect(validateHeader(validHeader)).toBe(true);
    expect(validateHeader(invalidHeader)).toBe(false);
  });

  it('should extract SketchUp version', () => {
    // A mock VFF header containing the version string in UTF-16LE inside braces
    // The second \xFF\xFE\xFF marker is at some offset, say index 6.
    // So: [0xFF, 0xFE, 0xFF, 0x0E, 0x00, 0x00, 0xFF, 0xFE, 0xFF, 0x00, ...]
    // Version starts at second_marker + 4, which is 6 + 4 = 10.
    const text = 'Some info {23.0.123}';
    const encoder = new TextEncoder();
    // Convert to UTF-16LE
    const textBytes = new Uint8Array(text.length * 2);
    for (let i = 0; i < text.length; i++) {
      const code = text.charCodeAt(i);
      textBytes[i * 2] = code & 0xff;
      textBytes[i * 2 + 1] = (code >> 8) & 0xff;
    }

    const data = new Uint8Array(10 + textBytes.length);
    data.set([0xFF, 0xFE, 0xFF, 0x0E, 0x00, 0x00, 0xFF, 0xFE, 0xFF, 0x00], 0);
    data.set(textBytes, 10);

    expect(readVersion(data)).toBe('{23.0.123}');
  });
});

describe('Low-Level Parser Decoders', () => {
  it('should read uint32 little-endian', () => {
    const data = new Uint8Array([0xEF, 0xBE, 0xAD, 0xDE]);
    expect(readU32(data, 0)).toBe(0xDEADBEEF);
  });

  it('should read float64 little-endian', () => {
    // 1.0 in float64 is 0x3FF0000000000000
    const data = new Uint8Array([0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0xF0, 0x3F]);
    expect(readF64(data, 0)).toBe(1.0);
  });

  it('should parse variable length integer', () => {
    const data = new Uint8Array([0x01, 0x02, 0x03]);
    expect(parseVarInt(data, 0, 1)).toBe(1);
    expect(parseVarInt(data, 0, 2)).toBe(0x0201);
    expect(parseVarInt(data, 0, 3)).toBe(0x030201);
  });

  it('should recursively decode TLV', () => {
    // Let's build a tag. Tag F601 is a container.
    // Structure:
    // [0xF6, 0x01] -> tag
    // [0x08, 0x00, 0x00, 0x00] -> size: 8
    //   Children payload (8 bytes):
    //   [0x03, 0x00] -> tag 0300 (Vertex)
    //   [0x00, 0x00, 0x00, 0x00] -> size: 0
    //   [0x00, 0x00] -> tag 0000
    //   [0x00, 0x00, 0x00, 0x00] -> size: 0 (or padding)
    const data = new Uint8Array([
      0xF6, 0x01, 0x06, 0x00, 0x00, 0x00, // Tag F601, size 6
      0x03, 0x00, 0x00, 0x00, 0x00, 0x00  // Tag 0300, size 0
    ]);
    const nodes = parseTlvRecursive(data, 0, data.length);
    expect(nodes.length).toBe(1);
    expect(nodes[0].tag).toBe('F601');
    expect(nodes[0].children.length).toBe(1);
    expect(nodes[0].children[0].tag).toBe('0300');
  });
});

describe('Transforms and Matrices', () => {
  it('should transform 3D points', () => {
    const translationMatrix = [
      1.0, 0.0, 0.0,
      0.0, 1.0, 0.0,
      0.0, 0.0, 1.0,
      5.0, 10.0, -2.0, // translation
      1.0
    ];
    const point: [number, number, number] = [1.0, 2.0, 3.0];
    const transformed = transformPoint(translationMatrix, point);
    expect(transformed).toEqual([6.0, 12.0, 1.0]);
  });

  it('should multiply 13-element matrices', () => {
    const m1 = [
      1, 0, 0,
      0, 1, 0,
      0, 0, 1,
      1, 2, 3,
      1.0
    ];
    const m2 = [
      1, 0, 0,
      0, 1, 0,
      0, 0, 1,
      4, 5, 6,
      1.0
    ];
    const result = multiplyMatrices(m1, m2);
    // Translation should be added
    expect(result[9]).toBe(5);
    expect(result[10]).toBe(7);
    expect(result[11]).toBe(9);
  });
});

describe('Triangulation', () => {
  it('should compute face normal', () => {
    const points: [number, number, number][] = [
      [0, 0, 0],
      [1, 0, 0],
      [1, 1, 0],
      [0, 1, 0]
    ];
    const normal = computeFaceNormal(points);
    expect(normal).toBeDefined();
    expect(normal![2]).toBeCloseTo(1.0);
  });

  it('should triangulate 3D polygons', () => {
    const vertices = new Map<number, [number, number, number]>([
      [0, [0, 0, 0]],
      [1, [1, 0, 0]],
      [2, [1, 1, 0]],
      [3, [0, 1, 0]]
    ]);
    const loops = [[0, 1, 2, 3]];
    const normal: [number, number, number] = [0, 0, 1];
    const triangles = triangulateFace3D(vertices, loops, normal);
    expect(triangles.length).toBe(2); // Two triangles for a quad
    expect(triangles[0]).toEqual([0, 1, 2]);
    expect(triangles[1]).toEqual([0, 2, 3]);
  });
});

describe('Instance material (paint the component)', () => {
  it('reads the D007/D107 material id from a 6419 instance node', () => {
    const d107 = tlv('D107', new Uint8Array([0x33, 0x73])); // id 0x7333
    const d007 = tlv('D007', d107);
    const ref = tlv('6719', new Uint8Array([0x05])); // refIdx 5
    const node = tlv('6419', concatBytes(ref, d007));

    const elements = parseTlvRecursive(node, 0, node.length);
    const builder = new GeometryBuilder();
    extractGeometryFromNodes(elements, builder);

    expect(builder.instances.length).toBe(1);
    expect(builder.instances[0].refIdx).toBe(5);
    expect(builder.instances[0].materialId).toBe(0x7333);
  });

  it('defaults materialId to null when the instance has no D007/D107', () => {
    const ref = tlv('6719', new Uint8Array([0x05]));
    const node = tlv('6419', ref);

    const elements = parseTlvRecursive(node, 0, node.length);
    const builder = new GeometryBuilder();
    extractGeometryFromNodes(elements, builder);

    expect(builder.instances.length).toBe(1);
    expect(builder.instances[0].materialId).toBeNull();
  });
});

describe('Face UV transform (positioned texture mapping)', () => {
  const ROT90 = [0.0, 1.0, 0.0, -1.0, 0.0, 0.0, 96.0, -96.0, 1.0];

  function packF64Array(values: number[]): Uint8Array {
    const buf = new Uint8Array(values.length * 8);
    const view = new DataView(buf.buffer);
    values.forEach((v, i) => view.setFloat64(i * 8, v, true));
    return buf;
  }

  function dc05(front: number[] | null, back: number[] | null): Uint8Array {
    const side = (tag: string, mat: number[]) => {
      const m1527 = tlv('1527', packF64Array(mat));
      const inner1327 = tlv('1327', concatBytes(tlv('1427', new Uint8Array([0x01])), m1527));
      return tlv(tag, inner1327);
    };
    let inner = new Uint8Array(0);
    if (front !== null) inner = concatBytes(inner, side('1127', front));
    if (back !== null) inner = concatBytes(inner, side('1227', back));
    const t1027 = tlv('1027', inner);
    return concatBytes(
      tlv('DE05', new Uint8Array([0x2a])),
      tlv('DD05', tlv('B136', tlv('B236', t1027)))
    );
  }

  it('extracts the front matrix', () => {
    const [front, back] = extractUvTransforms(dc05(ROT90, null));
    expect(front).not.toBeNull();
    front!.forEach((v, i) => expect(v).toBeCloseTo(ROT90[i]));
    expect(back).toBeNull();
  });

  it('extracts both sides', () => {
    const other = ROT90.map((v) => v * 2);
    const [front, back] = extractUvTransforms(dc05(ROT90, other));
    expect(front).not.toBeNull();
    expect(back).not.toBeNull();
    front!.forEach((v, i) => expect(v).toBeCloseTo(ROT90[i]));
    back!.forEach((v, i) => expect(v).toBeCloseTo(other[i]));
  });

  it('returns null for an untouched texture (no DD05 block)', () => {
    const plain = tlv('DE05', new Uint8Array([0x2a])); // entity id only
    const [front, back] = extractUvTransforms(plain);
    expect(front).toBeNull();
    expect(back).toBeNull();
  });
});

describe('Image entities', () => {
  it('extracts the instance wrapped in 9013 -> 401F placement containers', () => {
    const inner6419 = tlv('6419', tlv('6719', new Uint8Array([0x07]))); // refIdx 7
    const node = tlv('9013', tlv('401F', inner6419));

    const elements = parseTlvRecursive(node, 0, node.length);
    const builder = new GeometryBuilder();
    extractGeometryFromNodes(elements, builder);

    expect(builder.instances.length).toBe(1);
    expect(builder.instances[0].refIdx).toBe(7);
  });

  it('marks Definition.isImage when the 8315 kind byte is 2', () => {
    const defOn = tlv(
      '7C15',
      concatBytes(
        tlv('DE05', new Uint8Array([0x01])),
        tlv('7D15', new Uint8Array(16).fill(0x11)),
        tlv('7E15', new TextEncoder().encode('imagen#1')),
        tlv('8315', new Uint8Array([0x02]))
      )
    );
    const defOff = tlv(
      '7C15',
      concatBytes(
        tlv('DE05', new Uint8Array([0x02])),
        tlv('7D15', new Uint8Array(16).fill(0x22)),
        tlv('7E15', new TextEncoder().encode('Grupo')),
        tlv('8315', new Uint8Array([0x00]))
      )
    );
    const buf = concatBytes(defOn, defOff);

    const elements = parseTlvRecursive(buf, 0, buf.length);
    const defsDict = collectDefs(elements);

    const names = Array.from(defsDict.values()).map((d) => [d.name, d.isImage]);
    expect(names).toContainEqual(['imagen#1', true]);
    expect(names).toContainEqual(['Grupo', false]);
  });
});
