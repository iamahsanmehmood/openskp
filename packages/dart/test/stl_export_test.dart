import 'dart:typed_data';
import 'package:openskp/openskp.dart';
import 'package:test/test.dart';

void main() {
  group('STL Exporter (ASCII & Binary)', () {
    test('serializes Scene to ASCII STL text format', () {
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

      final stlText = toStlAscii(scene);
      expect(stlText, contains('solid OpenSKP_Model'));
      expect(stlText, contains('facet normal 0.000000 0.000000 1.000000'));
      expect(stlText, contains('vertex 0.000000 0.000000 0.000000'));
      expect(stlText, contains('endsolid OpenSKP_Model'));
    });

    test('serializes Scene to Binary STL byte array', () {
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

      final data = toStlBinary(scene);
      expect(data.length, equals(80 + 4 + 50)); // Header + uint32 count + 1 triangle
    });
  });
}
