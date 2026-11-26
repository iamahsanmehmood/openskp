import 'dart:io';

import 'package:openskp/openskp.dart';
import 'package:test/test.dart';

const _written = ((11, 122, 233), (201, 102, 3));
const _appDefault = ((255, 255, 255), (164, 178, 187));
const _sdkDefault = ((225, 225, 200), (128, 128, 200));

void main() {
  group('SkpModel faceFrontColor and faceBackColor (#533)', () {
    final cases = <String, ((int, int, int), (int, int, int))>{
      for (final v in [
        'v4',
        'v5',
        'v6',
        '2016',
        '2017',
        '2018',
        '2019',
        '2020',
        '2021',
      ])
        'face_colors_$v.skp': _written,
      'capilla_quiroz_v17.skp': _appDefault,
      'gondola_v20.skp': _appDefault,
      'zero_material_custom_layer_v18.skp': _appDefault,
      'SU_File.skp': _appDefault,
      'nesting_v6.skp': _sdkDefault,
      'nesting_2013.skp': _sdkDefault,
      'nesting_2021.skp': _sdkDefault,
      'legacy_v4_synthetic.skp': _sdkDefault,
      'legacy_v5_synthetic.skp': _sdkDefault,
      'legacy_v2019_synthetic.skp': _sdkDefault,
    };

    for (final entry in cases.entries) {
      test('matches SketchUp on ${entry.key}', () {
        final model = SkpFile.open(
          '${Directory.current.path}/test/fixtures/${entry.key}',
        ).parse();
        expect(
          (model.faceFrontColor, model.faceBackColor),
          equals(entry.value),
        );
      });
    }

    test('returns null on SketchUp 3 files', () {
      final model = SkpFile.open(
        '${Directory.current.path}/test/fixtures/legacy_v3_synthetic.skp',
      ).parse();
      expect(model.faceFrontColor, isNull);
      expect(model.faceBackColor, isNull);
    });

    test('reads current rendering-options colours rather than saved styles', () {
      final model = SkpFile.open(
        '${Directory.current.path}/test/fixtures/face_colors_2021.skp',
      ).parse();
      final stylePairs = {
        for (final s in model.styles) (s.frontColor, s.backColor),
      };
      expect(stylePairs, contains(_sdkDefault));
      expect(stylePairs, contains(_written));
      expect((model.faceFrontColor, model.faceBackColor), equals(_written));
    });
  });

  group('SkpModel colorByTag (#697)', () {
    final cbtCases = <String, bool>{
      'color_by_tag_v4.skp': true,
      'color_by_tag_v5.skp': true,
      'color_by_tag_v6.skp': true,
      'color_by_tag_2017.skp': true,
      'color_by_tag_2019.skp': true,
      'color_by_tag_2020.skp': true,
      'color_by_tag_2020_off.skp': false,
      'color_by_tag_2021.skp': true,
      'color_by_tag_2021_style.skp': true,
      'color_by_tag_2021_off.skp': false,
      'legacy_v3_synthetic.skp': false,
      for (final v in [
        'v4',
        'v5',
        'v6',
        '2016',
        '2017',
        '2018',
        '2019',
        '2020',
        '2021',
      ])
        'face_colors_$v.skp': false,
    };

    for (final entry in cbtCases.entries) {
      test('matches SketchUp DisplayColorByLayer on ${entry.key}', () {
        final model = SkpFile.open(
          '${Directory.current.path}/test/fixtures/${entry.key}',
        ).parse();
        expect(model.colorByTag, equals(entry.value));
      });
    }

    test('round-trips through classic create() styleItems 7011', () {
      const quad = [
        (0.0, 0.0, 0.0),
        (10.0, 0.0, 0.0),
        (10.0, 10.0, 0.0),
        (0.0, 10.0, 0.0),
      ];
      for (final (setting, expected) in <(double?, bool)>[
        (null, false),
        (0.0, false),
        (1.0, true),
      ]) {
        final builder = create();
        if (setting != null) {
          builder.setDocumentOptions(
            DocumentOptions(styleItems: {7011: setting}),
          );
        }
        builder.addFace(quad);
        final model = SkpFile.fromBuffer(builder.toBytes()).parse();
        expect(model.colorByTag, equals(expected));
      }
    });

    for (final (onName, offName) in [
      ('color_by_tag_2020.skp', 'color_by_tag_2020_off.skp'),
      ('color_by_tag_2021.skp', 'color_by_tag_2021_off.skp'),
    ]) {
      test('does not change scene output between $onName and $offName', () {
        final skpOn = SkpFile.open(
          '${Directory.current.path}/test/fixtures/$onName',
        );
        final skpOff = SkpFile.open(
          '${Directory.current.path}/test/fixtures/$offName',
        );
        expect(skpOn.parse().colorByTag, isTrue);
        expect(skpOff.parse().colorByTag, isFalse);
        expect(
          skpOn.buildScene().gltfMaterials,
          equals(skpOff.buildScene().gltfMaterials),
        );
        expect(
          skpOn.buildInstancedScene().gltfMaterials,
          equals(skpOff.buildInstancedScene().gltfMaterials),
        );
      });
    }
  });
}
