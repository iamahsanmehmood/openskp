import * as fs from 'fs';
import * as path from 'path';
import { describe, it, expect } from 'vitest';
import { create, type ComponentDefinitionBuilder } from '../src/create';
import { buildInstancedScene, fromFragments, toFragments, type InstancedNode } from '../src/index';

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

    const scene = buildInstancedScene(bytes.slice().buffer);
    const root = scene.sceneHierarchy;

    expect(root.hidden).toBe(false);
    expect(root.children.map((node) => [node.name, node.hidden])).toEqual([
      ['Visible', false],
      ['Hidden', true],
      ['OuterHidden', true],
    ]);
    expect(root.children[2].children.map((node) => [node.name, node.hidden])).toEqual([
      ['NestedVisible', false],
      ['NestedHidden', true],
    ]);

    const rebuilt = fromFragments(toFragments(scene, { raw: true }));
    const assertUnset = (node: InstancedNode) => {
      expect(node.hidden).toBeUndefined();
      for (const child of node.children) assertUnset(child);
    };
    assertUnset(rebuilt.sceneHierarchy);
  });

  it('reports component, group and nested Hide flags on SDK classic and 2021+ fixtures', () => {
    const expectedClassic = [
      ['VisibleComp', false],
      ['HiddenComp', true],
      ['VisibleGroup', false],
      ['HiddenGroup', true],
      ['VisibleOuter', false],
    ];
    const expectedVff = [
      ['VisibleComp', false],
      ['HiddenComp', true],
      ['VisibleOuter', false],
      ['VisibleGroup', false],
      ['HiddenGroup', true],
    ];
    const expectedNested = [
      ['NestedVisible', false],
      ['NestedHidden', true],
      ['NestedHiddenGroup', true],
    ];

    for (const [fixture, expectedTop] of [
      ['instanced_hidden_classic.skp', expectedClassic],
      ['instanced_hidden_vff.skp', expectedVff],
    ] as const) {
      const buf = fs.readFileSync(path.join(__dirname, 'fixtures', fixture));
      const root = buildInstancedScene(buf).sceneHierarchy;
      expect(root.hidden).toBe(false);
      expect(root.children.map((n) => [n.name, n.hidden])).toEqual(expectedTop);
      const outer = root.children.find((n) => n.name === 'VisibleOuter')!;
      expect(outer.children.map((n) => [n.name, n.hidden])).toEqual(expectedNested);
    }
  });
});
