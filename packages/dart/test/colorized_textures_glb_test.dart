import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:archive/archive.dart' show ZLibEncoder, getCrc32;
import 'package:openskp/openskp.dart';
import 'package:openskp/src/core.dart' show Core, RawParsed;
import 'package:openskp/src/geometry.dart'
    show
        GeometryBuilder,
        GeometryBuilderFace,
        GeometryBuilderInstance,
        RawDefinition,
        RawMaterial,
        RawTexture;
import 'package:openskp/src/glb_textures.dart'
    show
        GltfTextureBakeStats,
        colorizeImageU8,
        decodePngRgba,
        encodePngU8,
        resolveGltfTextures,
        stripJpegExifApp1;
import 'package:openskp/src/instanced_scene.dart' show InstancedSceneBuilder;
import 'package:openskp/src/scene.dart' show SceneBuilder, SceneTexture;
import 'package:test/test.dart';

({Map<String, dynamic> json, Uint8List binary}) _parseGlb(Uint8List bytes) {
  final bd = ByteData.sublistView(bytes);
  final jsonChunkLen = bd.getUint32(12, Endian.little);
  final jsonStr = utf8.decode(bytes.sublist(20, 20 + jsonChunkLen));
  final json = jsonDecode(jsonStr) as Map<String, dynamic>;

  final binHeaderOffset = 20 + jsonChunkLen;
  var binary = Uint8List(0);
  if (binHeaderOffset < bytes.length) {
    final binChunkLen = bd.getUint32(binHeaderOffset, Endian.little);
    binary = Uint8List.sublistView(
      bytes,
      binHeaderOffset + 8,
      binHeaderOffset + 8 + binChunkLen,
    );
  }
  return (json: json, binary: binary);
}

Uint8List _extractGlbImageBytes(
  Map<String, dynamic> json,
  Uint8List binary,
  int imgIdx,
) {
  final img = (json['images'] as List)[imgIdx] as Map<String, dynamic>;
  final bvIdx = img['bufferView'] as int;
  final bv = (json['bufferViews'] as List)[bvIdx] as Map<String, dynamic>;
  final offset = (bv['byteOffset'] as int?) ?? 0;
  final length = bv['byteLength'] as int;
  return Uint8List.fromList(binary.sublist(offset, offset + length));
}

List<double> _translationMatrix(double tx, double ty, double tz) => <double>[
      1,
      0,
      0,
      0,
      0,
      1,
      0,
      0,
      0,
      0,
      1,
      0,
      tx,
      ty,
      tz,
      1,
    ];

GeometryBuilder _makeQuadBuilder(double size, int materialId) {
  final gb = GeometryBuilder();
  gb.vertices[1] = (0, 0, 0);
  gb.vertices[2] = (size, 0, 0);
  gb.vertices[3] = (size, size, 0);
  gb.vertices[4] = (0, size, 0);
  gb.edges[1] = (1, 2);
  gb.edges[2] = (2, 3);
  gb.edges[3] = (3, 4);
  gb.edges[4] = (4, 1);
  gb.faces[1] = GeometryBuilderFace()
    ..loops = [
      [(1, 1), (2, 1), (3, 1), (4, 1)],
    ]
    ..normal = (0, 0, 1)
    ..materialId = materialId;
  return gb;
}

({Scene baked, InstancedScene instanced}) _buildColorizeQuadsScenes([
  int repeatsPerMaterial = 1,
]) {
  final origBytes = File(
    '${Directory.current.path}/test/fixtures/legacy_textured_colorize_2017.skp',
  ).readAsBytesSync();
  final origParsed = Core.fullParse(origBytes);
  final parsed = RawParsed()
    ..version = origParsed.version
    ..layerColors['Layer0'] = (200, 200, 200)
    ..layerIdToName[1] = 'Layer0';

  var nextMatId = 1;
  for (final entry in origParsed.materials.entries) {
    final id = nextMatId++;
    parsed.materials[entry.key] = entry.value;
    parsed.materialIdToName[id] = entry.key;
  }

  final shiftId =
      parsed.materialIdToName.entries.firstWhere((e) => e.value == 'shift').key;
  parsed.defsDict[1] = RawDefinition(
    guid: 'DEF_TILE',
    name: 'ColorizedTile',
    builder: _makeQuadBuilder(12.0, shiftId),
  );

  final rootGb = parsed.root.builder;
  var vId = 1;
  var eId = 100;
  var fId = 1000;
  const names = <String>['plain', 'tint', 'shift', 'tint_half'];
  for (var idx = 0; idx < names.length; idx++) {
    final mId = parsed.materialIdToName.entries
        .firstWhere((e) => e.value == names[idx])
        .key;
    for (var r = 0; r < repeatsPerMaterial; r++) {
      final x0 = idx * 24.0;
      final y0 = r * 24.0;
      final v1 = vId++, v2 = vId++, v3 = vId++, v4 = vId++;
      rootGb.vertices[v1] = (x0, y0, 0);
      rootGb.vertices[v2] = (x0 + 12.0, y0, 0);
      rootGb.vertices[v3] = (x0 + 12.0, y0 + 12.0, 0);
      rootGb.vertices[v4] = (x0, y0 + 12.0, 0);
      final e1 = eId++, e2 = eId++, e3 = eId++, e4 = eId++;
      rootGb.edges[e1] = (v1, v2);
      rootGb.edges[e2] = (v2, v3);
      rootGb.edges[e3] = (v3, v4);
      rootGb.edges[e4] = (v4, v1);
      rootGb.faces[fId++] = GeometryBuilderFace()
        ..loops = [
          [(e1, 1), (e2, 1), (e3, 1), (e4, 1)],
        ]
        ..normal = (0, 0, 1)
        ..materialId = mId;
    }
  }

  for (var r = 0; r < repeatsPerMaterial; r++) {
    rootGb.instances.add(
      GeometryBuilderInstance()
        ..refIdx = 1
        ..name = 'Tile_$r'
        ..matrix = _translationMatrix(200.0, r * 24.0, 0.0),
    );
  }

  return (
    baked: SceneBuilder.build(parsed),
    instanced: InstancedSceneBuilder.build(parsed),
  );
}

Uint8List _pngChunk(String type, List<int> payload) {
  final out = Uint8List(12 + payload.length);
  final view = ByteData.sublistView(out);
  view.setUint32(0, payload.length, Endian.big);
  for (var i = 0; i < 4; i++) {
    out[4 + i] = type.codeUnitAt(i);
  }
  out.setRange(8, 8 + payload.length, payload);
  final crc = getCrc32(Uint8List.sublistView(out, 4, 8 + payload.length));
  view.setUint32(8 + payload.length, crc, Endian.big);
  return out;
}

Uint8List _concatBytes(List<List<int>> parts) {
  var total = 0;
  for (final p in parts) {
    total += p.length;
  }
  final out = Uint8List(total);
  var off = 0;
  for (final p in parts) {
    out.setRange(off, off + p.length, p);
    off += p.length;
  }
  return out;
}

Uint8List _makeDeclaredSizePng(int width, int height) {
  const sig = <int>[137, 80, 78, 71, 13, 10, 26, 10];
  final ihdr = Uint8List(13);
  final view = ByteData.sublistView(ihdr);
  view.setUint32(0, width, Endian.big);
  view.setUint32(4, height, Endian.big);
  ihdr[8] = 8;
  ihdr[9] = 2;
  return _concatBytes([
    sig,
    _pngChunk('IHDR', ihdr),
    _pngChunk('IDAT', const <int>[0x78, 0x9c]),
    _pngChunk('IEND', const <int>[]),
  ]);
}

Uint8List _makeTestSof0Jpeg(int width, int height, List<int> rgb) {
  const app0 = <int>[
    0xff,
    0xe0,
    0x00,
    0x10,
    0x4a,
    0x46,
    0x49,
    0x46,
    0x00,
    0x01,
    0x01,
    0x00,
    0x00,
    0x01,
    0x00,
    0x01,
    0x00,
    0x00,
  ];
  final sof0 = <int>[
    0xff,
    0xc0,
    0x00,
    0x11,
    0x08,
    (height >> 8) & 0xff,
    height & 0xff,
    (width >> 8) & 0xff,
    width & 0xff,
    0x03,
    0x01,
    0x11,
    0x00,
    0x02,
    0x11,
    0x01,
    0x03,
    0x11,
    0x01,
  ];
  final app2Len = 2 + 4 + rgb.length;
  final app2 = Uint8List(2 + app2Len);
  app2[0] = 0xff;
  app2[1] = 0xe2;
  app2[2] = (app2Len >> 8) & 0xff;
  app2[3] = app2Len & 0xff;
  app2.setRange(4, 8, 'TRGB'.codeUnits);
  app2.setRange(8, 8 + rgb.length, rgb);
  const sosAndEoi = <int>[
    0xff,
    0xda,
    0x00,
    0x0c,
    0x03,
    0x01,
    0x00,
    0x02,
    0x11,
    0x03,
    0x11,
    0x00,
    0x3f,
    0x00,
    0x00,
    0xff,
    0xd9,
  ];
  return _concatBytes([
    const <int>[0xff, 0xd8],
    app0,
    sof0,
    app2,
    sosAndEoi,
  ]);
}

Uint8List _injectExifOrientationApp1(Uint8List jpegBytes,
    [int orientation = 6]) {
  final tiff = <int>[
    0x49,
    0x49,
    42,
    0,
    8,
    0,
    0,
    0,
    1,
    0,
    0x12,
    0x01,
    3,
    0,
    1,
    0,
    0,
    0,
    orientation & 0xff,
    (orientation >> 8) & 0xff,
    0,
    0,
    0,
    0,
    0,
    0,
  ];
  const exifHeader = <int>[0x45, 0x78, 0x69, 0x66, 0, 0];
  final segLen = 2 + exifHeader.length + tiff.length;
  final app1 = Uint8List(2 + segLen);
  app1[0] = 0xff;
  app1[1] = 0xe1;
  app1[2] = (segLen >> 8) & 0xff;
  app1[3] = segLen & 0xff;
  app1.setRange(4, 4 + exifHeader.length, exifHeader);
  app1.setRange(
      4 + exifHeader.length, 4 + exifHeader.length + tiff.length, tiff);
  return _concatBytes([
    jpegBytes.sublist(0, 2),
    app1,
    jpegBytes.sublist(2),
  ]);
}

bool _hasExifSignature(List<int> bytes) {
  for (var i = 0; i + 6 <= bytes.length; i++) {
    if (bytes[i] == 0x45 &&
        bytes[i + 1] == 0x78 &&
        bytes[i + 2] == 0x69 &&
        bytes[i + 3] == 0x66 &&
        bytes[i + 4] == 0 &&
        bytes[i + 5] == 0) {
      return true;
    }
  }
  return false;
}

SkpImageCodec _createTestJpegCodec({
  required void Function() onDecode,
  required void Function(int quality, String mimeType) onEncode,
}) {
  return SkpImageCodec(
    decode: (bytes, mimeType) {
      expect(mimeType, equals('image/jpeg'));
      onDecode();
      if (bytes.length < 4 || bytes[0] != 0xff || bytes[1] != 0xd8) return null;
      var pos = 2;
      var width = 0;
      var height = 0;
      Uint8List? rgbPayload;
      while (pos + 4 <= bytes.length && bytes[pos] == 0xff) {
        while (pos < bytes.length && bytes[pos] == 0xff) {
          pos++;
        }
        final marker = bytes[pos++];
        if (marker == 0xd9 || marker == 0xda) break;
        if (marker == 0x01 || (marker >= 0xd0 && marker <= 0xd8)) continue;
        final segLen = (bytes[pos] << 8) | bytes[pos + 1];
        if (marker == 0xc0) {
          height = (bytes[pos + 3] << 8) | bytes[pos + 4];
          width = (bytes[pos + 5] << 8) | bytes[pos + 6];
        } else if (marker == 0xe2 &&
            segLen >= 6 &&
            bytes[pos + 2] == 0x54 &&
            bytes[pos + 3] == 0x52 &&
            bytes[pos + 4] == 0x47 &&
            bytes[pos + 5] == 0x42) {
          rgbPayload = Uint8List.sublistView(bytes, pos + 6, pos + segLen);
        }
        pos += segLen;
      }
      if (width <= 0 ||
          height <= 0 ||
          rgbPayload == null ||
          rgbPayload.length < width * height * 3) {
        return null;
      }
      final rgba = Uint8List(width * height * 4);
      for (var i = 0; i < width * height; i++) {
        rgba[i * 4] = rgbPayload[i * 3];
        rgba[i * 4 + 1] = rgbPayload[i * 3 + 1];
        rgba[i * 4 + 2] = rgbPayload[i * 3 + 2];
        rgba[i * 4 + 3] = 255;
      }
      return (width: width, height: height, rgba: rgba);
    },
    encode: (rgba, width, height, mimeType, {int quality = 95}) {
      onEncode(quality, mimeType);
      expect(rgba.length, equals(width * height * 4));
      final rgb = Uint8List(width * height * 3);
      for (var i = 0; i < width * height; i++) {
        rgb[i * 3] = rgba[i * 4];
        rgb[i * 3 + 1] = rgba[i * 4 + 1];
        rgb[i * 3 + 2] = rgba[i * 4 + 2];
      }
      return _makeTestSof0Jpeg(width, height, rgb);
    },
  );
}

void main() {
  group('Colorized textures in GLB and instanced GLB (#285)', () {
    test(
        'bakes shift, tint, and opacity PNGs out of the box without mutating scenes',
        () {
      final (:baked, :instanced) = _buildColorizeQuadsScenes(3);

      final origSceneMatsJson = jsonEncode(baked.gltfMaterials);
      final origInstMatsJson = jsonEncode(instanced.gltfMaterials);
      final origTexBytes = Uint8List.fromList(baked.textures[0].data);

      const expected = <String>{
        '200,40,40|40,40,200|100',
        '32,237,80|32,237,80|100',
        '172,253,46|253,46,172|100',
        '65,73,255|65,73,255|50',
      };

      final ts = DateTime.now().microsecondsSinceEpoch;
      final tmpBaked =
          File('${Directory.systemTemp.path}/openskp_col_b_$ts.glb');
      final tmpInst =
          File('${Directory.systemTemp.path}/openskp_col_i_$ts.glb');
      try {
        exportGlb(baked, tmpBaked.path, textures: true);
        exportInstancedGlb(instanced, tmpInst.path, textures: true);

        for (final glbBytes in [
          toGlb(baked, textures: true),
          toInstancedGlb(instanced, textures: true),
          tmpBaked.readAsBytesSync(),
          tmpInst.readAsBytesSync(),
        ]) {
          expect(jsonEncode(baked.gltfMaterials), equals(origSceneMatsJson));
          expect(jsonEncode(instanced.gltfMaterials), equals(origInstMatsJson));
          expect(baked.textures[0].data, equals(origTexBytes));
          expect(instanced.textures[0].data, equals(origTexBytes));

          final parsed = _parseGlb(glbBytes);
          expect(parsed.json['images'], hasLength(4));
          final actual = <String>{};
          var texturedCount = 0;

          for (final rawMat in (parsed.json['materials'] as List)) {
            final mat = rawMat as Map<String, dynamic>;
            final pbr = mat['pbrMetallicRoughness'] as Map<String, dynamic>;
            final texRef = pbr['baseColorTexture'] as Map<String, dynamic>?;
            if (texRef == null) continue;
            texturedCount++;
            final bcf = (pbr['baseColorFactor'] as List)
                .cast<num>()
                .map((e) => e.toDouble())
                .toList();
            expect(bcf[0], closeTo(1.0, 1e-6));
            expect(bcf[1], closeTo(1.0, 1e-6));
            expect(bcf[2], closeTo(1.0, 1e-6));

            final imgIdx =
                ((parsed.json['textures'] as List)[texRef['index'] as int]
                    as Map<String, dynamic>)['source'] as int;
            expect(
              ((parsed.json['images'] as List)[imgIdx]
                  as Map<String, dynamic>)['mimeType'],
              equals('image/png'),
            );
            final imgBytes = _extractGlbImageBytes(
              parsed.json,
              parsed.binary,
              imgIdx,
            );
            final dec = decodePngRgba(imgBytes);
            expect(dec.width, equals(2));
            expect(dec.height, equals(1));
            final left = '${dec.rgba[0]},${dec.rgba[1]},${dec.rgba[2]}';
            final right = '${dec.rgba[4]},${dec.rgba[5]},${dec.rgba[6]}';
            final opacityPct = (bcf[3] * 100.0).round();
            actual.add('$left|$right|$opacityPct');
          }

          expect(texturedCount, equals(4));
          expect(actual, equals(expected));
        }
      } finally {
        if (tmpBaked.existsSync()) tmpBaked.deleteSync();
        if (tmpInst.existsSync()) tmpInst.deleteSync();
      }
    });

    test(
        'decodes each texture once and bakes once per unique (texture, colorizeType, color)',
        () {
      final (:baked, :instanced) = _buildColorizeQuadsScenes(8);

      for (final (textures, materials) in [
        (baked.textures, baked.gltfMaterials),
        (instanced.textures, instanced.gltfMaterials),
      ]) {
        final stats = GltfTextureBakeStats();
        final (outTex, _) = resolveGltfTextures(
          textures,
          materials,
          true,
          null,
          null,
          stats,
        );
        expect(outTex, hasLength(4));
        expect(stats.decodeCalls, equals(1));
        expect(stats.colorizeCalls, equals(3));
      }

      final png1x1 = encodePngU8(Uint8List.fromList([180, 50, 90]), 1, 1, 3);
      final sharedMats = <Map<String, dynamic>>[
        for (final alpha in [1.0, 0.5])
          <String, dynamic>{
            'name': 'm_$alpha',
            'pbrMetallicRoughness': <String, dynamic>{
              'baseColorFactor': <double>[200 / 255, 90 / 255, 20 / 255, alpha],
              'baseColorTexture': <String, dynamic>{'index': 0},
              'metallicFactor': 0.0,
              'roughnessFactor': 0.8,
            },
            'extras': <String, dynamic>{
              'sketchup': <String, dynamic>{
                'colorized': true,
                'colorize_type': 0,
              },
            },
          },
      ];
      final stats2 = GltfTextureBakeStats();
      final (outTex2, _) = resolveGltfTextures(
        [
          SceneTexture(data: png1x1, mimeType: 'image/png', filename: 'one.png')
        ],
        sharedMats,
        true,
        null,
        null,
        stats2,
      );
      expect(stats2.decodeCalls, equals(1));
      expect(stats2.colorizeCalls, equals(1));
      expect(outTex2, hasLength(1));
    });

    test(
        'decodes indexed, sub-byte greyscale, 16-bit, tRNS, and Adam7 interlaced PNGs',
        () {
      const sig = <int>[137, 80, 78, 71, 13, 10, 26, 10];

      // 1) 4-bit indexed PNG with PLTE + tRNS (2x1: alpha=64 red, opaque blue)
      const ihdrIdx = <int>[0, 0, 0, 2, 0, 0, 0, 1, 4, 3, 0, 0, 0];
      const plte = <int>[250, 10, 20, 30, 40, 240];
      const trns = <int>[64];
      final idatIdx = const ZLibEncoder().encode(const <int>[0, 0x01]);
      final indexedPng = _concatBytes([
        sig,
        _pngChunk('IHDR', ihdrIdx),
        _pngChunk('PLTE', plte),
        _pngChunk('tRNS', trns),
        _pngChunk('IDAT', idatIdx),
        _pngChunk('IEND', const <int>[]),
      ]);
      final decIdx = decodePngRgba(indexedPng);
      expect(decIdx.rgba, equals([250, 10, 20, 64, 30, 40, 240, 255]));

      // 2) 2-bit greyscale PNG with tRNS (4x1: samples 0, 1, 2, 3; sample 1 transparent)
      const ihdrGrey = <int>[0, 0, 0, 4, 0, 0, 0, 1, 2, 0, 0, 0, 0];
      const trnsGrey = <int>[0, 1];
      final idatGrey = const ZLibEncoder().encode(const <int>[0, 0x1b]);
      final greyPng = _concatBytes([
        sig,
        _pngChunk('IHDR', ihdrGrey),
        _pngChunk('tRNS', trnsGrey),
        _pngChunk('IDAT', idatGrey),
        _pngChunk('IEND', const <int>[]),
      ]);
      final decGrey = decodePngRgba(greyPng);
      expect(
        decGrey.rgba,
        equals([
          0,
          0,
          0,
          255,
          85,
          85,
          85,
          0,
          170,
          170,
          170,
          255,
          255,
          255,
          255,
          255,
        ]),
      );

      // 3) 16-bit RGB PNG with Adam7 interlace (3x3)
      const ihdrAdam7 = <int>[0, 0, 0, 3, 0, 0, 0, 3, 16, 2, 0, 0, 1];
      final passScanlines = <int>[];
      final expectedGrid = Uint8List(3 * 3 * 4);
      for (var y = 0; y < 3; y++) {
        for (var x = 0; x < 3; x++) {
          final i4 = (y * 3 + x) * 4;
          expectedGrid[i4] = x * 100;
          expectedGrid[i4 + 1] = y * 100;
          expectedGrid[i4 + 2] = 128;
          expectedGrid[i4 + 3] = 255;
        }
      }
      const adam7 = <(int, int, int, int)>[
        (0, 8, 0, 8),
        (4, 8, 0, 8),
        (0, 4, 4, 8),
        (2, 4, 0, 4),
        (0, 2, 2, 4),
        (1, 2, 0, 2),
        (0, 1, 1, 2),
      ];
      for (final (x0, dx, y0, dy) in adam7) {
        final pw = 3 > x0 ? (3 - x0 + dx - 1) ~/ dx : 0;
        final ph = 3 > y0 ? (3 - y0 + dy - 1) ~/ dy : 0;
        if (pw == 0 || ph == 0) continue;
        for (var py = 0; py < ph; py++) {
          passScanlines.add(0);
          final y = y0 + py * dy;
          for (var px = 0; px < pw; px++) {
            final x = x0 + px * dx;
            for (final c8 in [x * 100, y * 100, 128]) {
              final c16 = c8 * 257;
              passScanlines.add((c16 >> 8) & 0xff);
              passScanlines.add(c16 & 0xff);
            }
          }
        }
      }
      final adam7Png = _concatBytes([
        sig,
        _pngChunk('IHDR', ihdrAdam7),
        _pngChunk('IDAT', const ZLibEncoder().encode(passScanlines)),
        _pngChunk('IEND', const <int>[]),
      ]);
      final decAdam7 = decodePngRgba(adam7Png);
      expect(decAdam7.width, equals(3));
      expect(decAdam7.height, equals(3));
      expect(decAdam7.rgba, equals(expectedGrid));
    });

    test(
        'matches greyscale, alpha, achromatic target, and row-block chunking rules',
        () {
      final grey = Uint8List.fromList([
        0,
        0,
        0,
        0,
        128,
        128,
        128,
        64,
        255,
        255,
        255,
        255,
      ]);
      final shifted =
          colorizeImageU8(grey, 3, 1, (200, 90, 20), 0, keepAlpha: true);
      final tinted =
          colorizeImageU8(grey, 3, 1, (200, 90, 20), 1, keepAlpha: true);
      expect([shifted[3], shifted[7], shifted[11]], equals([0, 64, 255]));
      expect([tinted[3], tinted[7], tinted[11]], equals([0, 64, 255]));
      expect(shifted[0], greaterThan(0));

      final vivid = Uint8List.fromList([240, 30, 80, 180, 20, 210, 90, 255]);
      for (final ctype in [0, 1]) {
        final desat = colorizeImageU8(vivid, 2, 1, (140, 140, 140), ctype,
            keepAlpha: true);
        expect(desat[0], equals(desat[1]));
        expect(desat[1], equals(desat[2]));
        expect(desat[3], equals(180));
        expect(desat[4], equals(desat[5]));
        expect(desat[5], equals(desat[6]));
        expect(desat[7], equals(255));
      }

      final greyAvgChrom =
          Uint8List.fromList([255, 1, 1, 255, 1, 255, 255, 255]);
      final s0 = colorizeImageU8(greyAvgChrom, 2, 1, (140, 140, 140), 0,
          keepAlpha: false);
      final s1 = colorizeImageU8(greyAvgChrom, 2, 1, (140, 140, 140), 1,
          keepAlpha: false);
      const exp0 = <int>[255, 24, 24, 24, 255, 255];
      const exp1 = <int>[255, 24, 24, 255, 24, 24];
      for (var i = 0; i < 6; i++) {
        expect((s0[i] - exp0[i]).abs(), lessThanOrEqualTo(1));
        expect((s1[i] - exp1[i]).abs(), lessThanOrEqualTo(1));
      }

      final multiRow = Uint8List(19 * 37 * 4);
      var seed = 7;
      for (var i = 0; i < multiRow.length; i++) {
        seed = (seed * 1664525 + 1013904223) & 0xffffffff;
        multiRow[i] = seed & 0xff;
      }
      for (final ctype in [0, 1]) {
        final whole = colorizeImageU8(
          multiRow,
          19,
          37,
          (200, 90, 20),
          ctype,
          keepAlpha: true,
          maxBlockPixels: 100000,
        );
        final chunked = colorizeImageU8(
          multiRow,
          19,
          37,
          (200, 90, 20),
          ctype,
          keepAlpha: true,
          maxBlockPixels: 40,
        );
        expect(chunked, equals(whole));
      }
    });

    test(
        'strips EXIF APP1 on JPEGs, warns once without imageCodec, and bakes JPEGs at quality 95 with imageCodec',
        () {
      final baseRgb = <int>[200, 40, 40, 40, 40, 200];
      final cleanJpeg = _makeTestSof0Jpeg(2, 1, baseRgb);
      final exifJpeg = _injectExifOrientationApp1(cleanJpeg, 6);
      expect(_hasExifSignature(exifJpeg), isTrue);
      expect(stripJpegExifApp1(exifJpeg), equals(cleanJpeg));

      RawMaterial makeJpegMat(
        String name,
        (int, int, int) rgb,
        bool colorized,
        int colorizeType,
      ) =>
          RawMaterial(
            name: name,
            r: rgb.$1,
            g: rgb.$2,
            b: rgb.$3,
            a: 255,
            transparency: 1.0,
            colorized: colorized,
            colorizeType: colorizeType,
            texture: RawTexture(
              filename: 'exif6.jpg',
              xScale: 24.0,
              yScale: 12.0,
              data: exifJpeg,
            ),
          );

      final jpgParsed = RawParsed()
        ..version = '17.0.1'
        ..layerColors['Layer0'] = (200, 200, 200)
        ..layerIdToName[1] = 'Layer0'
        ..materials['PlainJpg'] =
            makeJpegMat('PlainJpg', (255, 255, 255), false, 0)
        ..materials['ShiftJpg'] =
            makeJpegMat('ShiftJpg', (200, 90, 20), true, 0)
        ..materials['TintJpg'] = makeJpegMat('TintJpg', (30, 160, 60), true, 1)
        ..materialIdToName[1] = 'PlainJpg'
        ..materialIdToName[2] = 'ShiftJpg'
        ..materialIdToName[3] = 'TintJpg'
        ..defsDict[1] = RawDefinition(
          guid: 'P1',
          name: 'P1',
          builder: _makeQuadBuilder(24.0, 1),
        )
        ..defsDict[2] = RawDefinition(
          guid: 'P2',
          name: 'P2',
          builder: _makeQuadBuilder(24.0, 2),
        )
        ..defsDict[3] = RawDefinition(
          guid: 'P3',
          name: 'P3',
          builder: _makeQuadBuilder(24.0, 3),
        );
      jpgParsed.root.builder.instances.addAll([
        GeometryBuilderInstance()
          ..refIdx = 1
          ..name = 'i1'
          ..matrix = _translationMatrix(0, 0, 0),
        GeometryBuilderInstance()
          ..refIdx = 2
          ..name = 'i2'
          ..matrix = _translationMatrix(30, 0, 0),
        GeometryBuilderInstance()
          ..refIdx = 3
          ..name = 'i3'
          ..matrix = _translationMatrix(60, 0, 0),
      ]);

      final scene = SceneBuilder.build(jpgParsed);
      final instanced = InstancedSceneBuilder.build(jpgParsed);
      expect(scene.textures, hasLength(1));
      expect(scene.textures[0].data, equals(exifJpeg));

      // 1) Without imageCodec: 1 warning, EXIF APP1 stripped without re-encoding, tinted factors kept.
      for (final isInst in [false, true]) {
        final warnings = <String>[];
        void onLog(SkpLogLevel lvl, String msg) {
          if (lvl == SkpLogLevel.warning) warnings.add(msg);
        }

        final glb = isInst
            ? toInstancedGlb(instanced, textures: true, onLog: onLog)
            : toGlb(scene, textures: true, onLog: onLog);
        expect(warnings, hasLength(1));
        expect(warnings.single, contains('imageCodec'));

        final parsed = _parseGlb(glb);
        expect(parsed.json['images'], hasLength(1));
        expect(
          ((parsed.json['images'] as List)[0]
              as Map<String, dynamic>)['mimeType'],
          equals('image/jpeg'),
        );
        final imgBytes = _extractGlbImageBytes(parsed.json, parsed.binary, 0);
        expect(_hasExifSignature(imgBytes), isFalse);
        expect(imgBytes, equals(cleanJpeg));

        final mats =
            (parsed.json['materials'] as List).cast<Map<String, dynamic>>();
        final shiftMat = mats.firstWhere(
          (m) =>
              m.containsKey('extras') &&
              ((m['extras'] as Map)['sketchup'] as Map)['colorize_type'] == 0,
        );
        final tintMat = mats.firstWhere(
          (m) =>
              m.containsKey('extras') &&
              ((m['extras'] as Map)['sketchup'] as Map)['colorize_type'] == 1,
        );
        expect(
          (shiftMat['pbrMetallicRoughness'] as Map)['baseColorFactor'],
          equals([200 / 255, 90 / 255, 20 / 255, 1.0]),
        );
        expect(
          (tintMat['pbrMetallicRoughness'] as Map)['baseColorFactor'],
          equals([30 / 255, 160 / 255, 60 / 255, 1.0]),
        );
      }

      // 2) With imageCodec: decoded once, baked at quality 95 once per colour, white factor, EXIF stripped on plain.
      for (final isInst in [false, true]) {
        var decodes = 0;
        final encodes = <(int, String)>[];
        final codec = _createTestJpegCodec(
          onDecode: () => decodes++,
          onEncode: (q, mt) => encodes.add((q, mt)),
        );
        final warnings = <String>[];
        void onLog(SkpLogLevel lvl, String msg) {
          if (lvl == SkpLogLevel.warning) warnings.add(msg);
        }

        final glb = isInst
            ? toInstancedGlb(
                instanced,
                textures: true,
                imageCodec: codec,
                onLog: onLog,
              )
            : toGlb(
                scene,
                textures: true,
                imageCodec: codec,
                onLog: onLog,
              );
        expect(warnings, isEmpty);
        expect(decodes, equals(1));
        expect(
          encodes,
          equals([(95, 'image/jpeg'), (95, 'image/jpeg')]),
        );

        final parsed = _parseGlb(glb);
        expect(parsed.json['images'], hasLength(3));
        final seenPixels = <String>{};
        for (final rawMat in (parsed.json['materials'] as List)) {
          final mat = rawMat as Map<String, dynamic>;
          final pbr = mat['pbrMetallicRoughness'] as Map<String, dynamic>;
          final texRef = pbr['baseColorTexture'] as Map<String, dynamic>?;
          if (texRef == null) continue;
          expect(pbr['baseColorFactor'], equals([1.0, 1.0, 1.0, 1.0]));
          final imgIdx =
              ((parsed.json['textures'] as List)[texRef['index'] as int]
                  as Map<String, dynamic>)['source'] as int;
          expect(
            ((parsed.json['images'] as List)[imgIdx]
                as Map<String, dynamic>)['mimeType'],
            equals('image/jpeg'),
          );
          final blob =
              _extractGlbImageBytes(parsed.json, parsed.binary, imgIdx);
          expect(_hasExifSignature(blob), isFalse);
          final dec = codec.decode(blob, 'image/jpeg')!;
          seenPixels.add(
            '${dec.rgba[0]},${dec.rgba[1]},${dec.rgba[2]}|${dec.rgba[4]},${dec.rgba[5]},${dec.rgba[6]}',
          );
        }
        expect(
          seenPixels,
          equals({
            '200,40,40|40,40,200',
            '32,237,80|32,237,80',
            '172,253,46|253,46,172',
          }),
        );
      }
    });

    test(
        'rejects oversize PNG and JPEG headers (> 178,956,970 pixels) and corrupt images before decoding',
        () {
      final oversizePng = _makeDeclaredSizePng(40000, 40000);
      expect(oversizePng.length, lessThan(100));

      final matShift = <String, dynamic>{
        'name': 'OversizeShift',
        'pbrMetallicRoughness': <String, dynamic>{
          'baseColorFactor': <double>[200 / 255, 90 / 255, 20 / 255, 1.0],
          'baseColorTexture': <String, dynamic>{'index': 0},
          'metallicFactor': 0.0,
          'roughnessFactor': 0.8,
        },
        'extras': <String, dynamic>{
          'sketchup': <String, dynamic>{'colorized': true, 'colorize_type': 0},
        },
      };
      final matTint = <String, dynamic>{
        'name': 'OversizeTint',
        'pbrMetallicRoughness': <String, dynamic>{
          'baseColorFactor': <double>[30 / 255, 160 / 255, 60 / 255, 0.5],
          'baseColorTexture': <String, dynamic>{'index': 0},
          'metallicFactor': 0.0,
          'roughnessFactor': 0.8,
        },
        'extras': <String, dynamic>{
          'sketchup': <String, dynamic>{'colorized': true, 'colorize_type': 1},
        },
      };

      final prim0 = GlbPrimitive(
        positions: [0, 0, 0, 1, 0, 0, 0, 1, 0],
        normals: [0, 0, 1, 0, 0, 1, 0, 0, 1],
        uvs: [0, 0, 1, 0, 0, 1],
        indices: [0, 1, 2],
        materialIndex: 0,
        geomName: 'tri0',
      );
      final prim1 = GlbPrimitive(
        positions: prim0.positions,
        normals: prim0.normals,
        uvs: prim0.uvs,
        indices: prim0.indices,
        materialIndex: 1,
        geomName: 'tri1',
      );

      final baked = Scene(
        sceneHierarchy: InstanceNode(),
        meshIndex: {},
        gltfMaterials: [matShift, matTint],
        textures: [
          SceneTexture(
            data: oversizePng,
            mimeType: 'image/png',
            filename: 'oversize.png',
          ),
        ],
        glbPrimitives: [prim0, prim1],
      );

      final inst = InstancedScene(
        sceneHierarchy: InstancedNode(name: 'Root', meshResourceId: 'm0'),
        meshResources: [
          InstancedMeshResource(
            id: 'm0',
            definitionId: 1,
            definitionName: 'Def',
            variantKey: 'v0',
            primitives: [
              LocalPrimitive(
                positions: prim0.positions,
                normals: prim0.normals,
                uvs: prim0.uvs,
                indices: prim0.indices,
                materialIndex: 0,
              ),
              LocalPrimitive(
                positions: prim0.positions,
                normals: prim0.normals,
                uvs: prim0.uvs,
                indices: prim0.indices,
                materialIndex: 1,
              ),
            ],
          ),
        ],
        gltfMaterials: [matShift, matTint],
        textures: [
          SceneTexture(
            data: oversizePng,
            mimeType: 'image/png',
            filename: 'oversize.png',
          ),
        ],
      );

      for (final isInst in [false, true]) {
        final warnings = <String>[];
        void onLog(SkpLogLevel lvl, String msg) {
          if (lvl == SkpLogLevel.warning) warnings.add(msg);
        }

        final glb = isInst
            ? toInstancedGlb(inst, textures: true, onLog: onLog)
            : toGlb(baked, textures: true, onLog: onLog);
        expect(warnings, hasLength(1));
        expect(warnings.single, contains('40000x40000'));
        expect(warnings.single, contains('1600000000'));
        expect(warnings.single, contains('178956970'));

        final parsed = _parseGlb(glb);
        expect(parsed.json['images'], hasLength(1));
        expect(
          _extractGlbImageBytes(parsed.json, parsed.binary, 0),
          equals(oversizePng),
        );
        final mats =
            (parsed.json['materials'] as List).cast<Map<String, dynamic>>();
        expect(
          (mats[0]['pbrMetallicRoughness'] as Map)['baseColorFactor'],
          equals([200 / 255, 90 / 255, 20 / 255, 1.0]),
        );
        expect(
          (mats[1]['pbrMetallicRoughness'] as Map)['baseColorFactor'],
          equals([30 / 255, 160 / 255, 60 / 255, 0.5]),
        );
      }

      // 2) Oversize JPEG header (50000x50000 = 2.5B pixels > 2^31 - 1) never calls imageCodec.decode
      final oversizeJpg =
          _makeTestSof0Jpeg(50000, 50000, const <int>[10, 20, 30]);
      var jpegDecodeCalled = 0;
      final spyCodec = SkpImageCodec(
        decode: (_, __) {
          jpegDecodeCalled++;
          return null;
        },
        encode: (_, __, ___, ____, {int quality = 95}) => Uint8List(0),
      );
      final jpgWarnings = <String>[];
      final jpgScene = Scene(
        sceneHierarchy: InstanceNode(),
        meshIndex: {},
        gltfMaterials: baked.gltfMaterials,
        glbPrimitives: baked.glbPrimitives,
        textures: [
          SceneTexture(
            data: oversizeJpg,
            mimeType: 'image/jpeg',
            filename: 'huge.jpg',
          ),
        ],
      );
      final jpgGlb = toGlb(
        jpgScene,
        textures: true,
        imageCodec: spyCodec,
        onLog: (lvl, msg) {
          if (lvl == SkpLogLevel.warning) jpgWarnings.add(msg);
        },
      );
      expect(jpegDecodeCalled, equals(0));
      expect(jpgWarnings, hasLength(1));
      expect(jpgWarnings.single, contains('50000x50000'));
      expect(jpgWarnings.single, contains('2500000000'));
      expect(jpgWarnings.single, contains('178956970'));
      final jpgParsedGlb = _parseGlb(jpgGlb);
      expect(
        _extractGlbImageBytes(jpgParsedGlb.json, jpgParsedGlb.binary, 0),
        equals(oversizeJpg),
      );

      // 3) Truncated 8-byte PNG keeps source bytes, keeps tinted factor, warns once, prints nothing
      final truncatedPng =
          Uint8List.fromList([137, 80, 78, 71, 13, 10, 26, 10]);
      final corruptScene = Scene(
        sceneHierarchy: InstanceNode(),
        meshIndex: {},
        gltfMaterials: [matShift],
        glbPrimitives: [prim0],
        textures: [
          SceneTexture(
            data: truncatedPng,
            mimeType: 'image/png',
            filename: 'truncated.png',
          ),
        ],
      );
      final printed = <String>[];
      runZoned(
        () => toGlb(corruptScene, textures: true),
        zoneSpecification: ZoneSpecification(
          print: (_, __, ___, line) => printed.add(line),
        ),
      );
      expect(printed, isEmpty);

      final corruptWarnings = <String>[];
      final corruptGlb = toGlb(
        corruptScene,
        textures: true,
        onLog: (lvl, msg) {
          if (lvl == SkpLogLevel.warning) corruptWarnings.add(msg);
        },
      );
      expect(corruptWarnings, hasLength(1));
      final cParsed = _parseGlb(corruptGlb);
      expect(
        _extractGlbImageBytes(cParsed.json, cParsed.binary, 0),
        equals(truncatedPng),
      );
      expect(
        (((cParsed.json['materials'] as List)[0]
                as Map<String, dynamic>)['pbrMetallicRoughness']
            as Map)['baseColorFactor'],
        equals([200 / 255, 90 / 255, 20 / 255, 1.0]),
      );
    });
  });
}
