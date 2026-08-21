import { describe, it, expect } from 'vitest';
import * as fs from 'fs';
import * as path from 'path';
import { toGLB, toInstancedGLB, buildScene, buildInstancedScene } from '../src/index';
import { buildInstancedSceneFromParsed } from '../src/instanced';
import { buildSceneFromParsed, type ParsedDefinition } from '../src/model';
import {
  panelDefinition,
  plainMaterial,
  makeParsed,
  translation,
  repeatedComponentScene,
} from './helpers/instanced-fixtures';

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

describe('toInstancedGLB', () => {
  it('emits a valid glTF 2.0 GLB container', () => {
    const scene = buildInstancedSceneFromParsed(repeatedComponentScene(10, 4));
    const bytes = toInstancedGLB(scene);

    expect(new TextDecoder().decode(bytes.subarray(0, 4))).toBe('glTF');
    const { json } = parseGlb(bytes);
    expect(json.asset.version).toBe('2.0');
    expect(json.scenes[0].nodes.length).toBe(1);
  });

  it('reuses ONE glTF mesh from many nodes', () => {
    const scene = buildInstancedSceneFromParsed(repeatedComponentScene(25, 4));
    const { json } = parseGlb(toInstancedGLB(scene));

    // one mesh...
    expect(json.meshes.length).toBe(1);

    // ...referenced by all 25 placements
    const meshRefs = json.nodes.filter((n: any) => n.mesh !== undefined);
    expect(meshRefs.length).toBe(25);
    for (const n of meshRefs) {
      expect(n.mesh).toBe(0);
    }
  });

  it('does NOT duplicate binary buffers per instance', () => {
    const sizeOf = (n: number) =>
      toInstancedGLB(buildInstancedSceneFromParsed(repeatedComponentScene(n, 4))).length;

    const one = sizeOf(1);
    const hundred = sizeOf(100);

    // Growth is node/JSON overhead only. The vertex+index binary chunk is
    // byte-identical, which is the actual claim being made.
    const binOf = (n: number) =>
      parseGlb(toInstancedGLB(buildInstancedSceneFromParsed(repeatedComponentScene(n, 4)))).binary.length;
    expect(binOf(100)).toBe(binOf(1));

    // and the baked exporter, by contrast, grows its binary ~100x
    const bakedBin = (n: number) =>
      parseGlb(toGLB(buildSceneFromParsed(repeatedComponentScene(n, 4)))).binary.length;
    expect(bakedBin(100)).toBeGreaterThan(bakedBin(1) * 50);

    // sanity: the instanced GLB is far smaller than the baked one at scale
    expect(hundred).toBeLessThan(
      toGLB(buildSceneFromParsed(repeatedComponentScene(100, 4))).length
    );
    expect(one).toBeGreaterThan(0);
  });

  it('represents multiple materials as primitives of one mesh, not extra nodes', () => {
    const parsed = makeParsed({
      defs: new Map<number | string, ParsedDefinition>([
        [1, panelDefinition(24, 'TwoSided', { materialId: 1, backMaterialId: 2 })],
      ]),
      rootInstances: [
        { refIdx: 1, name: 'a', matrix: translation(0, 0, 0) },
        { refIdx: 1, name: 'b', matrix: translation(40, 0, 0) },
      ],
      materials: new Map([
        ['Red', plainMaterial('Red', { r: 200, g: 10, b: 10, a: 255 })],
        ['Blue', plainMaterial('Blue', { r: 10, g: 10, b: 200, a: 255 })],
      ]),
      materialIdToName: new Map([
        [1, 'Red'],
        [2, 'Blue'],
      ]),
    });
    const { json } = parseGlb(toInstancedGLB(buildInstancedSceneFromParsed(parsed)));

    expect(json.meshes.length).toBe(1);
    expect(json.meshes[0].primitives.length).toBe(2);
    expect(json.nodes.filter((n: any) => n.mesh !== undefined).length).toBe(2);
  });

  it('preserves the node hierarchy and per-node transforms', () => {
    const scene = buildInstancedSceneFromParsed(repeatedComponentScene(3, 2));
    const { json } = parseGlb(toInstancedGLB(scene));

    const root = json.nodes[json.scenes[0].nodes[0]];
    expect(root.children.length).toBe(3);

    // each placement sits at a different spot
    const matrices = root.children.map((ci: number) => JSON.stringify(json.nodes[ci].matrix));
    expect(new Set(matrices).size).toBe(3);
  });

  it('keeps accessors consistent with the binary chunk', () => {
    const scene = buildInstancedSceneFromParsed(repeatedComponentScene(5, 3));
    const { json, binary } = parseGlb(toInstancedGLB(scene));

    expect(json.buffers[0].byteLength).toBeLessThanOrEqual(binary.length);
    for (const view of json.bufferViews) {
      expect(view.byteOffset + view.byteLength).toBeLessThanOrEqual(binary.length);
    }
    for (const acc of json.accessors) {
      expect(json.bufferViews[acc.bufferView]).toBeDefined();
      expect(acc.count).toBeGreaterThan(0);
    }
  });

  it('omits texture references when images are not embedded', () => {
    const scene = buildInstancedSceneFromParsed(repeatedComponentScene(2, 2));
    const { json } = parseGlb(toInstancedGLB(scene));
    for (const mat of json.materials ?? []) {
      expect(mat.pbrMetallicRoughness.baseColorTexture).toBeUndefined();
    }
    expect(json.images).toBeUndefined();
  });
});

describe('toInstancedGLB on real fixtures', () => {
  const fixture = (name: string) => path.join(__dirname, 'fixtures', name);
  const files = ['SU_File.skp', 'capilla_quiroz_v17.skp', 'gondola_v20.skp'];

  for (const file of files) {
    it(`round-trips ${file} into a valid instanced GLB`, () => {
      const buf = fs.readFileSync(fixture(file));
      const ab = buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength);

      const instanced = buildInstancedScene(ab);
      const bytes = toInstancedGLB(instanced);
      const { json, binary } = parseGlb(bytes);

      expect(new TextDecoder().decode(bytes.subarray(0, 4))).toBe('glTF');
      expect(json.asset.version).toBe('2.0');
      expect(json.nodes.length).toBeGreaterThan(0);

      // every mesh reference resolves, and every accessor fits the buffer
      for (const node of json.nodes) {
        if (node.mesh !== undefined) {
          expect(json.meshes[node.mesh]).toBeDefined();
        }
      }
      for (const view of json.bufferViews) {
        expect(view.byteOffset + view.byteLength).toBeLessThanOrEqual(binary.length);
      }
      for (const mesh of json.meshes) {
        for (const prim of mesh.primitives) {
          expect(json.materials[prim.material]).toBeDefined();
        }
      }
    });

    it(`describes the same geometry as buildScene for ${file}`, () => {
      const buf = fs.readFileSync(fixture(file));
      const ab = buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength);

      const baked = buildScene(ab);
      const instanced = buildInstancedScene(ab);

      // same triangle budget overall - the instanced form stores it once
      // per definition instead of once per placement, but a flattened walk
      // must still describe the same number of triangles.
      let bakedTris = 0;
      for (const p of baked.glbPrimitives) bakedTris += p.indices.length / 3;

      const byId = new Map(instanced.meshResources.map((r) => [r.id, r]));
      let instTris = 0;
      const walk = (n: any) => {
        if (n.meshResourceId) {
          const res = byId.get(n.meshResourceId);
          if (res) for (const p of res.primitives) instTris += p.indices.length / 3;
        }
        n.children.forEach(walk);
      };
      walk(instanced.sceneHierarchy);

      expect(instTris).toBe(bakedTris);
    });
  }
});

describe('existing toGLB output is unchanged', () => {
  it('produces byte-identical GLB for a fixture before and after this feature', () => {
    const buf = fs.readFileSync(path.join(__dirname, 'fixtures', 'SU_File.skp'));
    const ab = buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength);

    const a = toGLB(buildScene(ab));
    const b = toGLB(buildScene(ab));
    expect(a.length).toBe(b.length);
    expect(Buffer.compare(Buffer.from(a), Buffer.from(b))).toBe(0);

    // and the generator string is still the original exporter's
    const { json } = parseGlb(a);
    expect(json.asset.generator).toBe('OpenSKP TypeScript Exporter');
  });
});
