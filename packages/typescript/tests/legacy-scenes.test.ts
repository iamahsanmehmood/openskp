import { describe, it, expect } from 'vitest';
import * as fs from 'fs';
import * as path from 'path';
import { parseSkp } from '../src/index';

/**
 * Scenes in a classic (pre-2021) file (openskp#445).
 *
 * - gondola_v20.skp: SketchUp 2020's own save, one scene ("Cena1", capture flags 0x1FF) with a
 *   parallel-projection camera.
 * - sdk_saved_2019/2020.skp: built and saved through SketchUp's C API, which writes the scene
 *   class at a newer schema than the desktop application. Three scenes: "With camera" (eye
 *   100, -200, 80 looking at 5, 5, 0), "No camera" (no saved camera, hides the layer
 *   "Furniture") and "Plain". SketchUp's own library reads the same scenes back.
 * - sdk_saved_sections_2019/2020.skp: the same model with two scenes, the first capturing
 *   everything a scene can (flags 0x1FF), so the second is only found if the first was read to
 *   its last byte. No private content.
 */
function load(name: string): ArrayBuffer {
  const b = fs.readFileSync(path.join(__dirname, 'fixtures', name));
  return b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength) as ArrayBuffer;
}

describe('gondola_v20.skp', () => {
  it('reads its scene with the camera', () => {
    const { pages } = parseSkp(load('gondola_v20.skp'));
    expect(pages.length).toBe(1);
    const p = pages[0];
    expect(p.name).toBe('Cena1');
    expect(p.eye![0]).toBeCloseTo(2093.78994893, 6);
    expect(p.eye![1]).toBeCloseTo(412.425280402, 6);
    expect(p.eye![2]).toBeCloseTo(135.404197356, 6);
    expect(p.target![0]).toBeCloseTo(1802.70152746, 6);
    expect(p.parallel).toBe(true);
    expect(p.orthoHeight).toBeCloseTo(180.650232984, 6);
    expect(p.hiddenLayers).toEqual([]);
  });
});

describe.each(['sdk_saved_2019.skp', 'sdk_saved_2020.skp'])('%s', (name) => {
  it('reads its scenes with their cameras and hidden layers', () => {
    const { pages } = parseSkp(load(name));
    expect(pages.map((p) => p.name)).toEqual(['With camera', 'No camera', 'Plain']);
    const [withCamera, noCamera, plain] = pages;
    expect(withCamera.eye).toEqual([100, -200, 80]);
    expect(withCamera.target).toEqual([5, 5, 0]);
    expect(withCamera.fov).toBe(30);
    expect(withCamera.parallel).toBe(false);
    expect(withCamera.hiddenLayers).toEqual([]);
    expect(noCamera.eye).toBeNull();
    expect(noCamera.target).toBeNull();
    expect(noCamera.hiddenLayers).toEqual(['Furniture']);
    expect(plain.eye).toBeNull();
    expect(plain.hiddenLayers).toEqual([]);
  });
});

describe.each(['sdk_saved_sections_2019.skp', 'sdk_saved_sections_2020.skp'])('%s', (name) => {
  it('reads a scene capturing every property to its last byte', () => {
    const { pages } = parseSkp(load(name));
    expect(pages.map((p) => p.name)).toEqual(['Everything', 'Plain']);
    expect(pages[0].eye).toEqual([100, -200, 80]);
    expect(pages[0].hiddenLayers).toEqual(['Furniture']);
    expect(pages[1].eye).toBeNull();
  });
});
