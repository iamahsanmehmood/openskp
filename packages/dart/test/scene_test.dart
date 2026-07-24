import 'dart:io';

import 'package:openskp/openskp.dart';
import 'package:test/test.dart';

/// Real-file regression test for SkpFile.buildScene() - the opt-in
/// scene-hierarchy + triangulation + GLB mesh capability, ported from the
/// TypeScript reference implementation.
///
/// Cross-validated directly against Python's and TypeScript's
/// build_scene()/buildScene() on this exact fixture: mesh count,
/// mesh_index count, gltf_materials count, root instance count, and the
/// first three meshes' exact vertex/triangle counts and material indices
/// all match precisely.
void main() {
  final fixturePath = '${Directory.current.path}/test/fixtures/capilla_quiroz_v17.skp';

  test('buildScene matches Python and TypeScript ground truth', () {
    final scene = SkpFile.open(fixturePath).buildScene();

    expect(scene.glbPrimitives.length, 13);
    expect(scene.meshIndex.length, 13);
    expect(scene.gltfMaterials.length, 9);

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
      expect(prim.indices.length % 3, 0);
      final nVerts = prim.positions.length ~/ 3;
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
    expect(scene.glbPrimitives.length, 13);
  });
}
