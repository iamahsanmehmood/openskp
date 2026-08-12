import 'dart:typed_data';
import 'package:openskp/openskp.dart';
import 'package:test/test.dart';

void main() {
  group('DXF 3D Exporter', () {
    test('serializes Scene to 3D DXF text format', () {
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
            geomName: 'Walls',
            materialIndex: 0,
            positions: Float32List.fromList([0, 0, 0, 1, 0, 0, 0, 1, 0]),
            normals: Float32List.fromList([0, 0, 1, 0, 0, 1, 0, 0, 1]),
            uvs: Float32List.fromList([0, 0, 1, 0, 0, 1]),
            indices: Uint32List.fromList([0, 1, 2]),
          ),
        ],
        gltfMaterials: [],
      );

      final dxfText = toDxf(scene);
      expect(dxfText, contains('\$ACADVER'));
      expect(dxfText, contains('AC1015'));
      expect(dxfText, contains('POLYLINE'));
      expect(dxfText, contains('AcDbPolyFaceMesh'));
      expect(dxfText, contains('Walls'));
      expect(dxfText, contains('EOF'));

      final dxf3d = toDxf(scene, mode: '3dface');
      expect(dxf3d, contains('3DFACE'));
      expect(dxf3d, contains('AcDbFace'));
    });
  });
}
