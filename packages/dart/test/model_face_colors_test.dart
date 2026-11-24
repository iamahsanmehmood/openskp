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
}
