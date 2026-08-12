import { describe, it, expect } from 'vitest';
import { toOBJ } from '../src/obj';
import { SkpScene } from '../src/model';

describe('Wavefront OBJ Exporter', () => {
  it('serializes a SkpScene to OBJ text format', () => {
    const scene: SkpScene = {
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
          geomName: 'Cube',
          materialIndex: 0,
          positions: new Float32Array([0, 0, 0, 1, 0, 0, 0, 1, 0]),
          normals: new Float32Array([0, 0, 1, 0, 0, 1, 0, 0, 1]),
          uvs: new Float32Array([0, 0, 1, 0, 0, 1]),
          indices: new Uint32Array([0, 1, 2]),
        },
      ],
      gltfMaterials: [],
    };

    const objText = toOBJ(scene);
    expect(objText).toContain('# OpenSKP OBJ Export');
    expect(objText).toContain('o Cube');
    expect(objText).toContain('v 0.000000 0.000000 0.000000');
    expect(objText).toContain('f 1 2 3');
  });
});
