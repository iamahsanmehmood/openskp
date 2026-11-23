import 'dart:io';
import 'dart:math' as math;

import 'package:openskp/openskp.dart';
import 'package:openskp/src/curves.dart' show chainLooseEdges, inchesToM, solveRunArc;
import 'package:openskp/src/geometry.dart';
import 'package:test/test.dart';

const double _radiusIn = 40.0;
const List<double> _identity = [1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1];

String _fixturePath(String name) => '${Directory.current.path}/test/fixtures/$name';

ArcFrame _makeFrame({double radius = _radiusIn, double sweep = math.pi / 2}) => ArcFrame(
      center: (0.0, 0.0, 0.0),
      normal: (0.0, 0.0, 1.0),
      xAxis: (radius, 0.0, 0.0),
      yAxis: (0.0, radius, 0.0),
      startAngle: 0.0,
      endAngle: sweep,
    );

(double, double, double) _arcPoint(double t, {double radius = _radiusIn}) =>
    (radius * math.cos(t) * inchesToM, 0.0, -radius * math.sin(t) * inchesToM);

void main() {
  group('Loose-edge curve chaining and analytic arcs (#285)', () {
    test('keeps two touching curves separate and ungrouped edges as pairs', () {
      final b1 = GeometryBuilder();
      b1.edges[1] = (10, 20);
      b1.edges[2] = (20, 30);
      b1.edgeCurves[1] = 100;
      b1.edgeCurves[2] = 101;
      expect(chainLooseEdges(b1).map((r) => r.vertexIds.length).toList(), equals([2, 2]));

      final b2 = GeometryBuilder();
      b2.edges[1] = (10, 20);
      b2.edges[2] = (20, 30);
      expect(
        chainLooseEdges(b2),
        equals([
          const LooseEdgeRun(edgeIds: [1], vertexIds: [10, 20], closed: false),
          const LooseEdgeRun(edgeIds: [2], vertexIds: [20, 30], closed: false),
        ]),
      );
    });

    test('excludes face-bounded edges and orders open and closed curves', () {
      final openB = GeometryBuilder();
      openB.edges[1] = (10, 20);
      openB.edges[2] = (20, 30);
      openB.edges[3] = (30, 40);
      openB.edgeCurves[2] = 50;
      openB.edgeCurves[3] = 50;
      openB.faces[9] = GeometryBuilderFace()..loops = [[(1, 1)]];
      expect(
        chainLooseEdges(openB),
        equals([
          const LooseEdgeRun(edgeIds: [2, 3], vertexIds: [20, 30, 40], closed: false),
        ]),
      );

      final closedB = GeometryBuilder();
      closedB.edges[1] = (10, 20);
      closedB.edges[2] = (20, 30);
      closedB.edges[3] = (30, 10);
      closedB.edgeCurves[1] = 77;
      closedB.edgeCurves[2] = 77;
      closedB.edgeCurves[3] = 77;
      final runs = chainLooseEdges(closedB);
      expect(runs, hasLength(1));
      expect(runs[0].closed, isTrue);
      expect(runs[0].vertexIds, hasLength(3));
    });

    test('recovers circular arcs and rejects split, elliptical, or off-circle runs', () {
      final b = GeometryBuilder();
      b.edgeCurves[1] = 17;
      b.arcCurves[17] = _makeFrame();
      final pts = [0.0, math.pi / 8, math.pi / 4, (3 * math.pi) / 8, math.pi / 2]
          .map(_arcPoint)
          .toList();
      final arc = solveRunArc(b, [1], _identity, pts);
      expect(arc, isNotNull);
      expect(arc!.segments, equals([(0, 1, 2)]));
      expect(arc.points, hasLength(3));

      final b4 = GeometryBuilder();
      for (var i = 1; i <= 4; i++) {
        b4.edgeCurves[i] = 17;
      }
      b4.arcCurves[17] = _makeFrame(sweep: 2 * math.pi);
      final pts4 = [0, 1, 2, 3].map((i) => _arcPoint((2 * math.pi * i) / 4)).toList();
      final arc4 = solveRunArc(b4, [1, 2, 3, 4], _identity, pts4, closed: true);
      expect(arc4, isNotNull);
      expect(arc4!.segments, equals([(0, 1, 2), (2, 3, 0)]));

      // Split curve rejected
      b.edgeCurves[2] = 17;
      expect(solveRunArc(b, [1], _identity, pts.sublist(0, 2)), isNull);

      // Elliptical frame rejected
      final bEllipse = GeometryBuilder();
      bEllipse.edgeCurves[1] = 17;
      bEllipse.arcCurves[17] = ArcFrame(
        center: (0.0, 0.0, 0.0),
        normal: (0.0, 0.0, 1.0),
        xAxis: (_radiusIn, 0.0, 0.0),
        yAxis: (0.0, _radiusIn / 2, 0.0),
        startAngle: 0.0,
        endAngle: math.pi / 2,
      );
      expect(solveRunArc(bEllipse, [1], _identity, pts.sublist(0, 2)), isNull);

      // Off-circle vertex rejected
      final bOff = GeometryBuilder();
      bOff.edgeCurves[1] = 17;
      bOff.arcCurves[17] = _makeFrame();
      final badPts = [pts[0], (pts[1].$1, pts[1].$2 + 0.01, pts[1].$3)];
      expect(solveRunArc(bOff, [1], _identity, badPts), isNull);
    });

    test('bakes loose-edge curveSets on capilla_quiroz_v17.skp and gondola_v20.skp', () {
      final capilla = SkpFile.open(_fixturePath('capilla_quiroz_v17.skp'));
      final sc = capilla.buildScene();
      expect(sc.curveSets, hasLength(20));
      expect(
        sc.curveSets.every((cs) => cs.pointsM.length == 2 && !cs.closed && cs.arc == null),
        isTrue,
      );

      final rootRuns = sc.curveSets.where((cs) => cs.path == 'ROOT').toList();
      expect(rootRuns, hasLength(5));
      final rootNames = rootRuns.map((cs) => cs.name).toList()..sort();
      expect(rootNames, equals(['Layer0_1', 'Layer0_2', 'Layer0_3', 'Layer0_4', 'Layer0_5']));
      expect(rootRuns.every((cs) => cs.nameIsGenerated), isTrue);

      final innerRuns = sc.curveSets.where((cs) => cs.path == 'ROOT / puerta').toList();
      expect(innerRuns, hasLength(15));
      expect(innerRuns.map((cs) => cs.name).toSet(), equals({'puerta'}));
      expect(innerRuns.any((cs) => cs.nameIsGenerated), isFalse);

      final scOff = capilla.buildScene(const ParseOptions(includeCurveSets: false));
      expect(scOff.curveSets, isEmpty);
      expect(scOff.glbPrimitives.length, equals(sc.glbPrimitives.length));

      final gondolaSc = SkpFile.open(_fixturePath('gondola_v20.skp')).buildScene();
      expect(
        gondolaSc.curveSets.map((cs) => cs.layer).toSet(),
        equals({'Gondulas Laterais', 'Layer0'}),
      );
      expect(
        gondolaSc.curveSets.where((cs) => cs.layer == 'Gondulas Laterais').length,
        equals(57232),
      );
    });

    for (final fixtureName in [
      'SU_File.skp',
      'Untitled.skp',
      'capilla_quiroz_v17.skp',
      'gondola_v20.skp',
      'single_material_v17.skp',
    ]) {
      test('looseEdgeRuns matches buildInstancedScene().curveResources on $fixtureName', () {
        final file = SkpFile.open(_fixturePath(fixtureName));
        final model = file.parse();
        final instScene = file.buildInstancedScene();

        var checkedAny = false;
        for (final res in instScene.curveResources) {
          final def = res.definitionId == null ? model.root : model.definitions[res.definitionId!];
          expect(def, isNotNull);
          final runs = def!.looseEdgeRuns();
          expect(runs, hasLength(res.curves.length));
          for (var i = 0; i < runs.length; i++) {
            expect(runs[i].closed, equals(res.curves[i].closed));
            expect(runs[i].vertexIds, hasLength(res.curves[i].pointsM.length));
            for (var k = 0; k < runs[i].vertexIds.length; k++) {
              final v = def.vertices[runs[i].vertexIds[k]]!;
              final gx = double.parse((v.x * inchesToM).toStringAsFixed(6));
              final gy = double.parse((v.z * inchesToM).toStringAsFixed(6));
              final gz = double.parse((-v.y * inchesToM).toStringAsFixed(6));
              final p = res.curves[i].pointsM[k];
              expect((gx - p.$1).abs(), lessThan(1e-5));
              expect((gy - p.$2).abs(), lessThan(1e-5));
              expect((gz - p.$3).abs(), lessThan(1e-5));
            }
          }
          checkedAny = true;
        }

        if (!checkedAny) {
          for (final def in [...model.definitions.values, model.root]) {
            expect(looseEdgeRuns(def), isEmpty);
          }
        }
      });
    }
  });
}
