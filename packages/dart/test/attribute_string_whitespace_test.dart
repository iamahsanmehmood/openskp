import 'dart:io';

import 'package:openskp/openskp.dart';
import 'package:test/test.dart';

void main() {
  String pythonFixture(String name) =>
      '${Directory.current.path}/../python/tests/fixtures/$name';

  const expectedAttrs = <String, String>{
    'v_padded': '  leading and trailing  ',
    'v_tab_nl': '\t<render>\n  <exposure value="1.5"/>\n</render>\n',
    'v_crlf': 'line1\r\nline2\r\n',
    'v_empty': '',
    'v_spaces': '   ',
    '  padded_key  ': 'val_padded_key',
    '\ttab_nl_key\n': 'val_tab_nl_key',
    'crlf_key\r\n': 'val_crlf_key',
  };

  const specialDicts = <String, Map<String, String>>{
    '  padded_dict  ': {'probe': 'ok'},
    '\ttab_dict\n': {'probe': 'ok'},
    '   ': {'probe': 'ok'},
    '': {'probe': 'ok'},
  };

  for (final version in ['2021', '2017']) {
    group('SDK-written $version attribute string whitespace (#285)', () {
      test('preserves attribute values, keys, and dictionary names verbatim', () {
        final path = pythonFixture('attribute_string_whitespace_$version.skp');
        final model = SkpFile.open(path).parse();

        for (final ownerDicts in [
          model.attributeDictionaries,
          model.root.attributeDictionaries,
        ]) {
          expect(ownerDicts['whitespace_attrs'], expectedAttrs);
          expect(ownerDicts['dynamic_attributes'], expectedAttrs);
          for (final entry in specialDicts.entries) {
            expect(ownerDicts[entry.key], entry.value);
          }
        }

        final carrierDef = model.definitions.values
            .singleWhere((d) => d.name == 'AttrCarrierDef');
        expect(carrierDef.attributeDictionaries['whitespace_attrs'], expectedAttrs);
        expect(carrierDef.attributeDictionaries['dynamic_attributes'], expectedAttrs);
        for (final entry in specialDicts.entries) {
          expect(carrierDef.attributeDictionaries[entry.key], entry.value);
        }

        final scene = SkpFile.open(path).buildScene();
        final node = scene.sceneHierarchy.children
            .singleWhere((n) => n.definitionName == 'AttrCarrierDef');
        expect(node.properties, expectedAttrs);
        expect(node.attributeDictionaries['whitespace_attrs'], expectedAttrs);
        for (final entry in specialDicts.entries) {
          expect(node.attributeDictionaries[entry.key], entry.value);
        }
        expect(
          node.definitionAttributeDictionaries['whitespace_attrs'],
          expectedAttrs,
        );
        expect(
          node.definitionAttributeDictionaries['dynamic_attributes'],
          expectedAttrs,
        );
      });

      test('preserves material, layer, definition, and instance names matching SketchUp', () {
        final path = pythonFixture('attribute_string_whitespace_$version.skp');
        final model = SkpFile.open(path).parse();

        final expectedMaterials = [
          '  Padded Mat  ',
          '\tTabbed\nMat\n',
          version == '2021' ? 'CR\nLF Mat\n' : 'CR\r\nLF Mat\r\n',
          '   ',
        ]..sort();
        expect(
          model.materials.map((m) => m.name).toList()..sort(),
          expectedMaterials,
        );

        final expectedLayers = [
          'Layer0',
          '  Padded Tag  ',
          '\tTabbed\nTag\n',
          'CR\r\nLF Tag\r\n',
          '   ',
        ]..sort();
        expect(
          model.layers.map((l) => l.name).toList()..sort(),
          expectedLayers,
        );

        final expectedDefs = [
          'AttrCarrierDef',
          '  Padded Def  ',
          '\tTabbed\nDef\n',
          'CR\r\nLF Def\r\n',
          '   ',
        ]..sort();
        expect(
          model.definitions.values.map((d) => d.name).toList()..sort(),
          expectedDefs,
        );

        final expectedInstNames = [
          'AttrCarrierInst',
          'Padded Inst',
          'Tabbed\nInst',
          'CR\r\nLF Inst',
          '',
        ]..sort();
        expect(
          model.root.instances.map((i) => i.name).toList()..sort(),
          expectedInstNames,
        );
      });
    });
  }

  test('preserves raw VFF empty/spaces-only keys, untrimmed instance name, and empty material name', () {
    final path = pythonFixture('attribute_string_whitespace_raw_vff.skp');
    final model = SkpFile.open(path).parse();

    expect(model.attributeDictionaries['raw_keys'], {
      '': 'empty_key_val',
      '   ': 'spaces_key_val',
      '  k  ': '  v\n',
    });
    expect(model.materials.map((m) => m.name).toList(), ['']);
    expect(model.root.instances, hasLength(1));
    expect(model.root.instances.single.name, '  Raw Padded Inst\n');

    final scene = SkpFile.open(path).buildScene();
    final node = scene.sceneHierarchy.children.single;
    expect(node.name, '  Raw Padded Inst\n');
    expect(node.attributeDictionaries['raw_keys'], {
      '': 'inst_empty_k',
      '   ': 'inst_spaces_k',
    });
  });
}
