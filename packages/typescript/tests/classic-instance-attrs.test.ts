import { describe, it, expect } from 'vitest';
import { create, buildScene, buildInstancedScene, Point3 } from '../src/index';

const WALL_RECT: Point3[] = [
  [0, 0, 0],
  [20, 0, 0],
  [20, 10, 0],
  [0, 10, 0],
];

function toArrayBuffer(bytes: Uint8Array): ArrayBuffer {
  return bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer;
}

function buildFixture(): ArrayBuffer {
  const b = create();
  const wall = b.addComponentDefinition('Wall', (def) => {
    def.addFace(WALL_RECT);
  });
  b.addInstance(wall, {
    name: 'W1',
    attributes: { name: 'CustomOverride', sku: 'B42' },
  });
  b.addInstance(wall, {
    name: 'W2',
    translation: [30, 0, 0],
    attributes: { label: 'TS-9', count: 3 },
    attributeDictName: 'TechSteel',
  });
  b.addInstance(wall, {
    name: 'W3',
    translation: [60, 0, 0],
    attributes: { name: 'IgnoredDynamic', lenx: 20 },
    attributeDictName: 'dynamic_attributes',
  });
  return toArrayBuffer(b.toBytes());
}

describe('classic instance attribute dictionaries and names in scenes (#633)', () => {
  it('populates attributeDictionaries and name overrides in both scene types', () => {
    const buf = buildFixture();
    const baked = buildScene(buf);
    const instanced = buildInstancedScene(buf);

    for (const nodes of [baked.sceneHierarchy.children, instanced.sceneHierarchy.children]) {
      expect(nodes.map((n) => n.name)).toEqual(['CustomOverride', 'TS-9', 'W3']);
      expect(nodes.map((n) => n.nameIsGenerated)).toEqual([false, false, false]);
      expect(nodes[0].attributeDictionaries).toEqual({
        attributes: { name: 'CustomOverride', sku: 'B42' },
      });
      expect(nodes[0].properties).toEqual({});
      expect(nodes[1].attributeDictionaries).toEqual({
        TechSteel: { label: 'TS-9', count: '3' },
      });
      expect(nodes[1].properties).toEqual({});
      expect(nodes[2].attributeDictionaries).toEqual({});
      expect(nodes[2].properties).toEqual({ name: 'IgnoredDynamic', lenx: '20' });
    }

    const byName = new Map(
      baked.surfacePrimitives!.map((p) => {
        const meta = baked.meshIndex[p.geomName];
        return [meta.name, meta] as const;
      })
    );
    expect(new Set(byName.keys())).toEqual(new Set(['CustomOverride', 'TS-9', 'W3']));
    expect(byName.get('CustomOverride')!.attributeDictionaries).toEqual({
      attributes: { name: 'CustomOverride', sku: 'B42' },
    });
    expect(byName.get('TS-9')!.attributeDictionaries).toEqual({
      TechSteel: { label: 'TS-9', count: '3' },
    });
    expect(byName.get('W3')!.attributeDictionaries).toEqual({});
    expect(byName.get('W3')!.properties).toEqual({ name: 'IgnoredDynamic', lenx: '20' });
  });
});
