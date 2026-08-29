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

  it('falls back to the layer name when the component name has no keyword', () => {
    // SketchUp default names carry no signal, but a BIM-style layer/tag
    // often does (openskp#238).
    expect(classifyElement('Component#109415', 'Walls')[0]).toBe('IFCWALL');
    expect(classifyElement('Group#3', 'Doors')[0]).toBe('IFCDOOR');
  });

  it('prefers the component name over the layer name when both match', () => {
    expect(classifyElement('Interior Door', 'Walls')[0]).toBe('IFCDOOR');
  });

  it('falls back to the generic proxy when neither name matches', () => {
    expect(classifyElement('Component#109415', 'Layer0')[0]).toBe('IFCBUILDINGELEMENTPROXY');
    expect(classifyElement('Component#109415')[0]).toBe('IFCBUILDINGELEMENTPROXY');
  });

  it('uses the layer name fallback in toIFC for unnamed components', () => {
    const scene: SkpScene = {
      sceneHierarchy: {
        name: 'Root',
        definitionName: 'RootDef',
        layer: 'Layer0',
        positionMm: [0, 0, 0],
        properties: {},
        children: [],
      },
      meshIndex: {
        'Component#109415': {
          name: 'Component#109415',
          definitionName: 'CompDef',
          layer: 'Walls',
          positionMm: [0, 0, 0],
          properties: {},
          path: 'Root/Component#109415',
        },
      },
      glbPrimitives: [
        {
          geomName: 'Component#109415',
          materialIndex: 0,
          positions: new Float32Array([0, 0, 0, 1, 0, 0, 0, 1, 0]),
          normals: new Float32Array([0, 0, 1, 0, 0, 1, 0, 0, 1]),
          uvs: new Float32Array([0, 0, 1, 0, 0, 1]),
          indices: new Uint32Array([0, 1, 2]),
        },
      ],
      gltfMaterials: [{}],
    };
    const ifcText = toIFC(scene);
    expect(ifcText).toContain('IFCWALL(');
    expect(ifcText).not.toContain('IFCBUILDINGELEMENTPROXY');
  });

  it('accepts a custom classifier override in toIFC', () => {
    const scene = createMockScene();
    const alwaysColumn = (): [string, string] => ['IFCCOLUMN', 'IfcColumn'];
    const ifcText = toIFC(scene, METRES_TO_INCHES, 'IFC4', alwaysColumn);
    expect(ifcText).not.toContain('IFCWALL(');
    expect(ifcText).toContain('IFCCOLUMN(');
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
