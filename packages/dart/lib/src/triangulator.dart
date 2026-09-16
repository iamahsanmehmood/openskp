import 'dart:math';

import 'earcut.dart';

/// Triangulates a planar face given as one or more vertex-ID loops (first
/// loop is the outer boundary; any further loops are holes). Ported from
/// the TypeScript reference implementation (triangulator.ts's
/// triangulateFace3D): projects the 3D loop vertices onto the face's own
/// plane using its normal, then runs Earcut (see earcut.dart) on the
/// flattened 2D coordinates - correctly handling concave outlines and
/// holes, same as the Python/TypeScript ports.
class Triangulator {
  static List<List<int>> triangulateFace3D(
    Map<int, (double, double, double)> vertices3d,
    List<List<int>> loops,
    (double, double, double) normal,
  ) {
    if (loops.isEmpty) return [];

    // Trivial fast path for simple triangles and quads (no holes) -
    // identical to the reference implementation.
    if (loops.length == 1 && loops[0].length == 3) {
      return [loops[0]];
    }
    if (loops.length == 1 && loops[0].length == 4) {
      final v = loops[0];
      return [
        [v[0], v[1], v[2]],
        [v[0], v[2], v[3]],
      ];
    }

    var (nx, ny, nz) = normal;
    final normVal = sqrt(nx * nx + ny * ny + nz * nz);
    if (normVal > 1e-6) {
      nx /= normVal;
      ny /= normVal;
      nz /= normVal;
    } else {
      nx = 0;
      ny = 0;
      nz = 1;
    }

    final uAxisX = nx.abs() < 0.9 ? 1.0 : 0.0;
    final uAxisY = nx.abs() < 0.9 ? 0.0 : 1.0;
    const uAxisZ = 0.0;

    var ux = ny * uAxisZ - nz * uAxisY;
    var uy = nz * uAxisX - nx * uAxisZ;
    var uz = nx * uAxisY - ny * uAxisX;
    final uLen = sqrt(ux * ux + uy * uy + uz * uz);
    if (uLen < 1e-12) {
      ux = 1.0;
      uy = 0.0;
      uz = 0.0;
    } else {
      ux /= uLen;
      uy /= uLen;
      uz /= uLen;
    }

    var vx = ny * uz - nz * uy;
    var vy = nz * ux - nx * uz;
    var vz = nx * uy - ny * ux;
    final vLen = sqrt(vx * vx + vy * vy + vz * vz);
    if (vLen > 1e-12) {
      vx /= vLen;
      vy /= vLen;
      vz /= vLen;
    }

    // 2D (u, v) projection for every loop, kept as parallel per-loop lists
    // so overlap detection below can work in plain (double, double)
    // coordinates without touching vertex IDs at all.
    final loops2d = <List<(double, double)>>[];
    for (final loop in loops) {
      final pts = <(double, double)>[];
      for (final vId in loop) {
        final pt = vertices3d[vId];
        if (pt == null) return []; // missing vertex
        final (px, py, pz) = pt;
        pts.add((px * ux + py * uy + pz * uz, px * vx + py * vy + pz * vz));
      }
      loops2d.add(pts);
    }

    // Holes are supposed to be simple and mutually disjoint - that's what
    // Earcut's ring-based API assumes. A real file can carry two hole
    // loops that genuinely overlap (observed on a real fixture: two
    // ~0.69"-radius circles only 0.33" apart, evidently meant as one
    // slotted/oval cutout recorded as two separate full circles). Feeding
    // overlapping rings to Earcut is undefined - ported from the same fix
    // in the Python/.NET/TypeScript ports (openskp#285): rather than
    // compute an explicit union boundary (which would need a general
    // polygon-clipping routine this project doesn't otherwise need), fall
    // back to triangulating the outer boundary alone and discarding any
    // triangle whose centroid falls inside ANY hole - the same
    // "triangulate then filter" strategy this project's own Python port
    // used before its earcut migration, applied narrowly only when real
    // overlap is detected. A no-op for the overwhelming common case of
    // genuinely disjoint holes.
    if (loops2d.length > 2 && _holesOverlap(loops2d)) {
      return _triangulateByFilteringHoles(loops, loops2d);
    }

    final allVIds = <int>[];
    final holeIndices = <int>[];
    int currentOffset = 0;
    for (int l = 0; l < loops.length; l++) {
      if (l > 0) holeIndices.add(currentOffset);
      allVIds.addAll(loops[l]);
      currentOffset += loops[l].length;
    }

    final flatCoords = List<double>.filled(allVIds.length * 2, 0.0);
    int idx = 0;
    for (final pts in loops2d) {
      for (final (u, v) in pts) {
        flatCoords[idx * 2] = u;
        flatCoords[idx * 2 + 1] = v;
        idx++;
      }
    }

    List<int> triIndices;
    try {
      triIndices = Earcut.triangulate(flatCoords, holeIndices, 2);
    } catch (_) {
      // Fallback: simple fan triangulation of the outer loop, matching the
      // reference implementation's own fallback for a failed earcut.
      final outerLoop = loops[0];
      final fallback = <List<int>>[];
      for (int i = 1; i < outerLoop.length - 1; i++) {
        fallback.add([outerLoop[0], outerLoop[i], outerLoop[i + 1]]);
      }
      return fallback;
    }

    final result = <List<int>>[];
    for (int i = 0; i < triIndices.length; i += 3) {
      result.add([
        allVIds[triIndices[i]],
        allVIds[triIndices[i + 1]],
        allVIds[triIndices[i + 2]],
      ]);
    }
    return result;
  }

  /// True when at least one pair of hole loops (index 1+ in [loops2d])
  /// shares real area - not just a boundary point or edge, which is a
  /// normal, common pattern (e.g. two holes sharing a cut line). Detected
  /// via a vertex-containment test: for genuinely overlapping simple
  /// polygons (in particular the convex/circular holes real drilled
  /// geometry produces), the overlap region always contains at least one
  /// polygon's own vertex inside the other - a pathological overlap with
  /// no vertex crossing either boundary is possible in principle for very
  /// concave shapes but not observed in any real file so far. Mirrors the
  /// Python/.NET/TypeScript ports' identical check exactly (openskp#285).
  static bool _holesOverlap(List<List<(double, double)>> loops2d) {
    for (int i = 1; i < loops2d.length; i++) {
      for (int j = i + 1; j < loops2d.length; j++) {
        if (_anyVertexInside(loops2d[i], loops2d[j]) || _anyVertexInside(loops2d[j], loops2d[i])) {
          return true;
        }
      }
    }
    return false;
  }

  static bool _anyVertexInside(List<(double, double)> points, List<(double, double)> polygon) {
    for (final p in points) {
      if (_pointInPolygon(p.$1, p.$2, polygon)) return true;
    }
    return false;
  }

  /// Standard ray-casting point-in-polygon test (even-odd rule) - true
  /// when (u, v) lies strictly inside the given closed 2D ring.
  static bool _pointInPolygon(double u, double v, List<(double, double)> polygon) {
    bool inside = false;
    final n = polygon.length;
    for (int i = 0, j = n - 1; i < n; j = i++) {
      final (ui, vi) = polygon[i];
      final (uj, vj) = polygon[j];
      final intersects = ((vi > v) != (vj > v)) && (u < (uj - ui) * (v - vi) / (vj - vi) + ui);
      if (intersects) inside = !inside;
    }
    return inside;
  }

  /// Fallback path when two or more hole loops genuinely overlap:
  /// triangulate the outer boundary alone (ignoring hole rings entirely),
  /// then discard any resulting triangle whose centroid falls inside ANY
  /// hole. Mirrors the Python/.NET/TypeScript ports' identical fallback
  /// exactly (openskp#285).
  static List<List<int>> _triangulateByFilteringHoles(
      List<List<int>> loops, List<List<(double, double)>> loops2d) {
    final outerLoop = loops[0];
    final outer2d = loops2d[0];
    final flatOuter = List<double>.filled(outer2d.length * 2, 0.0);
    for (int i = 0; i < outer2d.length; i++) {
      flatOuter[i * 2] = outer2d[i].$1;
      flatOuter[i * 2 + 1] = outer2d[i].$2;
    }

    List<int> triIndices;
    try {
      triIndices = Earcut.triangulate(flatOuter, const [], 2);
    } catch (_) {
      final fallback = <List<int>>[];
      for (int i = 1; i < outerLoop.length - 1; i++) {
        fallback.add([outerLoop[0], outerLoop[i], outerLoop[i + 1]]);
      }
      return fallback;
    }

    final result = <List<int>>[];
    for (int i = 0; i < triIndices.length; i += 3) {
      final ia = triIndices[i], ib = triIndices[i + 1], ic = triIndices[i + 2];
      final cu = (outer2d[ia].$1 + outer2d[ib].$1 + outer2d[ic].$1) / 3.0;
      final cv = (outer2d[ia].$2 + outer2d[ib].$2 + outer2d[ic].$2) / 3.0;

      bool insideAnyHole = false;
      for (int h = 1; h < loops2d.length; h++) {
        if (_pointInPolygon(cu, cv, loops2d[h])) {
          insideAnyHole = true;
          break;
        }
      }
      if (insideAnyHole) continue;

      result.add([outerLoop[ia], outerLoop[ib], outerLoop[ic]]);
    }
    return result;
  }
}
