import { describe, it, expect } from 'vitest';
import * as fs from 'fs';
import * as path from 'path';
import * as fflate from 'fflate';
import * as flatbuffers from 'flatbuffers';
import { SingleThreadedFragmentsModel } from '@thatopen/fragments';
import {
  toFragments,
  buildInstancedScene,
  buildInstancedSceneFromParsed,
  SkpFile,
  InstancedScene,
  InstancedNode,
  InstancedMeshResource,
  LocalPrimitive,
} from '../src/index';
import {
  decomposeTrs,
  bakePrimitive,
  scaleCacheKey,
} from '../src/fragments';
import {
  Model,
  Meshes,
  Shell,
  ShellType,
  Transform,
  RepresentationClass,
  FloatVector,
  DoubleVector,
} from '../src/_fragments_fb/index';
import { repeatedComponentScene } from './helpers/instanced-fixtures';

const IDENTITY = [
  1.0, 0.0, 0.0, 0.0,
  0.0, 1.0, 0.0, 0.0,
  0.0, 0.0, 1.0, 0.0,
  0.0, 0.0, 0.0, 1.0,
];

function rotateZMatrix(radians: number, translation: [number, number, number] = [0, 0, 0]): number[] {
  const c = Math.cos(radians);
  const s = Math.sin(radians);
  return [
    c, s, 0.0, 0.0,
    -s, c, 0.0, 0.0,
    0.0, 0.0, 1.0, 0.0,
    translation[0], translation[1], translation[2], 1.0,
  ];
}

function diagMatrix(sx: number, sy: number, sz: number, translation: [number, number, number] = [0, 0, 0]): number[] {
  return [
    sx, 0.0, 0.0, 0.0,
    0.0, sy, 0.0, 0.0,
    0.0, 0.0, sz, 0.0,
    translation[0], translation[1], translation[2], 1.0,
  ];
}

function boxPrimitive(materialIndex = 0): LocalPrimitive {
  const positions = new Float32Array([
    0, 0, 0,  1, 0, 0,  1, 1, 0,  0, 1, 0,
    0, 0, 1,  1, 0, 1,  1, 1, 1,  0, 1, 1,
  ]);
  const normals = new Float32Array(24);
  const uvs = new Float32Array(16);
  const indices = new Uint32Array([
    0, 1, 2, 0, 2, 3,  // bottom
    4, 6, 5, 4, 7, 6,  // top
    0, 4, 5, 0, 5, 1,  // side
    1, 5, 6, 1, 6, 2,  // side
    2, 6, 7, 2, 7, 3,  // side
    3, 7, 4, 3, 4, 0,  // side
  ]);
  return { positions, normals, uvs, indices, materialIndex };
}

function makeTwoInstanceScene(secondMatrix?: number[]): InstancedScene {
  const m2 = secondMatrix ?? rotateZMatrix((45 * Math.PI) / 180, [5.0, 0.0, 0.0]);
  const resource: InstancedMeshResource = {
    id: 'mesh_0',
    definitionId: 1,
    definitionName: 'Box',
    variantKey: '1|255,255,255',
    primitives: [boxPrimitive()],
  };
  const nodeA: InstancedNode = {
    name: 'Box_A',
    nameIsGenerated: false,
    definitionName: 'Box',
    layer: 'Framing',
    matrix: IDENTITY,
    positionMm: [0, 0, 0],
    properties: {},
    guid: '',
    meshResourceId: 'mesh_0',
    children: [],
  };
  const nodeB: InstancedNode = {
    name: 'Box_B',
    nameIsGenerated: false,
    definitionName: 'Box',
    layer: 'Framing',
    matrix: m2,
    positionMm: [5000, 0, 0],
    properties: {},
    guid: '',
    meshResourceId: 'mesh_0',
    children: [],
  };
  const root: InstancedNode = {
    name: 'ROOT',
    nameIsGenerated: false,
    definitionName: 'ROOT_MODEL',
    layer: 'Layer0',
    matrix: IDENTITY,
    positionMm: [0, 0, 0],
    properties: {},
    guid: '',
    children: [nodeA, nodeB],
  };

  return {
    bounds: null,
    sceneHierarchy: root,
    meshResources: [resource],
    gltfMaterials: [{ pbrMetallicRoughness: { baseColorFactor: [0.8, 0.2, 0.2, 1.0] } }],
    textures: [],
    layerHidden: {},
  };
}

function makeOneInstanceScene(matrix: number[], name = 'Instance'): InstancedScene {
  const resource: InstancedMeshResource = {
    id: 'mesh_0',
    definitionId: 1,
    definitionName: 'Box',
    variantKey: '1|x',
    primitives: [boxPrimitive()],
  };
  const node: InstancedNode = {
    name,
    nameIsGenerated: false,
    definitionName: 'Box',
    layer: 'Layer0',
    matrix,
    positionMm: [0, 0, 0],
    properties: {},
    guid: '',
    meshResourceId: 'mesh_0',
    children: [],
  };
  const root: InstancedNode = {
    name: 'ROOT',
    nameIsGenerated: false,
    definitionName: 'ROOT_MODEL',
    layer: 'Layer0',
    matrix: IDENTITY,
    positionMm: [0, 0, 0],
    properties: {},
    guid: '',
    children: [node],
  };
  return {
    bounds: null,
    sceneHierarchy: root,
    meshResources: [resource],
    gltfMaterials: [{ pbrMetallicRoughness: { baseColorFactor: [1, 1, 1, 1] } }],
    textures: [],
    layerHidden: {},
  };
}

describe('toFragments FlatBuffers Schema & Runtime Verification', () => {
  it('produces a loadable model with schema identifier 0001 and correct item count', () => {
    const scene = makeTwoInstanceScene();
    const rawBytes = toFragments(scene, { raw: true });

    const bb = new flatbuffers.ByteBuffer(rawBytes);
    expect(Model.bufferHasIdentifier(bb)).toBe(true);

    const model = Model.getRootAsModel(bb);
    expect(model.localIdsLength()).toBe(2);
    expect(model.categoriesLength()).toBe(2);
    expect(model.categories(0)).toBe('Framing');
    expect(model.categories(1)).toBe('Framing');
    expect(model.maxLocalId()).toBe(2);
  });

  it('deduplicates shared geometry across instances into one Shell and one Representation', () => {
    const scene = makeTwoInstanceScene();
    const rawBytes = toFragments(scene, { raw: true });

    const bb = new flatbuffers.ByteBuffer(rawBytes);
    const model = Model.getRootAsModel(bb);
    const meshes = model.meshes()!;

    expect(meshes.shellsLength()).toBe(1);
    expect(meshes.materialsLength()).toBe(1);
    expect(meshes.representationsLength()).toBe(1);
    // Two placements each get their own sample
    expect(meshes.samplesLength()).toBe(2);

    // Verify sample 0 and sample 1 point to representation 0
    const sample0 = meshes.samples(0)!;
    const sample1 = meshes.samples(1)!;
    expect(sample0.representation()).toBe(0);
    expect(sample1.representation()).toBe(0);

    // Representation id points to shell 0
    const repr = meshes.representations(0)!;
    expect(repr.id()).toBe(0);
    expect(repr.representationClass()).toBe(RepresentationClass.SHELL);
  });

  it('global transforms reflect placement and have normalized unit direction vectors', () => {
    const scene = makeTwoInstanceScene();
    const rawBytes = toFragments(scene, { raw: true });

    const bb = new flatbuffers.ByteBuffer(rawBytes);
    const model = Model.getRootAsModel(bb);
    const meshes = model.meshes()!;

    expect(meshes.globalTransformsLength()).toBe(2);

    const t0 = meshes.globalTransforms(0)!;
    const p0 = t0.position()!;
    expect(p0.x()).toBeCloseTo(0.0);
    expect(p0.y()).toBeCloseTo(0.0);
    expect(p0.z()).toBeCloseTo(0.0);

    const t1 = meshes.globalTransforms(1)!;
    const p1 = t1.position()!;
    expect(p1.x()).toBeCloseTo(5.0);

    const x1 = t1.xDirection()!;
    const y1 = t1.yDirection()!;
    expect(Math.hypot(x1.x(), x1.y(), x1.z())).toBeCloseTo(1.0, 5);
    expect(Math.hypot(y1.x(), y1.y(), y1.z())).toBeCloseTo(1.0, 5);
    expect(x1.x()).toBeCloseTo(Math.cos((45 * Math.PI) / 180), 5);
    expect(x1.y()).toBeCloseTo(Math.sin((45 * Math.PI) / 180), 5);
  });

  it('guids and localIds have matching lengths and parity', () => {
    const scene = makeTwoInstanceScene();
    const rawBytes = toFragments(scene, { raw: true });

    const bb = new flatbuffers.ByteBuffer(rawBytes);
    const model = Model.getRootAsModel(bb);

    const n = model.localIdsLength();
    expect(n).toBe(2);
    expect(model.guidsLength()).toBe(n);
    expect(model.guidsItemsLength()).toBe(n);

    const guids = [];
    for (let i = 0; i < n; i++) {
      expect(model.guidsItems(i)).toBe(model.localIds(i));
      const g = model.guids(i);
      expect(g).toBeTruthy();
      guids.push(g);
    }
    // All GUIDs are distinct
    expect(new Set(guids).size).toBe(n);
  });

  it('converts material colors accurately from gltfMaterials', () => {
    const scene = makeTwoInstanceScene();
    const rawBytes = toFragments(scene, { raw: true });

    const bb = new flatbuffers.ByteBuffer(rawBytes);
    const model = Model.getRootAsModel(bb);
    const meshes = model.meshes()!;

    const mat = meshes.materials(0)!;
    // [0.8, 0.2, 0.2, 1.0] -> [204, 51, 51, 255]
    expect(mat.r()).toBe(Math.round(0.8 * 255));
    expect(mat.g()).toBe(Math.round(0.2 * 255));
    expect(mat.b()).toBe(Math.round(0.2 * 255));
    expect(mat.a()).toBe(255);
  });

  it('compresses output with zlib deflate by default, matching raw upon decompression', () => {
    const scene = makeTwoInstanceScene();
    const compressed = toFragments(scene);
    const raw = toFragments(scene, { raw: true });

    expect(compressed).toBeInstanceOf(Uint8Array);
    const decompressed = fflate.unzlibSync(compressed);
    expect(decompressed).toEqual(raw);

    // RFC 1950 zlib header verification
    expect(compressed[0] & 0x0f).toBe(8); // DEFLATE
    expect((compressed[0] << 8 | compressed[1]) % 31).toBe(0);
  });
});

describe('TRS Decomposition and Scale/Mirror Baking', () => {
  it('decomposeTrs correctly separates translation, rotation, scale, and mirror', () => {
    // Identity
    const trsId = decomposeTrs(IDENTITY);
    expect(trsId.scale).toEqual([1, 1, 1]);
    expect(trsId.mirrored).toBe(false);
    expect(trsId.position).toEqual([0, 0, 0]);

    // Scaled + translated
    const mScale = diagMatrix(2, 3, 4, [10, 20, 30]);
    const trsScale = decomposeTrs(mScale);
    expect(trsScale.scale).toEqual([2, 3, 4]);
    expect(trsScale.mirrored).toBe(false);
    expect(trsScale.position).toEqual([10, 20, 30]);
    expect(trsScale.xDir).toEqual([1, 0, 0]);
    expect(trsScale.yDir).toEqual([0, 1, 0]);

    // Mirrored (det < 0)
    const mMirror = diagMatrix(-1, 1, 1);
    const trsMirror = decomposeTrs(mMirror);
    expect(trsMirror.scale).toEqual([1, 1, 1]);
    expect(trsMirror.mirrored).toBe(true);
    // Local X direction inverted so it forms a right-handed basis
    expect(trsMirror.xDir).toEqual([1, 0, 0]);
  });

  it('bakePrimitive scales local coordinates and flips winding when mirrored', () => {
    const prim = boxPrimitive();

    // Normal scale
    const bakedScale = bakePrimitive(prim, [2, 3, 4], false);
    expect(bakedScale.points.length).toBe(8);
    expect(bakedScale.points[1]).toEqual([2, 0, 0]);
    expect(bakedScale.triangles[0]).toEqual([0, 1, 2]);

    // Mirrored scale
    const bakedMirror = bakePrimitive(prim, [1, 1, 1], true);
    expect(bakedMirror.points[1]).toEqual([-1, 0, 0]);
    // [0, 1, 2] -> [0, 2, 1] reversed winding
    expect(bakedMirror.triangles[0]).toEqual([0, 2, 1]);
  });

  it('scaleCacheKey produces stable keys folding mirror into X', () => {
    const k1 = scaleCacheKey(false, [2, 3, 4]);
    expect(k1).toBe('2_3_4');

    const k2 = scaleCacheKey(true, [2, 3, 4]);
    expect(k2).toBe('-2_3_4');
  });

  it('uniform scale bakes into geometry, keeping transform direction vectors unit length', () => {
    const scaled = diagMatrix(2.0, 2.0, 2.0);
    const scene = makeOneInstanceScene(scaled);
    const rawBytes = toFragments(scene, { raw: true });

    const bb = new flatbuffers.ByteBuffer(rawBytes);
    const model = Model.getRootAsModel(bb);
    const meshes = model.meshes()!;

    // Transform directions remain normalized unit vectors
    const xDir = meshes.globalTransforms(0)!.xDirection()!;
    expect(Math.hypot(xDir.x(), xDir.y(), xDir.z())).toBeCloseTo(1.0, 5);

    // Shell geometry has scale baked in: point 1 (1, 0, 0) becomes (2, 0, 0)
    const shell = meshes.shells(0)!;
    const pt = shell.points(1)!;
    expect(pt.x()).toBeCloseTo(2.0);
    expect(pt.y()).toBeCloseTo(0.0);
    expect(pt.z()).toBeCloseTo(0.0);
  });

  it('non-uniform scale bakes per-axis into geometry', () => {
    const scaled = diagMatrix(2.0, 1.0, 0.5);
    const scene = makeOneInstanceScene(scaled);
    const rawBytes = toFragments(scene, { raw: true });

    const bb = new flatbuffers.ByteBuffer(rawBytes);
    const model = Model.getRootAsModel(bb);
    const shell = model.meshes()!.shells(0)!;

    // Point 6 is (1, 1, 1) in boxPrimitive
    const pt = shell.points(6)!;
    expect(pt.x()).toBeCloseTo(2.0);
    expect(pt.y()).toBeCloseTo(1.0);
    expect(pt.z()).toBeCloseTo(0.5);
  });

  it('mirrored instance negates local X and reverses triangle winding', () => {
    const mirrored = diagMatrix(-1.0, 1.0, 1.0);
    const scene = makeOneInstanceScene(mirrored);
    const rawBytes = toFragments(scene, { raw: true });

    const bb = new flatbuffers.ByteBuffer(rawBytes);
    const model = Model.getRootAsModel(bb);
    const shell = model.meshes()!.shells(0)!;

    // Point 1 (1, 0, 0) becomes (-1, 0, 0)
    const pt = shell.points(1)!;
    expect(pt.x()).toBeCloseTo(-1.0);
    expect(pt.y()).toBeCloseTo(0.0);
    expect(pt.z()).toBeCloseTo(0.0);

    // Profile 0 winding is reversed: (0, 1, 2) -> (0, 2, 1)
    const profile = shell.profiles(0)!;
    expect(profile.indices(0)).toBe(0);
    expect(profile.indices(1)).toBe(2);
    expect(profile.indices(2)).toBe(1);
  });

  it('instances sharing the same scale deduplicate to one baked Shell', () => {
    const resource: InstancedMeshResource = {
      id: 'mesh_0',
      definitionId: 1,
      definitionName: 'Box',
      variantKey: '1|x',
      primitives: [boxPrimitive()],
    };
    const nodeA: InstancedNode = {
      name: 'A',
      definitionName: 'Box',
      layer: 'Layer0',
      matrix: diagMatrix(2.0, 2.0, 2.0, [0, 0, 0]),
      positionMm: [0, 0, 0],
      properties: {},
      meshResourceId: 'mesh_0',
      children: [],
    };
    const nodeB: InstancedNode = {
      name: 'B',
      definitionName: 'Box',
      layer: 'Layer0',
      matrix: diagMatrix(2.0, 2.0, 2.0, [10, 0, 0]),
      positionMm: [10000, 0, 0],
      properties: {},
      meshResourceId: 'mesh_0',
      children: [],
    };
    const root: InstancedNode = {
      name: 'ROOT',
      definitionName: 'ROOT',
      layer: 'Layer0',
      matrix: IDENTITY,
      positionMm: [0, 0, 0],
      properties: {},
      children: [nodeA, nodeB],
    };
    const scene: InstancedScene = {
      bounds: null,
      sceneHierarchy: root,
      meshResources: [resource],
      gltfMaterials: [{ pbrMetallicRoughness: { baseColorFactor: [1, 1, 1, 1] } }],
      textures: [],
    };

    const rawBytes = toFragments(scene, { raw: true });
    const bb = new flatbuffers.ByteBuffer(rawBytes);
    const model = Model.getRootAsModel(bb);

    expect(model.meshes()!.shellsLength()).toBe(1);
    expect(model.meshes()!.samplesLength()).toBe(2);
  });

  it('instances with different scales produce separate baked shells', () => {
    const resource: InstancedMeshResource = {
      id: 'mesh_0',
      definitionId: 1,
      definitionName: 'Box',
      variantKey: '1|x',
      primitives: [boxPrimitive()],
    };
    const nodeA: InstancedNode = {
      name: 'A',
      definitionName: 'Box',
      layer: 'Layer0',
      matrix: diagMatrix(1.0, 1.0, 1.0),
      positionMm: [0, 0, 0],
      properties: {},
      meshResourceId: 'mesh_0',
      children: [],
    };
    const nodeB: InstancedNode = {
      name: 'B',
      definitionName: 'Box',
      layer: 'Layer0',
      matrix: diagMatrix(3.0, 3.0, 3.0),
      positionMm: [0, 0, 0],
      properties: {},
      meshResourceId: 'mesh_0',
      children: [],
    };
    const root: InstancedNode = {
      name: 'ROOT',
      definitionName: 'ROOT',
      layer: 'Layer0',
      matrix: IDENTITY,
      positionMm: [0, 0, 0],
      properties: {},
      children: [nodeA, nodeB],
    };
    const scene: InstancedScene = {
      bounds: null,
      sceneHierarchy: root,
      meshResources: [resource],
      gltfMaterials: [{ pbrMetallicRoughness: { baseColorFactor: [1, 1, 1, 1] } }],
      textures: [],
    };

    const rawBytes = toFragments(scene, { raw: true });
    const bb = new flatbuffers.ByteBuffer(rawBytes);
    const model = Model.getRootAsModel(bb);

    expect(model.meshes()!.shellsLength()).toBe(2);
  });
});

describe('Metadata and Attributes', () => {
  it('attributes carry item display names matching IfcImporter convention', () => {
    const scene = makeTwoInstanceScene();
    const rawBytes = toFragments(scene, { raw: true });

    const bb = new flatbuffers.ByteBuffer(rawBytes);
    const model = Model.getRootAsModel(bb);

    expect(model.attributesLength()).toBe(2);

    const attr0 = model.attributes(0)!;
    expect(attr0.dataLength()).toBe(1);
    expect(JSON.parse(attr0.data(0))).toEqual(['Name', 'Box_A', 'STRING']);

    const attr1 = model.attributes(1)!;
    expect(attr1.dataLength()).toBe(1);
    expect(JSON.parse(attr1.data(0))).toEqual(['Name', 'Box_B', 'STRING']);
  });

  it('metadata preserves layer_hidden dictionary', () => {
    const scene = makeTwoInstanceScene();
    scene.layerHidden = { Framing: false, Cladding: true };
    const rawBytes = toFragments(scene, { raw: true });

    const bb = new flatbuffers.ByteBuffer(rawBytes);
    const model = Model.getRootAsModel(bb);
    const meta = JSON.parse(model.metadata()!);
    expect(meta.layer_hidden).toEqual({ Framing: false, Cladding: true });
  });

  it('items without a source guid get a unique synthetic one', () => {
    const scene = makeTwoInstanceScene();
    const rawBytes = toFragments(scene, { raw: true });

    const bb = new flatbuffers.ByteBuffer(rawBytes);
    const model = Model.getRootAsModel(bb);
    const guids: string[] = [];
    for (let i = 0; i < model.guidsLength(); i++) {
      const g = model.guids(i)!;
      expect(g.length).toBeGreaterThan(0);
      guids.push(g);
    }
    expect(new Set(guids).size).toBe(guids.length);
  });

  it('a real source guid is preserved exactly', () => {
    const scene = makeTwoInstanceScene();
    scene.sceneHierarchy.children[0].guid = 'F160C36229782F47A9857FC88DD1F2CB';
    const rawBytes = toFragments(scene, { raw: true });

    const bb = new flatbuffers.ByteBuffer(rawBytes);
    const model = Model.getRootAsModel(bb);
    expect(model.guids(0)).toBe('F160C36229782F47A9857FC88DD1F2CB');
  });

  // openskp#290: SketchUp's own native Copy/Move+Copy/Array tools carry an
  // instance's attribute dictionaries - and whatever GUID a framing plugin
  // wrote into one - to every copy verbatim, so a real file can have
  // several DIFFERENT physical instances all sharing the exact same
  // non-empty InstancedNode.guid. The first instance to claim a real GUID
  // keeps it; every later instance sharing that same value must fall back
  // to a synthetic one instead of silently colliding.
  it('a duplicated source guid does not collide', () => {
    const scene = makeTwoInstanceScene();
    const duplicatedGuid = 'F160C36229782F47A9857FC88DD1F2CB';
    scene.sceneHierarchy.children[0].guid = duplicatedGuid;
    scene.sceneHierarchy.children[1].guid = duplicatedGuid;
    const rawBytes = toFragments(scene, { raw: true });

    const bb = new flatbuffers.ByteBuffer(rawBytes);
    const model = Model.getRootAsModel(bb);
    expect(model.guidsLength()).toBe(2);
    const g0 = model.guids(0)!;
    const g1 = model.guids(1)!;
    expect(g0).not.toBe(g1);
    const realGuidCount = [g0, g1].filter((g) => g === duplicatedGuid).length;
    expect(realGuidCount).toBe(1);
  });

  it('a named organizational wrapper with no geometry gets a tracked item; a generic one does not', () => {
    const stud: InstancedNode = {
      name: 'Stud1',
      nameIsGenerated: false,
      definitionName: 'Stud',
      layer: 'Layer0',
      matrix: IDENTITY,
      positionMm: [0, 0, 0],
      properties: {},
      guid: '',
      meshResourceId: 'mesh_0',
      children: [],
    };
    const wrapper: InstancedNode = {
      name: 'W-2',
      nameIsGenerated: false,
      definitionName: '',
      layer: 'Layer0',
      matrix: IDENTITY,
      positionMm: [0, 0, 0],
      properties: {},
      guid: '',
      children: [stud],
    };
    const genericChild: InstancedNode = { ...stud, name: 'Stud2' };
    const genericWrapper: InstancedNode = {
      name: 'Component_5',
      nameIsGenerated: true,
      definitionName: '',
      layer: 'Layer0',
      matrix: IDENTITY,
      positionMm: [0, 0, 0],
      properties: {},
      guid: '',
      children: [genericChild],
    };
    const root: InstancedNode = {
      name: 'ROOT',
      nameIsGenerated: false,
      definitionName: 'ROOT_MODEL',
      layer: 'Layer0',
      matrix: IDENTITY,
      positionMm: [0, 0, 0],
      properties: {},
      guid: '',
      children: [wrapper, genericWrapper],
    };
    const scene: InstancedScene = {
      bounds: null,
      sceneHierarchy: root,
      meshResources: [
        {
          id: 'mesh_0',
          definitionId: 1,
          definitionName: 'Box',
          variantKey: '1|x',
          primitives: [boxPrimitive()],
        },
      ],
      gltfMaterials: [{ pbrMetallicRoughness: { baseColorFactor: [1, 1, 1, 1] } }],
      textures: [],
      layerHidden: {},
    };

    const rawBytes = toFragments(scene, { raw: true });
    const bb = new flatbuffers.ByteBuffer(rawBytes);
    const model = Model.getRootAsModel(bb);
    // wrapper (W-2, named, no geometry) + Stud1 + Stud2 = 3 tracked items;
    // genericWrapper itself (nameIsGenerated) is NOT tracked.
    expect(model.localIdsLength()).toBe(3);

    let foundW2 = false;
    for (let i = 0; i < model.attributesLength(); i++) {
      const attr = model.attributes(i)!;
      for (let j = 0; j < attr.dataLength(); j++) {
        if (attr.data(j).includes('W-2')) foundW2 = true;
      }
    }
    expect(foundW2).toBe(true);
  });

  it('generated-name guids are listed in metadata', () => {
    const scene = makeOneInstanceScene(IDENTITY, 'Component_7');
    scene.sceneHierarchy.children[0].nameIsGenerated = true;
    const rawBytes = toFragments(scene, { raw: true });

    const bb = new flatbuffers.ByteBuffer(rawBytes);
    const model = Model.getRootAsModel(bb);
    const guid = model.guids(0)!;
    const meta = JSON.parse(model.metadata()!);
    expect(meta.generated_name_guids).toContain(guid);
  });
});

describe('Real SketchUp Fixture Tests (No Silent Skips)', () => {
  it('converts real SketchUp fixture SU_File.skp and parses back valid FlatBuffers model', () => {
    const fixturePath = path.join(__dirname, 'fixtures', 'SU_File.skp');
    const buffer = fs.readFileSync(fixturePath);
    const scene = buildInstancedScene(buffer.buffer.slice(buffer.byteOffset, buffer.byteOffset + buffer.byteLength));
    const fragBytes = toFragments(scene);

    expect(fragBytes.length).toBeGreaterThan(0);

    const inflated = fflate.unzlibSync(fragBytes);
    const bb = new flatbuffers.ByteBuffer(inflated);
    expect(Model.bufferHasIdentifier(bb)).toBe(true);

    const model = Model.getRootAsModel(bb);
    const meshes = model.meshes()!;
    expect(meshes.shellsLength()).toBeGreaterThan(0);
    expect(meshes.samplesLength()).toBeGreaterThan(0);
    expect(meshes.representationsLength()).toBeGreaterThan(0);
    expect(meshes.materialsLength()).toBeGreaterThan(0);

    // Verify all global transforms have unit-length direction vectors
    for (let i = 0; i < meshes.globalTransformsLength(); i++) {
      const gt = meshes.globalTransforms(i)!;
      const x = gt.xDirection()!;
      const y = gt.yDirection()!;
      expect(Math.hypot(x.x(), x.y(), x.z())).toBeCloseTo(1.0, 3);
      expect(Math.hypot(y.x(), y.y(), y.z())).toBeCloseTo(1.0, 3);
    }
  });

  it('works via SkpFile.toFragments() API on SU_File.skp', () => {
    const fixturePath = path.join(__dirname, 'fixtures', 'SU_File.skp');
    const skpFile = SkpFile.open(fixturePath);
    const fragBytes = skpFile.toFragments();

    expect(fragBytes.length).toBeGreaterThan(0);
    const inflated = fflate.unzlibSync(fragBytes);
    const bb = new flatbuffers.ByteBuffer(inflated);
    expect(Model.bufferHasIdentifier(bb)).toBe(true);

    const model = Model.getRootAsModel(bb);
    expect(model.meshes()!.shellsLength()).toBeGreaterThan(0);
  });

  it('converts complex model gondola_v20.skp with deduplicated instancing', () => {
    const fixturePath = path.join(__dirname, 'fixtures', 'gondola_v20.skp');
    const buffer = fs.readFileSync(fixturePath);
    const scene = buildInstancedScene(buffer.buffer.slice(buffer.byteOffset, buffer.byteOffset + buffer.byteLength));
    const fragBytes = toFragments(scene);

    expect(fragBytes.length).toBeGreaterThan(0);
    expect(fragBytes.length).toBeLessThan(buffer.length);

    const inflated = fflate.unzlibSync(fragBytes);
    const bb = new flatbuffers.ByteBuffer(inflated);
    expect(Model.bufferHasIdentifier(bb)).toBe(true);

    const model = Model.getRootAsModel(bb);
    const meshes = model.meshes()!;

    // In gondola_v20, components are placed repeatedly -> samples > shells
    expect(meshes.samplesLength()).toBeGreaterThan(meshes.shellsLength());

    // Verify every representation has valid non-inverted bounds
    for (let i = 0; i < meshes.representationsLength(); i++) {
      const repr = meshes.representations(i)!;
      const box = repr.bbox()!;
      const bmin = box.min()!;
      const bmax = box.max()!;
      expect(bmin.x()).toBeLessThanOrEqual(bmax.x());
      expect(bmin.y()).toBeLessThanOrEqual(bmax.y());
      expect(bmin.z()).toBeLessThanOrEqual(bmax.z());
    }
  });

  it('converts Untitled.skp fixture and parses back cleanly', () => {
    const fixturePath = path.join(__dirname, 'fixtures', 'Untitled.skp');
    const buffer = fs.readFileSync(fixturePath);
    const scene = buildInstancedScene(buffer.buffer.slice(buffer.byteOffset, buffer.byteOffset + buffer.byteLength));
    const fragBytes = toFragments(scene);

    expect(fragBytes.length).toBeGreaterThan(0);
    const inflated = fflate.unzlibSync(fragBytes);
    const bb = new flatbuffers.ByteBuffer(inflated);
    expect(Model.bufferHasIdentifier(bb)).toBe(true);

    const model = Model.getRootAsModel(bb);
    expect(model.meshes()!.shellsLength()).toBeGreaterThan(0);
  });
});

describe('Real @thatopen/fragments npm package interop (SingleThreadedFragmentsModel)', () => {
  it('loads deflated .frag buffer of SU_File.skp directly into SingleThreadedFragmentsModel', () => {
    const fixturePath = path.join(__dirname, 'fixtures', 'SU_File.skp');
    const skpFile = SkpFile.open(fixturePath);
    const fragBytes = skpFile.toFragments();

    const model = new SingleThreadedFragmentsModel('test-su-file', fragBytes);
    expect(model.modelId).toBe('test-su-file');

    const localIds = model.getLocalIds();
    expect(localIds.length).toBeGreaterThan(0);

    const itemsIds = model.getItemsIds();
    expect(itemsIds.length).toBeGreaterThan(0);

    // Verify geometry can be retrieved through ThatOpen's engine
    const geometries = model.getItemsGeometry(itemsIds);
    expect(geometries.length).toBeGreaterThan(0);
    const firstGeomList = geometries[0];
    expect(firstGeomList.length).toBeGreaterThan(0);
    const chunk = firstGeomList[0];
    expect(chunk.positions.length).toBeGreaterThan(0);
    expect(chunk.indices.length).toBeGreaterThan(0);
    expect(chunk.transform).toBeDefined();

    // Verify item metadata/attributes through ThatOpen's engine
    const itemsData = model.getItemsData(itemsIds);
    expect(itemsData.length).toBe(itemsIds.length);
    expect(itemsData[0]._localId.value).toBe(0);
    expect(itemsData[0].Name?.value).toBe('ROOT');

    model.dispose();
  });

  it('loads raw uncompressed .frag buffer into SingleThreadedFragmentsModel', () => {
    const fixturePath = path.join(__dirname, 'fixtures', 'SU_File.skp');
    const buffer = fs.readFileSync(fixturePath);
    const scene = buildInstancedScene(buffer.buffer.slice(buffer.byteOffset, buffer.byteOffset + buffer.byteLength));
    const rawFragBytes = toFragments(scene, { raw: true });

    const model = new SingleThreadedFragmentsModel('test-raw', rawFragBytes, true);
    expect(model.modelId).toBe('test-raw');
    expect(model.getItemsIds().length).toBeGreaterThan(0);

    const geometries = model.getItemsGeometry([0]);
    expect(geometries.length).toBe(1);
    expect(geometries[0][0].positions.length).toBeGreaterThan(0);

    model.dispose();
  });

  it('loads complex model gondola_v20.skp and retrieves instanced item data and geometry chunks', () => {
    const fixturePath = path.join(__dirname, 'fixtures', 'gondola_v20.skp');
    const skpFile = SkpFile.open(fixturePath);
    const fragBytes = skpFile.toFragments();

    const model = new SingleThreadedFragmentsModel('test-gondola', fragBytes);
    const itemsIds = model.getItemsIds();
    // 84, not the previous 81: collectLeaves now tracks a named
    // organizational wrapper using the real nameIsGenerated flag (matching
    // Python's/C++'s own instanced_scene name resolution) instead of a
    // "has some non-empty name" approximation - a wrapper whose only name
    // is a real (non-generic) DEFINITION name, with no instance-level name
    // of its own, now correctly counts as named too. Cross-checked directly
    // against Python's own to_fragments() output for this exact fixture:
    // byte-for-byte the same 84 tracked items.
    expect(itemsIds.length).toBe(84);

    // Retrieve multiple geometry chunks
    const sampleIds = itemsIds.slice(0, 10);
    const geoms = model.getItemsGeometry(sampleIds);
    expect(geoms.length).toBeGreaterThanOrEqual(10);
    for (const geomList of geoms) {
      for (const geom of geomList) {
        expect(geom.positions.length).toBeGreaterThan(0);
        expect(geom.indices.length).toBeGreaterThan(0);
        expect(geom.transform).toBeDefined();
      }
    }

    // Check layer categories preserved in itemsData
    const itemsData = model.getItemsData([0, 1]);
    expect(itemsData[0]._category?.value).toBe('Gondulas Laterais');

    model.dispose();
  });
});

describe('oversized shell splitting (openskp#285 / PR #355)', () => {
  // A single shell (Fragments' term for one baked triangle mesh) has no
  // representation for more than 65535 triangles: profilesFaceIds is a
  // plain ushort array in the real schema (index.fbs), with no uint32
  // escape hatch the way POINTS get past 65535 via BigShellProfile. A real
  // production model with one 222,000+-triangle primitive (a large
  // flattened/dense mesh) hit this in practice on the Python port - see
  // PR #355 for the full incident. Unlike Python (which threw at write
  // time), this port's old `faceIds[i] = i & 0xffff` silently WRAPPED
  // instead of throwing - a worse bug in one sense: it wrote wrong/
  // colliding face ids instead of failing loudly. getOrBakeShell now
  // splits an oversized primitive's triangles into multiple shells
  // instead, each within the ushort limit.

  function gridPrimitive(cols: number, rows: number): LocalPrimitive {
    const positions = new Float32Array(cols * rows * 3);
    for (let j = 0; j < rows; j++) {
      for (let i = 0; i < cols; i++) {
        const v = j * cols + i;
        positions[v * 3] = i;
        positions[v * 3 + 1] = j;
        positions[v * 3 + 2] = 0;
      }
    }
    const triCells = (cols - 1) * (rows - 1);
    const indices = new Uint32Array(triCells * 6);
    let k = 0;
    for (let j = 0; j < rows - 1; j++) {
      for (let i = 0; i < cols - 1; i++) {
        const a = j * cols + i;
        const b = a + 1;
        const c = a + cols;
        const d = c + 1;
        indices[k++] = a; indices[k++] = b; indices[k++] = d;
        indices[k++] = a; indices[k++] = d; indices[k++] = c;
      }
    }
    return {
      positions,
      normals: new Float32Array(cols * rows * 3),
      uvs: new Float32Array(cols * rows * 2),
      indices,
      materialIndex: 0,
    };
  }

  function sceneWithOnePrimitive(prim: LocalPrimitive, meshId: string): InstancedScene {
    const resource: InstancedMeshResource = {
      id: meshId,
      definitionId: 1,
      definitionName: 'BigMesh',
      variantKey: '1|255,255,255',
      primitives: [prim],
    };
    const node: InstancedNode = {
      name: 'Big',
      nameIsGenerated: false,
      definitionName: 'BigMesh',
      layer: 'Framing',
      matrix: IDENTITY,
      positionMm: [0, 0, 0],
      properties: {},
      guid: '',
      meshResourceId: meshId,
      children: [],
    };
    const root: InstancedNode = {
      name: 'ROOT',
      nameIsGenerated: false,
      definitionName: 'ROOT_MODEL',
      layer: 'Layer0',
      matrix: IDENTITY,
      positionMm: [0, 0, 0],
      properties: {},
      guid: '',
      children: [node],
    };
    return {
      bounds: null,
      sceneHierarchy: root,
      meshResources: [resource],
      gltfMaterials: [{ pbrMetallicRoughness: { baseColorFactor: [1.0, 1.0, 1.0, 1.0] } }],
      textures: [],
      layerHidden: {},
    };
  }

  it('splits an oversized primitive and every face id across every sub-shell is correct and sequential', () => {
    const cols = 210, rows = 165;
    const expectedTriangles = (cols - 1) * (rows - 1) * 2;
    expect(expectedTriangles).toBeGreaterThan(65535); // sanity-check the fixture itself exceeds the limit under test

    const scene = sceneWithOnePrimitive(gridPrimitive(cols, rows), 'mesh_big');

    // This is the exact shape of call that, before the fix, silently wrote
    // wrapped-around (wrong) face ids via `i & 0xffff` - the primary
    // regression check is that the result is now actually correct, not
    // just that it doesn't throw (unlike Python's own crash-shaped version
    // of this bug).
    const rawBytes = toFragments(scene, { raw: true });
    const bb = new flatbuffers.ByteBuffer(rawBytes);
    const model = Model.getRootAsModel(bb);
    const meshes = model.meshes()!;

    expect(meshes.shellsLength()).toBeGreaterThan(1); // confirms the split actually happened, not a no-op
    expect(meshes.samplesLength()).toBe(meshes.shellsLength()); // one sample per split shell, same item/material

    let totalTriangles = 0;
    for (let i = 0; i < meshes.shellsLength(); i++) {
      const shell = meshes.shells(i)!;
      expect(shell.profilesFaceIdsLength()).toBeLessThanOrEqual(65535); // every sub-shell stays within the ushort limit
      expect(shell.profilesLength()).toBe(shell.profilesFaceIdsLength());
      // Every face id in a shell is a small, sequential, non-wrapped 0..N-1
      // run - the exact thing the old `i & 0xffff` mask could silently
      // violate once a shell's own triangle count exceeded 65535.
      for (let j = 0; j < shell.profilesFaceIdsLength(); j++) {
        expect(shell.profilesFaceIds(j)).toBe(j);
      }
      totalTriangles += shell.profilesFaceIdsLength();
    }
    expect(totalTriangles).toBe(expectedTriangles);
  });

  it('a primitive within the limit still produces exactly one shell', () => {
    // Regression guard on the split path itself: a normal, non-huge
    // primitive must not be needlessly split into multiple shells.
    const scene = sceneWithOnePrimitive(gridPrimitive(50, 50), 'mesh_small'); // 49*49*2 = 4802 triangles

    const rawBytes = toFragments(scene, { raw: true });
    const bb = new flatbuffers.ByteBuffer(rawBytes);
    const model = Model.getRootAsModel(bb);
    const meshes = model.meshes()!;

    expect(meshes.shellsLength()).toBe(1);
    expect(meshes.samplesLength()).toBe(1);
  });
});

