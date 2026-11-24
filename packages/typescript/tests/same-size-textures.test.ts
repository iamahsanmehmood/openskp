import { describe, it, expect } from 'vitest';
import { SkpFile, create } from '../src/index';

// Valid 1x1 RGB PNGs stored with uncompressed zlib blocks: identical 72-byte
// length and identical first 49 bytes, differing only in RGB pixel + checksums.
const PNG_RED = new Uint8Array([
  0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0x00, 0x00, 0x00, 0x0d, 0x49, 0x48, 0x44, 0x52,
  0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x01, 0x08, 0x02, 0x00, 0x00, 0x00, 0x90, 0x77, 0x53,
  0xde, 0x00, 0x00, 0x00, 0x0f, 0x49, 0x44, 0x41, 0x54, 0x78, 0x01, 0x01, 0x04, 0x00, 0xfb, 0xff,
  0x00, 0xdc, 0x28, 0x1e, 0x03, 0x06, 0x01, 0x23, 0xe0, 0x4f, 0x0f, 0xcc, 0x00, 0x00, 0x00, 0x00,
  0x49, 0x45, 0x4e, 0x44, 0xae, 0x42, 0x60, 0x82,
]);

const PNG_BLUE = new Uint8Array([
  0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0x00, 0x00, 0x00, 0x0d, 0x49, 0x48, 0x44, 0x52,
  0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x01, 0x08, 0x02, 0x00, 0x00, 0x00, 0x90, 0x77, 0x53,
  0xde, 0x00, 0x00, 0x00, 0x0f, 0x49, 0x44, 0x41, 0x54, 0x78, 0x01, 0x01, 0x04, 0x00, 0xfb, 0xff,
  0x00, 0x1e, 0x5a, 0xdc, 0x01, 0xee, 0x01, 0x55, 0xda, 0x6c, 0x0b, 0x85, 0x00, 0x00, 0x00, 0x00,
  0x49, 0x45, 0x4e, 0x44, 0xae, 0x42, 0x60, 0x82,
]);

const BUILDERS = ['buildScene', 'buildInstancedScene'] as const;

describe('full-content scene texture deduplication (#645)', () => {
  for (const builderName of BUILDERS) {
    it(`${builderName} keeps two different PNGs of equal byte length as two textures`, () => {
      expect(PNG_RED.length).toBe(PNG_BLUE.length);
      expect(Array.from(PNG_RED.subarray(0, 16))).toEqual(Array.from(PNG_BLUE.subarray(0, 16)));

      const b = create();
      const matA = b.addTextureMaterial('MatA', PNG_RED, 'red.png');
      const matB = b.addTextureMaterial('MatB', PNG_BLUE, 'blue.png');
      b.addFace(
        [
          [0, 0, 0],
          [10, 0, 0],
          [10, 10, 0],
          [0, 10, 0],
        ],
        { material: matA }
      );
      b.addFace(
        [
          [15, 0, 0],
          [25, 0, 0],
          [25, 10, 0],
          [15, 10, 0],
        ],
        { material: matA }
      );
      b.addFace(
        [
          [30, 0, 0],
          [40, 0, 0],
          [40, 10, 0],
          [30, 10, 0],
        ],
        { material: matB }
      );

      const skp = SkpFile.fromBuffer(b.toBytes());
      const scene = skp[builderName]();
      expect(scene.textures).toHaveLength(2);
      expect(Buffer.from(scene.textures[0].data).equals(Buffer.from(PNG_RED))).toBe(true);
      expect(Buffer.from(scene.textures[1].data).equals(Buffer.from(PNG_BLUE))).toBe(true);

      const indices = (scene.gltfMaterials as any[])
        .filter((m) => m.pbrMetallicRoughness?.baseColorTexture !== undefined)
        .map((m) => m.pbrMetallicRoughness.baseColorTexture.index)
        .sort((x, y) => x - y);
      expect(indices).toEqual([0, 1]);
    });

    it(`${builderName} deduplicates identical PNG bytes across two materials into one texture`, () => {
      const b = create();
      const mat1 = b.addTextureMaterial('Mat1', new Uint8Array(PNG_RED), 'one.png');
      const mat2 = b.addTextureMaterial('Mat2', new Uint8Array(PNG_RED), 'two.png', undefined, undefined, 0.6);
      b.addFace(
        [
          [0, 0, 0],
          [10, 0, 0],
          [10, 10, 0],
          [0, 10, 0],
        ],
        { material: mat1 }
      );
      b.addFace(
        [
          [20, 0, 0],
          [30, 0, 0],
          [30, 10, 0],
          [20, 10, 0],
        ],
        { material: mat2 }
      );

      const skp = SkpFile.fromBuffer(b.toBytes());
      const scene = skp[builderName]();
      expect(scene.textures).toHaveLength(1);
      expect(Buffer.from(scene.textures[0].data).equals(Buffer.from(PNG_RED))).toBe(true);

      const indices = (scene.gltfMaterials as any[])
        .filter((m) => m.pbrMetallicRoughness?.baseColorTexture !== undefined)
        .map((m) => m.pbrMetallicRoughness.baseColorTexture.index);
      expect(indices).toEqual([0, 0]);
    });
  }
});
