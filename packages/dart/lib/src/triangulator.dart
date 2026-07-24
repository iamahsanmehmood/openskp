/// Triangulates a planar face given as one or more vertex-ID loops (first
/// loop is the outer boundary; any further loops are holes).
///
/// The triangle and quad fast paths below are exact - identical behavior
/// to the Python/TypeScript reference (a quad is split [0,1,2],[0,2,3],
/// matching SketchUp's own convention). For general N-gons,
/// Python/TypeScript use a full constrained-Delaunay triangulation (via
/// shapely) that correctly handles holes and concave outlines; this port
/// instead uses a plane-projected fan from the first vertex, which is
/// exact for convex polygons but only an approximation for concave ones,
/// and does not carve out holes at all (a hole-bearing face's inner
/// loop(s) are ignored). This covers the overwhelming majority of real
/// faces (triangles/quads/convex n-gons) correctly; known gap tracked for
/// a from-scratch or library-based constrained-triangulation follow-up.
class Triangulator {
  static List<List<int>> triangulateFace3D(
    Map<int, (double, double, double)> vertices3d,
    List<List<int>> loops,
    (double, double, double) normal,
  ) {
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
    if (loops.isEmpty || loops[0].length < 3) {
      return [];
    }

    final outer = loops[0];
    for (final vId in outer) {
      if (!vertices3d.containsKey(vId)) return [];
    }

    final triangles = <List<int>>[];
    for (int i = 1; i < outer.length - 1; i++) {
      triangles.add([outer[0], outer[i], outer[i + 1]]);
    }
    return triangles;
  }
}
