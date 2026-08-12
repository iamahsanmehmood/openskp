import 'dart:typed_data';
import 'package:openskp/openskp.dart';
import 'package:test/test.dart';

void main() {
  group('Wavefront OBJ Exporter', () {
    test('serializes Scene to OBJ text format', () {
      final scene = Scene(
        sceneHierarchy: InstanceNode(
          name: 'Root',
          definitionName: 'RootDef',
          layer: 'Layer0',
          positionMm: (0.0, 0.0, 0.0),
          properties: {},
          children: [],
        ),
        meshIndex: {},
        glbPrimitives: [
          GlbPrimitive(
            geomName: 'Cube',
            materialIndex: 0,
            positions: Float32List.fromList([0, 0, 0, 1, 0, 0, 0, 1, 0]),
            normals: Float32List.fromList([0, 0, 1, 0, 0, 1, 0, 0, 1]),
            uvs: Float32List.fromList([0, 0, 1, 0, 0, 1]),
            indices: Uint32List.fromList([0, 1, 2]),
          ),
        ],
        gltfMaterials: [],
      );

      final objText = toObj(scene);
      expect(objText, contains('# OpenSKP OBJ Export'));
      expect(objText, contains('o Cube'));
      expect(objText, contains('v 0.000000 0.000000 0.000000'));
      expect(objText, contains('f 1 2 3'));
    });
  });
}
