import 'dart:io';

import 'package:openskp/openskp.dart';
import 'package:openskp/src/legacy.dart';
import 'package:test/test.dart';

/// Regression for SketchUp 2018 (file version 18) saves with no CMaterial
/// records and a custom tag listed ahead of Layer0.
///
/// Those files take probeLayerAnchorBases (the two-material bootstrap needs
/// matCount >= 2). The declared layerCount is 1; Layer0 follows after a
/// 16-byte colour-layer extension. Missing that extension left the probe
/// on padding: `base probe: anchor resolved to`.
///
/// Fixture is a SketchUp 2018 layout sample (two construction lines, no
/// faces). Identifiable strings were replaced with same-length ASCII so the
/// MFC record sizes are unchanged.
void main() {
  final fixturePath =
      '${Directory.current.path}/test/fixtures/zero_material_custom_layer_v18.skp';

  test('detects the fixture as a legacy container', () {
    expect(Legacy.isLegacy(File(fixturePath).readAsBytesSync()), isTrue);
  });

  test('parses a v18 file with a custom tag ahead of Layer0', () {
    final model = SkpFile.open(fixturePath).parse();
    expect(model.version, '{18.0.16975}');
    expect(model.materials, isEmpty);
    final names = model.layers.map((l) => l.name).toList();
    expect(names, contains('Layer0'));
    expect(names, contains('Guide'));
    expect(model.layers.length, greaterThanOrEqualTo(2));
    expect(model.root.faces, isEmpty);
    expect(model.root.constructionLines, hasLength(2));
  });
}
