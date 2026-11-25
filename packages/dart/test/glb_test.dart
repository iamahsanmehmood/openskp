import 'dart:convert';
import 'dart:io';
import 'dart:math';
import 'dart:typed_data';

import 'package:openskp/openskp.dart';
import 'package:test/test.dart';

double _srgbToLinear(double c) =>
    c <= 0.04045 ? c / 12.92 : pow((c + 0.055) / 1.055, 2.4).toDouble();

Scene _triangleScene() {
  return Scene(
    sceneHierarchy: InstanceNode(),
    meshIndex: {},
    gltfMaterials: [
      {
        'pbrMetallicRoughness': {
          'baseColorFactor': [0.25, 0.5, 0.75, 1.0],
          'metallicFactor': 0.1,
          'roughnessFactor': 0.9,
        },
      },
    ],
    glbPrimitives: [
      GlbPrimitive(
        positions: [1, 2, 3, -4, 5, 0, 2, -1, 7],
        normals: [0, 0, 1, 0, 0, 1, 0, 0, 1],
        uvs: [0, 0, 1, 0, 0, 1],
        indices: [0, 1, 2],
        materialIndex: 0,
        geomName: 'triangle',
      ),
    ],
  );
}

({Map<String, dynamic> json, Uint8List binary}) _parseGlb(Uint8List bytes) {
  final bd = ByteData.sublistView(bytes);
  final jsonChunkLen = bd.getUint32(12, Endian.little);
  final jsonStr = utf8.decode(bytes.sublist(20, 20 + jsonChunkLen));
  final json = jsonDecode(jsonStr) as Map<String, dynamic>;

  final binHeaderOffset = 20 + jsonChunkLen;
  var binary = Uint8List(0);
  if (binHeaderOffset < bytes.length) {
    final binChunkLen = bd.getUint32(binHeaderOffset, Endian.little);
    binary = bytes.sublist(binHeaderOffset + 8, binHeaderOffset + 8 + binChunkLen);
  }
  return (json: json, binary: binary);
}

void main() {
  group('GLB export', () {
    test('serializes scene and binary data', () {
      final bytes = toGlb(_triangleScene());
      expect(bytes.length, greaterThanOrEqualTo(12));
      expect(utf8.decode(bytes.sublist(0, 4)), 'glTF');

      final parsed = _parseGlb(bytes);
      final json = parsed.json;
      expect(json['asset']['version'], '2.0');

      final meshes = json['meshes'] as List;
      expect(meshes.length, 1);
      final prim = (meshes[0]['primitives'] as List)[0] as Map<String, dynamic>;
      final attrs = prim['attributes'] as Map<String, dynamic>;
      expect(attrs.containsKey('POSITION'), true);
      expect(attrs.containsKey('NORMAL'), true);
      expect(attrs.containsKey('TEXCOORD_0'), true);
      expect(prim['material'], 0);

      final accessors = json['accessors'] as List;
      final posAccessor = accessors[attrs['POSITION'] as int] as Map<String, dynamic>;
      expect(posAccessor['componentType'], 5126);
      expect(posAccessor['type'], 'VEC3');
      expect(posAccessor['count'], 3);
      expect(posAccessor['min'], [-4.0, -1.0, 0.0]);
      expect(posAccessor['max'], [2.0, 5.0, 7.0]);

      final uvAccessor = accessors[attrs['TEXCOORD_0'] as int] as Map<String, dynamic>;
      expect(uvAccessor['componentType'], 5126);
      expect(uvAccessor['type'], 'VEC2');
      expect(uvAccessor['count'], 3);
      final uvBufferView = (json['bufferViews'] as List)[uvAccessor['bufferView'] as int] as Map<String, dynamic>;
      final uvOffset = uvBufferView['byteOffset'] as int;
      final bd = ByteData.sublistView(parsed.binary);
      expect(bd.getFloat32(uvOffset + 2 * 4, Endian.little), 1.0);

      final materials = json['materials'] as List;
      final pbr = materials[0]['pbrMetallicRoughness'] as Map<String, dynamic>;
      expect(pbr['baseColorFactor'][0], closeTo(_srgbToLinear(0.25), 1e-9));
      expect(pbr['baseColorFactor'][1], closeTo(_srgbToLinear(0.5), 1e-9));
      expect(pbr['baseColorFactor'][2], closeTo(_srgbToLinear(0.75), 1e-9));
      expect(pbr['baseColorFactor'][3], 1.0);
      expect(pbr['metallicFactor'], 0.1);
      expect(pbr['roughnessFactor'], 0.9);
    });

    test('serializes an empty scene', () {
      final bytes = toGlb(Scene(
        sceneHierarchy: InstanceNode(),
        meshIndex: {},
        gltfMaterials: [],
        glbPrimitives: [],
      ));
      final json = _parseGlb(bytes).json;
      expect((json['meshes'] as List).length, 0);
      expect((json['nodes'] as List).length, 0);
    });

    test('rejects malformed geometry', () {
      var scene = _triangleScene();
      scene.glbPrimitives[0].positions.removeLast();
      expect(() => toGlb(scene), throwsArgumentError);

      scene = _triangleScene();
      scene.glbPrimitives[0].normals.clear();
      expect(() => toGlb(scene), throwsArgumentError);

      scene = _triangleScene();
      scene.glbPrimitives[0].uvs.removeLast();
      expect(() => toGlb(scene), throwsArgumentError);

      scene = _triangleScene();
      scene.glbPrimitives[0].indices.clear();
      expect(() => toGlb(scene), throwsArgumentError);

      scene = _triangleScene();
      scene.glbPrimitives[0].indices[2] = 5;
      expect(() => toGlb(scene), throwsArgumentError);

      scene = _triangleScene();
      scene.glbPrimitives[0] = GlbPrimitive(
        positions: scene.glbPrimitives[0].positions,
        normals: scene.glbPrimitives[0].normals,
        uvs: scene.glbPrimitives[0].uvs,
        indices: scene.glbPrimitives[0].indices,
        materialIndex: 1,
        geomName: scene.glbPrimitives[0].geomName,
      );
      expect(() => toGlb(scene), throwsArgumentError);
    });

    test('rejects non-finite values', () {
      var scene = _triangleScene();
      scene.glbPrimitives[0].positions[0] = double.nan;
      expect(() => toGlb(scene), throwsArgumentError);

      scene = _triangleScene();
      scene.glbPrimitives[0].normals[0] = double.infinity;
      expect(() => toGlb(scene), throwsArgumentError);

      scene = _triangleScene();
      scene.glbPrimitives[0].uvs[0] = double.negativeInfinity;
      expect(() => toGlb(scene), throwsArgumentError);
    });

    test('exports real fixture matching toGlb and buildScene', () {
      final fixturePath = '${Directory.current.path}/test/fixtures/capilla_quiroz_v17.skp';
      final scene = SkpFile.open(fixturePath).buildScene();
      final expected = toGlb(scene);

      final output = '${Directory.systemTemp.path}/openskp-dart-glb-test-${DateTime.now().microsecondsSinceEpoch}.glb';
      exportGlb(scene, output);
      final file = File(output);
      try {
        final actual = file.readAsBytesSync();
        expect(actual, expected);

        final parsed = _parseGlb(Uint8List.fromList(actual));
        final meshes = (parsed.json['meshes'] as List)[0]['primitives'] as List;
        expect(meshes.length, scene.glbPrimitives.length);

        // Every primitive must carry TEXCOORD_0, and the decoded values
        // must exactly match the source GlbPrimitive.uvs that fed the
        // writer - a real round-trip check, not just "some accessor
        // exists."
        final bd = ByteData.sublistView(parsed.binary);
        for (var i = 0; i < scene.glbPrimitives.length; i++) {
          final prim = scene.glbPrimitives[i];
          final attrs = (meshes[i] as Map<String, dynamic>)['attributes'] as Map<String, dynamic>;
          expect(attrs.containsKey('TEXCOORD_0'), true);
          final uvAccessor = (parsed.json['accessors'] as List)[attrs['TEXCOORD_0'] as int] as Map<String, dynamic>;
          final uvBufferView = (parsed.json['bufferViews'] as List)[uvAccessor['bufferView'] as int] as Map<String, dynamic>;
          final uvOffset = uvBufferView['byteOffset'] as int;
          final uvCount = uvAccessor['count'] as int;
          expect(prim.uvs.length, uvCount * 2);
          for (var j = 0; j < prim.uvs.length; j++) {
            final decoded = bd.getFloat32(uvOffset + j * 4, Endian.little);
            // prim.uvs is double (64-bit); the accessor stores float32,
            // so compare against the same round-trip precision.
            final expectedRounded = (ByteData(4)
                  ..setFloat32(0, prim.uvs[j], Endian.little))
                .getFloat32(0, Endian.little);
            expect(decoded, expectedRounded);
          }
        }
      } finally {
        if (file.existsSync()) file.deleteSync();
      }
    });

    test('reports file failures without creating directories', () {
      final output = '${Directory.systemTemp.path}/openskp-missing-parent-${DateTime.now().microsecondsSinceEpoch}/asset.glb';
      expect(() => exportGlb(_triangleScene(), output), throwsA(anything));
      expect(Directory(output).parent.existsSync(), false);
    });

    test('converts sRGB baseColorFactor to linear in baked and instanced GLB while keeping scene materials sRGB (#650)', () {
      final builder = create();
      final specs = <(String, List<int>)>[
        ('LayerRed', [255, 84, 84, 255]),
        ('White', [255, 255, 255, 255]),
        ('Black', [0, 0, 0, 255]),
        ('MidGrey', [188, 188, 188, 255]),
        ('DarkToe', [10, 10, 10, 128]),
      ];
      final handles = [
        for (final (name, rgba) in specs) builder.addMaterial(name, rgba),
      ];
      for (var i = 0; i < handles.length; i++) {
        final x = i * 10.0;
        builder.addFace(
          [(x, 0.0, 0.0), (x + 5.0, 0.0, 0.0), (x + 5.0, 5.0, 0.0), (x, 5.0, 0.0)],
          material: handles[i],
        );
      }

      final skp = SkpFile.fromBuffer(builder.toBytes());
      final bakedScene = skp.buildScene();
      final instancedScene = skp.buildInstancedScene();

      for (final glbBytes in [toGlb(bakedScene), toInstancedGlb(instancedScene)]) {
        final json = _parseGlb(glbBytes).json;
        final factors = (json['materials'] as List)
            .map((m) => (((m as Map)['pbrMetallicRoughness'] as Map)['baseColorFactor'] as List)
                .cast<num>()
                .map((e) => e.toDouble())
                .toList())
            .toList();

        List<double>? findClose(List<double> target) {
          for (final f in factors) {
            var ok = true;
            for (var k = 0; k < 4; k++) {
              if ((f[k] - target[k]).abs() >= 1e-4) {
                ok = false;
                break;
              }
            }
            if (ok) return f;
          }
          return null;
        }

        final red = findClose([1.0, _srgbToLinear(84 / 255), _srgbToLinear(84 / 255), 1.0]);
        expect(red, isNotNull);
        expect(red![0], 1.0);
        expect(red[1], closeTo(0.0887, 1e-4));
        expect(red[2], closeTo(0.0887, 1e-4));
        expect(red[3], 1.0);

        expect(findClose([1.0, 1.0, 1.0, 1.0]), equals([1.0, 1.0, 1.0, 1.0]));
        expect(findClose([0.0, 0.0, 0.0, 1.0]), equals([0.0, 0.0, 0.0, 1.0]));

        final mid = findClose([
          _srgbToLinear(188 / 255),
          _srgbToLinear(188 / 255),
          _srgbToLinear(188 / 255),
          1.0,
        ]);
        expect(mid, isNotNull);
        expect(mid![0], closeTo(0.503, 1e-3));
        expect(mid[0], closeTo(_srgbToLinear(188 / 255), 1e-9));

        final toe = findClose([
          (10 / 255) / 12.92,
          (10 / 255) / 12.92,
          (10 / 255) / 12.92,
          128 / 255,
        ]);
        expect(toe, isNotNull);
        expect(toe![0], closeTo((10 / 255) / 12.92, 1e-9));
        expect(toe[3], closeTo(128 / 255, 1e-6));
      }

      // Scene gltfMaterials remain sRGB.
      final sceneFactors = bakedScene.gltfMaterials
          .map((m) => ((m['pbrMetallicRoughness'] as Map)['baseColorFactor'] as List).cast<num>().toList())
          .toList();
      expect(sceneFactors.any((f) => f[0] == 1.0 && (f[1] - 84 / 255).abs() < 1e-6), isTrue);
      expect(sceneFactors.any((f) => (f[0] - 188 / 255).abs() < 1e-6), isTrue);

      // Embedded non-colorized textures write [1, 1, 1, alpha], while scene
      // gltfMaterials keeps average colours and colorized materials stay tinted (#657).
      final capillaSkp = SkpFile.open(
          '${Directory.current.path}/test/fixtures/capilla_quiroz_v17.skp');
      final capillaScene = capillaSkp.buildScene();
      final capillaInstanced = capillaSkp.buildInstancedScene();
      final sceneTexFactors = capillaScene.gltfMaterials
          .map((m) => m['pbrMetallicRoughness'] as Map<String, dynamic>)
          .where((p) => p.containsKey('baseColorTexture'))
          .map((p) => (p['baseColorFactor'] as List).cast<num>().map((e) => e.toDouble()).toList())
          .toList();
      expect(sceneTexFactors.any((f) => f[0] < 0.6 && f[2] < 0.2), isTrue);
      final expectedWhiteFactors = [
        for (final f in sceneTexFactors) <double>[1.0, 1.0, 1.0, f[3]],
      ];
      for (final capillaBytes in [
        toGlb(capillaScene, textures: true),
        toInstancedGlb(capillaInstanced, textures: true),
      ]) {
        final capillaJson = _parseGlb(capillaBytes).json;
        final actualTexFactors = (capillaJson['materials'] as List)
            .map((m) =>
                (m as Map)['pbrMetallicRoughness'] as Map<String, dynamic>)
            .where((p) => p.containsKey('baseColorTexture'))
            .map((p) => (p['baseColorFactor'] as List)
                .cast<num>()
                .map((e) => e.toDouble())
                .toList())
            .toList();
        expect(actualTexFactors, equals(expectedWhiteFactors));
      }

      // End-to-end model -> scene -> GLB on a real .skp containing one plain and one
      // colorized material sharing the same RGB (200, 80, 60) and PNG image (#657).
      final bothSkp = SkpFile.open(
        '${Directory.current.path}/../cpp/tests/fixtures/colorized_and_plain_texture.skp',
      );
      final bothBaked = bothSkp.buildScene();
      final bothInstanced = bothSkp.buildInstancedScene();
      for (final mats in [
        bothBaked.gltfMaterials,
        bothInstanced.gltfMaterials
      ]) {
        expect(mats, hasLength(2));
        final plainMat = mats.firstWhere((m) => !m.containsKey('extras'));
        final colMat = mats.firstWhere((m) => m.containsKey('extras'));
        expect(
          colMat['extras'],
          equals(<String, dynamic>{
            'sketchup': <String, dynamic>{
              'colorized': true,
              'colorize_type': 1
            },
          }),
        );
        expect(
          (plainMat['pbrMetallicRoughness'] as Map)['baseColorFactor'],
          equals([200 / 255, 80 / 255, 60 / 255, 1.0]),
        );
        expect(
          (colMat['pbrMetallicRoughness'] as Map)['baseColorFactor'],
          equals([200 / 255, 80 / 255, 60 / 255, 1.0]),
        );
      }

      for (final withTexBytes in [
        toGlb(bothBaked, textures: true),
        toInstancedGlb(bothInstanced, textures: true),
      ]) {
        final withTexJson = _parseGlb(withTexBytes).json;
        final mats =
            (withTexJson['materials'] as List).cast<Map<String, dynamic>>();
        expect(mats, hasLength(2));
        final plainGlbMat = mats.firstWhere((m) => !m.containsKey('extras'));
        final colGlbMat = mats.firstWhere((m) => m.containsKey('extras'));
        expect((plainGlbMat['pbrMetallicRoughness'] as Map)['baseColorTexture'],
            equals({'index': 0}));
        expect((plainGlbMat['pbrMetallicRoughness'] as Map)['baseColorFactor'],
            equals([1.0, 1.0, 1.0, 1.0]));
        expect(
          colGlbMat['extras'],
          equals(<String, dynamic>{
            'sketchup': <String, dynamic>{
              'colorized': true,
              'colorize_type': 1
            },
          }),
        );
        expect((colGlbMat['pbrMetallicRoughness'] as Map)['baseColorTexture'],
            equals({'index': 0}));
        expect(
          (colGlbMat['pbrMetallicRoughness'] as Map)['baseColorFactor'],
          equals([200 / 255, 80 / 255, 60 / 255, 1.0]),
        );
      }

      final expectedLinearTint = [
        _srgbToLinear(200 / 255),
        _srgbToLinear(80 / 255),
        _srgbToLinear(60 / 255),
        1.0,
      ];
      for (final noTexBytes in [
        toGlb(bothBaked, textures: false),
        toInstancedGlb(bothInstanced, textures: false),
      ]) {
        final noTexJson = _parseGlb(noTexBytes).json;
        expect(noTexJson.containsKey('textures'), isFalse);
        final mats =
            (noTexJson['materials'] as List).cast<Map<String, dynamic>>();
        expect(mats, hasLength(2));
        for (final m in mats) {
          final pbr = m['pbrMetallicRoughness'] as Map<String, dynamic>;
          expect(pbr.containsKey('baseColorTexture'), isFalse);
          final bcf = (pbr['baseColorFactor'] as List)
              .cast<num>()
              .map((e) => e.toDouble())
              .toList();
          for (var c = 0; c < 4; c++) {
            expect(bcf[c], closeTo(expectedLinearTint[c], 1e-9));
          }
        }
      }
    });
  });
}
