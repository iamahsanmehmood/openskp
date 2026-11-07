import 'dart:io';

import 'package:openskp/openskp.dart';
import 'package:test/test.dart';

/// Textured and colorized materials in a classic file read as SketchUp reads
/// them.
///
/// Fixture legacy_textured_colorize_2017.skp: four materials with the same 2x1
/// texture, made and saved as a SketchUp 2017 file by SketchUp's own library:
/// plain (type 1, textured), tint and shift (type 2, colorized, colorize mode
/// tint 1 and shift 0) and tint_half (a tint at opacity 0.5). Before, every
/// classic textured material read as colorized and the mode was always tint
/// (96 of 117 textured materials of the differential test files disagreed
/// with SketchUp's own SUMaterialGetType). No private content.
void main() {
  test('reads the texture type, the colorize mode and the paint colour', () {
    final model = SkpFile.open(
            '${Directory.current.path}/test/fixtures/legacy_textured_colorize_2017.skp')
        .parse();
    final got = {
      for (final m in model.materials)
        m.name: (
          m.color.$1,
          m.color.$2,
          m.color.$3,
          m.colorized,
          m.colorizeType,
          (m.transparency * 100).round() / 100,
        ),
    };
    expect(got, {
      'plain': (120, 40, 120, false, 0, 1.0),
      'tint': (30, 160, 60, true, 1, 1.0),
      'shift': (200, 90, 20, true, 0, 1.0),
      'tint_half': (10, 20, 230, true, 1, 0.5),
    });
  });

  // blender-openskp#9: a SketchUp 2017 textured material stores two colours
  // side by side and the first one's alpha byte can be 0 (as in the box the
  // report attached). Taking the colour and its alpha from the first made every
  // textured material fully transparent in the Blender add-on. Here the first
  // colour of 'plain' (120, 40, 120) gets alpha 0, as in that file.
  test('takes the colour alpha from the material colour, not the image average',
      () {
    final data = File(
            '${Directory.current.path}/test/fixtures/legacy_textured_colorize_2017.skp')
        .readAsBytesSync();
    // image average, 00, material colour (alpha 255)
    const pair = [0x78, 0x28, 0x78, 0xFF, 0x00, 0x78, 0x28, 0x78, 0xFF];
    int find(int from) {
      for (var i = from; i + pair.length <= data.length; i++) {
        var match = true;
        for (var j = 0; j < pair.length && match; j++) {
          match = data[i + j] == pair[j];
        }
        if (match) return i;
      }
      return -1;
    }

    final at = find(0);
    expect(at, greaterThan(0));
    expect(find(at + 1), -1);
    data[at + 3] = 0;
    final plain = SkpFile.fromBuffer(data)
        .parse()
        .materials
        .firstWhere((m) => m.name == 'plain');
    expect(plain.color, (120, 40, 120, 255));
  });
}
