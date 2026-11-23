import 'dart:io';
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

    test('falls back to the layer name when the component name has no keyword',
        () {
      // SketchUp default names carry no signal, but a BIM-style layer/tag
      // often does (openskp#238).
      expect(
          classifyElement('Component#109415', 'Walls')[0], equals('IFCWALL'));
      expect(classifyElement('Group#3', 'Doors')[0], equals('IFCDOOR'));
    });

    test('prefers the component name over the layer name when both match', () {
      expect(classifyElement('Interior Door', 'Walls')[0], equals('IFCDOOR'));
    });

    test('falls back to the generic proxy when neither name matches', () {
      expect(classifyElement('Component#109415', 'Layer0')[0],
          equals('IFCBUILDINGELEMENTPROXY'));
      expect(classifyElement('Component#109415')[0],
          equals('IFCBUILDINGELEMENTPROXY'));
    });

    test('uses the layer name fallback in toIfc for unnamed components', () {
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
          'Component#109415': MeshMetadata(
            name: 'Component#109415',
            definitionName: 'CompDef',
            layer: 'Walls',
            positionMm: (0.0, 0.0, 0.0),
            properties: {},
            path: 'Root/Component#109415',
          ),
        },
        glbPrimitives: [
          GlbPrimitive(
            geomName: 'Component#109415',
            materialIndex: 0,
            positions: Float32List.fromList([0, 0, 0, 1, 0, 0, 0, 1, 0]),
            normals: Float32List.fromList([0, 0, 1, 0, 0, 1, 0, 0, 1]),
            uvs: Float32List.fromList([0, 0, 1, 0, 0, 1]),
            indices: Uint32List.fromList([0, 1, 2]),
          ),
        ],
        gltfMaterials: [{}],
      );

      final ifcText = toIfc(scene);
      expect(ifcText, contains('IFCWALL('));
      expect(ifcText, isNot(contains('IFCBUILDINGELEMENTPROXY')));
    });

    test('accepts a custom classifier override in toIfc', () {
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
            properties: {},
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
        gltfMaterials: [{}],
      );

      final ifcText = toIfc(
        scene,
        classifier: (geomName, layerName) => ['IFCCOLUMN', 'IfcColumn'],
      );
      expect(ifcText, isNot(contains('IFCWALL(')));
      expect(ifcText, contains('IFCCOLUMN('));
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

    test('declares millimetres and scales coordinates to match by default', () {
      final scene = Scene(
        sceneHierarchy: InstanceNode(name: 'Root', definitionName: 'RootDef'),
        meshIndex: {
          'Test Triangle': MeshMetadata(name: 'Test Triangle'),
        },
        glbPrimitives: [
          GlbPrimitive(
            geomName: 'Test Triangle',
            materialIndex: 0,
            positions: Float32List.fromList([0, 0, 0, 1, 0, 0, 0, 0, 1]),
            normals: Float32List.fromList([0, 0, 1, 0, 0, 1, 0, 0, 1]),
            uvs: Float32List.fromList([0, 0, 1, 0, 0, 1]),
            indices: Uint32List.fromList([0, 1, 2]),
          ),
        ],
        gltfMaterials: [{}],
      );

      final ifcText = toIfc(scene);
      expect(ifcText, contains('IFCSIUNIT(*,.LENGTHUNIT.,.MILLI.,.METRE.)'));
      expect(ifcText, contains('(1000.000000,-0.000000,0.000000)'));
    });

    test('converts glTF Y-up positions back to IFC Z-up', () {
      final scene = Scene(
        sceneHierarchy: InstanceNode(name: 'Root', definitionName: 'RootDef'),
        meshIndex: {
          'Test Triangle': MeshMetadata(name: 'Test Triangle'),
        },
        glbPrimitives: [
          GlbPrimitive(
            geomName: 'Test Triangle',
            materialIndex: 0,
            positions: Float32List.fromList([0, 0, 0, 2, 3, 5, 0, 1, 0]),
            normals: Float32List.fromList([0, 0, 1, 0, 0, 1, 0, 0, 1]),
            uvs: Float32List.fromList([0, 0, 1, 0, 0, 1]),
            indices: Uint32List.fromList([0, 1, 2]),
          ),
        ],
        gltfMaterials: [{}],
      );

      final ifcText = toIfc(scene, scale: 1.0);
      expect(ifcText, contains('(2.000000,-5.000000,3.000000)'));
      expect(ifcText, isNot(contains('(2.000000,3.000000,5.000000)')));
    });

    test('uses MeshMetadata.name instead of internal geomName key', () {
      final scene = Scene(
        sceneHierarchy: InstanceNode(name: 'Root', definitionName: 'RootDef'),
        meshIndex: {
          'mesh_3_ROOT__W1_Layer0': MeshMetadata(name: 'W1', layer: 'Layer0'),
        },
        glbPrimitives: [
          GlbPrimitive(
            geomName: 'mesh_3_ROOT__W1_Layer0',
            materialIndex: 0,
            positions: Float32List.fromList([0, 0, 0, 1, 0, 0, 0, 1, 0]),
            normals: Float32List.fromList([0, 0, 1, 0, 0, 1, 0, 0, 1]),
            uvs: Float32List.fromList([0, 0, 1, 0, 0, 1]),
            indices: Uint32List.fromList([0, 1, 2]),
          ),
        ],
        gltfMaterials: [{}],
      );

      final ifcText = toIfc(scene);
      expect(ifcText, contains("'W1'"));
      expect(ifcText, isNot(contains('mesh_3_ROOT')));
    });

    test('writes a separate Pset_<dict> per extra attribute dictionary and skips empty dicts', () {
      final scene = Scene(
        sceneHierarchy: InstanceNode(name: 'Root', definitionName: 'RootDef'),
        meshIndex: {
          'mesh_0_ROOT__Profile25_Layer0': MeshMetadata(
            name: 'Profile25',
            properties: {'width': '10.0'},
            attributeDictionaries: {
              'fbd-einfo': {'code': 'aPf', 'angle': '90'},
              'empty_dict': {},
            },
          ),
        },
        glbPrimitives: [
          GlbPrimitive(
            geomName: 'mesh_0_ROOT__Profile25_Layer0',
            materialIndex: 0,
            positions: Float32List.fromList([0, 0, 0, 1, 0, 0, 0, 1, 0]),
            normals: Float32List.fromList([0, 0, 1, 0, 0, 1, 0, 0, 1]),
            uvs: Float32List.fromList([0, 0, 1, 0, 0, 1]),
            indices: Uint32List.fromList([0, 1, 2]),
          ),
        ],
        gltfMaterials: [{}],
      );

      final ifcText = toIfc(scene);
      expect(ifcText, contains("'Pset_CustomProperties'"));
      expect(ifcText, contains("'Pset_fbd-einfo'"));
      expect(ifcText, contains("'width'"));
      expect(ifcText, contains("'code'"));
      expect(ifcText, contains("'aPf'"));
      expect(ifcText, contains("'angle'"));
      expect(ifcText, contains("'90'"));
      expect(ifcText, isNot(contains('Pset_empty_dict')));
    });

    test('emits IFCPRESENTATIONLAYERWITHSTYLE reflecting scene.layerHidden', () {
      final scene = Scene(
        sceneHierarchy: InstanceNode(name: 'Root', definitionName: 'RootDef'),
        meshIndex: {
          'Outer Wall': MeshMetadata(name: 'Outer Wall', layer: 'Hidden Layer'),
          'Front Door': MeshMetadata(name: 'Front Door', layer: 'Visible Layer'),
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
          GlbPrimitive(
            geomName: 'Front Door',
            materialIndex: 0,
            positions: Float32List.fromList([2, 0, 0, 3, 0, 0, 2, 1, 0]),
            normals: Float32List.fromList([0, 0, 1, 0, 0, 1, 0, 0, 1]),
            uvs: Float32List.fromList([0, 0, 1, 0, 0, 1]),
            indices: Uint32List.fromList([0, 1, 2]),
          ),
        ],
        gltfMaterials: [{}],
        layerHidden: {'Hidden Layer': true, 'Visible Layer': false},
      );

      final ifcText = toIfc(scene);
      expect(ifcText, contains('IFCPRESENTATIONLAYERWITHSTYLE'));
      expect(ifcText, isNot(contains('IFCPRESENTATIONLAYERASSIGNMENT(')));
      final lines = ifcText.split(RegExp(r'\r?\n'));
      final hiddenLine = lines.firstWhere((l) => l.contains("'Hidden Layer'"));
      final visibleLine = lines.firstWhere((l) => l.contains("'Visible Layer'"));
      expect(hiddenLine, contains(',.F.,.F.,.F.,())'));
      expect(visibleLine, contains(',.T.,.F.,.F.,())'));
    });

    test('supports opt-in full-path keyword classification without corrupting displayed Name', () {
      const path = 'ROOT / Wall Frame / Stud 12';
      expect(classifyElement('Stud 12', 'Layer0', path)[0], equals('IFCBUILDINGELEMENTPROXY'));
      expect(classifyElement('Stud 12', 'Layer0', path, true)[0], equals('IFCWALL'));
      expect(
        classifyElement('Front Door', 'Layer0', 'ROOT / Wall Frame / Front Door', true)[0],
        equals('IFCDOOR'),
      );

      final scene = Scene(
        sceneHierarchy: InstanceNode(name: 'Root', definitionName: 'RootDef'),
        meshIndex: {
          'mesh_0_ROOT__Wall_Frame__Stud_12_Layer0': MeshMetadata(
            name: 'Stud 12',
            layer: 'Layer0',
            path: path,
          ),
        },
        glbPrimitives: [
          GlbPrimitive(
            geomName: 'mesh_0_ROOT__Wall_Frame__Stud_12_Layer0',
            materialIndex: 0,
            positions: Float32List.fromList([0, 0, 0, 1, 0, 0, 0, 1, 0]),
            normals: Float32List.fromList([0, 0, 1, 0, 0, 1, 0, 0, 1]),
            uvs: Float32List.fromList([0, 0, 1, 0, 0, 1]),
            indices: Uint32List.fromList([0, 1, 2]),
          ),
        ],
        gltfMaterials: [{}],
      );

      final defaultText = toIfc(scene);
      expect(defaultText, contains('IFCBUILDINGELEMENTPROXY'));
      expect(defaultText, isNot(contains('IFCWALL(')));

      final optedInText = toIfc(scene, classifyUsingFullPath: true);
      expect(optedInText, contains('IFCWALL('));
      expect(optedInText, contains("'Stud 12'"));
    });

    test('preserves definition display names, hidden layers, and plugin dicts end-to-end through buildScene', () {
      final builder = create();
      final hiddenWalls = builder.addLayer('HiddenWalls', color: [200, 100, 100], hidden: true);
      final stud = builder.addComponentDefinition('Stud 12', (def) {
        def.addFace([(0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (10.0, 10.0, 0.0), (0.0, 10.0, 0.0)]);
      });
      final wallFrame = builder.addComponentDefinition('Wall Frame', (def) {
        def.addInstance(stud);
      });
      builder.addInstance(wallFrame, layer: hiddenWalls);

      final tmpPath =
          '${Directory.systemTemp.path}/openskp_dart_ifc_e2e_${DateTime.now().microsecondsSinceEpoch}.skp';
      File(tmpPath).writeAsBytesSync(builder.toBytes());
      try {
        final scene = SkpFile.open(tmpPath).buildScene();
        expect(scene.layerHidden['HiddenWalls'], isTrue);
        expect(scene.layerHidden['Layer0'], isFalse);
        final ifcText = toIfc(scene, classifyUsingFullPath: true);
        expect(ifcText, contains('IFCWALL('));
        expect(ifcText, contains("'Stud 12'"));
        expect(ifcText, isNot(contains('mesh_0_ROOT')));
        final visibleLine = ifcText
            .split(RegExp(r'\r?\n'))
            .firstWhere((l) => l.contains("IFCPRESENTATIONLAYERWITHSTYLE('Layer0'"));
        expect(visibleLine, contains(',.T.,.F.,.F.,())'));

        scene.layerHidden['Layer0'] = true;
        final hiddenIfc = toIfc(scene, classifyUsingFullPath: true);
        final hiddenLine = hiddenIfc
            .split(RegExp(r'\r?\n'))
            .firstWhere((l) => l.contains("IFCPRESENTATIONLAYERWITHSTYLE('Layer0'"));
        expect(hiddenLine, contains(',.F.,.F.,.F.,())'));
      } finally {
        File(tmpPath).deleteSync();
      }

      final untitledScene =
          SkpFile.open('${Directory.current.path}/test/fixtures/Untitled.skp').buildScene();
      final untitledIfc = toIfc(untitledScene);
      expect(untitledIfc, contains("'Pset_steelframer-dict'"));
    });
  });
}
