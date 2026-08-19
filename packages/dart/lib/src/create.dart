/// Create new legacy-format (v17) `.skp` files from scratch.
///
/// This is a genuine, from-scratch binary writer for the same MFC
/// `CArchive` object-stream format [legacy] reads - built by inverting that
/// reader's own, already-proven decoding logic (the class-ref/back-ref
/// protocol, entity preambles, drawbase records), ported field-for-field
/// from `openskp.create` (Python), the reference implementation validated
/// against real desktop SketchUp until it produced files SketchUp actually
/// opens correctly. No SketchUp SDK is called at runtime; this module never
/// links against or shells out to any proprietary library - the one place
/// SDK-authored bytes are involved is the bundled blank scaffold, see below.
///
/// **Scope** mirrors the Python writer: faces built from vertex coordinates
/// (sharing vertices/edges automatically wherever coordinates coincide
/// exactly), solid-color and PNG/JPEG-textured materials, named layers,
/// reusable component definitions with multiple positioned instances, and
/// self-placing groups. A definition can nest instances/group-instances of
/// another already-closed definition. Faces support explicit per-side
/// texture positioning, custom attribute dictionaries, holes, and an
/// `autoTriangulate` fallback for non-planar input. Circles/arcs are real
/// `CArcCurve` entities; polylines are real `CCurve` groupings. Coordinates
/// are in **inches** (SketchUp's native unit for this era).
///
/// **The blank scaffold, and why it's there.** Every legacy `.skp` file
/// carries a header/material-manager/style-and-font-manager region this
/// project has not fully reverse-engineered - only enough of it is
/// understood to preserve it byte-for-byte and correctly renumber the
/// handful of internal references inside it that shift when new geometry
/// is inserted (see [_tailRefPositions] below). Rather than guess at
/// synthesizing that region from scratch, new files are built by splicing
/// genuinely-written geometry into a bundled minimal empty-document
/// template ([blankV17ScaffoldBase64] in scaffold_data.dart, decoded from
/// the same `blank_v17.skp` bytes the Python port bundles - see
/// `lib/src/scaffold/blank_v17.skp`).
///
/// That template's bytes came from Trimble's own official SketchUp SDK
/// during the Python writer's research phase (`SUModelCreate` + a bare
/// `SUModelSaveToFileWithVersion` call, nothing else) - disclosed here
/// plainly rather than hidden. Its content is SketchUp's own built-in
/// empty-document boilerplate (default style, default "Layer0", references
/// to system fonts like Arial/Tahoma) - the same bytes any brand-new
/// SketchUp document contains regardless of who created it, not anyone's
/// creative work or user/client data. The actual value in this module - the
/// entity byte-encoding, the object-graph protocol, the specific flag bytes
/// real SketchUp silently requires, the tail-reference renumbering - is
/// independently reverse-engineered (by the Python port) and ported here
/// faithfully, not derived from or wrapping the SDK.
library;

import 'dart:convert';
import 'dart:io';
import 'dart:math';
import 'dart:typed_data';

import 'package:crypto/crypto.dart';
import 'package:meta/meta.dart';

import 'legacy.dart';
import 'scaffold_data.dart';
import 'tlv.dart';

/// A 3D point/vector in inches: (x, y, z). Dart records give this
/// structural equality/hashCode for free, which is what lets
/// `Map<Point3, int>` (vertex deduplication by exact coordinate) work
/// without a custom key type - the same role Python's plain `(x, y, z)`
/// tuple plays as a dict key there.
typedef Point3 = (double, double, double);

/// A row-major 3x3 matrix (rotation/scale), flattened as 9 values in
/// row-major order: (m00, m01, m02, m10, m11, m12, m20, m21, m22).
typedef Matrix3x3 = (
  double,
  double,
  double,
  double,
  double,
  double,
  double,
  double,
  double,
);

/// `(axis, angleRadians)` - an alternative way to specify an
/// instance/group's rotation instead of a hand-derived [Matrix3x3]. See
/// [rotationMatrix3x3].
typedef Rotation = (Point3, double);

/// The 7 values [ArchiveWriterFace.writeArcCurve] needs: `(center, normal,
/// xaxis, startAngle, endAngle, radius, numSegments)`.
typedef CurveParams = (Point3, Point3, Point3, double, double, double, int);

/// `(translation, matrix3x3, materialSlot, layerSlot, hidden)` - a group's
/// deferred self-placement, recorded when [SkpBuilder.addGroup]'s body
/// closes and flushed the first time anything actually needs the root
/// geometry writer (see [SkpBuilder._ensureGeometryWriter]).
typedef GroupPlacement = (Point3, Matrix3x3?, int, int, bool);

/// Raised when a `.skp` file cannot be constructed.
class SkpWriteError implements Exception {
  final String message;
  SkpWriteError(this.message);
  @override
  String toString() => 'SkpWriteError: $message';
}

// ── scaffold / ground-truth constants ──────────────────────────────────────
//
// These offsets and byte blobs are specific to the exact bytes of the
// bundled blank_v17.skp scaffold, empirically derived (by the Python port)
// by diffing real SDK-authored v17 files - see the module doc comment above
// and each constant's own note. Copied unchanged from create.py; only the
// encoding (Python -> Dart) is translated, never the values.

const String _scaffoldSha256 =
    '809a1ab73a20a192ab13aaff197afb1c67d0e9352f6a353a9cd8030919f8a6c3';

/// Offsets (relative to the start of the document "tail" - the undecoded
/// style/font-manager region that follows the root entity list) of internal
/// references that must be renumbered by the same amount as the number of
/// new archive slots inserted before them. Found empirically by diffing two
/// real SDK-authored v17 files differing by exactly one piece of geometry,
/// confirmed to hold up to a 600-new-entity insertion via the real SketchUp
/// SDK as a validation oracle. Specific to this exact scaffold file's tail
/// content.
const List<int> _tailRefPositions = [409, 468, 477, 479, 1383, 1385];

/// The blank scaffold ships with SketchUp's own arbitrary default camera;
/// every file this writer produces instead patches it to the standard "Iso"
/// view (eye along the (1, -1, 1) octant looking at the origin, up = Z,
/// parallel/orthographic projection) so it opens already framed the
/// conventional way. Found by diffing two SDK-authored blank documents that
/// differ only in an explicit SUCameraSetOrientation +
/// SUCameraSetPerspective(False) call before saving - these are SketchUp's
/// own bytes for that camera setting, copied verbatim rather than decoded.
/// The prefix offset is absolute (within the always-unshifted scaffold
/// prefix); the tail patches are relative to the document "tail" like
/// [_tailRefPositions].
const int _isoCameraPrefixOffset = 2993;

final List<int> _isoCameraPrefixPatch = _hexBytes(
  '594000000000000059c000000000000059400000000000000000000000000000'
  '000000000000000000003f2c0c70bd20dabf3f2c0c70bd20da3f3f2c0c70bd20'
  'ea3f000000000000f03f0000000000408f40000000000000003e402adf272c80'
  '3457',
);

final List<(int, List<int>)> _isoCameraTailPatches = [
  (509, _hexBytes('d0a869613c442d4799a4667d1adfa836')),
  (1390, _hexBytes('4e53c84477029246bba95827bba7e2')),
];

/// Offset (relative to the material-manager insertion point - the position
/// right before the "layer list marker" a zero-material scaffold starts
/// with) of the active-layer anchor: a back-reference to the model's first
/// layer (Layer0) living immediately after the last existing layer record.
/// It moves only when materials shift Layer0's own slot (never when layers
/// are appended after it).
const int _activeLayerAnchorRel = 0;

/// Absolute offset of a u16 "next available pid" counter that lives BEFORE
/// the material insertion point (so only its value, not its position,
/// needs correction). Increments by exactly the material COUNT (one pid
/// consumed per material object; the material class declaration itself
/// doesn't consume a pid). Confirmed up to N=300.
const int _pidCounterPos = 1987;

const int _materialSchema = 12;
const int _dibSchema = 3;

/// Ground-truth byte pattern (not a meaningful float) that real SketchUp
/// writes for a texture's "applied height" when the caller never
/// explicitly overrides the texture's scale/aspect. Present verbatim
/// rather than derived from a formula since its bit pattern doesn't
/// correspond to any sensible height value (it decodes as ~1.29e-231 as an
/// f64).
final List<int> _textureHSentinel = _hexBytes('f0ffffffffffff0f');

const int _definitionSchema = 11;
const int _instanceSchema = 6;
const int _groupSchema = 1;
const int _thumbnailSchema = 1;

/// CCamera's class is declared inside the scaffold's own style/scene-manager
/// prefix, ground-truth confirmed fixed at slot 7 for this exact bundled
/// scaffold file. A thumbnail's camera sub-object is always written as a
/// short class-ref to this slot.
const int _ccameraSlot = 7;

/// Same pattern as [_ccameraSlot]: CAttributeContainer's class is declared
/// in the scaffold's own prefix, ground-truth confirmed fixed at slot 3.
const int _attrContainerSlot = 3;

/// Same pattern again: CAttributeNamed (one named key/value dictionary
/// within an attribute container) is also pre-declared in the scaffold's
/// own prefix, ground-truth confirmed fixed at slot 5.
const int _attributeNamedSlot = 5;

const int _attrTypeInt32 = 0x04;
const int _attrTypeDouble = 0x06;
const int _attrTypeString = 0x0A;

/// The 176 bytes (everything after CCamera's 2-byte class-ref tag) real
/// SketchUp writes for a definition's default thumbnail camera - copied
/// verbatim rather than decoded, the same way as [_textureHSentinel]: this
/// project has not reverse-engineered CCamera's internal fields, and a
/// thumbnail's camera framing has no bearing on the geometry it depicts.
final List<int> _cameraTemplate = _hexBytes(
  '00000000000000000000000000000000000000000000f03f0000000000000000'
  '00000000000000000000000000000000004000000000000000000000000000f0'
  '3f0000000000000000000000000000000000000000000000000100000000003e'
  '40000000000000f03f0000000000000000000000000000000000000000000000'
  '0000000000000000000100fffeff00000000000000000000000000000000f03f'
  '00000000000000000000000000000000',
);

/// The definition record's 22-byte "base block" (immediately after its own
/// preamble, before the embedded layer list) - all zero except offsets
/// 3-4, matching the same 1,1 padding convention drawbase already
/// requires. This project has not reverse-engineered its meaning, only
/// confirmed via ground truth that a definition with these bytes zeroed
/// loads correctly.
const List<int> _definitionBaseBlock = [
  0, 0, 0, 1, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
];

const int _ftcSchema = 4;
const int _arcCurveSchema = 3;
const int _ccurveSchema = 4;

/// A face with no explicit texture positioning stores no CFaceTextureCoords
/// at all, so this identity is only ever used to fill the *other* side's
/// slot when just one of front/back is explicitly positioned.
const List<double> _identityUvMatrix = [
  1.0, 0.0, 0.0, //
  0.0, 1.0, 0.0, //
  0.0, 0.0, 1.0,
];

List<int> _hexBytes(String hex) {
  final out = List<int>.filled(hex.length ~/ 2, 0);
  for (var i = 0; i < out.length; i++) {
    out[i] = int.parse(hex.substring(i * 2, i * 2 + 2), radix: 16);
  }
  return out;
}

// ── small binary/geometry helpers ──────────────────────────────────────────

List<int> _u16(int v) {
  final b = ByteData(2)..setUint16(0, v, Endian.little);
  return b.buffer.asUint8List();
}

List<int> _u32(int v) {
  final b = ByteData(4)..setUint32(0, v, Endian.little);
  return b.buffer.asUint8List();
}

List<int> _i32(int v) {
  final b = ByteData(4)..setInt32(0, v, Endian.little);
  return b.buffer.asUint8List();
}

List<int> _f64(double v) {
  final b = ByteData(8)..setFloat64(0, v, Endian.little);
  return b.buffer.asUint8List();
}

int _readU16At(List<int> buf, int pos) => buf[pos] | (buf[pos + 1] << 8);

void _patchU16(List<int> buf, int pos, int val) {
  buf[pos] = val & 0xFF;
  buf[pos + 1] = (val >> 8) & 0xFF;
}

void _patchU32(List<int> buf, int pos, int val) {
  buf[pos] = val & 0xFF;
  buf[pos + 1] = (val >> 8) & 0xFF;
  buf[pos + 2] = (val >> 16) & 0xFF;
  buf[pos + 3] = (val >> 24) & 0xFF;
}

/// Renumber the u16 archive slot-reference at [pos] in [buf] by [shift],
/// preserving the 0x8000 class-ref tag bit if the reference carries one.
///
/// Widens to the 6-byte escape form (same encoding
/// [_ArchiveWriter._newOfKnownClass]/[_ArchiveWriter._backref] use, and the
/// same `< 0x7FFF` boundary - see their comments) if the shifted slot would
/// land at or past 0x7FFF. The scaffold's own references always start
/// small enough to fit in 2 bytes on their own (it's a blank document), but
/// a large enough shift - a big model's total material/layer/definition/
/// geometry slot count - can push one past that boundary; masking it back
/// into 15 bits instead of widening would silently renumber it to the
/// wrong slot entirely, corrupting whatever it points to.
///
/// Returns the number of bytes the field grew by (0 or 4), so a caller
/// patching several positions in the same buffer can shift every position
/// after this one by the accumulated growth before acting on it.
int _shiftRef(List<int> buf, int pos, int shift) {
  final u16 = _readU16At(buf, pos);
  final tagBit = u16 & 0x8000;
  final slot = u16 & 0x7FFF;
  final newSlot = slot + shift;
  if (newSlot < 0x7FFF) {
    final packed = tagBit | newSlot;
    buf[pos] = packed & 0xFF;
    buf[pos + 1] = (packed >> 8) & 0xFF;
    return 0;
  }
  final val = tagBit != 0 ? (0x80000000 | newSlot) : newSlot;
  final replacement = [..._u16(0x7FFF), ..._u32(val)];
  buf.replaceRange(pos, pos + 2, replacement);
  return 4;
}

/// CDib's format tag for the two image formats this project has confirmed
/// via SDK ground truth - PNG and JPEG, both real SketchUp encodes as the
/// source file's bytes verbatim, distinguished only by this tag.
int _detectImageSubtype(Uint8List imageBytes) {
  if (imageBytes.length >= 8 &&
      imageBytes[0] == 0x89 &&
      imageBytes[1] == 0x50 &&
      imageBytes[2] == 0x4E &&
      imageBytes[3] == 0x47 &&
      imageBytes[4] == 0x0D &&
      imageBytes[5] == 0x0A &&
      imageBytes[6] == 0x1A &&
      imageBytes[7] == 0x0A) {
    return 4;
  }
  if (imageBytes.length >= 3 &&
      imageBytes[0] == 0xFF &&
      imageBytes[1] == 0xD8 &&
      imageBytes[2] == 0xFF) {
    return 1;
  }
  throw SkpWriteError(
    'unrecognized image format - only PNG and JPEG textures are supported '
    "for now (detected from the file's own magic bytes, not its extension)",
  );
}

/// Guards against silent corruption if the bundled scaffold is ever
/// swapped without updating [_tailRefPositions] and friends - those offsets
/// are specific to this exact file's bytes, not derived generically.
Uint8List _loadScaffold() {
  final data = base64Decode(blankV17ScaffoldBase64);
  final digest = sha256.convert(data).toString();
  if (digest != _scaffoldSha256) {
    throw SkpWriteError(
      'bundled blank-document scaffold does not match the expected content '
      "(hash mismatch) - openskp.create's tail-reference offsets are "
      'specific to the original scaffold file and would silently corrupt '
      'output against a different one',
    );
  }
  return data;
}

double _det3(List<List<double>> m) {
  return m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1]) -
      m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0]) +
      m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0]);
}

/// Solve the 3x3 linear system `a @ x = b` via Cramer's rule.
(double, double, double) _solve3x3(List<List<double>> a, List<double> b) {
  final d = _det3(a);
  if (d.abs() < 1e-9) {
    throw SkpWriteError(
      'the 3 texture-positioning points map to collinear (u, v) '
      'coordinates - cannot determine a texture mapping from them',
    );
  }
  final cols = <double>[];
  for (var col = 0; col < 3; col++) {
    final ai = [for (final row in a) [...row]];
    for (var r = 0; r < 3; r++) {
      ai[r][col] = b[r];
    }
    cols.add(_det3(ai) / d);
  }
  return (cols[0], cols[1], cols[2]);
}

Point3 _cross(Point3 a, Point3 b) => (
  a.$2 * b.$3 - a.$3 * b.$2,
  a.$3 * b.$1 - a.$1 * b.$3,
  a.$1 * b.$2 - a.$2 * b.$1,
);

Point3 _normalize3(Point3 v) {
  final length = sqrt(v.$1 * v.$1 + v.$2 * v.$2 + v.$3 * v.$3);
  if (length < 1e-9) {
    throw SkpWriteError(
      'cannot determine a texture-positioning basis: the geometry is degenerate',
    );
  }
  return (v.$1 / length, v.$2 / length, v.$3 / length);
}

/// The row-major 3x3 rotation matrix for rotating by [angle] radians
/// (right-hand rule) around [axis] (need not be a unit vector) - Rodrigues'
/// rotation formula. Same row-major convention `addInstance`'s own
/// `matrix3x3` parameter uses, so this is a drop-in way to get a rotation
/// without hand-deriving the matrix.
Matrix3x3 rotationMatrix3x3(Point3 axis, double angle) {
  final length = sqrt(axis.$1 * axis.$1 + axis.$2 * axis.$2 + axis.$3 * axis.$3);
  if (length < 1e-9) {
    throw SkpWriteError('rotation axis must not be the zero vector');
  }
  final x = axis.$1 / length, y = axis.$2 / length, z = axis.$3 / length;
  final c = cos(angle);
  final s = sin(angle);
  final t = 1.0 - c;
  return (
    t * x * x + c, t * x * y - s * z, t * x * z + s * y, //
    t * x * y + s * z, t * y * y + c, t * y * z - s * x, //
    t * x * z - s * y, t * y * z + s * x, t * z * z + c,
  );
}

/// Shared by every `addInstance`/`addGroup`/`addGroupInstance` call -
/// `matrix3x3` and `rotation` are alternate ways to specify the same
/// underlying transform field, not two separate ones, so at most one may
/// be given.
Matrix3x3? _resolveMatrix3x3(Matrix3x3? matrix3x3, Rotation? rotation) {
  if (matrix3x3 != null && rotation != null) {
    throw SkpWriteError(
      'pass at most one of matrix3x3/rotation - rotation is just a '
      'convenience for matrix3x3',
    );
  }
  if (rotation != null) {
    final (axis, angle) = rotation;
    return rotationMatrix3x3(axis, angle);
  }
  return matrix3x3;
}

/// The in-plane 2D basis (U, W) real SketchUp uses to parameterize a face's
/// texture mapping, for a face of ANY orientation - ground truth shows
/// it's simply the face's own first edge direction (`points[1] -
/// points[0]`, normalized) as U, and the plane normal crossed with that as
/// W - both unit vectors.
(Point3, Point3) _faceUvBasis(List<Point3> points, Point3 normal) {
  final u = _normalize3((
    points[1].$1 - points[0].$1,
    points[1].$2 - points[0].$2,
    points[1].$3 - points[0].$3,
  ));
  final w = _normalize3(_cross(normal, u));
  return (u, w);
}

/// An arbitrary orthonormal in-plane basis (U, W) for a circle/arc's plane,
/// given only its normal - pick whichever of world +Z/+X is less parallel
/// to [normal] as a seed and Gram-Schmidt it against [normal] to get U,
/// then W = normal x U.
(Point3, Point3) _circleBasis(Point3 normal) {
  final Point3 seed = normal.$3.abs() < 0.9 ? (0.0, 0.0, 1.0) : (1.0, 0.0, 0.0);
  final dot = seed.$1 * normal.$1 + seed.$2 * normal.$2 + seed.$3 * normal.$3;
  final Point3 uRaw = (
    seed.$1 - dot * normal.$1,
    seed.$2 - dot * normal.$2,
    seed.$3 - dot * normal.$3,
  );
  final u = _normalize3(uRaw);
  final w = _normalize3(_cross(normal, u));
  return (u, w);
}

/// The [numSegments] polygon vertices approximating a full circle, walking
/// counter-clockwise around [normal] (right-hand rule) starting at
/// `center + radius*u`.
List<Point3> _circlePoints(
  Point3 center,
  Point3 normal,
  double radius,
  int numSegments,
  Point3 u,
  Point3 w,
) {
  final pts = <Point3>[];
  for (var i = 0; i < numSegments; i++) {
    final angle = 2.0 * pi * i / numSegments;
    final c = cos(angle), s = sin(angle);
    pts.add((
      center.$1 + radius * (c * u.$1 + s * w.$1),
      center.$2 + radius * (c * u.$2 + s * w.$2),
      center.$3 + radius * (c * u.$3 + s * w.$3),
    ));
  }
  return pts;
}

/// The `numSegments + 1` points (both endpoints included) tracing a PARTIAL
/// arc from [startAngle] to [endAngle] (radians, measured from `u` toward
/// `w`) - a strict generalization of [_circlePoints].
List<Point3> _arcPoints(
  Point3 center,
  Point3 normal,
  double radius,
  int numSegments,
  Point3 u,
  Point3 w,
  double startAngle,
  double endAngle,
) {
  final pts = <Point3>[];
  for (var i = 0; i <= numSegments; i++) {
    final angle = startAngle + (endAngle - startAngle) * i / numSegments;
    final c = cos(angle), s = sin(angle);
    pts.add((
      center.$1 + radius * (c * u.$1 + s * w.$1),
      center.$2 + radius * (c * u.$2 + s * w.$2),
      center.$3 + radius * (c * u.$3 + s * w.$3),
    ));
  }
  return pts;
}

/// Fit the 3x3 UV-to-world affine matrix ground truth shows real SketchUp
/// stores for a positioned texture, from exactly 3 (world point, (u, v))
/// correspondences - the minimum that fully determines an affine map
/// (scale, rotation, shear, translation; no perspective term).
///
/// [basis] is the face's own (U, W) in-plane unit vectors (see
/// [_faceUvBasis]) - each correspondence's world point is projected onto
/// them via a plain dot product (no origin subtraction, ground-truth
/// confirmed).
///
/// Ground truth shows the stored matrix satisfies `(u, v, 1) @ M ==
/// (worldX, worldY, 1)` in row-vector convention - i.e. it maps a UV
/// coordinate to the world point it should land on.
List<double> _solveUvMatrix(
  List<(Point3, (double, double))> pairs,
  (Point3, Point3) basis,
) {
  if (pairs.length != 3) {
    throw SkpWriteError('texture positioning needs exactly 3 (point, uv) pairs');
  }
  final (uAxis, wAxis) = basis;
  final a = [for (final (_, uv) in pairs) [uv.$1, uv.$2, 1.0]];
  final bx = [
    for (final (pt, _) in pairs) pt.$1 * uAxis.$1 + pt.$2 * uAxis.$2 + pt.$3 * uAxis.$3,
  ];
  final by = [
    for (final (pt, _) in pairs) pt.$1 * wAxis.$1 + pt.$2 * wAxis.$2 + pt.$3 * wAxis.$3,
  ];
  final (a0, c0, e0) = _solve3x3(a, bx);
  final (b0, d0, f0) = _solve3x3(a, by);
  return [a0, b0, 0.0, c0, d0, 0.0, e0, f0, 1.0];
}

List<double> _uvMatrixForFace(
  List<Point3> points,
  List<(Point3, (double, double))> pairs,
  Point3 normal,
) {
  return _solveUvMatrix(pairs, _faceUvBasis(points, normal));
}

double _spanOf(List<Point3> points) {
  var maxSpan = 0.0;
  for (var axis = 0; axis < 3; axis++) {
    var mn = double.infinity, mx = double.negativeInfinity;
    for (final p in points) {
      final v = axis == 0 ? p.$1 : (axis == 1 ? p.$2 : p.$3);
      if (v < mn) mn = v;
      if (v > mx) mx = v;
    }
    final s = mx - mn;
    if (s > maxSpan) maxSpan = s;
  }
  return maxSpan;
}

/// Newell's method: sums a cross-product-like term over every edge rather
/// than reading the normal off just the first 3 points - that first-3-
/// points approach breaks for concave polygons whenever the first vertex
/// happens to be a reflex corner (wrong-signed normal). Newell's sum is the
/// polygon's true area-weighted normal regardless of convexity, as long as
/// it's planar and simple (non-self-intersecting).
(double, double, double, double) _planeFromPolygon(List<Point3> points) {
  final n = points.length;
  var nx = 0.0, ny = 0.0, nz = 0.0;
  for (var i = 0; i < n; i++) {
    final (x0, y0, z0) = points[i];
    final (x1, y1, z1) = points[(i + 1) % n];
    nx += (y0 - y1) * (z0 + z1);
    ny += (z0 - z1) * (x0 + x1);
    nz += (x0 - x1) * (y0 + y1);
  }
  final length = sqrt(nx * nx + ny * ny + nz * nz);
  if (length < 1e-9) {
    throw SkpWriteError('face points are collinear or degenerate; cannot compute a plane');
  }
  nx /= length;
  ny /= length;
  nz /= length;
  var cx = 0.0, cy = 0.0, cz = 0.0;
  for (final p in points) {
    cx += p.$1;
    cy += p.$2;
    cz += p.$3;
  }
  cx /= n;
  cy /= n;
  cz /= n;
  final d = nx * cx + ny * cy + nz * cz;

  // Every point must actually lie on the fitted plane - a mesh built from
  // slightly-off-plane input would otherwise silently warp instead of
  // failing loudly. Tolerance scales with the face's own size.
  final span = _spanOf(points);
  final tol = (span > 1.0 ? span : 1.0) * 1e-6;
  for (final p in points) {
    final dist = nx * p.$1 + ny * p.$2 + nz * p.$3 - d;
    if (dist.abs() > tol) {
      throw SkpWriteError(
        'face points are not coplanar (point $p is ${dist.abs()} units off '
        'the fitted plane) - openskp.create only supports planar faces',
      );
    }
  }
  return (nx, ny, nz, d);
}

/// Same fit/tolerance [_planeFromPolygon] uses, but returns a bool for "not
/// coplanar" instead of raising - used by `addFace`'s `autoTriangulate` to
/// decide whether a fan-triangulation fallback is even needed. Still
/// raises for a collinear/degenerate input (no triangulation fixes that).
bool _isCoplanar(List<Point3> points) {
  final n = points.length;
  var nx = 0.0, ny = 0.0, nz = 0.0;
  for (var i = 0; i < n; i++) {
    final (x0, y0, z0) = points[i];
    final (x1, y1, z1) = points[(i + 1) % n];
    nx += (y0 - y1) * (z0 + z1);
    ny += (z0 - z1) * (x0 + x1);
    nz += (x0 - x1) * (y0 + y1);
  }
  final length = sqrt(nx * nx + ny * ny + nz * nz);
  if (length < 1e-9) {
    throw SkpWriteError('face points are collinear or degenerate; cannot compute a plane');
  }
  nx /= length;
  ny /= length;
  nz /= length;
  var cx = 0.0, cy = 0.0, cz = 0.0;
  for (final p in points) {
    cx += p.$1;
    cy += p.$2;
    cz += p.$3;
  }
  cx /= n;
  cy /= n;
  cz /= n;
  final d = nx * cx + ny * cy + nz * cz;
  final span = _spanOf(points);
  final tol = (span > 1.0 ? span : 1.0) * 1e-6;
  return points.every((p) => (nx * p.$1 + ny * p.$2 + nz * p.$3 - d).abs() <= tol);
}

final Random _rnd = Random.secure();

/// A random RFC-4122-shaped v4 UUID's 16 raw bytes, matching
/// `uuid.uuid4().bytes` in Python - this format has no strict validator on
/// the SketchUp side, but setting the version/variant bits keeps the value
/// a genuine UUID rather than 16 arbitrary bytes.
List<int> _uuid4Bytes() {
  final bytes = List<int>.generate(16, (_) => _rnd.nextInt(256));
  bytes[6] = (bytes[6] & 0x0F) | 0x40;
  bytes[8] = (bytes[8] & 0x3F) | 0x80;
  return bytes;
}

List<(String, Map<String, Object>)> _attrDicts(Map<String, Object>? attributes, String name) {
  if (attributes == null || attributes.isEmpty) return const [];
  return [(name, attributes)];
}

(int, int) _edgeKey(int a, int b) => a <= b ? (a, b) : (b, a);

// ── the write-side archive mirror ───────────────────────────────────────────

/// Write-side mirror of [Archive]'s slot/class-ref bookkeeping - emits the
/// same MFC `CArchive` tag protocol (`0xFFFF` new-class, `0x8000|slot`
/// short class-ref, plain u16 back-ref) that [legacy] decodes, inverted for
/// writing.
class _ArchiveWriter {
  int nextSlot;
  final Map<String, int> classSlot;
  int nextPid;
  final List<int> buf = <int>[];

  _ArchiveWriter({
    required this.nextSlot,
    required Map<String, int> classSlot,
    // ignore: unused_element_parameter
    this.nextPid = 1,
  }) : classSlot = Map<String, int>.from(classSlot);

  int _alloc() {
    final s = nextSlot;
    nextSlot += 1;
    return s;
  }

  int _allocPid() {
    final p = nextPid;
    nextPid += 1;
    return p;
  }

  int _newOfKnownClass(String className, {int? schema}) {
    if (!classSlot.containsKey(className)) {
      if (schema == null) {
        throw SkpWriteError('$className not yet declared and no schema given');
      }
      buf.addAll(_u16(0xFFFF));
      buf.addAll(_u16(schema));
      buf.addAll(_u16(className.length));
      buf.addAll(ascii.encode(className));
      classSlot[className] = _alloc();
      return _alloc();
    }
    final slot = classSlot[className]!;
    // slot == 0x7FFF is deliberately excluded from the short form even
    // though it numerically fits in 15 bits: 0x8000 | 0x7FFF == 0xFFFF,
    // which Archive.readObject checks for "new class declaration" BEFORE
    // it ever checks the class-ref high bit - a class landing at exactly
    // that slot would be silently misinterpreted as the start of a bogus
    // class record, desyncing every read after it. The escape form has no
    // such collision.
    if (slot < 0x7FFF) {
      buf.addAll(_u16(0x8000 | slot));
    } else {
      buf.addAll(_u16(0x7FFF));
      buf.addAll(_u32(0x80000000 | slot));
    }
    return _alloc();
  }

  void _null() {
    buf.addAll(_u16(0));
  }

  /// Same exclusion as [_newOfKnownClass], for the plain (no class-ref bit)
  /// case: a bare slot value of 0x7FFF is indistinguishable from the
  /// big-tag escape marker itself.
  void _backref(int slot) {
    if (slot < 0x7FFF) {
      buf.addAll(_u16(slot));
    } else {
      buf.addAll(_u16(0x7FFF));
      buf.addAll(_u32(slot));
    }
  }

  List<int> _encodePid(int pid) {
    var mask = 0;
    final pidBytes = <int>[];
    for (var bit = 0; bit < 8; bit++) {
      final byteVal = (pid >> (8 * bit)) & 0xFF;
      if (byteVal != 0) {
        mask |= 1 << bit;
        pidBytes.add(byteVal);
      }
    }
    return [mask, ...pidBytes];
  }

  void _preamble({int? pid, bool realAttrs = false}) {
    if (realAttrs) {
      // Ground truth: CComponentDefinition and CComponentInstance both
      // reference a real (but childless) CAttributeContainer here instead
      // of the null pointer every other entity uses - CAttributeContainer's
      // class is pre-existing in the scaffold's prefix, same pattern as
      // _ccameraSlot.
      buf.addAll(_u16(0x8000 | _attrContainerSlot));
      _alloc(); // a class-ref always allocates a new object slot
      buf.addAll([0, 0, 0]); // container's own preamble: null attrs + mask=0
      buf.addAll(_u16(0)); // empty children-list terminator
    } else {
      _null(); // no CAttributeContainer
    }
    final p = pid ?? _allocPid();
    buf.addAll(_encodePid(p));
  }

  /// Like [_preamble] with `realAttrs: true`, but the attribute container's
  /// children list holds real content instead of closing immediately: an
  /// optional `CFaceTextureCoords` (faces with explicit texture positioning
  /// only) followed by zero or more named `CAttributeNamed` dictionaries.
  void _preambleWithRealAttrs({
    List<double>? frontMatrix,
    List<double>? backMatrix,
    List<(String, Map<String, Object>)> attributeDicts = const [],
    int? pid,
  }) {
    buf.addAll(_u16(0x8000 | _attrContainerSlot));
    _alloc();
    buf.addAll([0, 0, 0]);
    if (frontMatrix != null || backMatrix != null) {
      writeFaceTextureCoords(frontMatrix, backMatrix);
    }
    for (final (dictName, entries) in attributeDicts) {
      writeAttributeDict(dictName, entries);
    }
    _null(); // children-list terminator
    final p = pid ?? _allocPid();
    buf.addAll(_encodePid(p));
  }

  /// Shares [writeAttributeDict]'s own exact validation rules so a caller
  /// can check every attribute dict a multi-part write will need BEFORE
  /// that write starts mutating [buf] (and any shared vertex/edge-sharing
  /// maps a caller passes in) - an unvalidated write partway through would
  /// otherwise leave orphaned, uncounted edges silently corrupting the rest
  /// of the file (see `openskp.edit`'s replay, which relies on this).
  void _validateAttributeEntries(Map<String, Object> entries) {
    for (final entry in entries.entries) {
      final key = entry.key;
      final value = entry.value;
      if (value is String) continue;
      if (value is bool) {
        throw SkpWriteError(
          "attribute '$key': bool is not a supported value type - use an "
          'int (0/1) if you need a boolean-like flag',
        );
      }
      if (value is int) {
        if (value < -(1 << 31) || value >= (1 << 31)) {
          throw SkpWriteError("attribute '$key': int value $value out of signed 32-bit range");
        }
        continue;
      }
      if (value is double) continue;
      throw SkpWriteError(
        "attribute '$key': unsupported value type ${value.runtimeType} "
        '(only String, int, and double are supported for now)',
      );
    }
  }

  /// Write one `CAttributeNamed` record - a named dictionary of custom
  /// key/value metadata attached to an entity's real attribute container
  /// (the same mechanism SketchUp's own "dynamic component" attributes
  /// use). Unlike every other class this writer declares, `CAttributeNamed`
  /// is already pre-declared in the scaffold's own prefix, so this always
  /// writes a short class-ref to [_attributeNamedSlot], never a fresh
  /// `0xFFFF` declaration.
  void writeAttributeDict(String dictName, Map<String, Object> entries) {
    buf.addAll(_u16(0x8000 | _attributeNamedSlot));
    _alloc();
    buf.addAll([0, 0, 0]); // this dict's own preamble: null attrs + mask=0, pid=0
    buf.addAll(_u32(0)); // ground truth: read and discarded by the reader too
    _validateAttributeEntries(entries);
    _writeStr(dictName);
    for (final entry in entries.entries) {
      _writeStr(entry.key);
      final value = entry.value;
      if (value is String) {
        buf.add(_attrTypeString);
        _writeStr(value);
      } else if (value is int) {
        buf.add(_attrTypeInt32);
        buf.addAll(_i32(value));
      } else {
        buf.add(_attrTypeDouble);
        buf.addAll(_f64((value as num).toDouble()));
      }
    }
    _writeStr(''); // empty-key terminator
    buf.addAll(_u32(0)); // ground truth: read and discarded by the reader too
  }

  /// Write one `CFaceTextureCoords` record - the explicit front/back
  /// texture-positioning data a face's attribute container holds when
  /// either side has been explicitly positioned. [frontMatrix]/[backMatrix]
  /// are the 9-value row-major UV-to-world affine matrices from
  /// [_uvMatrixForFace], or null for a side that isn't explicitly
  /// positioned (written as identity, matching ground truth).
  void writeFaceTextureCoords(List<double>? frontMatrix, List<double>? backMatrix) {
    _newOfKnownClass('CFaceTextureCoords', schema: _ftcSchema);
    _preamble(pid: 0);
    buf.addAll(_u32(0)); // ground truth: read and discarded by the reader too
    final ks = List<double>.filled(24, 0.0);
    final fm = frontMatrix ?? _identityUvMatrix;
    final bm = backMatrix ?? _identityUvMatrix;
    for (var i = 0; i < 9; i++) {
      ks[i] = fm[i];
    }
    for (var i = 0; i < 9; i++) {
      ks[12 + i] = bm[i];
    }
    for (final v in ks) {
      buf.addAll(_f64(v));
    }
    buf.addAll(_u32(0)); // front pin count - this writer always emits a solved matrix
    buf.addAll(_u32(0)); // back pin count
    buf.addAll(_u32(frontMatrix != null ? 1 : 0)); // fflags bit 0: front painted
    buf.addAll(_u32(backMatrix != null ? 1 : 0)); // bflags bit 0: back painted
  }

  void _drawbase({int mat = 0, int layer = 0, bool hidden = false, bool soft = false, bool smooth = false}) {
    final b = List<int>.filled(10, 0);
    final matBytes = _u16(mat);
    b[0] = matBytes[0];
    b[1] = matBytes[1];
    b[2] = hidden ? 1 : 0;
    // offsets 3-4: the reader treats these as unused padding, but real
    // SketchUp silently drops any entity whose drawbase has them zeroed -
    // ground-truth-confirmed by diffing real SDK-authored files. Must be
    // 1, 1.
    b[3] = 1;
    b[4] = 1;
    b[5] = soft ? 1 : 0;
    b[6] = smooth ? 1 : 0;
    final layerBytes = _u16(layer);
    b[8] = layerBytes[0];
    b[9] = layerBytes[1];
    buf.addAll(b);
  }

  int _writeVertex(Point3 point) {
    final slot = _newOfKnownClass('CVertex', schema: 0);
    _preamble();
    buf.addAll(_f64(point.$1));
    buf.addAll(_f64(point.$2));
    buf.addAll(_f64(point.$3));
    return slot;
  }

  /// Write one `CArcCurve` record and return its slot - the shared
  /// geometric-parameter object a circle/arc's straight `CEdge` segments
  /// each carry a backref to, so real SketchUp recognizes the result as a
  /// true circle/arc (editable by radius, re-tessellatable) rather than N
  /// disconnected straight edges that merely happen to form that shape.
  ///
  /// [xaxis] is the arc's own fixed 0-angle reference direction (a unit
  /// vector times radius, in the plane perpendicular to [normal]) -
  /// [startAngle]/[endAngle] (radians) are offsets from it. Two of the 14
  /// stored values (ground truth offsets 11 and 13) were 0 in every sample
  /// tested and are written as 0 here too; their meaning hasn't been
  /// reverse-engineered.
  int writeArcCurve(
    Point3 center,
    Point3 normal,
    Point3 xaxis,
    double startAngle,
    double endAngle,
    double radius,
    int numSegments,
  ) {
    if (numSegments < 0 || numSegments > 0xFF) {
      throw SkpWriteError('num_segments must be between 0 and 255, got $numSegments');
    }
    final slot = _newOfKnownClass('CArcCurve', schema: _arcCurveSchema);
    _preamble();
    buf.addAll([0, numSegments, 0, 0, 0]);
    for (final v in [
      center.$1, center.$2, center.$3,
      normal.$1, normal.$2, normal.$3,
      xaxis.$1, xaxis.$2, xaxis.$3,
      startAngle, endAngle, 0.0, radius, 0.0,
    ]) {
      buf.addAll(_f64(v));
    }
    return slot;
  }

  /// Write one `CCurve` record and return its slot - a freeform polyline
  /// curve grouping (as opposed to `CArcCurve`'s arc geometry): a labeled
  /// set of already-straight `CEdge` segments, with no geometric data of
  /// its own beyond how many edges share it. Ground truth: the record is
  /// just a constant byte `1` (open or closed, meaning beyond a "this is a
  /// curve" tag not reverse-engineered) followed by [numEdges] as a u32.
  int writeCurve(int numEdges) {
    final slot = _newOfKnownClass('CCurve', schema: _ccurveSchema);
    _preamble();
    buf.add(1);
    buf.addAll(_u32(numEdges));
    return slot;
  }

  void _writeStr(String s) {
    final units = s.codeUnits; // UTF-16 code units == UTF-16LE encoding, 2 bytes each
    final n = units.length;
    if (n >= 0xFF) {
      throw SkpWriteError('string too long to encode (255 char limit)');
    }
    buf.addAll([0xFF, 0xFE, 0xFF, n]);
    for (final u in units) {
      buf.addAll(_u16(u));
    }
  }

  /// Write one solid-color `CMaterial` record and return its slot.
  int writeMaterial(String name, (int, int, int, int) rgba) {
    final slot = _newOfKnownClass('CMaterial', schema: _materialSchema);
    _preamble();
    _writeStr(name);
    buf.addAll(_u16(0)); // texflag: solid color, no texture
    buf.addAll([rgba.$1, rgba.$2, rgba.$3, rgba.$4]);
    _writeStr(''); // texture path (empty - no texture)
    buf.addAll(List<int>.filled(8, 0)); // unknown/padding - ground truth is all-zero
    buf.addAll(_f64(1.0)); // opacity
    buf.add(0); // use_opacity = False (alpha carries transparency instead)
    return slot;
  }

  /// Write one image-textured `CMaterial` record (embedding [imageBytes]
  /// verbatim inside a `CDib` sub-object) and return its slot. [subtype] is
  /// CDib's image format tag (4 for PNG, 1 for JPEG - see
  /// [_detectImageSubtype]).
  int writeTexturedMaterial(String name, Uint8List imageBytes, String texturePath, int subtype) {
    final slot = _newOfKnownClass('CMaterial', schema: _materialSchema);
    _preamble();
    _writeStr(name);
    buf.addAll(_u16(1)); // texflag: textured
    buf.addAll([0, 0]); // texture-flag pad (v17+)
    _newOfKnownClass('CDib', schema: _dibSchema);
    buf.addAll(_u32(subtype));
    buf.addAll(_u32(imageBytes.length));
    buf.addAll(imageBytes);
    if (subtype == 1) {
      // JPEG only: one extra u32 real SketchUp always writes here - ground-
      // truth confirmed constant 90 regardless of the source JPEG's own
      // actual encoded quality.
      buf.addAll(_u32(90));
    }
    buf.addAll(_f64(1.0)); // applied width - ground truth default when unscaled
    buf.addAll(_textureHSentinel);
    _writeStr(texturePath);
    // avg color (RGBA + pad + RGBA repeated) - neutral near-opaque white
    // rather than a real image average, since this project doesn't depend
    // on an image library to compute one. Alpha is 254, not a fully-opaque
    // 255: the reader treats alpha=255 here as one of its two "this
    // material is colorized" signals (alongside the blob flag below) - a
    // plain material's placeholder must not trip that, or every plain
    // texture this writer creates reads back as falsely colorized.
    buf.addAll([255, 255, 255, 254, 0, 255, 255, 255, 254]);
    _writeStr(''); // second name field - empty in ground truth
    buf.addAll(_u32(1)); // blob (colorize-related, ground truth: 1, 0)
    buf.addAll(_u32(0));
    buf.addAll(_f64(1.0)); // opacity
    buf.add(0); // use_opacity = False
    return slot;
  }

  /// Write one `CLayer` record and return its slot. CLayer is always
  /// already declared (the scaffold's Layer0 guarantees it), so this never
  /// emits a new-class declaration - only a short class-ref.
  ///
  /// Ground truth shows each top-level layer record contains a second,
  /// embedded pid (inside a 5-byte block after the visible name) - so each
  /// layer consumes 2 pids, not 1. `withPids: false` (used only for the
  /// layer a component definition embeds internally) omits both.
  int writeLayer(String name, {bool withPids = true, bool hidden = false, (int, int, int, int)? rgba}) {
    final slot = _newOfKnownClass('CLayer', schema: LayerSchema.value);
    _preamble(pid: withPids ? null : 0);
    _writeStr(name);
    final pid2 = withPids ? _allocPid() : 0;
    // byte 0 is the hidden flag, bytes 1-2 are always zero (ground truth)
    buf.addAll([hidden ? 1 : 0, 0, 0]);
    buf.addAll(_encodePid(pid2));
    _writeStr('Layer_$name');
    buf.addAll(_u16(256)); // ground truth is a constant 256 here
    if (rgba != null) {
      buf.addAll([rgba.$1, rgba.$2, rgba.$3, rgba.$4]);
    } else {
      buf.addAll(List<int>.filled(4, 0));
    }
    _writeStr(''); // second name field - empty in ground truth
    buf.addAll(List<int>.filled(8, 0));
    buf.addAll(_f64(0.5)); // 21-byte tail, opacity-like f64=0.5
    buf.addAll(List<int>.filled(5, 0));
    return slot;
  }

  /// Write a `CThumbnail` with a default camera and no image - ground
  /// truth shows the image itself is optional.
  void writeThumbnail() {
    _newOfKnownClass('CThumbnail', schema: _thumbnailSchema);
    _preamble(pid: 0); // structural container: ground truth carries no pid
    buf.addAll(_u16(0x8000 | _ccameraSlot));
    _alloc();
    buf.addAll(_cameraTemplate);
    _null(); // no thumbnail image
  }

  /// Begin a `CComponentDefinition` record - everything up to (not
  /// including) its internal entity list. Returns `(definitionSlot,
  /// countPatchPos)`: the caller writes the definition's geometry directly
  /// into [buf], then must patch a u32 entity count at `countPatchPos` and
  /// call [writeDefinitionTail] to close it out.
  (int, int) writeDefinitionHeader([List<(String, Map<String, Object>)> attributeDicts = const []]) {
    final slot = _newOfKnownClass('CComponentDefinition', schema: _definitionSchema);
    if (attributeDicts.isNotEmpty) {
      _preambleWithRealAttrs(attributeDicts: attributeDicts);
    } else {
      _preamble(realAttrs: true); // ground truth: a real pid and a real (empty) attr container
    }
    buf.addAll(_definitionBaseBlock);
    buf.addAll(_u32(1)); // nlayers: always 1, an embedded copy of Layer0
    final embeddedLayerSlot = writeLayer('Layer0', withPids: false);
    _backref(embeddedLayerSlot); // "decl": this definition's own active layer
    // A separate field from nested instances (which live in the entity
    // list just below): ground truth shows this counts CComponentDefinition
    // classes declared INLINE within this definition's own header, a
    // distinct construct this writer never produces - every definition is
    // declared at the top level, so this stays 0.
    buf.addAll(_u32(0));
    final countPatchPos = buf.length;
    buf.addAll(_u32(0)); // placeholder entity count, patched by the caller
    return (slot, countPatchPos);
  }

  /// Close out a `CComponentDefinition` record: relationship count, GUID,
  /// name, timestamp, behavior flags, and a default thumbnail.
  void writeDefinitionTail(String name) {
    buf.addAll(_u32(0)); // nrel: CRelationship count - always 0, not supported
    buf.addAll(_u16(0));
    buf.addAll(_uuid4Bytes());
    _writeStr(name);
    _writeStr(''); // description - empty in ground truth
    _writeStr(''); // second name field - empty in ground truth
    buf.addAll(_u32(DateTime.now().toUtc().millisecondsSinceEpoch ~/ 1000));
    // 43-byte gap; byte -9 carries the always-faces-camera/shadows-face-sun
    // behavior flags, both left off (neither exposed by this writer yet).
    buf.addAll(List<int>.filled(43, 0));
    writeThumbnail();
  }

  void _writeInstanceLike({
    required String className,
    required int schema,
    required bool realAttrs,
    required int definitionSlot,
    required String name,
    required Point3 translation,
    Matrix3x3? matrix3x3,
    required int mat,
    required int layer,
    List<(String, Map<String, Object>)> attributeDicts = const [],
    bool hidden = false,
  }) {
    _newOfKnownClass(className, schema: schema);
    if (realAttrs && attributeDicts.isNotEmpty) {
      _preambleWithRealAttrs(attributeDicts: attributeDicts);
    } else {
      _preamble(realAttrs: realAttrs);
    }
    _drawbase(mat: mat, layer: layer, hidden: hidden);
    _backref(definitionSlot);
    final Matrix3x3 m = matrix3x3 ?? (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0);
    for (final v in [
      m.$1, m.$2, m.$3, m.$4, m.$5, m.$6, m.$7, m.$8, m.$9,
      translation.$1, translation.$2, translation.$3,
      1.0,
    ]) {
      buf.addAll(_f64(v));
    }
    _writeStr(name);
    buf.addAll(_uuid4Bytes());
  }

  /// Write one `CComponentInstance` placing a copy of [definitionSlot] and
  /// return how many new root-entity-list slots it consumed - always 1.
  /// Ground truth shows the file's transform encoding is exactly a 3x3
  /// matrix (9 f64s) + translation (3 f64s) + a trailing 1.0 - the 4th row
  /// of a standard 4x4 affine matrix ([0, 0, 0, 1]) is omitted entirely.
  int writeInstance(
    int definitionSlot,
    String name,
    Point3 translation,
    Matrix3x3? matrix3x3,
    int instanceMaterial,
    int instanceLayer, {
    List<(String, Map<String, Object>)> attributeDicts = const [],
    bool hidden = false,
  }) {
    // ground truth: instances also carry a real (empty) attr container,
    // unlike CGroup
    _writeInstanceLike(
      className: 'CComponentInstance',
      schema: _instanceSchema,
      realAttrs: true,
      definitionSlot: definitionSlot,
      name: name,
      translation: translation,
      matrix3x3: matrix3x3,
      mat: instanceMaterial,
      layer: instanceLayer,
      attributeDicts: attributeDicts,
      hidden: hidden,
    );
    return 1;
  }

  /// Write one `CGroup` placing a copy of [definitionSlot] and return how
  /// many new root-entity-list slots it consumed - always 1, same contract
  /// as [writeInstance].
  ///
  /// A group is structurally almost identical to a component instance -
  /// the two real differences are its class name/schema (CGroup, schema 1)
  /// and that it uses a plain null attribute pointer rather than the real
  /// (empty) CAttributeContainer instances need.
  int writeGroup(
    int definitionSlot,
    String name,
    Point3 translation,
    Matrix3x3? matrix3x3,
    int groupMaterial,
    int groupLayer, [
    bool hidden = false,
  ]) {
    _writeInstanceLike(
      className: 'CGroup',
      schema: _groupSchema,
      realAttrs: false,
      definitionSlot: definitionSlot,
      name: name,
      translation: translation,
      matrix3x3: matrix3x3,
      mat: groupMaterial,
      layer: groupLayer,
      hidden: hidden,
    );
    return 1;
  }

  /// Write a chain of straight `CEdge` records connecting [points] in
  /// order, sharing vertices/edges via [vertexSlots]/[edgeRegistry] exactly
  /// like [writeFace] (which uses this for its own, always-closed polygon
  /// boundary). `closed: true` also connects the last point back to the
  /// first; `closed: false` stops after the last pair.
  ///
  /// Returns `(edgeSlots, edgeSenses, newEntities)` - the last is how many
  /// new root-entity-list slots were consumed (edges newly declared; the
  /// caller adds any of its own, e.g. a face record).
  ///
  /// At most one of [curveParams]/[polylineNumEdges] should be given - both
  /// describe the SAME first-use-inline-declaration pattern (ground truth
  /// shows the shared curve object is declared inline as the FIRST newly-
  /// declared edge's own "curve" field, and every other edge newly declared
  /// by this call backrefs that same slot instead of writing a null curve).
  (List<int>, List<int>, int) _writeEdgeChain(
    List<Point3> points,
    Map<Point3, int> vertexSlots,
    Map<(int, int), (int, int)> edgeRegistry,
    bool closed, {
    bool hiddenEdges = false,
    bool softEdges = false,
    bool smoothEdges = false,
    CurveParams? curveParams,
    int? polylineNumEdges,
  }) {
    final n = points.length;
    final pairCount = closed ? n : n - 1;
    final pointSlots = List<int?>.generate(n, (i) => vertexSlots[points[i]]);
    final edgeSlots = <int>[];
    final edgeSenses = <int>[];
    var newEntities = 0;
    int? curveSlot;

    for (var i = 0; i < pairCount; i++) {
      final v1Idx = i;
      final v2Idx = (i + 1) % n;
      final v1Known = pointSlots[v1Idx];
      final v2Known = pointSlots[v2Idx];
      final key = (v1Known != null && v2Known != null) ? _edgeKey(v1Known, v2Known) : null;
      if (key != null && edgeRegistry.containsKey(key)) {
        final (edgeSlot, fwdV1) = edgeRegistry[key]!;
        edgeSlots.add(edgeSlot);
        edgeSenses.add(fwdV1 == v1Known ? 0 : 1);
        continue;
      }

      final edgeSlot = _newOfKnownClass('CEdge', schema: 2);
      _preamble();
      _drawbase(hidden: hiddenEdges, soft: softEdges, smooth: smoothEdges);
      for (final idx in [v1Idx, v2Idx]) {
        if (pointSlots[idx] == null) {
          final s = _writeVertex(points[idx]);
          pointSlots[idx] = s;
          vertexSlots[points[idx]] = s;
        } else {
          _backref(pointSlots[idx]!);
        }
      }
      if (curveSlot != null) {
        _backref(curveSlot);
      } else if (curveParams != null) {
        final (center, normal, xaxis, startAngle, endAngle, radius, numSegments) = curveParams;
        curveSlot = writeArcCurve(center, normal, xaxis, startAngle, endAngle, radius, numSegments);
      } else if (polylineNumEdges != null) {
        curveSlot = writeCurve(polylineNumEdges);
      } else {
        _null(); // curve = None
      }
      edgeSlots.add(edgeSlot);
      edgeSenses.add(0);
      newEntities += 1;
      edgeRegistry[_edgeKey(pointSlots[v1Idx]!, pointSlots[v2Idx]!)] = (edgeSlot, pointSlots[v1Idx]!);
    }

    return (edgeSlots, edgeSenses, newEntities);
  }

  /// Write a partial (open) arc as a chain of straight `CEdge` records - no
  /// face, unlike [writeFace]'s always-closed polygon boundary. [points]
  /// are the `numSegments + 1` points along the arc in order (see
  /// [_arcPoints]). Returns how many new root-entity-list slots were
  /// consumed.
  int writeArc(
    List<Point3> points,
    Map<Point3, int> vertexSlots,
    Map<(int, int), (int, int)> edgeRegistry,
    CurveParams curveParams, {
    bool hiddenEdges = false,
    bool softEdges = false,
    bool smoothEdges = false,
  }) {
    final (_, _, newEntities) = _writeEdgeChain(
      points,
      vertexSlots,
      edgeRegistry,
      false,
      hiddenEdges: hiddenEdges,
      softEdges: softEdges,
      smoothEdges: smoothEdges,
      curveParams: curveParams,
    );
    return newEntities;
  }

  /// Write a freeform polyline curve - a chain of straight `CEdge` records
  /// connecting [points] in order, all sharing one `CCurve` grouping, no
  /// face. Distinct from [writeArc]: there's no geometric arc frame here,
  /// just a labeled set of already-straight edges.
  int writePolyline(
    List<Point3> points,
    Map<Point3, int> vertexSlots,
    Map<(int, int), (int, int)> edgeRegistry, {
    bool closed = false,
    bool hiddenEdges = false,
    bool softEdges = false,
    bool smoothEdges = false,
  }) {
    final n = points.length;
    final pairCount = closed ? n : n - 1;
    final (_, _, newEntities) = _writeEdgeChain(
      points,
      vertexSlots,
      edgeRegistry,
      closed,
      hiddenEdges: hiddenEdges,
      softEdges: softEdges,
      smoothEdges: smoothEdges,
      polylineNumEdges: pairCount,
    );
    return newEntities;
  }

  /// Write one planar face and return how many new root-entity-list slots
  /// it consumed (edges newly declared, plus the face itself).
  ///
  /// [points] form a closed polygon in order (do not repeat the first point
  /// at the end). Vertices and edges are shared automatically across calls
  /// via [vertexSlots]/[edgeRegistry] wherever coordinates coincide
  /// exactly. Edges always keep drawbase mat=0/layer=0 even when their face
  /// has a material or layer - ground truth confirms this for both fields.
  ///
  /// [frontUv]/[backUv], if given, explicitly position that side's texture
  /// instead of the default planar projection - exactly 3 (point, (u, v))
  /// pairs, which fully determines an affine mapping. [holes], if given, is
  /// a sequence of independent closed polygons cut out of the face - ground
  /// truth shows a hole is just another `CLoop` with its own flag byte (`0`
  /// instead of `1`) marking it as a hole rather than the boundary.
  int writeFace(
    List<Point3> points,
    Map<Point3, int> vertexSlots,
    Map<(int, int), (int, int)> edgeRegistry, {
    int faceMaterial = 0,
    int faceLayer = 0,
    int backMaterial = 0,
    bool hidden = false,
    bool softEdges = false,
    bool smoothEdges = false,
    bool hiddenEdges = false,
    List<(Point3, (double, double))>? frontUv,
    List<(Point3, (double, double))>? backUv,
    List<(String, Map<String, Object>)> attributeDicts = const [],
    CurveParams? curveParams,
    List<List<Point3>> holes = const [],
  }) {
    // Validate everything that CAN fail (a degenerate UV correspondence, an
    // unsupported attribute value, an off-plane hole) before writing a
    // single byte or touching vertexSlots/edgeRegistry below -
    // _writeEdgeChain mutates both this writer's own buffer AND those
    // caller-owned, shared-across-calls maps as it goes, with no rollback
    // if something later in this method throws.
    final (nx, ny, nz, d) = _planeFromPolygon(points);
    final frontMatrix = frontUv != null ? _uvMatrixForFace(points, frontUv, (nx, ny, nz)) : null;
    final backMatrix = backUv != null ? _uvMatrixForFace(points, backUv, (nx, ny, nz)) : null;
    for (final (_, entries) in attributeDicts) {
      _validateAttributeEntries(entries);
    }
    final span = _spanOf(points);
    final tol = (span > 1.0 ? span : 1.0) * 1e-6;
    for (final hole in holes) {
      if (hole.length < 3) {
        throw SkpWriteError('a hole needs at least 3 points');
      }
      for (final p in hole) {
        final dist = nx * p.$1 + ny * p.$2 + nz * p.$3 - d;
        if (dist.abs() > tol) {
          throw SkpWriteError(
            "hole point $p is ${dist.abs()} units off the face's own plane - "
            'a hole must lie on the same plane as the outer boundary',
          );
        }
      }
    }

    final (edgeSlots, edgeSenses, newEntities0) = _writeEdgeChain(
      points,
      vertexSlots,
      edgeRegistry,
      true,
      hiddenEdges: hiddenEdges,
      softEdges: softEdges,
      smoothEdges: smoothEdges,
      curveParams: curveParams,
    );
    var newEntities = newEntities0;
    final holeLoops = <(List<int>, List<int>)>[];
    for (final hole in holes) {
      final (hEdgeSlots, hEdgeSenses, hNew) = _writeEdgeChain(
        hole,
        vertexSlots,
        edgeRegistry,
        true,
        hiddenEdges: hiddenEdges,
        softEdges: softEdges,
        smoothEdges: smoothEdges,
      );
      holeLoops.add((hEdgeSlots, hEdgeSenses));
      newEntities += hNew;
    }

    _newOfKnownClass('CFace', schema: 3);
    if (frontUv != null || backUv != null || attributeDicts.isNotEmpty) {
      _preambleWithRealAttrs(frontMatrix: frontMatrix, backMatrix: backMatrix, attributeDicts: attributeDicts);
    } else {
      _preamble();
    }
    _drawbase(mat: faceMaterial, layer: faceLayer, hidden: hidden);
    buf.addAll(_f64(nx));
    buf.addAll(_f64(ny));
    buf.addAll(_f64(nz));
    buf.addAll(_f64(d));
    buf.addAll(_u32(1 + holes.length)); // nloops

    final loopSlot = _newOfKnownClass('CLoop', schema: 1);
    _preamble(pid: 0); // structural object: ground truth uses pid 0
    // the reader treats these 2 bytes as opaque, but real SketchUp
    // requires 01 01, not 00 00 - same silent-drop failure mode as the
    // drawbase padding above.
    buf.addAll([1, 1]);
    for (var i = 0; i < edgeSlots.length; i++) {
      _newOfKnownClass('CEdgeUse', schema: 1);
      _preamble(pid: 0);
      _backref(edgeSlots[i]);
      buf.add(edgeSenses[i]);
      _backref(loopSlot);
    }
    _null(); // loop terminator

    for (final (hEdgeSlots, hEdgeSenses) in holeLoops) {
      final hLoopSlot = _newOfKnownClass('CLoop', schema: 1);
      _preamble(pid: 0);
      buf.addAll([0, 1]); // ground truth: 0 marks a hole loop, not the boundary
      for (var i = 0; i < hEdgeSlots.length; i++) {
        _newOfKnownClass('CEdgeUse', schema: 1);
        _preamble(pid: 0);
        _backref(hEdgeSlots[i]);
        buf.add(hEdgeSenses[i]);
        _backref(hLoopSlot);
      }
      _null();
    }

    buf.addAll(_u16(backMaterial));
    newEntities += 1; // the face itself
    return newEntities;
  }
}

/// Namespaced constant so [_ArchiveWriter.writeLayer] doesn't collide with
/// the top-level `_layerSchema` name used elsewhere in this file.
class LayerSchema {
  static const int value = 3;
}

/// Shared by [SkpBuilder.addFace] and [ComponentDefinitionBuilder.addFace] -
/// writes [points] as one face normally, unless [autoTriangulate] is set
/// AND the points aren't coplanar, in which case it fan-triangulates from
/// `points[0]` and writes one real, always-planar triangular face per fan
/// wedge instead of throwing. This mirrors real SketchUp's own UI behavior
/// for a not-quite-flat polygon. Not attempted when [holes] is given, or
/// for a genuinely degenerate (collinear) input.
int _writeFaceOrTriangulate(
  _ArchiveWriter writer,
  List<Point3> points,
  Map<Point3, int> vertexSlots,
  Map<(int, int), (int, int)> edgeRegistry,
  int material,
  int layer,
  int backMaterial,
  bool hidden,
  bool softEdges,
  bool smoothEdges,
  bool hiddenEdges,
  List<(Point3, (double, double))>? frontUv,
  List<(Point3, (double, double))>? backUv,
  List<(String, Map<String, Object>)> attributeDicts,
  bool autoTriangulate, {
  List<List<Point3>> holes = const [],
}) {
  if (holes.isNotEmpty || !autoTriangulate || points.length == 3 || _isCoplanar(points)) {
    return writer.writeFace(
      points,
      vertexSlots,
      edgeRegistry,
      faceMaterial: material,
      faceLayer: layer,
      backMaterial: backMaterial,
      hidden: hidden,
      softEdges: softEdges,
      smoothEdges: smoothEdges,
      hiddenEdges: hiddenEdges,
      frontUv: frontUv,
      backUv: backUv,
      attributeDicts: attributeDicts,
      holes: holes,
    );
  }
  if (frontUv != null || backUv != null) {
    throw SkpWriteError('autoTriangulate cannot be combined with frontUv/backUv positioning');
  }
  var total = 0;
  for (var i = 1; i < points.length - 1; i++) {
    total += writer.writeFace(
      [points[0], points[i], points[i + 1]],
      vertexSlots,
      edgeRegistry,
      faceMaterial: material,
      faceLayer: layer,
      backMaterial: backMaterial,
      hidden: hidden,
      softEdges: softEdges,
      smoothEdges: smoothEdges,
      hiddenEdges: hiddenEdges,
      attributeDicts: attributeDicts,
    );
  }
  return total;
}

// ── public builder API ──────────────────────────────────────────────────────

/// Common shape shared by [SkpBuilder] and [ComponentDefinitionBuilder] -
/// both accept faces and instances the same way. This is what lets
/// `openskp.edit`'s replay code target either the root builder or a nested
/// definition through one generic code path, mirroring how Python's
/// `openskp.edit._replay_body` duck-types the same two classes there
/// (Dart has no structural typing for classes, so this explicit interface
/// is the port's equivalent).
abstract class GeometryHost {
  void addFace(
    List<Point3> points, {
    required int? material,
    required int? layer,
    required int? backMaterial,
    required bool hidden,
    required bool softEdges,
    required bool smoothEdges,
    required bool hiddenEdges,
    required List<(Point3, (double, double))>? frontUv,
    required List<(Point3, (double, double))>? backUv,
    required Map<String, Object>? attributes,
    required String attributeDictName,
    required bool autoTriangulate,
    required List<List<Point3>> holes,
  });

  void addInstance(
    ComponentDefinitionBuilder definition, {
    required String? name,
    required Point3 translation,
    required Matrix3x3? matrix3x3,
    required Rotation? rotation,
    required int? material,
    required int? layer,
    required Map<String, Object>? attributes,
    required String attributeDictName,
    required bool hidden,
  });
}

/// Accumulates one component/group definition's geometry. Construct via
/// [SkpBuilder.addComponentDefinition] or [SkpBuilder.addGroup], not
/// directly.
///
/// Dart has no `with`-statement equivalent to Python's context manager, so
/// the body that builds a definition's geometry is passed as a callback
/// instead of run inside a `with` block - the callback runs, then (only on
/// normal return - an exception skips this, leaving the definition
/// deliberately "still open" so further calls on the parent builder keep
/// failing loudly rather than silently building on top of corrupt state)
/// the definition is closed:
///
/// ```dart
/// final chair = builder.addComponentDefinition('Chair', (def) {
///   def.addFace([(0, 0, 0), (20, 0, 0), (20, 20, 0), (0, 20, 0)]);
/// });
/// builder.addInstance(chair, translation: (100, 0, 0));
/// ```
class ComponentDefinitionBuilder implements GeometryHost {
  final SkpBuilder _skp;
  final int slot;
  final String name;
  final int _countPatchPos;
  final Map<Point3, int> _vertexSlots = {};
  final Map<(int, int), (int, int)> _edgeRegistry = {};
  int _newEntityCount = 0;
  bool _closed = false;

  /// Set only when this builder was created by [SkpBuilder.addGroup] - a
  /// group places itself immediately on close, unlike a plain component
  /// definition, which needs an explicit later [SkpBuilder.addInstance]
  /// call.
  final GroupPlacement? _groupPlacement;

  ComponentDefinitionBuilder._(this._skp, this.slot, this.name, this._countPatchPos, this._groupPlacement);

  void _checkWritable(String action) {
    if (_closed) {
      throw SkpWriteError(
        "component definition '$name' has already closed - cannot add more $action to it",
      );
    }
  }

  /// Add one planar face to this definition - same signature and behavior
  /// as [SkpBuilder.addFace], except vertices/edges are shared only within
  /// this definition, never with the root model or other definitions.
  void addFace(
    List<Point3> points, {
    int? material,
    int? layer,
    int? backMaterial,
    bool hidden = false,
    bool softEdges = false,
    bool smoothEdges = false,
    bool hiddenEdges = false,
    List<(Point3, (double, double))>? frontUv,
    List<(Point3, (double, double))>? backUv,
    Map<String, Object>? attributes,
    String attributeDictName = 'attributes',
    bool autoTriangulate = false,
    List<List<Point3>> holes = const [],
  }) {
    _checkWritable('faces');
    if (points.length < 3) {
      throw SkpWriteError('a face needs at least 3 points');
    }
    final attributeDicts = _attrDicts(attributes, attributeDictName);
    _newEntityCount += _writeFaceOrTriangulate(
      _skp._definitionWriter!,
      points,
      _vertexSlots,
      _edgeRegistry,
      material ?? 0,
      layer ?? 0,
      backMaterial ?? 0,
      hidden,
      softEdges,
      smoothEdges,
      hiddenEdges,
      frontUv,
      backUv,
      attributeDicts,
      autoTriangulate,
      holes: holes,
    );
  }

  /// Add one circular face to this definition - same signature and
  /// behavior as [SkpBuilder.addCircle], except vertices/edges are shared
  /// only within this definition.
  void addCircle(
    Point3 center,
    Point3 normal,
    double radius, {
    int numSegments = 24,
    int? material,
    int? layer,
    int? backMaterial,
    bool hidden = false,
    List<(Point3, (double, double))>? frontUv,
    List<(Point3, (double, double))>? backUv,
    Map<String, Object>? attributes,
    String attributeDictName = 'attributes',
  }) {
    _checkWritable('faces');
    if (numSegments < 3 || numSegments > 255) {
      throw SkpWriteError('numSegments must be between 3 and 255, got $numSegments');
    }
    final n = _normalize3(normal);
    final writer = _skp._definitionWriter!;
    final (u, w) = _circleBasis(n);
    final xaxis = (radius * u.$1, radius * u.$2, radius * u.$3);
    final CurveParams curveParams = (center, n, xaxis, 0.0, 2.0 * pi, radius, numSegments);
    final points = _circlePoints(center, n, radius, numSegments, u, w);
    final attributeDicts = _attrDicts(attributes, attributeDictName);
    _newEntityCount += writer.writeFace(
      points,
      _vertexSlots,
      _edgeRegistry,
      faceMaterial: material ?? 0,
      faceLayer: layer ?? 0,
      backMaterial: backMaterial ?? 0,
      hidden: hidden,
      frontUv: frontUv,
      backUv: backUv,
      attributeDicts: attributeDicts,
      curveParams: curveParams,
    );
  }

  /// Add one partial (open) arc to this definition - same signature and
  /// behavior as [SkpBuilder.addArc], except vertices/edges are shared
  /// only within this definition.
  void addArc(
    Point3 center,
    Point3 normal,
    double radius,
    double startAngle,
    double endAngle, {
    int numSegments = 24,
    bool hiddenEdges = false,
    bool softEdges = false,
    bool smoothEdges = false,
  }) {
    _checkWritable('arcs');
    if (numSegments < 3 || numSegments > 255) {
      throw SkpWriteError('numSegments must be between 3 and 255, got $numSegments');
    }
    if (endAngle == startAngle) {
      throw SkpWriteError('startAngle and endAngle must differ - use addCircle for a full circle');
    }
    final n = _normalize3(normal);
    final writer = _skp._definitionWriter!;
    final (u, w) = _circleBasis(n);
    final xaxis = (radius * u.$1, radius * u.$2, radius * u.$3);
    final CurveParams curveParams = (center, n, xaxis, startAngle, endAngle, radius, numSegments);
    final points = _arcPoints(center, n, radius, numSegments, u, w, startAngle, endAngle);
    _newEntityCount += writer.writeArc(
      points,
      _vertexSlots,
      _edgeRegistry,
      curveParams,
      hiddenEdges: hiddenEdges,
      softEdges: softEdges,
      smoothEdges: smoothEdges,
    );
  }

  /// Add one freeform polyline curve to this definition - same signature
  /// and behavior as [SkpBuilder.addPolyline], except vertices/edges are
  /// shared only within this definition.
  void addPolyline(
    List<Point3> points, {
    bool closed = false,
    bool hiddenEdges = false,
    bool softEdges = false,
    bool smoothEdges = false,
  }) {
    _checkWritable('polylines');
    if (points.length < 2) {
      throw SkpWriteError('a polyline needs at least 2 points');
    }
    _newEntityCount += _skp._definitionWriter!.writePolyline(
      points,
      _vertexSlots,
      _edgeRegistry,
      closed: closed,
      hiddenEdges: hiddenEdges,
      softEdges: softEdges,
      smoothEdges: smoothEdges,
    );
  }

  /// Place one instance of another, already-closed component definition
  /// inside this one - the same nesting real SketchUp supports. [definition]
  /// must come from the same [SkpBuilder] and must already be closed (only
  /// one definition can be open on a builder at a time, and that one is
  /// always `this` while its own body is running - so any *other*
  /// definition reachable here was necessarily closed before `this` was
  /// even opened, which also rules out cycles).
  void addInstance(
    ComponentDefinitionBuilder definition, {
    String? name,
    Point3 translation = (0.0, 0.0, 0.0),
    Matrix3x3? matrix3x3,
    Rotation? rotation,
    int? material,
    int? layer,
    Map<String, Object>? attributes,
    String attributeDictName = 'attributes',
    bool hidden = false,
  }) {
    _checkWritable('instances');
    if (!identical(definition._skp, _skp)) {
      throw SkpWriteError(
        "component definition '${definition.name}' belongs to a different builder "
        "(a different create() call) - its slot number is meaningless here",
      );
    }
    if (identical(definition, this)) {
      throw SkpWriteError("component definition '$name' cannot nest an instance of itself");
    }
    final resolved = _resolveMatrix3x3(matrix3x3, rotation);
    final attributeDicts = _attrDicts(attributes, attributeDictName);
    _newEntityCount += _skp._definitionWriter!.writeInstance(
      definition.slot,
      name ?? definition.name,
      translation,
      resolved,
      material ?? 0,
      layer ?? 0,
      attributeDicts: attributeDicts,
      hidden: hidden,
    );
  }

  /// Place another, already-closed component definition inside this one as
  /// a *group* (`CGroup`) rather than a component instance - otherwise
  /// identical to [addInstance]. Unlike the self-placing [SkpBuilder.
  /// addGroup] at the root level, a nested group can't be declared inline:
  /// this format has no way to embed one definition's declaration inside
  /// another's, so build the group's geometry with a normal
  /// [SkpBuilder.addComponentDefinition] first, then place it here.
  void addGroupInstance(
    ComponentDefinitionBuilder definition, {
    String? name,
    Point3 translation = (0.0, 0.0, 0.0),
    Matrix3x3? matrix3x3,
    Rotation? rotation,
    int? material,
    int? layer,
    bool hidden = false,
  }) {
    _checkWritable('groups');
    if (!identical(definition._skp, _skp)) {
      throw SkpWriteError(
        "component definition '${definition.name}' belongs to a different builder "
        "(a different create() call) - its slot number is meaningless here",
      );
    }
    if (identical(definition, this)) {
      throw SkpWriteError("component definition '$name' cannot nest a group instance of itself");
    }
    final resolved = _resolveMatrix3x3(matrix3x3, rotation);
    _newEntityCount += _skp._definitionWriter!.writeGroup(
      definition.slot,
      name ?? definition.name,
      translation,
      resolved,
      material ?? 0,
      layer ?? 0,
      hidden,
    );
  }

  void _close() {
    if (_newEntityCount == 0) {
      throw SkpWriteError("component definition '$name' has no geometry - add at least one face");
    }
    final writer = _skp._definitionWriter!;
    _patchU32(writer.buf, _countPatchPos, _newEntityCount);
    writer.writeDefinitionTail(name);
    _closed = true;
    _skp._openDefinition = null;
    final gp = _groupPlacement;
    if (gp != null) {
      _skp._pendingGroups.add((this, gp));
    }
  }
}

/// Accumulates geometry and writes it into a new legacy-format (v17) `.skp`
/// file. Construct via [create], not directly.
class SkpBuilder implements GeometryHost {
  late final Uint8List _data;
  late final int _materialInsertPos;
  late final int _base;
  late final int _layerCountPos;
  late final int _origLayerCount;
  late final int _layerInsertPos;
  late final int _defCountPos;
  late final int _origDefCount;
  late final int _rootCountPos;
  late final int _origRootCount;
  late final int _tailPos;

  /// The scaffold-derived starting slot for anything written AFTER the
  /// (always byte-for-byte-copied) layer/definition/root-entity region -
  /// i.e. where geometry's own new slots would start if zero materials or
  /// layers are added. Materials splice in before the layer list and
  /// layers splice in right after the existing ones, so every slot from
  /// here on shifts by however many slots each section ends up consuming.
  late final int _scaffoldNextSlot;
  late final Map<String, int> _scaffoldClassSlot;

  /// Materials always start allocating at [_base], the same slot the
  /// (possibly absent) material section would have occupied.
  late final _ArchiveWriter _materialWriter;

  /// Every material registered so far, by name - populated by
  /// [addMaterial]/[addTextureMaterial] as a side effect (they already
  /// de-dupe by name through this same map). Useful for reusing a handle
  /// after [openExisting], where every material the source file had is
  /// already here.
  final Map<String, int> materialsByName = {};
  int _materialCount = 0;

  /// Deferred: layers splice in AFTER materials, so the layer writer's
  /// starting slot depends on the final material count.
  late final int _layerWriterBase;
  _ArchiveWriter? _layerWriter;
  int? _layerWriterStart;

  /// Every layer registered so far, by name - same pattern as
  /// [materialsByName].
  final Map<String, int> layersByName = {};
  int _layerCount = 0;

  /// Deferred the same way as the layer writer: component definitions
  /// splice in after layers, before root-level geometry.
  _ArchiveWriter? _definitionWriter;
  int? _definitionWriterStart;
  int _definitionCount = 0;
  ComponentDefinitionBuilder? _openDefinition;
  List<(ComponentDefinitionBuilder, GroupPlacement)> _pendingGroups = [];

  _ArchiveWriter? _geometryWriter;
  final Map<Point3, int> _vertexSlots = {};
  final Map<(int, int), (int, int)> _edgeRegistry = {};
  int _newEntityCount = 0;
  int _faceCount = 0;

  /// The root geometry writer's current next-archive-slot value, or null
  /// before any root-level geometry has been written. Exposed only so a
  /// large-model test can confirm it actually crosses the 0x7FFF slot
  /// boundary (see [_shiftRef]'s doc comment) rather than silently staying
  /// under it - not meant for production use.
  @visibleForTesting
  int? get debugGeometryNextSlot => _geometryWriter?.nextSlot;

  SkpBuilder() {
    final data = _loadScaffold();
    final clayerPos = _findClayerClassPattern(data);
    if (clayerPos < 0) {
      throw SkpWriteError('scaffold is missing its CLayer class record');
    }
    final start = clayerPos - 9;
    final base = Legacy.probeLayerAnchorBases(data, 17, start, 0)[0];

    final ar = Archive(data, 17);
    ar.readers.addAll(LegacyReaders.readers);
    ar.nextSlot = base;
    ar.walkBase = base;
    final r = ar.r;
    r.pos = start;
    r.u32();
    r.u8();
    final layerCountPos = r.pos;
    final origLayerCount = r.u32();
    for (var i = 0; i < origLayerCount; i++) {
      ar.readObject(r, 'CLayer');
    }
    final layerInsertPos = r.pos;
    final layerWriterBase = ar.nextSlot;
    ar.readObject(r); // definition-list anchor (active-layer back-ref)
    final defCountPos = r.pos;
    final defCount = r.u32();
    for (var i = 0; i < defCount; i++) {
      ar.readObject(r, 'CComponentDefinition');
    }

    final rootCountPos = r.pos;
    final origRootCount = Tlv.readU32(data, rootCountPos);
    r.u32();
    LegacyReaders.readEntityList(ar, r, origRootCount, 'root');
    final tailPos = r.pos;

    _data = data;
    _materialInsertPos = start;
    _base = base;
    _layerCountPos = layerCountPos;
    _origLayerCount = origLayerCount;
    _layerInsertPos = layerInsertPos;
    _defCountPos = defCountPos;
    _origDefCount = defCount;
    _rootCountPos = rootCountPos;
    _origRootCount = origRootCount;
    _tailPos = tailPos;
    _scaffoldNextSlot = ar.nextSlot;
    _scaffoldClassSlot = Map<String, int>.from(ar.classSlot);
    _materialWriter = _ArchiveWriter(nextSlot: base, classSlot: {});
    _layerWriterBase = layerWriterBase;
  }

  /// Register a solid-color material and return a handle to pass as
  /// `addFace`'s `material` argument. [rgba] is `[r, g, b]` or `[r, g, b,
  /// a]`, each 0-255; alpha defaults to 255 (opaque).
  ///
  /// Calling this again with a name already registered returns the same
  /// handle rather than creating a duplicate material.
  ///
  /// All materials must be added before the first [addFace] call, and
  /// before any [addLayer]/[addComponentDefinition] call - materials
  /// splice in earlier in the file, so those sections' own slot numbering
  /// depends on the final material count too.
  int addMaterial(String name, List<int> rgba) {
    if (_geometryWriter != null) {
      throw SkpWriteError('addMaterial must be called before any addFace calls');
    }
    if (_layerWriter != null) {
      throw SkpWriteError('addMaterial must be called before any addLayer calls');
    }
    if (_definitionWriter != null) {
      throw SkpWriteError('addMaterial must be called before any addComponentDefinition calls');
    }
    if (materialsByName.containsKey(name)) return materialsByName[name]!;
    var r = rgba;
    if (r.length == 3) r = [...r, 255];
    if (r.length != 4 || r.any((c) => c < 0 || c > 255)) {
      throw SkpWriteError('rgba must be 3 or 4 integers in 0-255');
    }
    final slot = _materialWriter.writeMaterial(name, (r[0], r[1], r[2], r[3]));
    materialsByName[name] = slot;
    _materialCount++;
    return slot;
  }

  /// Register an image-textured material from a local PNG or JPEG file and
  /// return a handle to pass as `addFace`'s `material` argument. The
  /// format is detected from the file's own magic bytes, not its
  /// extension. UV mapping is always the default planar projection;
  /// explicit positioning/pinning is not supported. Same ordering rules as
  /// [addMaterial].
  int addTextureMaterial(String name, String imagePath) {
    if (_geometryWriter != null) {
      throw SkpWriteError('addTextureMaterial must be called before any addFace calls');
    }
    if (_layerWriter != null) {
      throw SkpWriteError('addTextureMaterial must be called before any addLayer calls');
    }
    if (_definitionWriter != null) {
      throw SkpWriteError('addTextureMaterial must be called before any addComponentDefinition calls');
    }
    if (materialsByName.containsKey(name)) return materialsByName[name]!;
    final imageBytes = File(imagePath).readAsBytesSync();
    final subtype = _detectImageSubtype(imageBytes);
    final slot = _materialWriter.writeTexturedMaterial(name, imageBytes, imagePath, subtype);
    materialsByName[name] = slot;
    _materialCount++;
    return slot;
  }

  /// Register a layer and return a handle to pass as `addFace`'s `layer`
  /// argument. Calling this again with a name already registered returns
  /// the same handle (`color`/`hidden` are ignored on a repeat call). All
  /// layers must be added before the first [addFace] call and before any
  /// [addComponentDefinition] call.
  int addLayer(String name, {List<int>? color, bool hidden = false}) {
    if (_geometryWriter != null) {
      throw SkpWriteError('addLayer must be called before any addFace calls');
    }
    if (_definitionWriter != null) {
      throw SkpWriteError('addLayer must be called before any addComponentDefinition calls');
    }
    if (layersByName.containsKey(name)) return layersByName[name]!;
    (int, int, int, int)? rgba;
    if (color != null) {
      var c = color;
      if (c.length == 3) c = [...c, 255];
      if (c.length != 4 || c.any((v) => v < 0 || v > 255)) {
        throw SkpWriteError('color must be 3 or 4 integers in 0-255');
      }
      rgba = (c[0], c[1], c[2], c[3]);
    }
    if (_layerWriter == null) {
      final materialShift = _materialWriter.nextSlot - _base;
      _layerWriterStart = _layerWriterBase + materialShift;
      // CLayer's class declaration lives inside Layer0's copied-through
      // bytes, which - like everything else after the material section -
      // shifts by materialShift. The scaffold-derived classSlot map still
      // has its raw, unshifted value, so correct every entry before
      // handing it to a writer that might look one up.
      _layerWriter = _ArchiveWriter(nextSlot: _layerWriterStart!, classSlot: _materialShiftedClassSlot());
    }
    final slot = _layerWriter!.writeLayer(name, hidden: hidden, rgba: rgba);
    layersByName[name] = slot;
    _layerCount++;
    return slot;
  }

  Map<String, int> _materialShiftedClassSlot() {
    final materialShift = _materialWriter.nextSlot - _base;
    return {for (final e in _scaffoldClassSlot.entries) e.key: e.value + materialShift};
  }

  int _layerShift() {
    if (_layerWriter == null) return 0;
    return _layerWriter!.nextSlot - _layerWriterStart!;
  }

  /// The classSlot map a writer positioned right after the layer section
  /// (a definition writer, or root geometry if no definitions exist)
  /// should start from.
  Map<String, int> _postLayerClassSlot() {
    if (_layerWriter != null) return Map<String, int>.from(_layerWriter!.classSlot);
    return _materialShiftedClassSlot();
  }

  ComponentDefinitionBuilder _startDefinition(
    String name,
    String caller, {
    GroupPlacement? groupPlacement,
    List<(String, Map<String, Object>)> attributeDicts = const [],
  }) {
    if (_geometryWriter != null) {
      throw SkpWriteError('$caller must be called before any addFace/addInstance calls');
    }
    if (_openDefinition != null) {
      throw SkpWriteError(
        "component definition '${_openDefinition!.name}' is still open - "
        'finish its body before starting another',
      );
    }
    if (_definitionWriter == null) {
      _definitionWriterStart = _scaffoldNextSlot + (_materialWriter.nextSlot - _base) + _layerShift();
      _definitionWriter = _ArchiveWriter(nextSlot: _definitionWriterStart!, classSlot: _postLayerClassSlot());
    }
    final (slot, countPatchPos) = _definitionWriter!.writeDefinitionHeader(attributeDicts);
    _definitionCount++;
    final comp = ComponentDefinitionBuilder._(this, slot, name, countPatchPos, groupPlacement);
    _openDefinition = comp;
    return comp;
  }

  /// Start a new reusable component definition, run [body] to add its
  /// geometry, then close it - once closed, pass the returned builder to
  /// [addInstance] to place copies of it in the model.
  ///
  /// ```dart
  /// final chair = builder.addComponentDefinition('Chair', (def) {
  ///   def.addFace([(0, 0, 0), (20, 0, 0), (20, 20, 0), (0, 20, 0)]);
  /// });
  /// builder.addInstance(chair, translation: (100, 0, 0));
  /// ```
  ///
  /// Must be called before any [addFace]/[addInstance] call on the builder
  /// itself - component definitions splice in after materials and layers,
  /// before root-level geometry.
  ///
  /// [attributes], if given, is custom key/value metadata (values may be
  /// `String`, `int`, or `double`) attached to the definition itself,
  /// under a dictionary named [attributeDictName].
  ComponentDefinitionBuilder addComponentDefinition(
    String name,
    void Function(ComponentDefinitionBuilder def) body, {
    Map<String, Object>? attributes,
    String attributeDictName = 'attributes',
  }) {
    final attributeDicts = _attrDicts(attributes, attributeDictName);
    final comp = _startDefinition(name, 'addComponentDefinition', attributeDicts: attributeDicts);
    body(comp);
    comp._close();
    return comp;
  }

  /// Start a new group, run [body] to add its geometry, then place it at
  /// [translation]/[matrix3x3] automatically - unlike
  /// [addComponentDefinition] there is no separate placement call, matching
  /// how groups are normally used (defined and placed once).
  ///
  /// ```dart
  /// builder.addGroup((table) {
  ///   table.addFace([(0, 0, 0), (30, 0, 0), (30, 30, 0), (0, 30, 0)]);
  /// }, name: 'Table', translation: (50, 0, 0));
  /// ```
  ///
  /// [rotation], if given, is an `(axis, angleRadians)` pair - an
  /// alternative to hand-deriving [matrix3x3] for a pure rotation; pass at
  /// most one of the two.
  ComponentDefinitionBuilder addGroup(
    void Function(ComponentDefinitionBuilder def) body, {
    String? name,
    Point3 translation = (0.0, 0.0, 0.0),
    Matrix3x3? matrix3x3,
    Rotation? rotation,
    int? material,
    int? layer,
    bool hidden = false,
  }) {
    final resolved = _resolveMatrix3x3(matrix3x3, rotation);
    final comp = _startDefinition(
      name ?? 'Group',
      'addGroup',
      groupPlacement: (translation, resolved, material ?? 0, layer ?? 0, hidden),
    );
    body(comp);
    comp._close();
    return comp;
  }

  int _definitionShift() {
    if (_definitionWriter == null) return 0;
    return _definitionWriter!.nextSlot - _definitionWriterStart!;
  }

  Map<String, int> _postDefinitionClassSlot() {
    if (_definitionWriter != null) return Map<String, int>.from(_definitionWriter!.classSlot);
    return _postLayerClassSlot();
  }

  /// Place one instance of [definition] (from [addComponentDefinition],
  /// already closed) in the model.
  ///
  /// [matrix3x3] is a row-major 3x3 rotation/scale matrix (identity if
  /// omitted); [translation] is applied after it, in inches.
  /// [material]/[layer], if given, are handles from [addMaterial]/
  /// [addLayer] applied to the instance itself (not its contents).
  ///
  /// [rotation], if given, is an `(axis, angleRadians)` pair - an
  /// alternative to [matrix3x3] for a pure rotation; pass at most one of
  /// the two.
  ///
  /// [attributes], if given, is custom key/value metadata attached to this
  /// instance specifically, under a dictionary named [attributeDictName].
  /// [hidden] hides this specific placement.
  void addInstance(
    ComponentDefinitionBuilder definition, {
    String? name,
    Point3 translation = (0.0, 0.0, 0.0),
    Matrix3x3? matrix3x3,
    Rotation? rotation,
    int? material,
    int? layer,
    Map<String, Object>? attributes,
    String attributeDictName = 'attributes',
    bool hidden = false,
  }) {
    if (!identical(definition._skp, this)) {
      throw SkpWriteError(
        "component definition '${definition.name}' belongs to a different builder "
        "(a different create() call) - its slot number is meaningless here",
      );
    }
    if (!definition._closed) {
      throw SkpWriteError(
        "component definition '${definition.name}' is still open - "
        'finish its body before calling addInstance',
      );
    }
    final resolved = _resolveMatrix3x3(matrix3x3, rotation);
    _ensureGeometryWriter();
    final attributeDicts = _attrDicts(attributes, attributeDictName);
    _newEntityCount += _geometryWriter!.writeInstance(
      definition.slot,
      name ?? definition.name,
      translation,
      resolved,
      material ?? 0,
      layer ?? 0,
      attributeDicts: attributeDicts,
      hidden: hidden,
    );
    _faceCount++; // reuses the "at least one root entity" check in toBytes
  }

  void _ensureGeometryWriter() {
    if (_geometryWriter != null) return;
    if (_openDefinition != null) {
      // Calling this while a definition/group is still open would lock in
      // the geometry writer's starting slot before that definition (and
      // anything added to it afterward) finishes growing _definitionWriter
      // - the locked-in slot would then be too low, corrupting every
      // back-reference root-level geometry makes.
      throw SkpWriteError(
        "component definition '${_openDefinition!.name}' is still open - "
        'finish its body before adding root-level geometry',
      );
    }
    final materialShift = _materialWriter.nextSlot - _base;
    _geometryWriter = _ArchiveWriter(
      nextSlot: _scaffoldNextSlot + materialShift + _layerShift() + _definitionShift(),
      classSlot: _postDefinitionClassSlot(),
    );
    // Flush any groups that closed earlier, in the order they were created
    // - deferred until now so closing one group doesn't lock in root-level
    // slot numbering before a later addGroup/addComponentDefinition call
    // has had a chance to run.
    for (final (comp, placement) in _pendingGroups) {
      final (translation, matrix3x3, mat, layer, hidden) = placement;
      _newEntityCount += _geometryWriter!.writeGroup(comp.slot, comp.name, translation, matrix3x3, mat, layer, hidden);
      _faceCount++;
    }
    _pendingGroups = [];
  }

  /// Add one planar face, defined by 3 or more coplanar [points] (in
  /// inches) forming a closed polygon in order - do not repeat the first
  /// point at the end.
  ///
  /// Vertices and edges are automatically shared with previously-added
  /// faces wherever a point's (x, y, z) coordinates match exactly.
  ///
  /// [material]/[backMaterial], if given, are handles from [addMaterial]
  /// (or [addTextureMaterial]) applied to the face's front/back side.
  /// [layer], if given, is a handle from [addLayer].
  ///
  /// [frontUv]/[backUv], if given, explicitly position that side's texture:
  /// exactly 3 `(point, (u, v))` pairs.
  ///
  /// By default, non-coplanar [points] throw [SkpWriteError] -
  /// `autoTriangulate: true` instead fan-triangulates from `points[0]` into
  /// several always-planar triangular faces, mirroring real SketchUp's own
  /// behavior for a not-quite-flat polygon. [holes], if given, is a
  /// sequence of point lists cut out of the face - every hole's points
  /// must lie on the same plane as [points] itself.
  void addFace(
    List<Point3> points, {
    int? material,
    int? layer,
    int? backMaterial,
    bool hidden = false,
    bool softEdges = false,
    bool smoothEdges = false,
    bool hiddenEdges = false,
    List<(Point3, (double, double))>? frontUv,
    List<(Point3, (double, double))>? backUv,
    Map<String, Object>? attributes,
    String attributeDictName = 'attributes',
    bool autoTriangulate = false,
    List<List<Point3>> holes = const [],
  }) {
    if (points.length < 3) {
      throw SkpWriteError('a face needs at least 3 points');
    }
    _ensureGeometryWriter();
    final attributeDicts = _attrDicts(attributes, attributeDictName);
    _newEntityCount += _writeFaceOrTriangulate(
      _geometryWriter!,
      points,
      _vertexSlots,
      _edgeRegistry,
      material ?? 0,
      layer ?? 0,
      backMaterial ?? 0,
      hidden,
      softEdges,
      smoothEdges,
      hiddenEdges,
      frontUv,
      backUv,
      attributeDicts,
      autoTriangulate,
      holes: holes,
    );
    _faceCount++;
  }

  /// Add one circular face - a true SketchUp circle (editable by radius,
  /// re-tessellatable, selectable as a single "Curve" entity), not
  /// [numSegments] disconnected straight edges that merely happen to trace
  /// that shape.
  ///
  /// [center]/[radius] are in inches; [normal] is the circle's plane
  /// normal (need not be a unit vector). [numSegments] (3-255) controls
  /// tessellation, matching SketchUp's own circle tool default of 24.
  void addCircle(
    Point3 center,
    Point3 normal,
    double radius, {
    int numSegments = 24,
    int? material,
    int? layer,
    int? backMaterial,
    bool hidden = false,
    List<(Point3, (double, double))>? frontUv,
    List<(Point3, (double, double))>? backUv,
    Map<String, Object>? attributes,
    String attributeDictName = 'attributes',
  }) {
    if (numSegments < 3 || numSegments > 255) {
      throw SkpWriteError('numSegments must be between 3 and 255, got $numSegments');
    }
    final n = _normalize3(normal);
    _ensureGeometryWriter();
    final (u, w) = _circleBasis(n);
    final xaxis = (radius * u.$1, radius * u.$2, radius * u.$3);
    final CurveParams curveParams = (center, n, xaxis, 0.0, 2.0 * pi, radius, numSegments);
    final points = _circlePoints(center, n, radius, numSegments, u, w);
    final attributeDicts = _attrDicts(attributes, attributeDictName);
    _newEntityCount += _geometryWriter!.writeFace(
      points,
      _vertexSlots,
      _edgeRegistry,
      faceMaterial: material ?? 0,
      faceLayer: layer ?? 0,
      backMaterial: backMaterial ?? 0,
      hidden: hidden,
      frontUv: frontUv,
      backUv: backUv,
      attributeDicts: attributeDicts,
      curveParams: curveParams,
    );
    _faceCount++;
  }

  /// Add one partial (open) arc - a genuine SketchUp arc entity (editable
  /// by radius/angle, re-tessellatable), not disconnected straight edges
  /// that merely trace that shape. Unlike [addCircle], this creates edges
  /// only, no face.
  ///
  /// [startAngle]/[endAngle] (radians) measure the sweep from an arbitrary
  /// but fixed 0-angle reference direction in the plane perpendicular to
  /// [normal] - chosen automatically, the same way for every arc/circle
  /// built with the same normal.
  void addArc(
    Point3 center,
    Point3 normal,
    double radius,
    double startAngle,
    double endAngle, {
    int numSegments = 24,
    bool hiddenEdges = false,
    bool softEdges = false,
    bool smoothEdges = false,
  }) {
    if (numSegments < 3 || numSegments > 255) {
      throw SkpWriteError('numSegments must be between 3 and 255, got $numSegments');
    }
    if (endAngle == startAngle) {
      throw SkpWriteError('startAngle and endAngle must differ - use addCircle for a full circle');
    }
    final n = _normalize3(normal);
    _ensureGeometryWriter();
    final (u, w) = _circleBasis(n);
    final xaxis = (radius * u.$1, radius * u.$2, radius * u.$3);
    final CurveParams curveParams = (center, n, xaxis, startAngle, endAngle, radius, numSegments);
    final points = _arcPoints(center, n, radius, numSegments, u, w, startAngle, endAngle);
    _newEntityCount += _geometryWriter!.writeArc(
      points,
      _vertexSlots,
      _edgeRegistry,
      curveParams,
      hiddenEdges: hiddenEdges,
      softEdges: softEdges,
      smoothEdges: smoothEdges,
    );
    _faceCount++; // reuses the "at least one root entity" check in toBytes
  }

  /// Add one freeform polyline curve - a chain of straight edges ([points]
  /// in order, at least 2) grouped into one genuine SketchUp "Curve"
  /// entity, not disconnected individual edges that merely happen to
  /// connect end-to-end. No face, unlike [addFace]. [closed], if true,
  /// also connects the last point back to the first.
  void addPolyline(
    List<Point3> points, {
    bool closed = false,
    bool hiddenEdges = false,
    bool softEdges = false,
    bool smoothEdges = false,
  }) {
    if (points.length < 2) {
      throw SkpWriteError('a polyline needs at least 2 points');
    }
    _ensureGeometryWriter();
    _newEntityCount += _geometryWriter!.writePolyline(
      points,
      _vertexSlots,
      _edgeRegistry,
      closed: closed,
      hiddenEdges: hiddenEdges,
      softEdges: softEdges,
      smoothEdges: smoothEdges,
    );
    _faceCount++; // reuses the "at least one root entity" check in toBytes
  }

  /// Return the finished file's bytes.
  Uint8List toBytes() {
    if (_pendingGroups.isNotEmpty) {
      // A file with only groups (no addFace/addInstance call) would
      // otherwise never flush them - _ensureGeometryWriter is a no-op once
      // already created, so this is safe to call unconditionally alongside
      // every other call site.
      _ensureGeometryWriter();
    }
    if (_faceCount == 0) {
      throw SkpWriteError('no geometry added - call addFace at least once before saving');
    }

    // Every new-class declaration and every new object allocation each
    // consume one archive slot; nextSlot already reflects the running
    // total, so each shift is just the delta since its writer started.
    final materialShift = _materialWriter.nextSlot - _base;
    final layerShift = _layerShift();
    final definitionShift = _definitionShift();
    final geometryInitialSlot = _scaffoldNextSlot + materialShift + layerShift + definitionShift;
    final geometryShift = _geometryWriter!.nextSlot - geometryInitialSlot;
    final newRootCount = _origRootCount + _newEntityCount;

    final out = <int>[];

    // Each layer's record embeds 2 pids (see writeLayer); materials use 1
    // pid each (writeMaterial).
    final layerPids = _layerWriter != null ? (_layerWriter!.nextPid - 1) : 0;
    final pidDelta = _materialCount + layerPids;

    // The 4 bytes right before the material insertion point are a reserved
    // (always-present) mat_count field - zero/implicit in the zero-
    // material scaffold, not a gap that needs new bytes inserted. Real
    // SketchUp overwrites them in place rather than growing the file by 4
    // extra bytes here.
    final prefix = List<int>.from(_data.sublist(0, _materialInsertPos - 4));
    if (pidDelta != 0) {
      final cur = _readU16At(prefix, _pidCounterPos);
      _patchU16(prefix, _pidCounterPos, cur + pidDelta);
    }
    prefix.setRange(
      _isoCameraPrefixOffset,
      _isoCameraPrefixOffset + _isoCameraPrefixPatch.length,
      _isoCameraPrefixPatch,
    );
    out.addAll(prefix);
    out.addAll(_u32(_materialCount));
    out.addAll(_materialWriter.buf);

    // materialInsertPos -> layerInsertPos: Layer0 (and any other already-
    // existing layers) plus the layer_count field, unmodified except for
    // that count.
    final middle1 = List<int>.from(_data.sublist(_materialInsertPos, _layerInsertPos));
    final layerCountRel = _layerCountPos - _materialInsertPos;
    _patchU32(middle1, layerCountRel, _origLayerCount + _layerCount);
    out.addAll(middle1);
    if (_layerWriter != null) {
      out.addAll(_layerWriter!.buf);
    }

    // layerInsertPos -> defCountPos: just the active-layer anchor, which
    // needs +materialShift (never +layerShift - Layer0 itself never moves
    // just because more layers are appended after it).
    final middle2a = List<int>.from(_data.sublist(_layerInsertPos, _defCountPos));
    if (materialShift != 0) {
      _shiftRef(middle2a, _activeLayerAnchorRel, materialShift);
    }
    out.addAll(middle2a);

    out.addAll(_u32(_origDefCount + _definitionCount));
    if (_definitionWriter != null) {
      out.addAll(_definitionWriter!.buf);
    }

    // defCountPos+4 -> rootCountPos: any already-existing definitions
    // (none, in the blank scaffold), unmodified.
    out.addAll(_data.sublist(_defCountPos + 4, _rootCountPos));

    out.addAll(_u32(newRootCount));
    out.addAll(_data.sublist(_rootCountPos + 4, _tailPos));
    out.addAll(_geometryWriter!.buf);

    final tail = List<int>.from(_data.sublist(_tailPos));
    final totalTailShift = materialShift + layerShift + definitionShift + geometryShift;
    // _tailRefPositions and _isoCameraTailPatches's positions both index
    // into this same tail buffer. A ref-shift that widens to the 6-byte
    // escape form (see _shiftRef) grows the buffer at that point, pushing
    // every later position forward - so every action is applied in
    // ascending original-offset order, tracking that growth.
    final isoPatches = <int, List<int>>{for (final e in _isoCameraTailPatches) e.$1: e.$2};
    final actions = <(int pos, bool isRef)>[
      for (final pos in _tailRefPositions) (pos, true),
      for (final pos in isoPatches.keys) (pos, false),
    ]..sort((a, b) => a.$1.compareTo(b.$1));
    var growth = 0;
    for (final (pos, isRef) in actions) {
      final here = pos + growth;
      if (isRef) {
        growth += _shiftRef(tail, here, totalTailShift);
      } else {
        final patch = isoPatches[pos]!;
        tail.setRange(here, here + patch.length, patch);
      }
    }
    out.addAll(tail);
    return Uint8List.fromList(out);
  }

  /// Write the finished file to [path].
  void save(String path) {
    File(path).writeAsBytesSync(toBytes());
  }
}

/// FF FF <2-byte wildcard schema> 06 00 'CLayer' - the byte pattern of a
/// new-class declaration for CLayer, wherever it first appears in the
/// scaffold. Mirrors create.py's `_CLAYER_PATTERN` regex search (`re.
/// escape(b"\xff\xff") + b".." + re.escape(struct.pack("<H", 6) +
/// b"CLayer")`), reimplemented as a plain byte scan since Dart's RegExp
/// only operates on strings, not arbitrary binary wildcards.
int _findClayerClassPattern(Uint8List data) {
  final suffix = [0x06, 0x00, ...ascii.encode('CLayer')];
  final limit = data.length - 4 - suffix.length;
  for (var i = 0; i <= limit; i++) {
    if (data[i] != 0xFF || data[i + 1] != 0xFF) continue;
    var ok = true;
    for (var j = 0; j < suffix.length; j++) {
      if (data[i + 4 + j] != suffix[j]) {
        ok = false;
        break;
      }
    }
    if (ok) return i;
  }
  return -1;
}

/// Start building a new legacy-format (v17) `.skp` file from scratch.
///
/// ```dart
/// final builder = create();
/// final red = builder.addMaterial('Red', [255, 0, 0]);
/// final roof = builder.addLayer('Roof');
/// builder.addFace(
///   [(0, 0, 0), (100, 0, 0), (100, 100, 0), (0, 100, 0)],
///   material: red,
///   layer: roof,
/// );
/// builder.save('output.skp');
/// ```
///
/// See this library's own doc comment for the current scope and
/// limitations (no inline-declared nested groups; inches only).
SkpBuilder create() => SkpBuilder();
