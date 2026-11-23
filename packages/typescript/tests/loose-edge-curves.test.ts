import { describe, it, expect } from 'vitest';
import * as fs from 'fs';
import * as path from 'path';
import {
  parseSkp,
  buildScene,
  buildInstancedScene,
  looseEdgeRuns,
  Definition,
} from '../src/index';
import { GeometryBuilder, ArcFrame } from '../src/geometry';
import { chainLooseEdges, solveRunArc, INCHES_TO_M } from '../src/curves';

const FIXTURES = path.join(__dirname, 'fixtures');
const RADIUS_IN = 40.0;
const IDENTITY = [1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1];

function readFixtureBuffer(name: string): ArrayBuffer {
  const raw = fs.readFileSync(path.join(FIXTURES, name));
  return raw.buffer.slice(raw.byteOffset, raw.byteOffset + raw.byteLength) as ArrayBuffer;
}

function makeFrame(radius = RADIUS_IN, sweep = Math.PI / 2): ArcFrame {
  return {
    center: [0, 0, 0],
    normal: [0, 0, 1],
    xAxis: [radius, 0, 0],
    yAxis: [0, radius, 0],
    startAngle: 0,
    endAngle: sweep,
  };
}

function arcPoint(t: number, radius = RADIUS_IN): [number, number, number] {
  return [radius * Math.cos(t) * INCHES_TO_M, 0, -radius * Math.sin(t) * INCHES_TO_M];
}

describe('Loose-edge curve chaining and analytic arcs (#285)', () => {
  it('keeps two touching curves separate and ungrouped edges as pairs', () => {
    const b1 = new GeometryBuilder();
    b1.edges.set(1, [10, 20]);
    b1.edges.set(2, [20, 30]);
    b1.edgeCurves.set(1, 100);
    b1.edgeCurves.set(2, 101);
    expect(chainLooseEdges(b1).map((r) => r.vertexIds.length).sort()).toEqual([2, 2]);

    const b2 = new GeometryBuilder();
    b2.edges.set(1, [10, 20]);
    b2.edges.set(2, [20, 30]);
    expect(chainLooseEdges(b2)).toEqual([
      { edgeIds: [1], vertexIds: [10, 20], closed: false },
      { edgeIds: [2], vertexIds: [20, 30], closed: false },
    ]);
  });

  it('excludes face-bounded edges and orders open and closed curves', () => {
    const openB = new GeometryBuilder();
    openB.edges.set(1, [10, 20]);
    openB.edges.set(2, [20, 30]);
    openB.edges.set(3, [30, 40]);
    openB.edgeCurves.set(2, 50);
    openB.edgeCurves.set(3, 50);
    openB.faces.set(9, {
      loops: [[{ edgeId: 1, orientation: 1 }]],
      normal: [0, 0, 1],
      materialId: null,
      backMaterialId: null,
      uvTransform: null,
      uvTransformBack: null,
      uvProjected: false,
      uvProjectedBack: false,
      hidden: false,
      layerId: null,
    });
    expect(chainLooseEdges(openB)).toEqual([
      { edgeIds: [2, 3], vertexIds: [20, 30, 40], closed: false },
    ]);

    const closedB = new GeometryBuilder();
    closedB.edges.set(1, [10, 20]);
    closedB.edges.set(2, [20, 30]);
    closedB.edges.set(3, [30, 10]);
    closedB.edgeCurves.set(1, 77);
    closedB.edgeCurves.set(2, 77);
    closedB.edgeCurves.set(3, 77);
    const runs = chainLooseEdges(closedB);
    expect(runs).toHaveLength(1);
    expect(runs[0].closed).toBe(true);
    expect(runs[0].vertexIds).toHaveLength(3);
  });

  it('recovers circular arcs and rejects split, elliptical, or off-circle runs', () => {
    const b = new GeometryBuilder();
    b.edgeCurves.set(1, 17);
    b.arcCurves.set(17, makeFrame());
    const pts = [0, Math.PI / 8, Math.PI / 4, (3 * Math.PI) / 8, Math.PI / 2].map((t) =>
      arcPoint(t)
    );
    const arc = solveRunArc(b, [1], IDENTITY, pts, false);
    expect(arc).not.toBeNull();
    expect(arc!.segments).toEqual([[0, 1, 2]]);
    expect(arc!.points).toHaveLength(3);

    // 4-sided closed circle -> two half-turns
    const b4 = new GeometryBuilder();
    for (let i = 1; i <= 4; i++) b4.edgeCurves.set(i, 17);
    b4.arcCurves.set(17, makeFrame(RADIUS_IN, 2 * Math.PI));
    const pts4 = [0, 1, 2, 3].map((i) => arcPoint((2 * Math.PI * i) / 4));
    const arc4 = solveRunArc(b4, [1, 2, 3, 4], IDENTITY, pts4, true);
    expect(arc4).not.toBeNull();
    expect(arc4!.segments).toEqual([
      [0, 1, 2],
      [2, 3, 0],
    ]);

    // Split curve rejected
    b.edgeCurves.set(2, 17);
    expect(solveRunArc(b, [1], IDENTITY, pts.slice(0, 2), false)).toBeNull();

    // Elliptical frame rejected
    const bEllipse = new GeometryBuilder();
    bEllipse.edgeCurves.set(1, 17);
    const ef = makeFrame();
    ef.yAxis = [0, RADIUS_IN / 2, 0];
    bEllipse.arcCurves.set(17, ef);
    expect(solveRunArc(bEllipse, [1], IDENTITY, pts.slice(0, 2), false)).toBeNull();

    // Off-circle vertex rejected
    const bOff = new GeometryBuilder();
    bOff.edgeCurves.set(1, 17);
    bOff.arcCurves.set(17, makeFrame());
    const badPts: [number, number, number][] = [pts[0], [pts[1][0], pts[1][1] + 0.01, pts[1][2]]];
    expect(solveRunArc(bOff, [1], IDENTITY, badPts, false)).toBeNull();
  });

  it('bakes loose-edge curveSets on capilla_quiroz_v17.skp and gondola_v20.skp', () => {
    const capillaBuf = readFixtureBuffer('capilla_quiroz_v17.skp');
    const sc = buildScene(capillaBuf);
    expect(sc.curveSets).toHaveLength(20);
    expect(sc.curveSets.every((cs) => cs.pointsM.length === 2 && !cs.closed && cs.arc === null)).toBe(
      true
    );

    const rootRuns = sc.curveSets.filter((cs) => cs.path === 'ROOT');
    expect(rootRuns).toHaveLength(5);
    expect(rootRuns.map((cs) => cs.name).sort()).toEqual([
      'Layer0_1',
      'Layer0_2',
      'Layer0_3',
      'Layer0_4',
      'Layer0_5',
    ]);
    expect(rootRuns.every((cs) => cs.nameIsGenerated)).toBe(true);

    const innerRuns = sc.curveSets.filter((cs) => cs.path === 'ROOT / puerta');
    expect(innerRuns).toHaveLength(15);
    expect(new Set(innerRuns.map((cs) => cs.name))).toEqual(new Set(['puerta']));
    expect(innerRuns.some((cs) => cs.nameIsGenerated)).toBe(false);

    const scOff = buildScene(capillaBuf, { includeCurveSets: false });
    expect(scOff.curveSets).toEqual([]);
    expect(scOff.glbPrimitives).toHaveLength(sc.glbPrimitives.length);

    const gondolaSc = buildScene(readFixtureBuffer('gondola_v20.skp'));
    expect(new Set(gondolaSc.curveSets.map((cs) => cs.layer))).toEqual(
      new Set(['Gondulas Laterais', 'Layer0'])
    );
    expect(gondolaSc.curveSets.filter((cs) => cs.layer === 'Gondulas Laterais')).toHaveLength(
      57232
    );
  });

  it.each([
    'SU_File.skp',
    'Untitled.skp',
    'capilla_quiroz_v17.skp',
    'gondola_v20.skp',
    'single_material_v17.skp',
  ])('looseEdgeRuns matches buildInstancedScene().curveResources on %s', (fixtureName) => {
    const buf = readFixtureBuffer(fixtureName);
    const model = parseSkp(buf);
    const instScene = buildInstancedScene(buf);

    const defsById = new Map<number | string, Definition>(model.definitions);
    defsById.set('ROOT', model.root);

    let checkedAny = false;
    for (const res of instScene.curveResources) {
      const def = defsById.get(res.definitionId);
      expect(def).toBeDefined();
      const runs = looseEdgeRuns(def!);
      expect(runs).toHaveLength(res.curves.length);
      const vertsById = new Map(def!.vertices.map((v) => [v.id, v]));
      for (let i = 0; i < runs.length; i++) {
        expect(runs[i].closed).toBe(res.curves[i].closed);
        expect(runs[i].vertexIds).toHaveLength(res.curves[i].pointsM.length);
        for (let k = 0; k < runs[i].vertexIds.length; k++) {
          const v = vertsById.get(runs[i].vertexIds[k])!;
          const got = [
            Number((v.x * INCHES_TO_M).toFixed(6)),
            Number((v.z * INCHES_TO_M).toFixed(6)),
            Number((-v.y * INCHES_TO_M).toFixed(6)),
          ];
          expect(Math.abs(got[0] - res.curves[i].pointsM[k][0])).toBeLessThan(1e-5);
          expect(Math.abs(got[1] - res.curves[i].pointsM[k][1])).toBeLessThan(1e-5);
          expect(Math.abs(got[2] - res.curves[i].pointsM[k][2])).toBeLessThan(1e-5);
        }
      }
      checkedAny = true;
    }

    if (!checkedAny) {
      for (const def of [...model.definitions.values(), model.root]) {
        expect(looseEdgeRuns(def)).toEqual([]);
      }
    }
  });
});
