import 'dart:typed_data';
import 'package:openskp/openskp.dart';
import 'package:test/test.dart';

Scene _makeScene(List<GlbPrimitive> prims) {
  return Scene(
    sceneHierarchy: InstanceNode(
      name: 'Root',
      definitionName: 'RootDef',
      layer: 'Layer0',
      positionMm: (0.0, 0.0, 0.0),
      properties: {},
      children: [],
    ),
    meshIndex: {},
    glbPrimitives: prims,
    gltfMaterials: [],
  );
}

GlbPrimitive _triangle(String name) {
  return GlbPrimitive(
    geomName: name,
    materialIndex: 0,
    positions: Float32List.fromList([0, 0, 0, 1, 0, 0, 0, 1, 0]),
    normals: Float32List.fromList([0, 0, 1, 0, 0, 1, 0, 0, 1]),
    uvs: Float32List.fromList([0, 0, 1, 0, 0, 1]),
    indices: Uint32List.fromList([0, 1, 2]),
  );
}

int _crc32Ascii(String text) {
  int crc = 0xFFFFFFFF;
  for (final code in text.codeUnits) {
    crc ^= code & 0xFF;
    for (int bit = 0; bit < 8; bit++) {
      final lsb = crc & 1;
      crc = (crc >>> 1) ^ (lsb != 0 ? 0xEDB88320 : 0);
    }
  }
  return (~crc) & 0xFFFFFFFF;
}

String _surrogateEscape(List<int> raw) {
  return String.fromCharCodes(
    raw.map((b) => b < 0x80 ? b : (0xDC00 | b)),
  );
}

(Set<String>, List<String>) _inspect(String dxf) {
  for (final c in dxf.codeUnits) {
    expect(c, lessThan(128));
  }
  expect(dxf.endsWith('\r\n'), isTrue);
  final lines = dxf.substring(0, dxf.length - 2).split('\r\n');
  expect(lines.length % 2, equals(0));

  final layers = <String>{};
  final meshLayers = <String>[];
  final references = <String>[];
  String record = '';
  int tableCount = 0;
  bool layerTable = false;
  bool entity = false;

  for (int k = 0; k < lines.length; k += 2) {
    final group = int.parse(lines[k].trim());
    final value = lines[k + 1];
    if (group >= 60 && group <= 79) {
      final iv = int.parse(value);
      expect(iv, inInclusiveRange(-32768, 32767));
    }
    if (group == 0) {
      record = value;
      entity = false;
    }
    if (group == 100 && value == 'AcDbEntity') entity = true;
    if (record == 'TABLE' && group == 2) layerTable = value == 'LAYER';
    if (record == 'TABLE' && layerTable && group == 70) {
      tableCount = int.parse(value);
    }
    if (record == 'LAYER' && group == 2) {
      expect(layers.add(value), isTrue, reason: 'duplicate LAYER $value');
      expect(value.length, lessThanOrEqualTo(2049));
      final decoded = <int>[];
      for (int i = 0; i < value.length;) {
        if (value.codeUnitAt(i) == 0x5C) {
          expect(i + 7 <= value.length && value.substring(i, i + 3) == '\\U+', isTrue);
          decoded.add(int.parse(value.substring(i + 3, i + 7), radix: 16));
          i += 7;
        } else {
          final c = value.codeUnitAt(i++);
          expect(c >= 32 && c != 127, isTrue);
          decoded.add(c);
        }
      }
      expect(decoded.length, inInclusiveRange(1, 255));
      for (int i = 0; i < decoded.length; i++) {
        if (decoded[i] >= 0xD800 && decoded[i] <= 0xDBFF) {
          i++;
          expect(i < decoded.length && decoded[i] >= 0xDC00 && decoded[i] <= 0xDFFF, isTrue);
        } else {
          expect(decoded[i] < 0xDC00 || decoded[i] > 0xDFFF, isTrue);
        }
      }
    }
    if (group == 8 && entity) {
      references.add(value);
      if (record == 'POLYLINE' || record == '3DFACE') {
        meshLayers.add(value);
      }
    }
  }
  expect(tableCount, equals(layers.length));
  for (final r in references) {
    expect(layers.contains(r), isTrue);
  }
  return (layers, meshLayers);
}

void main() {
  group('DXF 3D Exporter', () {
    test('serializes Scene to 3D DXF text format', () {
      final scene = _makeScene([_triangle('Walls')]);

      final dxfText = toDxf(scene);
      expect(dxfText, contains('\$ACADVER'));
      expect(dxfText, contains('AC1015'));
      expect(dxfText, contains('POLYLINE'));
      expect(dxfText, contains('AcDbPolyFaceMesh'));
      expect(dxfText, contains('Walls'));
      expect(dxfText, contains('EOF'));

      final dxf3d = toDxf(scene, mode: '3dface');
      expect(dxf3d, contains('3DFACE'));
      expect(dxf3d, contains('AcDbFace'));
    });

    test('escapes Unicode layer names, deduplicates case-insensitively, and matches C++ reference bytes', () {
      const chinese = 'mesh_27_ROOT__\u4e0a\u67dc(\u4fa7\u5305\u9876\u5e95)__\u9876\u677f_\u540a\u67dc';
      const escaped =
          'mesh_27_ROOT__\\U+4E0A\\U+67DC(\\U+4FA7\\U+5305\\U+9876\\U+5E95)__\\U+9876\\U+677F_\\U+540A\\U+67DC';
      final longName = '\u67dc' * 260;
      final names = <String>[
        chinese,
        'Caf\u00e9',
        '\u67dc\u{1f600}',
        '${longName}A',
        '${longName}B',
        '${'x' * 245}\u{1f600}${'x' * 20}',
        'A/B',
        'A:B',
        'walls',
        'WALLS',
        '0',
        'Defpoints',
        '',
        '\t ',
        'line\nbreak\tname',
        chinese,
        'y' * 300,
      ];
      final scene = _makeScene(names.map(_triangle).toList());
      for (final mode in ['polyface', '3dface']) {
        final (layers, meshLayers) = _inspect(toDxf(scene, scale: 1.0, mode: mode));
        expect(layers.contains(escaped), isTrue);
        expect(layers.contains('Caf\\U+00E9'), isTrue);
        expect(layers.contains('\\U+67DC\\U+D83D\\U+DE00'), isTrue);
        expect(meshLayers.length, equals(names.length));
        expect(meshLayers[0], equals(meshLayers[15]));
        final unique = <String>[
          for (int i = 0; i < meshLayers.length; i++)
            if (i != 15) meshLayers[i],
        ];
        expect(unique.map((u) => u.toLowerCase()).toSet().length, equals(unique.length));
      }

      final polyDxf = toDxf(scene, scale: 1.0, mode: 'polyface');
      expect(polyDxf.length, equals(62908));
      expect(_crc32Ascii(polyDxf), equals(0x561316A9));

      final faceDxf = toDxf(scene, scale: 1.0, mode: '3dface');
      expect(faceDxf.length, equals(31833));
      expect(_crc32Ascii(faceDxf), equals(0x89DCD865));

      final emptyDxf = toDxf(_makeScene([]), scale: metresToInches, mode: 'polyface');
      expect(emptyDxf.length, equals(16676));
      expect(_crc32Ascii(emptyDxf), equals(0xFDEAA351));
    });

    test('falls back to 3DFACE past polyface 16-bit limits and rejects malformed meshes', () {
      final largePos = Float32List(32768 * 3);
      largePos[3] = 1.0;
      largePos[7] = 1.0;
      final large = GlbPrimitive(
        geomName: 'Large mesh',
        materialIndex: 0,
        positions: largePos,
        normals: Float32List(32768 * 3),
        uvs: Float32List(32768 * 2),
        indices: Uint32List.fromList([0, 1, 32767]),
      );
      final manyIdx = Uint32List(32768 * 3);
      for (int i = 0; i < 32768; i++) {
        manyIdx[i * 3] = 0;
        manyIdx[i * 3 + 1] = 1;
        manyIdx[i * 3 + 2] = 2;
      }
      final many = GlbPrimitive(
        geomName: 'Many faces',
        materialIndex: 0,
        positions: Float32List.fromList([0, 0, 0, 1, 0, 0, 0, 1, 0]),
        normals: Float32List(9),
        uvs: Float32List(6),
        indices: manyIdx,
      );
      final limitDxf = toDxf(_makeScene([large, many, _triangle('Fits')]), scale: 1.0, mode: 'polyface');
      final (_, limitMeshLayers) = _inspect(limitDxf);
      expect(limitMeshLayers.length, equals(1 + 32768 + 1));
      expect('\r\nPOLYLINE\r\n'.allMatches(limitDxf).length, equals(1));
      expect(limitDxf.length, equals(8863208));
      expect(_crc32Ascii(limitDxf), equals(0x1445729A));

      for (int failure = 0; failure < 5; failure++) {
        var pos = Float32List.fromList([0, 0, 0, 1, 0, 0, 0, 1, 0]);
        var idx = Uint32List.fromList([0, 1, 2]);
        if (failure == 0) pos = Float32List.fromList([0, 0, 0, 1, 0, 0, 0, 1]);
        if (failure == 1) idx = Uint32List.fromList([0, 1]);
        if (failure == 2) idx = Uint32List.fromList([3, 1, 2]);
        if (failure == 3) pos[0] = double.nan;
        final scale = failure == 4 ? double.infinity : 1.0;
        final bad = GlbPrimitive(
          geomName: 'Invalid mesh',
          materialIndex: 0,
          positions: pos,
          normals: Float32List(9),
          uvs: Float32List(6),
          indices: idx,
        );
        for (final mode in ['polyface', '3dface']) {
          expect(() => toDxf(_makeScene([bad]), scale: scale, mode: mode), throwsArgumentError);
        }
      }

      final cases = <(String, String)>[
        (_surrogateEscape([0x80]), '_'),
        (_surrogateEscape([0xFF]), '_'),
        (_surrogateEscape([0xC0, 0xAF]), '__'),
        (_surrogateEscape([0xE0, 0x80, 0xAF]), '_'),
        (_surrogateEscape([0xF0, 0x80, 0x80, 0xAF]), '_'),
        (_surrogateEscape([0xE4, 0x6F, 0x6B]), '_ok'),
        ('${String.fromCharCode(0xD800)}ok', '_ok'),
        (_surrogateEscape([0x80, 0xF0, 0x9F, 0x98, 0x80]), '_\\U+D83D\\U+DE00'),
      ];
      for (final (bad, replacement) in cases) {
        final name = 'before${bad}after';
        final scene = _makeScene([_triangle(name), _triangle('unaffected'), _triangle(name)]);
        final (_, meshLayers) = _inspect(toDxf(scene, scale: 1.0, mode: 'polyface'));
        expect(meshLayers, equals(['before${replacement}after', 'unaffected', 'before${replacement}after']));
      }
    });
  });
}
