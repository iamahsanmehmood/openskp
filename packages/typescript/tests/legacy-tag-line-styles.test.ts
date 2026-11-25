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
 * fields (both plain-colour and textured `CLayer` records): nothing for a tag
 * without a line style, the style written in full the first time a tag uses it
 * (a new-class record, then the object), a class-ref for a second style, and a
 * back-ref for a style that an earlier tag already wrote. The style is an
 * object in the walk like any other and takes a store-map slot.
 */
interface ExpectedModel {
  dir: string;
  tags: string[];
  materials: Array<[string, [number, number, number, number]]>;
}

const FIXTURES = path.join(__dirname, 'fixtures');
const CPP_FIXTURES = path.resolve(__dirname, '../../cpp/tests/fixtures');

const MODELS: Record<string, ExpectedModel> = {
  'sdk_saved_tag_line_styles_2020.skp': {
    dir: FIXTURES,
    tags: ['Tag 0 ', 'Tag 1 Dash', 'Tag 2 ', 'Tag 3 Dash', 'Tag 4 Dot', 'Tag 5 Dash'],
    materials: [],
  },
  'sdk_saved_tag_line_styles_2019.skp': {
    dir: FIXTURES,
    tags: ['Tag 0 ', 'Tag 1 Dash', 'Tag 2 ', 'Tag 3 Dash', 'Tag 4 Dot', 'Tag 5 Dash'],
    materials: [],
  },
  'sdk_saved_tag_line_styles_materials_2020.skp': {
    dir: FIXTURES,
    tags: ['Tag 0 Dash', 'Tag 1 ', 'Tag 2 Dot', 'Tag 3 Dash', 'Tag 4 Dot'],
    materials: [
      ['Paint 0', [10, 20, 30, 255]],
      ['Paint 1', [50, 20, 30, 255]],
      ['Paint 2', [90, 20, 30, 255]],
    ],
  },
  'sdk_saved_textured_tag_line_styles_2019.skp': {
    dir: CPP_FIXTURES,
    tags: ['Tag 0 Tex Dash', 'Tag 1 Tex ', 'Tag 2 Tex Dot', 'Tag 3 Tex Dash', 'Tag 4 Plain Dot'],
    materials: [],
  },
  'sdk_saved_textured_tag_line_styles_2020.skp': {
    dir: CPP_FIXTURES,
    tags: ['Tag 0 Tex Dash', 'Tag 1 Tex ', 'Tag 2 Tex Dot', 'Tag 3 Tex Dash', 'Tag 4 Plain Dot'],
    materials: [],
  },
};

function readFixture(dir: string, name: string): Uint8Array {
  const buf = fs.readFileSync(path.join(dir, name));
  return new Uint8Array(buf.buffer, buf.byteOffset, buf.byteLength);
}

describe('Legacy MFC reader - tags with line styles (#514)', () => {
  for (const [name, expected] of Object.entries(MODELS)) {
    it(`opens ${name} with tags, faces per tag, and materials matching Python`, () => {
      const data = readFixture(expected.dir, name);
      const ab = data.buffer.slice(data.byteOffset, data.byteOffset + data.byteLength) as ArrayBuffer;
      const model = parseSkp(ab);
      expect(model.layers.map((layer) => layer.name)).toEqual(['Layer0', ...expected.tags]);
      expect(model.root.faces.map((face) => face.layer)).toEqual(expected.tags);
      expect(
        model.materials.map((m) => [m.name, [m.color.r, m.color.g, m.color.b, m.color.a]]),
      ).toEqual(expected.materials);
    });

    it(`keeps each face on its own tag slot in ${name}`, () => {
      // Reading the line styles takes store-map slots between the tags, so a
      // tag's slot (the id its faces carry) is only right when the walk
      // counted them.
      const { root, layers } = walk(readFixture(expected.dir, name));
      const tagOfSlot = new Map<number, string>(
        layers.map(([slot, layer]) => [slot, layer.name as string]),
      );
      const faces = root.filter(([, cls]) => cls === 'CFace').map(([, , value]) => value);
      expect(faces.map((f) => tagOfSlot.get(f.db.layer))).toEqual(expected.tags);
    });
  }
});
