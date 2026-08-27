import 'dart:math';

import 'errors.dart';
import 'geometry.dart';
import 'triangulator.dart';

/// Local-space face grouping, shared by the baked (SceneBuilder) and
/// instanced (InstancedSceneBuilder) scene builders.
///
/// Extracted from scene.dart unchanged (openskp#200, mirroring
/// TypeScript's face-groups.ts): a definition's faces are grouped by
/// resolved (color, doubleSided, texture) identity in DEFINITION-LOCAL
/// space (inches, SketchUp Z-up) - exactly what the baked builder
/// assembles just before applying an instance's world matrix, and exactly
/// what the instanced builder keeps local and puts on the node instead.
/// Keeping one implementation is what makes the two paths agree on
/// triangulation, UV seams, normals and front/back handling by
/// construction rather than by parallel maintenance.
///
/// Faithful to the pre-existing baked behavior it was extracted from: an
/// unpainted face falls back to the caller-supplied fallbackColor for
/// color, but its material (and therefore texture tile size) is resolved
/// from the face's OWN materialId/backMaterialId only - an instance's
/// painted material is not consulted for texture purposes here. That is an
/// existing characteristic of this port (TypeScript's reference
/// additionally falls back to the inherited material itself for texture
/// tile size on unpainted faces), preserved rather than changed by this
/// extraction.

class LocalFaceGroup {
  final (int, int, int) color;
  final double transparency;
  final bool doubleSided;
  final int? textureIndex;
  final List<(double, double, double)> localVerts = [];
  final List<(double, double)> localUvs = [];
  final List<List<double>> normalsAccum = [];
  final List<List<int>> localFaces = [];
  final Map<(int, double, double), int> localVMap = {};

  LocalFaceGroup(this.color, this.doubleSided, this.textureIndex, this.transparency);
}

/// The resolved material's overall opacity: 1.0 fully opaque, 0.0 fully
/// invisible. Two independent SketchUp mechanisms can reduce it - the plain
/// RGBA color record's alpha byte, and the newer XML material definition's
/// own trans/useTrans attribute (already resolved into
/// RawMaterial.transparency). A real material only ever populates one of
/// the two, but multiplying both is safe either way: the untouched one
/// defaults to fully-opaque (255 or 1.0), so it never silently darkens a
/// material that only used the other mechanism.
double resolveTransparency(RawMaterial? mat) =>
    mat == null ? 1.0 : (mat.a / 255.0) * mat.transparency;

/// Key: (color, doubleSided, textureIndex, transparency).
typedef FaceGroupKey = ((int, int, int), bool, int?, double);

/// Everything [buildLocalFaceGroups] needs from its caller that isn't the
/// builder itself.
class FaceGroupContext {
  final RawMaterial? Function(int?) resolveMaterial;
  final int? Function(RawTexture?) textureIndexFor;

  /// Color an unpainted face falls back to (already resolved by the
  /// caller: the instance's inherited paint color, or the effective
  /// layer's color when nothing is inherited).
  final (int, int, int) fallbackColor;

  /// Identifies the definition in a triangulation failure.
  final int? definitionId;

  FaceGroupContext({
    required this.resolveMaterial,
    required this.textureIndexFor,
    required this.fallbackColor,
    this.definitionId,
  });
}

/// Inverse of a row-major 3x3 matrix, via the cofactor/adjugate method.
List<double> _invert3x3(List<double> m) {
  final a = m[0], b = m[1], c = m[2];
  final d = m[3], e = m[4], f = m[5];
  final g = m[6], h = m[7], i = m[8];
  final det = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g);
  if (det.abs() < 1e-12) {
    return [1, 0, 0, 0, 1, 0, 0, 0, 1];
  }
  final invDet = 1 / det;
  return [
    (e * i - f * h) * invDet, (c * h - b * i) * invDet, (b * f - c * e) * invDet,
    (f * g - d * i) * invDet, (a * i - c * g) * invDet, (c * d - a * f) * invDet,
    (d * h - e * g) * invDet, (b * g - a * h) * invDet, (a * e - b * d) * invDet,
  ];
}

/// Face-plane basis vectors (xr, yr) for UV projection, from a face normal.
((double, double, double), (double, double, double)) faceUvBasis((double, double, double) n) {
  final (nx, ny, nz) = n;
  final cx = -ny, cy = nx;
  final clen = sqrt(cx * cx + cy * cy);
  if (clen < 1e-9) {
    return ((1.0, 0.0, 0.0), (0.0, nz >= 0 ? 1.0 : -1.0, 0.0));
  }
  final xr = (cx / clen, cy / clen, 0.0);
  final yr = (ny * xr.$3 - nz * xr.$2, nz * xr.$1 - nx * xr.$3, nx * xr.$2 - ny * xr.$1);
  return (xr, yr);
}

/// UV of point p (inches, local/object space) on a face with the given
/// plane basis, per-face uvTransform (or null for the default projection),
/// and material tile size (inches).
(double, double) computeFaceUv(
  (double, double, double) p,
  (double, double, double) xr,
  (double, double, double) yr,
  List<double>? uvTransform,
  double tileW,
  double tileH,
) {
  final px = p.$1 * xr.$1 + p.$2 * xr.$2 + p.$3 * xr.$3;
  final py = p.$1 * yr.$1 + p.$2 * yr.$2 + p.$3 * yr.$3;
  if (uvTransform == null) {
    return (px / tileW, py / tileH);
  }
  final inv = _invert3x3(uvTransform);
  final u = px * inv[0] + py * inv[3] + inv[6];
  final v = px * inv[1] + py * inv[4] + inv[7];
  var q = px * inv[2] + py * inv[5] + inv[8];
  if (q.abs() < 1e-12) q = 1.0;
  return (u / q / tileW, v / q / tileH);
}

/// Walks a face loop's (edgeId, orientation) pairs to the start-vertex
/// sequence, collapsing a closed loop's duplicated first/last vertex.
List<int> reconstructLoopVertices(List<(int, int)> loop, Map<int, (int?, int?)> edges) {
  final loopVerts = <int>[];
  for (final (edgeId, orient) in loop) {
    final ends = edges[edgeId];
    if (ends != null) {
      final vStart = orient == 1 ? ends.$1 : ends.$2;
      if (vStart != null && (loopVerts.isEmpty || loopVerts.last != vStart)) {
        loopVerts.add(vStart);
      }
    }
  }
  if (loopVerts.length > 1 && loopVerts.first == loopVerts.last) {
    loopVerts.removeLast();
  }
  return loopVerts;
}

/// Group a definition's faces by resolved material identity, in local
/// space.
///
/// A face whose front/back resolve to the SAME color is emitted once with
/// doubleSided set; a face whose sides genuinely differ is emitted as two
/// single-sided triangle sets (one normal-wound front, one reverse-wound
/// back) so each side keeps its own color.
Map<FaceGroupKey, LocalFaceGroup> buildLocalFaceGroups(GeometryBuilder builder, FaceGroupContext ctx) {
  final faceGroups = <FaceGroupKey, LocalFaceGroup>{};

  void addSide(
    List<List<int>> triangles,
    (double, double, double) fn,
    (int, int, int) color,
    bool doubleSided,
    bool reverse,
    RawMaterial? mat,
    List<double>? uvTransform,
    (double, double, double) xr,
    (double, double, double) yr,
  ) {
    // faces are batched per emitted material, so the texture has to be
    // part of the key too - otherwise two differently-textured faces with
    // the same average color end up in one group with one image
    final texIndex = ctx.textureIndexFor(mat?.texture);
    final transparency = resolveTransparency(mat);
    final key = (color, doubleSided, texIndex, transparency);
    final group = faceGroups.putIfAbsent(
        key, () => LocalFaceGroup(color, doubleSided, texIndex, transparency));

    final tex = mat?.texture;
    final tileW = (tex != null && tex.xScale > 1e-9) ? tex.xScale : 1.0;
    final tileH = (tex != null && tex.yScale > 1e-9) ? tex.yScale : 1.0;
    final sideNormal = reverse ? (-fn.$1, -fn.$2, -fn.$3) : fn;

    // Vertices are deduped per (vId, uv) rather than just vId: UVs are
    // inherently per-face, so a vertex position shared by two faces that
    // disagree on texture mapping must become two distinct output
    // vertices (glTF requires position/normal/uv aligned per index).
    final faceLocalMap = <int, int>{};
    for (final tri in triangles) {
      final triIds = reverse ? [tri[0], tri[2], tri[1]] : tri;
      final faceIndices = <int>[];
      for (final vId in triIds) {
        if (!builder.vertices.containsKey(vId)) continue;
        var idx = faceLocalMap[vId];
        if (idx == null) {
          final p = builder.vertices[vId]!;
          final (u, v) = computeFaceUv(p, xr, yr, uvTransform, tileW, tileH);
          final vkey = (vId, u, v);
          idx = group.localVMap[vkey];
          if (idx == null) {
            group.localVerts.add(p);
            group.localUvs.add((u, v));
            group.normalsAccum.add([sideNormal.$1, sideNormal.$2, sideNormal.$3]);
            idx = group.localVerts.length - 1;
            group.localVMap[vkey] = idx;
          } else {
            final accum = group.normalsAccum[idx];
            accum[0] += sideNormal.$1;
            accum[1] += sideNormal.$2;
            accum[2] += sideNormal.$3;
          }
          faceLocalMap[vId] = idx;
        }
        faceIndices.add(idx);
      }
      if (faceIndices.length == 3) {
        group.localFaces.add(faceIndices);
      }
    }
  }

  for (final faceEntry in builder.faces.entries) {
    final fData = faceEntry.value;

    final frontMat = ctx.resolveMaterial(fData.materialId);
    final backMat = ctx.resolveMaterial(fData.backMaterialId);
    final frontColor = (frontMat != null) ? (frontMat.r, frontMat.g, frontMat.b) : ctx.fallbackColor;
    final backColor = (backMat != null) ? (backMat.r, backMat.g, backMat.b) : ctx.fallbackColor;

    final loops = <List<int>>[];
    for (final loop in fData.loops) {
      final loopVerts = reconstructLoopVertices(loop, builder.edges);
      if (loopVerts.isNotEmpty) loops.add(loopVerts);
    }
    if (loops.isEmpty) continue;

    List<List<int>> triangles;
    try {
      triangles = Triangulator.triangulateFace3D(builder.vertices, loops, fData.normal);
    } catch (e) {
      throw SkpParseException(
        'Failed to triangulate face: $e',
        stage: 'build_scene', definitionId: ctx.definitionId, cause: e,
      );
    }

    final fn = fData.normal;
    final (xr, yr) = faceUvBasis(fn);
    final uvTransform = fData.uvTransform;
    final uvTransformBack = fData.uvTransformBack;

    if (frontColor == backColor) {
      addSide(triangles, fn, frontColor, true, false, frontMat, uvTransform, xr, yr);
    } else {
      addSide(triangles, fn, frontColor, false, false, frontMat, uvTransform, xr, yr);
      addSide(triangles, fn, backColor, false, true, backMat, uvTransformBack, xr, yr);
    }
  }

  return faceGroups;
}
