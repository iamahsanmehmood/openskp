import 'dart:typed_data';
import 'package:openskp/openskp.dart';
import 'package:test/test.dart';

typedef _Tri = (
  (double, double, double),
  (double, double, double),
  (double, double, double)
);

const List<List<(double, double, double)>> _boxFaces = [
  [(0.0, 0.0, 0.0), (0.0, 10.0, 0.0), (10.0, 10.0, 0.0), (10.0, 0.0, 0.0)],
  [(0.0, 0.0, 10.0), (10.0, 0.0, 10.0), (10.0, 10.0, 10.0), (0.0, 10.0, 10.0)],
  [(0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (10.0, 0.0, 10.0), (0.0, 0.0, 10.0)],
  [(10.0, 0.0, 0.0), (10.0, 10.0, 0.0), (10.0, 10.0, 10.0), (10.0, 0.0, 10.0)],
  [(10.0, 10.0, 0.0), (0.0, 10.0, 0.0), (0.0, 10.0, 10.0), (10.0, 10.0, 10.0)],
  [(0.0, 10.0, 0.0), (0.0, 0.0, 0.0), (0.0, 0.0, 10.0), (0.0, 10.0, 10.0)],
];

Uint8List _buildBoxBytes(String paint) {
  final builder = create();
  final red = builder.addMaterial('Red', [255, 0, 0, 255]);
  final blue = builder.addMaterial('Blue', [0, 0, 255, 255]);
  for (var i = 0; i < _boxFaces.length; i++) {
    int? front;
    int? back;
    if (paint == 'one_face') {
      if (i == 0) front = red;
    } else {
      if (paint == 'front' || paint == 'both' || paint == 'mixed') front = red;
      if (paint == 'back' || paint == 'both' || paint == 'mixed') back = blue;
      if (paint == 'mixed' && i.isOdd) {
        final tmp = front;
        front = back;
        back = tmp;
      }
    }
    builder.addFace(_boxFaces[i], material: front, backMaterial: back);
  }
  return builder.toBytes();
}

List<_Tri> _parseAsciiStl(String text) {
  final verts = <(double, double, double)>[];
  for (final line in text.split('\n')) {
    final trimmed = line.trim();
    if (trimmed.startsWith('vertex ')) {
      final parts = trimmed.split(RegExp(r'\s+'));
      verts.add((
        double.parse(parts[1]),
        double.parse(parts[2]),
        double.parse(parts[3])
      ));
    }
  }
  final tris = <_Tri>[];
  for (var i = 0; i + 2 < verts.length; i += 3) {
    tris.add((verts[i], verts[i + 1], verts[i + 2]));
  }
  return tris;
}

List<_Tri> _parseBinaryStl(Uint8List data) {
  final view = ByteData.sublistView(data);
  final count = view.getUint32(80, Endian.little);
  expect(data.length, equals(84 + count * 50));
  final tris = <_Tri>[];
  for (var i = 0; i < count; i++) {
    final base = 84 + i * 50 + 12;
    tris.add((
      (
        view.getFloat32(base, Endian.little),
        view.getFloat32(base + 4, Endian.little),
        view.getFloat32(base + 8, Endian.little),
      ),
      (
        view.getFloat32(base + 12, Endian.little),
        view.getFloat32(base + 16, Endian.little),
        view.getFloat32(base + 20, Endian.little),
      ),
      (
        view.getFloat32(base + 24, Endian.little),
        view.getFloat32(base + 28, Endian.little),
        view.getFloat32(base + 32, Endian.little),
      ),
    ));
  }
  return tris;
}

void _assertClosed10InBox(List<_Tri> tris) {
  expect(tris, hasLength(12));
  String vKey((double, double, double) p) =>
      '${(p.$1 * 1e6).round()},${(p.$2 * 1e6).round()},${(p.$3 * 1e6).round()}';
  final edgeCounts = <String, int>{};
  var volM3 = 0.0;
  for (final (v0, v1, v2) in tris) {
    final cx = v1.$2 * v2.$3 - v1.$3 * v2.$2;
    final cy = v1.$3 * v2.$1 - v1.$1 * v2.$3;
    final cz = v1.$1 * v2.$2 - v1.$2 * v2.$1;
    volM3 += (v0.$1 * cx + v0.$2 * cy + v0.$3 * cz) / 6.0;

    final keys = [vKey(v0), vKey(v1), vKey(v2)];
    for (var e = 0; e < 3; e++) {
      final a = keys[e];
      final b = keys[(e + 1) % 3];
      final ek = a.compareTo(b) < 0 ? '$a|$b' : '$b|$a';
      edgeCounts[ek] = (edgeCounts[ek] ?? 0) + 1;
    }
  }
  final volCm3 = volM3 * 1e6;
  expect((volCm3 * 10).round() / 10, equals(16387.1));
  expect(volCm3, closeTo(16387.064, 0.05));
  expect(edgeCounts, hasLength(18));
  expect(edgeCounts.values.every((c) => c == 2), isTrue);
}

void main() {
  group('STL Exporter (ASCII & Binary)', () {
    test('serializes Scene to ASCII STL text format', () {
      final scene = Scene(
        sceneHierarchy: InstanceNode(
          name: 'Root',
          definitionName: 'RootDef',
          layer: 'Layer0',
          positionMm: (0.0, 0.0, 0.0),
          properties: {},
          children: [],
        ),
        meshIndex: {},
        glbPrimitives: [
          GlbPrimitive(
            geomName: 'Box',
            materialIndex: 0,
            positions: Float32List.fromList([0, 0, 0, 1, 0, 0, 0, 1, 0]),
            normals: Float32List.fromList([0, 0, 1, 0, 0, 1, 0, 0, 1]),
            uvs: Float32List.fromList([0, 0, 1, 0, 0, 1]),
            indices: Uint32List.fromList([0, 1, 2]),
          ),
        ],
        gltfMaterials: [],
      );

      final stlText = toStlAscii(scene);
      expect(stlText, contains('solid OpenSKP_Model'));
      expect(stlText, contains('facet normal 0.000000 0.000000 1.000000'));
      expect(stlText, contains('vertex 0.000000 0.000000 0.000000'));
      expect(stlText, contains('endsolid OpenSKP_Model'));
    });

    test('serializes Scene to Binary STL byte array', () {
      final scene = Scene(
        sceneHierarchy: InstanceNode(
          name: 'Root',
          definitionName: 'RootDef',
          layer: 'Layer0',
          positionMm: (0.0, 0.0, 0.0),
          properties: {},
          children: [],
        ),
        meshIndex: {},
        glbPrimitives: [
          GlbPrimitive(
            geomName: 'Box',
            materialIndex: 0,
            positions: Float32List.fromList([0, 0, 0, 1, 0, 0, 0, 1, 0]),
            normals: Float32List.fromList([0, 0, 1, 0, 0, 1, 0, 0, 1]),
            uvs: Float32List.fromList([0, 0, 1, 0, 0, 1]),
            indices: Uint32List.fromList([0, 1, 2]),
          ),
        ],
        gltfMaterials: [],
      );

      final data = toStlBinary(scene);
      expect(data.length,
          equals(80 + 4 + 50)); // Header + uint32 count + 1 triangle
    });

    for (final paint in [
      'unpainted',
      'one_face',
      'front',
      'back',
      'both',
      'mixed'
    ]) {
      for (final binary in [false, true]) {
        test(
            'omits backTriangles on 10 in $paint box (${binary ? "binary" : "ascii"} STL, #601)',
            () {
          final scene = SkpFile.fromBuffer(_buildBoxBytes(paint)).buildScene();
          final tris = binary
              ? _parseBinaryStl(toStlBinary(scene))
              : _parseAsciiStl(toStlAscii(scene));
          _assertClosed10InBox(tris);
        });
      }
    }

    test(
        'records backTriangles in instanced scenes and leaves colour exports unchanged (#601)',
        () {
      final skp = SkpFile.fromBuffer(_buildBoxBytes('both'));
      final baked = skp.buildScene();
      final instanced = skp.buildInstancedScene();

      final totalTris =
          baked.glbPrimitives.fold<int>(0, (s, p) => s + p.indices.length ~/ 3);
      final bakedBack = baked.glbPrimitives
          .fold<int>(0, (s, p) => s + p.backTriangles.length);
      final instBack = instanced.meshResources.first.primitives
          .fold<int>(0, (s, p) => s + p.backTriangles.length);
      expect(totalTris, equals(24));
      expect(bakedBack, equals(12));
      expect(instBack, equals(12));

      final ply = toPlyAscii(baked);
      expect(ply, contains('element face 24'));
      expect(ply, contains('255 0 0 255'));
      expect(ply, contains('0 0 255 255'));
    });
  });
}
