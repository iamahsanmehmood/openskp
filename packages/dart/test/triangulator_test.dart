import 'package:openskp/src/triangulator.dart';
import 'package:test/test.dart';

/// Direct tests of Triangulator/Earcut against known shapes where a naive
/// fan triangulation (this project's earlier approach) gives the wrong
/// answer: a concave outline, and a polygon with a hole. Earcut (ported
/// from the same library the TypeScript port already depends on) must
/// handle both correctly.
double _trianglesArea(List<List<int>> triangles, Map<int, (double, double, double)> vertices) {
  double total = 0;
  for (final tri in triangles) {
    final (ax, ay, _) = vertices[tri[0]]!;
    final (bx, by, _) = vertices[tri[1]]!;
    final (cx, cy, _) = vertices[tri[2]]!;
    total += 0.5 * ((bx - ax) * (cy - ay) - (cx - ax) * (by - ay)).abs();
  }
  return total;
}

void main() {
  test('concave L-shape covers exact area with no overlap', () {
    // (0,0) (4,0) (4,2) (2,2) (2,4) (0,4) - area = 4*4 - 2*2 = 12.
    final vertices = <int, (double, double, double)>{
      0: (0, 0, 0),
      1: (4, 0, 0),
      2: (4, 2, 0),
      3: (2, 2, 0),
      4: (2, 4, 0),
      5: (0, 4, 0),
    };
    final loops = [
      [0, 1, 2, 3, 4, 5],
    ];

    final triangles = Triangulator.triangulateFace3D(vertices, loops, (0, 0, 1));

    expect(triangles.length, 4); // n-2 triangles for a 6-gon
    expect(_trianglesArea(triangles, vertices), closeTo(12.0, 1e-6));

    for (final tri in triangles) {
      expect(tri.toSet().length, 3);
      for (final v in tri) {
        expect(vertices.containsKey(v), isTrue);
      }
    }
  });

  test('square with hole excludes hole area', () {
    // Outer 10x10 square (0..10), inner 2x2 hole square (4..6) - area must
    // be 100 - 4 = 96, and no triangle may cover the hole's interior.
    final vertices = <int, (double, double, double)>{
      0: (0, 0, 0),
      1: (10, 0, 0),
      2: (10, 10, 0),
      3: (0, 10, 0),
      10: (4, 4, 0),
      11: (6, 4, 0),
      12: (6, 6, 0),
      13: (4, 6, 0),
    };
    final loops = [
      [0, 1, 2, 3],
      [10, 11, 12, 13],
    ];

    final triangles = Triangulator.triangulateFace3D(vertices, loops, (0, 0, 1));

    expect(triangles, isNotEmpty);
    expect(_trianglesArea(triangles, vertices), closeTo(96.0, 1e-6));

    for (final tri in triangles) {
      final cx = (vertices[tri[0]]!.$1 + vertices[tri[1]]!.$1 + vertices[tri[2]]!.$1) / 3.0;
      final cy = (vertices[tri[0]]!.$2 + vertices[tri[1]]!.$2 + vertices[tri[2]]!.$2) / 3.0;
      final centroidInHole = cx > 4 && cx < 6 && cy > 4 && cy < 6;
      expect(centroidInHole, isFalse);
    }
  });
}
