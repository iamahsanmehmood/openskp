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

  it('uses MeshMetadata.name instead of internal geomName key and passes raw text to custom classifier', () => {
    const scene: SkpScene = {
      ...createMockScene(),
      meshIndex: {
        mesh_3_ROOT__W1_Layer0: {
          name: "Wände 'A'",
          definitionName: 'WallDef',
          layer: "Außenwände '1'",
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
    };

    const seen: Array<[string, string]> = [];
    const ifcText = toIFC(scene, METRES_TO_MM, 'IFC4', (name, layer) => {
      seen.push([name, layer]);
      return ['IFCWALL', 'IfcWall'];
    });
    expect(seen).toEqual([["Wände 'A'", "Außenwände '1'"]]);
    expect(ifcText).toContain("''A''");
    expect(ifcText).not.toContain('mesh_3_ROOT');
  });

  it('emits IFCPRESENTATIONLAYERWITHSTYLE reflecting scene.layerHidden and extra Pset_<dict>', () => {
    const scene: SkpScene = {
      ...createMockScene(),
      meshIndex: {
        'Outer Wall': {
          name: 'Outer Wall',
          definitionName: 'WallDef',
          layer: 'Hidden Layer',
          positionMm: [0, 0, 0],
          properties: { width: '10.0' },
          attributeDictionaries: {
            'fbd-einfo': { code: 'aPf', angle: '90' },
            empty_dict: {},
          },
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
    };

    const ifcText = toIFC(scene);
    expect(ifcText).toContain('IFCPRESENTATIONLAYERWITHSTYLE');
    expect(ifcText).not.toContain('IFCPRESENTATIONLAYERASSIGNMENT(');
    expect(ifcText).toContain("'Pset_CustomProperties'");
    expect(ifcText).toContain("'Pset_fbd-einfo'");
    expect(ifcText).not.toContain('Pset_empty_dict');

    const lines = ifcText.split(/\r?\n/);
    const hiddenLine = lines.find((l) => l.includes("'Hidden Layer'"))!;
    const visibleLine = lines.find((l) => l.includes("'Visible Layer'"))!;
    expect(hiddenLine).toContain(',.F.,.F.,.F.,())');
    expect(visibleLine).toContain(',.T.,.F.,.F.,())');
  });

  it('supports opt-in full-path keyword classification and preserves end-to-end buildScene metadata', () => {
    const p = 'ROOT / Wall Frame / Stud 12';
    expect(classifyElement('Stud 12', 'Layer0', p)[0]).toBe('IFCBUILDINGELEMENTPROXY');
    expect(classifyElement('Stud 12', 'Layer0', p, true)[0]).toBe('IFCWALL');
    expect(classifyElement('Front Door', 'Layer0', 'ROOT / Wall Frame / Front Door', true)[0]).toBe(
      'IFCDOOR'
    );

    const b = create();
    const hiddenWalls = b.addLayer('HiddenWalls', { color: [200, 100, 100], hidden: true });
    const stud = b.addComponentDefinition('Stud 12', (d) => {
      d.addFace([
        [0, 0, 0],
        [10, 0, 0],
        [10, 10, 0],
        [0, 10, 0],
      ]);
    });
    const wallFrame = b.addComponentDefinition('Wall Frame', (d) => {
      d.addInstance(stud);
    });
    b.addInstance(wallFrame, { layer: hiddenWalls });

    const bytes = b.toBytes();
    const ab = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer;
    const scene = buildScene(ab);
    expect(scene.layerHidden?.['HiddenWalls']).toBe(true);
    expect(scene.layerHidden?.['Layer0']).toBe(false);

    const ifcText = toIFC(scene, METRES_TO_MM, 'IFC4', undefined, true);
    expect(ifcText).toContain('IFCWALL(');
    expect(ifcText).toContain("'Stud 12'");
    expect(ifcText).not.toContain('mesh_0_ROOT');
    // Unpainted faces produce 2 glbPrimitives (front + back) and 1 surfacePrimitive
    expect(ifcText.match(/IFCTRIANGULATEDFACESET\(/g)?.length).toBe(1);

    const untitledBuf = fs.readFileSync(path.join(__dirname, 'fixtures', 'Untitled.skp'));
    const untitledAb = untitledBuf.buffer.slice(
      untitledBuf.byteOffset,
      untitledBuf.byteOffset + untitledBuf.byteLength
    ) as ArrayBuffer;
    const untitledIfc = toIFC(buildScene(untitledAb));
    expect(untitledIfc).toContain("'Pset_steelframer-dict'");
  });

  it('writes a hidden placement tag as hidden in IFC even when all definition faces are on Wood', () => {
    const b = create();
    const wood = b.addLayer('Wood', { color: [160, 110, 60] });
    const furnitureHidden = b.addLayer('FurnitureHidden', { color: [40, 40, 40], hidden: true });
    const woodPanel = b.addComponentDefinition('WoodPanel', (d) => {
      d.addFace([
        [0, 0, 0],
        [2, 0, 0],
        [2, 2, 0],
        [0, 2, 0],
      ], { layer: wood });
    });
    b.addInstance(woodPanel, { name: 'HiddenFurniture', layer: furnitureHidden });

    const bytes = b.toBytes();
    const ab = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer;
    const ifcText = toIFC(buildScene(ab));
    const layerLines = ifcText.split(/\r?\n/).filter((l) => l.includes('IFCPRESENTATIONLAYERWITHSTYLE('));
    const hiddenLines = layerLines.filter((l) => l.includes("'FurnitureHidden'"));
    expect(hiddenLines).toHaveLength(1);
    expect(hiddenLines[0]).toContain(',.F.,');
    expect(layerLines.some((l) => l.includes("'Wood'"))).toBe(false);
  });

  it('exports valid IFC2X3 geometry, arcs, proxies, and roof attributes (#658)', () => {
    const s45 = Math.SQRT1_2;
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
        ClosedCrate: {
          name: 'ClosedCrate',
          definitionName: 'CrateDef',
          layer: 'Layer0',
          positionMm: [0, 0, 0],
          properties: {},
          path: 'Root/ClosedCrate',
        },
        OpenRoof: {
          name: 'Gable Roof',
          definitionName: 'RoofDef',
          layer: 'Layer0',
          positionMm: [0, 0, 0],
          properties: {},
          path: 'Root/OpenRoof',
        },
        FrontDoor: {
          name: 'Front Door',
          definitionName: 'DoorDef',
          layer: 'Layer0',
          positionMm: [0, 0, 0],
          properties: {},
          path: 'Root/FrontDoor',
        },
      },
      glbPrimitives: [
        {
          geomName: 'ClosedCrate',
          materialIndex: 0,
          positions: new Float32Array([0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1]),
          normals: new Float32Array(12),
          uvs: new Float32Array(8),
          indices: new Uint32Array([0, 2, 1, 0, 1, 3, 1, 2, 3, 2, 0, 3]),
        },
        {
          geomName: 'OpenRoof',
          materialIndex: 0,
          positions: new Float32Array([0, 0, 0, 1, 0, 0, 0, 1, 0]),
          normals: new Float32Array(9),
          uvs: new Float32Array(6),
          indices: new Uint32Array([0, 1, 2]),
        },
        {
          geomName: 'FrontDoor',
          materialIndex: 0,
          positions: new Float32Array([2, 0, 0, 3, 0, 0, 2, 1, 0]),
          normals: new Float32Array(9),
          uvs: new Float32Array(6),
          indices: new Uint32Array([0, 1, 2]),
        },
      ],
      gltfMaterials: [{ pbrMetallicRoughness: { baseColorFactor: [0.5, 0.5, 0.5, 1.0] } }],
      curveSets: [
        {
          name: 'QuarterArc',
          layer: 'Layer0',
          pointsM: [
            [1, 0, 0],
            [s45, 0, -s45],
            [0, 0, -1],
          ],
          closed: false,
          arc: {
            points: [
              [1, 0, 0],
              [s45, 0, -s45],
              [0, 0, -1],
            ],
            segments: [[0, 1, 2]],
          },
        },
        {
          name: 'FullCircle',
          layer: 'Layer0',
          pointsM: [
            [1, 0, 0],
            [0, 0, -1],
            [-1, 0, 0],
            [0, 0, 1],
          ],
          closed: true,
          arc: {
            points: [
              [1, 0, 0],
              [0, 0, -1],
              [-1, 0, 0],
              [0, 0, 1],
            ],
            segments: [
              [0, 1, 2],
              [2, 3, 0],
            ],
          },
        },
        {
          name: 'DegenerateArc',
          layer: 'Layer0',
          pointsM: [
            [0, 0, 0],
            [1, 0, 0],
            [2, 0, 0],
          ],
          closed: false,
          arc: {
            points: [
              [0, 0, 0],
              [1, 0, 0],
              [2, 0, 0],
            ],
            segments: [[0, 1, 2]],
          },
        },
      ],
    };

    const ifcText = toIFC(scene, METRES_TO_MM, 'IFC2X3');
    expect(ifcText).toContain("FILE_SCHEMA(('IFC2X3'));");
    expect(ifcText).not.toContain('IFCTRIANGULATEDFACESET(');
    expect(ifcText).not.toContain('IFCCARTESIANPOINTLIST3D(');
    expect(ifcText).not.toContain('IFCINDEXEDPOLYCURVE(');
    expect(ifcText).not.toContain('IFCARCINDEX(');

    expect(ifcText).toContain('IFCCLOSEDSHELL(');
    expect(ifcText).toContain('IFCFACETEDBREP(');
    expect(ifcText).toContain("'Body','Brep',");
    expect(ifcText).toContain('IFCCONNECTEDFACESET(');
    expect(ifcText).toContain('IFCFACEBASEDSURFACEMODEL(');
    expect(ifcText).toContain("'Body','SurfaceModel',");

    expect(ifcText.match(/IFCCIRCLE\(/g)?.length).toBe(2);
    expect(ifcText.match(/IFCTRIMMEDCURVE\(/g)?.length).toBe(1);
    expect(ifcText).toContain('IFCPARAMETERVALUE(1.570796)');
    expect(ifcText.match(/IFCPOLYLINE\(/g)?.length).toBe(1);

    const lines = ifcText.split(/\r?\n/);
    const proxyLine = lines.find((l) => l.includes('=IFCBUILDINGELEMENTPROXY('))!;
    expect(proxyLine.endsWith(',.ELEMENT.);')).toBe(true);
    const roofLine = lines.find((l) => l.includes('=IFCROOF('))!;
    expect(roofLine.endsWith(',.NOTDEFINED.);')).toBe(true);
    const doorLine = lines.find((l) => l.includes('=IFCDOOR('))!;
    const doorBody = doorLine.slice(doorLine.indexOf('(') + 1, doorLine.lastIndexOf(')'));
    expect(doorBody.split(',').length).toBe(10);

    const ids = lines
      .filter((l) => l.startsWith('#') && l.includes('='))
      .map((l) => Number(l.slice(1, l.indexOf('='))));
    expect(ids).toEqual(Array.from({ length: ids.length }, (_, i) => i + 1));
  });
});
