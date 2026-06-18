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
    | Record<number, { x: number; y: number; z: number } | [number, number, number] | number[]>,
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
    if (vertices3D instanceof Map) {
      pt = vertices3D.get(id);
    } else {
      pt = vertices3D[id];
    }
    if (!pt) return null;
    if (Array.isArray(pt)) {
      return { x: pt[0], y: pt[1], z: pt[2] };
    }
    return pt;
  };

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
  for (const vId of allVIds) {
    const pt = getVertex(vId);
    if (!pt) {
      return []; // missing vertex
    }
    const u = pt.x * ux + pt.y * uy + pt.z * uz;
    const v = pt.x * vx + pt.y * vy + pt.z * vz;
    flatCoords.push(u, v);
  }

  let triIndices: number[];
  try {
    triIndices = earcut(flatCoords, holeIndices, 2);
  } catch (e) {
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
