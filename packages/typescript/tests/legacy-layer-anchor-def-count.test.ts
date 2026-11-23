import { describe, it, expect } from 'vitest';
import { create, parseSkp } from '../src/index';

function toBuffer(bytes: Uint8Array): ArrayBuffer {
  return bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer;
}

function buildModel(nTags: number, nDefs: number): Uint8Array {
  const builder = create();
  const mat = builder.addMaterial('M', [200, 100, 50, 255]);
  const layers: number[] = [];
  for (let i = 0; i < nTags; i++) {
    layers.push(builder.addLayer(`T${i}`));
  }
  const defs = [];
  for (let i = 0; i < nDefs; i++) {
    defs.push(
      builder.addComponentDefinition(`P${i}`, (comp) => {
        comp.addFace(
          [
            [0, 0, 0],
            [1 + i, 0, 0],
            [1 + i, 1, 0],
            [0, 1, 0],
          ],
          { material: mat }
        );
      })
    );
  }
  const container = builder.addComponentDefinition('Container', (c) => {
    for (let i = 0; i < defs.length; i++) {
      c.addInstance(defs[i], {
        name: `i${i}`,
        translation: [0, 2 * i, 0],
        layer: layers[0],
      });
    }
  });
  builder.addInstance(container, { name: 'root', layer: layers[0] });
  return builder.toBytes();
}

// Regression (#534 / #285): after the last CLayer the reader sees
// `<anchor u16><def_count u32>` and then the first CComponentDefinition.
// skipClayerParentRef consumed the anchor as a v18 "parent" back-ref whenever
// both u16s at that position pointed at CLayer objects - which also happens
// when the definition count (11, 12, or 13) coincides with a tag's slot.
describe('Legacy CLayer anchor vs definition count (#534)', () => {
  for (const nTags of [1, 2]) {
    for (const nDefs of [10, 11, 12, 13, 14]) {
      it(`round-trips with ${nTags} tag(s) and ${nDefs} part definitions`, () => {
        const model = parseSkp(toBuffer(buildModel(nTags, nDefs)));
        const expectedLayers = ['Layer0', ...Array.from({ length: nTags }, (_, i) => `T${i}`)];
        expect(model.layers.map((l) => l.name)).toEqual(expectedLayers);
        expect(model.definitions.size).toBe(nDefs + 1);
      });
    }
  }
});
