import { GeometryBuilder } from './geometry';
import { transformPoint } from './transforms';

export const INCHES_TO_M = 0.0254;

/**
 * One chained run of loose edges: its edges in walk order, the vertex ids
 * along it, and whether it returns to its first vertex (which is then NOT
 * repeated at the end).
 */
export interface LooseEdgeRun {
  edgeIds: number[];
  vertexIds: number[];
  closed: boolean;
}

/**
 * An analytic arc recovered for a loose-edge run: points in the run's own
 * frame (metres, glTF Y-up) plus 0-based `[start, mid, end]` index triples
 * into them, ready for IFC4 `IfcArcIndex`.
 */
export interface CurveArc {
  points: [number, number, number][];
  segments: [number, number, number][];
}

/** Round `x` to 6 decimal places. */
export function round6(x: number): number {
  if (!Number.isFinite(x)) return x;
  return Number(x.toFixed(6));
}

/**
 * Order ONE curve's edges into `{ edgeIds, vertexIds, closed }` runs.
 * A SketchUp Curve is a path or a single cycle; if branched, emits the
 * longest walk and orders the remainder again.
 */
export function orderCurveEdges(
  endsOf: (eid: number) => [number, number] | undefined,
  eids: readonly number[]
): LooseEdgeRun[] {
  const remaining = new Set<number>(eids);
  const runs: LooseEdgeRun[] = [];

  while (remaining.size > 0) {
    const adj = new Map<number, [number, number][]>();
    for (const e of remaining) {
      const ends = endsOf(e);
      if (!ends) continue;
      const [a, b] = ends;
      let listA = adj.get(a);
      if (!listA) {
        listA = [];
        adj.set(a, listA);
      }
      listA.push([e, b]);
      let listB = adj.get(b);
      if (!listB) {
        listB = [];
        adj.set(b, listB);
      }
      listB.push([e, a]);
    }
    if (adj.size === 0) break;

    let start: number | undefined;
    for (const [v, neighbors] of adj.entries()) {
      if (neighbors.length === 1) {
        start = v;
        break;
      }
    }
    if (start === undefined) {
      start = adj.keys().next().value as number;
    }

    const first = adj.get(start)![0];
    const eid = first[0];
    let nxt = first[1];
    remaining.delete(eid);
    const chainEids = [eid];
    const chain = [start, nxt];
    let closed = false;

    while (true) {
      const neighbors = adj.get(nxt);
      const nextOpt = neighbors ? neighbors.find((t) => remaining.has(t[0])) : undefined;
      if (!nextOpt) break;
      const [e2, v2] = nextOpt;
      remaining.delete(e2);
      chainEids.push(e2);
      if (v2 === chain[0]) {
        closed = true;
        break;
      }
      chain.push(v2);
      nxt = v2;
    }

    runs.push({ edgeIds: chainEids, vertexIds: chain, closed });
  }

  return runs;
}

/**
 * Turn a definition builder's *loose* edges (edges no face uses) into ordered
 * polyline runs, grouped strictly by `builder.edgeCurves` (`Edge#curve`).
 */
export function chainLooseEdges(builder: GeometryBuilder): LooseEdgeRun[] {
  const used = new Set<number>();
  for (const face of builder.faces.values()) {
    for (const loop of face.loops) {
      for (const co of loop) {
        used.add(co.edgeId);
      }
    }
  }

  const groups = new Map<number, number[]>();
  const runs: LooseEdgeRun[] = [];
  for (const [eid, [v1, v2]] of builder.edges.entries()) {
    if (used.has(eid)) continue;
    if (v1 === null || v2 === null || v1 === v2) continue;
    const cid = builder.edgeCurves.get(eid);
    if (cid) {
      let grp = groups.get(cid);
      if (!grp) {
        grp = [];
        groups.set(cid, grp);
      }
      grp.push(eid);
    } else {
      runs.push({ edgeIds: [eid], vertexIds: [v1, v2], closed: false });
    }
  }

  const endsOf = (eid: number): [number, number] | undefined => {
    const a = builder.edges.v1(eid);
    const b = builder.edges.v2(eid);
    if (a === null || a === undefined || b === null || b === undefined) return undefined;
    return [a, b];
  };
  for (const eids of groups.values()) {
    runs.push(...orderCurveEdges(endsOf, eids));
  }
  return runs;
}

/** Resolve the majority layer of a loose-edge run's edges, or `fallback`. */
export function resolveRunLayer(
  builder: GeometryBuilder,
  layerIdToName: Map<number, string>,
  edgeIds: readonly number[],
  fallback: string
): string {
  if (builder.edgeLayers.size === 0) return fallback;
  const counts = new Map<number, number>();
  for (const eid of edgeIds) {
    const lid = builder.edgeLayers.get(eid);
    if (lid) counts.set(lid, (counts.get(lid) ?? 0) + 1);
  }
  if (counts.size === 0) return fallback;
  let bestLid = 0;
  let bestCount = -1;
  for (const [lid, count] of counts.entries()) {
    if (count > bestCount) {
      bestLid = lid;
      bestCount = count;
    }
  }
  return layerIdToName.get(bestLid) ?? fallback;
}

function dist3(p: readonly [number, number, number], q: readonly [number, number, number]): number {
  const dx = p[0] - q[0];
  const dy = p[1] - q[1];
  const dz = p[2] - q[2];
  return Math.sqrt(dx * dx + dy * dy + dz * dz);
}

/**
 * Recover the analytic circular arc behind one loose-edge run, or `null` when
 * the run is not one whole circular `ArcCurve` whose stored vertices sit on
 * its frame.
 */
export function solveRunArc(
  builder: GeometryBuilder,
  eids: readonly number[],
  matrix: readonly number[] | null,
  pts: readonly [number, number, number][],
  closed: boolean = false
): CurveArc | null {
  if (builder.arcCurves.size === 0 || eids.length === 0) return null;
  const cid = builder.edgeCurves.get(eids[0]);
  if (cid === undefined) return null;
  const frame = builder.arcCurves.get(cid);
  if (!frame) return null;

  let curveEdgeCount = 0;
  for (const v of builder.edgeCurves.values()) {
    if (v === cid) curveEdgeCount++;
  }
  if (curveEdgeCount !== eids.length) return null;

  const xfPoint = (p: [number, number, number]): [number, number, number] => {
    const q = matrix ? transformPoint(matrix as number[], p) : p;
    return [q[0] * INCHES_TO_M, q[2] * INCHES_TO_M, -q[1] * INCHES_TO_M];
  };

  const xfVec = (v: [number, number, number]): [number, number, number] => {
    let x: number;
    let y: number;
    let z: number;
    if (!matrix || matrix.length < 12) {
      [x, y, z] = v;
    } else {
      x = matrix[0] * v[0] + matrix[1] * v[1] + matrix[2] * v[2];
      y = matrix[3] * v[0] + matrix[4] * v[1] + matrix[5] * v[2];
      z = matrix[6] * v[0] + matrix[7] * v[1] + matrix[8] * v[2];
    }
    return [x * INCHES_TO_M, z * INCHES_TO_M, -y * INCHES_TO_M];
  };

  const c = xfPoint(frame.center);
  const xa = xfVec(frame.xAxis);
  const ya = xfVec(frame.yAxis);
  const na = xfVec(frame.normal);

  const r = Math.sqrt(xa[0] * xa[0] + xa[1] * xa[1] + xa[2] * xa[2]);
  if (r <= 0) return null;

  const nlen = Math.sqrt(na[0] * na[0] + na[1] * na[1] + na[2] * na[2]);
  if (nlen <= 0) return null;
  const nh: [number, number, number] = [na[0] / nlen, na[1] / nlen, na[2] / nlen];
  const cross: [number, number, number] = [
    nh[1] * xa[2] - nh[2] * xa[1],
    nh[2] * xa[0] - nh[0] * xa[2],
    nh[0] * xa[1] - nh[1] * xa[0],
  ];
  if (dist3(cross, ya) > 1e-6 * r) return null;

  const invR = 1.0 / r;
  const xh: [number, number, number] = [xa[0] * invR, xa[1] * invR, xa[2] * invR];
  const yh: [number, number, number] = [ya[0] * invR, ya[1] * invR, ya[2] * invR];

  const tol = Math.max(1e-5, 1e-6 * r);
  for (const p of pts) {
    if (Math.abs(dist3(p, c) - r) > tol) return null;
  }

  const theta = (p: readonly [number, number, number]): number => {
    const dx = p[0] - c[0];
    const dy = p[1] - c[1];
    const dz = p[2] - c[2];
    return Math.atan2(
      dx * yh[0] + dy * yh[1] + dz * yh[2],
      dx * xh[0] + dy * xh[1] + dz * xh[2]
    );
  };

  const thetas = pts.map(theta);
  const pairs: [number, number][] = [];
  for (let i = 0; i + 1 < thetas.length; i++) {
    pairs.push([thetas[i], thetas[i + 1]]);
  }
  if (closed && thetas.length > 2) {
    pairs.push([thetas[thetas.length - 1], thetas[0]]);
  }

  const stepLimit = Math.PI * 0.5 * (1.0 + 1e-9);
  const steps: number[] = [];
  let total = 0.0;
  for (const [a, b] of pairs) {
    let d = b - a;
    while (d > Math.PI) d -= 2.0 * Math.PI;
    while (d < -Math.PI) d += 2.0 * Math.PI;
    if (Math.abs(d) > stepLimit) return null;
    if (steps.length > 0 && (d > 0) !== (steps[0] > 0)) return null;
    steps.push(d);
    total += d;
  }
  if (steps.length === 0 || total === 0.0) return null;

  const at = (t: number): [number, number, number] => {
    const ct = Math.cos(t);
    const st = Math.sin(t);
    return [
      c[0] + ct * xa[0] + st * ya[0],
      c[1] + ct * xa[1] + st * ya[1],
      c[2] + ct * xa[2] + st * ya[2],
    ];
  };

  const t0 = thetas[0];
  if (Math.abs(total) >= 2.0 * Math.PI - 1e-6 * Math.max(1.0, Math.abs(total))) {
    return {
      points: [at(t0), at(t0 + total * 0.25), at(t0 + total * 0.5), at(t0 + total * 0.75)],
      segments: [
        [0, 1, 2],
        [2, 3, 0],
      ],
    };
  }
  return {
    points: [at(t0), at(t0 + total * 0.5), at(t0 + total)],
    segments: [[0, 1, 2]],
  };
}
