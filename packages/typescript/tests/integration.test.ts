import { describe, it, expect } from 'vitest';
import { SkpFile } from '../src/index';
import * as path from 'path';

describe('SketchUp Parser Integration Test', () => {
  it('should parse reference Untitled.skp correctly', () => {
    const filePath = path.join(__dirname, 'fixtures', 'Untitled.skp');
    const skpFile = SkpFile.open(filePath);
    const model = skpFile.parse();

    // 1. Assert Version
    expect(model.version).toBe('{25.0.575}');

    // 2. Assert Layers
    expect(model.layers.length).toBe(14);
    const expectedLayers = [
      'Layer0', 'BottomPlate', 'TopPlate', 'Stud', 'Nog', 'KingStud',
      'HeaderJackStud', 'HeaderPlate1', 'HeaderPlate2', 'SillPlate1',
      'VerticalHeaderStud', 'generic_frame', 'dimension', 'Hat Sections'
    ];
    const parsedLayers = model.layers.map(l => l.name);
    for (const layerName of expectedLayers) {
      expect(parsedLayers).toContain(layerName);
    }
    // Check color fields of a layer
    const firstLayer = model.layers.find(l => l.name === 'Layer0');
    expect(firstLayer).toBeDefined();
    expect(typeof firstLayer!.color.r).toBe('number');
    expect(typeof firstLayer!.color.g).toBe('number');
    expect(typeof firstLayer!.color.b).toBe('number');

    // 3. Assert Materials
    expect(model.materials.length).toBe(15);
    const expectedMaterials = [
      '*', 'Layer_Layer0', 'Layer_BottomPlate', 'Layer_TopPlate', 'Layer_Stud',
      'Layer_Nog', 'Layer_KingStud', 'Layer_HeaderJackStud', 'Layer_HeaderPlate1',
      'Layer_HeaderPlate2', 'Layer_SillPlate1', 'Layer_VerticalHeaderStud',
      'Layer_generic_frame', 'Layer_dimension', 'Layer_Hat Sections'
    ];
    const parsedMaterials = model.materials.map(m => m.name);
    for (const matName of expectedMaterials) {
      expect(parsedMaterials).toContain(matName);
    }
    // Check material fields
    const matLayer0 = model.materials.find(m => m.name === 'Layer_Layer0');
    expect(matLayer0).toBeDefined();
    expect(typeof matLayer0!.color.r).toBe('number');
    expect(typeof matLayer0!.transparency).toBe('number');

    // 4. Assert Definitions
    expect(model.definitions.size).toBe(46);

    // Let's assert details of Definition 66
    const def66 = model.definitions.get(66);
    expect(def66).toBeDefined();
    expect(def66!.name).toBe('Group200#2');
    expect(def66!.guid).toBeDefined();
    expect(def66!.guid.length).toBe(32); // GUID as hex string should be 32 chars

    // 5. Assert Vertices in Definition 66
    expect(def66!.vertices.length).toBe(136);
    const firstVertex = def66!.vertices[0];
    expect(firstVertex).toBeDefined();
    expect(typeof firstVertex.id).toBe('number');
    expect(typeof firstVertex.x).toBe('number');
    expect(typeof firstVertex.y).toBe('number');
    expect(typeof firstVertex.z).toBe('number');

    // 6. Assert Edges in Definition 66
    expect(def66!.edges.length).toBe(158);
    const firstEdge = def66!.edges[0];
    expect(firstEdge).toBeDefined();
    expect(typeof firstEdge.id).toBe('number');
    expect(typeof firstEdge.v1Id).toBe('number');
    expect(typeof firstEdge.v2Id).toBe('number');

    // 7. Assert Faces in Definition 66
    expect(def66!.faces.length).toBe(26);
    const firstFace = def66!.faces[0];
    expect(firstFace).toBeDefined();
    expect(typeof firstFace.id).toBe('number');
    expect(firstFace.loops).toBeInstanceOf(Array);
    expect(firstFace.loops.length).toBeGreaterThan(0);
    // Each loop should be an array of CoEdges
    expect(firstFace.loops[0][0]).toBeDefined();
    expect(typeof firstFace.loops[0][0].edgeId).toBe('number');
    expect(typeof firstFace.loops[0][0].orientation).toBe('number');
    expect(firstFace.normal).toBeDefined();
    expect(firstFace.normal.length).toBe(3);
    expect(typeof firstFace.normal[0]).toBe('number');

    // 7b. Assert the model-parity fields added alongside the Python port
    // (materialId is already-existing baseline data now exposed publicly;
    // the rest default until their dedicated feature lands).
    expect(firstFace.materialId === null || typeof firstFace.materialId === 'number').toBe(true);
    expect(firstFace.backMaterialId).toBeNull();
    expect(firstFace.uvTransform).toBeNull();
    expect(firstFace.uvTransformBack).toBeNull();

    expect(firstEdge.soft).toBe(false);
    expect(firstEdge.smooth).toBe(false);
    expect(firstEdge.hidden).toBe(false);

    expect(Array.isArray(def66!.instances)).toBe(true);
    expect(def66!.isImage).toBe(false);
    expect(def66!.alwaysFacesCamera).toBe(false);

    expect(matLayer0!.id).toBeNull();
    expect(matLayer0!.texture).toBeNull();
    expect(matLayer0!.colorized).toBe(false);
    expect(matLayer0!.colorizeType).toBe(0);

    expect(model.materialsById).toBeInstanceOf(Map);
    expect(Array.isArray(model.styles)).toBe(true);

    // 8. Assert Scene Hierarchy
    expect(model.sceneHierarchy).toBeDefined();
    expect(model.sceneHierarchy.name).toBe('ROOT');
    expect(model.sceneHierarchy.definitionName).toBe('ROOT_MODEL');
    expect(model.sceneHierarchy.children.length).toBeGreaterThan(0);

    // 9. Assert Mesh Index
    const meshNames = Object.keys(model.meshIndex);
    expect(meshNames.length).toBe(43);
    const firstMesh = model.meshIndex[meshNames[0]];
    expect(firstMesh).toBeDefined();
    expect(firstMesh.name).toBeDefined();
    expect(firstMesh.layer).toBeDefined();
    expect(firstMesh.positionMm).toHaveLength(3);
  });

  it('should parse reference SU_File.skp correctly', () => {
    const filePath = path.join(__dirname, 'fixtures', 'SU_File.skp');
    const skpFile = SkpFile.open(filePath);
    const model = skpFile.parse();

    // 1. Assert Version
    expect(model.version).toBe('{25.0.575}');

    // 2. Assert Layers
    expect(model.layers).toHaveLength(1);
    expect(model.layers[0].name).toBe('Layer0');

    // 3. Assert Materials
    expect(model.materials).toHaveLength(1);
    expect(model.materials[0].name).toBe('Layer_Layer0');

    // 4. Assert Definitions (only ROOT, so finalDefinitions is empty)
    expect(model.definitions.size).toBe(0);

    // 5. Assert Scene Hierarchy & Mesh Index
    expect(model.sceneHierarchy).toBeDefined();
    expect(model.sceneHierarchy.name).toBe('ROOT');
    expect(model.sceneHierarchy.definitionName).toBe('ROOT_MODEL');

    const meshNames = Object.keys(model.meshIndex);
    expect(meshNames).toHaveLength(1);
    expect(model.meshIndex[meshNames[0]].definitionName).toBe('ROOT_MODEL');
  });
});
