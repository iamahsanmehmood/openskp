/** Texture placement, ported from Python's `_vff_writer/uv.py`. */
import { Record, f64s, record, u32 } from './core';
import type { Point3 } from './types';

export const T_UV_ATTR = 0x05dd;
export const T_UV_LIST = 0x36b1;
export const T_UV_SET = 0x36b2;
export const T_UV_COORDS = 0x2710;
export const T_UV_EMPTY = 0x36b0;
export const T_UV_FRONT = 0x2711;
export const T_UV_BACK = 0x2712;
export const T_UV_SIDE = 0x2713;
export const T_UV_FLAGS = 0x2714;
export const T_UV_MATRIX = 0x2715;
export const T_UV_OFFSET = 0x2716;
export const T_UV_PINS = 0x2717;

export const IDENTITY_MATRIX: number[] = [1, 0, 0, 0, 1, 0, 0, 0, 1];

export function faceUvBasis(normal: ArrayLike<number>): [Point3, Point3] {
  const nx = normal[0];
  const ny = normal[1];
  const nz = normal[2];
  const cx = -ny;
  const cy = nx;
  const length = Math.hypot(cx, cy);
  let xr: Point3;
  let yr: Point3;
  if (length < 1e-3) {
    xr = [nz >= 0 ? 1 : -1, 0, 0];
    yr = [0, 1, 0];
  } else {
    xr = [cx / length, cy / length, 0];
    yr = [
      ny * xr[2] - nz * xr[1],
      nz * xr[0] - nx * xr[2],
      nx * xr[1] - ny * xr[0],
    ];
  }
  return [xr, yr];
}

export function invert3(matrix: ArrayLike<number>): number[] {
  const [a, b, c, d, e, f, g, h, i] = Array.from(matrix, Number);
  const det = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g);
  if (Math.abs(det) < 1e-15) throw new Error('texture placement is degenerate (singular matrix)');
  return [
    (e * i - f * h) / det,
    (c * h - b * i) / det,
    (b * f - c * e) / det,
    (f * g - d * i) / det,
    (a * i - c * g) / det,
    (c * d - a * f) / det,
    (d * h - e * g) / det,
    (b * g - a * h) / det,
    (a * e - b * d) / det,
  ].map((v) => v + 0);
}

function solveAffine(points: Array<[number, number]>, values: number[]): [number, number, number] {
  if (points.length !== 3 || values.length !== 3) throw new Error('exactly three texture pins are supported');
  const [[x0, y0], [x1, y1], [x2, y2]] = points;
  const det = x0 * (y1 - y2) - y0 * (x1 - x2) + (x1 * y2 - x2 * y1);
  if (Math.abs(det) < 1e-15) throw new Error('texture pins must not be collinear');
  const [v0, v1, v2] = values;
  const a = (v0 * (y1 - y2) - y0 * (v1 - v2) + (v1 * y2 - v2 * y1)) / det;
  const b = (x0 * (v1 - v2) - v0 * (x1 - x2) + (x1 * v2 - x2 * v1)) / det;
  const c = (x0 * (y1 * v2 - y2 * v1) - y0 * (x1 * v2 - x2 * v1) + v0 * (x1 * y2 - x2 * y1)) / det;
  return [a + 0, b + 0, c + 0];
}

export function uvMatrixFromPins(
  pins: Array<[ArrayLike<number>, [number, number]]>,
  normal: ArrayLike<number>,
  tileWidth: number,
  tileHeight: number,
): number[] {
  if (Math.abs(tileWidth) < 1e-12 || Math.abs(tileHeight) < 1e-12) throw new Error('a textured material needs a non-zero applied size');
  const [xr, yr] = faceUvBasis(normal);
  const planePoints: Array<[number, number]> = [];
  for (const [point] of pins) {
    const x = point[0] * xr[0] + point[1] * xr[1] + point[2] * xr[2];
    const y = point[0] * yr[0] + point[1] * yr[1] + point[2] * yr[2];
    planePoints.push([x, y]);
  }
  const qx = pins.map(([, [u]]) => u * tileWidth);
  const qy = pins.map(([, [, v]]) => v * tileHeight);
  const [a, b, e] = solveAffine(planePoints, qx);
  const [c, d, f] = solveAffine(planePoints, qy);
  const inverse = [[a, c, 0], [b, d, 0], [e, f, 1]];
  return invert3(inverse.flat());
}

function side(matrix: number[], positioned: boolean): Record {
  return new Record(T_UV_SIDE, undefined, [
    record(T_UV_FLAGS, u32(positioned ? 1 : 0)),
    record(T_UV_MATRIX, f64s(matrix)),
    record(T_UV_OFFSET, f64s([0, 0, 0])),
    record(T_UV_PINS, new Uint8Array(0)),
  ]);
}

export function uvAttribute(front?: number[], back?: number[]): Record {
  const coords = new Record(T_UV_COORDS, undefined, [
    record(T_UV_EMPTY, new Uint8Array(0)),
    new Record(T_UV_FRONT, undefined, [side(front ?? IDENTITY_MATRIX, front !== undefined)]),
    new Record(T_UV_BACK, undefined, [side(back ?? IDENTITY_MATRIX, back !== undefined)]),
  ]);
  return coords;
}
