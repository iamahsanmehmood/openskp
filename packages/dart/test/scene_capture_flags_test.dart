import 'package:openskp/openskp.dart';
import 'package:test/test.dart';

String _fixture(String name) => '../cpp/tests/fixtures/$name';

const _years = [2015, 2016, 2017, 2018, 2019, 2020];

const _sceneOrder = [
  'Camera',
  'RenderingOptions',
  'ShadowInfo',
  'Axes',
  'Hidden',
  'HiddenGeometry',
  'HiddenObjects',
  'HiddenLayers',
  'SectionPlanes',
  'ShadowsCamera',
  'AxesCamera',
];

void main() {
  group('Classic 2015–2020 scene capture flags and section lengths (#661)', () {
    for (final year in _years) {
      test('reads all 11 scenes with cameras and hidden layers ($year)', () {
        final model =
            SkpFile.open(_fixture('scene_capture_flags_$year.skp')).parse();
        expect(model.pages.map((p) => p.name).toList(), equals(_sceneOrder));
        final byName = {for (final p in model.pages) p.name: p};

        final cam = byName['Camera']!;
        expect(cam.eye, equals((120.0, -240.0, 90.0)));
        expect(cam.target, equals((5.0, 5.0, 0.0)));
        expect(cam.up!.$1, closeTo(-0.13407807210149128, 1e-9));
        expect(cam.up!.$2, closeTo(0.28564458839013357, 1e-9));
        expect(cam.up!.$3, closeTo(0.9489100271917137, 1e-9));
        expect(cam.parallel, isFalse);
        expect(cam.fov, closeTo(42.0, 1e-9));

        final shadowsCam = byName['ShadowsCamera']!;
        expect(shadowsCam.eye, equals((0.0, -300.0, 150.0)));
        expect(shadowsCam.target, equals((0.0, 0.0, 0.0)));
        expect(shadowsCam.up!.$1, closeTo(0.0, 1e-9));
        expect(shadowsCam.up!.$2, closeTo(0.44721359549995804, 1e-9));
        expect(shadowsCam.up!.$3, closeTo(0.8944271909999161, 1e-9));
        expect(shadowsCam.parallel, isTrue);
        expect(shadowsCam.orthoHeight, closeTo(80.0, 1e-9));

        final axesCam = byName['AxesCamera']!;
        expect(axesCam.eye, equals((-180.0, -180.0, 110.0)));
        expect(axesCam.target, equals((10.0, 10.0, 0.0)));
        expect(axesCam.up!.$1, closeTo(0.2678945632789157, 1e-9));
        expect(axesCam.up!.$2, closeTo(0.2678945632789157, 1e-9));
        expect(axesCam.up!.$3, closeTo(0.9254539458726179, 1e-9));
        expect(axesCam.parallel, isFalse);
        expect(axesCam.fov, closeTo(55.0, 1e-9));

        for (final name in _sceneOrder) {
          final p = byName[name]!;
          if (name != 'Camera' &&
              name != 'ShadowsCamera' &&
              name != 'AxesCamera') {
            expect(p.eye, isNull, reason: name);
            expect(p.target, isNull, reason: name);
          }
          if (name == 'HiddenLayers') {
            expect(p.hiddenLayers, equals(['HiddenTag']));
          } else {
            expect(p.hiddenLayers, isEmpty, reason: name);
          }
        }
      });

      test(
          'resolves hidden entities in 2017+ and drops slot refs in 2015–2016 ($year)',
          () {
        final model =
            SkpFile.open(_fixture('scene_capture_flags_$year.skp')).parse();
        final byName = {for (final p in model.pages) p.name: p};
        if (year < 2017) {
          for (final p in model.pages) {
            expect(p.hiddenEntityIds, isEmpty, reason: p.name);
          }
        } else {
          final crate =
              model.root.instances.singleWhere((i) => i.name == 'Crate');
          expect(crate.id, equals(30));
          expect(
              model.root.edges.values.any((e) => e.persistentId == 21), isTrue);
          expect(byName['Hidden']!.hiddenEntityIds.toSet(), equals({21, 30}));
          expect(byName['HiddenGeometry']!.hiddenEntityIds, equals([21]));
          expect(byName['HiddenObjects']!.hiddenEntityIds, equals([30]));
          for (final name in _sceneOrder) {
            if (name != 'Hidden' &&
                name != 'HiddenGeometry' &&
                name != 'HiddenObjects') {
              expect(byName[name]!.hiddenEntityIds, isEmpty, reason: name);
            }
          }
        }
      });
    }
  });
}
