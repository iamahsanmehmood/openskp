import 'dart:io';

import 'package:openskp/openskp.dart';
import 'package:test/test.dart';

/// A leader text with any arrow type, and a font with a multi-byte pid, in a
/// classic file (openskp#442).
///
/// Fixture legacy_text_arrows_2017.skp: three leader texts written by
/// openskp.create with arrow types 1 (dot), 2 (closed arrow) and 4 (open
/// arrow) instead of the writer's 3, and a text font whose pid is two bytes, as
/// in the real project's SketchUp 2017 to 2020 saves (a pid above 255 takes two
/// bytes).
/// SketchUp 2025 opens it and reports the same three texts with arrow types 1,
/// 2 and 4. Before #442 this reader stopped on the first text. No private
/// content.
void main() {
  test('reads every text', () {
    final model = SkpFile.open(
            '${Directory.current.path}/test/fixtures/legacy_text_arrows_2017.skp')
        .parse();
    expect(model.root.texts.map((t) => t.text),
        ['Dot', 'Closed arrow', 'Open arrow']);
  });
}
