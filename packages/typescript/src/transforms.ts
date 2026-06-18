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
    const tx = matrix[0] * x + matrix[1] * y + matrix[2] * z + matrix[9];
    const ty = matrix[3] * x + matrix[4] * y + matrix[5] * z + matrix[10];
    const tz = matrix[6] * x + matrix[7] * y + matrix[8] * z + matrix[11];
    return [tx, ty, tz];
  }
}

export function multiplyMatrices(a: number[], b: number[]): number[] {
  if (!a || a.length === 0) return b;
  if (!b || b.length === 0) return a;

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
    const aPad = a.length < 13 ? [...a, ...new Array(13 - a.length).fill(0)] : a;
    const bPad = b.length < 13 ? [...b, ...new Array(13 - b.length).fill(0)] : b;
    if (a.length < 13 && aPad[12] === 0) aPad[12] = 1.0;
    if (b.length < 13 && bPad[12] === 0) bPad[12] = 1.0;

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
    out[12] = aPad[12] * bPad[12];
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
