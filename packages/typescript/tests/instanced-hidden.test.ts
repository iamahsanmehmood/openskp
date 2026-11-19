import { describe, it, expect } from 'vitest';
import { create, type ComponentDefinitionBuilder } from '../src/create';
import { buildInstancedScene } from '../src/index';

/**
 * InstancedNode.hidden - the instance's own "Hide" flag, which the parsed
 * instance already carries (and the baked path's Instance.hidden reports),
 * so a consumer of the instanced scene can skip hidden placements.
 */
describe('buildInstancedScene instance hidden flag', () => {
  it('reports each placement\'s own Hide flag, nested ones included', () => {
    const builder = create();
    const square = (def: ComponentDefinitionBuilder) => {
      def.addFace([[0, 0, 0], [10, 0, 0], [10, 10, 0], [0, 10, 0]]);
    };
    const box = builder.addComponentDefinition('Box', square);
    const inner = builder.addComponentDefinition('Inner', square);
    const outer = builder.addComponentDefinition('Outer', (def) => {
      def.addInstance(inner, { name: 'NestedVisible' });
      def.addInstance(inner, { name: 'NestedHidden', hidden: true, translation: [20, 0, 0] });
    });
    builder.addInstance(box, { name: 'Visible' });
    builder.addInstance(box, { name: 'Hidden', hidden: true, translation: [20, 0, 0] });
    builder.addInstance(outer, { name: 'OuterHidden', hidden: true, translation: [40, 0, 0] });
    const bytes = builder.toBytes();

    const root = buildInstancedScene(bytes.slice().buffer).sceneHierarchy;

    expect(root.children.map((node) => [node.name, node.hidden])).toEqual([
      ['Visible', false],
      ['Hidden', true],
      ['OuterHidden', true],
    ]);
    expect(root.children[2].children.map((node) => [node.name, node.hidden])).toEqual([
      ['NestedVisible', false],
      ['NestedHidden', true],
    ]);
  });
});
