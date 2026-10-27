import 'dart:io';
import 'dart:math' as math;

import 'package:openskp/openskp.dart';
import 'package:test/test.dart';

/// `Definition.arcs`: the analytic arc of every curve that is one
/// (openskp#462).
///
/// Fixtures: arcs_2017.skp, arcs_2020.skp (classic container) and
/// arcs_2021.skp (VFF) - three arcs built through the installed SketchUp
/// library: a loose arc (centre 0,0,0, radius 10, 0 to 90 degrees, 12
/// segments); a disc (a full circle bounding a face, centre 50,0,0, radius 5,
/// 24 sides); and a half disc (an arc closed by its chord, bounding a face,
/// centre 100,0,0, radius 8, 0 to 180 degrees, 12 segments). SketchUp reads
/// the same centres, radii and angles back from all three files. Untitled.skp
/// is a real file whose 368 arcs (24-edge circles) agree with SketchUp's own
/// values to 5e-6 inches. No private content.
void main() {
  SkpModel load(String name) =>
      SkpFile.open('${Directory.current.path}/test/fixtures/$name').parse();

  // centre x, radius, sweep in radians, edges, full circle
  final expected = [
    (0.0, 10.0, math.pi / 2, 12, false),
    (50.0, 5.0, 2 * math.pi, 24, true),
    (100.0, 8.0, math.pi, 12, false),
  ];

  for (final name in ['arcs_2017.skp', 'arcs_2020.skp', 'arcs_2021.skp']) {
    group(name, () {
      final model = load(name);
      final arcs = model.root.arcs.entries.toList()
        ..sort((a, b) => a.value.center.$1.compareTo(b.value.center.$1));

      test('reads every arc with its centre, radius and angles', () {
        expect(arcs.length, 3);
        for (var i = 0; i < 3; i++) {
          final a = arcs[i].value;
          final (cx, radius, sweep, _, full) = expected[i];
          expect(a.center.$1, closeTo(cx, 1e-9));
          expect(a.center.$2, closeTo(0, 1e-9));
          expect(a.center.$3, closeTo(0, 1e-9));
          expect(a.normal.$3, closeTo(1, 1e-9));
          expect(a.radius, closeTo(radius, 1e-9));
          expect(a.startAngle, closeTo(0, 1e-9));
          expect((a.endAngle - a.startAngle) % (2 * math.pi),
              closeTo(sweep % (2 * math.pi), 1e-9));
          expect(a.isFullCircle, full);
          expect(a.xAxis.$1, closeTo(radius, 1e-9));
          expect(a.yAxis.$2, closeTo(radius, 1e-9));
        }
      });

      test('joins each arc to its edges by curve id', () {
        final perCurve = <int?, int>{};
        for (final e in model.root.edges.values) {
          perCurve[e.curveId] = (perCurve[e.curveId] ?? 0) + 1;
        }
        expect(arcs.map((e) => perCurve[e.key]).toList(),
            expected.map((e) => e.$4).toList());
      });
    });
  }

  test('a model without arcs has none', () {
    for (final name in [
      'instance_scale_term_v6.skp',
      'instance_scale_term_2021.skp'
    ]) {
      final model = load(name);
      expect(model.root.arcs, isEmpty, reason: name);
      for (final d in model.definitions.values) {
        expect(d.arcs, isEmpty, reason: name);
      }
    }
  });

  test('Untitled has 368 arcs, each the 24-edge circle SketchUp reports', () {
    final model = load('Untitled.skp');
    var total = 0;
    for (final d in [...model.definitions.values, model.root]) {
      final perCurve = <int?, int>{};
      for (final e in d.edges.values) {
        perCurve[e.curveId] = (perCurve[e.curveId] ?? 0) + 1;
      }
      for (final entry in d.arcs.entries) {
        total++;
        expect(perCurve[entry.key], 24);
        expect(entry.value.isFullCircle, isTrue);
      }
    }
    expect(total, 368);
  });
}
