import { describe, it, expect } from 'vitest';
import * as fs from 'fs';
import * as path from 'path';
import { parseSkp } from '../src/index';

/**
 * Textured and colorized materials in a classic file read as SketchUp reads them.
 *
 * Fixture legacy_textured_colorize_2017.skp: four materials with the same 2x1
 * texture, made and saved as a SketchUp 2017 file by SketchUp's own library:
 * plain (type 1, textured), tint and shift (type 2, colorized, colorize mode
 * tint 1 and shift 0) and tint_half (a tint at opacity 0.5). Before, every
 * classic textured material read as colorized and the mode was always tint (96
 * of 117 textured materials of the differential test files disagreed with
 * SketchUp's own SUMaterialGetType). No private content.
 */
describe('a classic file with textured and colorized materials', () => {
  it('reads the texture type, the colorize mode and the paint colour', () => {
    const b = fs.readFileSync(path.join(__dirname, 'fixtures', 'legacy_textured_colorize_2017.skp'));
    const model = parseSkp(b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength) as ArrayBuffer);
    const got = Object.fromEntries(
      model.materials.map((m) => [
        m.name,
        [[m.color.r, m.color.g, m.color.b], m.colorized, m.colorizeType, Math.round(m.transparency * 100) / 100],
      ]),
    );
    expect(got).toEqual({
      plain: [[120, 40, 120], false, 0, 1],
      tint: [[30, 160, 60], true, 1, 1],
      shift: [[200, 90, 20], true, 0, 1],
      tint_half: [[10, 20, 230], true, 1, 0.5],
    });
  });

  // blender-openskp#9: a SketchUp 2017 textured material stores two colours side by side and the first
  // one's alpha byte can be 0 (as in the box the report attached). Taking the colour and its alpha from
  // the first made every textured material fully transparent in the Blender add-on. Here the first colour
  // of `plain` (120, 40, 120) gets alpha 0, as in that file.
  it('takes the colour alpha from the material colour, not the image average', () => {
    const b = fs.readFileSync(path.join(__dirname, 'fixtures', 'legacy_textured_colorize_2017.skp'));
    const data = new Uint8Array(b);
    const pair = Buffer.from('782878ff00782878ff', 'hex'); // image average, 00, material colour (alpha 255)
    const at = b.indexOf(pair);
    expect(at).toBeGreaterThan(0);
    expect(b.indexOf(pair, at + 1)).toBe(-1);
    data[at + 3] = 0;
    const model = parseSkp(data.buffer.slice(data.byteOffset, data.byteOffset + data.byteLength) as ArrayBuffer);
    const plain = model.materials.find((m) => m.name === 'plain')!;
    expect(plain.color).toEqual({ r: 120, g: 40, b: 120, a: 255 });
  });
});
