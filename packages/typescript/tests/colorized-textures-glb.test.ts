import { describe, it, expect, vi } from 'vitest';
import * as fs from 'fs';
import * as path from 'path';
import { zlibSync } from 'fflate';
import * as openskp from '../src/index';
import {
  SkpFile,
  buildSceneFromParsed,
  toGLB,
  toInstancedGLB,
  type GlbPrimitive,
  type ImageCodec,
  type InstancedScene,
  type LogLevel,
  type Material,
  type RawRgbaImage,
  type SkpScene,
} from '../src/index';
import { buildInstancedSceneFromParsed } from '../src/instanced';
import { GeometryBuilder, type ParsedDefinition } from '../src/geometry';
import { makeParsed, panelDefinition, translation } from './helpers/instanced-fixtures';
import {
  colorizeImageU8,
  decodePngRgba,
  encodePngU8,
  resolveGltfTextures,
  stripJpegExifApp1,
  type GltfTextureBakeStats,
} from '../src/glb-textures';

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

function extractGlbImageBytes(json: any, binary: Uint8Array, imgIdx: number): Uint8Array {
  const bv = json.bufferViews[json.images[imgIdx].bufferView];
  return binary.subarray(bv.byteOffset, bv.byteOffset + bv.byteLength);
}

function buildColorizeQuadsScenes(repeatsPerMaterial = 1): {
  scene: SkpScene;
  instanced: InstancedScene;
} {
  const origPath = path.join(__dirname, 'fixtures', 'legacy_textured_colorize_2017.skp');
  const origModel = SkpFile.open(origPath).parse();

  const materials = new Map<string, Material>();
  const materialIdToName = new Map<number, string>();
  origModel.materials.forEach((m, idx) => {
    const id = idx + 1;
    materials.set(m.name, { ...m, id });
    materialIdToName.set(id, m.name);
  });

  const shiftId = [...materialIdToName.entries()].find(([, n]) => n === 'shift')![0];
  const compDef = panelDefinition(12, 'ColorizedTile', { materialId: shiftId });

  const rootBuilder = new GeometryBuilder();
  let vId = 1;
  let eId = 100;
  let fId = 1000;
  const names = ['plain', 'tint', 'shift', 'tint_half'] as const;
  names.forEach((name, idx) => {
    const mId = [...materialIdToName.entries()].find(([, n]) => n === name)![0];
    for (let r = 0; r < repeatsPerMaterial; r++) {
      const x0 = idx * 24;
      const y0 = r * 24;
      const v1 = vId++,
        v2 = vId++,
        v3 = vId++,
        v4 = vId++;
      rootBuilder.vertices.set(v1, [x0, y0, 0]);
      rootBuilder.vertices.set(v2, [x0 + 12, y0, 0]);
      rootBuilder.vertices.set(v3, [x0 + 12, y0 + 12, 0]);
      rootBuilder.vertices.set(v4, [x0, y0 + 12, 0]);
      const e1 = eId++,
        e2 = eId++,
        e3 = eId++,
        e4 = eId++;
      rootBuilder.edges.set(e1, [v1, v2]);
      rootBuilder.edges.set(e2, [v2, v3]);
      rootBuilder.edges.set(e3, [v3, v4]);
      rootBuilder.edges.set(e4, [v4, v1]);
      rootBuilder.faces.set(fId++, {
        loops: [
          [
            { edgeId: e1, orientation: 0 },
            { edgeId: e2, orientation: 0 },
            { edgeId: e3, orientation: 0 },
            { edgeId: e4, orientation: 0 },
          ],
        ],
        normal: [0, 0, 1],
        materialId: mId,
        backMaterialId: null,
      });
    }
  });

  const rootInstances = [];
  for (let r = 0; r < repeatsPerMaterial; r++) {
    rootInstances.push({
      refIdx: 1,
      name: `Tile_${r}`,
      matrix: translation(200, r * 24, 0),
    });
  }

  const parsed = makeParsed({
    defs: new Map<number | string, ParsedDefinition>([[1, compDef]]),
    rootGeometry: {
      guid: 'ROOT_GEOM',
      name: 'ROOT_GEOM',
      isImage: false,
      alwaysFacesCamera: false,
      builder: rootBuilder,
    },
    rootInstances,
    materials,
    materialIdToName,
  });

  return {
    scene: buildSceneFromParsed(parsed),
    instanced: buildInstancedSceneFromParsed(parsed),
  };
}

function crc32Test(buf: Uint8Array): number {
  let c = 0xffffffff;
  for (let i = 0; i < buf.length; i++) {
    c ^= buf[i];
    for (let b = 0; b < 8; b++) {
      c = (c & 1) !== 0 ? (0xedb88320 ^ (c >>> 1)) : (c >>> 1);
    }
  }
  return (c ^ 0xffffffff) >>> 0;
}

function pngChunk(type: string, payload: Uint8Array): Uint8Array {
  const out = new Uint8Array(12 + payload.length);
  const dv = new DataView(out.buffer);
  dv.setUint32(0, payload.length, false);
  for (let i = 0; i < 4; i++) out[4 + i] = type.charCodeAt(i);
  out.set(payload, 8);
  dv.setUint32(8 + payload.length, crc32Test(out.subarray(4, 8 + payload.length)), false);
  return out;
}

function concatBytes(...parts: Uint8Array[]): Uint8Array {
  const total = parts.reduce((acc, p) => acc + p.length, 0);
  const out = new Uint8Array(total);
  let off = 0;
  for (const p of parts) {
    out.set(p, off);
    off += p.length;
  }
  return out;
}

/** Small header-only PNG declaring arbitrary dimensions without compressed pixel rows. */
function makeDeclaredSizePng(width: number, height: number): Uint8Array {
  const sig = new Uint8Array([137, 80, 78, 71, 13, 10, 26, 10]);
  const ihdr = new Uint8Array(13);
  const dv = new DataView(ihdr.buffer);
  dv.setUint32(0, width >>> 0, false);
  dv.setUint32(4, height >>> 0, false);
  ihdr[8] = 8;
  ihdr[9] = 2;
  return concatBytes(
    sig,
    pngChunk('IHDR', ihdr),
    pngChunk('IDAT', new Uint8Array([0x78, 0x9c])),
    pngChunk('IEND', new Uint8Array(0))
  );
}

/** Minimal valid JPEG container embedding uncompressed RGB test pixels behind a real SOF0 header
 *  so synchronous `ImageCodec` tests exercise full header parsing, EXIF stripping, and baking. */
function makeTestSof0Jpeg(width: number, height: number, rgb: Uint8Array): Uint8Array {
  // SOI + APP0 (JFIF) + SOF0 (width, height, 3 components) + APP2 (raw RGB test payload) + SOS + EOI
  const app0 = new Uint8Array([
    0xff, 0xe0, 0x00, 0x10, 0x4a, 0x46, 0x49, 0x46, 0x00, 0x01, 0x01, 0x00, 0x00, 0x01, 0x00,
    0x01, 0x00, 0x00,
  ]);
  const sof0 = new Uint8Array([
    0xff,
    0xc0,
    0x00,
    0x11,
    0x08,
    (height >>> 8) & 0xff,
    height & 0xff,
    (width >>> 8) & 0xff,
    width & 0xff,
    0x03,
    0x01,
    0x11,
    0x00,
    0x02,
    0x11,
    0x01,
    0x03,
    0x11,
    0x01,
  ]);
  const app2Len = 2 + 4 + rgb.length;
  const app2 = new Uint8Array(2 + app2Len);
  app2[0] = 0xff;
  app2[1] = 0xe2;
  app2[2] = (app2Len >>> 8) & 0xff;
  app2[3] = app2Len & 0xff;
  app2.set([0x54, 0x52, 0x47, 0x42], 4); // 'TRGB'
  app2.set(rgb, 8);
  const sosAndEoi = new Uint8Array([
    0xff, 0xda, 0x00, 0x0c, 0x03, 0x01, 0x00, 0x02, 0x11, 0x03, 0x11, 0x00, 0x3f, 0x00, 0x00,
    0xff, 0xd9,
  ]);
  return concatBytes(new Uint8Array([0xff, 0xd8]), app0, sof0, app2, sosAndEoi);
}

function createTestJpegCodec(calls?: {
  decodes: number;
  encodes: { quality: number; mimeType: string }[];
}): ImageCodec {
  return {
    decode(bytes: Uint8Array, mimeType: string): RawRgbaImage | null {
      expect(mimeType).toBe('image/jpeg');
      if (calls) calls.decodes++;
      if (bytes.length < 4 || bytes[0] !== 0xff || bytes[1] !== 0xd8) return null;
      let pos = 2;
      let width = 0;
      let height = 0;
      let rgbPayload: Uint8Array | null = null;
      while (pos + 4 <= bytes.length && bytes[pos] === 0xff) {
        while (pos < bytes.length && bytes[pos] === 0xff) pos++;
        const marker = bytes[pos++];
        if (marker === 0xd9 || marker === 0xda) break;
        if (marker === 0x01 || (marker >= 0xd0 && marker <= 0xd8)) continue;
        const segLen = (bytes[pos] << 8) | bytes[pos + 1];
        if (marker === 0xc0) {
          height = (bytes[pos + 3] << 8) | bytes[pos + 4];
          width = (bytes[pos + 5] << 8) | bytes[pos + 6];
        } else if (
          marker === 0xe2 &&
          segLen >= 6 &&
          bytes[pos + 2] === 0x54 &&
          bytes[pos + 3] === 0x52 &&
          bytes[pos + 4] === 0x47 &&
          bytes[pos + 5] === 0x42
        ) {
          rgbPayload = bytes.subarray(pos + 6, pos + segLen);
        }
        pos += segLen;
      }
      if (!width || !height || !rgbPayload || rgbPayload.length < width * height * 3) return null;
      const rgba = new Uint8Array(width * height * 4);
      for (let i = 0; i < width * height; i++) {
        rgba[i * 4] = rgbPayload[i * 3];
        rgba[i * 4 + 1] = rgbPayload[i * 3 + 1];
        rgba[i * 4 + 2] = rgbPayload[i * 3 + 2];
        rgba[i * 4 + 3] = 255;
      }
      return { width, height, data: rgba };
    },
    encode(image: RawRgbaImage, mimeType: 'image/jpeg', quality: number): Uint8Array {
      if (calls) calls.encodes.push({ quality, mimeType });
      expect(image.data.length).toBe(image.width * image.height * 4);
      const rgb = new Uint8Array(image.width * image.height * 3);
      for (let i = 0; i < image.width * image.height; i++) {
        rgb[i * 3] = image.data[i * 4];
        rgb[i * 3 + 1] = image.data[i * 4 + 1];
        rgb[i * 3 + 2] = image.data[i * 4 + 2];
      }
      return makeTestSof0Jpeg(image.width, image.height, rgb);
    },
  };
}

function injectExifOrientationApp1(jpegBytes: Uint8Array, orientation = 6): Uint8Array {
  const tiff = new Uint8Array([
    0x49,
    0x49,
    42,
    0,
    8,
    0,
    0,
    0,
    1,
    0,
    0x12,
    0x01,
    3,
    0,
    1,
    0,
    0,
    0,
    orientation & 0xff,
    (orientation >>> 8) & 0xff,
    0,
    0,
    0,
    0,
    0,
    0,
  ]);
  const exifHeader = new Uint8Array([0x45, 0x78, 0x69, 0x66, 0, 0]);
  const segLen = 2 + exifHeader.length + tiff.length;
  const app1 = new Uint8Array(2 + segLen);
  app1[0] = 0xff;
  app1[1] = 0xe1;
  app1[2] = (segLen >>> 8) & 0xff;
  app1[3] = segLen & 0xff;
  app1.set(exifHeader, 4);
  app1.set(tiff, 4 + exifHeader.length);
  return concatBytes(jpegBytes.subarray(0, 2), app1, jpegBytes.subarray(2));
}

function hasExifSignature(bytes: Uint8Array): boolean {
  for (let i = 0; i + 6 <= bytes.length; i++) {
    if (
      bytes[i] === 0x45 &&
      bytes[i + 1] === 0x78 &&
      bytes[i + 2] === 0x69 &&
      bytes[i + 3] === 0x66 &&
      bytes[i + 4] === 0 &&
      bytes[i + 5] === 0
    ) {
      return true;
    }
  }
  return false;
}

describe('Colorized textures in GLB and instanced GLB (#285)', () => {
  it('does not leak internal GLB texture-baking helpers onto the root namespace', () => {
    expect('resolveGltfTextures' in openskp).toBe(false);
    expect('decodePngRgba' in openskp).toBe(false);
    expect('encodePngU8' in openskp).toBe(false);
    expect('colorizeImageU8' in openskp).toBe(false);
    expect('stripJpegExifApp1' in openskp).toBe(false);
  });

  it('bakes shift, tint, and opacity PNGs out of the box without mutating Scene or InstancedScene', () => {
    const { scene, instanced } = buildColorizeQuadsScenes(3);

    const origSceneMats = structuredClone(scene.gltfMaterials);
    const origInstMats = structuredClone(instanced.gltfMaterials);
    const origTexBytes = new Uint8Array(scene.textures[0].data);

    // Ground truth from SUTextureGetColorizedImageRep on legacy_textured_colorize_2017.skp
    const expected = new Set([
      '200,40,40|40,40,200|100',
      '32,237,80|32,237,80|100',
      '172,253,46|253,46,172|100',
      '65,73,255|65,73,255|50',
    ]);

    for (const glbBytes of [
      toGLB(scene, { textures: true }),
      toInstancedGLB(instanced, { textures: true }),
    ]) {
      expect(scene.gltfMaterials).toEqual(origSceneMats);
      expect(instanced.gltfMaterials).toEqual(origInstMats);
      expect(scene.textures[0].data).toEqual(origTexBytes);
      expect(instanced.textures[0].data).toEqual(origTexBytes);

      const { json, binary } = parseGlb(glbBytes);
      expect(json.images).toHaveLength(4);
      const actual = new Set<string>();
      let texturedCount = 0;

      for (const mat of json.materials) {
        const texRef = mat.pbrMetallicRoughness?.baseColorTexture;
        if (!texRef) continue;
        texturedCount++;
        const bcf = mat.pbrMetallicRoughness.baseColorFactor;
        expect(bcf[0]).toBeCloseTo(1.0, 6);
        expect(bcf[1]).toBeCloseTo(1.0, 6);
        expect(bcf[2]).toBeCloseTo(1.0, 6);

        const imgIdx = json.textures[texRef.index].source;
        expect(json.images[imgIdx].mimeType).toBe('image/png');
        const imgBytes = extractGlbImageBytes(json, binary, imgIdx);
        const dec = decodePngRgba(imgBytes);
        expect(dec.width).toBe(2);
        expect(dec.height).toBe(1);
        const left = `${dec.rgba[0]},${dec.rgba[1]},${dec.rgba[2]}`;
        const right = `${dec.rgba[4]},${dec.rgba[5]},${dec.rgba[6]}`;
        const opacityPct = Math.round(bcf[3] * 100);
        actual.add(`${left}|${right}|${opacityPct}`);
      }
      expect(texturedCount).toBe(4);
      expect(actual).toEqual(expected);
    }
  });

  it('decodes each texture once and bakes once per unique (texture, colorizeType, color)', () => {
    const { scene, instanced } = buildColorizeQuadsScenes(8);

    for (const s of [scene, instanced]) {
      const stats: GltfTextureBakeStats = { decodeCalls: 0, colorizeCalls: 0 };
      const resolved = resolveGltfTextures(
        s.textures,
        s.gltfMaterials,
        true,
        undefined,
        undefined,
        stats
      );
      expect(resolved.textures).toHaveLength(4);
      expect(stats.decodeCalls).toBe(1);
      expect(stats.colorizeCalls).toBe(3);
    }

    // Two colorized materials sharing the same colour at opacities 1.0 and 0.5 bake only once.
    const png1x1 = encodePngU8(new Uint8Array([180, 50, 90]), 1, 1, 3);
    const sharedMats = [1.0, 0.5].map((alpha, i) => ({
      name: `m${i}`,
      pbrMetallicRoughness: {
        baseColorFactor: [200 / 255, 90 / 255, 20 / 255, alpha],
        baseColorTexture: { index: 0 },
        metallicFactor: 0,
        roughnessFactor: 0.8,
      },
      extras: { sketchup: { colorized: true, colorize_type: 0 } },
    }));
    const stats2: GltfTextureBakeStats = { decodeCalls: 0, colorizeCalls: 0 };
    const res2 = resolveGltfTextures(
      [{ data: png1x1, mimeType: 'image/png', filename: 'one.png' }],
      sharedMats,
      true,
      undefined,
      undefined,
      stats2
    );
    expect(stats2.decodeCalls).toBe(1);
    expect(stats2.colorizeCalls).toBe(1);
    expect(res2.textures).toHaveLength(1);
  });

  it('decodes indexed, sub-byte greyscale, 16-bit, tRNS, and Adam7 interlaced PNGs', () => {
    // 1) 4-bit indexed PNG with PLTE + tRNS (2x1: transparent red, opaque blue)
    const sig = new Uint8Array([137, 80, 78, 71, 13, 10, 26, 10]);
    const ihdrIdx = new Uint8Array([0, 0, 0, 2, 0, 0, 0, 1, 4, 3, 0, 0, 0]);
    const plte = new Uint8Array([250, 10, 20, 30, 40, 240]);
    const trns = new Uint8Array([64]); // index 0 alpha = 64, index 1 alpha = 255
    const idatIdx = zlibSync(new Uint8Array([0, 0x01])); // filter=0, indices [0, 1] in 4-bit nibbles
    const indexedPng = concatBytes(
      sig,
      pngChunk('IHDR', ihdrIdx),
      pngChunk('PLTE', plte),
      pngChunk('tRNS', trns),
      pngChunk('IDAT', idatIdx),
      pngChunk('IEND', new Uint8Array(0))
    );
    const decIdx = decodePngRgba(indexedPng);
    expect(Array.from(decIdx.rgba)).toEqual([250, 10, 20, 64, 30, 40, 240, 255]);

    // 2) 16-bit RGB PNG with Adam7 interlace (3x3)
    const ihdrAdam7 = new Uint8Array([0, 0, 0, 3, 0, 0, 0, 3, 16, 2, 0, 0, 1]);
    // Pass sizes for 3x3:
    // p0 (0,8,0,8): 1x1, p1 (4,8,0,8): 0x0, p2 (0,4,4,8): 0x0, p3 (2,4,0,4): 1x1,
    // p4 (0,2,2,4): 2x1, p5 (1,2,0,2): 1x2, p6 (0,1,1,2): 3x1
    const passScanlines: number[] = [];
    const expectedGrid = new Uint8Array(3 * 3 * 4);
    for (let y = 0; y < 3; y++) {
      for (let x = 0; x < 3; x++) {
        const i4 = (y * 3 + x) * 4;
        expectedGrid[i4] = x * 100;
        expectedGrid[i4 + 1] = y * 100;
        expectedGrid[i4 + 2] = 128;
        expectedGrid[i4 + 3] = 255;
      }
    }
    const adam7: [number, number, number, number][] = [
      [0, 8, 0, 8],
      [4, 8, 0, 8],
      [0, 4, 4, 8],
      [2, 4, 0, 4],
      [0, 2, 2, 4],
      [1, 2, 0, 2],
      [0, 1, 1, 2],
    ];
    for (const [x0, dx, y0, dy] of adam7) {
      const pw = 3 > x0 ? Math.floor((3 - x0 + dx - 1) / dx) : 0;
      const ph = 3 > y0 ? Math.floor((3 - y0 + dy - 1) / dy) : 0;
      if (pw === 0 || ph === 0) continue;
      for (let py = 0; py < ph; py++) {
        passScanlines.push(0); // filter None
        const y = y0 + py * dy;
        for (let px = 0; px < pw; px++) {
          const x = x0 + px * dx;
          const r8 = x * 100;
          const g8 = y * 100;
          const b8 = 128;
          for (const c8 of [r8, g8, b8]) {
            const c16 = c8 * 257;
            passScanlines.push((c16 >>> 8) & 0xff, c16 & 0xff);
          }
        }
      }
    }
    const adam7Png = concatBytes(
      sig,
      pngChunk('IHDR', ihdrAdam7),
      pngChunk('IDAT', zlibSync(new Uint8Array(passScanlines))),
      pngChunk('IEND', new Uint8Array(0))
    );
    const decAdam7 = decodePngRgba(adam7Png);
    expect(decAdam7.width).toBe(3);
    expect(decAdam7.height).toBe(3);
    expect(Array.from(decAdam7.rgba)).toEqual(Array.from(expectedGrid));
  });

  it('matches greyscale, alpha, achromatic target, and row-block chunking rules', () => {
    const grey = new Uint8Array([0, 0, 0, 0, 128, 128, 128, 64, 255, 255, 255, 255]);
    const shifted = colorizeImageU8(grey, 3, 1, [200, 90, 20], 0, true);
    const tinted = colorizeImageU8(grey, 3, 1, [200, 90, 20], 1, true);
    expect([shifted[3], shifted[7], shifted[11]]).toEqual([0, 64, 255]);
    expect([tinted[3], tinted[7], tinted[11]]).toEqual([0, 64, 255]);
    expect(shifted[0]).toBeGreaterThan(0);

    // Chromatic image -> grey target desaturates (delta_s = -1.0)
    const vivid = new Uint8Array([240, 30, 80, 180, 20, 210, 90, 255]);
    for (const ctype of [0, 1]) {
      const desat = colorizeImageU8(vivid, 2, 1, [140, 140, 140], ctype, true);
      expect(desat[0]).toBe(desat[1]);
      expect(desat[1]).toBe(desat[2]);
      expect(desat[3]).toBe(180);
      expect(desat[4]).toBe(desat[5]);
      expect(desat[5]).toBe(desat[6]);
      expect(desat[7]).toBe(255);
    }

    // Chromatic pixels whose whole-image average is grey (avg_s == 0) keep delta_s == 0
    const greyAvgChrom = new Uint8Array([255, 1, 1, 255, 1, 255, 255, 255]);
    const s0 = colorizeImageU8(greyAvgChrom, 2, 1, [140, 140, 140], 0, false);
    const s1 = colorizeImageU8(greyAvgChrom, 2, 1, [140, 140, 140], 1, false);
    const exp0 = [255, 24, 24, 24, 255, 255];
    const exp1 = [255, 24, 24, 255, 24, 24];
    for (let i = 0; i < 6; i++) {
      expect(Math.abs(s0[i] - exp0[i])).toBeLessThanOrEqual(1);
      expect(Math.abs(s1[i] - exp1[i])).toBeLessThanOrEqual(1);
    }

    // Row-block chunk invariance
    const multiRow = new Uint8Array(19 * 37 * 4);
    let seed = 7;
    for (let i = 0; i < multiRow.length; i++) {
      seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0;
      multiRow[i] = seed & 0xff;
    }
    for (const ctype of [0, 1]) {
      const whole = colorizeImageU8(multiRow, 19, 37, [200, 90, 20], ctype, true, 100000);
      const chunked = colorizeImageU8(multiRow, 19, 37, [200, 90, 20], ctype, true, 40);
      expect(chunked).toEqual(whole);
    }
  });

  it('strips EXIF APP1 on JPEGs, warns once without imageCodec, and bakes JPEGs at quality 95 with imageCodec', () => {
    // 2x1 RGB JPEG ([200,40,40], [40,40,200]) with EXIF Orientation=6 APP1 injected
    const baseRgb = new Uint8Array([200, 40, 40, 40, 40, 200]);
    const cleanJpeg = makeTestSof0Jpeg(2, 1, baseRgb);
    const exifJpeg = injectExifOrientationApp1(cleanJpeg, 6);
    expect(hasExifSignature(exifJpeg)).toBe(true);
    expect(stripJpegExifApp1(exifJpeg)).toEqual(cleanJpeg);

    const makeJpegMat = (
      id: number,
      name: string,
      rgb: [number, number, number],
      colorized: boolean,
      colorizeType: number
    ): Material => ({
      name,
      color: { r: rgb[0], g: rgb[1], b: rgb[2], a: 255 },
      transparency: 1,
      id,
      texture: { filename: 'exif6.jpg', width: 24, height: 12, data: exifJpeg },
      colorized,
      colorizeType,
    });
    const jpgParsed = makeParsed({
      defs: new Map<number | string, ParsedDefinition>([
        [1, panelDefinition(24, 'P1', { materialId: 1 })],
        [2, panelDefinition(24, 'P2', { materialId: 2 })],
        [3, panelDefinition(24, 'P3', { materialId: 3 })],
      ]),
      rootInstances: [
        { refIdx: 1, name: 'i1', matrix: translation(0, 0, 0) },
        { refIdx: 2, name: 'i2', matrix: translation(30, 0, 0) },
        { refIdx: 3, name: 'i3', matrix: translation(60, 0, 0) },
      ],
      materials: new Map([
        ['PlainJpg', makeJpegMat(1, 'PlainJpg', [255, 255, 255], false, 0)],
        ['ShiftJpg', makeJpegMat(2, 'ShiftJpg', [200, 90, 20], true, 0)],
        ['TintJpg', makeJpegMat(3, 'TintJpg', [30, 160, 60], true, 1)],
      ]),
      materialIdToName: new Map([
        [1, 'PlainJpg'],
        [2, 'ShiftJpg'],
        [3, 'TintJpg'],
      ]),
    });
    const scene = buildSceneFromParsed(jpgParsed);
    const instanced = buildInstancedSceneFromParsed(jpgParsed);
    expect(scene.textures).toHaveLength(1);
    expect(scene.textures[0].data).toEqual(exifJpeg);

    // 1) Without imageCodec: 1 warning, EXIF APP1 stripped without re-encoding, tinted factors kept.
    for (const inst of [false, true]) {
      const warnings: string[] = [];
      const onLog = (lvl: LogLevel, msg: string) => {
        if (lvl === 'warn') warnings.push(msg);
      };
      const glb = inst
        ? toInstancedGLB(instanced, { textures: true, onLog })
        : toGLB(scene, { textures: true, onLog });
      expect(warnings).toHaveLength(1);
      expect(warnings[0]).toContain('imageCodec');

      const { json, binary } = parseGlb(glb);
      expect(json.images).toHaveLength(1);
      expect(json.images[0].mimeType).toBe('image/jpeg');
      const imgBytes = extractGlbImageBytes(json, binary, 0);
      expect(hasExifSignature(imgBytes)).toBe(false);
      expect(imgBytes).toEqual(cleanJpeg);

      const shiftMat = json.materials.find((m: any) => m.extras?.sketchup?.colorize_type === 0);
      const tintMat = json.materials.find((m: any) => m.extras?.sketchup?.colorize_type === 1);
      expect(shiftMat.pbrMetallicRoughness.baseColorFactor).toEqual([200 / 255, 90 / 255, 20 / 255, 1]);
      expect(tintMat.pbrMetallicRoughness.baseColorFactor).toEqual([30 / 255, 160 / 255, 60 / 255, 1]);
    }

    // 2) With imageCodec: decoded once, baked at quality 95 once per colour, white factor, EXIF stripped on plain.
    for (const inst of [false, true]) {
      const calls = { decodes: 0, encodes: [] as { quality: number; mimeType: string }[] };
      const codec = createTestJpegCodec(calls);
      const warnings: string[] = [];
      const onLog = (lvl: LogLevel, msg: string) => {
        if (lvl === 'warn') warnings.push(msg);
      };
      const glb = inst
        ? toInstancedGLB(instanced, { textures: true, imageCodec: codec, onLog })
        : toGLB(scene, { textures: true, imageCodec: codec, onLog });
      expect(warnings).toEqual([]);
      expect(calls.decodes).toBe(1);
      expect(calls.encodes).toEqual([
        { quality: 95, mimeType: 'image/jpeg' },
        { quality: 95, mimeType: 'image/jpeg' },
      ]);

      const { json, binary } = parseGlb(glb);
      expect(json.images).toHaveLength(3);
      const seenPixels = new Set<string>();
      for (const mat of json.materials) {
        const texRef = mat.pbrMetallicRoughness?.baseColorTexture;
        if (!texRef) continue;
        expect(mat.pbrMetallicRoughness.baseColorFactor).toEqual([1, 1, 1, 1]);
        const imgIdx = json.textures[texRef.index].source;
        expect(json.images[imgIdx].mimeType).toBe('image/jpeg');
        const blob = extractGlbImageBytes(json, binary, imgIdx);
        expect(hasExifSignature(blob)).toBe(false);
        const dec = codec.decode(blob, 'image/jpeg')!;
        expect(dec).not.toBeNull();
        seenPixels.add(
          `${dec.data[0]},${dec.data[1]},${dec.data[2]}|${dec.data[4]},${dec.data[5]},${dec.data[6]}`
        );
      }
      expect(seenPixels).toEqual(
        new Set(['200,40,40|40,40,200', '32,237,80|32,237,80', '172,253,46|253,46,172'])
      );
    }
  });

  it('rejects oversize PNG and JPEG headers (> 178,956,970 pixels) and corrupt images before decoding, keeping source bytes and tinted factor', () => {
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const errSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    try {
      // 1) Small 59-byte PNG declaring 40000x40000 (1.6B pixels > 178,956,970, never decoded)
      const oversizePng = makeDeclaredSizePng(40000, 40000);
      expect(oversizePng.length).toBeLessThan(100);

      const matShift = {
        name: 'OversizeShift',
        pbrMetallicRoughness: {
          baseColorFactor: [200 / 255, 90 / 255, 20 / 255, 1.0],
          baseColorTexture: { index: 0 },
          metallicFactor: 0,
          roughnessFactor: 0.8,
        },
        extras: { sketchup: { colorized: true, colorize_type: 0 } },
      };
      const matTint = {
        name: 'OversizeTint',
        pbrMetallicRoughness: {
          baseColorFactor: [30 / 255, 160 / 255, 60 / 255, 0.5],
          baseColorTexture: { index: 0 },
          metallicFactor: 0,
          roughnessFactor: 0.8,
        },
        extras: { sketchup: { colorized: true, colorize_type: 1 } },
      };

      const prim0: GlbPrimitive = {
        positions: new Float32Array([0, 0, 0, 1, 0, 0, 0, 1, 0]),
        normals: new Float32Array([0, 0, 1, 0, 0, 1, 0, 0, 1]),
        uvs: new Float32Array([0, 0, 1, 0, 0, 1]),
        indices: new Uint32Array([0, 1, 2]),
        materialIndex: 0,
        geomName: 'tri0',
      };
      const prim1: GlbPrimitive = {
        ...prim0,
        materialIndex: 1,
        geomName: 'tri1',
      };

      const baked: SkpScene = {
        sceneHierarchy: { name: '', definitionName: '', layer: '', positionMm: [0, 0, 0], properties: {}, children: [] },
        meshIndex: {},
        gltfMaterials: [matShift, matTint],
        textures: [{ data: oversizePng, mimeType: 'image/png', filename: 'oversize.png' }],
        glbPrimitives: [prim0, prim1],
      };

      const inst: InstancedScene = {
        bounds: null,
        sceneHierarchy: {
          name: 'Root',
          nameIsGenerated: false,
          definitionName: '',
          layer: 'Layer0',
          guid: '',
          matrix: [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1],
          positionMm: [0, 0, 0],
          properties: {},
          attributeDictionaries: {},
          meshResourceId: 'm0',
          children: [],
        },
        meshResources: [
          {
            id: 'm0',
            definitionId: 1,
            definitionName: 'Def',
            variantKey: 'v0',
            primitives: [
              {
                positions: prim0.positions,
                normals: prim0.normals,
                uvs: prim0.uvs,
                indices: prim0.indices,
                materialIndex: 0,
                unpaintedBack: false,
                backTriangles: [],
              },
              {
                positions: prim0.positions,
                normals: prim0.normals,
                uvs: prim0.uvs,
                indices: prim0.indices,
                materialIndex: 1,
                unpaintedBack: false,
                backTriangles: [],
              },
            ],
          },
        ],
        gltfMaterials: [matShift, matTint],
        textures: [{ data: oversizePng, mimeType: 'image/png', filename: 'oversize.png' }],
        layerHidden: {},
        unreadRootEntities: 0,
        pages: [],
        curveResources: [],
      };

      for (const isInst of [false, true]) {
        const warnings: string[] = [];
        const onLog = (lvl: LogLevel, msg: string) => {
          if (lvl === 'warn') warnings.push(msg);
        };
        const glb = isInst
          ? toInstancedGLB(inst, { textures: true, onLog })
          : toGLB(baked, { textures: true, onLog });

        expect(warnings).toHaveLength(1);
        expect(warnings[0]).toContain('40000x40000');
        expect(warnings[0]).toContain('1600000000');
        expect(warnings[0]).toContain('178956970');

        const { json, binary } = parseGlb(glb);
        expect(json.images).toHaveLength(1);
        expect(extractGlbImageBytes(json, binary, 0)).toEqual(oversizePng);
        expect(json.materials[0].pbrMetallicRoughness.baseColorTexture).toEqual({ index: 0 });
        expect(json.materials[0].pbrMetallicRoughness.baseColorFactor).toEqual([
          200 / 255,
          90 / 255,
          20 / 255,
          1.0,
        ]);
        expect(json.materials[1].pbrMetallicRoughness.baseColorTexture).toEqual({ index: 0 });
        expect(json.materials[1].pbrMetallicRoughness.baseColorFactor).toEqual([
          30 / 255,
          160 / 255,
          60 / 255,
          0.5,
        ]);
      }

      // 2) Oversize JPEG header (50000x50000 = 2.5B pixels > INT_MAX) never calls imageCodec.decode
      const oversizeJpg = makeTestSof0Jpeg(50000, 50000, new Uint8Array([10, 20, 30]));
      let jpegDecodeCalled = 0;
      const spyCodec: ImageCodec = {
        decode: () => {
          jpegDecodeCalled++;
          return null;
        },
        encode: () => new Uint8Array(0),
      };
      const jpgWarnings: string[] = [];
      const jpgScene: SkpScene = {
        ...baked,
        textures: [{ data: oversizeJpg, mimeType: 'image/jpeg', filename: 'huge.jpg' }],
      };
      const jpgGlb = toGLB(jpgScene, {
        textures: true,
        imageCodec: spyCodec,
        onLog: (lvl, msg) => {
          if (lvl === 'warn') jpgWarnings.push(msg);
        },
      });
      expect(jpegDecodeCalled).toBe(0);
      expect(jpgWarnings).toHaveLength(1);
      expect(jpgWarnings[0]).toContain('50000x50000');
      expect(jpgWarnings[0]).toContain('2500000000');
      expect(jpgWarnings[0]).toContain('178956970');
      const { json: jpgJson, binary: jpgBin } = parseGlb(jpgGlb);
      expect(extractGlbImageBytes(jpgJson, jpgBin, 0)).toEqual(oversizeJpg);

      // 3) Truncated 8-byte PNG keeps source bytes, keeps tinted factor, warns once, silent on console
      const truncatedPng = new Uint8Array([137, 80, 78, 71, 13, 10, 26, 10]);
      const corruptScene: SkpScene = {
        ...baked,
        gltfMaterials: [matShift],
        glbPrimitives: [prim0],
        textures: [{ data: truncatedPng, mimeType: 'image/png', filename: 'truncated.png' }],
      };
      toGLB(corruptScene, { textures: true });
      expect(warnSpy).not.toHaveBeenCalled();
      expect(errSpy).not.toHaveBeenCalled();

      const corruptWarnings: string[] = [];
      const corruptGlb = toGLB(corruptScene, {
        textures: true,
        onLog: (lvl, msg) => {
          if (lvl === 'warn') corruptWarnings.push(msg);
        },
      });
      expect(corruptWarnings).toHaveLength(1);
      const { json: cJson, binary: cBin } = parseGlb(corruptGlb);
      expect(extractGlbImageBytes(cJson, cBin, 0)).toEqual(truncatedPng);
      expect(cJson.materials[0].pbrMetallicRoughness.baseColorFactor).toEqual([
        200 / 255,
        90 / 255,
        20 / 255,
        1.0,
      ]);
    } finally {
      warnSpy.mockRestore();
      errSpy.mockRestore();
    }
  });
});
