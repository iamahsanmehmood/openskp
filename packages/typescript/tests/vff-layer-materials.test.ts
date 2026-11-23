import { describe, it, expect } from 'vitest';
import * as fs from 'fs';
import * as path from 'path';
import { parseSkp } from '../src/index';

function loadFixture(name: string): ArrayBuffer {
  const buf = fs.readFileSync(path.join(__dirname, 'fixtures', name));
  return buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength) as ArrayBuffer;
}

describe('VFF tag colour records excluded from SkpModel.materials (#551)', () => {
  it('excludes Layer_Layer0 from arcs_2021.skp while keeping Layer0 colour', () => {
    const model = parseSkp(loadFixture('arcs_2021.skp'));
    expect(model.materials.map((m) => m.name)).toEqual([]);
    expect(model.layers.map((l) => [l.name, l.color.r, l.color.g, l.color.b])).toEqual([
      ['Layer0', 255, 84, 84],
    ]);
  });

  it('lists only Tex_TGA for texture_tga_2021.skp', () => {
    const model = parseSkp(loadFixture('texture_tga_2021.skp'));
    expect(model.materials.map((m) => m.name)).toEqual(['Tex_TGA']);
    expect(model.layers.map((l) => [l.name, l.color.r, l.color.g, l.color.b])).toEqual([
      ['Layer0', 255, 84, 84],
    ]);
  });

  it('preserves a genuine user material named Layer_Foo alongside tag Foo colour', () => {
    const model = parseSkp(loadFixture('sdk_saved_layer_named_material_2021.skp'));
    expect(model.materials.map((m) => [m.name, m.color.r, m.color.g, m.color.b])).toEqual([
      ['Layer_Foo', 200, 10, 20],
    ]);
    const layerMap = new Map(
      model.layers.map((l) => [l.name, [l.color.r, l.color.g, l.color.b]]),
    );
    expect(layerMap.get('Foo')).toEqual([30, 140, 250]);
    expect(layerMap.get('Layer0')).toEqual([255, 84, 84]);
  });
});
