import { describe, it, expect } from 'vitest';
import * as fs from 'fs';
import * as path from 'path';
import {
  toGLB,
  toInstancedGLB,
  buildScene,
  buildInstancedScene,
  create,
  SkpScene,
  GlbPrimitive,
} from '../src/index';

function srgbToLinear(c: number): number {
  return c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
}

function triangleScene(): SkpScene {
  return {
    sceneHierarchy: { name: '', definitionName: '', layer: '', positionMm: [0, 0, 0], properties: {}, children: [] },
    meshIndex: {},
    gltfMaterials: [
      {
        pbrMetallicRoughness: {
          baseColorFactor: [0.25, 0.5, 0.75, 1.0],
          metallicFactor: 0.1,
          roughnessFactor: 0.9,
        },
      },
    ],
    glbPrimitives: [
      {
        positions: new Float32Array([1, 2, 3, -4, 5, 0, 2, -1, 7]),
        normals: new Float32Array([0, 0, 1, 0, 0, 1, 0, 0, 1]),
        uvs: new Float32Array([0, 0, 1, 0, 0, 1]),
        indices: new Uint32Array([0, 1, 2]),
        materialIndex: 0,
        geomName: 'triangle',
      } as GlbPrimitive,
    ],
  };
}

function parseGlb(bytes: Uint8Array): { json: any; binary: Uint8Array } {
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const jsonChunkLen = view.getUint32(12, true);
  const jsonStr = new TextDecoder().decode(bytes.subarray(20, 20 + jsonChunkLen));
  const json = JSON.parse(jsonStr);

  const binHeaderOffset = 20 + jsonChunkLen;
  let binary = new Uint8Array(0);
  if (binHeaderOffset < bytes.length) {
    const binChunkLen = view.getUint32(binHeaderOffset, true);
    binary = bytes.subarray(binHeaderOffset + 8, binHeaderOffset + 8 + binChunkLen);
  }
  return { json, binary };
}

describe('toGLB', () => {
  it('serializes scene and binary data, including TEXCOORD_0', () => {
    const bytes = toGLB(triangleScene());
    expect(bytes.length).toBeGreaterThanOrEqual(12);
    expect(new TextDecoder().decode(bytes.subarray(0, 4))).toBe('glTF');

    const { json, binary } = parseGlb(bytes);
    expect(json.asset.version).toBe('2.0');

    expect(json.meshes.length).toBe(1);
    const prim = json.meshes[0].primitives[0];
    expect(prim.attributes.POSITION).toBeDefined();
    expect(prim.attributes.NORMAL).toBeDefined();
    expect(prim.attributes.TEXCOORD_0).toBeDefined();
    expect(prim.material).toBe(0);

    const posAccessor = json.accessors[prim.attributes.POSITION];
    expect(posAccessor.componentType).toBe(5126);
    expect(posAccessor.type).toBe('VEC3');
    expect(posAccessor.count).toBe(3);
    expect(posAccessor.min).toEqual([-4, -1, 0]);
    expect(posAccessor.max).toEqual([2, 5, 7]);

    const uvAccessor = json.accessors[prim.attributes.TEXCOORD_0];
    expect(uvAccessor.componentType).toBe(5126);
    expect(uvAccessor.type).toBe('VEC2');
    expect(uvAccessor.count).toBe(3);
    const uvBufferView = json.bufferViews[uvAccessor.bufferView];
    const uvView = new DataView(binary.buffer, binary.byteOffset + uvBufferView.byteOffset);
    expect(uvView.getFloat32(2 * 4, true)).toBe(1);

    const pbr = json.materials[0].pbrMetallicRoughness;
    expect(pbr.baseColorFactor[0]).toBeCloseTo(srgbToLinear(0.25), 9);
    expect(pbr.baseColorFactor[1]).toBeCloseTo(srgbToLinear(0.5), 9);
    expect(pbr.baseColorFactor[2]).toBeCloseTo(srgbToLinear(0.75), 9);
    expect(pbr.baseColorFactor[3]).toBe(1.0);
    expect(pbr.metallicFactor).toBe(0.1);
    expect(pbr.roughnessFactor).toBe(0.9);
  });

  it('serializes an empty scene', () => {
    const scene: SkpScene = {
      sceneHierarchy: { name: '', definitionName: '', layer: '', positionMm: [0, 0, 0], properties: {}, children: [] },
      meshIndex: {},
      gltfMaterials: [],
      glbPrimitives: [],
    };
    const { json } = parseGlb(toGLB(scene));
    expect(json.meshes.length).toBe(0);
    expect(json.nodes.length).toBe(0);
  });

  it('exports the real fixture with real UV data matching the source GlbPrimitive.uvs', () => {
    const filePath = path.join(__dirname, 'fixtures', 'capilla_quiroz_v17.skp');
    const buf = fs.readFileSync(filePath);
    const arrayBuffer = buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength) as ArrayBuffer;
    const scene = buildScene(arrayBuffer);

    const bytes = toGLB(scene);
    const { json, binary } = parseGlb(bytes);

    const meshPrimitives = json.meshes[0].primitives;
    expect(meshPrimitives.length).toBe(scene.glbPrimitives.length);

    // Every primitive must carry TEXCOORD_0, and the decoded values must
    // exactly match the source GlbPrimitive.uvs that fed the writer - a
    // real round-trip check, not just "some accessor exists."
    for (let i = 0; i < scene.glbPrimitives.length; i++) {
      const prim = scene.glbPrimitives[i];
      const attrs = meshPrimitives[i].attributes;
      expect(attrs.TEXCOORD_0).toBeDefined();
      const uvAccessor = json.accessors[attrs.TEXCOORD_0];
      const uvBufferView = json.bufferViews[uvAccessor.bufferView];
      const uvCount = uvAccessor.count;
      expect(prim.uvs.length).toBe(uvCount * 2);
      const uvView = new DataView(binary.buffer, binary.byteOffset + uvBufferView.byteOffset);
      for (let j = 0; j < prim.uvs.length; j++) {
        expect(uvView.getFloat32(j * 4, true)).toBeCloseTo(prim.uvs[j], 5);
      }
    }
  });

  it('converts sRGB baseColorFactor to linear in baked and instanced GLB while keeping scene materials sRGB (#650)', () => {
    const builder = create();
    const specs: [string, [number, number, number, number]][] = [
      ['LayerRed', [255, 84, 84, 255]],
      ['White', [255, 255, 255, 255]],
      ['Black', [0, 0, 0, 255]],
      ['MidGrey', [188, 188, 188, 255]],
      ['DarkToe', [10, 10, 10, 128]],
    ];
    const handles = specs.map(([name, rgba]) => builder.addMaterial(name, rgba));
    handles.forEach((mat, i) => {
      const x = i * 10;
      builder.addFace(
        [
          [x, 0, 0],
          [x + 5, 0, 0],
          [x + 5, 5, 0],
          [x, 5, 0],
        ],
        { material: mat }
      );
    });
    const bytes = builder.toBytes();
    const ab = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer;
    const bakedScene = buildScene(ab);
    const instancedScene = buildInstancedScene(ab);

    for (const glbBytes of [toGLB(bakedScene), toInstancedGLB(instancedScene)]) {
      const { json } = parseGlb(glbBytes);
      const factors: number[][] = json.materials.map((m: any) => m.pbrMetallicRoughness.baseColorFactor);
      const findClose = (target: number[]) =>
        factors.find((f) => target.every((v, idx) => Math.abs(f[idx] - v) < 1e-4));

      const red = findClose([1, srgbToLinear(84 / 255), srgbToLinear(84 / 255), 1])!;
      expect(red).toBeDefined();
      expect(red[0]).toBe(1);
      expect(red[1]).toBeCloseTo(0.0887, 4);
      expect(red[2]).toBeCloseTo(0.0887, 4);
      expect(red[3]).toBe(1);

      expect(findClose([1, 1, 1, 1])).toEqual([1, 1, 1, 1]);
      expect(findClose([0, 0, 0, 1])).toEqual([0, 0, 0, 1]);

      const mid = findClose([srgbToLinear(188 / 255), srgbToLinear(188 / 255), srgbToLinear(188 / 255), 1])!;
      expect(mid).toBeDefined();
      expect(mid[0]).toBeCloseTo(0.503, 3);
      expect(mid[0]).toBeCloseTo(srgbToLinear(188 / 255), 9);

      const toe = findClose([(10 / 255) / 12.92, (10 / 255) / 12.92, (10 / 255) / 12.92, 128 / 255])!;
      expect(toe).toBeDefined();
      expect(toe[0]).toBeCloseTo((10 / 255) / 12.92, 9);
      expect(toe[3]).toBeCloseTo(128 / 255, 6);
    }

    // Scene gltfMaterials remain sRGB after export.
    const sceneFactors = (bakedScene.gltfMaterials as any[]).map((m) => m.pbrMetallicRoughness.baseColorFactor);
    expect(sceneFactors.some((f) => Math.abs(f[1] - 84 / 255) < 1e-6 && f[0] === 1)).toBe(true);
    expect(sceneFactors.some((f) => Math.abs(f[0] - 188 / 255) < 1e-6)).toBe(true);

    // Embedded non-colorized textures write [1, 1, 1, alpha], while scene
    // gltfMaterials keeps average colours and colorized materials stay tinted (#657).
    const capillaBuf = fs.readFileSync(path.join(__dirname, 'fixtures', 'capilla_quiroz_v17.skp'));
    const capillaAb = capillaBuf.buffer.slice(capillaBuf.byteOffset, capillaBuf.byteOffset + capillaBuf.byteLength) as ArrayBuffer;
    const capillaScene = buildScene(capillaAb);
    const capillaInstanced = buildInstancedScene(capillaAb);
    const sceneTexFactors = (capillaScene.gltfMaterials as any[])
      .filter((m) => m.pbrMetallicRoughness?.baseColorTexture)
      .map((m) => m.pbrMetallicRoughness.baseColorFactor);
    expect(sceneTexFactors.some((f) => f[0] < 0.6 && f[2] < 0.2)).toBe(true);
    const expectedWhiteFactors = sceneTexFactors.map((f) => [1, 1, 1, f[3]]);

    for (const exportedBytes of [
      toGLB(capillaScene, { textures: true }),
      toInstancedGLB(capillaInstanced, { textures: true }),
    ]) {
      const { json: capillaGlbJson } = parseGlb(exportedBytes);
      const actualTexFactors = capillaGlbJson.materials
        .filter((m: any) => m.pbrMetallicRoughness?.baseColorTexture)
        .map((m: any) => m.pbrMetallicRoughness.baseColorFactor);
      expect(actualTexFactors).toEqual(expectedWhiteFactors);
    }

    // End-to-end model -> scene -> GLB on a real .skp containing one plain and one
    // colorized material sharing the same RGB (200, 80, 60) and PNG image (#657).
    const bothBuf = fs.readFileSync(
      path.join(__dirname, '..', '..', 'cpp', 'tests', 'fixtures', 'colorized_and_plain_texture.skp')
    );
    const bothAb = bothBuf.buffer.slice(bothBuf.byteOffset, bothBuf.byteOffset + bothBuf.byteLength) as ArrayBuffer;
    const bothBaked = buildScene(bothAb);
    const bothInstanced = buildInstancedScene(bothAb);
    for (const built of [bothBaked, bothInstanced]) {
      expect(built.gltfMaterials).toHaveLength(2);
      const plainSceneMat = (built.gltfMaterials as any[]).find((m) => !m.extras);
      const colSceneMat = (built.gltfMaterials as any[]).find((m) => m.extras?.sketchup?.colorized);
      expect(plainSceneMat).toBeDefined();
      expect(colSceneMat).toBeDefined();
      expect(colSceneMat.extras).toEqual({ sketchup: { colorized: true, colorize_type: 1 } });
      expect(plainSceneMat.pbrMetallicRoughness.baseColorFactor).toEqual([200 / 255, 80 / 255, 60 / 255, 1]);
      expect(colSceneMat.pbrMetallicRoughness.baseColorFactor).toEqual([200 / 255, 80 / 255, 60 / 255, 1]);
    }

    for (const glbWithTex of [
      toGLB(bothBaked, { textures: true }),
      toInstancedGLB(bothInstanced, { textures: true }),
    ]) {
      const { json: withTexJson } = parseGlb(glbWithTex);
      expect(withTexJson.materials).toHaveLength(2);
      const plainGlbMat = withTexJson.materials.find((m: any) => !m.extras);
      const colGlbMat = withTexJson.materials.find((m: any) => m.extras?.sketchup?.colorized);
      expect(plainGlbMat.pbrMetallicRoughness.baseColorTexture).toEqual({ index: 0 });
      expect(plainGlbMat.pbrMetallicRoughness.baseColorFactor).toEqual([1, 1, 1, 1]);
      expect(colGlbMat.extras).toEqual({ sketchup: { colorized: true, colorize_type: 1 } });
      expect(colGlbMat.pbrMetallicRoughness.baseColorTexture).toEqual({ index: 0 });
      expect(colGlbMat.pbrMetallicRoughness.baseColorFactor).toEqual([200 / 255, 80 / 255, 60 / 255, 1]);
    }

    const expectedLinearTint = [
      srgbToLinear(200 / 255),
      srgbToLinear(80 / 255),
      srgbToLinear(60 / 255),
      1,
    ];
    for (const glbNoTex of [
      toGLB(bothBaked, { textures: false }),
      toInstancedGLB(bothInstanced, { textures: false }),
    ]) {
      const { json: noTexJson } = parseGlb(glbNoTex);
      expect(noTexJson.materials).toHaveLength(2);
      expect(noTexJson.textures).toBeUndefined();
      for (const m of noTexJson.materials) {
        expect(m.pbrMetallicRoughness.baseColorTexture).toBeUndefined();
        for (let c = 0; c < 4; c++) {
          expect(m.pbrMetallicRoughness.baseColorFactor[c]).toBeCloseTo(expectedLinearTint[c], 9);
        }
      }
    }
  });
});
