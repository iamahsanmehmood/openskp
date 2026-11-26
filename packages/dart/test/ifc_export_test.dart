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

    test('declares millimetres and converts glTF Y-up positions back to IFC Z-up', () {
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

      final ifcText = toIfc(scene);
      expect(ifcText, contains('IFCSIUNIT(*,.LENGTHUNIT.,.MILLI.,.METRE.)'));
      expect(ifcText, contains('(2000.000000,-5000.000000,3000.000000)'));
      expect(ifcText, isNot(contains('(2000.000000,3000.000000,5000.000000)')));
    });

    test('uses MeshMetadata.name, passes raw text to custom classifier, and writes layer visibility & Psets', () {
      final scene = Scene(
        sceneHierarchy: InstanceNode(name: 'Root', definitionName: 'RootDef'),
        meshIndex: {
          'mesh_3_ROOT__W1_Layer0': MeshMetadata(
            name: "Wände 'A'",
            layer: "Außenwände '1'",
            properties: {'width': '10.0'},
            attributeDictionaries: {
              'fbd-einfo': {'code': 'aPf'},
              'empty_dict': {},
            },
          ),
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
        layerHidden: {"Außenwände '1'": true},
      );

      final seen = <(String, String)>[];
      final ifcText = toIfc(
        scene,
        classifier: (name, layer) {
          seen.add((name, layer));
          return ['IFCWALL', 'IfcWall'];
        },
      );
      expect(seen, [("Wände 'A'", "Außenwände '1'")]);
      expect(ifcText, contains("''A''"));
      expect(ifcText, isNot(contains('mesh_3_ROOT')));
      expect(ifcText, contains("'Pset_CustomProperties'"));
      expect(ifcText, contains("'Pset_fbd-einfo'"));
      expect(ifcText, isNot(contains('Pset_empty_dict')));
      expect(ifcText, contains(',.F.,.F.,.F.,());'));
    });

    test('preserves definition display names, hidden layers, single surface per unpainted face, and plugin dicts end-to-end', () {
      const path = 'ROOT / Wall Frame / Stud 12';
      expect(classifyElement('Stud 12', 'Layer0', path)[0], equals('IFCBUILDINGELEMENTPROXY'));
      expect(classifyElement('Stud 12', 'Layer0', path, true)[0], equals('IFCWALL'));

      final builder = create();
      final hiddenWalls = builder.addLayer('HiddenWalls', color: [200, 100, 100], hidden: true);
      final stud = builder.addComponentDefinition('Stud 12', (def) {
        def.addFace([(0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (10.0, 10.0, 0.0), (0.0, 10.0, 0.0)]);
      });
      final wallFrame = builder.addComponentDefinition('Wall Frame', (def) {
        def.addInstance(stud);
      });
      builder.addInstance(wallFrame, layer: hiddenWalls);

      final scene = SkpFile.fromBuffer(builder.toBytes()).buildScene();
      expect(scene.layerHidden['HiddenWalls'], isTrue);
      expect(scene.layerHidden['Layer0'], isFalse);
      final ifcText = toIfc(scene, classifyUsingFullPath: true);
      expect(ifcText, contains('IFCWALL('));
      expect(ifcText, contains("'Stud 12'"));
      expect(ifcText, isNot(contains('mesh_0_ROOT')));
      expect(RegExp(r'IFCTRIANGULATEDFACESET\(').allMatches(ifcText).length, 1);

      final untitledScene =
          SkpFile.open('${Directory.current.path}/test/fixtures/Untitled.skp').buildScene();
      final untitledIfc = toIfc(untitledScene);
      expect(untitledIfc, contains("'Pset_steelframer-dict'"));
    });

    test('writes a hidden placement tag as hidden in IFC even when all definition faces are on Wood', () {
      final b = create();
      final wood = b.addLayer('Wood', color: [160, 110, 60]);
      final furnitureHidden =
          b.addLayer('FurnitureHidden', color: [40, 40, 40], hidden: true);
      final woodPanel = b.addComponentDefinition('WoodPanel', (d) {
        d.addFace(
          [(0.0, 0.0, 0.0), (2.0, 0.0, 0.0), (2.0, 2.0, 0.0), (0.0, 2.0, 0.0)],
          layer: wood,
        );
      });
      b.addInstance(woodPanel, name: 'HiddenFurniture', layer: furnitureHidden);

      final ifcText = toIfc(SkpFile.fromBuffer(b.toBytes()).buildScene());
      final layerLines = ifcText
          .split(RegExp(r'\r?\n'))
          .where((l) => l.contains('IFCPRESENTATIONLAYERWITHSTYLE('))
          .toList();
      final hiddenLines =
          layerLines.where((l) => l.contains("'FurnitureHidden'")).toList();
      expect(hiddenLines.length, 1);
      expect(hiddenLines.first, contains(',.F.,'));
      expect(layerLines.any((l) => l.contains("'Wood'")), isFalse);
    });

    test('exports valid IFC2X3 geometry, arcs, proxies, and roof attributes (#658)', () {
      const s45 = 0.7071067811865476;
      final scene = Scene(
        sceneHierarchy: InstanceNode(name: 'Root', definitionName: 'RootDef'),
        meshIndex: {
          'ClosedCrate': MeshMetadata(name: 'ClosedCrate', path: 'Root/ClosedCrate'),
          'OpenRoof': MeshMetadata(name: 'Gable Roof', path: 'Root/OpenRoof'),
          'FrontDoor': MeshMetadata(name: 'Front Door', path: 'Root/FrontDoor'),
        },
        glbPrimitives: [
          GlbPrimitive(
            geomName: 'ClosedCrate',
            materialIndex: 0,
            positions: Float32List.fromList([0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1]),
            normals: Float32List(12),
            uvs: Float32List(8),
            indices: Uint32List.fromList([0, 2, 1, 0, 1, 3, 1, 2, 3, 2, 0, 3]),
          ),
          GlbPrimitive(
            geomName: 'OpenRoof',
            materialIndex: 0,
            positions: Float32List.fromList([0, 0, 0, 1, 0, 0, 0, 1, 0]),
            normals: Float32List(9),
            uvs: Float32List(6),
            indices: Uint32List.fromList([0, 1, 2]),
          ),
          GlbPrimitive(
            geomName: 'FrontDoor',
            materialIndex: 0,
            positions: Float32List.fromList([2, 0, 0, 3, 0, 0, 2, 1, 0]),
            normals: Float32List(9),
            uvs: Float32List(6),
            indices: Uint32List.fromList([0, 1, 2]),
          ),
        ],
        gltfMaterials: [
          {
            'pbrMetallicRoughness': {
              'baseColorFactor': [0.5, 0.5, 0.5, 1.0],
            },
          },
        ],
        curveSets: [
          CurveSetMetadata(
            name: 'QuarterArc',
            pointsM: [(1.0, 0.0, 0.0), (s45, 0.0, -s45), (0.0, 0.0, -1.0)],
            closed: false,
            arc: CurveArc(
              points: [(1.0, 0.0, 0.0), (s45, 0.0, -s45), (0.0, 0.0, -1.0)],
              segments: [(0, 1, 2)],
            ),
          ),
          CurveSetMetadata(
            name: 'FullCircle',
            pointsM: [(1.0, 0.0, 0.0), (0.0, 0.0, -1.0), (-1.0, 0.0, 0.0), (0.0, 0.0, 1.0)],
            closed: true,
            arc: CurveArc(
              points: [(1.0, 0.0, 0.0), (0.0, 0.0, -1.0), (-1.0, 0.0, 0.0), (0.0, 0.0, 1.0)],
              segments: [(0, 1, 2), (2, 3, 0)],
            ),
          ),
          CurveSetMetadata(
            name: 'DegenerateArc',
            pointsM: [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (2.0, 0.0, 0.0)],
            closed: false,
            arc: CurveArc(
              points: [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (2.0, 0.0, 0.0)],
              segments: [(0, 1, 2)],
            ),
          ),
        ],
      );

      final ifcText = toIfc(scene, schema: 'IFC2X3');
      expect(ifcText, contains("FILE_SCHEMA(('IFC2X3'));"));
      expect(ifcText, isNot(contains('IFCTRIANGULATEDFACESET(')));
      expect(ifcText, isNot(contains('IFCCARTESIANPOINTLIST3D(')));
      expect(ifcText, isNot(contains('IFCINDEXEDPOLYCURVE(')));
      expect(ifcText, isNot(contains('IFCARCINDEX(')));

      expect(ifcText, contains('IFCCLOSEDSHELL('));
      expect(ifcText, contains('IFCFACETEDBREP('));
      expect(ifcText, contains("'Body','Brep',"));
      expect(ifcText, contains('IFCCONNECTEDFACESET('));
      expect(ifcText, contains('IFCFACEBASEDSURFACEMODEL('));
      expect(ifcText, contains("'Body','SurfaceModel',"));

      expect(RegExp(r'=IFCCIRCLE\(').allMatches(ifcText).length, equals(2));
      expect(RegExp(r'=IFCTRIMMEDCURVE\(').allMatches(ifcText).length, equals(1));
      expect(ifcText, contains('IFCPARAMETERVALUE(1.570796)'));
      expect(RegExp(r'=IFCPOLYLINE\(').allMatches(ifcText).length, equals(1));

      final lines = ifcText.split(RegExp(r'\r?\n'));
      final proxyLine = lines.firstWhere((l) => l.contains('=IFCBUILDINGELEMENTPROXY('));
      expect(proxyLine.endsWith(',.ELEMENT.);'), isTrue);
      final roofLine = lines.firstWhere((l) => l.contains('=IFCROOF('));
      expect(roofLine.endsWith(',.NOTDEFINED.);'), isTrue);
      final doorLine = lines.firstWhere((l) => l.contains('=IFCDOOR('));
      final doorBody = doorLine.substring(doorLine.indexOf('(') + 1, doorLine.lastIndexOf(')'));
      expect(doorBody.split(',').length, equals(10));

      final ids = lines
          .where((l) => l.startsWith('#') && l.contains('='))
          .map((l) => int.parse(l.substring(1, l.indexOf('='))))
          .toList();
      expect(ids, equals(List<int>.generate(ids.length, (i) => i + 1)));
    });
  });
}
