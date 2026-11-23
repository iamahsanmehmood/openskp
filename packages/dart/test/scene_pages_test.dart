import 'dart:io';

import 'package:openskp/openskp.dart';
import 'package:test/test.dart';

void main() {
  group('Scene and InstancedScene pages (#593)', () {
    for (final (name, expectedNames) in [
      ('gondola_v20.skp', ['Cena1']),
      ('sdk_saved_2020.skp', ['With camera', 'No camera', 'Plain']),
      ('scene_without_camera_2021.skp', ['With camera', 'No camera', 'Plain']),
      ('nesting_2017.skp', <String>[]),
    ]) {
      test('matches parse().pages on $name', () {
        final skp = SkpFile.open('${Directory.current.path}/test/fixtures/$name');
        final model = skp.parse();
        final scene = skp.buildScene();
        final instanced = skp.buildInstancedScene();

        expect(model.pages.map((p) => p.name).toList(), expectedNames);
        expect(scene.pages, equals(model.pages));
        expect(instanced.pages, equals(model.pages));
      });
    }

    test('keeps native SketchUp Z-up inches and hidden layers on scene pages', () {
      final skp = SkpFile.open(
        '${Directory.current.path}/test/fixtures/scene_without_camera_2021.skp',
      );
      for (final pages in [skp.buildScene().pages, skp.buildInstancedScene().pages]) {
        expect(pages[0].eye, (100.0, -200.0, 80.0));
        expect(pages[0].target, (5.0, 5.0, 0.0));
        expect(pages[1].eye, isNull);
        expect(pages[1].hiddenLayers, ['Furniture']);
      }
    });
  });
}
