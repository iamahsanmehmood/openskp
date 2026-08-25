import 'dart:io';

import 'package:openskp/openskp.dart';
import 'package:openskp/src/legacy.dart';
import 'package:test/test.dart';

/// Real-file regression test for SketchUp 2020 (v20) classic .skp files.
///
/// Fixture: fixtures/gondola_v20.skp - a retail gondola display authored in
/// SketchUp 2020 (v20.1.235, ~755 KB), shared via the TypeScript port's
/// PR #155.
///
/// Before the v20 layout fixes, this file threw `implausible definition
/// count` from _walk(): v20 writes records the v17 layout does not have,
/// which left the reader a few bytes short and made it read garbage where a
/// count was expected. The existing v17 fixture (capilla_quiroz_v17.skp) has
/// only one layer and never exercised any of these paths, so the divergence
/// went unnoticed.
///
/// Every count below was read off this exact file after the fix and
/// sanity-checked for plausibility (bounding box in metres, definitions
/// carrying real geometry, instances actually placed in the scene) - a
/// parse that "succeeds" while silently dropping placements would still be
/// a bug, so the instance counts matter as much as the parse not throwing.
void main() {
  final fixturePath = '${Directory.current.path}/test/fixtures/gondola_v20.skp';

  test('is detected as a legacy container', () {
    final bytes = File(fixturePath).readAsBytesSync();
    expect(Legacy.isLegacy(bytes), isTrue);
  });

  test('parses a real v20 file that previously threw', () {
    final model = SkpFile.open(fixturePath).parse();

    expect(model.version, '{20.1.235}');
    // legacy files carry no meta/meta.dat, same as v17
    expect(model.units, isNull);

    expect(model.definitions.length, 20);
    expect(model.materials.length, 24);

    // v20 interleaves a null object-ref after EACH layer record; the count
    // is the number of REAL layers. The old reader counted the separators
    // as items and dropped every layer after the first - this fixture
    // really does carry "Gondulas Laterais" (visible in SketchUp), which
    // the previous assertion enshrined as missing. Nulls must still never
    // reach model.layers.
    final names = model.layers.map((l) => l.name).toList();
    expect(names, ['Layer0', 'Gondulas Laterais']);

    // real geometry, not an empty shell
    var faces = 0, edges = 0, vertices = 0;
    for (final d in model.definitions.values) {
      faces += d.faces.length;
      edges += d.edges.length;
      vertices += d.vertices.length;
    }
    expect(faces, 1887);
    expect(edges, 9174);
    expect(vertices, 6543);
  });

  test('places every root instance', () {
    final model = SkpFile.open(fixturePath).parse();
    // 23 root-level placements: the definitions above are useless if the
    // instances that position them in the model are lost, which is exactly
    // what a subtly misaligned walk produces - a file that parses into an
    // almost-empty scene instead of throwing.
    expect(model.root.instances.length, 23);

    final scene = SkpFile.open(fixturePath).buildScene();
    expect(scene.sceneHierarchy.children.length, 23);
    expect(scene.glbPrimitives.length, 201);
    expect(scene.meshIndex.length, 201);
    expect(scene.gltfMaterials, isNotEmpty);
  });

  test('resolves placed instances to definitions that carry geometry', () {
    // Guards the failure mode a zero entity count produces: the definitions
    // an instance points at come back empty, so the file parses into a
    // scene of correctly-positioned but invisible groups. Counting
    // definitions or instances alone does not catch it - the two have to be
    // checked together.
    final model = SkpFile.open(fixturePath).parse();

    final referenced = <int>{};
    for (final inst in model.root.instances) {
      if (inst.refIdx != null) referenced.add(inst.refIdx!);
    }
    for (final def in model.definitions.values) {
      for (final inst in def.instances) {
        if (inst.refIdx != null) referenced.add(inst.refIdx!);
      }
    }

    final memo = <int, bool>{};
    final inProgress = <int>{};
    bool carriesGeometry(int defId) {
      if (memo.containsKey(defId)) return memo[defId]!;
      if (inProgress.contains(defId)) return false; // reference cycle
      inProgress.add(defId);
      final def = model.definitions[defId];
      final result = def != null &&
          (def.faces.isNotEmpty ||
              def.instances.any((child) =>
                  child.refIdx != null && carriesGeometry(child.refIdx!)));
      inProgress.remove(defId);
      memo[defId] = result;
      return result;
    }

    final empty = referenced.where((id) => !carriesGeometry(id)).toList();
    expect(empty, isEmpty);
  });

  test('bakes geometry at a plausible real-world scale', () {
    final scene = SkpFile.open(fixturePath).buildScene();
    final mn = [double.infinity, double.infinity, double.infinity];
    final mx = [-double.infinity, -double.infinity, -double.infinity];
    for (final prim in scene.glbPrimitives) {
      final pos = prim.positions;
      for (int i = 0; i < pos.length; i += 3) {
        for (int a = 0; a < 3; a++) {
          final v = pos[i + a];
          if (v < mn[a]) mn[a] = v;
          if (v > mx[a]) mx[a] = v;
        }
      }
    }
    // a shop gondola display: metres, not the 1e3-off or degenerate box a
    // misaligned read produces
    expect((mx[0] - mn[0] - 3.82).abs() < 0.1, isTrue);
    expect((mx[1] - mn[1] - 3.14).abs() < 0.1, isTrue);
    expect((mx[2] - mn[2] - 4.82).abs() < 0.1, isTrue);
  });

  test('gives every baked primitive valid uv coordinates', () {
    final scene = SkpFile.open(fixturePath).buildScene();
    expect(scene.glbPrimitives, isNotEmpty);
    for (final prim in scene.glbPrimitives) {
      final nVerts = prim.positions.length ~/ 3;
      expect(prim.uvs.length, nVerts * 2);
      for (final uv in prim.uvs) {
        expect(uv.isFinite, isTrue);
      }
    }
  });
}
