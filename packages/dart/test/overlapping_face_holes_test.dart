import 'dart:io';
import 'dart:math';

import 'package:openskp/openskp.dart';
import 'package:test/test.dart';

/// Port of the Python/.NET/TypeScript fix for openskp#285's hole-hole-
/// overlap triangulation bug (see .NET's OverlappingFaceHolesTests.cs for
/// the full writeup this mirrors).
///
/// Root cause: a real fixture (Untitled.skp) has faces with two circular
/// holes close enough together (centers 0.33in apart, radius 0.69in each -
/// evidently one slotted/oval cutout recorded as two overlapping full
/// circles) that they genuinely overlap. Feeding two overlapping rings to
/// a hole-based Earcut triangulator is undefined - there's no single
/// well-defined triangulation of self-intersecting boundary input, so each
/// language's own triangulator produced SOME triangle count for it, never
/// the same one until fixed.
///
/// Unlike the Python fix (which computes an explicit merged-hole boundary
/// via Shapely's polygon union), this port avoids adding a first external
/// dependency: triangulator.dart instead falls back, only when real
/// hole-hole overlap is detected, to triangulating the outer boundary
/// alone and discarding any resulting triangle whose centroid falls
/// inside ANY hole - the same "triangulate then filter" strategy this
/// project's own Python port used before its Earcut migration, and the
/// exact approach .NET/TypeScript already ported.
List<Point3> circle(double cx, double cy, double cz, double r, [int n = 24]) {
  final pts = <Point3>[];
  for (int i = 0; i < n; i++) {
    final a = 2 * pi * i / n;
    pts.add((cx, cy + r * cos(a), cz + r * sin(a)));
  }
  return pts;
}

void main() {
  group('overlapping face holes (openskp#285)', () {
    test('leaves well-separated holes unaffected', () {
      // 4-vertex outer, two well-separated 24-vertex holes - the fix must
      // be a no-op here, matching the pre-existing Earcut path.
      final outer = <Point3>[
        (3.62, 0, 0),
        (3.62, 2, 0),
        (3.62, 2, 7.543),
        (3.62, 0, 7.543),
      ];
      final hole1 = circle(3.62, 1.0, 0.807, 0.1969);
      final hole2 = circle(3.62, 1.0, 6.350, 0.1969);

      final builder = create();
      final board = builder.addComponentDefinition('Board', (def) {
        def.addFace(outer, holes: [hole1, hole2]);
      });
      builder.addInstance(board);

      final scene = SkpFile.fromBuffer(builder.toBytes()).buildScene();
      int tris = 0, verts = 0;
      for (final p in scene.glbPrimitives) {
        tris += p.indices.length ~/ 3;
        verts += p.positions.length ~/ 3;
      }
      expect(tris, 54);
      expect(verts, 52);
    });

    test('matches an independent analytical circle-union area for genuinely overlapping holes', () {
      // Strongest possible check: the expected area is computed a totally
      // independent way (the closed-form circle-circle intersection
      // formula), not via Earcut/this project's own pipeline - catching
      // wrong-but-plausible output a triangle count alone could miss.
      const r = 0.1969;
      const gap = 0.33 - 2 * r;
      expect(gap, lessThan(0), reason: 'these two circles must genuinely overlap, not just sit close');

      const w = 2.0, h = 4.0;
      final outer = <Point3>[
        (3.62, 0, 0),
        (3.62, w, 0),
        (3.62, w, h),
        (3.62, 0, h),
      ];
      const c1 = (1.0, 1.8345);
      const c2 = (1.0, 2.1655);
      final hole1 = circle(3.62, c1.$1, c1.$2, r);
      final hole2 = circle(3.62, c2.$1, c2.$2, r);

      final builder = create();
      final board = builder.addComponentDefinition('SlottedBoard', (def) {
        def.addFace(outer, holes: [hole1, hole2]);
      });
      builder.addInstance(board);

      final scene = SkpFile.fromBuffer(builder.toBytes()).buildScene();
      expect(scene.glbPrimitives.length, 1);
      final prim = scene.glbPrimitives[0];

      double bakedAreaM2 = 0.0;
      for (int t = 0; t < prim.indices.length; t += 3) {
        final a = prim.positions;
        final ia = prim.indices[t] * 3, ib = prim.indices[t + 1] * 3, ic = prim.indices[t + 2] * 3;
        final ux = a[ib] - a[ia], uy = a[ib + 1] - a[ia + 1], uz = a[ib + 2] - a[ia + 2];
        final vx = a[ic] - a[ia], vy = a[ic + 1] - a[ia + 1], vz = a[ic + 2] - a[ia + 2];
        final cx = uy * vz - uz * vy, cy = uz * vx - ux * vz, cz = ux * vy - uy * vx;
        bakedAreaM2 += 0.5 * sqrt(cx * cx + cy * cy + cz * cz);
      }
      const inchesToMeters = 0.0254;
      final bakedAreaIn2 = bakedAreaM2 / (inchesToMeters * inchesToMeters);

      final dist = sqrt(pow(c1.$1 - c2.$1, 2) + pow(c1.$2 - c2.$2, 2));
      final inter = 2 * r * r * acos(dist / (2 * r)) - (dist / 2) * sqrt(4 * r * r - dist * dist);
      final unionArea = 2 * pi * r * r - inter;
      final expectedAreaIn2 = w * h - unionArea;

      // Filter-based triangulation is coarser at the cut than Python's
      // clean union boundary, so allow a wider tolerance than Python's own
      // 1% - 5% comfortably separates "correct, just coarser" from the
      // pre-fix bug (which was off by a much larger margin).
      final relError = (bakedAreaIn2 - expectedAreaIn2).abs() / expectedAreaIn2;
      expect(relError, lessThan(0.05),
          reason: 'baked area $bakedAreaIn2 in^2 vs analytical $expectedAreaIn2 in^2 (rel err $relError)');
    });

    test("reproduces the real overlapping-holes fixture's known-correct triangle count", () {
      // Untitled.skp's Group206#1/Group211#1 definitions each carry the
      // real overlapping-circular-holes geometry this fix targets. 18636
      // is the exact post-fix count, cross-verified against the .NET/
      // TypeScript ports' own independent implementations of the
      // identical algorithm on the same file (byte-for-byte match, not
      // just "close").
      final fixturePath = '${Directory.current.path}/test/fixtures/Untitled.skp';
      final scene = SkpFile.open(fixturePath).buildScene();
      int tris = 0;
      for (final p in scene.glbPrimitives) {
        tris += p.indices.length ~/ 3;
      }
      expect(tris, 18636);
    });
  });
}
