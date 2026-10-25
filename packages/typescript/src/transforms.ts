export const INCHES_TO_METRES = 0.0254;

export const IDENTITY_MATRIX = [
  1.0, 0.0, 0.0, 0.0,
  0.0, 1.0, 0.0, 0.0,
  0.0, 0.0, 1.0, 0.0,
  0.0, 0.0, 0.0, 1.0,
];

export const IDENTITY_MATRIX_13 = [
  1.0, 0.0, 0.0,
  0.0, 1.0, 0.0,
  0.0, 0.0, 1.0,
  0.0, 0.0, 0.0,
  1.0,
];

/**
 * Fold a 13-value instance matrix's scale term (index 12) into its rotation
 * and translation, so index 12 is 1.
 *
 * SketchUp can keep a uniform scale in the transformation's homogeneous term:
 * the 3x3 stays as it is, the translation is in the scaled units, and the 16th
 * value `w` is the divisor (`Transformation#to_a`'s w = 100 means 1/100). The
 * 13-value matrix stores `1/w` at index 12, so a point transforms as
 * `s * (R p + t)` with `s = m[12]`; folding gives the equivalent `(s R) p + s t`
 * with `s = 1`, which every consumer that reads only indices 0-11 handles
 * correctly (openskp#444). A matrix whose scale is already 1 - everything
 * SketchUp writes except that case - is returned unchanged. 16-value matrices
 * are column-major 4x4 and are never folded.
 */
export function foldScale(m: number[]): number[] {
  if (m.length < 13 || m.length >= 16 || m[12] === 1) return m;
  const s = m[12];
  const out = m.slice(0, 12).map((v) => v * s);
  out.push(1);
  return out;
}

export function transformPoint(
  matrix: number[],
  point: [number, number, number]
): [number, number, number] {
  if (!matrix || matrix.length < 12) {
    return point;
  }
  const [x, y, z] = point;
  if (matrix.length === 16) {
    const tx = matrix[0] * x + matrix[4] * y + matrix[8] * z + matrix[12];
    const ty = matrix[1] * x + matrix[5] * y + matrix[9] * z + matrix[13];
    const tz = matrix[2] * x + matrix[6] * y + matrix[10] * z + matrix[14];
    return [tx, ty, tz];
  } else {
    // 12 or 13 element matrix layout (from _core.py)
    const m = foldScale(matrix);
    const tx = m[0] * x + m[1] * y + m[2] * z + m[9];
    const ty = m[3] * x + m[4] * y + m[5] * z + m[10];
    const tz = m[6] * x + m[7] * y + m[8] * z + m[11];
    return [tx, ty, tz];
  }
}

export function multiplyMatrices(a: number[], b: number[]): number[] {
  if (!a || a.length === 0) return b && b.length === 13 ? foldScale(b) : b;
  if (!b || b.length === 0) return a.length === 13 ? foldScale(a) : a;

  if (a.length === 16 && b.length === 16) {
    const result: number[] = new Array(16).fill(0);
    for (let row = 0; row < 4; row++) {
      for (let col = 0; col < 4; col++) {
        let s = 0.0;
        for (let k = 0; k < 4; k++) {
          s += a[k * 4 + row] * b[col * 4 + k];
        }
        result[col * 4 + row] = s;
      }
    }
    return result;
  } else {
    // Treat as 13-element matrices
    // Ensure both are padded to at least 13 elements if shorter
    const aRaw = a.length < 13 ? [...a, ...new Array(13 - a.length).fill(0)] : a;
    const bRaw = b.length < 13 ? [...b, ...new Array(13 - b.length).fill(0)] : b;
    if (a.length < 13 && aRaw[12] === 0) aRaw[12] = 1.0;
    if (b.length < 13 && bRaw[12] === 0) bRaw[12] = 1.0;
    // Both operands' scale terms are folded in first, so the product's own is 1.
    const aPad = foldScale(aRaw);
    const bPad = foldScale(bRaw);

    const p_r0 = [aPad[0], aPad[1], aPad[2], aPad[9]];
    const p_r1 = [aPad[3], aPad[4], aPad[5], aPad[10]];
    const p_r2 = [aPad[6], aPad[7], aPad[8], aPad[11]];

    const c_c0 = [bPad[0], bPad[3], bPad[6], 0];
    const c_c1 = [bPad[1], bPad[4], bPad[7], 0];
    const c_c2 = [bPad[2], bPad[5], bPad[8], 0];
    const c_c3 = [bPad[9], bPad[10], bPad[11], 1];

    const dot = (row: number[], col: number[]) => {
      return row[0] * col[0] + row[1] * col[1] + row[2] * col[2] + row[3] * col[3];
    };

    const out = new Array(13).fill(0);
    out[0] = dot(p_r0, c_c0);
    out[1] = dot(p_r0, c_c1);
    out[2] = dot(p_r0, c_c2);
    out[3] = dot(p_r1, c_c0);
    out[4] = dot(p_r1, c_c1);
    out[5] = dot(p_r1, c_c2);
    out[6] = dot(p_r2, c_c0);
    out[7] = dot(p_r2, c_c1);
    out[8] = dot(p_r2, c_c2);
    out[9] = dot(p_r0, c_c3);
    out[10] = dot(p_r1, c_c3);
    out[11] = dot(p_r2, c_c3);
    out[12] = 1.0; // both operands were folded (index 12 is 1)
    return out;
  }
}

export function zUpToYUp(x: number, y: number, z: number): [number, number, number] {
  return [x, z, -y];
}

export function inchesToMetres(x: number, y: number, z: number): [number, number, number] {
  return [x * INCHES_TO_METRES, y * INCHES_TO_METRES, z * INCHES_TO_METRES];
}

export function decomposeTranslation(matrix: number[]): [number, number, number] {
  if (matrix.length === 16) {
    return [matrix[12], matrix[13], matrix[14]];
  } else {
    return [matrix[9], matrix[10], matrix[11]];
  }
}

export function isIdentity(matrix: number[], tol: number = 1e-9): boolean {
  if (!matrix || matrix.length === 0) return true;
  if (matrix.length === 16) {
    for (let i = 0; i < 16; i++) {
      const expected = i % 5 === 0 ? 1.0 : 0.0;
      if (Math.abs(matrix[i] - expected) > tol) {
        return false;
      }
    }
    return true;
  } else {
    // 13 elements
    const expected = [
      1, 0, 0,
      0, 1, 0,
      0, 0, 1,
      0, 0, 0,
      1,
    ];
    for (let i = 0; i < Math.min(matrix.length, 13); i++) {
      if (Math.abs(matrix[i] - expected[i]) > tol) {
        return false;
      }
    }
    return true;
  }
}
