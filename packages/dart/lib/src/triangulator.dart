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

    final allVIds = <int>[];
    final holeIndices = <int>[];
    int currentOffset = 0;
    for (int l = 0; l < loops.length; l++) {
      if (l > 0) holeIndices.add(currentOffset);
      allVIds.addAll(loops[l]);
      currentOffset += loops[l].length;
    }

    final flatCoords = List<double>.filled(allVIds.length * 2, 0.0);
    for (int i = 0; i < allVIds.length; i++) {
      final pt = vertices3d[allVIds[i]];
      if (pt == null) return []; // missing vertex
      final (px, py, pz) = pt;
      flatCoords[i * 2] = px * ux + py * uy + pz * uz;
      flatCoords[i * 2 + 1] = px * vx + py * vy + pz * vz;
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
}
