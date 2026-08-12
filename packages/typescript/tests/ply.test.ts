import { describe, it, expect } from 'vitest';
import { toPLYAscii, toPLYBinary } from '../src/ply';
import { SkpScene } from '../src/model';

describe('PLY Exporter (ASCII & Binary)', () => {
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
    gltfMaterials: [
      {
        name: 'Mat1',
        baseColorFactor: [1.0, 0.0, 0.0, 1.0],
      },
    ],
  });

  it('serializes SkpScene to ASCII PLY format', () => {
    const scene = createMockScene();
    const asciiText = toPLYAscii(scene);
    expect(asciiText).toContain('format ascii 1.0');
    expect(asciiText).toContain('element vertex 3');
    expect(asciiText).toContain('element face 1');
    expect(asciiText).toContain('3 0 1 2');
  });

  it('serializes SkpScene to Binary PLY format', () => {
    const scene = createMockScene();
    const binaryData = toPLYBinary(scene);
    const decoder = new TextDecoder();
    const str = decoder.decode(binaryData);
    expect(str).toContain('format binary_little_endian 1.0');
    expect(str).toContain('element vertex 3');
    expect(str).toContain('element face 1');
  });
});
