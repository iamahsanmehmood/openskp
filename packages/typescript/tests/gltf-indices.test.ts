import { describe, it, expect } from 'vitest';
import * as fs from 'fs';
import * as path from 'path';
import { toGLB, toInstancedGLB, buildScene, buildInstancedScene } from '../src/index';
import {
  encodeIndices,
  UINT16_INDEX_LIMIT,
  COMPONENT_TYPE_UNSIGNED_SHORT,
  COMPONENT_TYPE_UNSIGNED_INT,
} from '../src/gltf-indices';

/**
 * glTF allows UNSIGNED_SHORT indices, and real primitives are far below
 * the 65,536 limit, so writing every index as UNSIGNED_INT wasted half the
 * index bytes. Narrowing is an encoding choice at the export boundary: the
 * in-memory Uint32Array buffers and every public type are unchanged.
 *
 * The correctness risk is alignment, not the values: a 16-bit index buffer
 * of odd length leaves the running offset 2-byte aligned, and the next
 * primitive's POSITION accessor is float32, which glTF requires to be
 * 4-byte aligned. These tests check that on real multi-primitive files.
 */

function parseGlb(bytes: Uint8Array): { json: any; binary: Uint8Array } {
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const jsonChunkLen = view.getUint32(12, true);
  const json = JSON.parse(new TextDecoder().decode(bytes.subarray(20, 20 + jsonChunkLen)));
  const binHeaderOffset = 20 + jsonChunkLen;
  let binary = new Uint8Array(0);
  if (binHeaderOffset < bytes.length) {
    const binChunkLen = view.getUint32(binHeaderOffset, true);
    binary = bytes.subarray(binHeaderOffset + 8, binHeaderOffset + 8 + binChunkLen);
  }
  return { json, binary };
}

const readFixture = (name: string) => {
  const buf = fs.readFileSync(path.join(__dirname, 'fixtures', name));
  return buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength);
};

const FIXTURES = ['SU_File.skp', 'Untitled.skp', 'capilla_quiroz_v17.skp', 'gondola_v20.skp'];

/** Component-type -> byte width, for accessor validation. */
const WIDTH: Record<number, number> = { 5123: 2, 5125: 4, 5126: 4 };

describe('encodeIndices', () => {
  it('narrows to UNSIGNED_SHORT when every index fits', () => {
    const r = encodeIndices(new Uint32Array([0, 1, 2, 65535]));
    expect(r.componentType).toBe(COMPONENT_TYPE_UNSIGNED_SHORT);
    expect(r.data).toBeInstanceOf(Uint16Array);
    expect(r.bytesPerIndex).toBe(2);
    expect(Array.from(r.data)).toEqual([0, 1, 2, 65535]);
  });

  it('keeps UNSIGNED_INT when any index exceeds the limit', () => {
    const r = encodeIndices(new Uint32Array([0, 1, UINT16_INDEX_LIMIT + 1]));
    expect(r.componentType).toBe(COMPONENT_TYPE_UNSIGNED_INT);
    expect(r.data).toBeInstanceOf(Uint32Array);
    expect(r.bytesPerIndex).toBe(4);
  });

  it('keys on the largest VALUE, not the array length', () => {
    // A short array can still hold a large index.
    const r = encodeIndices(new Uint32Array([70000, 1, 2]));
    expect(r.componentType).toBe(COMPONENT_TYPE_UNSIGNED_INT);
  });

  it('handles an empty array', () => {
    const r = encodeIndices(new Uint32Array([]));
    expect(r.componentType).toBe(COMPONENT_TYPE_UNSIGNED_SHORT);
    expect(r.data.length).toBe(0);
  });

  it('round-trips values losslessly at the boundary', () => {
    for (const v of [0, 1, 65534, UINT16_INDEX_LIMIT]) {
      const r = encodeIndices(new Uint32Array([v]));
      expect(r.data[0]).toBe(v);
    }
  });
});

/** Every accessor must sit at an offset that is a multiple of its
 * component width, per the glTF 2.0 spec. */
function expectAlignedAccessors(json: any) {
  for (const acc of json.accessors) {
    const view = json.bufferViews[acc.bufferView];
    const width = WIDTH[acc.componentType];
    expect(width).toBeDefined();
    const absolute = (view.byteOffset ?? 0) + (acc.byteOffset ?? 0);
    expect(absolute % width).toBe(0);
  }
}

describe('index narrowing in the GLB exporters', () => {
  for (const name of FIXTURES) {
    it(`emits UNSIGNED_SHORT indices and stays aligned for ${name} (toGLB)`, () => {
      const { json, binary } = parseGlb(toGLB(buildScene(readFixture(name))));

      const indexAccessors = json.meshes.flatMap((m: any) =>
        m.primitives.map((p: any) => json.accessors[p.indices])
      );
      expect(indexAccessors.length).toBeGreaterThan(0);
      // Every fixture's primitives are far below 65k vertices.
      for (const acc of indexAccessors) {
        expect(acc.componentType).toBe(COMPONENT_TYPE_UNSIGNED_SHORT);
      }

      expectAlignedAccessors(json);
      for (const view of json.bufferViews) {
        expect((view.byteOffset ?? 0) + view.byteLength).toBeLessThanOrEqual(binary.length);
      }
    });

    it(`emits UNSIGNED_SHORT indices and stays aligned for ${name} (toInstancedGLB)`, () => {
      const { json, binary } = parseGlb(toInstancedGLB(buildInstancedScene(readFixture(name))));

      const indexAccessors = json.meshes.flatMap((m: any) =>
        m.primitives.map((p: any) => json.accessors[p.indices])
      );
      expect(indexAccessors.length).toBeGreaterThan(0);
      for (const acc of indexAccessors) {
        expect(acc.componentType).toBe(COMPONENT_TYPE_UNSIGNED_SHORT);
      }

      expectAlignedAccessors(json);
      for (const view of json.bufferViews) {
        expect((view.byteOffset ?? 0) + view.byteLength).toBeLessThanOrEqual(binary.length);
      }
    });

    it(`preserves index VALUES exactly for ${name}`, () => {
      // The narrowing must not renumber or reorder anything: read the
      // indices back out of the binary chunk and compare to the source.
      const scene = buildScene(readFixture(name));
      const { json, binary } = parseGlb(toGLB(scene));

      const prims = json.meshes.flatMap((m: any) => m.primitives);
      expect(prims.length).toBe(scene.glbPrimitives.length);

      prims.forEach((p: any, i: number) => {
        const acc = json.accessors[p.indices];
        const view = json.bufferViews[acc.bufferView];
        const start = (view.byteOffset ?? 0) + (acc.byteOffset ?? 0);
        const source = scene.glbPrimitives[i].indices;

        expect(acc.count).toBe(source.length);
        const read =
          acc.componentType === COMPONENT_TYPE_UNSIGNED_SHORT
            ? new Uint16Array(binary.buffer, binary.byteOffset + start, acc.count)
            : new Uint32Array(binary.buffer, binary.byteOffset + start, acc.count);

        for (let k = 0; k < source.length; k++) {
          expect(read[k]).toBe(source[k]);
        }
      });
    });
  }

  it('shrinks the binary chunk relative to 32-bit indices', () => {
    // Index data halves; positions/normals/uvs are unchanged, so the
    // saving shows up as a smaller binary chunk overall.
    const scene = buildScene(readFixture('gondola_v20.skp'));
    const { binary } = parseGlb(toGLB(scene));

    let indexBytes32 = 0;
    let attrBytes = 0;
    for (const p of scene.glbPrimitives) {
      indexBytes32 += p.indices.byteLength;
      attrBytes += p.positions.byteLength + p.normals.byteLength + p.uvs.byteLength;
    }

    // Allow for per-primitive 4-byte alignment padding.
    const padding = scene.glbPrimitives.length * 4;
    expect(binary.length).toBeLessThanOrEqual(attrBytes + indexBytes32 / 2 + padding);
    expect(binary.length).toBeLessThan(attrBytes + indexBytes32);
  });
});
