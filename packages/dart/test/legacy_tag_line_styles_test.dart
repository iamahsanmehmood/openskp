import 'dart:io';

import 'package:openskp/openskp.dart';
import 'package:openskp/src/legacy.dart';
import 'package:test/test.dart';

/// Tags with a line style in SketchUp 2019 and 2020 files (issue #514).
///
/// Since SketchUp 2019 a tag can carry a line style ("Dash", "Dot", ...). A
/// tag that has one points at a CCustomLineStyle object right after its own
/// fields: nothing for a tag without a line style, the style written in full
/// the first time a tag uses it (a new-class record, then the object), a
/// class-ref for a second style, and a back-ref for a style that an earlier
/// tag already wrote. The style is an object in the walk like any other and
/// takes a store-map slot.
const Map<String, List<String>> _models = {
  'sdk_saved_tag_line_styles_2020.skp': [
    'Tag 0 ',
    'Tag 1 Dash',
    'Tag 2 ',
    'Tag 3 Dash',
    'Tag 4 Dot',
    'Tag 5 Dash',
  ],
  'sdk_saved_tag_line_styles_2019.skp': [
    'Tag 0 ',
    'Tag 1 Dash',
    'Tag 2 ',
    'Tag 3 Dash',
    'Tag 4 Dot',
    'Tag 5 Dash',
  ],
  'sdk_saved_tag_line_styles_materials_2020.skp': [
    'Tag 0 Dash',
    'Tag 1 ',
    'Tag 2 Dot',
    'Tag 3 Dash',
    'Tag 4 Dot',
  ],
};

String _fixturePath(String name) =>
    '${Directory.current.path}/test/fixtures/$name';

void main() {
  group('Legacy MFC reader - tags with line styles (#514)', () {
    for (final entry in _models.entries) {
      final name = entry.key;
      final expectedTags = entry.value;

      test('opens $name whose tags have line styles', () {
        final model = SkpFile.open(_fixturePath(name)).parse();
        expect(
          [for (final layer in model.layers) layer.name],
          ['Layer0', ...expectedTags],
        );
        expect(model.root.faces.length, expectedTags.length);
      });

      test('keeps each face on its own tag in $name', () {
        final walked = Legacy.walk(File(_fixturePath(name)).readAsBytesSync());
        final tagOfSlot = <int, String>{
          for (final (slot, value) in walked.layers)
            slot: (value as LayerRec).name,
        };
        final faceTags = [
          for (final (_, cls, value) in walked.root)
            if (cls == 'CFace') tagOfSlot[(value as FaceRec).db.layer],
        ];
        expect(faceTags, expectedTags);
      });
    }
  });
}
