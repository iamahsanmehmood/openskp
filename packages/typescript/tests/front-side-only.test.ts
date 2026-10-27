import { describe, it, expect } from 'vitest';
import { create } from '../src/create';
import { buildInstancedScene, buildScene } from '../src/index';

/**
 * SceneOptions.frontSideOnly - a face whose sides are painted differently is
 * emitted once, double-sided with its front material, instead of a front
 * plus a reversed back-side copy.
 */
function twoToned(): ArrayBuffer {
  const builder = create();
  const red = builder.addMaterial('Red', [255, 0, 0]);
  const blue = builder.addMaterial('Blue', [0, 0, 255]);
  builder.addFace([[0, 0, 0], [10, 0, 0], [10, 10, 0], [0, 10, 0]], { material: red, backMaterial: blue });
  return builder.toBytes().slice().buffer;
}

interface GltfMaterial {
  doubleSided?: boolean;
  pbrMetallicRoughness: { baseColorFactor: number[] };
}

function emitted(options?: { frontSideOnly?: boolean }) {
  const scene = buildInstancedScene(twoToned(), options);
  const materials = scene.gltfMaterials as GltfMaterial[];
  const primitives = scene.meshResources.flatMap((resource) => resource.primitives);
  return {
    triangles: primitives.reduce((sum, primitive) => sum + primitive.indices.length / 3, 0),
    materials: primitives.map(({ materialIndex }) => ({
      rgb: materials[materialIndex].pbrMetallicRoughness.baseColorFactor.slice(0, 3),
      doubleSided: materials[materialIndex].doubleSided === true,
    })),
  };
}

describe('SceneOptions.frontSideOnly', () => {
  it('emits a two-toned face as a front and a reversed back by default', () => {
    const { triangles, materials } = emitted();
    expect(triangles).toBe(4);
    expect(materials).toEqual([
      { rgb: [1, 0, 0], doubleSided: false },
      { rgb: [0, 0, 1], doubleSided: false },
    ]);
  });

  it('emits it once, double-sided with the front material, when asked', () => {
    const { triangles, materials } = emitted({ frontSideOnly: true });
    expect(triangles).toBe(2);
    expect(materials).toEqual([{ rgb: [1, 0, 0], doubleSided: true }]);
  });

  it('applies to the baked scene too', () => {
    const triangles = (options?: { frontSideOnly?: boolean }) =>
      buildScene(twoToned(), options).glbPrimitives.reduce((sum, primitive) => sum + primitive.indices.length / 3, 0);
    expect(triangles()).toBe(4);
    expect(triangles({ frontSideOnly: true })).toBe(2);
  });
});
