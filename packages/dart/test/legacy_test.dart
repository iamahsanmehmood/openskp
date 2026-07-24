import 'dart:io';
import 'dart:math';

import 'package:openskp/openskp.dart';
import 'package:openskp/src/legacy.dart';
import 'package:test/test.dart';

/// Real-file regression test for the classic (pre-2021) MFC .skp reader.
///
/// Fixture: fixtures/capilla_quiroz_v17.skp - a small chapel authored in
/// SketchUp 2017 (v17.0.18899, ~212 KB), the same real-file fixture Marco
/// Sumari contributed for the Python legacy reader (PR #14), also used by
/// the TypeScript and C# ports' equivalent tests. Every assertion here
/// mirrors those tests' byte-for-byte-matched values against Python ground
/// truth.
void main() {
  final fixturePath =
      '${Directory.current.path}/test/fixtures/capilla_quiroz_v17.skp';

  test('detects a legacy container', () {
    final bytes = File(fixturePath).readAsBytesSync();
    expect(Legacy.isLegacy(bytes), isTrue);
  });

  test('parses a real v17 file matching Python ground truth exactly', () {
    final model = SkpFile.open(fixturePath).parse();

    expect(model.version, '{17.0.18899}');

    // Definitions - ROOT is exposed separately via model.root, so only the
    // two named component definitions show up here.
    expect(model.definitions.length, 2);

    final puerta = model.definitions[40];
    expect(puerta, isNotNull);
    expect(puerta!.name, 'puerta');
    expect(puerta.faces.length, 24);
    expect(puerta.edges.length, 95);
    expect(puerta.vertices.length, 64);

    final grada = model.definitions[395];
    expect(grada, isNotNull);
    expect(grada!.name, 'grada');
    expect(grada.faces.length, 11);
    expect(grada.edges.length, 30);
    expect(grada.vertices.length, 20);

    final v45 = puerta.vertices[45];
    expect(v45, isNotNull);
    expect(v45!.x, closeTo(60.671292283583, 1e-9));
    expect((v45.y - 8.526512829121202e-14).abs(), lessThan(1e-18));
    expect(v45.z, closeTo(109.03580700984524, 1e-9));

    expect(model.materials.length, 16);
    final materialNames = model.materials.map((m) => m.name).toList()..sort();
    final expectedNames = [
      '*1',
      '[0037_SandyBrown]',
      '[0048_PaleGoldenrod]',
      '[0050_LemonChiffon]',
      '[0062_YellowGreen]',
      '[0064_Chartreuse]',
      '[0069_LimeGreen]',
      '[0070_SpringGreen]',
      '[0097_DeepSkyBlue]',
      '[0102_RoyalBlue]',
      '[Color G03]',
      '[Polished Concrete New]',
      '[Polished Concrete Old]',
      '[Roofing Tile Spanish]',
      '[Translucent Glass Blue]',
      '[Translucent Glass Safety]',
    ]..sort();
    expect(materialNames, expectedNames);

    expect(model.layers.length, 1);
    expect(model.layers[0].name, 'Layer0');

    double minX = double.infinity,
        minY = double.infinity,
        minZ = double.infinity;
    double maxX = -double.infinity,
        maxY = -double.infinity,
        maxZ = -double.infinity;
    for (final d in model.definitions.values) {
      for (final v in d.vertices.values) {
        minX = min(minX, v.x);
        maxX = max(maxX, v.x);
        minY = min(minY, v.y);
        maxY = max(maxY, v.y);
        minZ = min(minZ, v.z);
        maxZ = max(maxZ, v.z);
      }
    }
    expect(minX, closeTo(0.0, 1e-2));
    expect(minY, closeTo(0.0, 1e-2));
    expect(minZ, closeTo(0.0, 1e-2));
    expect(maxX, closeTo(77.402, 1e-2));
    expect(maxY, closeTo(51.969, 1e-2));
    expect(maxZ, closeTo(133.071, 1e-2));

    // Root-level placements: 3 instances (2x grada, 1x puerta). ref_idx on
    // the legacy path is the definition's slot id (legacy instances carry
    // no guid, matching Python/TS/C#'s ref_guid = "" for this path).
    expect(model.root.instances.length, 3);
    final byRefIdx = model.root.instances
        .map((i) => model.definitions[i.refIdx]?.name)
        .toList()
      ..sort();
    expect(byRefIdx, ['grada', 'grada', 'puerta']);
  });
}
