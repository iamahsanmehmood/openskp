import 'dart:io';
import 'dart:typed_data';

import 'package:openskp/openskp.dart';
import 'package:test/test.dart';

void main() {
  final cppFixtures = '${Directory.current.path}/../cpp/tests/fixtures';

  group('noScaleMask (#519)', () {
    test('native scale mask round-trip', () {
      for (final mask in [0, 1, 2, 4, 8, 16, 32, 64, 127, 0x12345678]) {
        final builder = create();
        builder.setDocumentOptions(DocumentOptions(noScaleMask: mask));
        final def = builder.addComponentDefinition(
          'MaskProbe',
          (d) {
            d.addFace([(0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (0.0, 10.0, 0.0)]);
          },
          alwaysFacesCamera: true,
          shadowsFaceSun: true,
          noScaleMask: mask,
        );
        builder.addInstance(def);
        final model = SkpFile.fromBuffer(Uint8List.fromList(builder.toBytes())).parse();
        expect(model.definitions.length, equals(1));
        final actual = model.definitions.values.first;
        expect(actual.noScaleMask, equals(mask));
        expect(model.root.noScaleMask, equals(mask));
        expect(actual.alwaysFacesCamera, isTrue);
        expect(actual.shadowsFaceSun, isTrue);
        expect(actual.isGroup, isFalse);
      }
    });

    test('reads SDK scale mask fixtures', () {
      for (final name in ['behavior_mask_2017.skp', 'behavior_mask_2021.skp']) {
        final model = SkpFile.open('$cppFixtures/$name').parse();
        expect(model.definitions.length, equals(1));
        expect(model.definitions.values.first.noScaleMask, equals(127));
      }
    });

    test('reads SDK root scale mask fixtures', () {
      for (final name in [
        'root_behavior_mask_2013.skp',
        'root_behavior_mask_2017.skp',
        'root_behavior_mask_2021.skp',
      ]) {
        final model = SkpFile.open('$cppFixtures/$name').parse();
        expect(model.root.noScaleMask, equals(name.contains('2021') ? 127 : 2));
        expect(model.definitions.length, equals(1));
        expect(model.definitions.values.first.noScaleMask, equals(0));
      }
    });

    test('app-saved files have no scale mask', () {
      for (final name in [
        'capilla_quiroz_v17.skp',
        'gondola_v20.skp',
        'zero_material_custom_layer_v18.skp',
      ]) {
        final model = SkpFile.open('$cppFixtures/$name').parse();
        expect(model.root.noScaleMask, equals(0), reason: name);
        for (final defn in model.definitions.values) {
          expect(defn.noScaleMask, equals(0), reason: name);
        }
      }
    });

    test('scale mask survives edit and code generation', () {
      final builder = create();
      builder.setDocumentOptions(const DocumentOptions(noScaleMask: 16));
      final def = builder.addComponentDefinition(
        'Masked',
        (d) {
          d.addFace([(0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (0.0, 10.0, 0.0)]);
        },
        noScaleMask: 64,
      );
      builder.addInstance(def);

      final tmpDir = Directory.systemTemp.createTempSync('openskp_scale_mask_');
      final tmpPath = '${tmpDir.path}/masked.skp';
      try {
        builder.save(tmpPath);
        final edited = openExisting(tmpPath);
        final model = SkpFile.fromBuffer(Uint8List.fromList(edited.builder.toBytes())).parse();
        expect(model.definitions.length, equals(1));
        expect(model.definitions.values.first.noScaleMask, equals(64));
        expect(model.root.noScaleMask, equals(16));

        final code = toDartCode(model);
        expect(code, contains('noScaleMask: 64'));
        expect(code, contains('noScaleMask: 16'));
      } finally {
        tmpDir.deleteSync(recursive: true);
      }
    });
  });
}
