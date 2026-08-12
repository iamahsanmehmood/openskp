import 'dart:typed_data';
import 'package:openskp/openskp.dart';
import 'package:test/test.dart';

void main() {
  group('IFC4 3D Exporter', () {
    test('generates valid 22-char IFC GUIDs', () {
      final guid = generateIfcGuid();
      expect(guid.length, equals(22));
    });

    test('classifies geometry names into IFC classes', () {
      expect(classifyElement('Main Wall')[0], equals('IFCWALL'));
      expect(classifyElement('Front Door')[0], equals('IFCDOOR'));
      expect(classifyElement('Office Window')[0], equals('IFCWINDOW'));
      expect(classifyElement('Concrete Slab')[0], equals('IFCSLAB'));
      expect(classifyElement('Steel Beam')[0], equals('IFCBEAM'));
    });

    test('serializes Scene to IFC4 STEP text format', () {
      final scene = Scene(
        sceneHierarchy: InstanceNode(
          name: 'Root',
          definitionName: 'RootDef',
          layer: 'Layer0',
          positionMm: (0.0, 0.0, 0.0),
          properties: {},
          children: [],
        ),
        meshIndex: {
          'Outer Wall': MeshMetadata(
            name: 'Outer Wall',
            definitionName: 'WallDef',
            layer: 'Layer0',
            positionMm: (0.0, 0.0, 0.0),
            properties: {'Thickness': '200mm'},
            path: 'Root/Outer Wall',
          ),
        },
        glbPrimitives: [
          GlbPrimitive(
            geomName: 'Outer Wall',
            materialIndex: 0,
            positions: Float32List.fromList([0, 0, 0, 1, 0, 0, 0, 1, 0]),
            normals: Float32List.fromList([0, 0, 1, 0, 0, 1, 0, 0, 1]),
            uvs: Float32List.fromList([0, 0, 1, 0, 0, 1]),
            indices: Uint32List.fromList([0, 1, 2]),
          ),
        ],
        gltfMaterials: [
          {
            'pbrMetallicRoughness': {
              'baseColorFactor': [0.8, 0.2, 0.2, 1.0]
            }
          }
        ],
      );

      final ifcText = toIfc(scene);
      expect(ifcText, contains('ISO-10303-21;'));
      expect(ifcText, contains('HEADER;'));
      expect(ifcText, contains("FILE_SCHEMA(('IFC4'));"));
      expect(ifcText, contains('IFCPROJECT'));
      expect(ifcText, contains('IFCSITE'));
      expect(ifcText, contains('IFCBUILDING'));
      expect(ifcText, contains('IFCBUILDINGSTOREY'));
      expect(ifcText, contains('IFCWALL'));
      expect(ifcText, contains('IFCTRIANGULATEDFACESET'));
      expect(ifcText, contains('IFCCARTESIANPOINTLIST3D'));
      expect(ifcText, contains('IFCPROPERTYSET'));
      expect(ifcText, contains('ENDSEC;'));
    });
  });
}
