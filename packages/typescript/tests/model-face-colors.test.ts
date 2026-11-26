import { describe, it, expect } from 'vitest';
import * as fs from 'fs';
import * as path from 'path';
import { SkpFile, create, parseSkp } from '../src/index';

type Rgb = [number, number, number];

function loadFixture(name: string): ArrayBuffer {
  const bytes = fs.readFileSync(path.join(__dirname, 'fixtures', name));
  return bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer;
}

const WRITTEN: [Rgb, Rgb] = [
  [11, 122, 233],
  [201, 102, 3],
];
const APP_DEFAULT: [Rgb, Rgb] = [
  [255, 255, 255],
  [164, 178, 187],
];
const SDK_DEFAULT: [Rgb, Rgb] = [
  [225, 225, 200],
  [128, 128, 200],
];

const CASES: [string, [Rgb, Rgb]][] = [
  ['face_colors_v4.skp', WRITTEN],
  ['face_colors_v5.skp', WRITTEN],
  ['face_colors_v6.skp', WRITTEN],
  ['face_colors_2016.skp', WRITTEN],
  ['face_colors_2017.skp', WRITTEN],
  ['face_colors_2018.skp', WRITTEN],
  ['face_colors_2019.skp', WRITTEN],
  ['face_colors_2020.skp', WRITTEN],
  ['face_colors_2021.skp', WRITTEN],
  ['capilla_quiroz_v17.skp', APP_DEFAULT],
  ['gondola_v20.skp', APP_DEFAULT],
  ['zero_material_custom_layer_v18.skp', APP_DEFAULT],
  ['SU_File.skp', APP_DEFAULT],
  ['nesting_v6.skp', SDK_DEFAULT],
  ['nesting_2013.skp', SDK_DEFAULT],
  ['nesting_2021.skp', SDK_DEFAULT],
  ['legacy_v4_synthetic.skp', SDK_DEFAULT],
  ['legacy_v5_synthetic.skp', SDK_DEFAULT],
  ['legacy_v2019_synthetic.skp', SDK_DEFAULT],
];

describe('SkpModel faceFrontColor and faceBackColor (#533)', () => {
  it.each(CASES)('matches SketchUp FaceFrontColor / FaceBackColor on %s', (name, expected) => {
    const model = parseSkp(loadFixture(name));
    expect([model.faceFrontColor, model.faceBackColor]).toEqual(expected);
  });

  it('returns null on SketchUp 3 files', () => {
    const model = parseSkp(loadFixture('legacy_v3_synthetic.skp'));
    expect(model.faceFrontColor).toBeNull();
    expect(model.faceBackColor).toBeNull();
  });

  it('reads current rendering-options colours rather than saved styles', () => {
    const model = parseSkp(loadFixture('face_colors_2021.skp'));
    const stylePairs = model.styles.map((s) => [s.frontColor, s.backColor]);
    expect(stylePairs).toContainEqual(SDK_DEFAULT);
    expect(stylePairs).toContainEqual(WRITTEN);
    expect([model.faceFrontColor, model.faceBackColor]).toEqual(WRITTEN);
  });
});

const COLOR_BY_TAG_CASES: [string, boolean][] = [
  ['color_by_tag_v4.skp', true],
  ['color_by_tag_v5.skp', true],
  ['color_by_tag_v6.skp', true],
  ['color_by_tag_2017.skp', true],
  ['color_by_tag_2019.skp', true],
  ['color_by_tag_2020.skp', true],
  ['color_by_tag_2020_off.skp', false],
  ['color_by_tag_2021.skp', true],
  ['color_by_tag_2021_style.skp', true],
  ['color_by_tag_2021_off.skp', false],
  ['legacy_v3_synthetic.skp', false],
  ...CASES.map(([name]): [string, boolean] => [name, false]),
];

describe('SkpModel colorByTag (#697)', () => {
  it.each(COLOR_BY_TAG_CASES)('matches SketchUp DisplayColorByLayer on %s', (name, expected) => {
    const model = parseSkp(loadFixture(name));
    expect(model.colorByTag).toBe(expected);
  });

  it('round-trips through classic create() styleItems 7011', () => {
    const quad: [number, number, number][] = [
      [0, 0, 0],
      [10, 0, 0],
      [10, 10, 0],
      [0, 10, 0],
    ];
    for (const [styleItems, expected] of [
      [undefined, false],
      [{ 7011: 0 }, false],
      [{ 7011: 1 }, true],
    ] as const) {
      const builder = create();
      if (styleItems !== undefined) {
        builder.setDocumentOptions({ styleItems });
      }
      builder.addFace(quad);
      const bytes = builder.toBytes();
      const ab = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer;
      expect(parseSkp(ab).colorByTag).toBe(expected);
    }
  });

  it.each([
    ['color_by_tag_2020.skp', 'color_by_tag_2020_off.skp'],
    ['color_by_tag_2021.skp', 'color_by_tag_2021_off.skp'],
  ])('does not change scene output between %s and %s', (onName, offName) => {
    const skpOn = SkpFile.fromBuffer(loadFixture(onName));
    const skpOff = SkpFile.fromBuffer(loadFixture(offName));
    expect(skpOn.parse().colorByTag).toBe(true);
    expect(skpOff.parse().colorByTag).toBe(false);
    expect(skpOn.buildScene().gltfMaterials).toEqual(skpOff.buildScene().gltfMaterials);
    expect(skpOn.buildInstancedScene().gltfMaterials).toEqual(skpOff.buildInstancedScene().gltfMaterials);
  });
});

