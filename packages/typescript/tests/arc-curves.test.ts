import { describe, it, expect } from 'vitest';
import * as fs from 'fs';
import * as path from 'path';
import { parseSkp } from '../src/index';

/**
 * `Definition.arcs`: the analytic arc of every curve that is one (openskp#462).
 *
 * Fixtures: arcs_2017.skp, arcs_2020.skp (classic container) and arcs_2021.skp
 * (VFF) - three arcs built through the installed SketchUp library:
 *
 * - a loose arc: centre (0, 0, 0), radius 10, 0 to 90 degrees, 12 segments;
 * - a disc: a full circle bounding a face, centre (50, 0, 0), radius 5, 24 sides;
 * - a half disc: an arc closed by its chord, bounding a face, centre
 *   (100, 0, 0), radius 8, 0 to 180 degrees, 12 segments.
 *
 * SketchUp reads the same centres, radii and angles back from all three
 * files. Untitled.skp is a real file whose 368 arcs (24-edge circles) agree
 * with SketchUp's own values to 5e-6 inches. No private content.
 */
const NAMES = ['arcs_2017.skp', 'arcs_2020.skp', 'arcs_2021.skp'];

function load(name: string): ArrayBuffer {
  const b = fs.readFileSync(path.join(__dirname, 'fixtures', name));
  return b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength) as ArrayBuffer;
}

// [centre x, radius, sweep in radians, edges, is a full circle]
const EXPECTED: [number, number, number, number, boolean][] = [
  [0, 10, Math.PI / 2, 12, false],
  [50, 5, 2 * Math.PI, 24, true],
  [100, 8, Math.PI, 12, false],
];

describe.each(NAMES)('%s', (name) => {
  const model = parseSkp(load(name));
  const arcs = [...model.root.arcs.entries()].sort((a, b) => a[1].center[0] - b[1].center[0]);

  it('reads every arc with its centre, radius and angles', () => {
    expect(arcs.length).toBe(3);
    arcs.forEach(([, arc], i) => {
      const [cx, radius, sweep, , full] = EXPECTED[i];
      expect(arc.center[0]).toBeCloseTo(cx, 9);
      expect(arc.center[1]).toBeCloseTo(0, 9);
      expect(arc.center[2]).toBeCloseTo(0, 9);
      expect(arc.normal[2]).toBeCloseTo(1, 9);
      expect(arc.radius).toBeCloseTo(radius, 9);
      expect(arc.startAngle).toBeCloseTo(0, 9);
      const got = (arc.endAngle - arc.startAngle) % (2 * Math.PI);
      expect(got).toBeCloseTo(sweep % (2 * Math.PI), 9);
      expect(arc.isFullCircle).toBe(full);
      expect(arc.xAxis[0]).toBeCloseTo(radius, 9);
      expect(arc.yAxis[1]).toBeCloseTo(radius, 9);
    });
  });

  it('joins each arc to its edges by curve id', () => {
    const perCurve = new Map<number | null | undefined, number>();
    for (const e of model.root.edges) perCurve.set(e.curveId, (perCurve.get(e.curveId) ?? 0) + 1);
    expect(arcs.map(([id]) => perCurve.get(id))).toEqual(EXPECTED.map((e) => e[3]));
  });
});

describe('a model without arcs', () => {
  it.each(['instance_scale_term_v6.skp', 'instance_scale_term_2021.skp'])('%s has none', (name) => {
    const model = parseSkp(load(name));
    expect(model.root.arcs.size).toBe(0);
    for (const d of model.definitions.values()) expect(d.arcs.size).toBe(0);
  });
});

describe('Untitled.skp', () => {
  it('has 368 arcs, each the 24-edge circle SketchUp reports', () => {
    const model = parseSkp(load('Untitled.skp'));
    let total = 0;
    for (const d of [...model.definitions.values(), model.root]) {
      const perCurve = new Map<number | null | undefined, number>();
      for (const e of d.edges) perCurve.set(e.curveId, (perCurve.get(e.curveId) ?? 0) + 1);
      for (const [id, arc] of d.arcs) {
        total++;
        expect(perCurve.get(id)).toBe(24);
        expect(arc.isFullCircle).toBe(true);
      }
    }
    expect(total).toBe(368);
  });
});
