import earcut from 'earcut';

/**
 * Compute face normal using Newell's method on a loop of 3D points.
 *
 * @param points - Ordered list of 3D coordinates representing a polygon loop.
 * @returns Normal vector [nx, ny, nz] or null if degenerate.
 */
export function computeFaceNormal(
  points: [number, number, number][]
): [number, number, number] | null {
  const n = points.length;
  if (n < 3) return null;

  let nx = 0.0;
  let ny = 0.0;
  let nz = 0.0;

  for (let i = 0; i < n; i++) {
    const cur = points[i];
    const nxt = points[(i + 1) % n];
    nx += (cur[1] - nxt[1]) * (cur[2] + nxt[2]);
    ny += (cur[2] - nxt[2]) * (cur[0] + nxt[0]);
    nz += (cur[0] - nxt[0]) * (cur[1] + nxt[1]);
  }

  const length = Math.sqrt(nx * nx + ny * ny + nz * nz);
  if (length < 1e-12) {
    return null;
  }
  return [nx / length, ny / length, nz / length];
}

/**
 * Projects 3D loop vertices onto a 2D plane using the face normal,
 * runs 'earcut' on the flat coordinates, and maps the resulting triangle indices
 * back to the original 3D vertex IDs.
 *
 * @param vertices3D - Map or record lookup of vertex coordinates by ID.
 * @param loops - Array of loops, where each loop is an array of vertex IDs.
 * @param normal - 3D plane normal vector [nx, ny, nz].
 * @returns Array of triangles, where each triangle is [vId0, vId1, vId2].
 */
export function triangulateFace3D(
  vertices3D:
    | Map<number, { x: number; y: number; z: number } | [number, number, number] | number[]>
    | Record<number, { x: number; y: number; z: number } | [number, number, number] | number[]>
    // Any id-keyed lookup with a Map-shaped `get`, e.g. VertexStore.
    | { get(id: number): { x: number; y: number; z: number } | [number, number, number] | number[] | undefined },
  loops: number[][],
  normal: [number, number, number]
): number[][] {
  if (loops.length === 0) return [];

  // Trivial optimization for simple triangles and quads (no holes)
  if (loops.length === 1 && loops[0].length === 3) {
    return [loops[0]];
  }
  if (loops.length === 1 && loops[0].length === 4) {
    const v = loops[0];
    return [
      [v[0], v[1], v[2]],
      [v[0], v[2], v[3]],
    ];
  }

  // Project to 2D
  let nx = normal[0];
  let ny = normal[1];
  let nz = normal[2];
  const normVal = Math.sqrt(nx * nx + ny * ny + nz * nz);
  if (normVal > 1e-6) {
    nx /= normVal;
    ny /= normVal;
    nz /= normVal;
  } else {
    nx = 0.0;
    ny = 0.0;
    nz = 1.0;
  }

  let u_axis: [number, number, number];
  if (Math.abs(nx) < 0.9) {
    u_axis = [1.0, 0.0, 0.0];
  } else {
    u_axis = [0.0, 1.0, 0.0];
  }

  // u_axis = normal x u_axis
  let ux = ny * u_axis[2] - nz * u_axis[1];
  let uy = nz * u_axis[0] - nx * u_axis[2];
  let uz = nx * u_axis[1] - ny * u_axis[0];
  const uLen = Math.sqrt(ux * ux + uy * uy + uz * uz);
  if (uLen < 1e-12) {
    ux = 1.0; uy = 0.0; uz = 0.0;
  } else {
    ux /= uLen; uy /= uLen; uz /= uLen;
  }

  // v_axis = normal x u_axis
  let vx = ny * uz - nz * uy;
  let vy = nz * ux - nx * uz;
  let vz = nx * uy - ny * ux;
  const vLen = Math.sqrt(vx * vx + vy * vy + vz * vz);
  if (vLen > 1e-12) {
    vx /= vLen; vy /= vLen; vz /= vLen;
  }

  const getVertex = (id: number): { x: number; y: number; z: number } | null => {
    let pt: any;
    // Anything exposing a Map-shaped `get` is queried through it; only a
    // plain Record falls back to index access. Checking for the method
    // rather than `instanceof Map` is what lets a VertexStore be passed
    // here without the Record branch silently returning undefined.
    if (typeof (vertices3D as any).get === 'function') {
      pt = (vertices3D as any).get(id);
    } else {
      pt = (vertices3D as any)[id];
    }
    if (!pt) return null;
    if (Array.isArray(pt)) {
      return { x: pt[0], y: pt[1], z: pt[2] };
    }
    return pt;
  };

  // 2D (u, v) projection for every loop, kept as parallel per-loop arrays
  // so overlap detection below can work in plain {u, v} coordinates
  // without touching vertex IDs at all.
  const loops2d: { u: number; v: number }[][] = [];
  for (const loop of loops) {
    const pts: { u: number; v: number }[] = [];
    for (const vId of loop) {
      const pt = getVertex(vId);
      if (!pt) {
        return []; // missing vertex
      }
      pts.push({ u: pt.x * ux + pt.y * uy + pt.z * uz, v: pt.x * vx + pt.y * vy + pt.z * vz });
    }
    loops2d.push(pts);
  }

  // Holes are supposed to be simple and mutually disjoint - that's what
  // earcut's ring-based API assumes. A real file can carry two hole loops
  // that genuinely overlap (observed on a real fixture: two ~0.69"-radius
  // circles only 0.33" apart, evidently meant as one slotted/oval cutout
  // recorded as two separate full circles). Feeding overlapping rings to
  // earcut is undefined - ported from the same fix in the Python/.NET
  // ports (openskp#285): rather than compute an explicit union boundary
  // (which would need a general polygon-clipping routine this project
  // doesn't otherwise need), fall back to triangulating the outer
  // boundary alone and discarding any triangle whose centroid falls
  // inside ANY hole - the same "triangulate then filter" strategy this
  // project's own Python port used before its earcut migration, applied
  // narrowly only when real overlap is detected. A no-op for the
  // overwhelming common case of genuinely disjoint holes.
  if (loops.length > 2 && holesOverlap(loops2d)) {
    return triangulateByFilteringHoles(loops, loops2d);
  }

  const allVIds: number[] = [];
  const holeIndices: number[] = [];
  let currentOffset = 0;

  for (let l = 0; l < loops.length; l++) {
    if (l > 0) {
      holeIndices.push(currentOffset);
    }
    const loop = loops[l];
    for (const vId of loop) {
      allVIds.push(vId);
    }
    currentOffset += loop.length;
  }

  const flatCoords: number[] = [];
  for (const pts of loops2d) {
    for (const { u, v } of pts) {
      flatCoords.push(u, v);
    }
  }

  let triIndices: number[];
  try {
    triIndices = earcut(flatCoords, holeIndices, 2);
  } catch {
    // Fallback: simple fan triangulation of the outer loop if earcut fails
    const outerLoop = loops[0];
    const fallback: number[][] = [];
    for (let i = 1; i < outerLoop.length - 1; i++) {
      fallback.push([outerLoop[0], outerLoop[i], outerLoop[i + 1]]);
    }
    return fallback;
  }

  const result: number[][] = [];
  for (let i = 0; i < triIndices.length; i += 3) {
    const idx0 = triIndices[i];
    const idx1 = triIndices[i + 1];
    const idx2 = triIndices[i + 2];
    result.push([
      allVIds[idx0],
      allVIds[idx1],
      allVIds[idx2],
    ]);
  }

  return result;
}

/**
 * True when at least one pair of hole loops (index 1+ in `loops2d`) shares
 * real area - not just a boundary point or edge, which is a normal, common
 * pattern (e.g. two holes sharing a cut line). Detected via a
 * vertex-containment test: for genuinely overlapping simple polygons (in
 * particular the convex/circular holes real drilled geometry produces), the
 * overlap region always contains at least one polygon's own vertex inside
 * the other - a pathological overlap with no vertex crossing either
 * boundary is possible in principle for very concave shapes but not
 * observed in any real file so far. Mirrors the .NET/Python ports'
 * identical check exactly (openskp#285).
 */
function holesOverlap(loops2d: { u: number; v: number }[][]): boolean {
  for (let i = 1; i < loops2d.length; i++) {
    for (let j = i + 1; j < loops2d.length; j++) {
      if (anyVertexInside(loops2d[i], loops2d[j]) || anyVertexInside(loops2d[j], loops2d[i])) {
        return true;
      }
    }
  }
  return false;
}

function anyVertexInside(points: { u: number; v: number }[], polygon: { u: number; v: number }[]): boolean {
  for (const p of points) {
    if (pointInPolygon(p.u, p.v, polygon)) return true;
  }
  return false;
}

/** Standard ray-casting point-in-polygon test (even-odd rule) - true when
 * (u, v) lies strictly inside the given closed 2D ring. */
function pointInPolygon(u: number, v: number, polygon: { u: number; v: number }[]): boolean {
  let inside = false;
  const n = polygon.length;
  for (let i = 0, j = n - 1; i < n; j = i++) {
    const ui = polygon[i].u, vi = polygon[i].v;
    const uj = polygon[j].u, vj = polygon[j].v;
    const intersects = vi > v !== vj > v && u < ((uj - ui) * (v - vi)) / (vj - vi) + ui;
    if (intersects) inside = !inside;
  }
  return inside;
}

/**
 * Fallback path when two or more hole loops genuinely overlap: triangulate
 * the outer boundary alone (ignoring hole rings entirely), then discard any
 * resulting triangle whose centroid falls inside ANY hole. Mirrors the
 * .NET/Python ports' identical fallback exactly (openskp#285).
 */
function triangulateByFilteringHoles(
  loops: number[][],
  loops2d: { u: number; v: number }[][]
): number[][] {
  const outerLoop = loops[0];
  const outer2d = loops2d[0];
  const flatOuter: number[] = [];
  for (const { u, v } of outer2d) {
    flatOuter.push(u, v);
  }

  let triIndices: number[];
  try {
    triIndices = earcut(flatOuter, [], 2);
  } catch {
    const fallback: number[][] = [];
    for (let i = 1; i < outerLoop.length - 1; i++) {
      fallback.push([outerLoop[0], outerLoop[i], outerLoop[i + 1]]);
    }
    return fallback;
  }

  const result: number[][] = [];
  for (let i = 0; i < triIndices.length; i += 3) {
    const ia = triIndices[i], ib = triIndices[i + 1], ic = triIndices[i + 2];
    const cu = (outer2d[ia].u + outer2d[ib].u + outer2d[ic].u) / 3.0;
    const cv = (outer2d[ia].v + outer2d[ib].v + outer2d[ic].v) / 3.0;

    let insideAnyHole = false;
    for (let h = 1; h < loops2d.length; h++) {
      if (pointInPolygon(cu, cv, loops2d[h])) {
        insideAnyHole = true;
        break;
      }
    }
    if (insideAnyHole) continue;

    result.push([outerLoop[ia], outerLoop[ib], outerLoop[ic]]);
  }
  return result;
}
