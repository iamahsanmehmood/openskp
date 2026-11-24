import { describe, it, expect } from 'vitest';
import { create, buildScene, buildInstancedScene } from '../src/index';

const SQUARE: [number, number, number][] = [
  [0, 0, 0],
  [10, 0, 0],
  [10, 10, 0],
  [0, 10, 0],
];

function toArrayBuffer(bytes: Uint8Array): ArrayBuffer {
  return bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer;
}

function paintedModelBuffer(): ArrayBuffer {
  const builder = create();
  const red = builder.addMaterial('Red', [200, 10, 10, 255]);
  const blue = builder.addMaterial('Blue', [10, 10, 200, 128]);
  const leaf = builder.addComponentDefinition('Leaf', (def) => {
    def.addFace(SQUARE);
  });
  const outer = builder.addComponentDefinition('Outer', (def) => {
    def.addInstance(leaf, { name: 'InheritsPaint' });
    def.addInstance(leaf, { name: 'OwnPaint', material: blue, translation: [20, 0, 0] });
  });
  builder.addInstance(outer, { name: 'Painted', material: red });
  return toArrayBuffer(builder.toBytes());
}

describe('Classic instance paint and tag (#596)', () => {
  it('applies paint and inherits it (including translucency) across nested components', () => {
    const ab = paintedModelBuffer();
    const baked = buildScene(ab);
    const instanced = buildInstancedScene(ab);

    const bakedIndices = baked.glbPrimitives.map((p) => p.materialIndex);
    const instancedIndices = instanced.meshResources.flatMap((r) =>
      r.primitives.map((p) => p.materialIndex)
    );

    const round6 = (v: number) => Math.round(v * 1e6) / 1e6;
    for (const [gltfMats, indices] of [
      [baked.gltfMaterials, bakedIndices],
      [instanced.gltfMaterials, instancedIndices],
    ] as const) {
      const summaries = new Set(
        indices.map((i) => {
          const m = gltfMats[i] as Record<string, any>;
          const bcf = m.pbrMetallicRoughness.baseColorFactor as number[];
          return JSON.stringify([
            round6(bcf[0]),
            round6(bcf[1]),
            round6(bcf[2]),
            round6(bcf[3]),
            Boolean(m.doubleSided),
            m.alphaMode ?? 'OPAQUE',
          ]);
        })
      );
      expect(summaries).toEqual(
        new Set([
          JSON.stringify([
            round6(200 / 255),
            round6(10 / 255),
            round6(10 / 255),
            1,
            true,
            'OPAQUE',
          ]),
          JSON.stringify([
            round6(10 / 255),
            round6(10 / 255),
            round6(200 / 255),
            round6(128 / 255),
            true,
            'BLEND',
          ]),
        ])
      );
    }
  });

  it('gives each paint its own instanced mesh resource', () => {
    const scene = buildInstancedScene(paintedModelBuffer());
    const byName = new Map(scene.sceneHierarchy.children[0].children.map((n) => [n.name, n]));
    const inherits = byName.get('InheritsPaint');
    const own = byName.get('OwnPaint');
    expect(inherits?.meshResourceId).toBeDefined();
    expect(own?.meshResourceId).toBeDefined();
    expect(inherits?.meshResourceId).not.toBe(own?.meshResourceId);
  });

  it("places each instance on its own tag rather than its parent's", () => {
    const builder = create();
    const walls = builder.addLayer('Walls');
    const part = builder.addComponentDefinition('Part', (def) => {
      def.addFace(SQUARE);
    });
    builder.addInstance(part, { name: 'Tagged', layer: walls });
    builder.addInstance(part, { name: 'Untagged', translation: [20, 0, 0] });

    const ab = toArrayBuffer(builder.toBytes());
    for (const built of [buildScene(ab), buildInstancedScene(ab)]) {
      const layers = Object.fromEntries(
        built.sceneHierarchy.children.map((n) => [n.name, n.layer])
      );
      expect(layers).toEqual({ Tagged: 'Walls', Untagged: 'Layer0' });
    }
  });
});
