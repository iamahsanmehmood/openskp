import { describe, it, expect } from 'vitest';
import { validateHeader, readVersion } from '../src/vff';
import { readU32, readF64, parseVarInt, parseTlvRecursive } from '../src/parser';
import { transformPoint, multiplyMatrices, isIdentity } from '../src/transforms';
import { computeFaceNormal, triangulateFace3D } from '../src/triangulator';

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
