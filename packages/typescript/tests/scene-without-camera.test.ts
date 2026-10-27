import { describe, it, expect } from 'vitest';
import * as fs from 'fs';
import * as path from 'path';
import { parseSkp } from '../src/index';

/**
 * A scene that doesn't save its camera (openskp#447).
 *
 * SketchUp lets a scene leave "Camera Location" unticked: such a scene saves no
 * camera and still carries its name and the layers it hides. The 2021+ reader
 * used to drop it.
 *
 * Fixture: scene_without_camera_2021.skp - one layer ("Furniture"), one face
 * and three scenes built through SketchUp's own library: "With camera" (eye
 * 100, -200, 80 looking at 5, 5, 0), "No camera" (SUSceneSetUseCamera off,
 * hides Furniture) and "Plain" (no camera, hides nothing). SketchUp reports the
 * same three scenes. No private content.
 */
function load(name: string): ArrayBuffer {
  const b = fs.readFileSync(path.join(__dirname, 'fixtures', name));
  return b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength) as ArrayBuffer;
}

describe('a scene without a saved camera', () => {
  const pages = parseSkp(load('scene_without_camera_2021.skp')).pages;

  it('reads every scene', () => {
    expect(pages.map((p) => p.name)).toEqual(['With camera', 'No camera', 'Plain']);
  });

  it('still reads a saved camera', () => {
    expect(pages[0].eye).toEqual([100, -200, 80]);
    expect(pages[0].target).toEqual([5, 5, 0]);
  });

  it('keeps the hidden layers of a scene that has no camera', () => {
    expect(pages[1].eye).toBeNull();
    expect(pages[1].target).toBeNull();
    expect(pages[1].up).toBeNull();
    expect(pages[1].hiddenLayers).toEqual(['Furniture']);
    expect(pages[2].eye).toBeNull();
    expect(pages[2].hiddenLayers).toEqual([]);
  });
});
