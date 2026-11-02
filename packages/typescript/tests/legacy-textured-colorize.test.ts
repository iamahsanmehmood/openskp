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
});
