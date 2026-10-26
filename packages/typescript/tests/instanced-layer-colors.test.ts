import { describe, it, expect } from 'vitest';
import { create } from '../src/create';
import { buildInstancedScene } from '../src/index';

/**
 * InstancedScene.layerColors - every layer the file defines, with its
 * colour, so a consumer can rebuild the tag list without a second parse.
 */
describe('buildInstancedScene layer colours', () => {
  it('lists every layer with its colour, used by a placement or not', () => {
    const builder = create();
    const walls = builder.addLayer('Walls', { color: [153, 0, 255] });
    builder.addLayer('Unused', { color: [255, 0, 0], hidden: true });
    const box = builder.addComponentDefinition('Box', (def) => {
      def.addFace([[0, 0, 0], [10, 0, 0], [10, 10, 0], [0, 10, 0]]);
    });
    builder.addInstance(box, { layer: walls });
    const bytes = builder.toBytes();

    const scene = buildInstancedScene(bytes.slice().buffer);

    expect(scene.layerColors).toMatchObject({ Walls: [153, 0, 255], Unused: [255, 0, 0] });
    expect(Object.keys(scene.layerColors ?? {})).toContain('Layer0');
    expect(scene.layerHidden).toMatchObject({ Walls: false, Unused: true });
    expect(scene.sceneHierarchy.children.map((node) => node.layer)).toEqual(['Walls']);
  });
});
