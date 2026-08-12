import { describe, it, expect } from 'vitest';
import { toDXF, METRES_TO_INCHES } from '../src/dxf';
import { SkpScene } from '../src/model';

describe('DXF 3D Exporter', () => {
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
        geomName: 'Walls',
        materialIndex: 0,
        positions: new Float32Array([0, 0, 0, 1, 0, 0, 0, 1, 0]),
        normals: new Float32Array([0, 0, 1, 0, 0, 1, 0, 0, 1]),
        uvs: new Float32Array([0, 0, 1, 0, 0, 1]),
        indices: new Uint32Array([0, 1, 2]),
      },
    ],
    gltfMaterials: [],
  });

  it('serializes SkpScene to 3D DXF format in polyface mode by default', () => {
    const scene = createMockScene();
    const dxfText = toDXF(scene);
    expect(dxfText).toContain('$ACADVER');
    expect(dxfText).toContain('AC1015');
    expect(dxfText).toContain('POLYLINE');
    expect(dxfText).toContain('AcDbPolyFaceMesh');
    expect(dxfText).toContain('Walls');
    expect(dxfText).toContain('EOF');
  });

  it('serializes SkpScene to 3DFACE format when mode is 3dface', () => {
    const scene = createMockScene();
    const dxfText = toDXF(scene, METRES_TO_INCHES, '3dface');
    expect(dxfText).toContain('3DFACE');
    expect(dxfText).toContain('AcDbFace');
  });
});
