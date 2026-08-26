import { describe, it, expect } from 'vitest';
import * as path from 'path';
import { SkpFile } from '../src/index';
import { Definition } from '../src/model';

/**
 * Regression coverage for the CoEdge orientation contract (+1 = same
 * direction as the edge's own v1->v2, -1 = reversed). Both the legacy
 * (pre-2021 MFC) and modern (VFF) readers used to leak SketchUp's raw
 * storage bit (0 = forward, 1 = reversed) straight through instead of
 * normalizing it - counting coedges or checking their type does not catch
 * this, since a face with every coedge reversed still has the same edge
 * count and loop length, just the wrong winding. The only thing that
 * catches it is checking that consecutive coedges in a loop actually
 * connect head-to-tail.
 */
function assertConnectedNormalizedLoops(definitions: Definition[]): void {
  for (const def of definitions) {
    const edgesById = new Map(def.edges.map((e) => [e.id, e]));
    for (const face of def.faces) {
      for (const loop of face.loops) {
        expect(loop.length).toBeGreaterThan(0);
        const n = loop.length;
        for (let i = 0; i < n; i++) {
          const { edgeId, orientation } = loop[i];
          const { edgeId: nextEdgeId, orientation: nextOrientation } = loop[(i + 1) % n];
          expect([1, -1]).toContain(orientation);
          const edge = edgesById.get(edgeId);
          const nextEdge = edgesById.get(nextEdgeId);
          expect(edge).toBeDefined();
          expect(nextEdge).toBeDefined();
          const end = orientation === 1 ? edge!.v2Id : edge!.v1Id;
          const nextStart = nextOrientation === 1 ? nextEdge!.v1Id : nextEdge!.v2Id;
          expect(end).toBe(nextStart);
        }
      }
    }
  }
}

describe('CoEdge orientation is normalized and connected', () => {
  it('legacy MFC reader (capilla_quiroz_v17.skp)', () => {
    const filePath = path.join(__dirname, 'fixtures', 'capilla_quiroz_v17.skp');
    const model = SkpFile.open(filePath).parse();
    assertConnectedNormalizedLoops([...model.definitions.values(), model.root]);
  });

  it('modern VFF reader (Untitled.skp)', () => {
    const filePath = path.join(__dirname, 'fixtures', 'Untitled.skp');
    const model = SkpFile.open(filePath).parse();
    assertConnectedNormalizedLoops([...model.definitions.values(), model.root]);
  });
});
