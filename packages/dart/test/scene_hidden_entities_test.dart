import 'package:openskp/openskp.dart';
import 'package:test/test.dart';

String _fixture(String name) => '../cpp/tests/fixtures/$name';

const _files = [
  'scene_hidden_entities_2017.skp',
  'scene_hidden_entities_2019.skp',
  'scene_hidden_entities_2020.skp',
  'scene_hidden_entities_2024.skp',
];

Set<int> _hidden(Page page) => page.hiddenEntityIds.toSet();

Map<String, int> _placementIds(SkpModel model) {
  final ids = <String, int>{};
  void add(Definition defn) {
    for (final inst in defn.instances) {
      if (inst.id != null) {
        ids[inst.name] = inst.id!;
      }
    }
  }

  add(model.root);
  for (final defn in model.definitions.values) {
    add(defn);
  }
  return ids;
}

void main() {
  group('Scene hidden entities (#472)', () {
    for (final name in _files) {
      test('each scene lists what it hides ($name)', () {
        final model = SkpFile.open(_fixture(name)).parse();
        expect(model.pages.length, equals(6));
        expect(model.pages[0].name, equals('All'));
        expect(model.pages[0].hiddenEntityIds, isEmpty);
        expect(model.pages[1].name, equals('No B, no crate'));
        expect(_hidden(model.pages[1]), equals({71, 73}));
        expect(model.pages[2].name, equals('No A, no crate'));
        expect(_hidden(model.pages[2]), equals({70, 73}));
        expect(model.pages[4].name, equals('Edge'));
        expect(_hidden(model.pages[4]), equals({17}));
        expect(model.pages[5].name, equals('Not saved'));
        expect(model.pages[5].hiddenEntityIds, isEmpty);
      });

      test('resolves hidden IDs through placements ($name)', () {
        final model = SkpFile.open(_fixture(name)).parse();
        expect(model.pages.length, equals(6));
        final ids = _placementIds(model);
        expect(ids.containsKey('Part A'), isTrue);
        expect(ids.containsKey('Part B'), isTrue);
        expect(ids.containsKey('Crate'), isTrue);
        expect(_hidden(model.pages[1]), equals({ids['Part B']!, ids['Crate']!}));
        expect(_hidden(model.pages[2]), equals({ids['Part A']!, ids['Crate']!}));
      });

      test('matches hidden edge persistentId on the box ($name)', () {
        final model = SkpFile.open(_fixture(name)).parse();
        expect(model.root.edges.values.any((e) => e.persistentId == 17), isTrue);
        expect(model.root.faces.values.any((f) => f.persistentId > 0), isTrue);
      });
    }

    for (final name in [
      'scene_hidden_entities_2020.skp',
      'scene_hidden_entities_2024.skp',
    ]) {
      test('keeps nested placement in 2020+ ($name)', () {
        final model = SkpFile.open(_fixture(name)).parse();
        expect(model.pages.length, equals(6));
        expect(model.pages[3].name, equals('No C'));
        expect(_hidden(model.pages[3]), equals({100}));
        expect(_placementIds(model)['Part C'], equals(100));
      });
    }

    test('keeps no nested placement in 2017 like SketchUp', () {
      final model =
          SkpFile.open(_fixture('scene_hidden_entities_2017.skp')).parse();
      expect(model.pages.length, equals(6));
      expect(model.pages[3].hiddenEntityIds, isEmpty);
    });

    test('lists the 12 hidden edges in gondola_v20.skp', () {
      final model = SkpFile.open(_fixture('gondola_v20.skp')).parse();
      expect(model.pages.length, equals(1));
      expect(model.pages[0].hiddenEntityIds.length, equals(12));
    });
  });
}
