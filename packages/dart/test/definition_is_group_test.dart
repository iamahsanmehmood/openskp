import 'dart:io';

import 'package:openskp/openskp.dart';
import 'package:test/test.dart';

/// `Definition.isGroup`: whether SketchUp made a definition for a group or a
/// component (openskp#446).
///
/// Fixtures: nesting_<version>.skp - the group "Outer" holds an instance of the
/// component "Shelf", which holds the group "Inner", a face at each level.
/// Built and saved through the installed SketchUp library as SketchUp 6, 2013,
/// 2017, 2020 and 2021 (the last is the VFF container, the rest the classic
/// one). The library reports two group definitions and one component
/// definition for each (SUModelGetNumGroupDefinitions /
/// SUModelGetNumComponentDefinitions). No private content.
void main() {
  const names = [
    'nesting_v6.skp',
    'nesting_2013.skp',
    'nesting_2017.skp',
    'nesting_2020.skp',
    'nesting_2021.skp',
  ];

  for (final name in names) {
    group(name, () {
      final path = '${Directory.current.path}/test/fixtures/$name';
      final model = SkpFile.open(path).parse();
      Definition definitionOf(Instance i) => model.definitions[i.refIdx!]!;

      test('tells the groups from the component', () {
        final all = model.definitions.values.toList();
        expect(all.where((d) => d.isGroup).length, 2);
        expect(all.where((d) => !d.isGroup).length, 1);
      });

      test('follows the nesting', () {
        expect(model.root.instances.length, 1);
        final outer = model.root.instances.single;
        expect(outer.name, 'Outer');
        expect(definitionOf(outer).isGroup, isTrue);

        expect(definitionOf(outer).instances.length, 1);
        final shelf = definitionOf(outer).instances.single;
        expect(definitionOf(shelf).name, 'Shelf');
        expect(definitionOf(shelf).isGroup, isFalse);

        expect(definitionOf(shelf).instances.length, 1);
        final inner = definitionOf(shelf).instances.single;
        expect(inner.name, 'Inner');
        expect(definitionOf(inner).isGroup, isTrue);
      });

      test('does not take a definition placed once for a group', () {
        // "Shelf" is placed exactly once, which is what the old guess got wrong
        final placements = <int, int>{};
        for (final d in [...model.definitions.values, model.root]) {
          for (final i in d.instances) {
            placements[i.refIdx!] = (placements[i.refIdx!] ?? 0) + 1;
          }
        }
        final shelf =
            model.definitions.entries.firstWhere((e) => e.value.name == 'Shelf');
        expect(placements[shelf.key], 1);
        expect(shelf.value.isGroup, isFalse);
      });
    });
  }
}
