import { describe, it, expect } from 'vitest';
import { toIFC, METRES_TO_INCHES, classifyElement, generateIFCGUID } from '../src/ifc';
import { SkpScene } from '../src/model';

describe('IFC4 3D Exporter', () => {
  const createMockScene = (): SkpScene => ({
    sceneHierarchy: {
      name: 'Root',
      definitionName: 'RootDef',
      layer: 'Layer0',
      positionMm: [0, 0, 0],
      properties: {},
      children: [],
    },
    meshIndex: {
      'Outer Wall': {
        name: 'Outer Wall',
        definitionName: 'WallDef',
        layer: 'Layer0',
        positionMm: [0, 0, 0],
        properties: { Thickness: '200mm' },
        path: 'Root/Outer Wall',
      },
    },
    glbPrimitives: [
      {
        geomName: 'Outer Wall',
        materialIndex: 0,
        positions: new Float32Array([0, 0, 0, 1, 0, 0, 0, 1, 0]),
        normals: new Float32Array([0, 0, 1, 0, 0, 1, 0, 0, 1]),
        uvs: new Float32Array([0, 0, 1, 0, 0, 1]),
        indices: new Uint32Array([0, 1, 2]),
      },
    ],
    gltfMaterials: [
      {
        pbrMetallicRoughness: {
          baseColorFactor: [0.8, 0.2, 0.2, 1.0],
        },
      },
    ],
  });

  it('generates valid 22-char IFC GUIDs', () => {
    const guid = generateIFCGUID();
    expect(guid).toHaveLength(22);
  });

  it('classifies geometry names into IFC classes', () => {
    expect(classifyElement('Main Wall')[0]).toBe('IFCWALL');
    expect(classifyElement('Front Door')[0]).toBe('IFCDOOR');
    expect(classifyElement('Office Window')[0]).toBe('IFCWINDOW');
    expect(classifyElement('Concrete Slab')[0]).toBe('IFCSLAB');
    expect(classifyElement('Steel Beam')[0]).toBe('IFCBEAM');
  });

  it('serializes SkpScene to IFC4 STEP format', () => {
    const scene = createMockScene();
    const ifcText = toIFC(scene);

    expect(ifcText).toContain('ISO-10303-21;');
    expect(ifcText).toContain('HEADER;');
    expect(ifcText).toContain("FILE_SCHEMA(('IFC4'));");
    expect(ifcText).toContain('IFCPROJECT');
    expect(ifcText).toContain('IFCSITE');
    expect(ifcText).toContain('IFCBUILDING');
    expect(ifcText).toContain('IFCBUILDINGSTOREY');
    expect(ifcText).toContain('IFCWALL');
    expect(ifcText).toContain('IFCTRIANGULATEDFACESET');
    expect(ifcText).toContain('IFCCARTESIANPOINTLIST3D');
    expect(ifcText).toContain('IFCPROPERTYSET');
    expect(ifcText).toContain('ENDSEC;');
  });
});
