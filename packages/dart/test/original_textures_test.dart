import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:openskp/openskp.dart';
import 'package:openskp/src/scene.dart' show SceneTexture, originalImageMime;
import 'package:test/test.dart';

Map<String, dynamic> _glbJson(Uint8List glb) {
  final view = ByteData.sublistView(glb);
  final jsonLen = view.getUint32(12, Endian.little);
  return jsonDecode(utf8.decode(glb.sublist(20, 20 + jsonLen))) as Map<String, dynamic>;
}

String _fixture(String name) => '${Directory.current.path}/test/fixtures/$name';

const _kept = <String, (String, String, String, String, List<int>?)>{
  'texture_tiff_2017.skp': ('Tex_TIF', 'image/tiff', 'TIFF', 'tex.tif', [0x49, 0x49, 0x2a, 0x00]),
  'texture_tiff_2021.skp': ('Tex_TIF', 'image/tiff', 'TIFF', 'tex.tif', [0x49, 0x49, 0x2a, 0x00]),
  'texture_tga_2021.skp': ('Tex_TGA', 'image/x-tga', 'TGA', 'tex.tga', null),
  'texture_gif_2021.skp': ('Tex_GIF', 'image/gif', 'GIF', 'tex.gif', [0x47, 0x49, 0x46, 0x38, 0x39, 0x61]),
};

bool _hasTex(Map<String, dynamic> m) =>
    (m['pbrMetallicRoughness'] as Map<String, dynamic>).containsKey('baseColorTexture');

void main() {
  group('originalTextures option and skipped-texture warning (#635)', () {
    for (final instanced in [false, true]) {
      final builderLabel = instanced ? 'buildInstancedScene' : 'buildScene';
      for (final entry in _kept.entries) {
        final fixture = entry.key;
        final (matName, mime, fmt, file, sig) = entry.value;

        test('$builderLabel keeps $fixture in its own format when originalTextures: true', () {
          final skp = SkpFile.open(_fixture(fixture));
          final warnings = <String>[];
          final opts = ParseOptions(
            originalTextures: true,
            onLog: (lvl, msg) {
              if (lvl == SkpLogLevel.warning && msg.startsWith('Texture of material')) {
                warnings.add(msg);
              }
            },
          );

          late final List<SceneTexture> textures;
          late final List<Map<String, dynamic>> mats;
          if (instanced) {
            final s = skp.buildInstancedScene(opts);
            textures = s.textures;
            mats = s.gltfMaterials;
          } else {
            final s = skp.buildScene(opts);
            textures = s.textures;
            mats = s.gltfMaterials;
          }
          expect(textures, hasLength(1));
          final tex = textures.single;
          expect(tex.mimeType, equals(mime));
          if (sig != null) {
            expect(tex.data.sublist(0, sig.length), equals(sig));
          }
          final stored = skp.parse().materials.firstWhere((m) => m.name == matName).texture!.data!;
          expect(tex.data, equals(stored));
          final textured = mats.where(_hasTex).toList();
          expect(textured, hasLength(1));
          expect(
            (textured.single['pbrMetallicRoughness'] as Map<String, dynamic>)['baseColorTexture'],
            equals({'index': 0}),
          );
          expect(warnings, isEmpty);
        });

        test('$builderLabel drops $fixture by default and logs warning naming originalTextures: true', () {
          final skp = SkpFile.open(_fixture(fixture));
          final warnings = <String>[];
          final opts = ParseOptions(
            onLog: (lvl, msg) {
              if (lvl == SkpLogLevel.warning && msg.startsWith('Texture of material')) {
                warnings.add(msg);
              }
            },
          );

          if (instanced) {
            final s = skp.buildInstancedScene(opts);
            expect(s.textures, isEmpty);
            expect(s.gltfMaterials.any(_hasTex), isFalse);
          } else {
            final s = skp.buildScene(opts);
            expect(s.textures, isEmpty);
            expect(s.gltfMaterials.any(_hasTex), isFalse);
          }

          expect(
            warnings,
            equals([
              "Texture of material '$matName' ($file) is $fmt, which a scene cannot carry (only PNG and JPEG): "
                  'the material is built without its texture (pass originalTextures: true to keep it in its own format)',
            ]),
          );
        });
      }

      for (final fixture in ['texture_bmp_2021.skp', 'capilla_quiroz_v17.skp']) {
        test('$builderLabel leaves PNG/JPEG textures unchanged on $fixture', () {
          final skp = SkpFile.open(_fixture(fixture));
          const opts = ParseOptions(originalTextures: true);
          if (instanced) {
            final plain = skp.buildInstancedScene();
            final kept = skp.buildInstancedScene(opts);
            expect(plain.textures, isNotEmpty);
            expect(
              kept.textures.map((t) => (t.mimeType, t.data.length)).toList(),
              equals(plain.textures.map((t) => (t.mimeType, t.data.length)).toList()),
            );
            expect(kept.gltfMaterials, equals(plain.gltfMaterials));
          } else {
            final plain = skp.buildScene();
            final kept = skp.buildScene(opts);
            expect(plain.textures, isNotEmpty);
            expect(
              kept.textures.map((t) => (t.mimeType, t.data.length)).toList(),
              equals(plain.textures.map((t) => (t.mimeType, t.data.length)).toList()),
            );
            expect(kept.gltfMaterials, equals(plain.gltfMaterials));
          }
        });
      }
    }

    test('originalImageMime recognizes signatures and rejects arbitrary bytes', () {
      final tga = Uint8List(60);
      tga[2] = 2;
      final footer = 'TRUEVISION-XFILE.\x00'.codeUnits;
      tga.setRange(42, 42 + footer.length, footer);
      expect(originalImageMime([0x49, 0x49, 0x2a, 0x00, 0, 0, 0, 0]), equals('image/tiff'));
      expect(originalImageMime([0x4d, 0x4d, 0x00, 0x2a, 0, 0, 0, 0]), equals('image/tiff'));
      expect(originalImageMime('GIF89a000000'.codeUnits), equals('image/gif'));
      expect(originalImageMime('BM0000000000'.codeUnits), equals('image/bmp'));
      expect(originalImageMime('RIFF\x00\x00\x00\x00WEBPVP8 '.codeUnits), equals('image/webp'));
      expect(originalImageMime(tga), equals('image/x-tga'));
      expect(originalImageMime('zzzzzzzzzzzzzzzz'.codeUnits), isNull);
      expect(originalImageMime(const <int>[]), isNull);
    });

    for (final instanced in [false, true]) {
      final exporterLabel = instanced ? 'toInstancedGlb' : 'toGlb';

      test('$exporterLabel omits lone TIFF texture with a single warning', () {
        final skp = SkpFile.open(_fixture('texture_tiff_2021.skp'));
        final warnings = <String>[];
        void onLog(SkpLogLevel lvl, String msg) {
          if (lvl == SkpLogLevel.warning) warnings.add(msg);
        }

        final glb = instanced
            ? toInstancedGlb(
                skp.buildInstancedScene(const ParseOptions(originalTextures: true)),
                textures: true,
                onLog: onLog,
              )
            : toGlb(
                skp.buildScene(const ParseOptions(originalTextures: true)),
                textures: true,
                onLog: onLog,
              );
        final doc = _glbJson(glb);
        expect(doc.containsKey('images'), isFalse);
        expect(doc.containsKey('textures'), isFalse);
        expect(doc.containsKey('samplers'), isFalse);
        final mats = (doc['materials'] as List<dynamic>).cast<Map<String, dynamic>>();
        expect(mats.any(_hasTex), isFalse);
        expect(mats.any((m) => m['alphaMode'] == 'MASK'), isFalse);
        expect(
          warnings,
          equals([
            "Texture 'tex.tif' is TIFF, which glTF 2.0 cannot carry (only PNG and JPEG): leaving it out of the GLB",
          ]),
        );
      });

      test('$exporterLabel drops TIFF, keeps its colour, remaps PNG index, and warns once', () {
        final skp = SkpFile.open(_fixture('texture_tiff_and_png_2021.skp'));
        final warnings = <String>[];
        void onLog(SkpLogLevel lvl, String msg) {
          if (lvl == SkpLogLevel.warning) warnings.add(msg);
        }

        late final Uint8List glb;
        if (instanced) {
          final scene = skp.buildInstancedScene(const ParseOptions(originalTextures: true));
          expect(scene.textures.map((t) => t.mimeType).toList(), equals(['image/tiff', 'image/png']));
          glb = toInstancedGlb(scene, textures: true, onLog: onLog);
          expect(scene.textures.map((t) => t.mimeType).toList(), equals(['image/tiff', 'image/png']));
        } else {
          final scene = skp.buildScene(const ParseOptions(originalTextures: true));
          expect(scene.textures.map((t) => t.mimeType).toList(), equals(['image/tiff', 'image/png']));
          glb = toGlb(scene, textures: true, onLog: onLog);
          expect(scene.textures.map((t) => t.mimeType).toList(), equals(['image/tiff', 'image/png']));
        }

        final doc = _glbJson(glb);
        final images = (doc['images'] as List<dynamic>).cast<Map<String, dynamic>>();
        expect(images, hasLength(1));
        expect(images.single['mimeType'], equals('image/png'));
        expect(doc['textures'], equals([{'sampler': 0, 'source': 0}]));
        expect(doc['samplers'], hasLength(1));

        final mats = (doc['materials'] as List<dynamic>).cast<Map<String, dynamic>>();
        final tiffMat = mats.firstWhere(
          (m) => ((m['pbrMetallicRoughness'] as Map<String, dynamic>)['baseColorFactor'] as List<dynamic>)[0] as num > 0.7,
        );
        final pngMat = mats.firstWhere(
          (m) => ((m['pbrMetallicRoughness'] as Map<String, dynamic>)['baseColorFactor'] as List<dynamic>)[2] as num > 0.8,
        );
        final tiffPbr = tiffMat['pbrMetallicRoughness'] as Map<String, dynamic>;
        expect(tiffPbr.containsKey('baseColorTexture'), isFalse);
        expect(tiffMat.containsKey('alphaMode'), isFalse);
        final tiffBcf = (tiffPbr['baseColorFactor'] as List<dynamic>).cast<num>();
        expect(tiffBcf[0], closeTo(200 / 255, 1e-4));
        expect(tiffBcf[1], closeTo(60 / 255, 1e-4));
        expect(tiffBcf[2], closeTo(40 / 255, 1e-4));

        final pngPbr = pngMat['pbrMetallicRoughness'] as Map<String, dynamic>;
        expect(pngPbr['baseColorTexture'], equals({'index': 0}));
        expect(pngMat['alphaMode'], equals('MASK'));
        expect(
          warnings,
          equals([
            "Texture 'tex.tif' is TIFF, which glTF 2.0 cannot carry (only PNG and JPEG): leaving it out of the GLB",
          ]),
        );
      });
    }
  });
}
