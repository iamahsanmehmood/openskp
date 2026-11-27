/// Create new legacy-format (v17) `.skp` files from scratch.
///
/// This is a genuine, from-scratch binary writer for the same MFC
/// `CArchive` object-stream format [legacy] reads - built by inverting that
/// reader's own, already-proven decoding logic (the class-ref/back-ref
/// protocol, entity preambles, drawbase records) and constructing the empty
/// SketchUp 2017 document in code via `blank_document.dart`, ported
/// field-for-field from `openskp.create` (Python). No SketchUp SDK is called at
/// runtime and no SDK-written file or byte blob is bundled; this module never
/// links against or shells out to any proprietary library.
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
/// **Empty-document construction.** [buildBlankV17] in `blank_document.dart`
/// synthesizes the entire classic v17 empty document field by field in Dart and
/// returns both the serialized bytes and the named offsets and MFC store-map
/// slots where this module splices new records and renumbers downstream object
/// references.
library;

import 'dart:convert';
import 'dart:io';
import 'dart:math';
import 'dart:typed_data';

import 'package:crypto/crypto.dart';
import 'package:meta/meta.dart';

import 'blank_document.dart';
import 'face_groups.dart' show faceUvBasis;

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

/// `(translation, matrix3x3, materialSlot, layerSlot, attributeDicts,
/// hidden)` - a group's deferred self-placement, recorded when
/// [SkpBuilder.addGroup]'s body closes and flushed the first time anything
/// actually needs the root geometry writer (see
/// [SkpBuilder._ensureGeometryWriter]).
typedef GroupPlacement = (Point3, Matrix3x3?, int, int, List<(String, Map<String, Object>)>, bool);

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

const String _scaffoldSha256 = scaffoldSha256;

BlankDocument _loadBlankDocument() {
  final doc = buildBlankV17();
  final digest = sha256.convert(doc.data).toString();
  if (digest != _scaffoldSha256) {
    throw SkpWriteError(
      'built blank document does not match the expected SHA-256 ($digest != $_scaffoldSha256)',
    );
  }
  return doc;
}

final BlankDocument _blankDoc = _loadBlankDocument();

final List<int> _tailRefPositions = _blankDoc.tailRefPositions;

const Point3 _isoCameraUp = (-0.4082482904638631, 0.4082482904638631, 0.8164965809277261);
const double _isoCameraOrthoHeight = 92.82032302755093;
final Uint8List _isoCameraBody = packCameraBody(
  eye: (100.0, -100.0, 100.0),
  target: (0.0, 0.0, 0.0),
  up: _isoCameraUp,
  clipNear: 1.0,
  clipFar: 1000.0,
  perspective: false,
  fov: 30.0,
  orthoHeight: _isoCameraOrthoHeight,
);
final int _isoCameraPrefixOffset = _blankDoc.cameraBodyOffset + 6;
final List<int> _isoCameraPrefixPatch = _isoCameraBody.sublist(6, 104);
final List<(int, List<int>)> _isoCameraTailPatches = [
  (_blankDoc.savedStyleGuidTailRel, uuidToBytesLe('6169a8d0-443c-472d-99a4-667d1adfa836')),
  (
    _blankDoc.workingStyleGuidTailRel,
    uuidToBytesLe('44c8534e-0277-4692-bba9-5827bba7e261').sublist(0, 15),
  ),
];

final int _activeLayerAnchorRel = _blankDoc.activeLayerRefPos - _blankDoc.layerInsertPos;

final int _pidCounterPos = _blankDoc.pidCounterOffset;

const int _materialSchema = 12;
const int _dibSchema = 3;

/// Byte pattern found in one SDK-authored textured-material sample's
/// "applied height" field, decoding to ~1.29e-231 as an f64 - not a
/// meaningful height value. Confirmed 2026-08-27 via real SketchUp
/// screenshots that a material written with this exact value renders as a
/// corrupted, vertically-smeared texture, so it was never a genuine
/// "never scaled" default - almost certainly uninitialized memory in the
/// one sample file this was calibrated against, given
/// [writeTexturedMaterial]'s applied WIDTH is unconditionally 1.0 (a
/// clearly deliberate value) with no equivalent garbage pattern. No
/// longer used as this project's own default (that method now defaults to
/// 1.0 instead) - kept only as a documented historical artifact of what
/// real SketchUp can apparently write here.
// ignore: unused_element
final List<int> _textureHSentinel = _hexBytes('f0ffffffffffff0f');

const int _definitionSchema = 11;
const int _instanceSchema = 6;
const int _groupSchema = 1;

/// UNVERIFIED - unlike every other schema constant here, not calibrated
/// against a real SketchUp-authored file: no sample containing a CImage
/// entity (File > Import > Image) was available (ported from the Python
/// writer, same caveat there - see create.py's own comment). legacy.dart's
/// image reader never branches on schema the way instance/group reading
/// does, so this project's own reader round-trips correctly regardless of
/// the exact value - this only affects whether real SketchUp accepts the
/// file. Chosen to match [_instanceSchema] for the same reason as Python's
/// `_IMAGE_SCHEMA`: CImage's read path always expects the trailing GUID
/// unconditionally, the same "always present" shape CComponentInstance has.
const int _imageSchema = 6;
const int _thumbnailSchema = 1;

/// Pre-declared class slots recorded when building the blank document's prefix.
final int _ccameraSlot = _blankDoc.ccameraSlot;
final int _attrContainerSlot = _blankDoc.attrContainerSlot;
final int _attributeNamedSlot = _blankDoc.attributeNamedSlot;

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

const int _sectionPlaneSchema = 3;
const int _dimensionLinearSchema = 6;
const int _skFontSchema = 1;
const int _textSchema = 9;
const int _constructionLineSchema = 1;
const int _constructionPointSchema = 0;

/// Byte-exact templates harvested from a real SketchUp 2017 file (28
/// dimensions, capilla quiroz corpus model); see
/// docs/dimension-record-notes.md for the full layout. Ported verbatim
/// from create.py's own `_DIM_FONT_PAYLOAD`/`_TEXT_DELIM`.
final List<int> _dimFontPayload = _hexBytes(
  '000000' // preamble: null attrs + pid mask 0
  'fffeff065400610068006f006d006100' // "Tahoma"
  '0000'
  '08000000'
  '00'
  'ecf57abd5eaf2340', // height f64
);

/// Sentinel a CConstructionLine's start/end distance-parameter carries when
/// unbounded in that direction - real SketchUp's own value, ground-truth
/// verified against `Sketchup::ConstructionLine#start`/`#end` returning nil
/// for that side.
const double _clineInfinite = 1e30;

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

int _readU32At(List<int> buf, int pos) =>
    buf[pos] | (buf[pos + 1] << 8) | (buf[pos + 2] << 16) | (buf[pos + 3] << 24);

int _readU64At(List<int> buf, int pos) =>
    _readU32At(buf, pos) + _readU32At(buf, pos + 4) * 0x100000000;

void _patchU32(List<int> buf, int pos, int val) {
  buf[pos] = val & 0xFF;
  buf[pos + 1] = (val >> 8) & 0xFF;
  buf[pos + 2] = (val >> 16) & 0xFF;
  buf[pos + 3] = (val >> 24) & 0xFF;
}

void _patchU64(List<int> buf, int pos, int val) {
  _patchU32(buf, pos, val);
  _patchU32(buf, pos + 4, val ~/ 0x100000000);
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

/// Turn `(point, (u, v))` pins from tiles into texture-space inches so
/// SketchUp's read-side division by `(appliedWidth, appliedHeight)` lands
/// back on `(u, v)`.
List<(Point3, (double, double))>? _scalePins(
  List<(Point3, (double, double))>? pins,
  Map<int, (double, double)> appliedSizes,
  int? material,
) {
  if (pins == null || material == null) return pins;
  final size = appliedSizes[material];
  if (size == null) return pins;
  final (w, h) = size;
  if (w == 1.0 && h == 1.0) return pins;
  return [for (final (pt, (u, v)) in pins) (pt, (u * w, v * h))];
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
/// [faceUvBasis]) - each correspondence's world point is projected onto
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
  List<(Point3, (double, double))> pairs,
  Point3 normal,
) {
  return _solveUvMatrix(pairs, faceUvBasis(normal));
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

const List<int> _windowsGuidOrder = [
  3, 2, 1, 0, 5, 4, 7, 6, 8, 9, 10, 11, 12, 13, 14, 15,
];

/// A random v4 UUID's 16 bytes in Windows GUID byte order (Data1/Data2/Data3
/// little-endian), matching CArchive's on-disk GUID layout.
List<int> _uuid4Bytes() {
  final wire = List<int>.generate(16, (_) => _rnd.nextInt(256));
  wire[6] = (wire[6] & 0x0F) | 0x40;
  wire[8] = (wire[8] & 0x3F) | 0x80;
  return [for (final idx in _windowsGuidOrder) wire[idx]];
}

List<int> _archiveUuid([String? value]) {
  if (value == null || value.isEmpty) {
    return _uuid4Bytes();
  }
  String hex;
  if (value.length == 32) {
    hex = value;
  } else if (value.length == 36 &&
      value[8] == '-' &&
      value[13] == '-' &&
      value[18] == '-' &&
      value[23] == '-') {
    final sb = StringBuffer();
    for (var i = 0; i < value.length; i++) {
      if (i != 8 && i != 13 && i != 18 && i != 23) sb.write(value[i]);
    }
    hex = sb.toString();
  } else {
    throw SkpWriteError('GUID must contain 32 hex digits or use 8-4-4-4-12 notation');
  }
  int hexDigit(int code) {
    if (code >= 0x30 && code <= 0x39) return code - 0x30;
    if (code >= 0x61 && code <= 0x66) return code - 0x61 + 10;
    if (code >= 0x41 && code <= 0x46) return code - 0x41 + 10;
    return -1;
  }

  final wire = List<int>.filled(16, 0);
  for (var i = 0; i < 16; i++) {
    final hi = hexDigit(hex.codeUnitAt(2 * i));
    final lo = hexDigit(hex.codeUnitAt(2 * i + 1));
    if (hi < 0 || lo < 0) {
      throw SkpWriteError('GUID contains a non-hex digit');
    }
    wire[i] = (hi << 4) | lo;
  }
  return [for (final idx in _windowsGuidOrder) wire[idx]];
}

List<(String, Map<String, Object>)> _attrDicts(
  Map<String, Object>? attributes,
  String name, [
  List<(String, Map<String, Object>)> attributeDicts = const [],
]) {
  final hasAttributes = attributes != null && attributes.isNotEmpty;
  if (hasAttributes && attributeDicts.isNotEmpty) {
    throw SkpWriteError(
      'pass either attributes/attributeDictName or attributeDicts, not both',
    );
  }
  if (hasAttributes) return [(name, attributes)];
  if (attributeDicts.isEmpty) return const [];
  return List<(String, Map<String, Object>)>.of(attributeDicts);
}

(int, int) _edgeKey(int a, int b) => a <= b ? (a, b) : (b, a);

((double, double, double), (double, double), int, bool) _prepareText(
  String text,
  Point3 point,
  Point3 leader,
  int leaderType,
  int arrowType,
  (double, double)? screenPosition,
  bool displayLeader,
) {
  final units = text.codeUnits;
  if (units.length >= 0xFFFFFFFF) {
    throw SkpWriteError('string too long to encode');
  }
  for (var i = 0; i < units.length; i++) {
    final cu = units[i];
    if (cu >= 0xD800 && cu <= 0xDBFF) {
      if (i + 1 >= units.length || units[i + 1] < 0xDC00 || units[i + 1] > 0xDFFF) {
        throw SkpWriteError('addText text contains an unpaired UTF-16 surrogate');
      }
      i++;
    } else if (cu >= 0xDC00 && cu <= 0xDFFF) {
      throw SkpWriteError('addText text contains an unpaired UTF-16 surrogate');
    }
  }
  final lb = (point.$1 + leader.$1, point.$2 + leader.$2, point.$3 + leader.$3);
  if (!point.$1.isFinite ||
      !point.$2.isFinite ||
      !point.$3.isFinite ||
      !leader.$1.isFinite ||
      !leader.$2.isFinite ||
      !leader.$3.isFinite ||
      !lb.$1.isFinite ||
      !lb.$2.isFinite ||
      !lb.$3.isFinite) {
    throw SkpWriteError('addText coordinates must be finite');
  }
  if (leaderType < 0 || leaderType > 2 || arrowType < 0 || arrowType > 4) {
    throw SkpWriteError('addText invalid leader or arrow type');
  }
  var screen = (0.0, 0.0);
  if (screenPosition != null) {
    if (!screenPosition.$1.isFinite ||
        !screenPosition.$2.isFinite ||
        screenPosition.$1 < 0.0 ||
        screenPosition.$1 > 1.0 ||
        screenPosition.$2 < 0.0 ||
        screenPosition.$2 > 1.0) {
      throw SkpWriteError('addText screen position must be between zero and one');
    }
    screen = screenPosition;
  }
  final effLeaderType = screenPosition != null ? 0 : (leaderType == 0 ? 2 : leaderType);
  final effDisplayLeader = displayLeader && (screenPosition != null || leaderType != 0);
  return (lb, screen, effLeaderType, effDisplayLeader);
}

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

  /// One CSkFont per file, serialized inline at the first dimension/text's
  /// font field (exactly as SketchUp writes it) and re-used by back-ref
  /// afterwards - see [writeDimension]/[writeText].
  int? _dimFontSlot;

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
    for (final (dictName, entries) in attributeDicts) {
      writeAttributeDict(dictName, entries);
    }
    if (frontMatrix != null || backMatrix != null) {
      writeFaceTextureCoords(frontMatrix, backMatrix);
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
    if (n >= 0xFFFFFFFF) {
      throw SkpWriteError('string too long to encode');
    }
    buf.addAll(const [0xFF, 0xFE, 0xFF]);
    if (n < 0xFF) {
      buf.add(n);
    } else if (n < 0xFFFE) {
      buf.add(0xFF);
      buf.addAll(_u16(n));
    } else {
      buf.add(0xFF);
      buf.addAll(_u16(0xFFFF));
      buf.addAll(_u32(n));
    }
    for (final u in units) {
      buf.addAll(_u16(u));
    }
  }

  /// Write the first dimension/text's CSkFont record inline, or back-ref
  /// the one already written earlier in this file - shared 1-per-file
  /// state, same as create.py's own `_dim_font_slot`.
  void _writeDimFontRef() {
    if (_dimFontSlot == null) {
      _dimFontSlot = _newOfKnownClass('CSkFont', schema: _skFontSchema);
      buf.addAll(_dimFontPayload);
    } else {
      _backref(_dimFontSlot!);
    }
  }

  /// Add a FREE linear dimension between two explicit points (inches,
  /// world space). [offset] is the dimension line's offset from the
  /// measured segment, in inches (signed).
  ///
  /// The record layout is the byte-exact one the real SketchUp SDK writes
  /// for free dimensions (generated via SketchUpAPI and harvested - see
  /// docs/dimension-record-notes.md): connection type 1 with the point
  /// stored inline in each connection block and null object refs. Free
  /// dimensions render in any orientation; anchored (type 2) dimensions
  /// are a future refinement.
  void writeDimension(
    Point3 p1,
    Point3 p2, {
    double offset = 10.0,
    Point3? offsetVector,
    int? layer,
    bool hidden = false,
  }) {
    var normal = (0.0, 0.0, 1.0);
    var xaxis = (1.0, 0.0, 0.0);
    var resolvedOffset = offset;
    if (offsetVector != null) {
      if (!p1.$1.isFinite ||
          !p1.$2.isFinite ||
          !p1.$3.isFinite ||
          !p2.$1.isFinite ||
          !p2.$2.isFinite ||
          !p2.$3.isFinite ||
          !offsetVector.$1.isFinite ||
          !offsetVector.$2.isFinite ||
          !offsetVector.$3.isFinite) {
        throw SkpWriteError('addDimension coordinates must be finite');
      }
      if (p1.$1 == p2.$1 && p1.$2 == p2.$2 && p1.$3 == p2.$3) {
        throw SkpWriteError('addDimension endpoints coincide');
      }
      final dx = p2.$1 - p1.$1;
      final dy = p2.$2 - p1.$2;
      final dz = p2.$3 - p1.$3;
      final length = sqrt(dx * dx + dy * dy + dz * dz);
      if (!length.isFinite || length == 0.0) {
        throw SkpWriteError('addDimension segment length must be finite and nonzero');
      }
      xaxis = (dx / length, dy / length, dz / length);
      final nx = xaxis.$2 * offsetVector.$3 - xaxis.$3 * offsetVector.$2;
      final ny = xaxis.$3 * offsetVector.$1 - xaxis.$1 * offsetVector.$3;
      final nz = xaxis.$1 * offsetVector.$2 - xaxis.$2 * offsetVector.$1;
      resolvedOffset = sqrt(nx * nx + ny * ny + nz * nz);
      if (!resolvedOffset.isFinite) {
        throw SkpWriteError('addDimension offset must be finite');
      }
      if (resolvedOffset > 0.0) {
        normal = (nx / resolvedOffset, ny / resolvedOffset, nz / resolvedOffset);
      } else {
        final seed = xaxis.$3.abs() < 0.9 ? (0.0, 0.0, 1.0) : (0.0, 1.0, 0.0);
        final cx = xaxis.$2 * seed.$3 - xaxis.$3 * seed.$2;
        final cy = xaxis.$3 * seed.$1 - xaxis.$1 * seed.$3;
        final cz = xaxis.$1 * seed.$2 - xaxis.$2 * seed.$1;
        final clen = sqrt(cx * cx + cy * cy + cz * cz);
        normal = (cx / clen, cy / clen, cz / clen);
      }
    } else {
      if (!p1.$1.isFinite ||
          !p1.$2.isFinite ||
          !p1.$3.isFinite ||
          !p2.$1.isFinite ||
          !p2.$2.isFinite ||
          !p2.$3.isFinite ||
          !resolvedOffset.isFinite) {
        throw SkpWriteError('addDimension coordinates must be finite');
      }
      if (p1.$1 == p2.$1 && p1.$2 == p2.$2 && p1.$3 == p2.$3) {
        throw SkpWriteError('addDimension endpoints coincide');
      }
    }
    _newOfKnownClass('CDimensionLinear', schema: _dimensionLinearSchema);
    _preamble();
    _drawbase(mat: 0, layer: layer ?? 0, hidden: hidden);
    _writeStr(''); // auto-computed measurement text
    _writeDimFontRef();
    // connection 1: [u8 0][u32 0][u32 type=1][u32 4][point A], null ref
    buf.addAll(List<int>.filled(5, 0));
    buf.addAll(_u32(1));
    buf.addAll(_u32(4));
    buf.addAll(_f64(p1.$1));
    buf.addAll(_f64(p1.$2));
    buf.addAll(_f64(p1.$3));
    buf.addAll(_u16(0));
    // connection 2: [u16 0][f64 0][u32 type=1][u32 4][point B], null ref
    buf.addAll(List<int>.filled(10, 0));
    buf.addAll(_u32(1));
    buf.addAll(_u32(4));
    buf.addAll(_f64(p2.$1));
    buf.addAll(_f64(p2.$2));
    buf.addAll(_f64(p2.$3));
    buf.addAll(_u16(0));
    // placement block: SDK free-dimension defaults or constrained plane + offset
    buf.addAll(List<int>.filled(2, 0));
    buf.addAll(_f64(0.0));
    for (final v in [normal.$1, normal.$2, normal.$3, xaxis.$1, xaxis.$2, xaxis.$3]) {
      buf.addAll(_f64(v));
    }
    buf.addAll(_u32(offsetVector != null ? 1 : 0));
    buf.addAll(_f64(resolvedOffset));
    buf.addAll(_f64(0.0));
    buf.addAll(_u32(1));
  }

  /// Add a leader or screen text anchored at [point] (inches, owner-local),
  /// with the label floating at `point + leader`.
  void writeText(
    String text,
    Point3 point, {
    Point3 leader = (15.0, 15.0, 15.0),
    int? layer,
    bool hidden = false,
    int leaderType = 2,
    int arrowType = 3,
    (double, double)? screenPosition,
    bool displayLeader = true,
  }) {
    final (lb, screen, effLeaderType, effDisplayLeader) = _prepareText(
      text,
      point,
      leader,
      leaderType,
      arrowType,
      screenPosition,
      displayLeader,
    );

    _newOfKnownClass('CText', schema: _textSchema);
    _preamble();
    _drawbase(mat: 0, layer: layer ?? 0, hidden: hidden);
    _writeDimFontRef();
    buf.addAll(_f64(screen.$1));
    buf.addAll(_f64(screen.$2));
    buf.addAll(_u32(1));
    buf.addAll(_u32(4));
    buf.addAll(_f64(point.$1));
    buf.addAll(_f64(point.$2));
    buf.addAll(_f64(point.$3));
    buf.addAll(List<int>.filled(12, 0));
    buf.addAll(_f64(lb.$1));
    buf.addAll(_f64(lb.$2));
    buf.addAll(_f64(lb.$3));
    buf.addAll(List<int>.filled(16, 0));
    buf.addAll(_f64(1.0));
    buf.addAll(_u32(effLeaderType));
    buf.addAll(_u32(1));
    buf.addAll(_u16(1));
    buf.addAll(_u32(arrowType));
    buf.add(effDisplayLeader ? 1 : 0);
    _writeStr(text);
    buf.addAll(List<int>.filled(5, 0));
  }

  /// Add a construction/guide line (SketchUp's Construction Line tool).
  /// Pass exactly one of [point2] (a bounded segment between [point] and
  /// [point2]) or [direction] (an unbounded guide line through [point]).
  ///
  /// Ground truth (real SketchUp 2025, SDK/Ruby cross-checked against both
  /// a v2020-downgrade save and a genuinely v17-native save): the record
  /// stores a point + normalized direction + two signed distance
  /// parameters along that direction marking the visible segment's
  /// start/end. An unbounded direction is written as the real ±1e30
  /// sentinel SketchUp itself uses.
  void writeConstructionLine(
    Point3 point, {
    Point3? point2,
    Point3? direction,
    int layer = 0,
    bool hidden = false,
  }) {
    final (dx, dy, dz, startParam, endParam) = _resolveConstructionLineParams(
      point,
      point2: point2,
      direction: direction,
    );
    _newOfKnownClass('CConstructionLine', schema: _constructionLineSchema);
    _preamble();
    _drawbase(layer: layer, hidden: hidden);
    for (final v in [point.$1, point.$2, point.$3, dx, dy, dz, startParam, endParam]) {
      buf.addAll(_f64(v));
    }
    buf.addAll(List<int>.filled(4, 0)); // trailer
  }

  /// Add a construction/guide point (SketchUp's Construction Point tool)
  /// at [position] (inches, world space).
  ///
  /// Ground truth (real SketchUp 2025, SDK/Ruby cross-checked): a second,
  /// always-zero 3-double block and a trailing zero byte follow the
  /// position - reserved/unused, written as zero to match every real-file
  /// sample seen.
  void writeConstructionPoint(Point3 position, {int layer = 0, bool hidden = false}) {
    _validateFinitePoint3(position, 'addConstructionPoint');
    _newOfKnownClass('CConstructionPoint', schema: _constructionPointSchema);
    _preamble();
    _drawbase(layer: layer, hidden: hidden);
    for (final v in [position.$1, position.$2, position.$3, 0.0, 0.0, 0.0]) {
      buf.addAll(_f64(v));
    }
    buf.addAll(List<int>.filled(1, 0));
  }

  /// Add a section plane (SketchUp's Section Plane tool) through [point]
  /// with the given [normal] (need not be unit length), matching
  /// `Entities#add_section_plane([point, normal])`.
  ///
  /// Ground truth (real SketchUp 2025, SDK/Ruby cross-checked against a
  /// genuinely v17-native save): the record is preamble + drawbase + the
  /// plane as 4 doubles `(a, b, c, d)` satisfying `a*x + b*y + c*z + d = 0`
  /// for every point on the plane - the same implicit form
  /// `Sketchup::SectionPlane#get_plane` returns (normal normalized,
  /// `d = -(normal . point)`). A name/short-label pair can follow on v18+
  /// saves per the reader (legacy.dart's `_readSectionPlane`) - omitted
  /// here since this writer only ever produces v17-tagged files, same
  /// scope as [writeDimension]/[writeText]/[writeConstructionLine].
  void writeSectionPlane(Point3 point, Point3 normal) {
    final nlen = sqrt(normal.$1 * normal.$1 + normal.$2 * normal.$2 + normal.$3 * normal.$3);
    if (nlen == 0.0) {
      throw SkpWriteError('addSectionPlane: normal must be nonzero');
    }
    final a = normal.$1 / nlen;
    final b = normal.$2 / nlen;
    final c = normal.$3 / nlen;
    final d = -(a * point.$1 + b * point.$2 + c * point.$3);
    _newOfKnownClass('CSectionPlane', schema: _sectionPlaneSchema);
    _preamble();
    _drawbase();
    buf.addAll(_f64(a));
    buf.addAll(_f64(b));
    buf.addAll(_f64(c));
    buf.addAll(_f64(d));
  }

  /// Write one solid-color `CMaterial` record and return its slot.
  int writeMaterial(String name, (int, int, int, int) rgba, {double? opacity}) {
    final slot = _newOfKnownClass('CMaterial', schema: _materialSchema);
    _preamble();
    _writeStr(name);
    buf.addAll(_u16(0)); // texflag: solid color, no texture
    buf.addAll([rgba.$1, rgba.$2, rgba.$3, rgba.$4]);
    _writeStr(''); // texture path (empty - no texture)
    buf.addAll(List<int>.filled(8, 0)); // unknown/padding - ground truth is all-zero
    // Stored TRANSPARENCY (0 = opaque); see writeTexturedMaterial.
    buf.addAll(_f64(opacity == null ? 1.0 : 1.0 - opacity));
    buf.add(opacity == null ? 0 : 1); // use_opacity gates it
    return slot;
  }

  /// Write one image-textured `CMaterial` record (embedding [imageBytes]
  /// verbatim inside a `CDib` sub-object) and return its slot. [subtype] is
  /// CDib's image format tag (4 for PNG, 1 for JPEG - see
  /// [_detectImageSubtype]).
  ///
  /// [appliedWidth]/[appliedHeight] both default to 1.0. Pass the
  /// material's real-world tile size for a textured material used with
  /// default (unpositioned) projection, to make the texture repeat at a
  /// specific size instead of every 1 inch (real SketchUp writes the
  /// material's own size here - a file authored in SketchUp Web carries
  /// 8.0 x 16.0 for a brick) - the reader's own ground-truth-derived UV
  /// formula divides a face's final UV by the material's applied
  /// width/height, for a default-projected face exactly as much as a
  /// positioned (`frontUv`/`backUv`) one. Until 2026-08-28 this defaulted
  /// to a corrupted sentinel byte pattern instead (see
  /// [_textureHSentinel]'s own comment) - confirmed via real SketchUp
  /// screenshots to render as a streaky, vertically-smeared texture
  /// regardless of projection mode.
  int writeTexturedMaterial(String name, Uint8List imageBytes, String texturePath, int subtype,
      {double? appliedHeight, double? appliedWidth, double? opacity}) {
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
    // Applied size: how much MODEL SPACE one tile of the image covers, in
    // inches - two plain f64s. For a texture applied WITHOUT positioning it
    // is the only thing that says how big the image is - such faces carry
    // no per-face UV record at all, so a wrong size here is the whole
    // mapping wrong.
    buf.addAll(_f64(appliedWidth ?? 1.0));
    buf.addAll(_f64(appliedHeight ?? 1.0));
    _writeStr(texturePath);
    // avg color (RGBA + pad + RGBA repeated) - neutral opaque white (alpha=255
    // in both color slots, matching SketchUp's C SDK; colorization is gated on
    // the u32 material-type field below).
    buf.addAll([255, 255, 255, 255, 0, 255, 255, 255, 255]);
    _writeStr(''); // second name field - empty in ground truth
    buf.addAll(_u32(1)); // blob (colorize-related, ground truth: 1, 0)
    buf.addAll(_u32(0));
    // Opacity, and the u8 that GATES it. The stored f64 is TRANSPARENCY
    // (0 = opaque) - the reader turns it into the opacity factor exposed
    // with `1.0 - stored`, and only when the flag is set - so an
    // [opacity] argument here is written inverted and round-trips as
    // itself. Hardcoding 1.0/false meant a translucent material came out
    // solid: a pool's water at 0.6 exported as an opaque slab.
    buf.addAll(_f64(opacity == null ? 1.0 : 1.0 - opacity));
    buf.add(opacity == null ? 0 : 1);
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
  int writeDefinitionTail(String name, {bool isGroup = false, List<int>? guid}) {
    buf.addAll(_u32(0)); // nrel: CRelationship count - always 0, not supported
    buf.addAll(_u16(0));
    buf.addAll(guid ?? _uuid4Bytes());
    _writeStr(name);
    _writeStr(''); // description - empty in ground truth
    _writeStr(''); // second name field - empty in ground truth
    buf.addAll(_u32(DateTime.now().toUtc().millisecondsSinceEpoch ~/ 1000));
    // 43-byte gap; byte -9 carries the always-faces-camera/shadows-face-sun
    // behavior flags, both left off (neither exposed by this writer yet). The
    // last u32 is 1 for the definition of a group and 0 for a component's (what
    // SketchUp's own API reports as SUComponentType_Group).
    final groupFlagPos = buf.length + 39;
    buf.addAll(List<int>.filled(39, 0));
    buf.addAll(_u32(isGroup ? 1 : 0));
    writeThumbnail();
    // where that u32 sits, so a definition first closed as a component can be flagged later, when
    // it is placed as a group
    return groupFlagPos;
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
  /// and its attribute pointer: unlike `CComponentInstance` (which always
  /// carries a real, if often empty, `CAttributeContainer` regardless of
  /// whether attributes are given), a group only gets one when
  /// [attributeDicts] is actually given - matching `writeFace`'s
  /// conditional pattern instead. A real production Group WITH attributes
  /// (SketchUp 2020 export, ground truth) carries a genuine
  /// `CAttributeContainer` at this exact schema; a never-attributed group
  /// still correctly gets a null pointer either way (openskp#261).
  int writeGroup(
    int definitionSlot,
    String name,
    Point3 translation,
    Matrix3x3? matrix3x3,
    int groupMaterial,
    int groupLayer, {
    List<(String, Map<String, Object>)> attributeDicts = const [],
    bool hidden = false,
  }) {
    _writeInstanceLike(
      className: 'CGroup',
      schema: _groupSchema,
      realAttrs: attributeDicts.isNotEmpty,
      definitionSlot: definitionSlot,
      name: name,
      translation: translation,
      matrix3x3: matrix3x3,
      mat: groupMaterial,
      layer: groupLayer,
      attributeDicts: attributeDicts,
      hidden: hidden,
    );
    return 1;
  }

  /// Write one `CImage` placing [definitionSlot] (the quad + texture
  /// material [addImage] built for it) - return contract matches
  /// [writeInstance]/[writeGroup] (always 1).
  ///
  /// legacy.dart's image reader treats CImage as "instance-shaped":
  /// preamble, drawbase, a definition back-ref, a 3x4 placement, a constant
  /// 1.0, a source-path string, and a 16-byte GUID - field-for-field
  /// identical in count and order to [writeInstance]'s own
  /// matrix3x3(9)+translation(3)+1.0(1)=13 f64s, name string, GUID. The
  /// source-path string is always empty - ground truth shows real SketchUp
  /// writes it empty too. No material argument - an Image entity isn't
  /// painted a material the way a face or instance can be; its appearance
  /// comes entirely from the definition's own textured face.
  int writeImage(
    int definitionSlot,
    Point3 translation,
    Matrix3x3? matrix3x3,
    int imageLayer, [
    bool hidden = false,
  ]) {
    _writeInstanceLike(
      className: 'CImage',
      schema: _imageSchema,
      realAttrs: false,
      definitionSlot: definitionSlot,
      name: '',
      translation: translation,
      matrix3x3: matrix3x3,
      mat: 0,
      layer: imageLayer,
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
    final curveForSeg = List<(CurveParams?, int, int)?>.filled(pairCount, null);
    if (curveParams != null || polylineNumEdges != null) {
      final tempSlots = <Point3, int>{};
      var nextTemp = -1;
      final simEdges = <(int, int)>{...edgeRegistry.keys};
      final segIsNew = List<bool>.filled(pairCount, false);
      for (var i = 0; i < pairCount; i++) {
        final p1 = points[i];
        final p2 = points[(i + 1) % n];
        if (p1 == p2) continue;
        final s1 = vertexSlots[p1] ?? tempSlots.putIfAbsent(p1, () => nextTemp--);
        final s2 = vertexSlots[p2] ?? tempSlots.putIfAbsent(p2, () => nextTemp--);
        if (simEdges.add(_edgeKey(s1, s2))) {
          segIsNew[i] = true;
        }
      }
      var runs = <List<int>>[];
      var curRun = <int>[];
      for (var i = 0; i < pairCount; i++) {
        if (segIsNew[i]) {
          curRun.add(i);
        } else if (curRun.isNotEmpty) {
          runs.add(curRun);
          curRun = <int>[];
        }
      }
      if (curRun.isNotEmpty) runs.add(curRun);
      if (closed && runs.length >= 2 && runs.first.first == 0 && runs.last.last == pairCount - 1) {
        runs = [
          [...runs.last, ...runs.first],
          ...runs.sublist(1, runs.length - 1),
        ];
      }
      for (var runId = 0; runId < runs.length; runId++) {
        final run = runs[runId];
        final runLen = run.length;
        if (runLen < 2) continue;
        if (curveParams != null) {
          final (center, normal, xaxis, startAngle, endAngle, radius, numSegments) = curveParams;
          CurveParams subParams = curveParams;
          if (runLen != numSegments) {
            final s0 = run[0];
            final sweep = endAngle - startAngle;
            var a0 = startAngle + sweep * (s0 / numSegments);
            var a1 = startAngle + sweep * ((s0 + runLen) / numSegments);
            if (closed && s0 + runLen > numSegments) {
              a0 -= 2.0 * pi;
              a1 -= 2.0 * pi;
            }
            subParams = (center, normal, xaxis, a0, a1, radius, runLen);
          }
          for (final segIdx in run) {
            curveForSeg[segIdx] = (subParams, 0, runId);
          }
        } else {
          for (final segIdx in run) {
            curveForSeg[segIdx] = (null, runLen, runId);
          }
        }
      }
    }

    final edgeSlots = <int>[];
    final edgeSenses = <int>[];
    var newEntities = 0;
    final runCurveSlots = <int, int>{};

    for (var i = 0; i < pairCount; i++) {
      final p1 = points[i];
      final p2 = points[(i + 1) % n];
      if (p1 == p2) continue;
      final v1Known = vertexSlots[p1];
      final v2Known = vertexSlots[p2];
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
      for (final pt in [p1, p2]) {
        final known = vertexSlots[pt];
        if (known == null) {
          vertexSlots[pt] = _writeVertex(pt);
        } else {
          _backref(known);
        }
      }
      final spec = curveForSeg[i];
      if (spec == null) {
        _null(); // curve = None
      } else {
        final (arcSpec, polyEdges, runId) = spec;
        final existingCurveSlot = runCurveSlots[runId];
        if (existingCurveSlot != null) {
          _backref(existingCurveSlot);
        } else if (arcSpec != null) {
          final (center, normal, xaxis, startAngle, endAngle, radius, numSegments) = arcSpec;
          runCurveSlots[runId] = writeArcCurve(center, normal, xaxis, startAngle, endAngle, radius, numSegments);
        } else {
          runCurveSlots[runId] = writeCurve(polyEdges);
        }
      }
      edgeSlots.add(edgeSlot);
      edgeSenses.add(0);
      newEntities += 1;
      final s1Final = vertexSlots[p1]!;
      final s2Final = vertexSlots[p2]!;
      edgeRegistry[_edgeKey(s1Final, s2Final)] = (edgeSlot, s1Final);
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
    final frontMatrix = frontUv != null ? _uvMatrixForFace(frontUv, (nx, ny, nz)) : null;
    final backMatrix = backUv != null ? _uvMatrixForFace(backUv, (nx, ny, nz)) : null;
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
    // SketchUp stores a face's plane as n.p + d = 0 (checked on real files), so d is minus the
    // fitted n.centroid; _planeFromPolygon returns +n.centroid.
    buf.addAll(_f64(-d));
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
  for (final p in points) {
    if (!p.$1.isFinite || !p.$2.isFinite || !p.$3.isFinite) {
      throw SkpWriteError('face contains non-finite coordinates');
    }
  }
  final triangles = <List<Point3>>[];
  for (var i = 1; i < points.length - 1; i++) {
    final tri = <Point3>[points[0], points[i], points[i + 1]];
    try {
      _planeFromPolygon(tri);
    } on SkpWriteError {
      continue;
    }
    triangles.add(tri);
  }
  if (triangles.isEmpty) {
    throw SkpWriteError('triangulation has no non-degenerate faces');
  }
  var total = 0;
  for (final tri in triangles) {
    total += writer.writeFace(
      tri,
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

/// Display settings supported by the legacy v17 document scaffold.
class DocumentOptions {
  /// Native `UnitsOptions` values: `0`=inches, `1`=feet, `2`=mm, `3`=cm,
  /// `4`=metres, `5`=yards.
  final int lengthUnit;
  final int lengthPrecision;

  /// Native style item IDs mapped to scalar values (colours are packed ABGR
  /// `uint32` values).
  final Map<int, double> styleItems;

  const DocumentOptions({
    this.lengthUnit = 0,
    this.lengthPrecision = 3,
    this.styleItems = const {},
  });
}

int _styleU32(double v) => v.truncate() & 0xFFFFFFFF;

void _patchDocumentPrefix(List<int> prefix, DocumentOptions options) {
  final u = _blankDoc.unitOptionOffsets;
  _patchU32(prefix, u['LengthPrecision']!, options.lengthPrecision);
  _patchU32(prefix, u['LengthFormat']!, 0); // LengthFormat: decimal
  _patchU32(prefix, u['LengthUnit']!, options.lengthUnit);
  _patchU32(prefix, u['AreaUnit']!, options.lengthUnit);
  _patchU32(prefix, u['VolumeUnit']!, options.lengthUnit);
  for (final (id, offset, isBool) in _blankDoc.renderingOptionFields) {
    final v = options.styleItems[id];
    if (v == null) continue;
    if (isBool) {
      prefix[offset] = v != 0.0 ? 1 : 0;
    } else {
      _patchU32(prefix, offset, _styleU32(v));
    }
  }
}

void _patchStyleItems(List<int> tail, int startPos, DocumentOptions options) {
  var pos = startPos;
  final bd = ByteData(8);
  for (var i = 0; i < _blankDoc.styleItemCount; i++) {
    final id = _readU32At(tail, pos).toSigned(32);
    final valid = _readU32At(tail, pos + 4) != 0;
    pos += 8;
    if (!valid) continue;
    final kind = _readU32At(tail, pos);
    pos += 4;
    final size = kind == 1 ? 1 : (kind == 7 ? 8 : 4);
    final v = options.styleItems[id];
    if (v != null) {
      if (kind == 1) {
        tail[pos] = v != 0.0 ? 1 : 0;
      } else if (kind == 4) {
        _patchU32(tail, pos, _styleU32(v));
      } else if (kind == 6) {
        bd.setFloat32(0, v, Endian.little);
        for (var b = 0; b < 4; b++) tail[pos + b] = bd.getUint8(b);
      } else if (kind == 7) {
        bd.setFloat64(0, v, Endian.little);
        for (var b = 0; b < 8; b++) tail[pos + b] = bd.getUint8(b);
      }
    }
    pos += size;
  }
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
    List<(String, Map<String, Object>)> attributeDicts = const [],
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
    List<(String, Map<String, Object>)> attributeDicts = const [],
    required bool hidden,
  });

  void addConstructionLine(Point3 point, {Point3? point2, Point3? direction, int? layer, bool hidden = false}) {
    throw UnsupportedError('addConstructionLine is not implemented by this GeometryHost');
  }

  void addConstructionPoint(Point3 position, {int? layer, bool hidden = false}) {
    throw UnsupportedError('addConstructionPoint is not implemented by this GeometryHost');
  }
}

void _validateFinitePoint3(Point3 p, String caller) {
  if (!p.$1.isFinite || !p.$2.isFinite || !p.$3.isFinite) {
    throw SkpWriteError('$caller: coordinates must be finite');
  }
}

(double, double, double, double, double) _resolveConstructionLineParams(
  Point3 point, {
  Point3? point2,
  Point3? direction,
}) {
  _validateFinitePoint3(point, 'addConstructionLine');
  if ((point2 == null) == (direction == null)) {
    throw SkpWriteError('addConstructionLine: pass exactly one of point2 or direction');
  }
  if (point2 != null) {
    _validateFinitePoint3(point2, 'addConstructionLine');
    final dx = point2.$1 - point.$1;
    final dy = point2.$2 - point.$2;
    final dz = point2.$3 - point.$3;
    final length = sqrt(dx * dx + dy * dy + dz * dz);
    if (!length.isFinite || length == 0.0) {
      throw SkpWriteError('addConstructionLine: point and point2 coincide');
    }
    return (dx / length, dy / length, dz / length, 0.0, length);
  }
  final d = direction!;
  _validateFinitePoint3(d, 'addConstructionLine');
  final dlen = sqrt(d.$1 * d.$1 + d.$2 * d.$2 + d.$3 * d.$3);
  if (!dlen.isFinite || dlen == 0.0) {
    throw SkpWriteError('addConstructionLine: direction must be finite and nonzero');
  }
  return (d.$1 / dlen, d.$2 / dlen, d.$3 / dlen, -_clineInfinite, _clineInfinite);
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
  final List<int> _guid;
  int? _groupFlagPos;

  ComponentDefinitionBuilder._(
    this._skp,
    this.slot,
    this.name,
    this._countPatchPos,
    this._groupPlacement,
    this._guid,
  );

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
    List<(String, Map<String, Object>)> attributeDicts = const [],
    bool autoTriangulate = false,
    List<List<Point3>> holes = const [],
  }) {
    _checkWritable('faces');
    _skp._checkMaterialHandle(material, 'material');
    _skp._checkMaterialHandle(backMaterial, 'backMaterial');
    _skp._checkLayerHandle(layer);
    if (points.length < 3) {
      throw SkpWriteError('a face needs at least 3 points');
    }
    final resolvedAttributeDicts = _attrDicts(attributes, attributeDictName, attributeDicts);
    final scaledFront = _scalePins(frontUv, _skp._appliedSizes, material);
    final scaledBack = _scalePins(backUv, _skp._appliedSizes, backMaterial);
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
      scaledFront,
      scaledBack,
      resolvedAttributeDicts,
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
    List<(String, Map<String, Object>)> attributeDicts = const [],
  }) {
    _checkWritable('faces');
    _skp._checkMaterialHandle(material, 'material');
    _skp._checkMaterialHandle(backMaterial, 'backMaterial');
    _skp._checkLayerHandle(layer);
    if (numSegments < 3 || numSegments > 255) {
      throw SkpWriteError('numSegments must be between 3 and 255, got $numSegments');
    }
    final n = _normalize3(normal);
    final writer = _skp._definitionWriter!;
    final (u, w) = _circleBasis(n);
    final xaxis = (radius * u.$1, radius * u.$2, radius * u.$3);
    final CurveParams curveParams = (center, n, xaxis, 0.0, 2.0 * pi, radius, numSegments);
    final points = _circlePoints(center, n, radius, numSegments, u, w);
    final resolvedAttributeDicts = _attrDicts(attributes, attributeDictName, attributeDicts);
    final scaledFront = _scalePins(frontUv, _skp._appliedSizes, material);
    final scaledBack = _scalePins(backUv, _skp._appliedSizes, backMaterial);
    _newEntityCount += writer.writeFace(
      points,
      _vertexSlots,
      _edgeRegistry,
      faceMaterial: material ?? 0,
      faceLayer: layer ?? 0,
      backMaterial: backMaterial ?? 0,
      hidden: hidden,
      frontUv: scaledFront,
      backUv: scaledBack,
      attributeDicts: resolvedAttributeDicts,
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

  /// Add a construction/guide line to this definition (local coordinates) -
  /// same signature and behavior as [SkpBuilder.addConstructionLine].
  void addConstructionLine(Point3 point, {Point3? point2, Point3? direction, int? layer, bool hidden = false}) {
    _checkWritable('construction lines');
    _skp._checkLayerHandle(layer);
    _skp._definitionWriter!.writeConstructionLine(
      point,
      point2: point2,
      direction: direction,
      layer: layer ?? 0,
      hidden: hidden,
    );
    _newEntityCount++;
  }

  /// Add a construction/guide point to this definition (local coordinates) -
  /// same signature and behavior as [SkpBuilder.addConstructionPoint].
  void addConstructionPoint(Point3 position, {int? layer, bool hidden = false}) {
    _checkWritable('construction points');
    _skp._checkLayerHandle(layer);
    _skp._definitionWriter!.writeConstructionPoint(
      position,
      layer: layer ?? 0,
      hidden: hidden,
    );
    _newEntityCount++;
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
    List<(String, Map<String, Object>)> attributeDicts = const [],
    bool hidden = false,
  }) {
    _checkWritable('instances');
    _skp._checkMaterialHandle(material, 'material');
    _skp._checkLayerHandle(layer);
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
    final resolvedAttributeDicts = _attrDicts(attributes, attributeDictName, attributeDicts);
    _newEntityCount += _skp._definitionWriter!.writeInstance(
      definition.slot,
      name ?? definition.name,
      translation,
      resolved,
      material ?? 0,
      layer ?? 0,
      attributeDicts: resolvedAttributeDicts,
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
    Map<String, Object>? attributes,
    String attributeDictName = 'attributes',
    List<(String, Map<String, Object>)> attributeDicts = const [],
    bool hidden = false,
  }) {
    _checkWritable('groups');
    _skp._checkMaterialHandle(material, 'material');
    _skp._checkLayerHandle(layer);
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
    final resolvedAttributeDicts = _attrDicts(attributes, attributeDictName, attributeDicts);
    _newEntityCount += _skp._definitionWriter!.writeGroup(
      definition.slot,
      name ?? definition.name,
      translation,
      resolved,
      material ?? 0,
      layer ?? 0,
      attributeDicts: resolvedAttributeDicts,
      hidden: hidden,
    );
    // the definition behind a group is a group's definition to SketchUp: flag it (it closed earlier in the same definition buffer, which is only copied out on save). A definition placed both as a group and as a component instance reads as a group.
    final flagPos = definition._groupFlagPos;
    if (flagPos != null) _patchU32(_skp._definitionWriter!.buf, flagPos, 1);
  }

  /// Add a FREE linear dimension between two explicit points (inches,
  /// definition-local space) - same signature and behavior as
  /// [SkpBuilder.addDimension].
  void addDimension(
    Point3 p1,
    Point3 p2, {
    double? offset,
    Point3? offsetVector,
    int? layer,
    bool hidden = false,
  }) {
    _checkWritable('dimensions');
    _skp._checkLayerHandle(layer);
    if (offset != null && offsetVector != null) {
      throw SkpWriteError('pass either offset or offsetVector, not both');
    }
    _skp._definitionWriter!.writeDimension(
      p1,
      p2,
      offset: offset ?? 10.0,
      offsetVector: offsetVector,
      layer: layer,
      hidden: hidden,
    );
    _newEntityCount++;
  }

  /// Add owner-local leader or screen text, with optional tag and hidden state.
  void addText(
    String text,
    Point3 point, {
    Point3 leader = (15.0, 15.0, 15.0),
    int? layer,
    bool hidden = false,
    int leaderType = 2,
    int arrowType = 3,
    (double, double)? screenPosition,
    bool displayLeader = true,
  }) {
    _checkWritable('text');
    _skp._checkLayerHandle(layer);
    _skp._definitionWriter!.writeText(
      text,
      point,
      leader: leader,
      layer: layer,
      hidden: hidden,
      leaderType: leaderType,
      arrowType: arrowType,
      screenPosition: screenPosition,
      displayLeader: displayLeader,
    );
    _newEntityCount++;
  }

  void _close() {
    if (_newEntityCount == 0) {
      throw SkpWriteError("component definition '$name' has no geometry - add at least one face");
    }
    final writer = _skp._definitionWriter!;
    _patchU32(writer.buf, _countPatchPos, _newEntityCount);
    _groupFlagPos = writer.writeDefinitionTail(name, isGroup: _groupPlacement != null, guid: _guid);
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

  /// First persistent id this builder hands out: one past the scaffold's own
  /// counter.
  late final int _pidStart;

  /// The next free persistent ID: sections are written in order (materials,
  /// layers, definitions, root geometry), each continuing where the previous
  /// one stopped.
  int _nextPid() => (_geometryWriter ?? _definitionWriter ?? _layerWriter ?? _materialWriter).nextPid;

  /// Every material registered so far, by name - populated by
  /// [addMaterial]/[addTextureMaterial] as a side effect (they already
  /// de-dupe by name through this same map). Useful for reusing a handle
  /// after [openExisting], where every material the source file had is
  /// already here.
  final Map<String, int> materialsByName = {};
  final Map<int, (double, double)> _appliedSizes = {};
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
  DocumentOptions? _documentOptions;
  List<int>? _modelGuid;

  /// Set decimal display units and working/saved style values without scaling
  /// geometry.
  void setDocumentOptions(DocumentOptions options) {
    if (options.lengthUnit < 0 ||
        options.lengthUnit > 5 ||
        options.lengthPrecision < 0 ||
        options.lengthPrecision > 6) {
      throw SkpWriteError('invalid decimal display units or precision');
    }
    for (final v in options.styleItems.values) {
      if (!v.isFinite || v < -2147483648.0 || v > 4294967295.0) {
        throw SkpWriteError('style scalar is out of range');
      }
    }
    _documentOptions = DocumentOptions(
      lengthUnit: options.lengthUnit,
      lengthPrecision: options.lengthPrecision,
      styleItems: Map<int, double>.from(options.styleItems),
    );
  }

  /// Set the file/model UUID (also its identity when imported as a component).
  /// Accepts 32 hex digits or 8-4-4-4-12, case-insensitive; invalid values
  /// throw [SkpWriteError]. Empty generates a new UUID.
  void setGuid(String guid) {
    _modelGuid = _archiveUuid(guid);
  }

  /// The root geometry writer's current next-archive-slot value, or null
  /// before any root-level geometry has been written. Exposed only so a
  /// large-model test can confirm it actually crosses the 0x7FFF slot
  /// boundary (see [_shiftRef]'s doc comment) rather than silently staying
  /// under it - not meant for production use.
  @visibleForTesting
  int? get debugGeometryNextSlot => _geometryWriter?.nextSlot;

  SkpBuilder() {
    final doc = _blankDoc;
    _data = doc.data;
    _materialInsertPos = doc.materialInsertPos;
    _base = doc.materialBaseSlot;
    _layerCountPos = doc.layerCountPos;
    _origLayerCount = doc.origLayerCount;
    _layerInsertPos = doc.layerInsertPos;
    _defCountPos = doc.defCountPos;
    _origDefCount = doc.origDefCount;
    _rootCountPos = doc.rootCountPos;
    _origRootCount = doc.origRootCount;
    _tailPos = doc.tailPos;
    _scaffoldNextSlot = doc.scaffoldNextSlot;
    _scaffoldClassSlot = Map<String, int>.from(doc.scaffoldClassSlot);
    _pidStart = _readU64At(_data, _pidCounterPos);
    _materialWriter = _ArchiveWriter(nextSlot: _base, classSlot: {}, nextPid: _pidStart);
    _layerWriterBase = doc.layerWriterBaseSlot;
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
  int addMaterial(String name, List<int> rgba, {double? opacity}) {
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
    final slot = _materialWriter.writeMaterial(name, (r[0], r[1], r[2], r[3]), opacity: opacity);
    materialsByName[name] = slot;
    _materialCount++;
    return slot;
  }

  /// Register an image-textured material from a local PNG or JPEG file and
  /// return a handle to pass as `addFace`'s `material` argument. The
  /// format is detected from the file's own magic bytes, not its
  /// extension. Same ordering rules as [addMaterial].
  ///
  /// [appliedHeight]/[appliedWidth], if given, are the applied size in
  /// INCHES - how much model space one tile of the image covers. Both
  /// default to 1.0. A texture applied without positioning carries no
  /// per-face UV record, so this pair IS its mapping - see
  /// [_ArchiveWriter.writeTexturedMaterial]'s own comment for why it
  /// matters even for `addFace`'s `frontUv`/`backUv` pinning (a positioned
  /// mapping still divides by it).
  int addTextureMaterial(String name, String imagePath,
      {double? appliedHeight, double? appliedWidth, double? opacity, String? filename}) {
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
    final slot = _materialWriter.writeTexturedMaterial(
        name, imageBytes, filename ?? imagePath, subtype,
        appliedHeight: appliedHeight, appliedWidth: appliedWidth, opacity: opacity);
    materialsByName[name] = slot;
    _appliedSizes[slot] = (appliedWidth ?? 1.0, appliedHeight ?? 1.0);
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
      _layerWriter = _ArchiveWriter(
        nextSlot: _layerWriterStart!,
        classSlot: _materialShiftedClassSlot(),
        nextPid: _materialWriter.nextPid,
      );
    }
    final slot = _layerWriter!.writeLayer(name, hidden: hidden, rgba: rgba);
    layersByName[name] = slot;
    _layerCount++;
    return slot;
  }

  /// Reject a material/backMaterial argument that isn't a handle this
  /// builder's own [addMaterial]/[addTextureMaterial] actually returned.
  /// Without this, a stray value - most commonly a layer handle passed to
  /// the wrong parameter by mistake - gets written straight into the file
  /// as a material reference: this project's own reader tolerates the
  /// dangling reference silently, but real SketchUp rejects the whole
  /// file as corrupt on open, with no indication of which call caused it.
  void _checkMaterialHandle(int? value, String param) {
    if (value != null && value != 0 && !materialsByName.values.contains(value)) {
      throw SkpWriteError(
        "$param=$value is not a handle this builder's addMaterial()/addTextureMaterial() "
        'returned - passing an unrelated value (e.g. a layer handle by mistake) would '
        'silently write an invalid material reference that real SketchUp rejects on open',
      );
    }
  }

  /// Reject a layer argument that isn't a handle this builder's own
  /// [addLayer] actually returned - see [_checkMaterialHandle] for why
  /// this matters.
  void _checkLayerHandle(int? value, [String param = 'layer']) {
    if (value != null && value != 0 && !layersByName.values.contains(value)) {
      throw SkpWriteError(
        "$param=$value is not a handle this builder's addLayer() returned - passing an "
        'unrelated value (e.g. a material handle by mistake) would silently write an '
        'invalid layer reference that real SketchUp rejects on open',
      );
    }
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
    List<int>? guid,
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
      _definitionWriter = _ArchiveWriter(
        nextSlot: _definitionWriterStart!,
        classSlot: _postLayerClassSlot(),
        nextPid: _nextPid(),
      );
    }
    final (slot, countPatchPos) = _definitionWriter!.writeDefinitionHeader(attributeDicts);
    _definitionCount++;
    final comp = ComponentDefinitionBuilder._(
      this,
      slot,
      name,
      countPatchPos,
      groupPlacement,
      guid ?? _uuid4Bytes(),
    );
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
  /// [guid], if given, sets the definition's canonical UUID (32 hex digits
  /// or 8-4-4-4-12, case-insensitive).
  ComponentDefinitionBuilder addComponentDefinition(
    String name,
    void Function(ComponentDefinitionBuilder def) body, {
    Map<String, Object>? attributes,
    String attributeDictName = 'attributes',
    List<(String, Map<String, Object>)> attributeDicts = const [],
    String? guid,
  }) {
    final resolvedGuid = _archiveUuid(guid);
    final resolvedAttributeDicts = _attrDicts(attributes, attributeDictName, attributeDicts);
    final comp = _startDefinition(
      name,
      'addComponentDefinition',
      attributeDicts: resolvedAttributeDicts,
      guid: resolvedGuid,
    );
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
    Map<String, Object>? attributes,
    String attributeDictName = 'attributes',
    List<(String, Map<String, Object>)> attributeDicts = const [],
    bool hidden = false,
  }) {
    _checkMaterialHandle(material, 'material');
    _checkLayerHandle(layer);
    final resolved = _resolveMatrix3x3(matrix3x3, rotation);
    final resolvedAttributeDicts = _attrDicts(attributes, attributeDictName, attributeDicts);
    final comp = _startDefinition(
      name ?? 'Group',
      'addGroup',
      groupPlacement: (translation, resolved, material ?? 0, layer ?? 0, resolvedAttributeDicts, hidden),
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
    List<(String, Map<String, Object>)> attributeDicts = const [],
    bool hidden = false,
  }) {
    _checkMaterialHandle(material, 'material');
    _checkLayerHandle(layer);
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
    final resolvedAttributeDicts = _attrDicts(attributes, attributeDictName, attributeDicts);
    _ensureGeometryWriter();
    _newEntityCount += _geometryWriter!.writeInstance(
      definition.slot,
      name ?? definition.name,
      translation,
      resolved,
      material ?? 0,
      layer ?? 0,
      attributeDicts: resolvedAttributeDicts,
      hidden: hidden,
    );
    _faceCount++; // reuses the "at least one root entity" check in toBytes
  }

  /// Place a SketchUp Image entity (File > Import > Image) - a picture
  /// placed as its own object, distinct from painting a texture material
  /// onto an ordinary face (an Image gets its own Outliner classification
  /// and explode behavior a plain textured face doesn't).
  ///
  /// [width]/[height] size the image's quad in inches; the image covers it
  /// edge to edge, undistorted regardless of the source file's own pixel
  /// aspect ratio (get the ratio right yourself if that matters - this does
  /// not auto-derive it). [translation]/[matrix3x3]/[rotation]/[hidden]
  /// place it exactly like [addInstance] - the quad starts in the XY
  /// plane; rotate it to stand upright (e.g. on a wall) the same way you
  /// would any other placement. [layer], if given, is a handle from
  /// [addLayer].
  ///
  /// ```dart
  /// builder.addImage('photo.jpg', 48, 36,
  ///     translation: (0, 0, 40), rotation: ((1, 0, 0), pi / 2));
  /// ```
  ///
  /// Must be called before any [addLayer]/[addComponentDefinition]/
  /// [addGroup]/[addFace]/[addInstance] call - like [addTextureMaterial]
  /// (which this calls internally to register the image itself), it needs
  /// a material, and this writer's file format requires every material to
  /// be registered before any geometry section begins.
  ///
  /// The image's quad and UV mapping are pinned explicitly (`addFace`'s
  /// `frontUv`), not left to the default per-material tile-size projection
  /// - the read-side UV formula divides by the material's applied height
  /// even for a pinned mapping, and [addTextureMaterial]'s default height
  /// (1.0) makes that division a no-op against this method's own 0..1
  /// pins.
  ///
  /// Unlike every other entity this writer produces, CImage's exact binary
  /// schema version (see `_imageSchema`) is a best-effort guess, not
  /// calibrated against a real SketchUp-authored Image entity - none was
  /// available. This project's own reader round-trips the result
  /// correctly, but real SketchUp's acceptance of the file is unverified
  /// beyond the Python port's own real-SketchUp test (placement/
  /// orientation/texture all confirmed correct there - see CHECKLIST.md).
  void addImage(
    String imagePath,
    double width,
    double height, {
    Point3 translation = (0.0, 0.0, 0.0),
    Matrix3x3? matrix3x3,
    Rotation? rotation,
    int? layer,
    bool hidden = false,
    String? filename,
  }) {
    _checkLayerHandle(layer);
    final mat = addTextureMaterial('__openskp_image_$_materialCount', imagePath, filename: filename);
    late final ComponentDefinitionBuilder imageDef;
    imageDef = addComponentDefinition('Image$_definitionCount', (def) {
      // Standard (0,0)-at-bottom-left, V increasing upward - no vertical
      // flip. Every other UV-related fact in this file is calibrated
      // against real SketchUp output; this one specific sense is NOT (no
      // ground truth available - see this method's own warning above) and
      // could come out upside down in real SketchUp if its texture
      // sampling flips V the other way.
      def.addFace(
        [(0.0, 0.0, 0.0), (width, 0.0, 0.0), (width, height, 0.0), (0.0, height, 0.0)],
        material: mat,
        frontUv: [
          ((0.0, 0.0, 0.0), (0.0, 0.0)),
          ((width, 0.0, 0.0), (1.0, 0.0)),
          ((0.0, height, 0.0), (0.0, 1.0)),
        ],
      );
    });
    final resolved = _resolveMatrix3x3(matrix3x3, rotation);
    _ensureGeometryWriter();
    _newEntityCount += _geometryWriter!.writeImage(imageDef.slot, translation, resolved, layer ?? 0, hidden);
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
      nextPid: _nextPid(),
    );
    // Flush any groups that closed earlier, in the order they were created
    // - deferred until now so closing one group doesn't lock in root-level
    // slot numbering before a later addGroup/addComponentDefinition call
    // has had a chance to run.
    for (final (comp, placement) in _pendingGroups) {
      final (translation, matrix3x3, mat, layer, attributeDicts, hidden) = placement;
      _newEntityCount += _geometryWriter!.writeGroup(
        comp.slot, comp.name, translation, matrix3x3, mat, layer,
        attributeDicts: attributeDicts, hidden: hidden,
      );
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
    List<(String, Map<String, Object>)> attributeDicts = const [],
    bool autoTriangulate = false,
    List<List<Point3>> holes = const [],
  }) {
    _checkMaterialHandle(material, 'material');
    _checkMaterialHandle(backMaterial, 'backMaterial');
    _checkLayerHandle(layer);
    if (points.length < 3) {
      throw SkpWriteError('a face needs at least 3 points');
    }
    final resolvedAttributeDicts = _attrDicts(attributes, attributeDictName, attributeDicts);
    final scaledFront = _scalePins(frontUv, _appliedSizes, material);
    final scaledBack = _scalePins(backUv, _appliedSizes, backMaterial);
    _ensureGeometryWriter();
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
      scaledFront,
      scaledBack,
      resolvedAttributeDicts,
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
    List<(String, Map<String, Object>)> attributeDicts = const [],
  }) {
    _checkMaterialHandle(material, 'material');
    _checkMaterialHandle(backMaterial, 'backMaterial');
    _checkLayerHandle(layer);
    if (numSegments < 3 || numSegments > 255) {
      throw SkpWriteError('numSegments must be between 3 and 255, got $numSegments');
    }
    final n = _normalize3(normal);
    final resolvedAttributeDicts = _attrDicts(attributes, attributeDictName, attributeDicts);
    final scaledFront = _scalePins(frontUv, _appliedSizes, material);
    final scaledBack = _scalePins(backUv, _appliedSizes, backMaterial);
    _ensureGeometryWriter();
    final (u, w) = _circleBasis(n);
    final xaxis = (radius * u.$1, radius * u.$2, radius * u.$3);
    final CurveParams curveParams = (center, n, xaxis, 0.0, 2.0 * pi, radius, numSegments);
    final points = _circlePoints(center, n, radius, numSegments, u, w);
    _newEntityCount += _geometryWriter!.writeFace(
      points,
      _vertexSlots,
      _edgeRegistry,
      faceMaterial: material ?? 0,
      faceLayer: layer ?? 0,
      backMaterial: backMaterial ?? 0,
      hidden: hidden,
      frontUv: scaledFront,
      backUv: scaledBack,
      attributeDicts: resolvedAttributeDicts,
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

  /// Add a FREE linear dimension between two explicit points (inches,
  /// world space). [offset] is the dimension line's offset from the
  /// measured segment in inches (signed), or pass [offsetVector] to place
  /// the dimension line using a 3D vector whose component perpendicular to
  /// `p1 -> p2` determines the plane and side. See
  /// [_ArchiveWriter.writeDimension] for the record's ground truth.
  void addDimension(
    Point3 p1,
    Point3 p2, {
    double? offset,
    Point3? offsetVector,
    int? layer,
    bool hidden = false,
  }) {
    _checkLayerHandle(layer);
    if (offset != null && offsetVector != null) {
      throw SkpWriteError('pass either offset or offsetVector, not both');
    }
    if (offsetVector != null) {
      if (!p1.$1.isFinite ||
          !p1.$2.isFinite ||
          !p1.$3.isFinite ||
          !p2.$1.isFinite ||
          !p2.$2.isFinite ||
          !p2.$3.isFinite ||
          !offsetVector.$1.isFinite ||
          !offsetVector.$2.isFinite ||
          !offsetVector.$3.isFinite) {
        throw SkpWriteError('addDimension coordinates must be finite');
      }
      final dx = p2.$1 - p1.$1;
      final dy = p2.$2 - p1.$2;
      final dz = p2.$3 - p1.$3;
      final length = sqrt(dx * dx + dy * dy + dz * dz);
      if (!length.isFinite || length == 0.0) {
        throw SkpWriteError('addDimension segment length must be finite and nonzero');
      }
    } else {
      final scalarOffset = offset ?? 10.0;
      if (!p1.$1.isFinite ||
          !p1.$2.isFinite ||
          !p1.$3.isFinite ||
          !p2.$1.isFinite ||
          !p2.$2.isFinite ||
          !p2.$3.isFinite ||
          !scalarOffset.isFinite) {
        throw SkpWriteError('addDimension coordinates must be finite');
      }
    }
    if (p1.$1 == p2.$1 && p1.$2 == p2.$2 && p1.$3 == p2.$3) {
      throw SkpWriteError('addDimension endpoints coincide');
    }
    _ensureGeometryWriter();
    _geometryWriter!.writeDimension(
      p1,
      p2,
      offset: offset ?? 10.0,
      offsetVector: offsetVector,
      layer: layer,
      hidden: hidden,
    );
    _newEntityCount++;
    _faceCount++; // reuses the "at least one root entity" check in toBytes
  }

  /// Add a leader or screen text anchored at [point] (inches, world space),
  /// with the label floating at `point + leader`.
  void addText(
    String text,
    Point3 point, {
    Point3 leader = (15.0, 15.0, 15.0),
    int? layer,
    bool hidden = false,
    int leaderType = 2,
    int arrowType = 3,
    (double, double)? screenPosition,
    bool displayLeader = true,
  }) {
    _checkLayerHandle(layer);
    _prepareText(
      text,
      point,
      leader,
      leaderType,
      arrowType,
      screenPosition,
      displayLeader,
    );
    _ensureGeometryWriter();
    _geometryWriter!.writeText(
      text,
      point,
      leader: leader,
      layer: layer,
      hidden: hidden,
      leaderType: leaderType,
      arrowType: arrowType,
      screenPosition: screenPosition,
      displayLeader: displayLeader,
    );
    _newEntityCount++;
    _faceCount++; // reuses the "at least one root entity" check in toBytes
  }

  /// Add a construction/guide line (SketchUp's Construction Line tool).
  /// Pass exactly one of [point2] (a bounded segment between [point] and
  /// [point2], matching `Entities#add_cline(p1, p2)`) or [direction] (an
  /// unbounded guide line through [point], matching
  /// `Entities#add_cline(point, vector)`). See
  /// [_ArchiveWriter.writeConstructionLine] for the record's ground truth.
  void addConstructionLine(Point3 point, {Point3? point2, Point3? direction, int? layer, bool hidden = false}) {
    _checkLayerHandle(layer);
    _resolveConstructionLineParams(point, point2: point2, direction: direction);
    _ensureGeometryWriter();
    _geometryWriter!.writeConstructionLine(
      point,
      point2: point2,
      direction: direction,
      layer: layer ?? 0,
      hidden: hidden,
    );
    _newEntityCount++;
    _faceCount++; // reuses the "at least one root entity" check in toBytes
  }

  /// Add a construction/guide point (SketchUp's Construction Point tool)
  /// at [position] (inches, world space). See
  /// [_ArchiveWriter.writeConstructionPoint] for the record's ground truth.
  void addConstructionPoint(Point3 position, {int? layer, bool hidden = false}) {
    _checkLayerHandle(layer);
    _validateFinitePoint3(position, 'addConstructionPoint');
    _ensureGeometryWriter();
    _geometryWriter!.writeConstructionPoint(
      position,
      layer: layer ?? 0,
      hidden: hidden,
    );
    _newEntityCount++;
    _faceCount++; // reuses the "at least one root entity" check in toBytes
  }

  /// Add a section plane (SketchUp's Section Plane tool) through [point]
  /// with the given [normal] (need not be unit length), matching
  /// `Entities#add_section_plane([point, normal])`. See
  /// [_ArchiveWriter.writeSectionPlane] for the record's ground truth.
  void addSectionPlane(Point3 point, Point3 normal) {
    _ensureGeometryWriter();
    _geometryWriter!.writeSectionPlane(point, normal);
    _newEntityCount++;
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

    // The 4 bytes right before the material insertion point are a reserved
    // (always-present) mat_count field - zero/implicit in the zero-
    // material scaffold, not a gap that needs new bytes inserted. Real
    // SketchUp overwrites them in place rather than growing the file by 4
    // extra bytes here.
    final prefix = List<int>.from(_data.sublist(0, _materialInsertPos - 4));
    if (_modelGuid != null) {
      prefix.setRange(
        _blankDoc.modelGuidOffset,
        _blankDoc.modelGuidOffset + 16,
        _modelGuid!,
      );
    }
    if (_documentOptions != null) {
      _patchDocumentPrefix(prefix, _documentOptions!);
    }
    // The model's 64-bit next-persistent-ID counter: the next unused pid
    // after every section has allocated its entities.
    _patchU64(prefix, _pidCounterPos, _nextPid());
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
    if (_documentOptions != null) {
      _patchStyleItems(tail, _blankDoc.savedStyleItemsTailRel, _documentOptions!);
      _patchStyleItems(tail, _blankDoc.workingStyleItemsTailRel, _documentOptions!);
    }
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
