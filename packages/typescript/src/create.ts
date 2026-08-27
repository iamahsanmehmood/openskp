/**
 * Create new legacy-format (v17) `.skp` files from scratch.
 *
 * Ported from Python's `openskp.create` (`packages/python/src/openskp/create.py`)
 * - a genuine, from-scratch binary writer for the same MFC `CArchive`
 * object-stream format `legacy.ts` reads, built by inverting that reader's
 * own, already-proven decoding logic (the class-ref/back-ref protocol,
 * entity preambles, drawbase records). No SketchUp SDK is called at
 * runtime; this module never links against or shells out to any
 * proprietary library. See `scaffold.ts` for the one place SDK-authored
 * bytes are involved, and how.
 *
 * **Scope (deliberately limited for this first version, matching Python's
 * own):** faces built directly from vertex coordinates, sharing vertices
 * and edges automatically wherever coordinates coincide exactly.
 * Solid-color and PNG/JPEG-textured materials, named layers, reusable
 * component definitions with multiple positioned instances, and groups
 * are all supported. A definition can nest instances (or group instances)
 * of another, already-closed definition inside its own body. A face's
 * texture can be explicitly positioned (scaled/rotated/sheared/offset,
 * independently per side) instead of the default planar projection, on a
 * face of any orientation. Component definitions, instances, and faces
 * can carry custom key/value metadata (the same mechanism SketchUp's own
 * "dynamic component" attributes use). Circular faces (`addCircle`) and
 * partial arcs (`addArc`) are genuine `CArcCurve` entities, and freeform
 * polylines (`addPolyline`) are genuine `CCurve` entities. Faces support
 * one or more holes. `autoTriangulate` fan-splits a non-planar polygon
 * into real, always-planar triangles instead of throwing.
 *
 * Coordinates are in **inches** - SketchUp's own native internal unit for
 * this era of the format. Every file opens to the standard "Iso" view.
 *
 * **The blank scaffold.** See `scaffold.ts`'s own docstring for the full
 * explanation of why new files are built by splicing genuinely-written
 * geometry into a bundled minimal empty-document template, and where that
 * template's bytes came from (disclosed plainly, exactly as Python's own
 * module docstring discloses it).
 */
import {
  loadScaffold,
  MATERIAL_INSERT_POS,
  BASE,
  LAYER_COUNT_POS,
  ORIG_LAYER_COUNT,
  LAYER_INSERT_POS,
  DEF_COUNT_POS,
  ORIG_DEF_COUNT,
  ROOT_COUNT_POS,
  ORIG_ROOT_COUNT,
  TAIL_POS,
  SCAFFOLD_NEXT_SLOT,
  LAYER_WRITER_BASE,
  SCAFFOLD_CLASS_SLOT,
} from './scaffold';

// This package has no hard dependency on @types/node (it targets the
// browser too), so - like index.ts's own SkpFile.open - Node-only globals
// used only by SkpBuilder.save() are declared `any` here rather than
// pulled in via @types/node.
declare const process: any;
declare const require: any;
declare const Buffer: any;

export type Point3 = [number, number, number];
/** Row-major 3x3 matrix, 9 values: [m00,m01,m02, m10,m11,m12, m20,m21,m22]. */
export type Matrix3x3 = [number, number, number, number, number, number, number, number, number];
/** A (world point, (u, v)) correspondence for explicit texture positioning
 * - see `addFace`'s `frontUv`/`backUv` options. */
export type UvPair = [Point3, [number, number]];
/** An alternative to a hand-derived `matrix3x3` for the common case of a
 * pure rotation - see `_rotationMatrix3x3`. */
export interface Rotation {
  axis: Point3;
  angleRadians: number;
}
/** Custom key/value metadata value - the same mechanism SketchUp's own
 * "dynamic component" attributes use. A whole-number value within signed
 * 32-bit range is stored as a compact int32; any other number (including
 * a large integer, matching what Python's writer would reject as an
 * out-of-range `int`) is stored as a float64. TypeScript has no
 * runtime-visible int/float distinction the way Python does, so unlike
 * Python's writer (which raises for an out-of-range `int` rather than
 * silently widening it) this widens instead - a deliberate, documented
 * judgment call. */
export type AttributeValue = string | number;
export type AttributeDict = Record<string, AttributeValue>;

/** Raised when a `.skp` file cannot be constructed. */
export class SkpWriteError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'SkpWriteError';
    Object.setPrototypeOf(this, SkpWriteError.prototype);
  }
}

// ---------------------------------------------------------------------
// Ground-truth constants - see create.py for how each was derived
// (diffing real SDK-authored files). Values copied verbatim; only the
// encoding is translated.
// ---------------------------------------------------------------------

function hexToBytes(hex: string): number[] {
  const out: number[] = [];
  for (let i = 0; i < hex.length; i += 2) out.push(parseInt(hex.slice(i, i + 2), 16));
  return out;
}

/** Offsets (relative to the start of the document "tail") of internal
 * references that must be renumbered by the same amount as the number of
 * new archive slots inserted before them. */
const TAIL_REF_POSITIONS = [409, 468, 477, 479, 1383, 1385];

/** The blank scaffold ships with SketchUp's own arbitrary default camera;
 * every file this writer produces is instead patched to the standard
 * "Iso" view (eye along the (1, -1, 1) octant, up = Z, parallel
 * projection). */
const ISO_CAMERA_PREFIX_OFFSET = 2993;
const ISO_CAMERA_PREFIX_PATCH = hexToBytes(
  '594000000000000059c000000000000059400000000000000000000000000000' +
    '000000000000000000003f2c0c70bd20dabf3f2c0c70bd20da3f3f2c0c70bd20' +
    'ea3f000000000000f03f0000000000408f40000000000000003e402adf272c80' +
    '3457'
);
const ISO_CAMERA_TAIL_PATCHES: Array<[number, number[]]> = [
  [509, hexToBytes('d0a869613c442d4799a4667d1adfa836')],
  [1390, hexToBytes('4e53c84477029246bba95827bba7e2')],
];

/** Offset (relative to the layer insertion point) of the active-layer
 * anchor - a back-reference to the model's first layer (Layer0). */
const ACTIVE_LAYER_ANCHOR_REL = 0;

/** Absolute offset of a u16 "next available pid" counter that lives
 * before the material insertion point. Increments by exactly the
 * material COUNT (one pid consumed per material object). */
const PID_COUNTER_POS = 1987;

const MATERIAL_SCHEMA = 12;
const DIB_SCHEMA = 3;

/** Ground-truth byte pattern (not a meaningful float) real SketchUp
 * writes for a texture's "applied height" when unscaled - present
 * verbatim since its bit pattern doesn't correspond to any sensible
 * height value (~1.29e-231 as an f64). */
const TEXTURE_H_SENTINEL = hexToBytes('f0ffffffffffff0f');

const DEFINITION_SCHEMA = 11;
const INSTANCE_SCHEMA = 6;
const GROUP_SCHEMA = 1;
// UNVERIFIED - unlike every other schema constant here, not calibrated
// against a real SketchUp-authored file: no sample containing a CImage
// entity (File > Import > Image) was available (ported from the Python
// writer, same caveat there - see create.py's own comment). legacy.ts's
// image reader never branches on schema the way instance/group reading
// does, so this project's own reader round-trips correctly regardless of
// the exact value - this only affects whether real SketchUp accepts the
// file. Chosen to match INSTANCE_SCHEMA for the same reason as Python's
// _IMAGE_SCHEMA: CImage's read path always expects the trailing GUID
// unconditionally, the same "always present" shape CComponentInstance has.
const IMAGE_SCHEMA = 6;
const THUMBNAIL_SCHEMA = 1;
const LAYER_SCHEMA = 3;
const FTC_SCHEMA = 4;
const ARCCURVE_SCHEMA = 3;
const CCURVE_SCHEMA = 4;

/** CCamera's class is declared inside the scaffold's own prefix - ground
 * truth confirmed fixed at slot 7 for this exact bundled scaffold. */
const CCAMERA_SLOT = 7;
/** CAttributeContainer's class is declared in the scaffold's own prefix,
 * ground truth confirmed fixed at slot 3. */
const ATTR_CONTAINER_SLOT = 3;
/** CAttributeNamed is also pre-declared in the scaffold's own prefix,
 * ground truth confirmed fixed at slot 5. */
const ATTRIBUTE_NAMED_SLOT = 5;

const ATTR_TYPE_INT32 = 0x04;
const ATTR_TYPE_DOUBLE = 0x06;
const ATTR_TYPE_STRING = 0x0a;

/** The 176 bytes (everything after CCamera's 2-byte class-ref tag) real
 * SketchUp writes for a definition's default thumbnail camera - copied
 * verbatim. */
const CAMERA_TEMPLATE = hexToBytes(
  '00000000000000000000000000000000000000000000f03f0000000000000000' +
    '00000000000000000000000000000000004000000000000000000000000000f0' +
    '3f0000000000000000000000000000000000000000000000000100000000003e' +
    '40000000000000f03f0000000000000000000000000000000000000000000000' +
    '0000000000000000000100fffeff00000000000000000000000000000000f03f' +
    '00000000000000000000000000000000'
);

/** The definition record's 22-byte "base block" - all zero except
 * offsets 3-4, matching the same 1,1 padding convention `drawbase`
 * already requires. */
const DEFINITION_BASE_BLOCK = [0, 0, 0, 1, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0];

/** A face with no explicit texture positioning stores no
 * CFaceTextureCoords at all; this identity fills the *other* side's slot
 * when only one of front/back is explicitly positioned. */
const IDENTITY_UV_MATRIX: readonly number[] = [1, 0, 0, 0, 1, 0, 0, 0, 1];

// ---------------------------------------------------------------------
// Small math helpers - direct ports of create.py's module-level functions.
// ---------------------------------------------------------------------

function det3(m: number[][]): number {
  return (
    m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1]) -
    m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0]) +
    m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0])
  );
}

function solve3x3(a: number[][], b: number[]): [number, number, number] {
  const d = det3(a);
  if (Math.abs(d) < 1e-9) {
    throw new SkpWriteError(
      'the 3 texture-positioning points map to collinear (u, v) coordinates - ' +
        'cannot determine a texture mapping from them'
    );
  }
  const cols: number[] = [];
  for (let col = 0; col < 3; col++) {
    const ai = a.map((row) => row.slice());
    for (let r = 0; r < 3; r++) ai[r][col] = b[r];
    cols.push(det3(ai) / d);
  }
  return [cols[0], cols[1], cols[2]];
}

function cross(a: Point3, b: Point3): Point3 {
  return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
}

function normalize3(v: Point3): Point3 {
  const length = Math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2]);
  if (length < 1e-9) {
    throw new SkpWriteError("cannot determine a texture-positioning basis: the face's first edge is degenerate");
  }
  return [v[0] / length, v[1] / length, v[2] / length];
}

/** The row-major 3x3 rotation matrix for rotating by `angleRadians`
 * (right-hand rule) around `axis` (need not be a unit vector) -
 * Rodrigues' rotation formula. */
function rotationMatrix3x3(axis: Point3, angleRadians: number): Matrix3x3 {
  const length = Math.sqrt(axis[0] ** 2 + axis[1] ** 2 + axis[2] ** 2);
  if (length < 1e-9) throw new SkpWriteError('rotation axis must not be the zero vector');
  const x = axis[0] / length;
  const y = axis[1] / length;
  const z = axis[2] / length;
  const c = Math.cos(angleRadians);
  const s = Math.sin(angleRadians);
  const t = 1.0 - c;
  return [
    t * x * x + c, t * x * y - s * z, t * x * z + s * y,
    t * x * y + s * z, t * y * y + c, t * y * z - s * x,
    t * x * z - s * y, t * y * z + s * x, t * z * z + c,
  ];
}

/** Shared by every addInstance/addGroup/addGroupInstance call -
 * `matrix3x3` and `rotation` are alternate ways to specify the same
 * underlying transform field, not two separate ones. */
function resolveMatrix3x3(matrix3x3?: Matrix3x3, rotation?: Rotation): Matrix3x3 | undefined {
  if (matrix3x3 !== undefined && rotation !== undefined) {
    throw new SkpWriteError('pass at most one of matrix3x3/rotation - rotation is just a convenience for matrix3x3');
  }
  if (rotation !== undefined) return rotationMatrix3x3(rotation.axis, rotation.angleRadians);
  return matrix3x3;
}

/** The in-plane 2D basis (U, W) real SketchUp uses to parameterize a
 * face's texture mapping: the face's own first edge direction
 * (points[1] - points[0], normalized) as U, and the plane normal crossed
 * with that as W. */
function faceUvBasis(points: readonly Point3[], normal: Point3): [Point3, Point3] {
  const u = normalize3([points[1][0] - points[0][0], points[1][1] - points[0][1], points[1][2] - points[0][2]]);
  const w = normalize3(cross(normal, u));
  return [u, w];
}

/** An arbitrary orthonormal in-plane basis (U, W) for a circle/arc's
 * plane, given only its normal. */
function circleBasis(normal: Point3): [Point3, Point3] {
  const seed: Point3 = Math.abs(normal[2]) < 0.9 ? [0, 0, 1] : [1, 0, 0];
  const dot = seed[0] * normal[0] + seed[1] * normal[1] + seed[2] * normal[2];
  const uRaw: Point3 = [seed[0] - dot * normal[0], seed[1] - dot * normal[1], seed[2] - dot * normal[2]];
  const u = normalize3(uRaw);
  const w = normalize3(cross(normal, u));
  return [u, w];
}

function circlePoints(center: Point3, radius: number, numSegments: number, u: Point3, w: Point3): Point3[] {
  const pts: Point3[] = [];
  for (let i = 0; i < numSegments; i++) {
    const angle = (2.0 * Math.PI * i) / numSegments;
    const c = Math.cos(angle);
    const s = Math.sin(angle);
    pts.push([
      center[0] + radius * (c * u[0] + s * w[0]),
      center[1] + radius * (c * u[1] + s * w[1]),
      center[2] + radius * (c * u[2] + s * w[2]),
    ]);
  }
  return pts;
}

/** The `numSegments + 1` points (both endpoints included) tracing a
 * PARTIAL arc from `startAngle` to `endAngle`. */
function arcPoints(
  center: Point3, radius: number, numSegments: number, u: Point3, w: Point3,
  startAngle: number, endAngle: number
): Point3[] {
  const pts: Point3[] = [];
  for (let i = 0; i <= numSegments; i++) {
    const angle = startAngle + ((endAngle - startAngle) * i) / numSegments;
    const c = Math.cos(angle);
    const s = Math.sin(angle);
    pts.push([
      center[0] + radius * (c * u[0] + s * w[0]),
      center[1] + radius * (c * u[1] + s * w[1]),
      center[2] + radius * (c * u[2] + s * w[2]),
    ]);
  }
  return pts;
}

/** Fit the 3x3 UV-to-world affine matrix real SketchUp stores for a
 * positioned texture, from exactly 3 (world point, (u, v))
 * correspondences. */
function solveUvMatrix(pairs: readonly UvPair[], basis: [Point3, Point3]): number[] {
  if (pairs.length !== 3) throw new SkpWriteError('texture positioning needs exactly 3 (point, uv) pairs');
  const [uAxis, wAxis] = basis;
  const a = pairs.map(([, uv]) => [uv[0], uv[1], 1.0]);
  const bx = pairs.map(([pt]) => pt[0] * uAxis[0] + pt[1] * uAxis[1] + pt[2] * uAxis[2]);
  const by = pairs.map(([pt]) => pt[0] * wAxis[0] + pt[1] * wAxis[1] + pt[2] * wAxis[2]);
  const [a0, c0, e0] = solve3x3(a, bx);
  const [b0, d0, f0] = solve3x3(a, by);
  return [a0, b0, 0.0, c0, d0, 0.0, e0, f0, 1.0];
}

function uvMatrixForFace(points: readonly Point3[], pairs: readonly UvPair[], normal: Point3): number[] {
  return solveUvMatrix(pairs, faceUvBasis(points, normal));
}

// ---------------------------------------------------------------------
// Byte-level helpers.
// ---------------------------------------------------------------------

/** Append every element of `src` onto `dest`, one at a time - NOT
 * `dest.push(...src)`. The spread form blows the JS engine's call-argument
 * limit (~100k-ish, engine-dependent) once `src` is a large geometry
 * buffer - a real bug found while stress-testing the 0x7FFF slot-boundary
 * fix at real scale (thousands of faces): `toBytes()` splices several
 * multi-hundred-KB buffers together, all of which go through this. */
function appendAll(dest: number[], src: ArrayLike<number>): void {
  for (let i = 0; i < src.length; i++) dest.push(src[i]);
}

function f64Bytes(v: number): number[] {
  const b = new Uint8Array(8);
  new DataView(b.buffer).setFloat64(0, v, true);
  return Array.from(b);
}

function u32Bytes(v: number): number[] {
  return [v & 0xff, (v >>> 8) & 0xff, (v >>> 16) & 0xff, (v >>> 24) & 0xff];
}

function readU16(buf: number[], pos: number): number {
  return (buf[pos] | (buf[pos + 1] << 8)) & 0xffff;
}

function writeU16At(buf: number[], pos: number, v: number): void {
  buf[pos] = v & 0xff;
  buf[pos + 1] = (v >> 8) & 0xff;
}

function writeU32At(buf: number[], pos: number, v: number): void {
  const b = u32Bytes(v);
  buf[pos] = b[0];
  buf[pos + 1] = b[1];
  buf[pos + 2] = b[2];
  buf[pos + 3] = b[3];
}

/** Renumber the u16 archive slot-reference at `pos` by `shift`,
 * preserving the 0x8000 class-ref tag bit if the reference carries one.
 * Widens to the 6-byte escape form (same encoding
 * `newOfKnownClass`/`writeBackref` use, same `< 0x7FFF` boundary) if the
 * shifted slot would land at or past 0x7FFF - masking it back into 15
 * bits instead of widening would silently renumber it to the wrong slot,
 * corrupting whatever it points to. Returns the number of bytes the
 * field grew by (0 or 4). */
function shiftRef(buf: number[], pos: number, shift: number): number {
  const u16 = readU16(buf, pos);
  const tagBit = u16 & 0x8000;
  const slot = u16 & 0x7fff;
  const newSlot = slot + shift;
  if (newSlot < 0x7fff) {
    writeU16At(buf, pos, tagBit | newSlot);
    return 0;
  }
  const val = tagBit ? (0x80000000 | newSlot) >>> 0 : newSlot;
  buf.splice(pos, 2, 0xff, 0x7f, ...u32Bytes(val));
  return 4;
}

function randomGuidBytes(): number[] {
  const bytes = new Uint8Array(16);
  if (typeof crypto !== 'undefined' && typeof crypto.getRandomValues === 'function') {
    crypto.getRandomValues(bytes);
  } else {
    for (let i = 0; i < 16; i++) bytes[i] = Math.floor(Math.random() * 256);
  }
  bytes[6] = (bytes[6] & 0x0f) | 0x40; // version 4
  bytes[8] = (bytes[8] & 0x3f) | 0x80; // variant
  return Array.from(bytes);
}

/** CDib's format tag for the two image formats this project has
 * confirmed via SDK ground truth - PNG and JPEG, both real SketchUp
 * encodes as the source file's bytes verbatim, distinguished only by
 * this tag. */
function detectImageSubtype(imageBytes: Uint8Array): number {
  if (
    imageBytes.length >= 8 &&
    imageBytes[0] === 0x89 && imageBytes[1] === 0x50 && imageBytes[2] === 0x4e && imageBytes[3] === 0x47 &&
    imageBytes[4] === 0x0d && imageBytes[5] === 0x0a && imageBytes[6] === 0x1a && imageBytes[7] === 0x0a
  ) {
    return 4;
  }
  if (imageBytes.length >= 3 && imageBytes[0] === 0xff && imageBytes[1] === 0xd8 && imageBytes[2] === 0xff) {
    return 1;
  }
  throw new SkpWriteError(
    'unrecognized image format - only PNG and JPEG textures are supported for now ' +
      "(detected from the file's own magic bytes, not its extension)"
  );
}

function vertexKey(p: Point3): string {
  return `${p[0]}|${p[1]}|${p[2]}`;
}

function edgeKey(a: number, b: number): string {
  return a < b ? `${a}_${b}` : `${b}_${a}`;
}

interface CurveParams {
  center: Point3;
  normal: Point3;
  xaxis: Point3;
  startAngle: number;
  endAngle: number;
  radius: number;
  numSegments: number;
}

/** Write-side mirror of legacy.ts's archive slot/class-ref bookkeeping -
 * emits the same MFC CArchive tag protocol (0xFFFF new-class,
 * 0x8000|slot short class-ref, plain u16 back-ref) that legacy.ts
 * decodes, inverted for writing. */
class ArchiveWriter {
  nextSlot: number;
  classSlot: Record<string, number>;
  nextPid: number;
  bytes: number[] = [];

  constructor(nextSlot: number, classSlot: Record<string, number>, nextPid = 1) {
    this.nextSlot = nextSlot;
    this.classSlot = { ...classSlot };
    this.nextPid = nextPid;
  }

  get length(): number {
    return this.bytes.length;
  }

  private alloc(): number {
    const s = this.nextSlot;
    this.nextSlot += 1;
    return s;
  }

  private allocPid(): number {
    const p = this.nextPid;
    this.nextPid += 1;
    return p;
  }

  private pushU8(v: number): void {
    this.bytes.push(v & 0xff);
  }

  private pushU16(v: number): void {
    this.bytes.push(v & 0xff, (v >> 8) & 0xff);
  }

  private pushU32(v: number): void {
    this.bytes.push(...u32Bytes(v));
  }

  private pushI32(v: number): void {
    this.pushU32(v);
  }

  private pushF64(v: number): void {
    this.bytes.push(...f64Bytes(v));
  }

  private pushBytes(arr: ArrayLike<number>): void {
    for (let i = 0; i < arr.length; i++) this.bytes.push(arr[i]);
  }

  private pushZeros(n: number): void {
    for (let i = 0; i < n; i++) this.bytes.push(0);
  }

  private patchU32(pos: number, v: number): void {
    writeU32At(this.bytes, pos, v);
  }

  newOfKnownClass(className: string, schema?: number): number {
    if (!(className in this.classSlot)) {
      if (schema === undefined) throw new SkpWriteError(`${className} not yet declared and no schema given`);
      this.pushU16(0xffff);
      this.pushU16(schema);
      this.pushU16(className.length);
      for (let i = 0; i < className.length; i++) this.bytes.push(className.charCodeAt(i) & 0xff);
      this.classSlot[className] = this.alloc();
      return this.alloc();
    }
    const slot = this.classSlot[className];
    // slot === 0x7FFF is deliberately excluded from the short form even
    // though it numerically fits in 15 bits: 0x8000 | 0x7FFF === 0xFFFF,
    // which legacy.ts's reader checks for "new class declaration" BEFORE
    // it ever checks the class-ref high bit - a class landing at exactly
    // that slot would be silently misinterpreted as the start of a bogus
    // class record, desyncing every read after it. The escape form has
    // no such collision.
    if (slot < 0x7fff) {
      this.pushU16(0x8000 | slot);
    } else {
      this.pushU16(0x7fff);
      this.pushU32(0x80000000 | slot);
    }
    return this.alloc();
  }

  private writeNull(): void {
    this.pushU16(0);
  }

  writeBackref(slot: number): void {
    // Same exclusion as newOfKnownClass, for the plain (no class-ref bit)
    // case: a bare slot value of 0x7FFF is indistinguishable from the
    // big-tag escape marker itself.
    if (slot < 0x7fff) {
      this.pushU16(slot);
    } else {
      this.pushU16(0x7fff);
      this.pushU32(slot);
    }
  }

  private encodePid(pid: number): number[] {
    let mask = 0;
    const pidBytes: number[] = [];
    let p = pid;
    for (let bit = 0; bit < 8; bit++) {
      const byteVal = p % 256;
      p = Math.floor(p / 256);
      if (byteVal) {
        mask |= 1 << bit;
        pidBytes.push(byteVal);
      }
    }
    return [mask, ...pidBytes];
  }

  preamble(pid?: number, realAttrs = false): void {
    if (realAttrs) {
      // Ground truth: CComponentDefinition and CComponentInstance both
      // reference a real (but childless) CAttributeContainer here instead
      // of the null pointer every other entity uses.
      this.pushU16(0x8000 | ATTR_CONTAINER_SLOT);
      this.alloc(); // a class-ref always allocates a new object slot, even a bookkeeping-only one
      this.pushZeros(3); // the container's own nested preamble: null attrs (2) + mask=0 (1)
      this.pushU16(0); // empty children-list terminator
    } else {
      this.writeNull(); // no CAttributeContainer
    }
    const realPid = pid === undefined ? this.allocPid() : pid;
    this.pushBytes(this.encodePid(realPid));
  }

  preambleWithRealAttrs(
    frontMatrix?: readonly number[],
    backMatrix?: readonly number[],
    attributeDicts: ReadonlyArray<[string, AttributeDict]> = [],
    pid?: number
  ): void {
    this.pushU16(0x8000 | ATTR_CONTAINER_SLOT);
    this.alloc();
    this.pushZeros(3);
    if (frontMatrix !== undefined || backMatrix !== undefined) {
      this.writeFaceTextureCoords(frontMatrix, backMatrix);
    }
    for (const [dictName, entries] of attributeDicts) {
      this.writeAttributeDict(dictName, entries);
    }
    this.writeNull(); // children-list terminator
    const realPid = pid === undefined ? this.allocPid() : pid;
    this.pushBytes(this.encodePid(realPid));
  }

  /** Shares writeAttributeDict's own exact validation rules so a caller
   * can check every attribute dict a multi-part write will need BEFORE
   * that write starts mutating this.bytes. */
  validateAttributeEntries(entries: AttributeDict): void {
    for (const [key, value] of Object.entries(entries)) {
      if (typeof value === 'boolean') {
        throw new SkpWriteError(`attribute ${JSON.stringify(key)}: bool is not a supported value type - use 0/1 instead`);
      }
      if (typeof value !== 'string' && typeof value !== 'number') {
        throw new SkpWriteError(`attribute ${JSON.stringify(key)}: unsupported value type (only str and number are supported)`);
      }
    }
  }

  writeAttributeDict(dictName: string, entries: AttributeDict): void {
    // Unlike every other class this project declares, CAttributeNamed is
    // already pre-declared in the scaffold's own prefix, so this always
    // writes a short class-ref, never a fresh 0xFFFF declaration.
    this.pushU16(0x8000 | ATTRIBUTE_NAMED_SLOT);
    this.alloc();
    this.pushZeros(3); // this dict's own preamble: null attrs (2) + mask=0 (1), pid=0
    this.pushU32(0); // ground truth: read and discarded by legacy.ts's reader too
    this.validateAttributeEntries(entries);
    this.writeStr(dictName);
    for (const [key, value] of Object.entries(entries)) {
      this.writeStr(key);
      if (typeof value === 'string') {
        this.pushU8(ATTR_TYPE_STRING);
        this.writeStr(value);
      } else if (Number.isInteger(value) && value >= -(2 ** 31) && value < 2 ** 31) {
        this.pushU8(ATTR_TYPE_INT32);
        this.pushI32(value);
      } else {
        this.pushU8(ATTR_TYPE_DOUBLE);
        this.pushF64(value);
      }
    }
    this.writeStr(''); // empty-key terminator
    this.pushU32(0); // ground truth: read and discarded by legacy.ts's reader too
  }

  /** Write one CFaceTextureCoords record. `frontMatrix`/`backMatrix` are
   * the 9-value row-major UV-to-world affine matrices from
   * uvMatrixForFace, or undefined for a side that isn't explicitly
   * positioned (written as identity). */
  writeFaceTextureCoords(frontMatrix?: readonly number[], backMatrix?: readonly number[]): void {
    this.newOfKnownClass('CFaceTextureCoords', FTC_SCHEMA);
    this.preamble(0);
    this.pushU32(0); // ground truth: read and discarded by legacy.ts's reader too
    const ks = new Array(24).fill(0);
    const front = frontMatrix ?? IDENTITY_UV_MATRIX;
    const back = backMatrix ?? IDENTITY_UV_MATRIX;
    for (let i = 0; i < 9; i++) ks[i] = front[i];
    for (let i = 0; i < 9; i++) ks[12 + i] = back[i];
    for (const v of ks) this.pushF64(v);
    this.pushU32(0); // front pin count - this writer always emits a solved matrix, never raw pins
    this.pushU32(0); // back pin count
    this.pushU32(frontMatrix !== undefined ? 1 : 0); // fflags bit 0: front painted/positioned
    this.pushU32(backMatrix !== undefined ? 1 : 0); // bflags bit 0: back painted/positioned
  }

  private drawbase(mat = 0, layer = 0, hidden = false, soft = false, smooth = false): void {
    const b = new Array(10).fill(0);
    b[0] = mat & 0xff;
    b[1] = (mat >> 8) & 0xff;
    b[2] = hidden ? 1 : 0;
    // offsets 3-4: legacy.ts's reader documents these as unused padding,
    // but real SketchUp silently drops any entity whose drawbase has
    // them zeroed - ground-truth-confirmed. Must be 1, 1.
    b[3] = 1;
    b[4] = 1;
    b[5] = soft ? 1 : 0;
    b[6] = smooth ? 1 : 0;
    b[8] = layer & 0xff;
    b[9] = (layer >> 8) & 0xff;
    this.pushBytes(b);
  }

  private writeVertex(point: Point3): number {
    const slot = this.newOfKnownClass('CVertex', 0);
    this.preamble();
    this.pushF64(point[0]);
    this.pushF64(point[1]);
    this.pushF64(point[2]);
    return slot;
  }

  /** Write one CArcCurve record and return its slot - the shared
   * geometric-parameter object a circle/arc's straight CEdge segments
   * each carry a backref to. `xaxis` is the arc's own fixed 0-angle
   * reference direction (a unit vector times radius, in the plane
   * perpendicular to normal) - startAngle/endAngle are offsets from it. */
  writeArcCurve(p: CurveParams): number {
    if (!(p.numSegments >= 0 && p.numSegments <= 0xff)) {
      throw new SkpWriteError(`num_segments must be between 0 and 255, got ${p.numSegments}`);
    }
    const slot = this.newOfKnownClass('CArcCurve', ARCCURVE_SCHEMA);
    this.preamble();
    this.pushBytes([0, p.numSegments, 0, 0, 0]);
    const values = [...p.center, ...p.normal, ...p.xaxis, p.startAngle, p.endAngle, 0.0, p.radius, 0.0];
    for (const v of values) this.pushF64(v);
    return slot;
  }

  /** Write one CCurve record and return its slot - a freeform polyline
   * curve grouping: a labeled set of already-straight CEdge segments,
   * with no geometric data of its own beyond how many edges share it. */
  writeCurve(numEdges: number): number {
    const slot = this.newOfKnownClass('CCurve', CCURVE_SCHEMA);
    this.preamble();
    this.pushU8(1);
    this.pushU32(numEdges);
    return slot;
  }

  private writeStr(s: string): void {
    if (s.length >= 0xff) throw new SkpWriteError('string too long to encode (255 char limit)');
    this.bytes.push(0xff, 0xfe, 0xff, s.length);
    for (let i = 0; i < s.length; i++) {
      const c = s.charCodeAt(i);
      this.bytes.push(c & 0xff, (c >> 8) & 0xff);
    }
  }

  /** Write one solid-color CMaterial record and return its slot. */
  writeMaterial(name: string, rgba: readonly [number, number, number, number]): number {
    const slot = this.newOfKnownClass('CMaterial', MATERIAL_SCHEMA);
    this.preamble();
    this.writeStr(name);
    this.pushU16(0); // texflag: solid color, no texture
    this.pushBytes(rgba);
    this.writeStr(''); // texture path (empty - no texture)
    this.pushZeros(8); // unknown/padding - ground truth is all-zero here
    this.pushF64(1.0); // opacity
    this.pushU8(0); // use_opacity = False (alpha carries transparency instead)
    return slot;
  }

  /** Write one image-textured CMaterial record (embedding `imageBytes`
   * verbatim inside a CDib sub-object) and return its slot. `subtype` is
   * CDib's image format tag (4 for PNG, 1 for JPEG).
   *
   * `appliedHeight`, if given, is written in place of TEXTURE_H_SENTINEL
   * (applied width stays a fixed 1.0 either way). Needed because the
   * reader's own ground-truth-derived UV formula divides a face's final
   * UV by the material's applied width/height EVEN for a positioned
   * (`frontUv`) mapping, not just the default projection - the sentinel
   * decodes to ~1.29e-231, and dividing by it blows up to an astronomical
   * value, which real SketchUp visibly renders as a corrupted,
   * vertically-smeared texture (confirmed against real SketchUp
   * 2026-08-27 via the Python writer - see create.py's own note on this).
   * A caller positioning this material via `frontUv`/`backUv` should pass
   * a real `appliedHeight` (addImage uses 1.0, matching its own pins'
   * 0..1 range) so that division is a no-op instead of a corruption. */
  writeTexturedMaterial(
    name: string, imageBytes: Uint8Array, texturePath: string, subtype: number, appliedHeight?: number
  ): number {
    const slot = this.newOfKnownClass('CMaterial', MATERIAL_SCHEMA);
    this.preamble();
    this.writeStr(name);
    this.pushU16(1); // texflag: textured
    this.pushZeros(2); // texture-flag pad (v17+)
    this.newOfKnownClass('CDib', DIB_SCHEMA);
    this.pushU32(subtype);
    this.pushU32(imageBytes.length);
    this.pushBytes(imageBytes);
    if (subtype === 1) {
      // JPEG only: one extra u32 real SketchUp always writes here -
      // ground-truth confirmed constant 90 regardless of the source
      // JPEG's own actual encoded quality.
      this.pushU32(90);
    }
    this.pushF64(1.0); // applied width - ground truth default when unscaled
    if (appliedHeight !== undefined) {
      this.pushF64(appliedHeight);
    } else {
      this.pushBytes(TEXTURE_H_SENTINEL);
    }
    this.writeStr(texturePath);
    // avg color: neutral near-opaque white, alpha 254 not 255 - legacy.ts's
    // own reader treats alpha=255 here as one of its two "this material is
    // colorized" signals, so a PLAIN one's placeholder must not have it.
    this.pushBytes([255, 255, 255, 254, 0, 255, 255, 255, 254]);
    this.writeStr(''); // second name field - empty in ground truth
    this.pushU32(1);
    this.pushU32(0); // blob (colorize-related, ground truth: 1, 0)
    this.pushF64(1.0); // opacity
    this.pushU8(0); // use_opacity = False
    return slot;
  }

  /** Write one CLayer record and return its slot. Ground truth shows
   * each top-level layer record contains a second, embedded pid - so
   * each layer consumes 2 pids, not 1. `withPids=false` (used only for
   * the layer a component definition embeds internally) omits both. */
  writeLayer(name: string, withPids = true, hidden = false, rgba?: readonly [number, number, number, number]): number {
    const slot = this.newOfKnownClass('CLayer', LAYER_SCHEMA);
    this.preamble(withPids ? undefined : 0);
    this.writeStr(name);
    const pid2 = withPids ? this.allocPid() : 0;
    // byte 0 is the hidden flag, bytes 1-2 are always zero (ground truth)
    this.pushBytes([hidden ? 1 : 0, 0, 0]);
    this.pushBytes(this.encodePid(pid2));
    this.writeStr(`Layer_${name}`);
    this.pushU16(256); // ground truth is a constant 256 here
    this.pushBytes(rgba ?? [0, 0, 0, 0]);
    this.writeStr(''); // second name field - empty in ground truth
    this.pushZeros(8);
    this.pushF64(0.5); // opacity-like f64
    this.pushZeros(5);
    return slot;
  }

  /** Write a CThumbnail with a default camera and no image - ground
   * truth shows the image itself is optional. */
  writeThumbnail(): void {
    this.newOfKnownClass('CThumbnail', THUMBNAIL_SCHEMA);
    this.preamble(0); // structural container: ground truth carries no pid
    this.pushU16(0x8000 | CCAMERA_SLOT);
    this.alloc();
    this.pushBytes(CAMERA_TEMPLATE);
    this.writeNull(); // no thumbnail image
  }

  /** Begin a CComponentDefinition record - everything up to (not
   * including) its internal entity list. Returns [definitionSlot,
   * countPatchPos]. */
  writeDefinitionHeader(attributeDicts: ReadonlyArray<[string, AttributeDict]> = []): [number, number] {
    const slot = this.newOfKnownClass('CComponentDefinition', DEFINITION_SCHEMA);
    if (attributeDicts.length > 0) {
      this.preambleWithRealAttrs(undefined, undefined, attributeDicts);
    } else {
      this.preamble(undefined, true); // ground truth: a real pid and a real (empty) attr container
    }
    this.pushBytes(DEFINITION_BASE_BLOCK);
    this.pushU32(1); // nlayers: always 1, an embedded copy of Layer0
    const embeddedLayerSlot = this.writeLayer('Layer0', false);
    this.writeBackref(embeddedLayerSlot); // "decl": this definition's own active layer
    // A separate field from nested instances - ground truth shows this
    // counts CComponentDefinition classes declared inline within this
    // definition's own header; every definition this writer produces is
    // declared at the top level, so this stays 0.
    this.pushU32(0);
    const countPatchPos = this.length;
    this.pushU32(0); // placeholder entity count, patched by the caller
    return [slot, countPatchPos];
  }

  /** Close out a CComponentDefinition record: relationship count, GUID,
   * name, timestamp, behavior flags, and a default thumbnail. */
  writeDefinitionTail(name: string): void {
    this.pushU32(0); // nrel: CRelationship count - always 0, not supported
    this.pushU16(0);
    this.pushBytes(randomGuidBytes());
    this.writeStr(name);
    this.writeStr(''); // description - empty in ground truth
    this.writeStr(''); // second name field - empty in ground truth
    this.pushU32(Math.floor(Date.now() / 1000));
    // 43-byte gap; byte -9 carries the always-faces-camera/shadows-face-sun
    // behavior flags - both left off, matching neither being exposed yet.
    this.pushZeros(43);
    this.writeThumbnail();
  }

  private writeInstanceLike(
    className: string, schema: number, realAttrs: boolean,
    definitionSlot: number, name: string, translation: Point3, matrix3x3: Matrix3x3 | undefined,
    mat: number, layer: number,
    attributeDicts: ReadonlyArray<[string, AttributeDict]> = [], hidden = false
  ): void {
    this.newOfKnownClass(className, schema);
    if (realAttrs && attributeDicts.length > 0) {
      this.preambleWithRealAttrs(undefined, undefined, attributeDicts);
    } else {
      this.preamble(undefined, realAttrs);
    }
    this.drawbase(mat, layer, hidden);
    this.writeBackref(definitionSlot);
    const m = matrix3x3 ?? [1, 0, 0, 0, 1, 0, 0, 0, 1];
    for (const v of [...m, ...translation, 1.0]) this.pushF64(v);
    this.writeStr(name);
    this.pushBytes(randomGuidBytes());
  }

  /** Write one CComponentInstance placing a copy of `definitionSlot` and
   * return how many new root-entity-list slots it consumed (always 1). */
  writeInstance(
    definitionSlot: number, name: string, translation: Point3 = [0, 0, 0], matrix3x3?: Matrix3x3,
    instanceMaterial = 0, instanceLayer = 0,
    attributeDicts: ReadonlyArray<[string, AttributeDict]> = [], hidden = false
  ): number {
    // ground truth: instances also carry a real (empty) attr container, unlike CGroup
    this.writeInstanceLike(
      'CComponentInstance', INSTANCE_SCHEMA, true,
      definitionSlot, name, translation, matrix3x3, instanceMaterial, instanceLayer,
      attributeDicts, hidden
    );
    return 1;
  }

  /** Write one CGroup placing a copy of `definitionSlot` - structurally
   * almost identical to writeInstance; the real differences are its
   * class name/schema and that it uses a plain null attribute pointer
   * rather than the real (empty) CAttributeContainer instances need. */
  writeGroup(
    definitionSlot: number, name: string, translation: Point3 = [0, 0, 0], matrix3x3?: Matrix3x3,
    groupMaterial = 0, groupLayer = 0, hidden = false
  ): number {
    this.writeInstanceLike(
      'CGroup', GROUP_SCHEMA, false,
      definitionSlot, name, translation, matrix3x3, groupMaterial, groupLayer, [], hidden
    );
    return 1;
  }

  /** Write one CImage placing `definitionSlot` (the quad + texture
   * material `addImage` built for it) - return contract matches
   * writeInstance/writeGroup (always 1).
   *
   * legacy.ts's image reader treats CImage as "instance-shaped": preamble,
   * drawbase, a definition back-ref, a 3x4 placement, a constant 1.0, a
   * source-path string, and a 16-byte GUID - field-for-field identical in
   * count and order to writeInstance's own
   * matrix3x3(9)+translation(3)+1.0(1)=13 f64s, name string, GUID. The
   * source-path string is always empty - ground truth shows real SketchUp
   * writes it empty too. No material argument - an Image entity isn't
   * painted a material the way a face or instance can be; its appearance
   * comes entirely from the definition's own textured face. */
  writeImage(
    definitionSlot: number, translation: Point3 = [0, 0, 0], matrix3x3?: Matrix3x3,
    imageLayer = 0, hidden = false
  ): number {
    this.writeInstanceLike(
      'CImage', IMAGE_SCHEMA, false,
      definitionSlot, '', translation, matrix3x3, 0, imageLayer, [], hidden
    );
    return 1;
  }

  /** Write a chain of straight CEdge records connecting `points` in
   * order, sharing vertices/edges via `vertexSlots`/`edgeRegistry`.
   * `closed=true` also connects the last point back to the first.
   * Returns [edgeSlots, edgeSenses, newEntities]. At most one of
   * `curveParams`/`polylineNumEdges` should be given - ground truth
   * shows the shared curve object is declared inline as the FIRST
   * newly-declared edge's own "curve" field. */
  private writeEdgeChain(
    points: readonly Point3[],
    vertexSlots: Map<string, number>,
    edgeRegistry: Map<string, [number, number]>,
    closed: boolean,
    hiddenEdges = false, softEdges = false, smoothEdges = false,
    curveParams?: CurveParams,
    polylineNumEdges?: number
  ): [number[], number[], number] {
    const n = points.length;
    const pairCount = closed ? n : n - 1;
    const pointSlots: Array<number | undefined> = points.map((p) => vertexSlots.get(vertexKey(p)));
    const edgeSlots: number[] = [];
    const edgeSenses: number[] = [];
    let newEntities = 0;
    let curveSlot: number | undefined;

    for (let i = 0; i < pairCount; i++) {
      const v1Idx = i;
      const v2Idx = (i + 1) % n;
      const v1Known = pointSlots[v1Idx];
      const v2Known = pointSlots[v2Idx];
      const key = v1Known !== undefined && v2Known !== undefined ? edgeKey(v1Known, v2Known) : undefined;
      if (key !== undefined && edgeRegistry.has(key)) {
        const [edgeSlot, fwdV1] = edgeRegistry.get(key)!;
        edgeSlots.push(edgeSlot);
        edgeSenses.push(fwdV1 === v1Known ? 0 : 1);
        continue;
      }

      const edgeSlot = this.newOfKnownClass('CEdge', 2);
      this.preamble();
      this.drawbase(0, 0, hiddenEdges, softEdges, smoothEdges);
      for (const idx of [v1Idx, v2Idx]) {
        if (pointSlots[idx] === undefined) {
          const s = this.writeVertex(points[idx]);
          pointSlots[idx] = s;
          vertexSlots.set(vertexKey(points[idx]), s);
        } else {
          this.writeBackref(pointSlots[idx] as number);
        }
      }
      if (curveSlot !== undefined) {
        this.writeBackref(curveSlot);
      } else if (curveParams !== undefined) {
        curveSlot = this.writeArcCurve(curveParams);
      } else if (polylineNumEdges !== undefined) {
        curveSlot = this.writeCurve(polylineNumEdges);
      } else {
        this.writeNull(); // curve = None
      }
      edgeSlots.push(edgeSlot);
      edgeSenses.push(0);
      newEntities += 1;
      edgeRegistry.set(edgeKey(pointSlots[v1Idx] as number, pointSlots[v2Idx] as number), [
        edgeSlot,
        pointSlots[v1Idx] as number,
      ]);
    }

    return [edgeSlots, edgeSenses, newEntities];
  }

  /** Write a partial (open) arc as a chain of straight CEdge records - no
   * face. Returns how many new root-entity-list slots were consumed. */
  writeArc(
    points: readonly Point3[], vertexSlots: Map<string, number>, edgeRegistry: Map<string, [number, number]>,
    curveParams: CurveParams, hiddenEdges = false, softEdges = false, smoothEdges = false
  ): number {
    const [, , newEntities] = this.writeEdgeChain(
      points, vertexSlots, edgeRegistry, false, hiddenEdges, softEdges, smoothEdges, curveParams
    );
    return newEntities;
  }

  /** Write a freeform polyline curve - a chain of straight CEdge records
   * connecting `points` in order, all sharing one CCurve grouping, no
   * face. Returns how many new root-entity-list slots were consumed. */
  writePolyline(
    points: readonly Point3[], vertexSlots: Map<string, number>, edgeRegistry: Map<string, [number, number]>,
    closed = false, hiddenEdges = false, softEdges = false, smoothEdges = false
  ): number {
    const n = points.length;
    const pairCount = closed ? n : n - 1;
    const [, , newEntities] = this.writeEdgeChain(
      points, vertexSlots, edgeRegistry, closed, hiddenEdges, softEdges, smoothEdges, undefined, pairCount
    );
    return newEntities;
  }

  /** Write one planar face and return how many new root-entity-list
   * slots it consumed (edges newly declared, plus the face itself).
   * `points` form a closed polygon in order (do not repeat the first
   * point). `holes`, if given, cuts out independent closed polygons -
   * ground truth (an SDK-authored window-in-a-wall face) shows a hole is
   * just another CLoop, distinguished only by its first flag byte (0
   * instead of 1). */
  writeFace(
    points: readonly Point3[],
    vertexSlots: Map<string, number>,
    edgeRegistry: Map<string, [number, number]>,
    faceMaterial = 0, faceLayer = 0, backMaterial = 0,
    hidden = false, softEdges = false, smoothEdges = false, hiddenEdges = false,
    frontUv?: readonly UvPair[], backUv?: readonly UvPair[],
    attributeDicts: ReadonlyArray<[string, AttributeDict]> = [],
    curveParams?: CurveParams,
    holes: ReadonlyArray<readonly Point3[]> = []
  ): number {
    // Validate everything that CAN fail before writing a single byte or
    // touching vertexSlots/edgeRegistry - writeEdgeChain mutates both
    // this writer's own buffer AND those caller-owned, shared-across-calls
    // maps as it goes, with no rollback if something later here throws.
    const [nx, ny, nz, d] = planeFromPolygon(points);
    const frontMatrix = frontUv !== undefined ? uvMatrixForFace(points, frontUv, [nx, ny, nz]) : undefined;
    const backMatrix = backUv !== undefined ? uvMatrixForFace(points, backUv, [nx, ny, nz]) : undefined;
    for (const [, entries] of attributeDicts) this.validateAttributeEntries(entries);
    const span = Math.max(
      ...[0, 1, 2].map((i) => Math.max(...points.map((p) => p[i])) - Math.min(...points.map((p) => p[i])))
    );
    const tol = Math.max(span, 1.0) * 1e-6;
    for (const hole of holes) {
      if (hole.length < 3) throw new SkpWriteError('a hole needs at least 3 points');
      for (const p of hole) {
        const dist = nx * p[0] + ny * p[1] + nz * p[2] - d;
        if (Math.abs(dist) > tol) {
          throw new SkpWriteError(
            `hole point ${JSON.stringify(p)} is ${Math.abs(dist)} units off the face's own plane - ` +
              'a hole must lie on the same plane as the outer boundary'
          );
        }
      }
    }

    const [edgeSlots, edgeSenses, edgeNewEntities] = this.writeEdgeChain(
      points, vertexSlots, edgeRegistry, true, hiddenEdges, softEdges, smoothEdges, curveParams
    );
    let newEntities = edgeNewEntities;
    const holeLoops: Array<[number[], number[]]> = [];
    for (const hole of holes) {
      const [hEdgeSlots, hEdgeSenses, hNew] = this.writeEdgeChain(
        hole, vertexSlots, edgeRegistry, true, hiddenEdges, softEdges, smoothEdges, undefined
      );
      holeLoops.push([hEdgeSlots, hEdgeSenses]);
      newEntities += hNew;
    }

    this.newOfKnownClass('CFace', 3);
    if (frontUv !== undefined || backUv !== undefined || attributeDicts.length > 0) {
      this.preambleWithRealAttrs(frontMatrix, backMatrix, attributeDicts);
    } else {
      this.preamble();
    }
    this.drawbase(faceMaterial, faceLayer, hidden);
    this.pushF64(nx);
    this.pushF64(ny);
    this.pushF64(nz);
    this.pushF64(d);
    this.pushU32(1 + holes.length); // nloops

    const loopSlot = this.newOfKnownClass('CLoop', 1);
    this.preamble(0); // structural object: ground truth uses pid 0
    // legacy.ts's reader treats these 2 bytes as opaque, but real
    // SketchUp requires 01 01, not 00 00 - same silent-drop failure mode
    // as the drawbase padding above.
    this.pushBytes([1, 1]);

    for (let i = 0; i < edgeSlots.length; i++) {
      this.newOfKnownClass('CEdgeUse', 1);
      this.preamble(0);
      this.writeBackref(edgeSlots[i]);
      this.pushU8(edgeSenses[i]);
      this.writeBackref(loopSlot);
    }
    this.writeNull(); // loop terminator

    for (const [hEdgeSlots, hEdgeSenses] of holeLoops) {
      const hLoopSlot = this.newOfKnownClass('CLoop', 1);
      this.preamble(0);
      this.pushBytes([0, 1]); // ground truth: 0 marks a hole loop, not the boundary
      for (let i = 0; i < hEdgeSlots.length; i++) {
        this.newOfKnownClass('CEdgeUse', 1);
        this.preamble(0);
        this.writeBackref(hEdgeSlots[i]);
        this.pushU8(hEdgeSenses[i]);
        this.writeBackref(hLoopSlot);
      }
      this.writeNull();
    }

    this.pushU16(backMaterial);
    newEntities += 1; // the face itself
    return newEntities;
  }
}

/** Newell's method: sums a cross-product-like term over every edge
 * rather than reading the normal off just the first 3 points, so it
 * works for concave polygons too. */
function planeFromPolygon(points: readonly Point3[]): [number, number, number, number] {
  const n = points.length;
  let nx = 0, ny = 0, nz = 0;
  for (let i = 0; i < n; i++) {
    const [x0, y0, z0] = points[i];
    const [x1, y1, z1] = points[(i + 1) % n];
    nx += (y0 - y1) * (z0 + z1);
    ny += (z0 - z1) * (x0 + x1);
    nz += (x0 - x1) * (y0 + y1);
  }
  const length = Math.sqrt(nx * nx + ny * ny + nz * nz);
  if (length < 1e-9) throw new SkpWriteError('face points are collinear or degenerate; cannot compute a plane');
  nx /= length;
  ny /= length;
  nz /= length;
  const cx = points.reduce((s, p) => s + p[0], 0) / n;
  const cy = points.reduce((s, p) => s + p[1], 0) / n;
  const cz = points.reduce((s, p) => s + p[2], 0) / n;
  const d = nx * cx + ny * cy + nz * cz;

  const span = Math.max(
    ...[0, 1, 2].map((i) => Math.max(...points.map((p) => p[i])) - Math.min(...points.map((p) => p[i])))
  );
  const tol = Math.max(span, 1.0) * 1e-6;
  for (const p of points) {
    const dist = nx * p[0] + ny * p[1] + nz * p[2] - d;
    if (Math.abs(dist) > tol) {
      throw new SkpWriteError(
        `face points are not coplanar (point ${JSON.stringify(p)} is ${Math.abs(dist)} units ` +
          'off the fitted plane) - openskp only supports planar faces'
      );
    }
  }
  return [nx, ny, nz, d];
}

/** Same fit/tolerance planeFromPolygon uses, but returns a bool for "not
 * coplanar" instead of throwing - used by addFace's autoTriangulate to
 * decide whether a fan-triangulation fallback is even needed. */
function isCoplanar(points: readonly Point3[]): boolean {
  const n = points.length;
  let nx = 0, ny = 0, nz = 0;
  for (let i = 0; i < n; i++) {
    const [x0, y0, z0] = points[i];
    const [x1, y1, z1] = points[(i + 1) % n];
    nx += (y0 - y1) * (z0 + z1);
    ny += (z0 - z1) * (x0 + x1);
    nz += (x0 - x1) * (y0 + y1);
  }
  const length = Math.sqrt(nx * nx + ny * ny + nz * nz);
  if (length < 1e-9) throw new SkpWriteError('face points are collinear or degenerate; cannot compute a plane');
  nx /= length;
  ny /= length;
  nz /= length;
  const cx = points.reduce((s, p) => s + p[0], 0) / n;
  const cy = points.reduce((s, p) => s + p[1], 0) / n;
  const cz = points.reduce((s, p) => s + p[2], 0) / n;
  const d = nx * cx + ny * cy + nz * cz;
  const span = Math.max(
    ...[0, 1, 2].map((i) => Math.max(...points.map((p) => p[i])) - Math.min(...points.map((p) => p[i])))
  );
  const tol = Math.max(span, 1.0) * 1e-6;
  return points.every((p) => Math.abs(nx * p[0] + ny * p[1] + nz * p[2] - d) <= tol);
}

interface WriteFaceOrTriangulateArgs {
  writer: ArchiveWriter;
  points: Point3[];
  vertexSlots: Map<string, number>;
  edgeRegistry: Map<string, [number, number]>;
  material: number;
  layer: number;
  backMaterial: number;
  hidden: boolean;
  softEdges: boolean;
  smoothEdges: boolean;
  hiddenEdges: boolean;
  frontUv?: readonly UvPair[];
  backUv?: readonly UvPair[];
  attributeDicts: ReadonlyArray<[string, AttributeDict]>;
  autoTriangulate: boolean;
  holes: ReadonlyArray<readonly Point3[]>;
}

/** Shared by SkpBuilder.addFace and ComponentDefinitionBuilder.addFace -
 * writes `points` as one face normally, unless `autoTriangulate` is set
 * AND the points aren't coplanar, in which case it fan-triangulates from
 * points[0] into real, always-planar triangular faces - mirroring real
 * SketchUp's own UI behavior for a not-quite-flat quad. */
function writeFaceOrTriangulate(args: WriteFaceOrTriangulateArgs): number {
  const { writer, points, vertexSlots, edgeRegistry, material, layer, backMaterial, hidden, softEdges, smoothEdges, hiddenEdges, frontUv, backUv, attributeDicts, autoTriangulate, holes } = args;
  if (holes.length > 0 || !autoTriangulate || points.length === 3 || isCoplanar(points)) {
    return writer.writeFace(
      points, vertexSlots, edgeRegistry, material, layer, backMaterial,
      hidden, softEdges, smoothEdges, hiddenEdges, frontUv, backUv, attributeDicts, undefined, holes
    );
  }
  if (frontUv !== undefined || backUv !== undefined) {
    throw new SkpWriteError('autoTriangulate cannot be combined with frontUv/backUv positioning');
  }
  let total = 0;
  for (let i = 1; i < points.length - 1; i++) {
    total += writer.writeFace(
      [points[0], points[i], points[i + 1]], vertexSlots, edgeRegistry,
      material, layer, backMaterial, hidden, softEdges, smoothEdges, hiddenEdges,
      undefined, undefined, attributeDicts
    );
  }
  return total;
}

// ---------------------------------------------------------------------
// Public option types.
// ---------------------------------------------------------------------

export interface AddFaceOptions {
  material?: number;
  layer?: number;
  backMaterial?: number;
  hidden?: boolean;
  softEdges?: boolean;
  smoothEdges?: boolean;
  hiddenEdges?: boolean;
  /** Explicitly position the front side's texture instead of the
   * default planar projection: exactly 3 (point, (u, v)) pairs. */
  frontUv?: UvPair[];
  backUv?: UvPair[];
  attributes?: AttributeDict;
  attributeDictName?: string;
  /** Fan-triangulate a non-coplanar polygon instead of throwing. */
  autoTriangulate?: boolean;
  holes?: Point3[][];
}

export interface AddCircleOptions {
  numSegments?: number;
  material?: number;
  layer?: number;
  backMaterial?: number;
  hidden?: boolean;
  frontUv?: UvPair[];
  backUv?: UvPair[];
  attributes?: AttributeDict;
  attributeDictName?: string;
}

export interface AddArcOptions {
  numSegments?: number;
  hiddenEdges?: boolean;
  softEdges?: boolean;
  smoothEdges?: boolean;
}

export interface AddPolylineOptions {
  closed?: boolean;
  hiddenEdges?: boolean;
  softEdges?: boolean;
  smoothEdges?: boolean;
}

export interface AddInstanceOptions {
  name?: string;
  translation?: Point3;
  matrix3x3?: Matrix3x3;
  rotation?: Rotation;
  material?: number;
  layer?: number;
  attributes?: AttributeDict;
  attributeDictName?: string;
  hidden?: boolean;
}

export interface AddImageOptions {
  translation?: Point3;
  matrix3x3?: Matrix3x3;
  rotation?: Rotation;
  layer?: number;
  hidden?: boolean;
  /** Stored as-is in the image material's own texture-path field (SketchUp
   * shows it as the source file's original path); has no effect on the
   * embedded image bytes themselves. */
  texturePath?: string;
}

export interface AddGroupInstanceOptions {
  name?: string;
  translation?: Point3;
  matrix3x3?: Matrix3x3;
  rotation?: Rotation;
  material?: number;
  layer?: number;
  hidden?: boolean;
}

export interface AddComponentDefinitionOptions {
  attributes?: AttributeDict;
  attributeDictName?: string;
}

export interface AddGroupOptions {
  name?: string;
  translation?: Point3;
  matrix3x3?: Matrix3x3;
  rotation?: Rotation;
  material?: number;
  layer?: number;
  hidden?: boolean;
}

type GroupPlacement = [Point3, Matrix3x3 | undefined, number, number, boolean];

function toPoint3(p: readonly [number, number, number]): Point3 {
  return [Number(p[0]), Number(p[1]), Number(p[2])];
}

function attributeDictsFrom(attributes: AttributeDict | undefined, name: string): Array<[string, AttributeDict]> {
  return attributes ? [[name, attributes]] : [];
}

/**
 * Accumulates one component/group definition's geometry. Construct via
 * `SkpBuilder.addComponentDefinition`/`SkpBuilder.addGroup`, not
 * directly - the build callback runs synchronously; use the returned
 * (already-closed) builder for `addInstance`.
 *
 * ```ts
 * const chair = builder.addComponentDefinition('Chair', (def) => {
 *   def.addFace([[0, 0, 0], [20, 0, 0], [20, 20, 0], [0, 20, 0]]);
 * });
 * builder.addInstance(chair, { translation: [100, 0, 0] });
 * ```
 */
export class ComponentDefinitionBuilder {
  readonly slot: number;
  readonly name: string;
  /** @internal */
  _skp: SkpBuilder;
  private countPatchPos: number;
  private vertexSlots = new Map<string, number>();
  private edgeRegistry = new Map<string, [number, number]>();
  private newEntityCount = 0;
  private closed = false;
  private groupPlacement?: GroupPlacement;

  /** @internal */
  constructor(skp: SkpBuilder, slot: number, name: string, countPatchPos: number, groupPlacement?: GroupPlacement) {
    this._skp = skp;
    this.slot = slot;
    this.name = name;
    this.countPatchPos = countPatchPos;
    this.groupPlacement = groupPlacement;
  }

  private checkWritable(action: string): void {
    if (this.closed) {
      throw new SkpWriteError(
        `component definition ${JSON.stringify(this.name)} has already closed - cannot add more ${action} to it`
      );
    }
  }

  addFace(points: readonly Point3[], options: AddFaceOptions = {}): void {
    this.checkWritable('faces');
    const pts = points.map(toPoint3);
    if (pts.length < 3) throw new SkpWriteError('a face needs at least 3 points');
    const holes = (options.holes ?? []).map((h) => h.map(toPoint3));
    const attributeDicts = attributeDictsFrom(options.attributes, options.attributeDictName ?? 'attributes');
    this.newEntityCount += writeFaceOrTriangulate({
      writer: this._skp._definitionWriter(),
      points: pts, vertexSlots: this.vertexSlots, edgeRegistry: this.edgeRegistry,
      material: options.material ?? 0, layer: options.layer ?? 0, backMaterial: options.backMaterial ?? 0,
      hidden: options.hidden ?? false, softEdges: options.softEdges ?? false, smoothEdges: options.smoothEdges ?? false,
      hiddenEdges: options.hiddenEdges ?? false, frontUv: options.frontUv, backUv: options.backUv,
      attributeDicts, autoTriangulate: options.autoTriangulate ?? false, holes,
    });
  }

  addCircle(center: Point3, normal: Point3, radius: number, options: AddCircleOptions = {}): void {
    this.checkWritable('faces');
    const numSegments = options.numSegments ?? 24;
    if (!(numSegments >= 3 && numSegments <= 255)) {
      throw new SkpWriteError(`num_segments must be between 3 and 255, got ${numSegments}`);
    }
    const c = toPoint3(center);
    const n = normalize3(toPoint3(normal));
    const [u, w] = circleBasis(n);
    const xaxis: Point3 = [radius * u[0], radius * u[1], radius * u[2]];
    const curveParams: CurveParams = { center: c, normal: n, xaxis, startAngle: 0, endAngle: 2 * Math.PI, radius, numSegments };
    const points = circlePoints(c, radius, numSegments, u, w);
    const attributeDicts = attributeDictsFrom(options.attributes, options.attributeDictName ?? 'attributes');
    this.newEntityCount += this._skp._definitionWriter().writeFace(
      points, this.vertexSlots, this.edgeRegistry,
      options.material ?? 0, options.layer ?? 0, options.backMaterial ?? 0,
      options.hidden ?? false, false, false, false,
      options.frontUv, options.backUv, attributeDicts, curveParams
    );
  }

  addArc(center: Point3, normal: Point3, radius: number, startAngle: number, endAngle: number, options: AddArcOptions = {}): void {
    this.checkWritable('arcs');
    const numSegments = options.numSegments ?? 24;
    if (!(numSegments >= 3 && numSegments <= 255)) {
      throw new SkpWriteError(`num_segments must be between 3 and 255, got ${numSegments}`);
    }
    if (endAngle === startAngle) {
      throw new SkpWriteError('start_angle and end_angle must differ - use addCircle for a full circle');
    }
    const c = toPoint3(center);
    const n = normalize3(toPoint3(normal));
    const [u, w] = circleBasis(n);
    const xaxis: Point3 = [radius * u[0], radius * u[1], radius * u[2]];
    const curveParams: CurveParams = { center: c, normal: n, xaxis, startAngle, endAngle, radius, numSegments };
    const points = arcPoints(c, radius, numSegments, u, w, startAngle, endAngle);
    this.newEntityCount += this._skp._definitionWriter().writeArc(
      points, this.vertexSlots, this.edgeRegistry, curveParams,
      options.hiddenEdges ?? false, options.softEdges ?? false, options.smoothEdges ?? false
    );
  }

  addPolyline(points: readonly Point3[], options: AddPolylineOptions = {}): void {
    this.checkWritable('polylines');
    const pts = points.map(toPoint3);
    if (pts.length < 2) throw new SkpWriteError('a polyline needs at least 2 points');
    this.newEntityCount += this._skp._definitionWriter().writePolyline(
      pts, this.vertexSlots, this.edgeRegistry,
      options.closed ?? false, options.hiddenEdges ?? false, options.softEdges ?? false, options.smoothEdges ?? false
    );
  }

  /** Place one instance of another, already-closed component definition
   * inside this one - the same nesting real SketchUp supports.
   * `definition` must come from this same builder, and be a different,
   * already-closed definition (never `this`). */
  addInstance(definition: ComponentDefinitionBuilder, options: AddInstanceOptions = {}): void {
    this.checkWritable('instances');
    if (definition._skp !== this._skp) {
      throw new SkpWriteError(
        `component definition ${JSON.stringify(definition.name)} belongs to a different builder (a different create() call) - its slot number is meaningless here`
      );
    }
    if (definition === (this as unknown as ComponentDefinitionBuilder)) {
      throw new SkpWriteError(`component definition ${JSON.stringify(this.name)} cannot nest an instance of itself`);
    }
    const matrix3x3 = resolveMatrix3x3(options.matrix3x3, options.rotation);
    const attributeDicts = attributeDictsFrom(options.attributes, options.attributeDictName ?? 'attributes');
    this.newEntityCount += this._skp._definitionWriter().writeInstance(
      definition.slot, options.name ?? definition.name, options.translation ?? [0, 0, 0], matrix3x3,
      options.material ?? 0, options.layer ?? 0, attributeDicts, options.hidden ?? false
    );
  }

  /** Place another, already-closed component definition inside this one
   * as a *group* rather than a component instance. A nested group can't
   * be declared inline - build the group's geometry with a normal
   * `addComponentDefinition` first, then place it here. */
  addGroupInstance(definition: ComponentDefinitionBuilder, options: AddGroupInstanceOptions = {}): void {
    this.checkWritable('groups');
    if (definition._skp !== this._skp) {
      throw new SkpWriteError(
        `component definition ${JSON.stringify(definition.name)} belongs to a different builder (a different create() call) - its slot number is meaningless here`
      );
    }
    if (definition === (this as unknown as ComponentDefinitionBuilder)) {
      throw new SkpWriteError(`component definition ${JSON.stringify(this.name)} cannot nest a group instance of itself`);
    }
    const matrix3x3 = resolveMatrix3x3(options.matrix3x3, options.rotation);
    this.newEntityCount += this._skp._definitionWriter().writeGroup(
      definition.slot, options.name ?? definition.name, options.translation ?? [0, 0, 0], matrix3x3,
      options.material ?? 0, options.layer ?? 0, options.hidden ?? false
    );
  }

  /** @internal called automatically once the defining callback (passed
   * to addComponentDefinition/addGroup) returns. */
  _close(): void {
    if (this.newEntityCount === 0) {
      throw new SkpWriteError(`component definition ${JSON.stringify(this.name)} has no geometry - add at least one face`);
    }
    const writer = this._skp._definitionWriter();
    this._skp._patchDefinitionCount(this.countPatchPos, this.newEntityCount);
    writer.writeDefinitionTail(this.name);
    this.closed = true;
    this._skp._clearOpenDefinition();
    if (this.groupPlacement !== undefined) {
      // Deferred rather than written here - see SkpBuilder._ensureGeometryWriter.
      this._skp._pushPendingGroup(this, this.groupPlacement);
    }
  }
}

/**
 * Accumulates geometry and writes it into a new legacy-format (v17)
 * `.skp` file. Construct via `create()`, not directly.
 */
export class SkpBuilder {
  private data: Uint8Array;
  private materialInsertPos = MATERIAL_INSERT_POS;
  private base = BASE;
  private layerCountPos = LAYER_COUNT_POS;
  private origLayerCount = ORIG_LAYER_COUNT;
  private layerInsertPos = LAYER_INSERT_POS;
  private defCountPos = DEF_COUNT_POS;
  private origDefCount = ORIG_DEF_COUNT;
  private rootCountPos = ROOT_COUNT_POS;
  private origRootCount = ORIG_ROOT_COUNT;
  private tailPos = TAIL_POS;
  private scaffoldNextSlot = SCAFFOLD_NEXT_SLOT;
  private scaffoldClassSlot: Record<string, number> = { ...SCAFFOLD_CLASS_SLOT };

  private materialWriter: ArchiveWriter;
  /** Every material registered so far, by name - populated by
   * addMaterial/addTextureMaterial as a side effect. */
  readonly materialsByName = new Map<string, number>();
  private materialCount = 0;

  private layerWriterBase = LAYER_WRITER_BASE;
  private layerWriter: ArchiveWriter | null = null;
  private layerWriterStart: number | null = null;
  /** Every layer registered so far, by name - populated by addLayer. */
  readonly layersByName = new Map<string, number>();
  private layerCount = 0;

  private definitionWriterInstance: ArchiveWriter | null = null;
  private definitionWriterStart: number | null = null;
  private definitionCount = 0;
  private openDefinition: ComponentDefinitionBuilder | null = null;
  private pendingGroups: Array<[ComponentDefinitionBuilder, GroupPlacement]> = [];

  private geometryWriter: ArchiveWriter | null = null;
  private vertexSlots = new Map<string, number>();
  private edgeRegistry = new Map<string, [number, number]>();
  private newEntityCount = 0;
  private faceCount = 0;

  constructor() {
    this.data = loadScaffold();
    // Materials always start allocating at `base`, the same slot the
    // (possibly absent) material section would have occupied.
    this.materialWriter = new ArchiveWriter(this.base, {});
  }

  addMaterial(name: string, rgba: readonly number[]): number {
    if (this.geometryWriter !== null) throw new SkpWriteError('addMaterial must be called before any addFace calls');
    if (this.layerWriter !== null) throw new SkpWriteError('addMaterial must be called before any addLayer calls');
    if (this.definitionWriterInstance !== null) {
      throw new SkpWriteError('addMaterial must be called before any addComponentDefinition calls');
    }
    if (this.materialsByName.has(name)) return this.materialsByName.get(name)!;
    let full = rgba;
    if (full.length === 3) full = [...full, 255];
    if (full.length !== 4 || !full.every((c) => Number.isInteger(c) && c >= 0 && c <= 255)) {
      throw new SkpWriteError('rgba must be 3 or 4 integers in 0-255');
    }
    const slot = this.materialWriter.writeMaterial(name, full as [number, number, number, number]);
    this.materialsByName.set(name, slot);
    this.materialCount += 1;
    return slot;
  }

  /** Register an image-textured material and return a handle to pass as
   * addFace's `material` option. Unlike Python's `add_texture_material`
   * (which reads a file path), this takes the image bytes directly - a
   * deliberate adaptation since this package targets the browser as well
   * as Node, where there's no universal way to read an arbitrary file
   * path. `texturePath`, if given, is stored as-is in the material
   * record (SketchUp shows it as the texture's original file path); it
   * has no effect on the embedded image bytes themselves.
   *
   * If this material will ever be used with addFace's `frontUv`/`backUv`
   * pinning, pass `appliedHeight: 1.0` (matching those pins' own 0..1
   * range) - see writeTexturedMaterial's own note on why the default
   * (an internal sentinel) corrupts any face using this material, not
   * just default-projected ones. */
  addTextureMaterial(name: string, imageBytes: Uint8Array, texturePath = '', appliedHeight?: number): number {
    if (this.geometryWriter !== null) {
      throw new SkpWriteError('addTextureMaterial must be called before any addFace calls');
    }
    if (this.layerWriter !== null) {
      throw new SkpWriteError('addTextureMaterial must be called before any addLayer calls');
    }
    if (this.definitionWriterInstance !== null) {
      throw new SkpWriteError('addTextureMaterial must be called before any addComponentDefinition calls');
    }
    if (this.materialsByName.has(name)) return this.materialsByName.get(name)!;
    const subtype = detectImageSubtype(imageBytes);
    const slot = this.materialWriter.writeTexturedMaterial(name, imageBytes, texturePath, subtype, appliedHeight);
    this.materialsByName.set(name, slot);
    this.materialCount += 1;
    return slot;
  }

  addLayer(name: string, options: { color?: readonly number[]; hidden?: boolean } = {}): number {
    if (this.geometryWriter !== null) throw new SkpWriteError('addLayer must be called before any addFace calls');
    if (this.definitionWriterInstance !== null) {
      throw new SkpWriteError('addLayer must be called before any addComponentDefinition calls');
    }
    if (this.layersByName.has(name)) return this.layersByName.get(name)!;
    let rgba: [number, number, number, number] | undefined;
    if (options.color !== undefined) {
      let c = options.color;
      if (c.length === 3) c = [...c, 255];
      if (c.length !== 4 || !c.every((v) => Number.isInteger(v) && v >= 0 && v <= 255)) {
        throw new SkpWriteError('color must be 3 or 4 integers in 0-255');
      }
      rgba = c as [number, number, number, number];
    }
    if (this.layerWriter === null) {
      const materialShift = this.materialWriter.nextSlot - this.base;
      this.layerWriterStart = this.layerWriterBase + materialShift;
      this.layerWriter = new ArchiveWriter(this.layerWriterStart, this.materialShiftedClassSlot());
    }
    const slot = this.layerWriter.writeLayer(name, true, options.hidden ?? false, rgba);
    this.layersByName.set(name, slot);
    this.layerCount += 1;
    return slot;
  }

  private materialShiftedClassSlot(): Record<string, number> {
    const materialShift = this.materialWriter.nextSlot - this.base;
    const out: Record<string, number> = {};
    for (const [n, s] of Object.entries(this.scaffoldClassSlot)) out[n] = s + materialShift;
    return out;
  }

  private layerShift(): number {
    if (this.layerWriter === null) return 0;
    return this.layerWriter.nextSlot - (this.layerWriterStart as number);
  }

  private postLayerClassSlot(): Record<string, number> {
    if (this.layerWriter !== null) return { ...this.layerWriter.classSlot };
    return this.materialShiftedClassSlot();
  }

  private startDefinition(
    name: string, caller: string, groupPlacement?: GroupPlacement,
    attributeDicts: ReadonlyArray<[string, AttributeDict]> = []
  ): ComponentDefinitionBuilder {
    if (this.geometryWriter !== null) {
      throw new SkpWriteError(`${caller} must be called before any addFace/addInstance calls`);
    }
    if (this.openDefinition !== null) {
      throw new SkpWriteError(
        `component definition ${JSON.stringify(this.openDefinition.name)} is still open - close it before starting another`
      );
    }
    if (this.definitionWriterInstance === null) {
      this.definitionWriterStart =
        this.scaffoldNextSlot + (this.materialWriter.nextSlot - this.base) + this.layerShift();
      this.definitionWriterInstance = new ArchiveWriter(this.definitionWriterStart, this.postLayerClassSlot());
    }
    const [slot, countPatchPos] = this.definitionWriterInstance.writeDefinitionHeader(attributeDicts);
    this.definitionCount += 1;
    const comp = new ComponentDefinitionBuilder(this, slot, name, countPatchPos, groupPlacement);
    this.openDefinition = comp;
    return comp;
  }

  /** Start a new reusable component definition. `build` runs
   * synchronously; add geometry to the definition inside it (via
   * `.addFace` etc.) - the returned, already-closed builder can then be
   * passed to `addInstance` to place copies of it in the model.
   *
   * ```ts
   * const chair = builder.addComponentDefinition('Chair', (def) => {
   *   def.addFace([[0, 0, 0], [20, 0, 0], [20, 20, 0], [0, 20, 0]]);
   * });
   * builder.addInstance(chair, { translation: [100, 0, 0] });
   * ```
   *
   * Must be called before any addFace/addInstance call on the builder
   * itself - component definitions splice in after materials and
   * layers, before root-level geometry. */
  addComponentDefinition(
    name: string, build: (def: ComponentDefinitionBuilder) => void, options: AddComponentDefinitionOptions = {}
  ): ComponentDefinitionBuilder {
    const attributeDicts = attributeDictsFrom(options.attributes, options.attributeDictName ?? 'attributes');
    const def = this.startDefinition(name, 'addComponentDefinition', undefined, attributeDicts);
    build(def);
    def._close();
    return def;
  }

  /** Start a new group. `build` runs synchronously; add geometry inside
   * it - the group is placed at `translation`/`matrix3x3` automatically
   * once `build` returns, unlike `addComponentDefinition` there is no
   * separate placement call.
   *
   * ```ts
   * builder.addGroup((table) => {
   *   table.addFace([[0, 0, 0], [30, 0, 0], [30, 30, 0], [0, 30, 0]]);
   * }, { name: 'Table', translation: [50, 0, 0] });
   * ``` */
  addGroup(build: (def: ComponentDefinitionBuilder) => void, options: AddGroupOptions = {}): ComponentDefinitionBuilder {
    const matrix3x3 = resolveMatrix3x3(options.matrix3x3, options.rotation);
    const placement: GroupPlacement = [
      options.translation ?? [0, 0, 0], matrix3x3, options.material ?? 0, options.layer ?? 0, options.hidden ?? false,
    ];
    const def = this.startDefinition(options.name ?? 'Group', 'addGroup', placement);
    build(def);
    def._close();
    return def;
  }

  private definitionShift(): number {
    if (this.definitionWriterInstance === null) return 0;
    return this.definitionWriterInstance.nextSlot - (this.definitionWriterStart as number);
  }

  private postDefinitionClassSlot(): Record<string, number> {
    if (this.definitionWriterInstance !== null) return { ...this.definitionWriterInstance.classSlot };
    return this.postLayerClassSlot();
  }

  /** Place one instance of `definition` (from addComponentDefinition,
   * already closed) in the model. `rotation`, if given, is an
   * alternative to `matrix3x3` for the common case of a pure rotation. */
  addInstance(definition: ComponentDefinitionBuilder, options: AddInstanceOptions = {}): void {
    if (definition._skp !== this) {
      throw new SkpWriteError(
        `component definition ${JSON.stringify(definition.name)} belongs to a different builder (a different create() call) - its slot number is meaningless here`
      );
    }
    const matrix3x3 = resolveMatrix3x3(options.matrix3x3, options.rotation);
    this.ensureGeometryWriter();
    const attributeDicts = attributeDictsFrom(options.attributes, options.attributeDictName ?? 'attributes');
    this.newEntityCount += (this.geometryWriter as ArchiveWriter).writeInstance(
      definition.slot, options.name ?? definition.name, options.translation ?? [0, 0, 0], matrix3x3,
      options.material ?? 0, options.layer ?? 0, attributeDicts, options.hidden ?? false
    );
    this.faceCount += 1; // reuses the "at least one root entity" check in toBytes
  }

  /** Place a SketchUp Image entity (File > Import > Image) - a picture
   * placed as its own object, distinct from painting a texture material
   * onto an ordinary face (an Image gets its own Outliner classification
   * and explode behavior a plain textured face doesn't).
   *
   * `width`/`height` size the image's quad in inches; the image covers it
   * edge to edge, undistorted regardless of the source file's own pixel
   * aspect ratio (get the ratio right yourself if that matters). Unlike
   * `addTextureMaterial`, this takes the image bytes directly (browser
   * compatibility - see addTextureMaterial's own note).
   *
   * ```ts
   * builder.addImage(photoBytes, 48, 36, {
   *   translation: [0, 0, 40],
   *   rotation: { axis: [1, 0, 0], angleRadians: Math.PI / 2 },
   * });
   * ```
   *
   * Must be called before any addLayer/addComponentDefinition/addGroup/
   * addFace/addInstance call - like addTextureMaterial (which this calls
   * internally to register the image itself), it needs a material, and
   * this writer's file format requires every material to be registered
   * before any geometry section begins.
   *
   * The image's quad and UV mapping are pinned explicitly (addFace's
   * `frontUv`), not left to the default per-material tile-size projection
   * - addTextureMaterial is called with `appliedHeight: 1.0` for exactly
   * this reason: the read-side UV formula divides by the material's
   * applied height even for a pinned mapping, and the library default
   * there (a ground-truth sentinel, not a real number) is astronomically
   * small - confirmed via real SketchUp screenshots (2026-08-27, Python
   * writer) to render as a corrupted, vertically-smeared texture when
   * left in place. 1.0 makes that division a no-op against this method's
   * own 0..1 pins.
   *
   * ⚠️ Unlike every other entity this writer produces, CImage's exact
   * binary schema version (see IMAGE_SCHEMA) is a best-effort guess, not
   * calibrated against a real SketchUp-authored Image entity - none was
   * available. This project's own reader round-trips the result
   * correctly, but real SketchUp's acceptance of the file is unverified
   * beyond the Python port's own real-SketchUp test (placement/
   * orientation/texture all confirmed correct there after the
   * appliedHeight fix - see CHECKLIST.md). */
  addImage(imageBytes: Uint8Array, width: number, height: number, options: AddImageOptions = {}): void {
    const mat = this.addTextureMaterial(
      `__openskp_image_${this.materialCount}`, imageBytes, options.texturePath ?? '', 1.0
    );
    const imageDef = this.addComponentDefinition(`Image${this.definitionCount}`, (def) => {
      // Standard (0,0)-at-bottom-left, V increasing upward - no vertical
      // flip. Every other UV-related fact in this file is calibrated
      // against real SketchUp output; this one specific sense is NOT (no
      // ground truth available) and could come out upside down in real
      // SketchUp if its texture sampling flips V the other way.
      def.addFace(
        [[0, 0, 0], [width, 0, 0], [width, height, 0], [0, height, 0]],
        {
          material: mat,
          frontUv: [
            [[0, 0, 0], [0, 0]],
            [[width, 0, 0], [1, 0]],
            [[0, height, 0], [0, 1]],
          ],
        }
      );
    });
    const matrix3x3 = resolveMatrix3x3(options.matrix3x3, options.rotation);
    this.ensureGeometryWriter();
    this.newEntityCount += (this.geometryWriter as ArchiveWriter).writeImage(
      imageDef.slot, options.translation ?? [0, 0, 0], matrix3x3, options.layer ?? 0, options.hidden ?? false
    );
    this.faceCount += 1; // reuses the "at least one root entity" check in toBytes
  }

  private ensureGeometryWriter(): void {
    if (this.geometryWriter !== null) return;
    if (this.openDefinition !== null) {
      // Calling this while a definition/group is still open would lock in
      // the geometry writer's starting slot before that definition
      // finishes growing definitionWriterInstance - corrupting every
      // back-reference root-level geometry makes.
      throw new SkpWriteError(
        `component definition ${JSON.stringify(this.openDefinition.name)} is still open - close it before adding root-level geometry`
      );
    }
    const materialShift = this.materialWriter.nextSlot - this.base;
    this.geometryWriter = new ArchiveWriter(
      this.scaffoldNextSlot + materialShift + this.layerShift() + this.definitionShift(),
      this.postDefinitionClassSlot()
    );
    // Flush any groups that closed earlier, in the order they were
    // created - deferred until now so closing one group doesn't lock in
    // root-level slot numbering before a later addGroup/
    // addComponentDefinition call has had a chance to run.
    for (const [comp, [translation, matrix3x3, mat, layer, hidden]] of this.pendingGroups) {
      this.newEntityCount += this.geometryWriter.writeGroup(comp.slot, comp.name, translation, matrix3x3, mat, layer, hidden);
      this.faceCount += 1;
    }
    this.pendingGroups = [];
  }

  /** Add one planar face, defined by 3+ coplanar points (inches) forming
   * a closed polygon in order - do not repeat the first point. Vertices
   * and edges are automatically shared with previously-added faces
   * wherever a point's coordinates match exactly. */
  addFace(points: readonly Point3[], options: AddFaceOptions = {}): void {
    const pts = points.map(toPoint3);
    if (pts.length < 3) throw new SkpWriteError('a face needs at least 3 points');
    const holes = (options.holes ?? []).map((h) => h.map(toPoint3));
    this.ensureGeometryWriter();
    const attributeDicts = attributeDictsFrom(options.attributes, options.attributeDictName ?? 'attributes');
    this.newEntityCount += writeFaceOrTriangulate({
      writer: this.geometryWriter as ArchiveWriter,
      points: pts, vertexSlots: this.vertexSlots, edgeRegistry: this.edgeRegistry,
      material: options.material ?? 0, layer: options.layer ?? 0, backMaterial: options.backMaterial ?? 0,
      hidden: options.hidden ?? false, softEdges: options.softEdges ?? false, smoothEdges: options.smoothEdges ?? false,
      hiddenEdges: options.hiddenEdges ?? false, frontUv: options.frontUv, backUv: options.backUv,
      attributeDicts, autoTriangulate: options.autoTriangulate ?? false, holes,
    });
    this.faceCount += 1;
  }

  /** Add one circular face - a true SketchUp circle (editable by radius,
   * re-tessellatable), not `numSegments` disconnected straight edges. */
  addCircle(center: Point3, normal: Point3, radius: number, options: AddCircleOptions = {}): void {
    const numSegments = options.numSegments ?? 24;
    if (!(numSegments >= 3 && numSegments <= 255)) {
      throw new SkpWriteError(`num_segments must be between 3 and 255, got ${numSegments}`);
    }
    const c = toPoint3(center);
    const n = normalize3(toPoint3(normal));
    this.ensureGeometryWriter();
    const [u, w] = circleBasis(n);
    const xaxis: Point3 = [radius * u[0], radius * u[1], radius * u[2]];
    const curveParams: CurveParams = { center: c, normal: n, xaxis, startAngle: 0, endAngle: 2 * Math.PI, radius, numSegments };
    const points = circlePoints(c, radius, numSegments, u, w);
    const attributeDicts = attributeDictsFrom(options.attributes, options.attributeDictName ?? 'attributes');
    this.newEntityCount += (this.geometryWriter as ArchiveWriter).writeFace(
      points, this.vertexSlots, this.edgeRegistry,
      options.material ?? 0, options.layer ?? 0, options.backMaterial ?? 0,
      options.hidden ?? false, false, false, false,
      options.frontUv, options.backUv, attributeDicts, curveParams
    );
    this.faceCount += 1;
  }

  /** Add one partial (open) arc - a genuine SketchUp arc entity, edges
   * only, no face. `startAngle`/`endAngle` (radians) measure the sweep
   * from an arbitrary but fixed reference direction in the arc's plane. */
  addArc(center: Point3, normal: Point3, radius: number, startAngle: number, endAngle: number, options: AddArcOptions = {}): void {
    const numSegments = options.numSegments ?? 24;
    if (!(numSegments >= 3 && numSegments <= 255)) {
      throw new SkpWriteError(`num_segments must be between 3 and 255, got ${numSegments}`);
    }
    if (endAngle === startAngle) {
      throw new SkpWriteError('start_angle and end_angle must differ - use addCircle for a full circle');
    }
    const c = toPoint3(center);
    const n = normalize3(toPoint3(normal));
    this.ensureGeometryWriter();
    const [u, w] = circleBasis(n);
    const xaxis: Point3 = [radius * u[0], radius * u[1], radius * u[2]];
    const curveParams: CurveParams = { center: c, normal: n, xaxis, startAngle, endAngle, radius, numSegments };
    const points = arcPoints(c, radius, numSegments, u, w, startAngle, endAngle);
    this.newEntityCount += (this.geometryWriter as ArchiveWriter).writeArc(
      points, this.vertexSlots, this.edgeRegistry, curveParams,
      options.hiddenEdges ?? false, options.softEdges ?? false, options.smoothEdges ?? false
    );
    this.faceCount += 1; // reuses the "at least one root entity" check in toBytes
  }

  /** Add one freeform polyline curve - a chain of straight edges grouped
   * into one genuine SketchUp "Curve" entity, no face. */
  addPolyline(points: readonly Point3[], options: AddPolylineOptions = {}): void {
    const pts = points.map(toPoint3);
    if (pts.length < 2) throw new SkpWriteError('a polyline needs at least 2 points');
    this.ensureGeometryWriter();
    this.newEntityCount += (this.geometryWriter as ArchiveWriter).writePolyline(
      pts, this.vertexSlots, this.edgeRegistry,
      options.closed ?? false, options.hiddenEdges ?? false, options.softEdges ?? false, options.smoothEdges ?? false
    );
    this.faceCount += 1; // reuses the "at least one root entity" check in toBytes
  }

  /** Return the finished file's bytes. */
  toBytes(): Uint8Array {
    if (this.pendingGroups.length > 0) {
      // A file with only groups (no addFace/addInstance call) would
      // otherwise never flush them.
      this.ensureGeometryWriter();
    }
    if (this.faceCount === 0) throw new SkpWriteError('no geometry added - call addFace at least once before saving');

    const materialShift = this.materialWriter.nextSlot - this.base;
    const layerShift = this.layerShift();
    const definitionShift = this.definitionShift();
    const geometryInitialSlot = this.scaffoldNextSlot + materialShift + layerShift + definitionShift;
    const geometryShift = (this.geometryWriter as ArchiveWriter).nextSlot - geometryInitialSlot;
    const newRootCount = this.origRootCount + this.newEntityCount;

    const out: number[] = [];

    // The 4 bytes right before the material insertion point are a
    // reserved (always-present) mat_count field - zero/implicit in the
    // zero-material scaffold, not a gap that needs new bytes inserted.
    // Real SketchUp overwrites them in place rather than growing the
    // file by 4 extra bytes here.
    const layerPids = this.layerWriter ? this.layerWriter.nextPid - 1 : 0;
    const pidDelta = this.materialCount + layerPids;

    const prefix = Array.from(this.data.subarray(0, this.materialInsertPos - 4));
    if (pidDelta) {
      const u16 = readU16(prefix, PID_COUNTER_POS);
      writeU16At(prefix, PID_COUNTER_POS, u16 + pidDelta);
    }
    for (let i = 0; i < ISO_CAMERA_PREFIX_PATCH.length; i++) {
      prefix[ISO_CAMERA_PREFIX_OFFSET + i] = ISO_CAMERA_PREFIX_PATCH[i];
    }
    appendAll(out, prefix);
    appendAll(out, u32Bytes(this.materialCount));
    appendAll(out, this.materialWriter.bytes);

    // materialInsertPos -> layerInsertPos: Layer0 (and any other already-
    // existing layers) plus the layer_count field, unmodified except for
    // that count.
    const middle1 = Array.from(this.data.subarray(this.materialInsertPos, this.layerInsertPos));
    const layerCountRel = this.layerCountPos - this.materialInsertPos;
    writeU32At(middle1, layerCountRel, this.origLayerCount + this.layerCount);
    appendAll(out, middle1);
    if (this.layerWriter !== null) appendAll(out, this.layerWriter.bytes);

    // layerInsertPos -> defCountPos: just the active-layer anchor, which
    // needs +materialShift (never +layerShift - Layer0 itself never moves
    // just because more layers are appended after it).
    const middle2a = Array.from(this.data.subarray(this.layerInsertPos, this.defCountPos));
    if (materialShift) shiftRef(middle2a, ACTIVE_LAYER_ANCHOR_REL, materialShift);
    appendAll(out, middle2a);

    appendAll(out, u32Bytes(this.origDefCount + this.definitionCount));
    if (this.definitionWriterInstance !== null) appendAll(out, this.definitionWriterInstance.bytes);

    // defCountPos+4 -> rootCountPos: any already-existing definitions
    // (none, in the blank scaffold), unmodified.
    appendAll(out, this.data.subarray(this.defCountPos + 4, this.rootCountPos));

    appendAll(out, u32Bytes(newRootCount));
    appendAll(out, this.data.subarray(this.rootCountPos + 4, this.tailPos));
    appendAll(out, (this.geometryWriter as ArchiveWriter).bytes);

    const tail = Array.from(this.data.subarray(this.tailPos));
    const totalTailShift = materialShift + layerShift + definitionShift + geometryShift;
    // TAIL_REF_POSITIONS and ISO_CAMERA_TAIL_PATCHES's positions both
    // index into this same tail buffer. A ref-shift that widens to the
    // 6-byte escape form grows the buffer at that point, pushing every
    // later position forward - so every action is applied in ascending
    // original-offset order, tracking that growth.
    const isoPatches = new Map(ISO_CAMERA_TAIL_PATCHES);
    const actions: Array<[number, 'ref' | 'patch']> = [
      ...TAIL_REF_POSITIONS.map((pos): [number, 'ref'] => [pos, 'ref']),
      ...Array.from(isoPatches.keys()).map((pos): [number, 'patch'] => [pos, 'patch']),
    ].sort((a, b) => a[0] - b[0]);
    let growth = 0;
    for (const [pos, kind] of actions) {
      const here = pos + growth;
      if (kind === 'ref') {
        growth += shiftRef(tail, here, totalTailShift);
      } else {
        const patch = isoPatches.get(pos) as number[];
        for (let i = 0; i < patch.length; i++) tail[here + i] = patch[i];
      }
    }
    appendAll(out, tail);
    return Uint8Array.from(out);
  }

  /** Write the finished file to `path` (Node.js only). */
  save(path: string): void {
    if (typeof process === 'undefined' || !process.versions || !process.versions.node) {
      throw new Error('SkpBuilder.save is only supported in Node.js environments - use toBytes() elsewhere');
    }
    const fs = require('fs');
    fs.writeFileSync(path, Buffer.from(this.toBytes()));
  }

  // -- internals used by ComponentDefinitionBuilder --

  /** @internal */
  _definitionWriter(): ArchiveWriter {
    return this.definitionWriterInstance as ArchiveWriter;
  }

  /** @internal */
  _patchDefinitionCount(countPatchPos: number, count: number): void {
    const writer = this.definitionWriterInstance as ArchiveWriter;
    writeU32At(writer.bytes, countPatchPos, count);
  }

  /** @internal */
  _clearOpenDefinition(): void {
    this.openDefinition = null;
  }

  /** @internal */
  _pushPendingGroup(comp: ComponentDefinitionBuilder, placement: GroupPlacement): void {
    this.pendingGroups.push([comp, placement]);
  }
}

/**
 * Start building a new legacy-format (v17) `.skp` file from scratch.
 *
 * ```ts
 * const builder = create();
 * const red = builder.addMaterial('Red', [255, 0, 0]);
 * const roof = builder.addLayer('Roof');
 * builder.addFace([[0, 0, 0], [100, 0, 0], [100, 100, 0], [0, 100, 0]], { material: red, layer: roof });
 * builder.save('output.skp');
 * ```
 *
 * See this module's own docstring for the current scope and limitations
 * (no inline-declared nested groups; inches only).
 */
export function create(): SkpBuilder {
  return new SkpBuilder();
}

/** @internal Exposed only for this package's own test suite - not part of
 * the public API (not re-exported from index.ts). Mirrors how Python's
 * test suite reaches into create.py's underscore-prefixed internals
 * directly via the module object (e.g. `create_module._ArchiveWriter`,
 * `create_module._shift_ref`) for the slot-0x7FFF boundary-encoding
 * tests specifically. */
export const _internal = {
  ArchiveWriter,
  shiftRef,
  planeFromPolygon,
  isCoplanar,
};
