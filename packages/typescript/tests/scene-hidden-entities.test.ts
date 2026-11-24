import * as path from 'path';
import { describe, expect, it } from 'vitest';
import { Definition, Page, SkpFile, SkpModel } from '../src';

const CPP_FIXTURES = path.resolve(__dirname, '../../cpp/tests/fixtures');

const FILES = [
  'scene_hidden_entities_2017.skp',
  'scene_hidden_entities_2019.skp',
  'scene_hidden_entities_2020.skp',
  'scene_hidden_entities_2024.skp',
] as const;

function hiddenSet(page: Page): Set<number> {
  return new Set(page.hiddenEntityIds);
}

function placementIds(model: SkpModel): Map<string, number> {
  const ids = new Map<string, number>();
  const add = (defn: Definition) => {
    for (const inst of defn.instances) {
      if (inst.id != null) {
        ids.set(inst.name, inst.id);
      }
    }
  };
  add(model.root);
  for (const defn of model.definitions.values()) {
    add(defn);
  }
  return ids;
}

describe('Scene hidden entities (#472)', () => {
  it.each(FILES)('lists what each scene hides (%s)', (name) => {
    const model = SkpFile.open(path.join(CPP_FIXTURES, name)).parse();
    expect(model.pages.length).toBe(6);
    expect(model.pages[0].name).toBe('All');
    expect(model.pages[0].hiddenEntityIds).toEqual([]);
    expect(model.pages[1].name).toBe('No B, no crate');
    expect(hiddenSet(model.pages[1])).toEqual(new Set([71, 73]));
    expect(model.pages[2].name).toBe('No A, no crate');
    expect(hiddenSet(model.pages[2])).toEqual(new Set([70, 73]));
    expect(model.pages[4].name).toBe('Edge');
    expect(hiddenSet(model.pages[4])).toEqual(new Set([17]));
    expect(model.pages[5].name).toBe('Not saved');
    expect(model.pages[5].hiddenEntityIds).toEqual([]);
  });

  it.each(FILES)('resolves hidden IDs through placements (%s)', (name) => {
    const model = SkpFile.open(path.join(CPP_FIXTURES, name)).parse();
    expect(model.pages.length).toBe(6);
    const ids = placementIds(model);
    expect(ids.has('Part A')).toBe(true);
    expect(ids.has('Part B')).toBe(true);
    expect(ids.has('Crate')).toBe(true);
    expect(hiddenSet(model.pages[1])).toEqual(
      new Set([ids.get('Part B')!, ids.get('Crate')!]),
    );
    expect(hiddenSet(model.pages[2])).toEqual(
      new Set([ids.get('Part A')!, ids.get('Crate')!]),
    );
  });

  it.each(FILES)('matches the hidden edge persistentId on the box (%s)', (name) => {
    const model = SkpFile.open(path.join(CPP_FIXTURES, name)).parse();
    const { edges, faces } = model.root;
    expect(edges.some((e) => e.persistentId === 17)).toBe(true);
    expect(faces.some((f) => f.persistentId > 0)).toBe(true);
  });

  it.each(['scene_hidden_entities_2020.skp', 'scene_hidden_entities_2024.skp'])(
    'keeps the nested placement in 2020+ (%s)',
    (name) => {
      const model = SkpFile.open(path.join(CPP_FIXTURES, name)).parse();
      expect(model.pages.length).toBe(6);
      expect(model.pages[3].name).toBe('No C');
      expect(hiddenSet(model.pages[3])).toEqual(new Set([100]));
      expect(placementIds(model).get('Part C')).toBe(100);
    },
  );

  it('keeps no nested placement in 2017 like SketchUp', () => {
    const model = SkpFile.open(
      path.join(CPP_FIXTURES, 'scene_hidden_entities_2017.skp'),
    ).parse();
    expect(model.pages.length).toBe(6);
    expect(model.pages[3].hiddenEntityIds).toEqual([]);
  });

  it('lists the 12 hidden edges in gondola_v20.skp', () => {
    const model = SkpFile.open(path.join(CPP_FIXTURES, 'gondola_v20.skp')).parse();
    expect(model.pages.length).toBe(1);
    expect(model.pages[0].hiddenEntityIds.length).toBe(12);
  });
});
