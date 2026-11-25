import { describe, it, expect } from 'vitest';
import * as fs from 'fs';
import * as path from 'path';
import * as openskp from '../src/index';
import { SkpFile, toGLB, toInstancedGLB } from '../src/index';
import { originalImageMime } from '../src/face-groups';
import type { LogLevel } from '../src/observability';

function glbJson(glb: Uint8Array): any {
  const view = new DataView(glb.buffer, glb.byteOffset, glb.byteLength);
  const jsonLen = view.getUint32(12, true);
  return JSON.parse(new TextDecoder().decode(glb.slice(20, 20 + jsonLen)));
}

function fixturePath(name: string): string {
  return path.join(__dirname, 'fixtures', name);
}

function openFixture(name: string): SkpFile {
  const bytes = fs.readFileSync(fixturePath(name));
  const ab = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer;
  return SkpFile.fromBuffer(ab);
}

const KEPT: Record<string, { name: string; mime: string; format: string; file: string; sig: number[] | null }> = {
  'texture_tiff_2017.skp': { name: 'Tex_TIF', mime: 'image/tiff', format: 'TIFF', file: 'tex.tif', sig: [0x49, 0x49, 0x2a, 0x00] },
  'texture_tiff_2021.skp': { name: 'Tex_TIF', mime: 'image/tiff', format: 'TIFF', file: 'tex.tif', sig: [0x49, 0x49, 0x2a, 0x00] },
  'texture_tga_2021.skp': { name: 'Tex_TGA', mime: 'image/x-tga', format: 'TGA', file: 'tex.tga', sig: null },
  'texture_gif_2021.skp': { name: 'Tex_GIF', mime: 'image/gif', format: 'GIF', file: 'tex.gif', sig: [0x47, 0x49, 0x46, 0x38, 0x39, 0x61] },
};

const BUILDERS = ['buildScene', 'buildInstancedScene'] as const;

describe('originalTextures option and skipped-texture warning (#635)', () => {
  for (const builder of BUILDERS) {
    for (const [fixture, expected] of Object.entries(KEPT)) {
      it(`${builder} keeps ${fixture} in its own format with originalTextures: true`, () => {
        const skp = openFixture(fixture);
        const warnings: string[] = [];
        const onLog = (level: LogLevel, msg: string) => {
          if (level === 'warn' && msg.startsWith('Texture of material')) warnings.push(msg);
        };
        const scene = skp[builder]({ originalTextures: true, onLog });
        expect(scene.textures).toHaveLength(1);
        const tex = scene.textures[0]!;
        expect(tex.mimeType).toBe(expected.mime);
        if (expected.sig) {
          expect(Array.from(tex.data.subarray(0, expected.sig.length))).toEqual(expected.sig);
        }
        const model = skp.parse();
        const stored = model.materials.find((m) => m.name === expected.name)?.texture?.data;
        expect(stored).toBeDefined();
        expect(Buffer.from(tex.data).equals(Buffer.from(stored!))).toBe(true);
        const textured = scene.gltfMaterials.filter((m) => m.pbrMetallicRoughness?.baseColorTexture !== undefined);
        expect(textured).toHaveLength(1);
        expect(textured[0].pbrMetallicRoughness.baseColorTexture).toEqual({ index: 0 });
        expect(warnings).toEqual([]);
      });

      it(`${builder} drops ${fixture} by default and logs a warning naming originalTextures: true`, () => {
        const skp = openFixture(fixture);
        const warnings: string[] = [];
        const onLog = (level: LogLevel, msg: string) => {
          if (level === 'warn' && msg.startsWith('Texture of material')) warnings.push(msg);
        };
        const scene = skp[builder]({ onLog });
        expect(scene.textures).toEqual([]);
        expect(scene.gltfMaterials.some((m) => m.pbrMetallicRoughness?.baseColorTexture !== undefined)).toBe(false);
        expect(warnings).toEqual([
          `Texture of material '${expected.name}' (${expected.file}) is ${expected.format}, ` +
            'which a scene cannot carry (only PNG and JPEG): the material is built without its texture ' +
            '(pass originalTextures: true to keep it in its own format)',
        ]);
      });
    }

    for (const fixture of ['texture_bmp_2021.skp', 'capilla_quiroz_v17.skp']) {
      it(`${builder} leaves PNG/JPEG textures unchanged on ${fixture}`, () => {
        const skp = openFixture(fixture);
        const plain = skp[builder]();
        const kept = skp[builder]({ originalTextures: true });
        expect(plain.textures.length).toBeGreaterThan(0);
        expect(kept.textures.map((t) => [t.mimeType, Buffer.from(t.data)])).toEqual(
          plain.textures.map((t) => [t.mimeType, Buffer.from(t.data)])
        );
        expect(kept.gltfMaterials).toEqual(plain.gltfMaterials);
      });
    }
  }

  it('sniffs known non-glTF image signatures and rejects unknown bytes', () => {
    const tga = new Uint8Array(60);
    tga[2] = 2;
    tga.set(Buffer.from('TRUEVISION-XFILE.\0', 'ascii'), 42);
    expect(originalImageMime(Uint8Array.from([0x49, 0x49, 0x2a, 0x00, 0, 0, 0, 0]))).toBe('image/tiff');
    expect(originalImageMime(Uint8Array.from([0x4d, 0x4d, 0x00, 0x2a, 0, 0, 0, 0]))).toBe('image/tiff');
    expect(originalImageMime(Buffer.from('GIF89a0000000000', 'ascii'))).toBe('image/gif');
    expect(originalImageMime(Buffer.from('BM00000000000000', 'ascii'))).toBe('image/bmp');
    expect(originalImageMime(Buffer.from('RIFF\0\0\0\0WEBPVP8 ', 'binary'))).toBe('image/webp');
    expect(originalImageMime(Buffer.from('8BPS0000', 'ascii'))).toBe('image/vnd.adobe.photoshop');
    expect(originalImageMime(Buffer.from('DDS 0000', 'ascii'))).toBe('image/vnd.ms-dds');
    expect(originalImageMime(Uint8Array.from([0, 0, 0, 0x0c, 0x6a, 0x50, 0x20, 0x20]))).toBe('image/jp2');
    expect(originalImageMime(tga)).toBe('image/x-tga');
    expect(originalImageMime(Buffer.from('zzzzzzzzzzzzzzzz', 'ascii'))).toBeNull();
    expect(originalImageMime(new Uint8Array(0))).toBeNull();
  });

  it('does not re-export internal image-sniffing helpers from the package root', () => {
    expect('describeImageFormat' in openskp).toBe(false);
    expect('originalImageMime' in openskp).toBe(false);
    expect('warnSkippedTexture' in openskp).toBe(false);
    expect('filterGltfTextures' in openskp).toBe(false);
  });

  for (const instanced of [false, true]) {
    const label = instanced ? 'toInstancedGLB' : 'toGLB';

    it(`${label} omits a lone TIFF texture with a single warning and writes valid glTF`, () => {
      const skp = openFixture('texture_tiff_2021.skp');
      const warnings: string[] = [];
      const onLog = (level: LogLevel, msg: string) => {
        if (level === 'warn') warnings.push(msg);
      };
      const glb = instanced
        ? toInstancedGLB(skp.buildInstancedScene({ originalTextures: true }), { textures: true, onLog })
        : toGLB(skp.buildScene({ originalTextures: true }), { textures: true, onLog });
      const doc = glbJson(glb);
      expect(doc.images).toBeUndefined();
      expect(doc.textures).toBeUndefined();
      expect(doc.samplers).toBeUndefined();
      expect(doc.materials.some((m: any) => m.pbrMetallicRoughness?.baseColorTexture !== undefined)).toBe(false);
      expect(doc.materials.some((m: any) => m.alphaMode === 'MASK')).toBe(false);
      expect(warnings).toEqual([
        "Texture 'tex.tif' is TIFF, which glTF 2.0 cannot carry (only PNG and JPEG): leaving it out of the GLB",
      ]);
    });

    it(`${label} drops TIFF, keeps its colour, remaps PNG texture index, and warns once`, () => {
      const skp = openFixture('texture_tiff_and_png_2021.skp');
      const warnings: string[] = [];
      const onLog = (level: LogLevel, msg: string) => {
        if (level === 'warn') warnings.push(msg);
      };
      const scene = instanced
        ? skp.buildInstancedScene({ originalTextures: true })
        : skp.buildScene({ originalTextures: true });
      expect(scene.textures.map((t) => t.mimeType)).toEqual(['image/tiff', 'image/png']);

      const glb = instanced
        ? toInstancedGLB(scene as any, { textures: true, onLog })
        : toGLB(scene as any, { textures: true, onLog });
      const doc = glbJson(glb);

      expect(doc.images).toHaveLength(1);
      expect(doc.images[0].mimeType).toBe('image/png');
      expect(doc.textures).toEqual([{ sampler: 0, source: 0 }]);
      expect(doc.samplers).toHaveLength(1);

      const srgbToLinear = (c: number) =>
        c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
      const tiffMat = doc.materials.find((m: any) => m.pbrMetallicRoughness.baseColorFactor[0] > 0.5);
      const pngMat = doc.materials.find((m: any) => m.pbrMetallicRoughness.baseColorFactor[2] > 0.8);
      expect(tiffMat).toBeDefined();
      expect(tiffMat.pbrMetallicRoughness.baseColorTexture).toBeUndefined();
      expect(tiffMat.alphaMode).toBeUndefined();
      expect(tiffMat.pbrMetallicRoughness.baseColorFactor[0]).toBeCloseTo(srgbToLinear(200 / 255), 4);
      expect(tiffMat.pbrMetallicRoughness.baseColorFactor[1]).toBeCloseTo(srgbToLinear(60 / 255), 4);
      expect(tiffMat.pbrMetallicRoughness.baseColorFactor[2]).toBeCloseTo(srgbToLinear(40 / 255), 4);

      expect(pngMat).toBeDefined();
      expect(pngMat.pbrMetallicRoughness.baseColorTexture).toEqual({ index: 0 });
      expect(pngMat.alphaMode).toBe('MASK');

      // Caller's scene must remain unmutated.
      expect(scene.textures.map((t) => t.mimeType)).toEqual(['image/tiff', 'image/png']);
      expect(warnings).toEqual([
        "Texture 'tex.tif' is TIFF, which glTF 2.0 cannot carry (only PNG and JPEG): leaving it out of the GLB",
      ]);
    });
  }
});
