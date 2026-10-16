import { describe, expect, it } from 'vitest';
import { toPLYAscii, toPLYBinary } from '../src/ply';
import type { SkpScene } from '../src/model';

describe('PLY glTF material colors', () => {
  it('preserves nested baseColorFactor in ASCII and binary vertices', () => {
    const scene = {
      sceneHierarchy: {
        name: 'Root', definitionName: 'Root', layer: 'Layer0',
        positionMm: [0, 0, 0], properties: {}, children: [],
      },
      meshIndex: {},
      glbPrimitives: [{
        geomName: 'Red', materialIndex: 0,
        positions: new Float32Array([0, 0, 0]),
        normals: new Float32Array([0, 0, 1]),
        uvs: new Float32Array([0, 0]),
        indices: new Uint32Array(0),
      }],
      gltfMaterials: [{
        pbrMetallicRoughness: { baseColorFactor: [1, 0, 0, 0.5] },
      }],
    } as SkpScene;

    const vertex = toPLYAscii(scene).split('end_header\n')[1].split('\n')[0];
    expect(vertex.endsWith('255 0 0 128')).toBe(true);

    const binary = toPLYBinary(scene);
    const headerEnd = new TextDecoder().decode(binary).indexOf('end_header\n') + 'end_header\n'.length;
    expect(Array.from(binary.slice(headerEnd + 32, headerEnd + 36))).toEqual([255, 0, 0, 128]);
  });
});
