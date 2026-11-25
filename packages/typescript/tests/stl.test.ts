import { describe, it, expect } from 'vitest';
import {
  create,
  buildScene,
  buildInstancedScene,
  toPLYAscii,
  toSTLAscii,
  toSTLBinary,
  SkpScene,
  Point3,
} from '../src';

type Tri = [[number, number, number], [number, number, number], [number, number, number]];

const BOX_FACES: Point3[][] = [
  [
    [0, 0, 0],
    [0, 10, 0],
    [10, 10, 0],
    [10, 0, 0],
  ],
  [
    [0, 0, 10],
    [10, 0, 10],
    [10, 10, 10],
    [0, 10, 10],
  ],
  [
    [0, 0, 0],
    [10, 0, 0],
    [10, 0, 10],
    [0, 0, 10],
  ],
  [
    [10, 0, 0],
    [10, 10, 0],
    [10, 10, 10],
    [10, 0, 10],
  ],
  [
    [10, 10, 0],
    [0, 10, 0],
    [0, 10, 10],
    [10, 10, 10],
  ],
  [
    [0, 10, 0],
    [0, 0, 0],
    [0, 0, 10],
    [0, 10, 10],
  ],
];

type PaintMode = 'unpainted' | 'one_face' | 'front' | 'back' | 'both' | 'mixed';

function buildBoxBuffer(paint: PaintMode): ArrayBuffer {
  const builder = create();
  const red = builder.addMaterial('Red', [255, 0, 0, 255]);
  const blue = builder.addMaterial('Blue', [0, 0, 255, 255]);
  BOX_FACES.forEach((face, i) => {
    let front: number | undefined;
    let back: number | undefined;
    if (paint === 'one_face') {
      if (i === 0) front = red;
    } else {
      if (paint === 'front' || paint === 'both' || paint === 'mixed') front = red;
      if (paint === 'back' || paint === 'both' || paint === 'mixed') back = blue;
      if (paint === 'mixed' && i % 2 === 1) {
        [front, back] = [back, front];
      }
    }
    builder.addFace(face, { material: front, backMaterial: back });
  });
  const bytes = builder.toBytes();
  return bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer;
}

function parseAsciiStlTriangles(text: string): Tri[] {
  const verts: [number, number, number][] = [];
  for (const line of text.split('\n')) {
    const trimmed = line.trim();
    if (trimmed.startsWith('vertex ')) {
      const parts = trimmed.split(/\s+/);
      verts.push([Number(parts[1]), Number(parts[2]), Number(parts[3])]);
    }
  }
  const tris: Tri[] = [];
  for (let i = 0; i + 2 < verts.length; i += 3) {
    tris.push([verts[i], verts[i + 1], verts[i + 2]]);
  }
  return tris;
}

function parseBinaryStlTriangles(data: Uint8Array): Tri[] {
  const view = new DataView(data.buffer, data.byteOffset, data.byteLength);
  const count = view.getUint32(80, true);
  expect(data.byteLength).toBe(84 + count * 50);
  const tris: Tri[] = [];
  for (let i = 0; i < count; i++) {
    const base = 84 + i * 50 + 12;
    tris.push([
      [view.getFloat32(base, true), view.getFloat32(base + 4, true), view.getFloat32(base + 8, true)],
      [view.getFloat32(base + 12, true), view.getFloat32(base + 16, true), view.getFloat32(base + 20, true)],
      [view.getFloat32(base + 24, true), view.getFloat32(base + 28, true), view.getFloat32(base + 32, true)],
    ]);
  }
  return tris;
}

function measureSolid(tris: Tri[]): { volumeCm3: number; maxEdgeSharing: number; edgeCounts: number[] } {
  let volM3 = 0;
  const vKey = (p: [number, number, number]) =>
    `${Math.round(p[0] * 1e6)},${Math.round(p[1] * 1e6)},${Math.round(p[2] * 1e6)}`;
  const edgeMap = new Map<string, number>();

  for (const [v0, v1, v2] of tris) {
    const cx = v1[1] * v2[2] - v1[2] * v2[1];
    const cy = v1[2] * v2[0] - v1[0] * v2[2];
    const cz = v1[0] * v2[1] - v1[1] * v2[0];
    volM3 += (v0[0] * cx + v0[1] * cy + v0[2] * cz) / 6;

    const keys = [vKey(v0), vKey(v1), vKey(v2)];
    for (let e = 0; e < 3; e++) {
      const a = keys[e];
      const b = keys[(e + 1) % 3];
      const ek = a < b ? `${a}|${b}` : `${b}|${a}`;
      edgeMap.set(ek, (edgeMap.get(ek) ?? 0) + 1);
    }
  }

  const edgeCounts = Array.from(edgeMap.values());
  return {
    volumeCm3: volM3 * 1e6,
    maxEdgeSharing: Math.max(0, ...edgeCounts),
    edgeCounts,
  };
}

describe('STL Exporter (ASCII & Binary)', () => {
  const createMockScene = (): SkpScene => ({
    sceneHierarchy: {
      name: 'Root',
      definitionName: 'RootDef',
      layer: 'Layer0',
      positionMm: [0, 0, 0],
      properties: {},
      children: [],
    },
    meshIndex: {},
    glbPrimitives: [
      {
        geomName: 'Box',
        materialIndex: 0,
        positions: new Float32Array([0, 0, 0, 1, 0, 0, 0, 1, 0]),
        normals: new Float32Array([0, 0, 1, 0, 0, 1, 0, 0, 1]),
        uvs: new Float32Array([0, 0, 1, 0, 0, 1]),
        indices: new Uint32Array([0, 1, 2]),
      },
    ],
    gltfMaterials: [],
  });

  it('serializes SkpScene to ASCII STL format', () => {
    const scene = createMockScene();
    const asciiText = toSTLAscii(scene);
    expect(asciiText).toContain('solid OpenSKP_Model');
    expect(asciiText).toContain('facet normal 0.000000 0.000000 1.000000');
    expect(asciiText).toContain('vertex 0.000000 0.000000 0.000000');
    expect(asciiText).toContain('endsolid OpenSKP_Model');
  });

  it('serializes SkpScene to Binary STL format', () => {
    const scene = createMockScene();
    const binaryData = toSTLBinary(scene);
    expect(binaryData.length).toBe(80 + 4 + 50); // Header + uint32 count + 1 triangle
    const headerStr = String.fromCharCode(...binaryData.slice(0, 27));
    expect(headerStr).toBe('# OpenSKP Binary STL Export');
  });

  for (const paint of ['unpainted', 'one_face', 'front', 'back', 'both', 'mixed'] as PaintMode[]) {
    for (const binary of [false, true]) {
      it(`omits back_triangles on 10 in ${paint} box (${binary ? 'binary' : 'ascii'} STL, #601)`, () => {
        const scene = buildScene(buildBoxBuffer(paint));
        const tris = binary
          ? parseBinaryStlTriangles(toSTLBinary(scene))
          : parseAsciiStlTriangles(toSTLAscii(scene));
        expect(tris).toHaveLength(12);
        const { volumeCm3, maxEdgeSharing, edgeCounts } = measureSolid(tris);
        expect(Math.round(volumeCm3 * 10) / 10).toBe(16387.1);
        expect(volumeCm3).toBeCloseTo(16387.064, 1);
        expect(maxEdgeSharing).toBe(2);
        expect(edgeCounts.every((c) => c === 2)).toBe(true);
      });
    }
  }

  it('records backTriangles in instanced scenes and leaves colour-carrying exports unchanged (#601)', () => {
    const ab = buildBoxBuffer('both');
    const baked = buildScene(ab);
    const instanced = buildInstancedScene(ab);
    expect(baked.glbPrimitives.reduce((acc, p) => acc + p.indices.length / 3, 0)).toBe(24);
    expect(baked.glbPrimitives.reduce((acc, p) => acc + (p.backTriangles?.length ?? 0), 0)).toBe(12);
    expect(
      instanced.meshResources[0].primitives.reduce((acc, p) => acc + (p.backTriangles?.length ?? 0), 0)
    ).toBe(12);

    const ply = toPLYAscii(baked);
    expect(ply).toContain('element face 24');
    expect(ply).toContain('255 0 0 255');
    expect(ply).toContain('0 0 255 255');
  });
});
