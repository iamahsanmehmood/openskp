import 'dart:io';

import 'package:openskp/openskp.dart';
import 'package:test/test.dart';

/// Scenes in a classic (pre-2021) file (openskp#445).
///
/// gondola_v20.skp: SketchUp 2020's own save, one scene ("Cena1", capture
/// flags 0x1FF) with a parallel-projection camera. sdk_saved_2019/2020.skp:
/// built and saved through SketchUp's C API, which writes the scene class at a
/// newer schema than the desktop application. Three scenes: "With camera" (eye
/// 100, -200, 80 looking at 5, 5, 0), "No camera" (no saved camera, hides the
/// layer "Furniture") and "Plain". SketchUp's own library reads the same scenes
/// back. sdk_saved_sections_2019/2020.skp: the same model with two scenes, the
/// first capturing everything a scene can (flags 0x1FF), so the second is only
/// found if the first was read to its last byte. No private content.
void main() {
  SkpModel load(String name) =>
      SkpFile.open('${Directory.current.path}/test/fixtures/$name').parse();

  test('gondola_v20 reads its scene with the camera', () {
    final pages = load('gondola_v20.skp').pages;
    expect(pages.length, 1);
    final p = pages.single;
    expect(p.name, 'Cena1');
    expect(p.eye!.$1, closeTo(2093.78994893, 1e-6));
    expect(p.eye!.$2, closeTo(412.425280402, 1e-6));
    expect(p.eye!.$3, closeTo(135.404197356, 1e-6));
    expect(p.target!.$1, closeTo(1802.70152746, 1e-6));
    expect(p.parallel, isTrue);
    expect(p.orthoHeight, closeTo(180.650232984, 1e-6));
    expect(p.hiddenLayers, isEmpty);
  });

  for (final name in ['sdk_saved_2019.skp', 'sdk_saved_2020.skp']) {
    test('$name reads its scenes with their cameras and hidden layers', () {
      final pages = load(name).pages;
      expect(pages.map((p) => p.name), ['With camera', 'No camera', 'Plain']);
      expect(pages[0].eye, (100.0, -200.0, 80.0));
      expect(pages[0].target, (5.0, 5.0, 0.0));
      expect(pages[0].fov, 30.0);
      expect(pages[0].parallel, isFalse);
      expect(pages[0].hiddenLayers, isEmpty);
      expect(pages[1].eye, isNull);
      expect(pages[1].target, isNull);
      expect(pages[1].hiddenLayers, ['Furniture']);
      expect(pages[2].eye, isNull);
      expect(pages[2].hiddenLayers, isEmpty);
    });
  }

  for (final name in [
    'sdk_saved_sections_2019.skp',
    'sdk_saved_sections_2020.skp'
  ]) {
    test('$name reads a scene capturing every property to its last byte', () {
      final pages = load(name).pages;
      expect(pages.map((p) => p.name), ['Everything', 'Plain']);
      expect(pages[0].eye, (100.0, -200.0, 80.0));
      expect(pages[0].hiddenLayers, ['Furniture']);
      expect(pages[1].eye, isNull);
    });
  }
}
