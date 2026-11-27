import 'dart:convert';
import 'dart:io';

import 'package:openskp/openskp.dart';
import 'package:test/test.dart';

/// Real-file regression test for toJson() - openskp's canonical JSON
/// export schema, shared with the Python (to_dict) and TypeScript
/// (toJSON) ports. Cross-checked directly against both on this exact
/// fixture.
void main() {
  final fixturePath = '${Directory.current.path}/test/fixtures/capilla_quiroz_v17.skp';

  test('matches Python/TypeScript ground truth on a real file', () {
    final model = SkpFile.open(fixturePath).parse();
    final d = toJson(model);

    expect(d['format_version'], '1.0');
    expect(d['sketchup_version'], '{17.0.18899}');
    expect(d['total_definitions'], 2);
    expect(d['total_layers'], 1);
    expect(d['total_meshes'], 0);

    final root = d['root'] as Map<String, dynamic>;
    expect(root['vertex_count'], 251);
    expect(root['edge_count'], 390);
    expect(root['face_count'], 146);
    expect((root['instances'] as List).length, 3);
    expect(
      (root['instances'][0] as Map<String, dynamic>).keys.toSet(),
      {'name', 'ref_idx', 'guid', 'matrix'},
    );

    final definitions = d['definitions'] as Map<String, dynamic>;
    final puerta = definitions.values.firstWhere((v) => v['name'] == 'puerta') as Map<String, dynamic>;
    expect(puerta['id'], 40);
    expect(puerta['vertex_count'], 64);
    expect(puerta['edge_count'], 95);
    expect(puerta['face_count'], 24);
    expect((puerta['edges'] as List).length, 95);
    expect((puerta['faces'] as List).length, 24);

    final layers = d['layers'] as List;
    expect(layers[0]['color'], {'r': 255, 'g': 84, 'b': 84});

    expect(d['mesh_index'], {});
    expect(d['scene_hierarchy'], null);
  });

  test('includes scene_hierarchy/mesh_index when a scene is passed', () {
    final model = SkpFile.open(fixturePath).parse();
    final scene = SkpFile.open(fixturePath).buildScene();
    final d = toJson(model, scene);

    expect(d['total_meshes'], scene.meshIndex.length);
    final hierarchy = d['scene_hierarchy'] as Map<String, dynamic>;
    expect(hierarchy['name'], 'ROOT');
    expect(hierarchy.containsKey('definition_name'), true);
    expect(hierarchy.containsKey('position_mm'), true);
    expect(hierarchy.containsKey('attribute_dictionaries'), true);

    final meshIndex = d['mesh_index'] as Map<String, dynamic>;
    final firstMesh = meshIndex.values.first as Map<String, dynamic>;
    expect(firstMesh.containsKey('definition_name'), true);
    expect(firstMesh.containsKey('position_mm'), true);
    expect(firstMesh.containsKey('attribute_dictionaries'), true);
  });

  test('includes attribute_dictionaries from real plugin data in scene_hierarchy and mesh_index', () {
    final untitledPath = '${Directory.current.path}/test/fixtures/Untitled.skp';
    final model = SkpFile.open(untitledPath).parse();
    final scene = SkpFile.open(untitledPath).buildScene();
    final d = toJson(model, scene);

    Map<String, dynamic>? findNode(Map<String, dynamic> node, String target) {
      if (node['name'] == target) return node;
      for (final child in node['children'] as List) {
        final found = findNode(child as Map<String, dynamic>, target);
        if (found != null) return found;
      }
      return null;
    }

    final w1 = findNode(d['scene_hierarchy'] as Map<String, dynamic>, 'W1');
    expect(w1, isNotNull);
    expect(w1!.keys.toList(), [
      'name',
      'definition_name',
      'layer',
      'position_mm',
      'properties',
      'attribute_dictionaries',
      'children',
    ]);
    expect(w1['properties'], isEmpty);
    final dicts = w1['attribute_dictionaries'] as Map<String, dynamic>;
    final steelframer = dicts['steelframer-dict'] as Map<String, String>;
    expect(steelframer['generator'], 'SteelFramer::Engine::PanelGenerator');
    expect(steelframer['profile'], '362S200-43');
    expect(steelframer['height'], '2700.0');
    expect(steelframer['auto_dimensions'], '1');

    final meshIndex = d['mesh_index'] as Map<String, dynamic>;
    final meshWithSteel = meshIndex.values.cast<Map<String, dynamic>>().firstWhere(
          (m) => (m['attribute_dictionaries'] as Map<String, dynamic>)
              .containsKey('steelframer-dict'),
        );
    expect(meshWithSteel.keys.toList(), [
      'name',
      'definition_name',
      'layer',
      'position_mm',
      'properties',
      'attribute_dictionaries',
      'path',
    ]);
  });

  test('exports all VFF attribute value types matching Python shape', () {
    final attrPath =
        '${Directory.current.path}/../python/tests/fixtures/attribute_bool_time_2025.skp';
    final model = SkpFile.open(attrPath).parse();
    final scene = SkpFile.open(attrPath).buildScene();
    final roundTripped =
        jsonDecode(jsonEncode(toJson(model, scene))) as Map<String, dynamic>;

    final hierarchy = roundTripped['scene_hierarchy'] as Map<String, dynamic>;
    final attrNode = (hierarchy['children'] as List)
        .cast<Map<String, dynamic>>()
        .firstWhere((n) => n['name'] == 'AttrComp');
    expect(attrNode['properties'], isEmpty);
    expect(attrNode['attribute_dictionaries'], {
      'stage3': {
        'array': '1,two,3.5,1',
        'bool_false': '0',
        'bool_true': '1',
        'float': '1.5',
        'int': '42',
        'length': '60.0',
        'nested': '1,2,3,4',
        'nothing': '',
        'point': '1.0,2.0,3.0',
        'string': 'hello',
        'timestamp': '1700000000',
        'vector': '4.0,5.0,6.0',
      },
      'stage3_other': {
        'key': 'value',
      },
    });

    final meshIndex = roundTripped['mesh_index'] as Map<String, dynamic>;
    final attrMeshes = meshIndex.values
        .cast<Map<String, dynamic>>()
        .where((m) => m['name'] == 'AttrComp')
        .toList();
    expect(attrMeshes, isNotEmpty);
    for (final m in attrMeshes) {
      expect(m['attribute_dictionaries'], attrNode['attribute_dictionaries']);
    }
  });

  test('produces JSON-encodable output', () {
    final model = SkpFile.open(fixturePath).parse();
    final scene = SkpFile.open(fixturePath).buildScene();
    final d = toJson(model, scene);
    expect(() => jsonEncode(d), returnsNormally);
  });
}
