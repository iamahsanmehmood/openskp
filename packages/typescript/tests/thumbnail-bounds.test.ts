import { describe, it, expect } from 'vitest';
import * as fs from 'fs';
import * as path from 'path';
import {
  extractThumbnail,
  buildScene,
  buildInstancedScene,
  parseSkp,
  SkpFile,
} from '../src/index';

/**
 * Two things a catalogue or asset browser needs and previously had to
 * reconstruct itself: the preview image SketchUp already stored in the
 * file, and the model's overall size.
 */

const fixturePath = (name: string) => path.join(__dirname, 'fixtures', name);
const readFixture = (name: string) => {
  const buf = fs.readFileSync(fixturePath(name));
  return buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength);
};

/** Modern VFF containers, which carry named thumbnail entries. */
const VFF_FIXTURES = ['SU_File.skp', 'Untitled.skp'];
/** Legacy MFC containers, which store images without entry names. */
const LEGACY_FIXTURES = [
  'gondola_v20.skp',
  'capilla_quiroz_v17.skp',
  'single_material_v17.skp',
  'blank_v17.skp',
];

describe('extractThumbnail', () => {
  for (const name of VFF_FIXTURES) {
    it(`returns the stored preview for ${name}`, () => {
      const thumb = extractThumbnail(readFixture(name));

      expect(thumb).not.toBeNull();
      expect(thumb!.mimeType).toBe('image/png');
      expect(thumb!.width).toBe(256);
      expect(thumb!.height).toBe(256);
      expect(thumb!.data.length).toBeGreaterThan(0);

      // real PNG signature, not just something that parsed
      expect(Array.from(thumb!.data.subarray(0, 8))).toEqual([
        0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a,
      ]);
    });

    it(`prefers the clean model image over the axes preview for ${name}`, () => {
      // preview_thumbnail.png has SketchUp's red/green/blue axis lines
      // drawn in, which is clutter on a catalogue card. Both exist in
      // these fixtures, so the preference is observable.
      const thumb = extractThumbnail(readFixture(name));
      expect(thumb!.source).toBe('model');
    });
  }

  for (const name of LEGACY_FIXTURES) {
    it(`returns null for the legacy container ${name}`, () => {
      // Legacy files do embed PNGs, but with no entry names a thumbnail
      // cannot be told apart from a material texture. null is honest;
      // returning a texture as a "preview" would not be.
      expect(extractThumbnail(readFixture(name))).toBeNull();
    });
  }

  it('throws on a file that is not a SketchUp file at all', () => {
    const notSkp = new Uint8Array([1, 2, 3, 4, 5, 6, 7, 8]).buffer;
    expect(() => extractThumbnail(notSkp)).toThrow(/bad header magic/);
  });

  it('does not require parsing geometry', () => {
    // The whole point: a catalogue lists thousands of files, and paying
    // parseSkp()/buildScene() per file to get a cover image would be
    // absurd. Guard the cheap path by timing it against a full parse.
    const ab = readFixture('Untitled.skp');

    const t0 = performance.now();
    for (let i = 0; i < 5; i++) extractThumbnail(ab);
    const thumbMs = performance.now() - t0;

    const t1 = performance.now();
    parseSkp(ab);
    const parseMs = performance.now() - t1;

    // Deliberately loose - this asserts an order-of-magnitude property,
    // not a benchmark, so it does not turn into a flaky CI failure.
    expect(thumbMs / 5).toBeLessThan(parseMs);
  });

  it('is reachable from SkpFile', () => {
    const thumb = SkpFile.open(fixturePath('SU_File.skp')).thumbnail();
    expect(thumb).not.toBeNull();
    expect(thumb!.width).toBe(256);
  });
});

describe('scene bounds', () => {
  const GEOMETRY_FIXTURES = [
    'SU_File.skp',
    'Untitled.skp',
    'capilla_quiroz_v17.skp',
    'gondola_v20.skp',
  ];

  for (const name of GEOMETRY_FIXTURES) {
    it(`matches a manual sweep of the baked positions for ${name}`, () => {
      const scene = buildScene(readFixture(name));
      expect(scene.bounds).not.toBeNull();

      const min = [Infinity, Infinity, Infinity];
      const max = [-Infinity, -Infinity, -Infinity];
      for (const prim of scene.glbPrimitives) {
        for (let i = 0; i < prim.positions.length; i += 3) {
          for (let k = 0; k < 3; k++) {
            const v = prim.positions[i + k];
            if (v < min[k]) min[k] = v;
            if (v > max[k]) max[k] = v;
          }
        }
      }

      for (let k = 0; k < 3; k++) {
        expect(scene.bounds!.min[k]).toBeCloseTo(min[k], 6);
        expect(scene.bounds!.max[k]).toBeCloseTo(max[k], 6);
        expect(scene.bounds!.size[k]).toBeCloseTo(max[k] - min[k], 6);
        expect(scene.bounds!.center[k]).toBeCloseTo((min[k] + max[k]) / 2, 6);
      }
    });

    it(`agrees between the baked and instanced builders for ${name}`, () => {
      // The instanced builder keeps geometry in local space, so its bounds
      // must come from transforming resources by their node matrices. If it
      // reported the union of local boxes instead, this would diverge.
      const baked = buildScene(readFixture(name));
      const instanced = buildInstancedScene(readFixture(name));

      expect(instanced.bounds).not.toBeNull();
      for (let k = 0; k < 3; k++) {
        // 1e-5 m, the same float32 tolerance the parity tests use: the two
        // paths reach float32 at different points in the pipeline.
        expect(instanced.bounds!.min[k]).toBeCloseTo(baked.bounds!.min[k], 5);
        expect(instanced.bounds!.max[k]).toBeCloseTo(baked.bounds!.max[k], 5);
      }
    });
  }

  it('is null for a model with no geometry', () => {
    // blank_v17 has no faces, so an empty scene must be distinguishable
    // from one sitting at the origin.
    const scene = buildScene(readFixture('blank_v17.skp'));
    expect(scene.glbPrimitives.length).toBe(0);
    expect(scene.bounds).toBeNull();
    expect(buildInstancedScene(readFixture('blank_v17.skp')).bounds).toBeNull();
  });

  it('reports a usable model size', () => {
    // The catalogue use case: "how big is this block?"
    const scene = buildScene(readFixture('gondola_v20.skp'));
    const { size } = scene.bounds!;
    for (const s of size) {
      expect(s).toBeGreaterThan(0);
      expect(Number.isFinite(s)).toBe(true);
    }
  });
});
