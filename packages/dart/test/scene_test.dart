import 'dart:io';

import 'package:openskp/openskp.dart';
import 'package:test/test.dart';

/// Real-file regression test for SkpFile.buildScene() - the opt-in
/// scene-hierarchy + triangulation + GLB mesh capability, ported from the
/// TypeScript reference implementation.
///
/// Root instance count is cross-validated directly against Python's and
/// TypeScript's build_scene()/buildScene() on this exact fixture.
/// Mesh/gltfMaterials counts (21/21/13) instead match C++'s
/// independently-verified reference for this file - the correct counts
/// once faces with genuinely different front/back materials are split
/// into two single-sided primitives each, rather than the pre-fix
/// single-sided-only count (13/13/9). This fixture has 30 such faces
/// (confirmed by direct inspection), so the split isn't a rare edge case
/// here.
void main() {
  final fixturePath = '${Directory.current.path}/test/fixtures/capilla_quiroz_v17.skp';

  test('buildScene matches Python and TypeScript ground truth', () {
    final scene = SkpFile.open(fixturePath).buildScene();

    expect(scene.glbPrimitives.length, 21);
    expect(scene.meshIndex.length, 21);
    expect(scene.gltfMaterials.length, 13);

    expect(scene.sceneHierarchy.name, 'ROOT');
    expect(scene.sceneHierarchy.definitionName, 'ROOT_MODEL');
    expect(scene.sceneHierarchy.children.length, 3);
    final defNames = scene.sceneHierarchy.children.map((c) => c.definitionName).toList()..sort();
    expect(defNames, ['grada', 'grada', 'puerta']);
  });

  test('primitives have valid geometry', () {
    final scene = SkpFile.open(fixturePath).buildScene();
    for (final prim in scene.glbPrimitives) {
      expect(prim.positions.length % 3, 0);
      expect(prim.normals.length, prim.positions.length);
      final nVerts = prim.positions.length ~/ 3;
      expect(prim.uvs.length, nVerts * 2);
      for (final uv in prim.uvs) {
        expect(uv.isNaN, false);
        expect(uv.isFinite, true);
      }
      expect(prim.indices.length % 3, 0);
      for (final idx in prim.indices) {
        expect(idx, inInclusiveRange(0, nVerts - 1));
      }
      expect(prim.materialIndex, inInclusiveRange(0, scene.gltfMaterials.length - 1));
    }
  });

  test('buildScene is independent of parse', () {
    // buildScene() must not require parse() to have been called first -
    // it re-parses independently.
    final scene = SkpFile.open(fixturePath).buildScene();
    expect(scene.glbPrimitives.length, 21);
  });

  test('renders back-face materials correctly (item 14 regression)', () {
    // This fixture has 30 faces (e.g. faces 133/152 in the 'puerta'
    // definition) whose front and back materials resolve to genuinely
    // different colors. Verified directly: front material 29 is blue
    // (2, 0, 237), back material 27 is light blue (204, 235, 244).
    final scene = SkpFile.open(fixturePath).buildScene();

    bool hasColor(int r, int g, int b) {
      return scene.gltfMaterials.any((m) {
        final pbr = m['pbrMetallicRoughness'] as Map<String, dynamic>;
        final c = pbr['baseColorFactor'] as List;
        return ((c[0] as double) * 255).round() == r &&
            ((c[1] as double) * 255).round() == g &&
            ((c[2] as double) * 255).round() == b;
      });
    }

    expect(hasColor(2, 0, 237), true);
    expect(hasColor(204, 235, 244), true);

    final doubleSidedCount = scene.gltfMaterials.where((m) => m['doubleSided'] == true).length;
    expect(doubleSidedCount, 4);
  });

  test('translucent material gets BLEND alpha', () {
    // Round-tripped through the real writer and reader rather than a
    // hand-built fixture: addMaterial's 4th (alpha) channel is documented
    // to carry SketchUp's own opacity mechanism, so this exercises the
    // exact path a real .skp file with a translucent material takes.
    // Before this fix, baseColorFactor's alpha was hardcoded to 1.0 and no
    // glTF material ever declared alphaMode, so a conformant renderer
    // showed every material fully opaque regardless of the source file's
    // actual transparency.
    final builder = create();
    final glass = builder.addMaterial('Glass', [40, 70, 100, 128]);
    builder.addFace(
      [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 1.0, 0.0), (0.0, 1.0, 0.0)],
      material: glass,
    );
    final bytes = builder.toBytes();

    final path = '${Directory.systemTemp.path}/openskp_dart_glass_test.skp';
    File(path).writeAsBytesSync(bytes);
    try {
      final scene = SkpFile.open(path).buildScene();
      final mat = scene.gltfMaterials.firstWhere((m) {
        final pbr = m['pbrMetallicRoughness'] as Map<String, dynamic>;
        final c = pbr['baseColorFactor'] as List;
        return (c[3] as double) != 1.0;
      });
      final pbr = mat['pbrMetallicRoughness'] as Map<String, dynamic>;
      final alpha = (pbr['baseColorFactor'] as List)[3] as double;
      expect(alpha, closeTo(128 / 255, 0.01));
      expect(mat['alphaMode'], 'BLEND');
    } finally {
      File(path).deleteSync();
    }
  });

  test('opaque material stays byte-for-byte unchanged (no alphaMode field)', () {
    final builder = create();
    final red = builder.addMaterial('Red', [255, 0, 0]);
    builder.addFace(
      [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 1.0, 0.0), (0.0, 1.0, 0.0)],
      material: red,
    );
    final bytes = builder.toBytes();

    final path = '${Directory.systemTemp.path}/openskp_dart_red_test.skp';
    File(path).writeAsBytesSync(bytes);
    try {
      final scene = SkpFile.open(path).buildScene();
      final mat = scene.gltfMaterials[0];
      final pbr = mat['pbrMetallicRoughness'] as Map<String, dynamic>;
      expect((pbr['baseColorFactor'] as List)[3], 1.0);
      expect(mat.containsKey('alphaMode'), false);
    } finally {
      File(path).deleteSync();
    }
  });

  test('each nested level keeps its own instance name in meshIndex (openskp#240)', () {
    // Regression: each mesh's own name must reflect the specific instance
    // that placed its OWN definition, not an ancestor's - matching how
    // sceneHierarchy already builds each InstanceNode from that same
    // instance directly. A prior bug backfilled meshIndex by a substring
    // match on the sanitized path string; since a shallow instance's path
    // is always a string prefix of every deeper descendant's path too,
    // the shallowest instance's own name silently overwrote every mesh
    // beneath it as recursion unwound.
    final builder = create();
    final leaf = builder.addComponentDefinition('Leaf', (def) {
      def.addFace([(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 1.0, 0.0), (0.0, 1.0, 0.0)]);
    });
    final middle = builder.addComponentDefinition('Middle', (def) {
      def.addFace([(0.0, 0.0, 10.0), (1.0, 0.0, 10.0), (1.0, 1.0, 10.0), (0.0, 1.0, 10.0)]);
      def.addInstance(leaf, name: 'LeafInstance');
    });
    final outer = builder.addComponentDefinition('Outer', (def) {
      def.addFace([(0.0, 0.0, 20.0), (1.0, 0.0, 20.0), (1.0, 1.0, 20.0), (0.0, 1.0, 20.0)]);
      def.addInstance(middle, name: 'MiddleInstance');
    });
    builder.addInstance(outer, name: 'OuterInstance');
    final bytes = builder.toBytes();

    final path = '${Directory.systemTemp.path}/openskp_dart_nested_metadata_test.skp';
    File(path).writeAsBytesSync(bytes);
    try {
      final scene = SkpFile.open(path).buildScene();
      final names = scene.meshIndex.values.map((m) => m.name).toList()..sort();
      expect(names, ['LeafInstance', 'MiddleInstance', 'OuterInstance']);
    } finally {
      File(path).deleteSync();
    }
  });

  test('textured materials get MASK or BLEND, never left OPAQUE', () {
    // capilla_quiroz_v17.skp has four textured materials: two ordinary
    // opaque ones (MASK - a safe no-op, nothing in their JPEGs to cut out)
    // and two genuinely translucent stained-glass-style materials at alpha
    // 0.5 (BLEND, so that opacity actually renders instead of being
    // silently dropped under glTF's OPAQUE default).
    final scene = SkpFile.open(fixturePath).buildScene();
    final textured = scene.gltfMaterials.where((m) {
      final pbr = m['pbrMetallicRoughness'] as Map<String, dynamic>;
      return pbr.containsKey('baseColorTexture');
    }).toList();
    expect(textured.length, 4);

    final translucent = textured.where((m) => m['alphaMode'] == 'BLEND').toList();
    final opaque = textured.where((m) => m['alphaMode'] == 'MASK').toList();
    expect(translucent.length, 2);
    expect(opaque.length, 2);
    for (final m in translucent) {
      final pbr = m['pbrMetallicRoughness'] as Map<String, dynamic>;
      expect((pbr['baseColorFactor'] as List)[3] as double, lessThan(1.0));
    }
    for (final m in opaque) {
      final pbr = m['pbrMetallicRoughness'] as Map<String, dynamic>;
      expect((pbr['baseColorFactor'] as List)[3], 1.0);
    }
  });
}
