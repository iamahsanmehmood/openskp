import 'dart:io';

import 'package:openskp/openskp.dart';
import 'package:test/test.dart';

void main() {
  String pythonFixture(String name) =>
      '${Directory.current.path}/../python/tests/fixtures/$name';

  String localFixture(String name) =>
      '${Directory.current.path}/test/fixtures/$name';

  const vffBoxDynamic = <String, Object?>{
    '_name': 'Box',
    '_lenx_formula': 'Shelf!Width/2',
    '_lenx_label': 'LenX',
    '_lengthunits': 'CENTIMETERS',
    '_copies_formula': '2',
    '_hasbehaviors': 1.0,
    'copies': 2.0,
    'lenx': 10.0,
  };

  const legacyBoxDynamic = <String, Object?>{
    '_name': 'Box',
    '_lenx_formula': 'Shelf!Width/2',
    '_lengthunits': 'CENTIMETERS',
    '_copies_formula': '2',
    'lenx': 10.0,
  };

  const shelfModelDynamic = <String, Object?>{
    '_name': 'Shelf',
    'width': 25.0,
    '_width_access': 'LIST',
    '_width_options': '&Narrow=20&Wide=25&',
    '_width_formlabel': 'Shelf width',
  };

  group('VFF definition and model attribute dictionaries (#285)', () {
    test('definitions, model, root, and scene nodes carry expected dictionaries', () {
      final path = pythonFixture('definition_attribute_dictionaries_2026.skp');
      final model = SkpFile.open(path).parse();

      final box = model.definitions.values
          .singleWhere((d) => d.name == 'dc_fixture_box');
      expect(box.attributeDictionaries['dynamic_attributes'], vffBoxDynamic);
      expect(box.attributeDictionaries['fixture_custom'], {
        'note': 'definition-level custom dictionary',
      });

      final modelDynamic = model.attributeDictionaries['dynamic_attributes']!;
      expect(modelDynamic['_name'], 'Shelf');
      expect(modelDynamic['width'], 25.0);
      expect(modelDynamic['_width_access'], 'LIST');
      expect(modelDynamic['_width_options'], '&Narrow=20&Wide=25&');
      expect(modelDynamic['_width_formlabel'], 'Shelf width');
      expect(model.root.attributeDictionaries, model.attributeDictionaries);

      final scene = SkpFile.open(path).buildScene();
      expect(scene.sceneHierarchy.definitionAttributeDictionaries, isEmpty);
      expect(scene.sceneHierarchy.children, hasLength(1));
      final node = scene.sceneHierarchy.children.single;
      expect(node.definitionName, 'dc_fixture_box');
      expect(node.properties, {
        '_hasbehaviors': '1.0',
        '_lenx_formula': 'Shelf!Width/2',
        'lenx': '12.5',
      });
      expect(node.definitionAttributeDictionaries['dynamic_attributes'], {
        for (final e in vffBoxDynamic.entries) e.key: '${e.value}',
      });
      expect(node.definitionAttributeDictionaries['fixture_custom'], {
        'note': 'definition-level custom dictionary',
      });
    });
  });

  for (final version in ['2017', '2018']) {
    group('classic $version definition and model attribute dictionaries (#285)', () {
      test('definitions, model, root, and scene nodes carry expected dictionaries', () {
        final path = pythonFixture('legacy_attribute_dictionaries_$version.skp');
        final model = SkpFile.open(path).parse();

        final box = model.definitions.values
            .singleWhere((d) => d.name == 'dc_fixture_box');
        expect(
          box.attributeDictionaries['dynamic_attributes'],
          legacyBoxDynamic,
        );
        expect(box.attributeDictionaries['fixture_custom'], {
          'note': 'definition-level custom dictionary',
        });

        expect(
          model.attributeDictionaries.containsKey('ModelProperties'),
          isFalse,
        );
        expect(
          model.attributeDictionaries['dynamic_attributes'],
          shelfModelDynamic,
        );
        expect(model.root.attributeDictionaries, model.attributeDictionaries);

        final scene = SkpFile.open(path).buildScene();
        expect(scene.sceneHierarchy.definitionAttributeDictionaries, isEmpty);
        final node = scene.sceneHierarchy.children.single;
        expect(node.definitionName, 'dc_fixture_box');
        expect(node.properties, {'lenx': '12.5'});
        expect(node.definitionAttributeDictionaries['dynamic_attributes'], {
          for (final e in legacyBoxDynamic.entries) e.key: '${e.value}',
        });
        expect(node.definitionAttributeDictionaries['fixture_custom'], {
          'note': 'definition-level custom dictionary',
        });
      });
    });
  }

  test('files without user dictionaries exclude ModelProperties and dynamic_attributes', () {
    final vffModel = SkpFile.open(localFixture('nesting_2021.skp')).parse();
    for (final def in vffModel.definitions.values) {
      expect(def.attributeDictionaries.containsKey('dynamic_attributes'), isFalse);
    }

    final classicModel =
        SkpFile.open(localFixture('legacy_v2019_synthetic.skp')).parse();
    expect(
      classicModel.attributeDictionaries.containsKey('ModelProperties'),
      isFalse,
    );
    expect(
      classicModel.attributeDictionaries.containsKey('dynamic_attributes'),
      isFalse,
    );
    for (final def in classicModel.definitions.values) {
      expect(def.attributeDictionaries.containsKey('dynamic_attributes'), isFalse);
    }
  });
}
