import 'dart:io';

import 'package:openskp/openskp.dart';
import 'package:test/test.dart';

/// A scene that doesn't save its camera (openskp#447).
///
/// SketchUp lets a scene leave "Camera Location" unticked: such a scene saves
/// no camera and still carries its name and the layers it hides. The 2021+
/// reader used to drop it.
///
/// Fixture: scene_without_camera_2021.skp - one layer ("Furniture"), one face
/// and three scenes built through SketchUp's own library: "With camera" (eye
/// 100, -200, 80 looking at 5, 5, 0), "No camera" (SUSceneSetUseCamera off,
/// hides Furniture) and "Plain" (no camera, hides nothing). SketchUp reports
/// the same three scenes. No private content.
void main() {
  final path =
      '${Directory.current.path}/test/fixtures/scene_without_camera_2021.skp';
  final pages = SkpFile.open(path).parse().pages;

  test('reads every scene', () {
    expect(pages.map((p) => p.name), ['With camera', 'No camera', 'Plain']);
  });

  test('still reads a saved camera', () {
    expect(pages[0].eye, (100.0, -200.0, 80.0));
    expect(pages[0].target, (5.0, 5.0, 0.0));
  });

  test('keeps the hidden layers of a scene that has no camera', () {
    expect(pages[1].eye, isNull);
    expect(pages[1].target, isNull);
    expect(pages[1].up, isNull);
    expect(pages[1].hiddenLayers, ['Furniture']);
    expect(pages[2].eye, isNull);
    expect(pages[2].hiddenLayers, isEmpty);
  });
}
