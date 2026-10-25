import { describe, it, expect } from 'vitest';
import * as fs from 'fs';
import * as path from 'path';
import { parseSkp, buildScene, buildInstancedScene } from '../src/index';
import { foldScale, multiplyMatrices, transformPoint } from '../src/transforms';

/**
 * An instance scaled through the matrix's scale term (openskp#444).
 *
 * SketchUp can keep a uniform scale in the homogeneous term of an instance's
 * transformation instead of in the 3x3: the 3x3 stays as it is, the
 * translation is in the scaled units, and the 16th value `w` is the divisor.
 * SketchUp's own library reports a 100 in cube placed with `w = 100` and a
 * translation of 2000 in as a 1 in cube at x = 20 in. The 13-value matrix
 * stores `1/w` at index 12, and every consumer used to ignore it, so such an
 * instance came out 100x its size and 100x its distance from its parent.
 *
 * Fixtures: instance_scale_term_2021.skp and instance_scale_term_v6.skp - one
 * 100 in cube definition placed twice, once through the 3x3 (scaling 0.01) and
 * once through w = 100 with a translation of 2000 in, built and saved through
 * the installed SketchUp library. No private content.
 */
const FIXTURES = path.join(__dirname, 'fixtures');
const FILES = ['instance_scale_term_2021.skp', 'instance_scale_term_v6.skp'];
const INCH_MM = 25.4;

function load(name: string): ArrayBuffer {
  const b = fs.readFileSync(path.join(FIXTURES, name));
  return b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength) as ArrayBuffer;
}

describe('matrix arithmetic with the scale term', () => {
  it('scales the translated point', () => {
    const m = [1, 0, 0, 0, 1, 0, 0, 0, 1, 2000, 0, 0, 0.01];
    const p = transformPoint(m, [100, 100, 100]);
    expect(p[0]).toBeCloseTo(21, 9);
    expect(p[1]).toBeCloseTo(1, 9);
    expect(p[2]).toBeCloseTo(1, 9);
  });

  it('a scale of one changes nothing', () => {
    const m = [2, 0, 0, 0, 3, 0, 0, 0, 4, 5, 6, 7, 1];
    expect(transformPoint(m, [1, 1, 1])).toEqual([7, 9, 11]);
    expect(foldScale(m)).toBe(m);
  });

  it('scaling through the 3x3 and through the term agree', () => {
    const via3x3 = [0.01, 0, 0, 0, 0.01, 0, 0, 0, 0.01, 0, 0, 0, 1];
    const viaTerm = [1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 0.01];
    const a = transformPoint(via3x3, [50, 20, 10]);
    const b = transformPoint(viaTerm, [50, 20, 10]);
    a.forEach((v, i) => expect(v).toBeCloseTo(b[i], 9));
  });

  it('never folds a 16-value column-major matrix', () => {
    const m16 = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 5, 6, 7, 1];
    expect(foldScale(m16)).toBe(m16);
  });

  it('composition matches applying one transform after the other', () => {
    let seed = 12345;
    const rnd = (lo: number, hi: number) => {
      seed = (seed * 1103515245 + 12345) & 0x7fffffff;
      return lo + (seed / 0x7fffffff) * (hi - lo);
    };
    const scales = [1, 1, 0.01, 0.5, 2, 0.1];
    const randomMatrix = () => [
      ...Array.from({ length: 9 }, () => rnd(-2, 2)),
      ...Array.from({ length: 3 }, () => rnd(-50, 50)),
      scales[Math.floor(rnd(0, scales.length)) % scales.length],
    ];
    for (let k = 0; k < 40; k++) {
      const parent = randomMatrix();
      const child = randomMatrix();
      const p: [number, number, number] = [rnd(-100, 100), rnd(-100, 100), rnd(-100, 100)];
      const composed = multiplyMatrices(parent, child);
      const want = transformPoint(parent, transformPoint(child, p));
      const got = transformPoint(composed, p);
      for (let i = 0; i < 3; i++) expect(got[i]).toBeCloseTo(want[i], 6);
    }
  });

  it('a composed matrix has its scale folded in', () => {
    const out = multiplyMatrices(
      [1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 0.01],
      [1, 0, 0, 0, 1, 0, 0, 0, 1, 2000, 0, 0, 1],
    );
    expect(out[12]).toBe(1);
    expect(out[9]).toBeCloseTo(20, 9);
  });
});

describe.each(FILES)('%s', (name) => {
  it('the parse still reports the raw matrix', () => {
    const model = parseSkp(load(name));
    expect(
      model.root.instances.some(
        (i: any) => Math.abs(i.matrix[12] - 0.01) < 1e-12 && Math.abs(i.matrix[9] - 2000) < 1e-9,
      ),
    ).toBe(true);
  });

  it('the scene places both cubes where SketchUp does', () => {
    const scene = buildScene(load(name));
    const pos = scene.sceneHierarchy.children.map((n: any) => n.positionMm as number[]).sort((a, b) => a[0] - b[0]);
    expect(pos[0][0]).toBeCloseTo(0, 6);
    expect(pos[1][0]).toBeCloseTo(20 * INCH_MM, 6); // 508 mm, not 50,800
  });

  it('the instanced scene node matrices carry the scale and the translation', () => {
    const scene = buildInstancedScene(load(name));
    const placed = scene.sceneHierarchy.children;
    expect(placed.length).toBe(2);
    for (const n of placed) {
      expect(n.matrix[0]).toBeCloseTo(0.01, 9);
      expect(n.matrix[5]).toBeCloseTo(0.01, 9);
      expect(n.matrix[10]).toBeCloseTo(0.01, 9);
    }
    const tx = placed.map((n: any) => n.matrix[12] as number).sort((a: number, b: number) => a - b);
    expect(tx[0]).toBeCloseTo(0, 9);
    expect(tx[1]).toBeCloseTo((20 * INCH_MM) / 1000, 9); // 0.508 m, not 50.8
  });
});
