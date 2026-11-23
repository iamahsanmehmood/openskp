import 'dart:io';
import 'dart:typed_data';

import 'package:openskp/openskp.dart';
import 'package:openskp_fragments/openskp_fragments.dart';
import 'package:test/test.dart';

const List<double> _identity = [
  1, 0, 0, 0,
  0, 1, 0, 0,
  0, 0, 1, 0,
  0, 0, 0, 1,
];

Uint8List _makeCircleExtrusionFrag() {
  final scene = InstancedScene(
    meshResources: [
      InstancedMeshResource(
        id: 'mesh_0',
        definitionId: 1,
        definitionName: 'Box',
        variantKey: '1|255,255,255',
        primitives: [
          LocalPrimitive(
            positions: [
              1.25, 2.5, 3.75,
              4.5, 2.5, 3.75,
              4.5, 5.25, 6.75,
            ],
            normals: List<double>.filled(9, 0.0),
            uvs: List<double>.filled(6, 0.0),
            indices: [0, 1, 2],
            materialIndex: 0,
          ),
        ],
      ),
    ],
    gltfMaterials: [{}],
    sceneHierarchy: InstancedNode(
      name: 'ROOT',
      matrix: _identity,
      children: [
        InstancedNode(
          name: 'Box1',
          matrix: _identity,
          meshResourceId: 'mesh_0',
          guid: '',
        ),
      ],
    ),
  );
  final raw = Uint8List.fromList(
    toFragments(scene, const FragmentExportOptions(raw: true)),
  );

  // Representation struct layout: [u32 id][float32[6] bbox][u8 class][3 pad].
  final expectedBbox = ByteData(24);
  const floats = [1.25, 2.5, 3.75, 4.5, 5.25, 6.75];
  for (var i = 0; i < 6; i++) {
    expectedBbox.setFloat32(i * 4, floats[i], Endian.little);
  }
  final needle = expectedBbox.buffer.asUint8List();
  var foundAt = -1;
  outer:
  for (var i = 0; i + needle.length + 1 <= raw.length; i++) {
    for (var j = 0; j < needle.length; j++) {
      if (raw[i + j] != needle[j]) continue outer;
    }
    foundAt = i;
    break;
  }
  if (foundAt < 0) {
    throw StateError('Representation bbox not found in raw fragments buffer');
  }
  raw[foundAt + 24] = 2; // CIRCLE_EXTRUSION
  return raw;
}

void main() {
  test(
      'fromFragments writes nothing to stdout or stderr with no logging configured (#556)',
      () {
    final patched = _makeCircleExtrusionFrag();
    final tmpDir = Directory.systemTemp.createTempSync('openskp_556_');
    try {
      final fragFile = File('${tmpDir.path}/patched.frag')
        ..writeAsBytesSync(patched);
      final driver = File('${tmpDir.path}/probe.dart')
        ..writeAsStringSync('''
import 'dart:io';
import 'package:openskp_fragments/openskp_fragments.dart';
void main(List<String> args) {
  final scene = readFragments(args[0]);
  if (scene.meshResources.any((r) => r.primitives.isNotEmpty)) {
    exit(2);
  }
}
''');
      final res = Process.runSync(
        Platform.resolvedExecutable,
        [
          'run',
          '--packages=${Directory.current.path}/.dart_tool/package_config.json',
          driver.path,
          fragFile.path,
        ],
        workingDirectory: Directory.current.path,
      );
      expect(res.exitCode, 0, reason: 'stderr=${res.stderr}');
      expect(res.stdout, '');
      expect(res.stderr, '');
    } finally {
      tmpDir.deleteSync(recursive: true);
    }
  });

  test('fromFragments delivers warnings through ParseOptions.onLog (#556)', () {
    final patched = _makeCircleExtrusionFrag();
    final seen = <(SkpLogLevel, String)>[];
    final back = fromFragments(
      patched,
      ParseOptions(onLog: (level, msg) => seen.add((level, msg))),
    );
    expect(back.meshResources.every((r) => r.primitives.isEmpty), isTrue);
    expect(
      seen.any((e) =>
          e.$1 == SkpLogLevel.warning && e.$2.contains('RepresentationClass')),
      isTrue,
    );
  });
}
