import 'dart:io';

import 'package:openskp/openskp.dart';
import 'package:openskp/src/scene.dart' show SceneTexture;
import 'package:test/test.dart';

const _pngRed = <int>[
  0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0x00, 0x00, 0x00, 0x0d, 0x49, 0x48, 0x44, 0x52,
  0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x01, 0x08, 0x02, 0x00, 0x00, 0x00, 0x90, 0x77, 0x53,
  0xde, 0x00, 0x00, 0x00, 0x0f, 0x49, 0x44, 0x41, 0x54, 0x78, 0x01, 0x01, 0x04, 0x00, 0xfb, 0xff,
  0x00, 0xdc, 0x28, 0x1e, 0x03, 0x06, 0x01, 0x23, 0xe0, 0x4f, 0x0f, 0xcc, 0x00, 0x00, 0x00, 0x00,
  0x49, 0x45, 0x4e, 0x44, 0xae, 0x42, 0x60, 0x82,
];

const _pngBlue = <int>[
  0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0x00, 0x00, 0x00, 0x0d, 0x49, 0x48, 0x44, 0x52,
  0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x01, 0x08, 0x02, 0x00, 0x00, 0x00, 0x90, 0x77, 0x53,
  0xde, 0x00, 0x00, 0x00, 0x0f, 0x49, 0x44, 0x41, 0x54, 0x78, 0x01, 0x01, 0x04, 0x00, 0xfb, 0xff,
  0x00, 0x1e, 0x5a, 0xdc, 0x01, 0xee, 0x01, 0x55, 0xda, 0x6c, 0x0b, 0x85, 0x00, 0x00, 0x00, 0x00,
  0x49, 0x45, 0x4e, 0x44, 0xae, 0x42, 0x60, 0x82,
];

List<int> _texturedIndices(List<Map<String, dynamic>> mats) {
  final out = <int>[];
  for (final m in mats) {
    final pbr = m['pbrMetallicRoughness'] as Map<String, dynamic>;
    final texRef = pbr['baseColorTexture'] as Map<String, dynamic>?;
    if (texRef != null) out.add(texRef['index'] as int);
  }
  return out;
}

void main() {
  group('full-content scene texture deduplication (#645)', () {
    for (final instanced in [false, true]) {
      final label = instanced ? 'buildInstancedScene' : 'buildScene';

      test('$label keeps two different PNGs of equal byte length as two textures', () {
        expect(_pngRed.length, equals(_pngBlue.length));
        expect(_pngRed.sublist(0, 16), equals(_pngBlue.sublist(0, 16)));

        final tmpDir = Directory.systemTemp.createTempSync('openskp_645_');
        try {
          final pathA = '${tmpDir.path}${Platform.pathSeparator}a.png';
          final pathB = '${tmpDir.path}${Platform.pathSeparator}b.png';
          File(pathA).writeAsBytesSync(_pngRed);
          File(pathB).writeAsBytesSync(_pngBlue);

          final b = create();
          final matA = b.addTextureMaterial('MatA', pathA);
          final matB = b.addTextureMaterial('MatB', pathB);
          b.addFace([(0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0)], material: matA);
          b.addFace([(15, 0, 0), (25, 0, 0), (25, 10, 0), (15, 10, 0)], material: matA);
          b.addFace([(30, 0, 0), (40, 0, 0), (40, 10, 0), (30, 10, 0)], material: matB);

          final skp = SkpFile.fromBuffer(b.toBytes());
          late final List<SceneTexture> textures;
          late final List<Map<String, dynamic>> mats;
          if (instanced) {
            final s = skp.buildInstancedScene();
            textures = s.textures;
            mats = s.gltfMaterials;
          } else {
            final s = skp.buildScene();
            textures = s.textures;
            mats = s.gltfMaterials;
          }

          expect(textures, hasLength(2));
          expect(textures[0].data, equals(_pngRed));
          expect(textures[1].data, equals(_pngBlue));
          final indices = _texturedIndices(mats)..sort();
          expect(indices, equals([0, 1]));
        } finally {
          tmpDir.deleteSync(recursive: true);
        }
      });

      test('$label deduplicates identical PNG bytes across two materials into one texture', () {
        final tmpDir = Directory.systemTemp.createTempSync('openskp_645_dup_');
        try {
          final path1 = '${tmpDir.path}${Platform.pathSeparator}one.png';
          final path2 = '${tmpDir.path}${Platform.pathSeparator}two.png';
          File(path1).writeAsBytesSync(_pngRed);
          File(path2).writeAsBytesSync(List<int>.from(_pngRed));

          final b = create();
          final mat1 = b.addTextureMaterial('Mat1', path1);
          final mat2 = b.addTextureMaterial('Mat2', path2, opacity: 0.6);
          b.addFace([(0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0)], material: mat1);
          b.addFace([(20, 0, 0), (30, 0, 0), (30, 10, 0), (20, 10, 0)], material: mat2);

          final skp = SkpFile.fromBuffer(b.toBytes());
          late final List<SceneTexture> textures;
          late final List<Map<String, dynamic>> mats;
          if (instanced) {
            final s = skp.buildInstancedScene();
            textures = s.textures;
            mats = s.gltfMaterials;
          } else {
            final s = skp.buildScene();
            textures = s.textures;
            mats = s.gltfMaterials;
          }

          expect(textures, hasLength(1));
          expect(textures.single.data, equals(_pngRed));
          expect(_texturedIndices(mats), equals([0, 0]));
        } finally {
          tmpDir.deleteSync(recursive: true);
        }
      });
    }
  });
}
