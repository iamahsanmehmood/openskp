import { describe, it, expect } from 'vitest';
import * as fs from 'fs';
import * as path from 'path';
import { parseSkp } from '../src/index';

/**
 * `Definition.isGroup`: whether SketchUp made a definition for a group or a
 * component (openskp#446).
 *
 * Fixtures: nesting_<version>.skp - the group "Outer" holds an instance of the
 * component "Shelf", which holds the group "Inner", a face at each level. Built
 * and saved through the installed SketchUp library as SketchUp 6, 2013, 2017,
 * 2020 and 2021 (the last is the VFF container, the rest the classic one). The
 * library reports two group definitions and one component definition for each
 * (SUModelGetNumGroupDefinitions / SUModelGetNumComponentDefinitions). No
 * private content.
 */
const NAMES = ['nesting_v6.skp', 'nesting_2013.skp', 'nesting_2017.skp', 'nesting_2020.skp', 'nesting_2021.skp'];

function load(name: string): ArrayBuffer {
  const b = fs.readFileSync(path.join(__dirname, 'fixtures', name));
  return b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength) as ArrayBuffer;
}

describe.each(NAMES)('%s', (name) => {
  const model = parseSkp(load(name));
  const definition = (instance: { refIdx: number }) => model.definitions.get(instance.refIdx)!;

  it('tells the groups from the component', () => {
    const all = [...model.definitions.values()];
    expect(all.filter((d) => d.isGroup).length).toBe(2);
    expect(all.filter((d) => !d.isGroup).length).toBe(1);
  });

  it('follows the nesting', () => {
    expect(model.root.instances.length).toBe(1);
    const outer = model.root.instances[0];
    expect(outer.name).toBe('Outer');
    expect(definition(outer).isGroup).toBe(true);

    expect(definition(outer).instances.length).toBe(1);
    const shelf = definition(outer).instances[0];
    expect(definition(shelf).name).toBe('Shelf');
    expect(definition(shelf).isGroup).toBe(false);

    expect(definition(shelf).instances.length).toBe(1);
    const inner = definition(shelf).instances[0];
    expect(inner.name).toBe('Inner');
    expect(definition(inner).isGroup).toBe(true);
  });

  it('does not take a definition placed once for a group', () => {
    // "Shelf" is placed exactly once, which is what the old guess got wrong
    const placements = new Map<number, number>();
    for (const d of [...model.definitions.values(), model.root]) {
      for (const i of d.instances) placements.set(i.refIdx, (placements.get(i.refIdx) ?? 0) + 1);
    }
    const shelf = [...model.definitions.entries()].find(([, d]) => d.name === 'Shelf')!;
    expect(placements.get(shelf[0])).toBe(1);
    expect(shelf[1].isGroup).toBe(false);
  });
});
