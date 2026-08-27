import { describe, it, expect } from 'vitest';
import * as fs from 'fs';
import * as path from 'path';
import { buildScene, toGLB, create } from '../src/index';
import { sniffImageMime } from '../src/model';

/**
 * Texture images in the GLB export.
 *
 * The parser has extracted texture bytes for a while, and every baked
 * primitive already carries UVs, but `toGLB` never wrote `images`/`textures`,
 * so an exported model lost its textures and fell back to each material's
 * averaged colour. Embedding them is opt-in: the images dominate the file
 * size, and a caller who only wants geometry should not pay for them.
 *
 * Fixture: capilla_quiroz_v17.skp, which carries three distinct JPEG textures
 * across four materials.
 */
function readFixture(name: string): ArrayBuffer {
  const buf = fs.readFileSync(path.join(__dirname, 'fixtures', name));
  return buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength) as ArrayBuffer;
}

/** Reads a GLB's JSON chunk (12-byte header + 8-byte chunk header). */
function glbJson(glb: Uint8Array): any {
  const view = new DataView(glb.buffer, glb.byteOffset, glb.byteLength);
  const jsonLen = view.getUint32(12, true);
  return JSON.parse(new TextDecoder().decode(glb.slice(20, 20 + jsonLen)));
}

/** Reads a GLB's BIN chunk. */
function glbBin(glb: Uint8Array): Uint8Array {
  const view = new DataView(glb.buffer, glb.byteOffset, glb.byteLength);
  const jsonLen = view.getUint32(12, true);
  const binStart = 20 + jsonLen;
  return glb.slice(binStart + 8, binStart + 8 + view.getUint32(binStart, true));
}

describe('sniffImageMime', () => {
  it('identifies PNG and JPEG by their magic bytes', () => {
    expect(sniffImageMime(new Uint8Array([0xff, 0xd8, 0xff, 0xe0, 1, 2]))).toBe('image/jpeg');
    expect(sniffImageMime(new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]))).toBe('image/png');
  });

  it('rejects anything glTF cannot carry', () => {
    // e.g. TIFF, which older SketchUp files do contain
    expect(sniffImageMime(new Uint8Array([0x49, 0x49, 0x2a, 0x00, 1, 2, 3, 4]))).toBeNull();
    expect(sniffImageMime(new Uint8Array([]))).toBeNull();
  });
});

describe('buildScene texture collection', () => {
  const scene = buildScene(readFixture('capilla_quiroz_v17.skp'));

  it('collects the distinct texture images', () => {
    expect(scene.textures.length).toBe(3);
    for (const tex of scene.textures) {
      expect(tex.mimeType).toBe('image/jpeg');
      expect(tex.data.length).toBeGreaterThan(0);
      expect(typeof tex.filename).toBe('string');
    }
  });

  it('points textured materials at them', () => {
    const textured = (scene.gltfMaterials as any[]).filter(
      (m) => m.pbrMetallicRoughness?.baseColorTexture
    );
    expect(textured.length).toBe(4);
    for (const mat of textured) {
      const idx = mat.pbrMetallicRoughness.baseColorTexture.index;
      expect(idx).toBeGreaterThanOrEqual(0);
      expect(idx).toBeLessThan(scene.textures.length);
      // the resolved colour is kept alongside the image: glTF multiplies the
      // two, which is what SketchUp's colorized materials expect
      expect(mat.pbrMetallicRoughness.baseColorFactor).toHaveLength(4);
    }
  });

  it('keeps two textures apart when their average colours collide', () => {
    // Materials used to be deduplicated on colour alone, so two different
    // images that average to the same RGB collapsed into one material and one
    // of the images was lost. Real files do this - two fabrics both resolving
    // to 141,141,141 - so the texture has to be part of the key.
    const seen = new Set<number>();
    for (const mat of scene.gltfMaterials as any[]) {
      const tex = mat.pbrMetallicRoughness?.baseColorTexture;
      if (tex) seen.add(tex.index);
    }
    expect(seen.size).toBe(scene.textures.length);
  });

  it('does not fragment the mesh (same primitive count as before)', () => {
    // adding the texture to the grouping key must not split primitives that
    // were previously batched together
    expect(scene.glbPrimitives.length).toBe(21);
  });
});

describe('Material transparency -> glTF alpha', () => {
  // Round-tripped through the real writer and reader rather than a hand-built
  // fixture: addMaterial's 4th (alpha) channel is documented to carry
  // SketchUp's own opacity mechanism (create.ts's `use_opacity = False -
  // alpha carries transparency instead`), so this exercises the exact path a
  // real .skp file with a translucent material takes.
  it('propagates a translucent material into baseColorFactor alpha + alphaMode BLEND', () => {
    const builder = create();
    const glass = builder.addMaterial('Glass', [40, 70, 100, 128]);
    builder.addFace(
      [
        [0, 0, 0],
        [1, 0, 0],
        [1, 1, 0],
        [0, 1, 0],
      ],
      { material: glass }
    );
    const scene = buildScene(builder.toBytes());
    const mat = (scene.gltfMaterials as any[]).find(
      (m) => m.pbrMetallicRoughness?.baseColorFactor
    );
    expect(mat).toBeDefined();
    expect(mat.pbrMetallicRoughness.baseColorFactor[3]).toBeCloseTo(128 / 255, 2);
    expect(mat.alphaMode).toBe('BLEND');
  });

  it('leaves a fully opaque material byte-for-byte as before (no alphaMode field)', () => {
    const builder = create();
    const red = builder.addMaterial('Red', [255, 0, 0]);
    builder.addFace(
      [
        [0, 0, 0],
        [1, 0, 0],
        [1, 1, 0],
        [0, 1, 0],
      ],
      { material: red }
    );
    const scene = buildScene(builder.toBytes());
    const mat = (scene.gltfMaterials as any[])[0];
    expect(mat.pbrMetallicRoughness.baseColorFactor[3]).toBe(1.0);
    expect(mat.alphaMode).toBeUndefined();
  });

  it('marks every textured material MASK or BLEND, never left at the OPAQUE default', () => {
    // A conformant glTF viewer ignores alpha entirely under the default
    // alphaMode (OPAQUE) - both the texture's own channel and
    // baseColorFactor's. capilla_quiroz_v17.skp has four textured
    // materials: two ordinary opaque ones (MASK - a safe no-op, since
    // there's nothing in their JPEGs to cut out) and two genuinely
    // translucent stained-glass-style materials at alpha 0.5 (BLEND, so
    // that opacity actually renders instead of being silently dropped).
    const scene = buildScene(readFixture('capilla_quiroz_v17.skp'));
    const textured = (scene.gltfMaterials as any[]).filter(
      (m) => m.pbrMetallicRoughness?.baseColorTexture
    );
    expect(textured.length).toBe(4);
    for (const mat of textured) {
      expect(['MASK', 'BLEND']).toContain(mat.alphaMode);
      if (mat.alphaMode === 'BLEND') {
        expect(mat.pbrMetallicRoughness.baseColorFactor[3]).toBeLessThan(1.0);
      } else {
        expect(mat.pbrMetallicRoughness.baseColorFactor[3]).toBe(1.0);
      }
    }
    const translucent = textured.filter((m) => m.alphaMode === 'BLEND');
    expect(translucent.length).toBe(2);
  });
});

describe('toGLB texture embedding', () => {
  const scene = buildScene(readFixture('capilla_quiroz_v17.skp'));

  it('omits the images by default', () => {
    const json = glbJson(toGLB(scene));
    expect(json.images).toBeUndefined();
    expect(json.textures).toBeUndefined();
    expect(json.samplers).toBeUndefined();
    // and leaves no dangling reference behind - a strict reader would reject it
    for (const mat of json.materials) {
      expect(mat.pbrMetallicRoughness.baseColorTexture).toBeUndefined();
    }
  });

  it('embeds them when asked', () => {
    const glb = toGLB(scene, { textures: true });
    const json = glbJson(glb);
    expect(json.images.length).toBe(3);
    expect(json.textures.length).toBe(3);
    expect(json.samplers.length).toBe(1);
    expect(json.materials.filter((m: any) => m.pbrMetallicRoughness?.baseColorTexture).length).toBe(4);

    // the bytes are really in the BIN chunk, not just declared
    const bin = glbBin(glb);
    for (let i = 0; i < json.images.length; i++) {
      const view = json.bufferViews[json.images[i].bufferView];
      const bytes = bin.slice(view.byteOffset, view.byteOffset + view.byteLength);
      expect(bytes.length).toBe(scene.textures[i].data.length);
      expect(bytes[0]).toBe(scene.textures[i].data[0]);
      // every bufferView must start 4-byte aligned
      expect(view.byteOffset % 4).toBe(0);
    }
  });

  it('produces a structurally valid GLB either way', () => {
    for (const glb of [toGLB(scene), toGLB(scene, { textures: true })]) {
      const view = new DataView(glb.buffer, glb.byteOffset, glb.byteLength);
      expect(view.getUint32(0, true)).toBe(0x46546c67); // "glTF"
      expect(view.getUint32(8, true)).toBe(glb.byteLength);
      const jsonLen = view.getUint32(12, true);
      expect(jsonLen % 4).toBe(0);
      const binStart = 20 + jsonLen;
      expect(view.getUint32(binStart + 4, true)).toBe(0x004e4942); // "BIN"
      const declared = glbJson(glb).buffers[0].byteLength;
      const chunkLen = view.getUint32(binStart, true);
      // the chunk may be padded up to 3 bytes past the declared length
      expect(declared).toBeLessThanOrEqual(chunkLen);
      expect(chunkLen - declared).toBeLessThan(4);
    }
  });

  it('is a no-op on a model with no textures', () => {
    const plain = buildScene(readFixture('Untitled.skp'));
    expect(plain.textures.length).toBe(0);
    expect(toGLB(plain, { textures: true })).toEqual(toGLB(plain));
  });
});
