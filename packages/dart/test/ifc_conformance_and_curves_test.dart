import 'dart:io';
import 'dart:math' as math;
import 'dart:typed_data';
import 'package:openskp/openskp.dart';
import 'package:test/test.dart';

String _entityLine(String text, String token) =>
    text.split(RegExp(r'\r?\n')).firstWhere((l) => l.contains(token));

List<String> _splitAttributes(String line) {
  final body = line.substring(line.indexOf('(') + 1, line.lastIndexOf(')'));
  final out = <String>[];
  var depth = 0;
  final cur = StringBuffer();
  for (var i = 0; i < body.length; i++) {
    final ch = body[i];
    if (ch == '(') depth++;
    if (ch == ')') depth--;
    if (ch == ',' && depth == 0) {
      out.add(cur.toString());
      cur.clear();
    } else {
      cur.write(ch);
    }
  }
  out.add(cur.toString());
  return out;
}

void _assertIdsAreDense(String text) {
  final ids = RegExp(r'^#(\d+)=', multiLine: true)
      .allMatches(text)
      .map((m) => int.parse(m.group(1)!))
      .toList();
  expect(ids, isNotEmpty);
  expect(ids.toSet().length, equals(ids.length));
  expect(ids.reduce(math.max), equals(ids.length));
}

Scene _meshScene(
  String name,
  List<double> positions,
  List<int> indices, {
  String layer = 'Layer0',
  Map<String, bool>? layerHidden,
  Map<String, String>? properties,
}) =>
    Scene(
      sceneHierarchy: InstanceNode(name: 'Root', definitionName: 'RootDef'),
      meshIndex: {
        name: MeshMetadata(
          name: name,
          layer: layer,
          properties: properties ?? {},
        ),
      },
      glbPrimitives: [
        GlbPrimitive(
          geomName: name,
          materialIndex: 0,
          positions: Float32List.fromList(positions),
          normals: Float32List(positions.length),
          uvs: Float32List((positions.length ~/ 3) * 2),
          indices: Uint32List.fromList(indices),
        ),
      ],
      gltfMaterials: [
        {
          'pbrMetallicRoughness': {
            'baseColorFactor': [0.5, 0.5, 0.5, 1.0]
          }
        }
      ],
      layerHidden: layerHidden ?? {},
    );

void main() {
  group('IFC4 STEP conformance and loose-edge curve annotations (#285)', () {
    test('encodes non-ASCII text with ISO 10303-21 \\X2\\ and \\X4\\ escapes',
        () {
      expect(stepText("it's a\\b"), equals("it''s a\\\\b"));
      expect(stepText('测试项目'), equals(r'\X2\6D4B8BD5987976EE\X0\'));
      expect(stepText('emoji\u{1F600}墙'), equals(r'emoji\X4\0001F60000005899\X0\'));
      expect(stepText('剪力墙 JLQ-1'), equals(r'\X2\526A529B5899\X0\ JLQ-1'));

      final guid = generateIfcGuid();
      expect(guid.length, equals(22));
      expect(['0', '1', '2', '3'], contains(guid[0]));

      final seenArgs = <(String, String)>[];
      final text = toIfc(
        _meshScene(
          '剪力墙 JLQ-1',
          [0, 0, 0, 1, 0, 0, 0, 1, 0],
          [0, 1, 2],
          layer: '隐藏层',
          layerHidden: {'隐藏层': true},
          properties: {'名称': '承重墙'},
        ),
        classifier: (geomName, layerName) {
          seenArgs.add((geomName, layerName));
          return ['IFCWALL', 'IfcWall'];
        },
      );
      expect(seenArgs, equals([('剪力墙 JLQ-1', '隐藏层')]));
      expect(text, isNot(contains('剪力墙')));
      expect(text, contains(r'\X2\526A529B5899\X0\ JLQ-1'));
      expect(text, contains(r'\X2\540D79F0\X0\'));
      expect(text, contains(r'\X2\627F91CD5899\X0\'));
      expect(_entityLine(text, 'IFCPRESENTATIONLAYERWITHSTYLE('),
          contains(',.F.,.F.,.F.,())'));
    });

    test('writes IFC4 enum spellings and per-entity attribute counts', () {
      final doorText =
          toIfc(_meshScene('Front Door', [0, 0, 0, 1, 0, 0, 0, 1, 0], [0, 1, 2]));
      expect(doorText, isNot(contains('.TRUE.')));
      expect(doorText, isNot(contains('.FALSE.')));
      expect(doorText, isNot(contains('.READWRITE.')));
      expect(doorText, isNot(contains('.STERADIANUNIT.')));
      expect(doorText, contains('.NOCHANGE.'));
      expect(doorText, contains('.SOLIDANGLEUNIT.'));
      expect(doorText, contains('.STERADIAN.'));
      expect(_splitAttributes(_entityLine(doorText, 'IFCDOOR(')).length,
          equals(13));

      final wallText =
          toIfc(_meshScene('Outer Wall', [0, 0, 0, 1, 0, 0, 0, 1, 0], [0, 1, 2]));
      expect(
          _splitAttributes(_entityLine(wallText, 'IFCWALL(')).length, equals(9));

      final door2x3 = toIfc(
        _meshScene('Front Door', [0, 0, 0, 1, 0, 0, 0, 1, 0], [0, 1, 2]),
        schema: 'IFC2X3',
      );
      expect(_splitAttributes(_entityLine(door2x3, 'IFCDOOR(')).length,
          equals(10));
    });

    test('computes Closed on IfcTriangulatedFaceSet from welded manifold topology',
        () {
      String closedFlag(String txt) =>
          _splitAttributes(_entityLine(txt, 'IFCTRIANGULATEDFACESET('))[2];

      expect(
          closedFlag(
              toIfc(_meshScene('Open', [0, 0, 0, 1, 0, 0, 0, 1, 0], [0, 1, 2]))),
          equals('.F.'));

      const perFaceTetra = <double>[
        0, 0, 0, 1, 0, 0, 0, 1, 0,
        0, 0, 0, 1, 0, 0, 0, 0, 1,
        0, 0, 0, 0, 1, 0, 0, 0, 1,
        1, 0, 0, 0, 1, 0, 0, 0, 1,
      ];
      expect(
        closedFlag(toIfc(_meshScene(
          'PerFaceTetra',
          perFaceTetra,
          List<int>.generate(12, (i) => i),
        ))),
        equals('.T.'),
      );
    });

    test('exports polyline and analytic-arc curveSets as IfcAnnotation with inline IfcArcIndex',
        () {
      final polyScene = Scene(
        sceneHierarchy: InstanceNode(name: 'Root'),
        meshIndex: {},
        glbPrimitives: [],
        gltfMaterials: [],
        curveSets: [
          CurveSetMetadata(
            name: 'ClosedOutline',
            layer: 'Framing',
            path: 'ROOT',
            pointsM: const [
              (0.0, 0.0, 0.0),
              (1.0, 0.0, 0.0),
              (0.0, 0.0, 1.0),
            ],
            closed: true,
            attributeDictionaries: {
              'frame-meta': {'mark': 'B1'},
            },
          ),
        ],
      );
      final polyText = toIfc(polyScene);
      _assertIdsAreDense(polyText);
      expect(
          polyText, contains("IFCGEOMETRICREPRESENTATIONSUBCONTEXT('Annotation','Model'"));
      expect(polyText, contains('IFCANNOTATION('));
      expect(polyText, contains("'ClosedOutline'"));
      expect(polyText, contains("'Pset_frame-meta'"));
      expect(
        RegExp(r'#\d+')
            .allMatches(_splitAttributes(_entityLine(polyText, 'IFCPOLYLINE('))[0])
            .length,
        equals(4),
      );

      final arcScene = Scene(
        sceneHierarchy: InstanceNode(name: 'Root'),
        meshIndex: {},
        glbPrimitives: [],
        gltfMaterials: [],
        curveSets: [
          CurveSetMetadata(
            name: 'ArcRun',
            layer: 'Layer0',
            path: 'ROOT',
            pointsM: const [(1.0, 0.0, 0.0), (0.0, 0.0, -1.0)],
            closed: false,
            arc: CurveArc(
              points: [
                (1.0, 0.0, 0.0),
                (math.sqrt1_2, 0.0, -math.sqrt1_2),
                (0.0, 0.0, -1.0),
              ],
              segments: const [(0, 1, 2)],
            ),
          ),
        ],
      );
      final arcText = toIfc(arcScene);
      _assertIdsAreDense(arcText);
      expect(arcText, contains('IFCINDEXEDPOLYCURVE('));
      expect(arcText, contains('IFCARCINDEX((1,2,3))'));
      expect(RegExp(r'^#\d+=IFCARCINDEX\(', multiLine: true).hasMatch(arcText),
          isFalse);

      final capillaIfc =
          toIfc(SkpFile.open('test/fixtures/capilla_quiroz_v17.skp').buildScene());
      _assertIdsAreDense(capillaIfc);
      expect(RegExp(r'=IFCANNOTATION\(').allMatches(capillaIfc).length, equals(20));

      final arcsIfc =
          toIfc(SkpFile.open('test/fixtures/arcs_2021.skp').buildScene());
      _assertIdsAreDense(arcsIfc);
      expect(
          RegExp(r'=IFCINDEXEDPOLYCURVE\(').allMatches(arcsIfc).length, equals(1));
      expect(arcsIfc, contains('IFCARCINDEX((1,2,3))'));
    });

    test('escapes control characters and 0x7F and rejects non-finite coordinates or scales', () {
      expect(
        stepText('line1\r\nline2\t\x00\x1f\x7f'),
        equals(r'line1\X2\000D000A\X0\line2\X2\00090000001F007F\X0\'),
      );
      final ctrlText = toIfc(_meshScene(
        'Wall\nOne\x7f',
        [0, 0, 0, 1, 0, 0, 0, 1, 0],
        [0, 1, 2],
        properties: {'note\tkey': 'a\nb\x7f'},
      ));
      expect(ctrlText, contains(r'Wall\X2\000A\X0\One\X2\007F\X0\'));
      expect(ctrlText, contains(r'note\X2\0009\X0\key'));
      expect(ctrlText, contains(r'a\X2\000A\X0\b\X2\007F\X0\'));
      final lines = ctrlText.split('\r\n');
      final dataStart = lines.indexOf('DATA;') + 1;
      final dataEnd = lines.indexOf('ENDSEC;', dataStart);
      for (var i = dataStart; i < dataEnd; i++) {
        expect(lines[i].startsWith('#'), isTrue);
        expect(lines[i].endsWith(');'), isTrue);
      }

      final good = _meshScene('Wall', [0, 0, 0, 1, 0, 0, 0, 1, 0], [0, 1, 2]);
      expect(() => toIfc(good, scale: double.nan), throwsArgumentError);
      expect(() => toIfc(good, scale: double.infinity), throwsArgumentError);

      final nanMesh =
          _meshScene('Wall', [double.nan, 0, 0, 1, 0, 0, 0, 1, 0], [0, 1, 2]);
      expect(() => toIfc(nanMesh), throwsArgumentError);

      final infCurve = Scene(
        sceneHierarchy: InstanceNode(name: 'Root'),
        meshIndex: {},
        glbPrimitives: [],
        gltfMaterials: [],
        curveSets: [
          CurveSetMetadata(
            name: 'C',
            pointsM: const [(0.0, 0.0, 0.0), (1e308, 0.0, 0.0)],
          ),
        ],
      );
      expect(() => toIfc(infCurve, scale: 1000.0), throwsArgumentError);

      final tmpDir = Directory.systemTemp.createTempSync('openskp_ifc_nan_');
      final outPath = '${tmpDir.path}/nested/bad.ifc';
      try {
        expect(() => exportIfc(nanMesh, outPath), throwsArgumentError);
        expect(File(outPath).existsSync(), isFalse);
        expect(File(outPath).parent.existsSync(), isFalse);
      } finally {
        if (tmpDir.existsSync()) tmpDir.deleteSync(recursive: true);
      }
    });
  });
}
