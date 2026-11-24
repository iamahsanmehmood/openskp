import 'dart:typed_data';

import 'package:openskp/openskp.dart';
import 'package:test/test.dart';

const List<Point3> _wallRect = [
  (0.0, 0.0, 0.0),
  (20.0, 0.0, 0.0),
  (20.0, 10.0, 0.0),
  (0.0, 10.0, 0.0),
];

SkpFile _buildFixture() {
  final b = create();
  final wall = b.addComponentDefinition('Wall', (def) {
    def.addFace(_wallRect);
  });

  b.addInstance(
    wall,
    name: 'W1',
    attributes: {'name': 'CustomOverride', 'sku': 'B42'},
  );
  b.addInstance(
    wall,
    name: 'W2',
    translation: (30.0, 0.0, 0.0),
    attributes: {'label': 'TS-9', 'count': 3},
    attributeDictName: 'TechSteel',
  );
  b.addInstance(
    wall,
    name: 'W3',
    translation: (60.0, 0.0, 0.0),
    attributes: {'name': 'IgnoredDynamic', 'lenx': 20},
    attributeDictName: 'dynamic_attributes',
  );
  return SkpFile.fromBuffer(Uint8List.fromList(b.toBytes()));
}

void main() {
  test('classic instance attribute dictionaries and names match in both scenes', () {
    final skp = _buildFixture();
    final baked = skp.buildScene();
    final instanced = skp.buildInstancedScene();

    final bakedNodes = baked.sceneHierarchy.children;
    expect(bakedNodes.map((n) => n.name), ['CustomOverride', 'TS-9', 'W3']);
    expect(bakedNodes.map((n) => n.nameIsGenerated), [false, false, false]);
    expect(bakedNodes[0].attributeDictionaries, {
      'attributes': {'name': 'CustomOverride', 'sku': 'B42'},
    });
    expect(bakedNodes[0].properties, isEmpty);
    expect(bakedNodes[1].attributeDictionaries, {
      'TechSteel': {'label': 'TS-9', 'count': '3'},
    });
    expect(bakedNodes[1].properties, isEmpty);
    expect(bakedNodes[2].attributeDictionaries, isEmpty);
    expect(bakedNodes[2].properties, {'name': 'IgnoredDynamic', 'lenx': '20'});

    final instNodes = instanced.sceneHierarchy.children;
    expect(instNodes.map((n) => n.name), ['CustomOverride', 'TS-9', 'W3']);
    expect(instNodes.map((n) => n.nameIsGenerated), [false, false, false]);
    expect(instNodes[0].attributeDictionaries, {
      'attributes': {'name': 'CustomOverride', 'sku': 'B42'},
    });
    expect(instNodes[0].properties, isEmpty);
    expect(instNodes[1].attributeDictionaries, {
      'TechSteel': {'label': 'TS-9', 'count': '3'},
    });
    expect(instNodes[1].properties, isEmpty);
    expect(instNodes[2].attributeDictionaries, isEmpty);
    expect(instNodes[2].properties, {'name': 'IgnoredDynamic', 'lenx': '20'});

    final byName = {
      for (final p in baked.surfacePrimitives)
        baked.meshIndex[p.geomName]!.name: baked.meshIndex[p.geomName]!,
    };
    expect(byName.keys.toSet(), {'CustomOverride', 'TS-9', 'W3'});
    expect(byName['CustomOverride']!.attributeDictionaries, {
      'attributes': {'name': 'CustomOverride', 'sku': 'B42'},
    });
    expect(byName['TS-9']!.attributeDictionaries, {
      'TechSteel': {'label': 'TS-9', 'count': '3'},
    });
    expect(byName['W3']!.attributeDictionaries, isEmpty);
    expect(byName['W3']!.properties, {'name': 'IgnoredDynamic', 'lenx': '20'});
  });
}
