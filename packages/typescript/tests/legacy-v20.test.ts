import { describe, it, expect } from 'vitest';
import * as fs from 'fs';
import * as path from 'path';
import { parseSkp, buildScene } from '../src/index';
import { isLegacy } from '../src/legacy';

/**
 * Real-file regression test for SketchUp 2020 (v20) classic .skp files.
 *
 * Fixture: fixtures/gondola_v20.skp - a retail gondola display authored in
 * SketchUp 2020 (v20.1.235, ~755 KB), contributed for this fix.
 *
 * Before the v20 layout fixes, this file threw
 * `implausible definition count` from walk(): v20 writes records the v17
 * layout does not have, which left the reader a few bytes short and made it
 * read garbage where a count was expected. The existing v17 fixture
 * (capilla_quiroz_v17.skp) has only one layer and never exercised any of
 * these paths, so the divergence went unnoticed.
 *
 * Every count below was read off this exact file after the fix and
 * sanity-checked for plausibility (bounding box in metres, definitions
 * carrying real geometry, instances actually placed in the scene) - a parse
 * that "succeeds" while silently dropping placements would still be a bug,
 * so the instance counts matter as much as the parse not throwing.
 */
describe('Legacy MFC reader - SketchUp 2020 (v20) layout', () => {
  const filePath = path.join(__dirname, 'fixtures', 'gondola_v20.skp');
  const buf = fs.readFileSync(filePath);
  const data = new Uint8Array(buf.buffer, buf.byteOffset, buf.byteLength);
  const arrayBuffer = buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength) as ArrayBuffer;

  it('is detected as a legacy container', () => {
    expect(isLegacy(data)).toBe(true);
  });

  it('parses a real v20 file that previously threw', () => {
    const model = parseSkp(arrayBuffer);

    expect(model.version).toBe('{20.1.235}');
    // legacy files carry no meta/meta.dat, same as v17
    expect(model.units).toBeNull();

    expect(model.definitions.size).toBe(20);
    expect(model.materials.length).toBe(24);

    // v20 interleaves a null object-ref after EACH layer record; the count
    // is the number of REAL layers. The old reader counted the separators
    // as items and dropped every layer after the first - this fixture
    // really does carry "Gondulas Laterais" (visible in SketchUp), which
    // the previous assertion enshrined as missing. Nulls must still never
    // reach model.layers.
    expect(model.layers.map((l) => l.name)).toEqual(['Layer0', 'Gondulas Laterais']);
    for (const layer of model.layers) {
      expect(layer).not.toBeNull();
      expect(layer.name).toBeTypeOf('string');
    }

    // real geometry, not an empty shell
    let faces = 0;
    let edges = 0;
    let vertices = 0;
    for (const d of model.definitions.values()) {
      faces += d.faces.length;
      edges += d.edges.length;
      vertices += d.vertices.length;
    }
    expect(faces).toBe(1887);
    expect(edges).toBe(9174);
    expect(vertices).toBe(6543);
  });

  it(
    'places every root instance (a parse that drops them is still broken)',
    () => {
      // Both parseSkp() and buildScene() do their own full pass over this
      // legacy v20 fixture - genuinely takes longer than vitest's 5s
      // default on a loaded CI runner (same buildScene() cost flagged on
      // "gives every baked primitive valid uv coordinates" below), not a
      // regression, just more headroom than the default budget.
      const model = parseSkp(arrayBuffer);
      // 23 root-level placements: the definitions above are useless if the
      // instances that position them in the model are lost, which is exactly
      // what a subtly misaligned walk produces - a file that parses into an
      // almost-empty scene instead of throwing.
      expect(model.root.instances.length).toBe(23);

      const scene = buildScene(arrayBuffer);
      expect(scene.sceneHierarchy.children.length).toBe(23);
      expect(scene.glbPrimitives.length).toBe(201);
      expect(Object.keys(scene.meshIndex).length).toBe(201);
      expect(scene.gltfMaterials.length).toBe(17);
    },
    20000
  );

  it('resolves placed instances to definitions that carry geometry', () => {
    // Guards the failure mode that a zero entity count produces: the
    // definitions an instance points at come back empty, so the file parses
    // into a scene of correctly-positioned but invisible groups. Counting
    // definitions or instances alone does not catch it - the two have to be
    // checked together.
    const model = parseSkp(arrayBuffer);
    const referenced = new Set<number>();
    for (const inst of model.root.instances) referenced.add(inst.refIdx);
    for (const def of model.definitions.values()) {
      for (const inst of def.instances) referenced.add(inst.refIdx);
    }
    const memo = new Map<number, boolean>();
    const inProgress = new Set<number>();
    const carriesGeometry = (id: number): boolean => {
      const cached = memo.get(id);
      if (cached !== undefined) return cached;
      if (inProgress.has(id)) return false; // reference cycle
      inProgress.add(id);
      const def = model.definitions.get(id);
      // a group whose own geometry lives in nested children still counts
      const result =
        def !== undefined &&
        (def.faces.length > 0 || def.instances.some((child) => carriesGeometry(child.refIdx)));
      inProgress.delete(id);
      memo.set(id, result);
      return result;
    };
    const empty = [...referenced].filter((id) => !carriesGeometry(id));
    expect(empty).toEqual([]);
  });

  it(
    'bakes geometry at a plausible real-world scale',
    () => {
      // Another fresh buildScene() on the same heavy legacy v20 fixture -
      // see the timeout comment on "gives every baked primitive valid uv
      // coordinates" below.
      const scene = buildScene(arrayBuffer);
      let min = [Infinity, Infinity, Infinity];
      let max = [-Infinity, -Infinity, -Infinity];
      for (const prim of scene.glbPrimitives) {
        for (let i = 0; i < prim.positions.length; i += 3) {
          for (let a = 0; a < 3; a++) {
            const v = prim.positions[i + a];
            if (v < min[a]) min[a] = v;
            if (v > max[a]) max[a] = v;
          }
        }
      }
      // a shop gondola display: metres, not the 1e3-off or degenerate box a
      // misaligned read produces
      expect(max[0] - min[0]).toBeCloseTo(3.82, 1);
      expect(max[1] - min[1]).toBeCloseTo(3.14, 1);
      expect(max[2] - min[2]).toBeCloseTo(4.82, 1);
    },
    20000
  );

  it(
    'gives every baked primitive valid uv coordinates',
    () => {
      // A fresh full buildScene() on this legacy v20 fixture, then a
      // per-value assertion over every UV of every primitive - the
      // heaviest single call in this file. Genuinely takes longer than
      // vitest's 5s default on a loaded CI runner (observed timing out
      // there), not a regression, just more headroom than the default
      // budget. Same situation as edge-flags.test.ts's
      // randomised-access-pattern test.
      const scene = buildScene(arrayBuffer);
      expect(scene.glbPrimitives.length).toBeGreaterThan(0);
      for (const prim of scene.glbPrimitives) {
        const nVerts = prim.positions.length / 3;
        expect(prim.uvs.length).toBe(nVerts * 2);
        for (const uv of prim.uvs) {
          expect(Number.isFinite(uv)).toBe(true);
        }
      }
    },
    20000
  );
});
