import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:openskp/openskp.dart';
import 'package:test/test.dart';

/// Instanced scene building and export (openskp#200, ported from
/// TypeScript's buildInstancedScene()/toInstancedGLB()).
///
/// The strongest correctness evidence available: run BOTH builders over
/// the repository's real .skp fixtures and require that flattening the
/// instanced result reproduces the baked builder's world-space triangles
/// exactly. This covers, on genuine files, everything a synthetic test
/// would cover piecewise - nested groups/components, instance-painted
/// materials, layers, front/back materials, textures, holes, mirrored
/// transforms - because whatever those files happen to contain has to
/// come out the same either way.

// One modern VFF container plus two legacy MFC ones, so both parse paths
// feed the instanced builder here too.
const _fixtures = [
  'SU_File.skp',
  'Untitled.skp',
  'capilla_quiroz_v17.skp',
  'gondola_v20.skp',
  'single_material_v17.skp',
];

// Float32 round-off only, same tolerance and justification as the
// TypeScript reference: the baked path transforms in float64 then stores
// the world-space result as float32; the instanced path stores the
// local-space value as float32 and transforms afterwards. Both are
// single-rounding-step correct, but round at different moments, so a
// coordinate can land one float32 ulp apart between them. (Dart's own
// paths store both in native double precision, so measured deltas here
// are far below this.)
const _tolerance = 1e-5;

const _identity4 = <double>[
  1.0, 0.0, 0.0, 0.0,
  0.0, 1.0, 0.0, 0.0,
  0.0, 0.0, 1.0, 0.0,
  0.0, 0.0, 0.0, 1.0,
];

List<double> _mul4(List<double> a, List<double> b) {
  final out = List<double>.filled(16, 0.0);
  for (var col = 0; col < 4; col++) {
    for (var row = 0; row < 4; row++) {
      var s = 0.0;
      for (var k = 0; k < 4; k++) s += a[k * 4 + row] * b[col * 4 + k];
      out[col * 4 + row] = s;
    }
  }
  return out;
}

(double, double, double) _applyMatrix(List<double> m, (double, double, double) p) {
  final (x, y, z) = p;
  return (
    m[0] * x + m[4] * y + m[8] * z + m[12],
    m[1] * x + m[5] * y + m[9] * z + m[13],
    m[2] * x + m[6] * y + m[10] * z + m[14],
  );
}

typedef _FlatTri = ((double, double, double), (double, double, double), (double, double, double), int);

/// Walk the instanced tree, composing node transforms, and emit every
/// triangle in world space - i.e. reconstruct what SkpFile.buildScene()
/// bakes. Test-only: the whole point of the instanced output is to avoid
/// materialising this.
List<_FlatTri> _flattenInstanced(InstancedScene scene) {
  final byId = {for (final r in scene.meshResources) r.id: r};
  final out = <_FlatTri>[];

  void visit(InstancedNode node, List<double> parent) {
    final world = _mul4(parent, node.matrix);
    final resId = node.meshResourceId;
    if (resId != null && byId.containsKey(resId)) {
      final res = byId[resId]!;
      for (final prim in res.primitives) {
        for (var i = 0; i < prim.indices.length; i += 3) {
          final tri = <(double, double, double)>[];
          for (var k = 0; k < 3; k++) {
            final vi = prim.indices[i + k];
            tri.add(_applyMatrix(
                world, (prim.positions[vi * 3], prim.positions[vi * 3 + 1], prim.positions[vi * 3 + 2])));
          }
          out.add((tri[0], tri[1], tri[2], prim.materialIndex));
        }
      }
    }
    for (final child in node.children) visit(child, world);
  }

  visit(scene.sceneHierarchy, _identity4);
  return out;
}

List<_FlatTri> _flattenBaked(Scene scene) {
  final out = <_FlatTri>[];
  for (final prim in scene.glbPrimitives) {
    for (var i = 0; i < prim.indices.length; i += 3) {
      final tri = <(double, double, double)>[];
      for (var k = 0; k < 3; k++) {
        final vi = prim.indices[i + k];
        tri.add((prim.positions[vi * 3], prim.positions[vi * 3 + 1], prim.positions[vi * 3 + 2]));
      }
      out.add((tri[0], tri[1], tri[2], prim.materialIndex));
    }
  }
  return out;
}

int _instancedBufferBytes(InstancedScene scene) {
  var total = 0;
  for (final r in scene.meshResources) {
    for (final p in r.primitives) {
      total += p.positions.length * 4 + p.normals.length * 4 + p.uvs.length * 4 + p.indices.length * 4;
    }
  }
  return total;
}

int _bakedBufferBytes(Scene scene) {
  var total = 0;
  for (final p in scene.glbPrimitives) {
    total += p.positions.length * 4 + p.normals.length * 4 + p.uvs.length * 4 + p.indices.length * 4;
  }
  return total;
}

void _walkMetadataParity(InstanceNode baked, InstancedNode instanced) {
  expect(instanced.name, equals(baked.name));
  expect(instanced.definitionName, equals(baked.definitionName));
  expect(instanced.layer, equals(baked.layer));
  expect(instanced.positionMm, equals(baked.positionMm));
  expect(instanced.properties, equals(baked.properties));
  expect(instanced.children.length, equals(baked.children.length));
  for (var k = 0; k < baked.children.length; k++) {
    _walkMetadataParity(baked.children[k], instanced.children[k]);
  }
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
  group('instanced vs baked parity on real fixtures', () {
    for (final name in _fixtures) {
      final path = 'test/fixtures/$name';

      test('reproduces buildScene\'s world-space triangles for $name', () {
        final baked = SkpFile.open(path).buildScene();
        final instanced = SkpFile.open(path).buildInstancedScene();

        final bakedTris = _flattenBaked(baked);
        final instTris = _flattenInstanced(instanced);

        expect(instTris.length, equals(bakedTris.length));

        var worstDelta = 0.0;
        var materialMismatches = 0;
        final n = bakedTris.length < instTris.length ? bakedTris.length : instTris.length;
        for (var i = 0; i < n; i++) {
          final a = instTris[i];
          final e = bakedTris[i];
          if (jsonEncode(instanced.gltfMaterials[a.$4]) != jsonEncode(baked.gltfMaterials[e.$4])) {
            materialMismatches++;
          }
          for (final pair in [(a.$1, e.$1), (a.$2, e.$2), (a.$3, e.$3)]) {
            final (pa, pe) = pair;
            worstDelta = [worstDelta, (pa.$1 - pe.$1).abs(), (pa.$2 - pe.$2).abs(), (pa.$3 - pe.$3).abs()]
                .reduce((a, b) => a > b ? a : b);
          }
        }

        expect(materialMismatches, equals(0));
        expect(worstDelta, lessThan(_tolerance));
      });
    }

    test('never stores more geometry than the baked path', () {
      for (final name in _fixtures) {
        final path = 'test/fixtures/$name';
        final bakedBytes = _bakedBufferBytes(SkpFile.open(path).buildScene());
        final instBytes = _instancedBufferBytes(SkpFile.open(path).buildInstancedScene());
        // Equal when nothing repeats; strictly smaller once anything does.
        expect(instBytes, lessThanOrEqualTo(bakedBytes));
      }
    });

    test('resolves the same layers and dynamic properties per node', () {
      for (final name in _fixtures) {
        final path = 'test/fixtures/$name';
        final baked = SkpFile.open(path).buildScene();
        final instanced = SkpFile.open(path).buildInstancedScene();
        // Walk both trees in lockstep: the instance walk order is
        // identical, so a divergence in metadata shows up as a mismatch
        // here.
        _walkMetadataParity(baked.sceneHierarchy, instanced.sceneHierarchy);
      }
    });
  });

  group('instanced GLB export', () {
    const fixturePath = 'test/fixtures/capilla_quiroz_v17.skp';

    test('omits images by default', () {
      final scene = SkpFile.open(fixturePath).buildInstancedScene();
      final bytes = toInstancedGlb(scene);

      final str = latin1.decode(bytes, allowInvalid: true);
      expect(str.contains('"images"'), isFalse);
      expect(_containsBytes(bytes, _jpegMagic), isFalse);
    });

    test('embeds textures when asked', () {
      final scene = SkpFile.open(fixturePath).buildInstancedScene();
      final noTex = toInstancedGlb(scene);
      final withTex = toInstancedGlb(scene, textures: true);

      expect(withTex.length, greaterThan(noTex.length));
      expect(_containsBytes(withTex, _jpegMagic), isTrue);

      final parsed = _parseGlb(withTex);
      final images = parsed.json['images'] as List;
      expect(images.length, equals(3));
      for (final img in images) {
        expect(img['bufferView'], isNotNull);
        expect((img['mimeType'] as String).startsWith('image/'), isTrue);
      }
    });

    test('is smaller than the baked export on a file with repeated geometry', () {
      const gondolaPath = 'test/fixtures/gondola_v20.skp';
      final baked = toGlb(SkpFile.open(gondolaPath).buildScene());
      final instanced = toInstancedGlb(SkpFile.open(gondolaPath).buildInstancedScene());
      expect(instanced.length, lessThan(baked.length));
    });

    test('exportInstancedGlb file writes embedded textures', () {
      final scene = SkpFile.open(fixturePath).buildInstancedScene();
      final tmp = File('${Directory.systemTemp.path}/openskp_instanced_glbtex_test.glb');
      try {
        exportInstancedGlb(scene, tmp.path, textures: true);
        final bytes = tmp.readAsBytesSync();
        expect(_containsBytes(bytes, _jpegMagic), isTrue);
      } finally {
        if (tmp.existsSync()) tmp.deleteSync();
      }
    });
  });
}
