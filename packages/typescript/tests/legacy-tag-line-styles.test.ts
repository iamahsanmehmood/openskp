import { describe, it, expect } from 'vitest';
import * as fs from 'fs';
import * as path from 'path';
import { parseSkp } from '../src/index';
import { walk } from '../src/legacy';

/**
 * Tags with a line style in SketchUp 2019 and 2020 files (issue #514).
 *
 * Since SketchUp 2019 a tag can carry a line style ("Dash", "Dot", ...). A tag
 * that has one points at a `CCustomLineStyle` object right after its own
 * fields: nothing for a tag without a line style, the style written in full
 * the first time a tag uses it (a new-class record, then the object), a
 * class-ref for a second style, and a back-ref for a style that an earlier tag
 * already wrote. The style is an object in the walk like any other and takes a
 * store-map slot.
 */
const MODELS: Record<string, string[]> = {
  'sdk_saved_tag_line_styles_2020.skp': [
    'Tag 0 ',
    'Tag 1 Dash',
    'Tag 2 ',
    'Tag 3 Dash',
    'Tag 4 Dot',
    'Tag 5 Dash',
  ],
  'sdk_saved_tag_line_styles_2019.skp': [
    'Tag 0 ',
    'Tag 1 Dash',
    'Tag 2 ',
    'Tag 3 Dash',
    'Tag 4 Dot',
    'Tag 5 Dash',
  ],
  'sdk_saved_tag_line_styles_materials_2020.skp': [
    'Tag 0 Dash',
    'Tag 1 ',
    'Tag 2 Dot',
    'Tag 3 Dash',
    'Tag 4 Dot',
  ],
};

function readFixture(name: string): Uint8Array {
  const buf = fs.readFileSync(path.join(__dirname, 'fixtures', name));
  return new Uint8Array(buf.buffer, buf.byteOffset, buf.byteLength);
}

describe('Legacy MFC reader - tags with line styles (#514)', () => {
  for (const [name, expectedTags] of Object.entries(MODELS)) {
    it(`opens ${name} whose tags have line styles`, () => {
      const data = readFixture(name);
      const ab = data.buffer.slice(data.byteOffset, data.byteOffset + data.byteLength) as ArrayBuffer;
      const model = parseSkp(ab);
      expect(model.layers.map((layer) => layer.name)).toEqual(['Layer0', ...expectedTags]);
      expect(model.root.faces.length).toBe(expectedTags.length);
    });

    it(`keeps each face on its own tag in ${name}`, () => {
      // Reading the line styles takes store-map slots between the tags, so a
      // tag's slot (the id its faces carry) is only right when the walk
      // counted them.
      const { root, layers } = walk(readFixture(name));
      const tagOfSlot = new Map<number, string>(
        layers.map(([slot, layer]) => [slot, layer.name as string]),
      );
      const faces = root.filter(([, cls]) => cls === 'CFace').map(([, , value]) => value);
      expect(faces.map((f) => tagOfSlot.get(f.db.layer))).toEqual(expectedTags);
    });
  }
});
