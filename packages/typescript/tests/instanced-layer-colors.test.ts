import { describe, it, expect } from 'vitest';
import { create } from '../src/create';
import { buildInstancedScene } from '../src/index';

/**
 * InstancedScene.layerColors - every layer the file defines, with its
 * colour - and InstancedNode.ownLayer - the layer an instance carries
 * itself - so a consumer can rebuild the tag list and each instance's own
 * tag without a second parse.
 */
describe('buildInstancedScene layer colours and own layers', () => {
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

  it('tells an inherited layer from one the instance carries itself', () => {
    const builder = create();
    const trees = builder.addLayer('Trees');
    const square = (z: number): [number, number, number][] => [[0, 0, z], [10, 0, z], [10, 10, z], [0, 10, z]];
    const box = builder.addComponentDefinition('Box', (def) => def.addFace(square(0)));
    const planter = builder.addComponentDefinition('Planter', (def) => {
      def.addFace(square(5));
      def.addInstance(box, { translation: [0, 0, 20] });
      def.addInstance(box, { translation: [0, 0, 40], layer: trees });
    });
    builder.addInstance(planter, { layer: trees });
    const bytes = builder.toBytes();

    const [placed] = buildInstancedScene(bytes.slice().buffer).sceneHierarchy.children;

    expect(placed.ownLayer).toBe('Trees');
    expect(placed.children.map((node) => [node.layer, node.ownLayer])).toEqual([
      ['Trees', undefined],
      ['Trees', 'Trees'],
    ]);
  });
});
