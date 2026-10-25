import 'dart:io';
import 'dart:math';

import 'package:openskp/openskp.dart';
import 'package:openskp/src/transforms.dart';
import 'package:test/test.dart';

/// An instance scaled through the matrix's scale term (openskp#444).
///
/// SketchUp can keep a uniform scale in the homogeneous term of an
/// instance's transformation instead of in the 3x3: the 3x3 stays as it is,
/// the translation is in the scaled units, and the 16th value `w` is the
/// divisor. SketchUp's own library reports a 100 in cube placed with
/// `w = 100` and a translation of 2000 in as a 1 in cube at x = 20 in. The
/// 13-value matrix stores `1/w` at index 12, and every consumer used to
/// ignore it, so such an instance came out 100x its size and 100x its
/// distance from its parent.
///
/// Fixtures: instance_scale_term_2021.skp and instance_scale_term_v6.skp - one
/// 100 in cube definition placed twice, once through the 3x3 (scaling 0.01)
/// and once through w = 100 with a translation of 2000 in, built and saved
/// through the installed SketchUp library. No private content.
void main() {
  const inchMm = 25.4;
  const files = ['instance_scale_term_2021.skp', 'instance_scale_term_v6.skp'];
  String path(String name) => '${Directory.current.path}/test/fixtures/$name';

  group('matrix arithmetic with the scale term', () {
    test('scales the translated point', () {
      final m = <double>[1, 0, 0, 0, 1, 0, 0, 0, 1, 2000, 0, 0, 0.01];
      final p = Transforms.transformPoint(m, (100.0, 100.0, 100.0));
      expect(p.$1, closeTo(21, 1e-9));
      expect(p.$2, closeTo(1, 1e-9));
      expect(p.$3, closeTo(1, 1e-9));
    });

    test('a scale of one changes nothing', () {
      final m = <double>[2, 0, 0, 0, 3, 0, 0, 0, 4, 5, 6, 7, 1];
      expect(Transforms.transformPoint(m, (1.0, 1.0, 1.0)), (7.0, 9.0, 11.0));
      expect(identical(Transforms.foldScale(m), m), isTrue);
    });

    test('scaling through the 3x3 and through the term agree', () {
      final via3x3 = <double>[0.01, 0, 0, 0, 0.01, 0, 0, 0, 0.01, 0, 0, 0, 1];
      final viaTerm = <double>[1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 0.01];
      final a = Transforms.transformPoint(via3x3, (50.0, 20.0, 10.0));
      final b = Transforms.transformPoint(viaTerm, (50.0, 20.0, 10.0));
      expect(a.$1, closeTo(b.$1, 1e-9));
      expect(a.$2, closeTo(b.$2, 1e-9));
      expect(a.$3, closeTo(b.$3, 1e-9));
    });

    test('composition matches applying one transform after the other', () {
      final rng = Random(12345);
      const scales = [1.0, 1.0, 0.01, 0.5, 2.0, 0.1];
      List<double> randomMatrix() => [
            for (var i = 0; i < 9; i++) rng.nextDouble() * 4 - 2,
            for (var i = 0; i < 3; i++) rng.nextDouble() * 100 - 50,
            scales[rng.nextInt(scales.length)],
          ];
      for (var k = 0; k < 40; k++) {
        final parent = randomMatrix();
        final child = randomMatrix();
        final p = (rng.nextDouble() * 200 - 100, rng.nextDouble() * 200 - 100, rng.nextDouble() * 200 - 100);
        final composed = Transforms.multiplyMatrices(parent, child);
        final want = Transforms.transformPoint(parent, Transforms.transformPoint(child, p));
        final got = Transforms.transformPoint(composed, p);
        expect(got.$1, closeTo(want.$1, 1e-6));
        expect(got.$2, closeTo(want.$2, 1e-6));
        expect(got.$3, closeTo(want.$3, 1e-6));
      }
    });

    test('a composed matrix has its scale folded in', () {
      final out = Transforms.multiplyMatrices(
        <double>[1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 0.01],
        <double>[1, 0, 0, 0, 1, 0, 0, 0, 1, 2000, 0, 0, 1],
      );
      expect(out[12], 1.0);
      expect(out[9], closeTo(20, 1e-9));
    });
  });

  for (final name in files) {
    group(name, () {
      test('the parse still reports the raw matrix', () {
        final model = SkpFile.open(path(name)).parse();
        expect(
          model.root.instances.any((i) => (i.matrix[12] - 0.01).abs() < 1e-12 && (i.matrix[9] - 2000).abs() < 1e-9),
          isTrue,
        );
      });

      test('the scene places both cubes where SketchUp does', () {
        final scene = SkpFile.open(path(name)).buildScene();
        final xs = scene.sceneHierarchy.children.map((n) => n.positionMm.$1).toList()..sort();
        expect(xs[0], closeTo(0, 1e-6));
        expect(xs[1], closeTo(20 * inchMm, 1e-6)); // 508 mm, not 50,800
      });

      test('the instanced scene node matrices carry the scale and the translation', () {
        final scene = SkpFile.open(path(name)).buildInstancedScene();
        final placed = scene.sceneHierarchy.children;
        expect(placed.length, 2);
        for (final n in placed) {
          expect(n.matrix[0], closeTo(0.01, 1e-9));
          expect(n.matrix[5], closeTo(0.01, 1e-9));
          expect(n.matrix[10], closeTo(0.01, 1e-9));
        }
        final tx = placed.map((n) => n.matrix[12]).toList()..sort();
        expect(tx[0], closeTo(0, 1e-9));
        expect(tx[1], closeTo(20 * inchMm / 1000, 1e-9)); // 0.508 m, not 50.8
      });
    });
  }
}
