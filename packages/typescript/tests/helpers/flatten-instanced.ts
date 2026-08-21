import type { InstancedScene, InstancedNode } from '../../src/instanced';

/**
 * Test-only helper: collapse an {@link InstancedScene} back into flat,
 * world-space triangles, so it can be compared against what the baked
 * {@link buildScene} path produces.
 *
 * Deliberately NOT public API: the whole point of the instanced output is
 * to avoid materialising this. It exists so the tests can prove the two
 * paths describe the same geometry, which is the property that makes the
 * instanced path safe to adopt.
 */

export interface FlatTriangle {
  /** World-space vertex positions, metres, glTF Y-up. */
  a: [number, number, number];
  b: [number, number, number];
  c: [number, number, number];
  materialIndex: number;
}

/** Multiply two 16-element column-major matrices (out = a * b). */
function multiply4(a: number[], b: number[]): number[] {
  const out = new Array(16).fill(0);
  for (let col = 0; col < 4; col++) {
    for (let row = 0; row < 4; row++) {
      let s = 0;
      for (let k = 0; k < 4; k++) {
        s += a[k * 4 + row] * b[col * 4 + k];
      }
      out[col * 4 + row] = s;
    }
  }
  return out;
}

/** Apply a 16-element column-major matrix to a point. */
function applyMatrix(m: number[], p: [number, number, number]): [number, number, number] {
  const [x, y, z] = p;
  return [
    m[0] * x + m[4] * y + m[8] * z + m[12],
    m[1] * x + m[5] * y + m[9] * z + m[13],
    m[2] * x + m[6] * y + m[10] * z + m[14],
  ];
}

const IDENTITY4 = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1];

/**
 * Walk the instanced tree, composing node transforms, and emit every
 * triangle in world space - i.e. reconstruct what buildScene() bakes.
 */
export function flattenInstancedScene(scene: InstancedScene): FlatTriangle[] {
  const byId = new Map(scene.meshResources.map((r) => [r.id, r]));
  const out: FlatTriangle[] = [];

  const visit = (node: InstancedNode, parentMatrix: number[]) => {
    const world = multiply4(parentMatrix, node.matrix);

    if (node.meshResourceId !== undefined) {
      const res = byId.get(node.meshResourceId);
      if (res) {
        for (const prim of res.primitives) {
          for (let i = 0; i < prim.indices.length; i += 3) {
            const tri: [number, number, number][] = [];
            for (let k = 0; k < 3; k++) {
              const vi = prim.indices[i + k];
              tri.push(
                applyMatrix(world, [
                  prim.positions[vi * 3],
                  prim.positions[vi * 3 + 1],
                  prim.positions[vi * 3 + 2],
                ])
              );
            }
            out.push({
              a: tri[0],
              b: tri[1],
              c: tri[2],
              materialIndex: prim.materialIndex,
            });
          }
        }
      }
    }

    for (const child of node.children) {
      visit(child, world);
    }
  };

  visit(scene.sceneHierarchy, IDENTITY4);
  return out;
}

/** Total bytes held by every mesh resource's geometry buffers. */
export function instancedBufferBytes(scene: InstancedScene): number {
  let total = 0;
  for (const res of scene.meshResources) {
    for (const p of res.primitives) {
      total += p.positions.byteLength + p.normals.byteLength + p.uvs.byteLength + p.indices.byteLength;
    }
  }
  return total;
}

/**
 * Canonical, order-independent signature of a triangle set: each triangle
 * reduced to its rounded vertex coordinates plus its RESOLVED MATERIAL,
 * then sorted.
 *
 * Three things make a naive comparison fail on geometry that is in fact
 * identical, and all three are addressed here:
 *
 * - Ordering. The two paths group and emit faces in their own order, so
 *   sequences differ while the sets do not; hence the sort, and the sort of
 *   each triangle's own three corners so winding/rotation of the same
 *   triangle does not read as a different triangle.
 * - Material INDICES. Both paths build the same glTF material table, but
 *   allocate into it in the order they first encounter each material: the
 *   baked path during its flattened walk, the instanced path as each mesh
 *   resource is first created. Comparing raw indices would therefore report
 *   a difference that does not exist, so the signature carries the resolved
 *   material's CONTENT instead.
 * - Float32 rounding POINT. The baked path transforms in float64 and stores
 *   the world-space result as float32; the instanced path stores the
 *   local-space value as float32 and the transform happens afterwards. Both
 *   are single-rounding-step correct, but they round at different moments,
 *   so a coordinate can land one float32 ulp apart between them. See
 *   {@link SIGNATURE_DECIMALS}.
 *
 * @param decimals - Coordinate rounding, in decimal places of a metre.
 */
export const SIGNATURE_DECIMALS = 3;

export function triangleSignature(
  tris: { a: [number, number, number]; b: [number, number, number]; c: [number, number, number]; materialIndex: number }[],
  materials?: unknown[],
  decimals: number = SIGNATURE_DECIMALS
): string[] {
  const eps = Math.pow(10, -decimals) / 2;
  const r = (v: number) => {
    const rounded = Number(v.toFixed(decimals));
    return Math.abs(rounded) < eps ? 0 : rounded;
  };
  const materialKey = (idx: number) =>
    materials ? JSON.stringify(materials[idx] ?? null) : String(idx);
  return tris
    .map((t) => {
      const corners = [t.a, t.b, t.c]
        .map((p) => `${r(p[0])},${r(p[1])},${r(p[2])}`)
        .sort();
      return `${corners.join('|')}#${materialKey(t.materialIndex)}`;
    })
    .sort();
}

/**
 * Compare two triangle sets IN ORDER, with a numeric tolerance.
 *
 * Both builders walk the same instance tree in the same order and group
 * faces by the same rule, so the k-th triangle of one corresponds to the
 * k-th triangle of the other. Comparing in order is not just simpler than
 * set matching - it is the only correct option here, because real models
 * contain many geometrically coincident triangles (coplanar duplicates,
 * mirrored halves), and a nearest-neighbour matcher happily pairs a
 * triangle with the wrong twin and then reports a phantom difference.
 *
 * Tolerance defaults to 1e-5 m (10 micrometres), justified by float32
 * precision rather than picked to make the suite pass. The two paths reach
 * float32 by different routes - the baked path transforms in float64 then
 * stores the world-space result, the instanced path stores the local-space
 * value then transforms - so identical geometry differs by an ulp or two of
 * the LARGER, world-space magnitude. float32 keeps ~7 significant decimal
 * digits, so at the ~50 m extent of the repository's largest fixture one
 * ulp is already ~4e-6 m; 1e-5 m covers a couple of those while staying
 * three orders of magnitude below a millimetre, far under anything
 * geometrically meaningful. The tests also assert the WORST delta actually
 * observed, so a real regression that shifts geometry still fails loudly.
 */
export function compareTrianglesInOrder(
  actual: FlatTriangle[],
  expected: FlatTriangle[],
  materials: { actual?: unknown[]; expected?: unknown[] } = {},
  tolerance = 1e-5
): { worstDelta: number; firstMismatch: string | null; materialMismatches: number } {
  const matKey = (t: FlatTriangle, mats?: unknown[]) =>
    JSON.stringify(mats ? mats[t.materialIndex] ?? null : t.materialIndex);

  let worstDelta = 0;
  let firstMismatch: string | null = null;
  let materialMismatches = 0;

  const n = Math.min(actual.length, expected.length);
  for (let i = 0; i < n; i++) {
    const a = actual[i];
    const e = expected[i];

    if (matKey(a, materials.actual) !== matKey(e, materials.expected)) {
      materialMismatches++;
      if (firstMismatch === null) {
        firstMismatch = `triangle ${i}: material ${matKey(a, materials.actual)} != ${matKey(e, materials.expected)}`;
      }
    }

    for (const [pa, pe] of [[a.a, e.a], [a.b, e.b], [a.c, e.c]] as const) {
      for (let k = 0; k < 3; k++) {
        const d = Math.abs(pa[k] - pe[k]);
        if (d > worstDelta) worstDelta = d;
        if (d > tolerance && firstMismatch === null) {
          firstMismatch = `triangle ${i}: coordinate delta ${d.toExponential(3)} (${pa.join(',')} vs ${pe.join(',')})`;
        }
      }
    }
  }

  return { worstDelta, firstMismatch, materialMismatches };
}

/** Flatten the baked path's primitives into the same triangle shape. */
export function flattenBakedScene(scene: {
  glbPrimitives: { positions: Float32Array; indices: Uint32Array; materialIndex: number }[];
}): FlatTriangle[] {
  const out: FlatTriangle[] = [];
  for (const prim of scene.glbPrimitives) {
    for (let i = 0; i < prim.indices.length; i += 3) {
      const tri: [number, number, number][] = [];
      for (let k = 0; k < 3; k++) {
        const vi = prim.indices[i + k];
        tri.push([prim.positions[vi * 3], prim.positions[vi * 3 + 1], prim.positions[vi * 3 + 2]]);
      }
      out.push({ a: tri[0], b: tri[1], c: tri[2], materialIndex: prim.materialIndex });
    }
  }
  return out;
}

/** Total bytes held by the baked path's geometry buffers. */
export function bakedBufferBytes(scene: {
  glbPrimitives: { positions: Float32Array; normals: Float32Array; uvs: Float32Array; indices: Uint32Array }[];
}): number {
  let total = 0;
  for (const p of scene.glbPrimitives) {
    total += p.positions.byteLength + p.normals.byteLength + p.uvs.byteLength + p.indices.byteLength;
  }
  return total;
}
