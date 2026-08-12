import { describe, it, expect } from 'vitest';
import { toSTLAscii, toSTLBinary } from '../src/stl';
import { SkpScene } from '../src/model';

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
});
