import { describe, it, expect } from 'vitest';
import * as fs from 'fs';
import * as path from 'path';
import { toIFC, METRES_TO_INCHES, METRES_TO_MM, classifyElement, generateIFCGUID } from '../src/ifc';
import { SkpScene } from '../src/model';
import { buildScene, create } from '../src/index';

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

  it('declares millimetres and scales coordinates to match by default', () => {
    const scene = createMockScene();
    scene.glbPrimitives[0].positions = new Float32Array([0, 0, 0, 1, 0, 0, 0, 0, 1]);
    const ifcText = toIFC(scene);

    expect(ifcText).toContain('IFCSIUNIT(*,.LENGTHUNIT.,.MILLI.,.METRE.)');
    expect(ifcText).toContain('(1000.000000,0.000000,0.000000)');
    expect(ifcText).toContain('(0.000000,-1000.000000,0.000000)');
  });

  it('converts glTF Y-up positions back to IFC Z-up', () => {
    const scene = createMockScene();
    scene.glbPrimitives[0].positions = new Float32Array([0, 0, 0, 2, 3, 5, 0, 1, 0]);
    const ifcText = toIFC(scene, 1.0);

    expect(ifcText).toContain('(2.000000,-5.000000,3.000000)');
    expect(ifcText).not.toContain('(2.000000,3.000000,5.000000)');
  });

  it('uses MeshMetadata.name instead of internal geomName key', () => {
    const scene: SkpScene = {
      ...createMockScene(),
      meshIndex: {
        mesh_3_ROOT__W1_Layer0: {
          name: 'W1',
          definitionName: 'WallDef',
          layer: 'Layer0',
          positionMm: [0, 0, 0],
          properties: {},
          attributeDictionaries: {},
          path: 'ROOT / W1',
        },
      },
      glbPrimitives: [
        {
          geomName: 'mesh_3_ROOT__W1_Layer0',
          materialIndex: 0,
          positions: new Float32Array([0, 0, 0, 1, 0, 0, 0, 1, 0]),
          normals: new Float32Array([0, 0, 1, 0, 0, 1, 0, 0, 1]),
          uvs: new Float32Array([0, 0, 1, 0, 0, 1]),
          indices: new Uint32Array([0, 1, 2]),
        },
      ],
    } as SkpScene;

    const ifcText = toIFC(scene);
    expect(ifcText).toContain("'W1'");
    expect(ifcText).not.toContain('mesh_3_ROOT');
  });

  it('emits IFCPRESENTATIONLAYERWITHSTYLE reflecting scene.layerHidden', () => {
    const scene: SkpScene = {
      ...createMockScene(),
      meshIndex: {
        'Outer Wall': {
          name: 'Outer Wall',
          definitionName: 'WallDef',
          layer: 'Hidden Layer',
          positionMm: [0, 0, 0],
          properties: {},
          attributeDictionaries: {},
          path: 'ROOT / Outer Wall',
        },
        'Front Door': {
          name: 'Front Door',
          definitionName: 'DoorDef',
          layer: 'Visible Layer',
          positionMm: [0, 0, 0],
          properties: {},
          attributeDictionaries: {},
          path: 'ROOT / Front Door',
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
        {
          geomName: 'Front Door',
          materialIndex: 0,
          positions: new Float32Array([2, 0, 0, 3, 0, 0, 2, 1, 0]),
          normals: new Float32Array([0, 0, 1, 0, 0, 1, 0, 0, 1]),
          uvs: new Float32Array([0, 0, 1, 0, 0, 1]),
          indices: new Uint32Array([0, 1, 2]),
        },
      ],
      layerHidden: { 'Hidden Layer': true, 'Visible Layer': false },
    } as SkpScene;

    const ifcText = toIFC(scene);
    expect(ifcText).toContain('IFCPRESENTATIONLAYERWITHSTYLE');
    expect(ifcText).not.toContain('IFCPRESENTATIONLAYERASSIGNMENT(');
    const lines = ifcText.split(/\r?\n/);
    const hiddenLine = lines.find((l) => l.includes("'Hidden Layer'")) ?? '';
    const visibleLine = lines.find((l) => l.includes("'Visible Layer'")) ?? '';
    expect(hiddenLine).toContain(',.F.,.F.,.F.,())');
    expect(visibleLine).toContain(',.T.,.F.,.F.,())');
  });

  it('writes a separate Pset_<dict> per extra attribute dictionary and skips empty dicts', () => {
    const scene: SkpScene = {
      ...createMockScene(),
      meshIndex: {
        mesh_0_ROOT__Profile25_Layer0: {
          name: 'Profile25',
          definitionName: 'ProfileDef',
          layer: 'Layer0',
          positionMm: [0, 0, 0],
          properties: { width: '10.0' },
          attributeDictionaries: {
            'fbd-einfo': { code: 'aPf', angle: '90' },
            empty_dict: {},
          },
          path: 'ROOT / Profile25',
        },
      },
      glbPrimitives: [
        {
          geomName: 'mesh_0_ROOT__Profile25_Layer0',
          materialIndex: 0,
          positions: new Float32Array([0, 0, 0, 1, 0, 0, 0, 1, 0]),
          normals: new Float32Array([0, 0, 1, 0, 0, 1, 0, 0, 1]),
          uvs: new Float32Array([0, 0, 1, 0, 0, 1]),
          indices: new Uint32Array([0, 1, 2]),
        },
      ],
    } as SkpScene;

    const ifcText = toIFC(scene);
    expect(ifcText).toContain("'Pset_CustomProperties'");
    expect(ifcText).toContain("'Pset_fbd-einfo'");
    expect(ifcText).toContain("'width'");
    expect(ifcText).toContain("'code'");
    expect(ifcText).toContain("'aPf'");
    expect(ifcText).toContain("'angle'");
    expect(ifcText).toContain("'90'");
    expect(ifcText).not.toContain('Pset_empty_dict');
  });

  it('supports opt-in full-path keyword classification without corrupting displayed Name', () => {
    const path = 'ROOT / Wall Frame / Stud 12';
    expect(classifyElement('Stud 12', 'Layer0', path)[0]).toBe('IFCBUILDINGELEMENTPROXY');
    expect(classifyElement('Stud 12', 'Layer0', path, true)[0]).toBe('IFCWALL');
    expect(classifyElement('Front Door', 'Layer0', 'ROOT / Wall Frame / Front Door', true)[0]).toBe(
      'IFCDOOR'
    );

    const scene: SkpScene = {
      ...createMockScene(),
      meshIndex: {
        mesh_0_ROOT__Wall_Frame__Stud_12_Layer0: {
          name: 'Stud 12',
          definitionName: 'StudDef',
          layer: 'Layer0',
          positionMm: [0, 0, 0],
          properties: {},
          attributeDictionaries: {},
          path,
        },
      },
      glbPrimitives: [
        {
          geomName: 'mesh_0_ROOT__Wall_Frame__Stud_12_Layer0',
          materialIndex: 0,
          positions: new Float32Array([0, 0, 0, 1, 0, 0, 0, 1, 0]),
          normals: new Float32Array([0, 0, 1, 0, 0, 1, 0, 0, 1]),
          uvs: new Float32Array([0, 0, 1, 0, 0, 1]),
          indices: new Uint32Array([0, 1, 2]),
        },
      ],
    } as SkpScene;

    const defaultText = toIFC(scene);
    expect(defaultText).toContain('IFCBUILDINGELEMENTPROXY');
    expect(defaultText).not.toContain('IFCWALL(');

    const optedInText = toIFC(scene, METRES_TO_MM, 'IFC4', undefined, true);
    expect(optedInText).toContain('IFCWALL(');
    expect(optedInText).toContain("'Stud 12'");
  });

  it('preserves definition display names, hidden layers, and plugin dicts end-to-end through buildScene', () => {
    const builder = create();
    const hiddenWalls = builder.addLayer('HiddenWalls', { color: [200, 100, 100], hidden: true });
    const stud = builder.addComponentDefinition('Stud 12', (def) => {
      def.addFace([
        [0, 0, 0],
        [10, 0, 0],
        [10, 10, 0],
        [0, 10, 0],
      ]);
    });
    const wallFrame = builder.addComponentDefinition('Wall Frame', (def) => {
      def.addInstance(stud);
    });
    builder.addInstance(wallFrame, { layer: hiddenWalls });

    const bytes = builder.toBytes();
    const buf = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength);
    const scene = buildScene(buf);

    expect(scene.layerHidden['HiddenWalls']).toBe(true);
    const ifcText = toIFC(scene, METRES_TO_MM, 'IFC4', undefined, true);
    expect(ifcText).toContain('IFCWALL(');
    expect(ifcText).toContain("'Stud 12'");
    expect(ifcText).not.toContain('mesh_0_ROOT');
    const hiddenLine = ifcText.split(/\r?\n/).find((l) => l.includes("'HiddenWalls'")) ?? '';
    expect(hiddenLine).toContain(',.F.,.F.,.F.,())');

    const untitledBuf = fs.readFileSync(path.join(__dirname, 'fixtures', 'Untitled.skp'));
    const untitledScene = buildScene(
      untitledBuf.buffer.slice(
        untitledBuf.byteOffset,
        untitledBuf.byteOffset + untitledBuf.byteLength
      )
    );
    const untitledIfc = toIFC(untitledScene);
    expect(untitledIfc).toContain("'Pset_steelframer-dict'");
  });
});
