import * as fs from 'fs';
import * as path from 'path';
import { zipSync } from 'fflate';
import { describe, expect, it, vi } from 'vitest';
import {
  SkpFile,
  buildInstancedScene,
  buildScene,
  create,
  extractThumbnail,
  fromFragments,
  openExisting,
  parseSkp,
  toFragments,
} from '../src';

const FIXTURES = path.join(__dirname, 'fixtures');

/** Minimal valid 1x1 PNG for synthetic VFF thumbnail tests. */
const ONE_PX_PNG = Uint8Array.from([
  0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a,
  0x00, 0x00, 0x00, 0x0d, 0x49, 0x48, 0x44, 0x52,
  0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x01,
  0x08, 0x02, 0x00, 0x00, 0x00, 0x90, 0x77, 0x53,
  0xde, 0x00, 0x00, 0x00, 0x0c, 0x49, 0x44, 0x41,
  0x54, 0x08, 0xd7, 0x63, 0xf8, 0xcf, 0xc0, 0x00,
  0x00, 0x03, 0x01, 0x01, 0x00, 0x18, 0xdd, 0x8d,
  0xb0, 0x00, 0x00, 0x00, 0x00, 0x49, 0x45, 0x4e,
  0x44, 0xae, 0x42, 0x60, 0x82,
]);

/** Build a compact (< 1 KiB) valid VFF `.skp` so `Buffer.from(...)` uses Node's default 8 KiB pool. */
function makeSmallVffSkpWithThumbnail(): Uint8Array {
  const vffMagic = [0xff, 0xfe, 0xff, 0x0e];
  const verMarker = [0xff, 0xfe, 0xff, 0x09];
  const verUtf16 = Buffer.from('{21.0.1}', 'utf16le');
  const header = new Uint8Array(vffMagic.length + verMarker.length + verUtf16.length);
  header.set(vffMagic, 0);
  header.set(verMarker, 4);
  header.set(verUtf16, 8);

  // Empty F601 root record (6 bytes)
  const modelDat = Uint8Array.from([0xf6, 0x01, 0x00, 0x00, 0x00, 0x00]);
  const zip = zipSync({
    'model.dat': modelDat,
    'meta/model_thumbnail.png': ONE_PX_PNG,
  });

  const out = new Uint8Array(header.length + zip.length);
  out.set(header, 0);
  out.set(zip, header.length);
  return out;
}

/** Allocate a genuine pooled Node `Buffer` (`byteOffset > 0`, `buffer.byteLength > byteLength`). */
function allocFromPool(payload: Uint8Array, minPoolSize?: number): Buffer {
  const prevPool = Buffer.poolSize;
  if (minPoolSize !== undefined && Buffer.poolSize < minPoolSize) {
    Buffer.poolSize = minPoolSize;
    // Trigger allocation of the enlarged slab and advance its cursor past 0.
    Buffer.allocUnsafe(8192).fill(0xff);
  }
  try {
    const prefix = Buffer.allocUnsafe(13);
    prefix.fill(0xff);
    const pooled = Buffer.from(payload);
    // Write decoy bytes into the slab right after `pooled` as well.
    const tail = Buffer.allocUnsafe(19);
    tail.set([0xff, 0xfe, 0xff, 0x0e, 0x50, 0x4b, 0x03, 0x04]);
    expect(pooled.byteOffset).toBeGreaterThan(0);
    expect(pooled.buffer.byteLength).toBeGreaterThan(pooled.byteLength);
    return pooled;
  } finally {
    Buffer.poolSize = prevPool;
  }
}

/** Embed `payload` at an odd offset inside a larger `ArrayBuffer` flanked by decoy SKP/ZIP headers. */
function makeSandwichedView(payload: Uint8Array): { u8: Uint8Array; buf: Buffer } {
  const prefix = Uint8Array.from([
    0xff, 0xfe, 0xff, 0x0e, // decoy SKP magic at offset 0
    0x50, 0x4b, 0x03, 0x04, // decoy ZIP local header
    0xde, 0xad, 0xbe, 0xef, 0x7f, // odd length = 13 bytes
  ]);
  const suffix = Uint8Array.from([
    0x50, 0x4b, 0x05, 0x06, 0x00, 0x00, 0x00, 0x00, // decoy EOCD after end
    0xff, 0xfe, 0xff, 0x0e, 0xca, 0xfe, 0xba, 0xbe,
  ]);
  const backing = new ArrayBuffer(prefix.length + payload.length + suffix.length);
  const full = new Uint8Array(backing);
  full.set(prefix, 0);
  full.set(payload, prefix.length);
  full.set(suffix, prefix.length + payload.length);

  const u8 = new Uint8Array(backing, prefix.length, payload.length);
  const buf = Buffer.from(backing, prefix.length, payload.length);
  return { u8, buf };
}

describe('Uint8Array and pooled Node.js Buffer inputs', () => {
  it('reads a small VFF model and thumbnail from Node default 8 KiB Buffer pool without copying', () => {
    const raw = makeSmallVffSkpWithThumbnail();
    expect(raw.byteLength).toBeLessThan(Buffer.poolSize >>> 1);

    const pooled = allocFromPool(raw);
    expect(pooled.byteOffset).toBeGreaterThan(0);

    const abSliceSpy = vi.spyOn(ArrayBuffer.prototype, 'slice');
    try {
      const model = parseSkp(pooled);
      expect(model.version).toBe('{21.0.1}');

      const scene = buildScene(pooled);
      expect(scene.glbPrimitives).toEqual([]);

      const instanced = buildInstancedScene(pooled);
      expect(instanced.meshResources).toEqual([]);

      const thumb = extractThumbnail(pooled);
      expect(thumb).not.toBeNull();
      expect(thumb!.width).toBe(1);
      expect(thumb!.height).toBe(1);
      expect(thumb!.mimeType).toBe('image/png');

      const skp = SkpFile.fromBuffer(pooled);
      expect(skp.parse().version).toBe('{21.0.1}');
      expect(skp.thumbnail()?.width).toBe(1);

      expect(abSliceSpy).not.toHaveBeenCalled();
    } finally {
      abSliceSpy.mockRestore();
    }
  });

  it('matches ArrayBuffer results byte-for-byte on classic and VFF fixtures from pooled and sandwiched views', () => {
    // Classic writer output (~6 KiB)
    const builder = create();
    const red = builder.addMaterial('Red', [220, 40, 40]);
    const comp = builder.addComponentDefinition('Panel', (def) => {
      def.addFace(
        [[0, 0, 0], [20, 0, 0], [20, 30, 0], [0, 30, 0]],
        { material: red }
      );
    });
    builder.addInstance(comp, { translation: [10, 20, 0] });
    builder.addFace([[0, 0, 0], [40, 0, 0], [40, 40, 0], [0, 40, 0]]);
    const classicBytes = builder.toBytes();
    const classicAb = classicBytes.buffer.slice(
      classicBytes.byteOffset,
      classicBytes.byteOffset + classicBytes.byteLength
    ) as ArrayBuffer;

    const pooledClassic = allocFromPool(classicBytes, 128 * 1024);
    const { u8: sandClassicU8, buf: sandClassicBuf } = makeSandwichedView(classicBytes);

    const expectedModel = parseSkp(classicAb);
    const expectedScene = buildScene(classicAb);
    const expectedInstanced = buildInstancedScene(classicAb);

    const abSliceSpy = vi.spyOn(ArrayBuffer.prototype, 'slice');
    try {
      for (const candidate of [classicBytes, pooledClassic, sandClassicU8, sandClassicBuf]) {
        const m = parseSkp(candidate);
        expect(m.version).toBe(expectedModel.version);
        expect(m.definitions.size).toBe(expectedModel.definitions.size);
        expect(m.root.faces.length).toBe(expectedModel.root.faces.length);
        expect(m.materials.map((x) => x.name)).toEqual(expectedModel.materials.map((x) => x.name));

        const s = buildScene(candidate);
        expect(s.glbPrimitives.length).toBe(expectedScene.glbPrimitives.length);
        expect(s.bounds).toEqual(expectedScene.bounds);

        const is = buildInstancedScene(candidate);
        expect(is.meshResources.length).toBe(expectedInstanced.meshResources.length);

        const viaFromBuffer = SkpFile.fromBuffer(candidate);
        expect(viaFromBuffer.parse().root.faces.length).toBe(1);
        expect(viaFromBuffer.buildScene().glbPrimitives.length).toBe(expectedScene.glbPrimitives.length);
        expect(viaFromBuffer.buildInstancedScene().meshResources.length).toBe(expectedInstanced.meshResources.length);
        expect(extractThumbnail(candidate)).toBeNull();

        const edited = openExisting(candidate);
        edited.builder.addFace([[100, 0, 0], [110, 0, 0], [110, 10, 0], [100, 10, 0]]);
        const reParsed = parseSkp(edited.builder.toBytes());
        expect(reParsed.root.faces.length).toBe(2);
      }

      expect(abSliceSpy).not.toHaveBeenCalled();
    } finally {
      abSliceSpy.mockRestore();
    }

    // Modern VFF fixture (`SU_File.skp` ~16 KiB)
    const vffRaw = fs.readFileSync(path.join(FIXTURES, 'SU_File.skp'));
    const vffAb = vffRaw.buffer.slice(vffRaw.byteOffset, vffRaw.byteOffset + vffRaw.byteLength) as ArrayBuffer;
    const pooledVff = allocFromPool(vffRaw, 128 * 1024);
    const { u8: sandVffU8, buf: sandVffBuf } = makeSandwichedView(vffRaw);

    const vffExpected = parseSkp(vffAb);
    const vffExpectedScene = buildScene(vffAb);
    for (const candidate of [vffRaw, pooledVff, sandVffU8, sandVffBuf]) {
      expect(parseSkp(candidate).definitions.size).toBe(vffExpected.definitions.size);
      expect(buildScene(candidate).glbPrimitives.length).toBe(vffExpectedScene.glbPrimitives.length);
      expect(() => openExisting(candidate)).toThrow(/not a legacy-format/);
    }

    // Also verify fromFragments accepts ArrayBuffer, compressed pooled Buffer, and unaligned raw Buffer
    const instanced = buildInstancedScene(pooledClassic);
    const fragZlib = toFragments(instanced);
    const fragRaw = toFragments(instanced, { raw: true });
    const fragAb = fragZlib.buffer.slice(fragZlib.byteOffset, fragZlib.byteOffset + fragZlib.byteLength) as ArrayBuffer;
    expect(fromFragments(fragAb).meshResources.length).toBe(instanced.meshResources.length);
    expect(fromFragments(allocFromPool(fragZlib, 64 * 1024)).meshResources.length).toBe(instanced.meshResources.length);
    expect(fromFragments(allocFromPool(fragRaw, 64 * 1024)).meshResources.length).toBe(instanced.meshResources.length);
    for (const offset of [1, 3]) {
      const unaligned = Buffer.alloc(8192 + fragRaw.length).subarray(offset, offset + fragRaw.length);
      unaligned.set(fragRaw);
      expect(fromFragments(unaligned).meshResources).toEqual(fromFragments(fragZlib).meshResources);
    }
  });

  it('gives plain Uint8Arrays back for a Buffer', () => {
    for (const name of ['capilla_quiroz_v17.skp', 'texture_bmp_2021.skp']) {
      const bytes = fs.readFileSync(path.join(FIXTURES, name));
      const scene = buildScene(bytes);
      expect(scene.textures.length).toBeGreaterThan(0);
      for (const texture of scene.textures) {
        expect(texture.data.constructor).toBe(Uint8Array);
      }
      expect(scene).toEqual(buildScene(new Uint8Array(bytes)));
    }
  });
});
