import { describe, expect, it } from 'vitest';
import { toMTL, toOBJ } from '../src/obj';
import type { SkpScene } from '../src/model';

const scenarios: Array<{
  label: string;
  names: Array<string | undefined>;
  expected: string[];
}> = [
  {
    label: 'sanitized collisions and reserved suffixes',
    names: ['Wood/Oak', 'Wood_Oak', 'Wood_Oak_2'],
    expected: ['Wood_Oak', 'Wood_Oak_2_1', 'Wood_Oak_2'],
  },
  { label: 'non-conflicting names', names: ['Oak', 'Pine'], expected: ['Oak', 'Pine'] },
  { label: 'repeated names', names: ['A', 'A', 'A'], expected: ['A', 'A_2', 'A_3'] },
  {
    label: 'an unnamed material after an existing fallback name',
    names: ['Material_1', undefined],
    expected: ['Material_1', 'Material_1_2'],
  },
  {
    label: 'names empty after trimming',
    names: [' ', '   '],
    expected: ['default_material', 'default_material_2'],
  },
  { label: 'punctuation-only names', names: ['!!!', '???'], expected: ['___', '____2'] },
  { label: 'case-sensitive names', names: ['Oak', 'oak'], expected: ['Oak', 'oak'] },
];

describe('OBJ material names', () => {
  for (const { label, names, expected } of scenarios) {
    it('preserves distinct OBJ and MTL identifiers for ' + label, () => {
      const scene = {
        gltfMaterials: names.map(name => ({
          ...(name === undefined ? {} : { name }),
          pbrMetallicRoughness: { baseColorFactor: [1, 0, 0, 1] },
        })),
        glbPrimitives: names.map((_, materialIndex) => ({
          geomName: 'part' + materialIndex,
          materialIndex,
          positions: new Float32Array([0, 0, 0, 1, 0, 0, 0, 1, 0]),
          normals: new Float32Array(0),
          uvs: new Float32Array(0),
          indices: new Uint32Array([0, 1, 2]),
        })),
      } as SkpScene;
      const references = (text: string, prefix: string) =>
        text.split('\n').filter(line => line.startsWith(prefix)).map(line => line.slice(prefix.length));
      const mtlNames = references(toMTL(scene), 'newmtl ');
      const objNames = references(toOBJ(scene), 'usemtl ');
      expect(mtlNames).toEqual(expected);
      expect(objNames).toEqual(expected);
      expect(new Set(mtlNames).size).toBe(expected.length);
    });
  }
});
