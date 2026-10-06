import { describe, it, expect } from 'vitest';
import * as fs from 'fs';
import * as path from 'path';
import { isLegacy, walk } from '../src/legacy';

/**
 * The legacy (pre-2021 MFC) walk builds each CComponentDefinition's
 * geometry as the definition record completes, and releases that
 * definition's entity objects right away.
 *
 * Before, every face, loop, edge-use, edge and vertex of the model stayed
 * alive until the walk ended, and only then was each definition built: a
 * real 411 MB SketchUp 2020 file held 11.4 million slots (~3.5 GB of live
 * heap) before the first builder existed, and could not be parsed with a
 * 4 GB heap. Building per definition keeps only the largest definition's
 * entities alive at once; the same file parses with 2.5 GB, with a
 * byte-identical result.
 */

const FIXTURES = path.join(__dirname, 'fixtures');
const RELEASED = new Set(['CVertex', 'CEdge', 'CEdgeUse', 'CLoop', 'CFace']);

const legacyFixtures = fs
  .readdirSync(FIXTURES)
  .filter((f) => f.endsWith('.skp'))
  .filter((f) => isLegacy(new Uint8Array(fs.readFileSync(path.join(FIXTURES, f)))));

function load(name: string): Uint8Array {
  return new Uint8Array(fs.readFileSync(path.join(FIXTURES, name)));
}

describe('legacy walk - definitions are built and released as they complete', () => {
  it('covers legacy fixtures that have definitions to release', () => {
    const withDefs = legacyFixtures.filter((f) =>
      [...walk(load(f)).ar.slots.values()].some((e) => e[0] === 'obj' && e[1] === 'CComponentDefinition')
    );
    expect(withDefs.length).toBeGreaterThan(0);
  });

  for (const name of legacyFixtures) {
    describe(name, () => {
      it('holds no entity object of a built definition after the walk', () => {
        const { ar } = walk(load(name));
        for (const [first, end] of ar.releasedSpans) {
          for (let slot = first; slot < end; slot++) {
            const ent = ar.slots.get(slot);
            if (ent === undefined || ent[0] !== 'obj' || ent[1] === null || !RELEASED.has(ent[1])) continue;
            expect(ent[2], `slot ${slot} (${ent[1]}) still alive`).toBeNull();
          }
        }
      });

      it('has a built builder for every definition, and empties its entity list', () => {
        const { ar } = walk(load(name));
        for (const [slot, ent] of ar.slots.entries()) {
          if (ent[0] !== 'obj' || ent[1] !== 'CComponentDefinition' || !ent[2]) continue;
          expect(ar.prebuilt.has(slot)).toBe(true);
          expect(ent[2].ents).toEqual([]);
        }
      });
    });
  }
});
