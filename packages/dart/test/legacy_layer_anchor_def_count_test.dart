import 'dart:typed_data';

import 'package:openskp/openskp.dart';
import 'package:test/test.dart';

Uint8List _build(int nTags, int nDefs) {
  final builder = create();
  final mat = builder.addMaterial('M', [200, 100, 50, 255]);
  final layers = [for (int i = 0; i < nTags; i++) builder.addLayer('T$i')];
  final defs = <ComponentDefinitionBuilder>[];
  for (int i = 0; i < nDefs; i++) {
    defs.add(
      builder.addComponentDefinition('P$i', (comp) {
        comp.addFace(
          [
            (0.0, 0.0, 0.0),
            (1.0 + i, 0.0, 0.0),
            (1.0 + i, 1.0, 0.0),
            (0.0, 1.0, 0.0),
          ],
          material: mat,
        );
      }),
    );
  }
  final container = builder.addComponentDefinition('Container', (c) {
    for (int i = 0; i < defs.length; i++) {
      c.addInstance(
        defs[i],
        name: 'i$i',
        translation: (0.0, 2.0 * i, 0.0),
        layer: layers[0],
      );
    }
  });
  builder.addInstance(container, name: 'root', layer: layers[0]);
  return builder.toBytes();
}

/// Regression (#534 / #285): after the last CLayer the reader sees
/// `<anchor u16><def_count u32>` and then the first CComponentDefinition.
/// [Archive.skipClayerParentRef] consumed the anchor as a v18 "parent"
/// back-ref whenever both u16s at that position pointed at CLayer objects -
/// which also happens when the definition count (11, 12, or 13) coincides with
/// a tag's slot.
void main() {
  group('Legacy CLayer anchor vs definition count (#534)', () {
    for (final nTags in [1, 2]) {
      for (final nDefs in [10, 11, 12, 13, 14]) {
        test('round-trips with $nTags tag(s) and $nDefs part definitions', () {
          final model = SkpFile.fromBuffer(_build(nTags, nDefs)).parse();
          expect(
            model.layers.map((l) => l.name).toList(),
            ['Layer0', for (int i = 0; i < nTags; i++) 'T$i'],
          );
          expect(model.definitions, hasLength(nDefs + 1));
        });
      }
    }
  });
}
