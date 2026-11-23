import 'dart:io';

import 'package:openskp/openskp.dart';
import 'package:test/test.dart';

String _fixturePath(String name) =>
    '${Directory.current.path}/test/fixtures/$name';

void main() {
  group('VFF tag colour records excluded from SkpModel.materials (#551)', () {
    test('arcs_2021.skp has zero materials and keeps Layer0 colour', () {
      final model = SkpFile.open(_fixturePath('arcs_2021.skp')).parse();
      expect(model.materials, isEmpty);
      expect(
        [
          for (final l in model.layers)
            (l.name, l.colorR, l.colorG, l.colorB)
        ],
        [('Layer0', 255, 84, 84)],
      );
    });

    test('texture_tga_2021.skp lists only Tex_TGA', () {
      final model = SkpFile.open(_fixturePath('texture_tga_2021.skp')).parse();
      expect([for (final m in model.materials) m.name], ['Tex_TGA']);
      expect(
        [
          for (final l in model.layers)
            (l.name, l.colorR, l.colorG, l.colorB)
        ],
        [('Layer0', 255, 84, 84)],
      );
    });

    test('preserves a genuine user material named Layer_Foo alongside tag Foo',
        () {
      final model = SkpFile.open(
              _fixturePath('sdk_saved_layer_named_material_2021.skp'))
          .parse();
      expect(
        [for (final m in model.materials) (m.name, m.color)],
        [('Layer_Foo', (200, 10, 20, 255))],
      );
      final layers = {
        for (final l in model.layers) l.name: (l.colorR, l.colorG, l.colorB),
      };
      expect(layers['Foo'], (30, 140, 250));
      expect(layers['Layer0'], (255, 84, 84));
    });
  });
}
