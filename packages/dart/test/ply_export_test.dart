import 'dart:typed_data';
import 'package:openskp/openskp.dart';
import 'package:test/test.dart';

void main() {
  group('PLY Exporter (ASCII & Binary)', () {
    test('serializes Scene to ASCII PLY text format', () {
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
            geomName: 'Box',
            materialIndex: 0,
            positions: Float32List.fromList([0, 0, 0, 1, 0, 0, 0, 1, 0]),
            normals: Float32List.fromList([0, 0, 1, 0, 0, 1, 0, 0, 1]),
            uvs: Float32List.fromList([0, 0, 1, 0, 0, 1]),
            indices: Uint32List.fromList([0, 1, 2]),
          ),
        ],
        gltfMaterials: [],
      );

      final plyText = toPlyAscii(scene);
      expect(plyText, contains('format ascii 1.0'));
      expect(plyText, contains('element vertex 3'));
      expect(plyText, contains('element face 1'));
      expect(plyText, contains('3 0 1 2'));
    });

    test('serializes Scene to Binary PLY byte array', () {
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
            geomName: 'Box',
            materialIndex: 0,
            positions: Float32List.fromList([0, 0, 0, 1, 0, 0, 0, 1, 0]),
            normals: Float32List.fromList([0, 0, 1, 0, 0, 1, 0, 0, 1]),
            uvs: Float32List.fromList([0, 0, 1, 0, 0, 1]),
            indices: Uint32List.fromList([0, 1, 2]),
          ),
        ],
        gltfMaterials: [],
      );

      final data = toPlyBinary(scene);
      final text = String.fromCharCodes(data);
      expect(text, contains('format binary_little_endian 1.0'));
      expect(text, contains('element vertex 3'));
      expect(text, contains('element face 1'));
    });
  });
}
