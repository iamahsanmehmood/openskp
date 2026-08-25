import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:openskp/openskp.dart';
import 'package:test/test.dart';

/// GLB texture embedding and the material-identity fix it depends on
/// (openskp#193, ported from TypeScript).
///
/// Before this, gltfMaterials was keyed on (color, doubleSided) alone, so
/// two different textures that happened to average to the same RGB would
/// silently collapse into one material and lose an image. Fixed by keying
/// on (color, doubleSided, textureIndex) instead, at both the
/// face-grouping and material-dedup layers.
///
/// Fixture: capilla_quiroz_v17.skp, which carries 3 real, distinct JPEG
/// textures - real coverage, not a synthetic mock.

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

bool _containsBytes(List<int> haystack, List<int> needle) {
  for (var i = 0; i <= haystack.length - needle.length; i++) {
    var match = true;
    for (var j = 0; j < needle.length; j++) {
      if (haystack[i + j] != needle[j]) { match = false; break; }
    }
    if (match) return true;
  }
  return false;
}

const _jpegMagic = [0xFF, 0xD8, 0xFF];

void main() {
  final fixturePath = 'test/fixtures/capilla_quiroz_v17.skp';

  group('GLB texture embedding', () {
    test('scene deduplicates textures and keys materials by them', () {
      final skp = SkpFile.open(fixturePath);
      final scene = skp.buildScene();

      expect(scene.textures.length, equals(3));
      for (final tex in scene.textures) {
        expect(tex.mimeType, anyOf('image/jpeg', 'image/png'));
        expect(tex.data, isNotEmpty);
      }

      var textured = 0;
      for (final m in scene.gltfMaterials) {
        final pbr = m['pbrMetallicRoughness'] as Map<String, dynamic>;
        if (pbr.containsKey('baseColorTexture')) {
          textured++;
          final idx = (pbr['baseColorTexture'] as Map<String, dynamic>)['index'] as int;
          expect(idx, inInclusiveRange(0, scene.textures.length - 1));
        }
      }
      expect(textured, equals(4));
    });

    test('export omits images by default', () {
      final skp = SkpFile.open(fixturePath);
      final scene = skp.buildScene();
      final bytes = toGlb(scene);

      final str = latin1.decode(bytes, allowInvalid: true);
      expect(str.contains('"images"'), isFalse);
      expect(_containsBytes(bytes, _jpegMagic), isFalse);
    });

    test('export embeds textures when asked', () {
      final skp = SkpFile.open(fixturePath);
      final scene = skp.buildScene();
      final noTex = toGlb(scene);
      final withTex = toGlb(scene, textures: true);

      expect(withTex.length, greaterThan(noTex.length));
      final str = latin1.decode(withTex, allowInvalid: true);
      expect(str.contains('"images"'), isTrue);
      expect(_containsBytes(withTex, _jpegMagic), isTrue);

      final parsed = _parseGlb(withTex);
      final images = parsed.json['images'] as List;
      expect(images.length, equals(3));
      for (final img in images) {
        expect(img['bufferView'], isNotNull);
        expect((img['mimeType'] as String).startsWith('image/'), isTrue);
      }
    });

    test('exportGlb file writes embedded textures', () {
      final skp = SkpFile.open(fixturePath);
      final scene = skp.buildScene();
      final tmp = File('${Directory.systemTemp.path}/openskp_glbtex_test.glb');
      try {
        exportGlb(scene, tmp.path, textures: true);
        final bytes = tmp.readAsBytesSync();
        expect(_containsBytes(bytes, _jpegMagic), isTrue);
      } finally {
        if (tmp.existsSync()) tmp.deleteSync();
      }
    });
  });
}
