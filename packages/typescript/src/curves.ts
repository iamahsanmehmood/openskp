import { GeometryBuilder } from './geometry';
import { foldScale } from './transforms';

export const INCHES_TO_M = 0.0254;
const NON_DRAWABLE_EDGE_MASK = 0x01 | 0x08 | 0x10; // hidden | soft | smooth

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

export interface PreparedCurveRun {
  edgeIds: number[];
  localVerts: [number, number, number][];
  closed: boolean;
  explicitLayer?: string;
}

export interface PreparedDefCurves {
  runs: PreparedCurveRun[];
  curveEdgeCounts?: Map<number, number>;
}

const _round6Dv = new DataView(new ArrayBuffer(8));

/** Round `x` to 6 decimal places, matching Python's `round(x, 6)` (exact IEEE-754 binary64 ties-to-even). */
export function round6(x: number): number {
  if (!Number.isFinite(x) || Math.abs(x) >= 8589934592.0 || x === 0) return x;
  const absScaled = Math.abs(x * 1e6);
  if (absScaled < 4.5e12) {
    const fl = Math.floor(absScaled);
    const frac = absScaled - fl;
    if (Math.abs(frac - 0.5) > 1e-3) {
      const res = (frac > 0.5 ? fl + 1 : fl) / 1e6;
      return x < 0 ? -res : res;
    }
  }
  _round6Dv.setFloat64(0, Math.abs(x), true);
  const bits = _round6Dv.getBigUint64(0, true);
  const rawExp = Number((bits >> 52n) & 0x7ffn);
  if (rawExp === 0) return x < 0 ? -0 : 0;
  const mant = (1n << 52n) | (bits & 0xfffffffffffffn);
  const shift = BigInt(1069 - rawExp);
  const num = mant * 15625n;
  let k = num >> shift;
  const rem = num & ((1n << shift) - 1n);
  const half = 1n << (shift - 1n);
  if (rem > half || (rem === half && (k & 1n) === 1n)) {
    k += 1n;
  }
  const res = Number(k) / 1e6;
  return x < 0 ? -res : res;
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
export function chainLooseEdges(
  builder: GeometryBuilder,
  respectVisibility = false
): LooseEdgeRun[] {
  if (builder.edges.size === 0) return [];

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
    if (respectVisibility && ((builder.edgeFlags.get(eid) ?? 0) & NON_DRAWABLE_EDGE_MASK) !== 0) {
      continue;
    }
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

/** Index total edge count per `curveId` in `O(|edgeCurves|)`. */
export function indexEdgeCurves(builder: GeometryBuilder): Map<number, number> {
  const counts = new Map<number, number>();
  for (const cid of builder.edgeCurves.values()) {
    if (cid) {
      counts.set(cid, (counts.get(cid) ?? 0) + 1);
    }
  }
  return counts;
}

/** Resolve the majority layer of a loose-edge run's edges, or `fallback`. */
export function resolveRunLayer(
  builder: GeometryBuilder,
  layerIdToName: Map<number, string>,
  edgeIds: readonly number[],
  fallback: string
): string {
  const explicit = resolveExplicitRunLayer(builder, layerIdToName, edgeIds);
  return explicit ?? fallback;
}

function resolveExplicitRunLayer(
  builder: GeometryBuilder,
  layerIdToName: Map<number, string>,
  edgeIds: readonly number[]
): string | undefined {
  if (builder.edgeLayers.size === 0) return undefined;
  const counts = new Map<number, number>();
  for (const eid of edgeIds) {
    const lid = builder.edgeLayers.get(eid);
    if (lid) counts.set(lid, (counts.get(lid) ?? 0) + 1);
  }
  if (counts.size === 0) return undefined;
  let bestLid = 0;
  let bestCount = -1;
  for (const [lid, count] of counts.entries()) {
    if (count > bestCount) {
      bestLid = lid;
      bestCount = count;
    }
  }
  return layerIdToName.get(bestLid);
}

/** Precompute chained loose-edge runs, local vertices, explicit run layers,
 * and per-curve edge counts once for a definition. */
export function prepareDefinitionCurves(
  builder: GeometryBuilder,
  layerIdToName: Map<number, string>,
  respectVisibility = false
): PreparedDefCurves {
  const rawRuns = chainLooseEdges(builder, respectVisibility);
  if (rawRuns.length === 0) return { runs: [] };

  const runs: PreparedCurveRun[] = [];
  for (const run of rawRuns) {
    const localVerts: [number, number, number][] = [];
    let complete = true;
    for (const vk of run.vertexIds) {
      const v = builder.vertices.get(vk);
      if (!v) {
        complete = false;
        break;
      }
      localVerts.push(v);
    }
    if (!complete || localVerts.length < 2) continue;
    runs.push({
      edgeIds: run.edgeIds,
      localVerts,
      closed: run.closed,
      explicitLayer: resolveExplicitRunLayer(builder, layerIdToName, run.edgeIds),
    });
  }
  if (runs.length === 0) return { runs: [] };
  const curveEdgeCounts = builder.arcCurves.size > 0 ? indexEdgeCurves(builder) : undefined;
  return { runs, curveEdgeCounts };
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
  closed: boolean = false,
  curveEdgeCounts?: ReadonlyMap<number, number>
): CurveArc | null {
  if (builder.arcCurves.size === 0 || eids.length === 0) return null;
  const cid = builder.edgeCurves.get(eids[0]);
  if (cid === undefined) return null;
  const frame = builder.arcCurves.get(cid);
  if (!frame) return null;

  let curveEdgeCount = 0;
  if (curveEdgeCounts !== undefined) {
    curveEdgeCount = curveEdgeCounts.get(cid) ?? 0;
  } else {
    for (const v of builder.edgeCurves.values()) {
      if (v === cid) curveEdgeCount++;
    }
  }
  if (curveEdgeCount !== eids.length) return null;

  const fm = matrix && matrix.length >= 12 ? foldScale(matrix as number[]) : null;
  let mirrored = false;
  if (fm) {
    const det =
      fm.length === 16
        ? fm[0] * (fm[5] * fm[10] - fm[6] * fm[9]) -
          fm[4] * (fm[1] * fm[10] - fm[2] * fm[9]) +
          fm[8] * (fm[1] * fm[6] - fm[2] * fm[5])
        : fm[0] * (fm[4] * fm[8] - fm[5] * fm[7]) -
          fm[1] * (fm[3] * fm[8] - fm[5] * fm[6]) +
          fm[2] * (fm[3] * fm[7] - fm[4] * fm[6]);
    mirrored = det < 0;
  }

  const xfPoint = (p: [number, number, number]): [number, number, number] => {
    if (!fm) {
      return [p[0] * INCHES_TO_M, p[2] * INCHES_TO_M, -p[1] * INCHES_TO_M];
    }
    let q0: number;
    let q1: number;
    let q2: number;
    if (fm.length === 16) {
      q0 = fm[0] * p[0] + fm[4] * p[1] + fm[8] * p[2] + fm[12];
      q1 = fm[1] * p[0] + fm[5] * p[1] + fm[9] * p[2] + fm[13];
      q2 = fm[2] * p[0] + fm[6] * p[1] + fm[10] * p[2] + fm[14];
    } else {
      q0 = fm[0] * p[0] + fm[1] * p[1] + fm[2] * p[2] + fm[9];
      q1 = fm[3] * p[0] + fm[4] * p[1] + fm[5] * p[2] + fm[10];
      q2 = fm[6] * p[0] + fm[7] * p[1] + fm[8] * p[2] + fm[11];
    }
    return [q0 * INCHES_TO_M, q2 * INCHES_TO_M, -q1 * INCHES_TO_M];
  };

  const xfVec = (v: [number, number, number]): [number, number, number] => {
    let x: number;
    let y: number;
    let z: number;
    if (!fm) {
      [x, y, z] = v;
    } else if (fm.length === 16) {
      x = fm[0] * v[0] + fm[4] * v[1] + fm[8] * v[2];
      y = fm[1] * v[0] + fm[5] * v[1] + fm[9] * v[2];
      z = fm[2] * v[0] + fm[6] * v[1] + fm[10] * v[2];
    } else {
      x = fm[0] * v[0] + fm[1] * v[1] + fm[2] * v[2];
      y = fm[3] * v[0] + fm[4] * v[1] + fm[5] * v[2];
      z = fm[6] * v[0] + fm[7] * v[1] + fm[8] * v[2];
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
  const nSign = mirrored ? -1 : 1;
  const nh: [number, number, number] = [
    (nSign * na[0]) / nlen,
    (nSign * na[1]) / nlen,
    (nSign * na[2]) / nlen,
  ];
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
