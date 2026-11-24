import 'dart:typed_data';

import 'package:openskp/openskp.dart';
import 'package:test/test.dart';

const _square = [
  (0.0, 0.0, 0.0),
  (10.0, 0.0, 0.0),
  (10.0, 10.0, 0.0),
  (0.0, 10.0, 0.0),
];

SkpFile _paintedModel() {
  final builder = create();
  final red = builder.addMaterial('Red', [200, 10, 10, 255]);
  final blue = builder.addMaterial('Blue', [10, 10, 200, 128]);
  final leaf = builder.addComponentDefinition('Leaf', (def) {
    def.addFace(_square);
  });
  final outer = builder.addComponentDefinition('Outer', (def) {
    def.addInstance(leaf, name: 'InheritsPaint');
    def.addInstance(leaf, name: 'OwnPaint', material: blue, translation: (20.0, 0.0, 0.0));
  });
  builder.addInstance(outer, name: 'Painted', material: red);
  return SkpFile.fromBuffer(Uint8List.fromList(builder.toBytes()));
}

void main() {
  test('paint is applied and inherited by nested components', () {
    final skp = _paintedModel();
    final baked = skp.buildScene();
    final instanced = skp.buildInstancedScene();

    final cases = [
      (baked.gltfMaterials, [for (final p in baked.glbPrimitives) p.materialIndex]),
      (
        instanced.gltfMaterials,
        [for (final r in instanced.meshResources) for (final p in r.primitives) p.materialIndex],
      ),
    ];

    for (final (gltfMats, matIndices) in cases) {
      final summaries = <(double, double, double, double, bool, String)>{};
      for (final idx in matIndices) {
        final m = gltfMats[idx];
        final pbr = m['pbrMetallicRoughness'] as Map<String, dynamic>;
        final bcf = (pbr['baseColorFactor'] as List).cast<num>();
        double r6(num v) => (v.toDouble() * 1e6).roundToDouble() / 1e6;
        summaries.add((
          r6(bcf[0]),
          r6(bcf[1]),
          r6(bcf[2]),
          r6(bcf[3]),
          m['doubleSided'] == true,
          (m['alphaMode'] as String?) ?? 'OPAQUE',
        ));
      }
      double r6(double v) => (v * 1e6).roundToDouble() / 1e6;
      expect(summaries, {
        (r6(200 / 255), r6(10 / 255), r6(10 / 255), 1.0, true, 'OPAQUE'),
        (r6(10 / 255), r6(10 / 255), r6(200 / 255), r6(128 / 255), true, 'BLEND'),
      });
    }
  });

  test('each paint is its own instanced mesh', () {
    final scene = _paintedModel().buildInstancedScene();
    final nodes = {for (final n in scene.sceneHierarchy.children[0].children) n.name: n};
    expect(nodes['InheritsPaint']!.meshResourceId, isNotNull);
    expect(nodes['OwnPaint']!.meshResourceId, isNotNull);
    expect(nodes['InheritsPaint']!.meshResourceId, isNot(nodes['OwnPaint']!.meshResourceId));
  });

  test("tag is the instance's own", () {
    final builder = create();
    final walls = builder.addLayer('Walls');
    final part = builder.addComponentDefinition('Part', (def) {
      def.addFace(_square);
    });
    builder.addInstance(part, name: 'Tagged', layer: walls);
    builder.addInstance(part, name: 'Untagged', translation: (20.0, 0.0, 0.0));

    final skp = SkpFile.fromBuffer(Uint8List.fromList(builder.toBytes()));
    final baked = skp.buildScene();
    final instanced = skp.buildInstancedScene();

    expect(
      {for (final n in baked.sceneHierarchy.children) n.name: n.layer},
      {'Tagged': 'Walls', 'Untagged': 'Layer0'},
    );
    expect(
      {for (final n in instanced.sceneHierarchy.children) n.name: n.layer},
      {'Tagged': 'Walls', 'Untagged': 'Layer0'},
    );
  });
}
