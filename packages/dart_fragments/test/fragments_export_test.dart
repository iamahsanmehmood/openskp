import 'dart:io';

import 'package:openskp/openskp.dart';
import 'package:openskp_fragments/openskp_fragments.dart';
import 'package:openskp_fragments/src/fragments_fb/fragments_generated.dart' as ffb;
import 'package:test/test.dart';

/// Direct SKP -> Fragments (.frag) export - Dart port of Python's
/// `openskp.export.fragments` (openskp#285). See that module's own tests
/// (`packages/python/tests/test_fragments.py`), the C++ port's
/// (`packages/cpp/tests/fragments_export_test.cpp`), and the TypeScript/
/// .NET ports for the reference coverage this file mirrors - export-only,
/// at full Python/C++/TypeScript/.NET GUID/name-is-generated/layer-hidden
/// fidelity from the start.

const List<double> identity = [
  1, 0, 0, 0,
  0, 1, 0, 0,
  0, 0, 1, 0,
  0, 0, 0, 1,
];

InstancedMeshResource makeBoxResource(String id) {
  return InstancedMeshResource(
    id: id,
    definitionId: 1,
    definitionName: 'Box',
    variantKey: '1|255,255,255',
    primitives: [
      LocalPrimitive(
        positions: [0, 0, 0, 1, 0, 0, 1, 1, 0, 0, 1, 0, 0, 0, 1, 1, 0, 1, 1, 1, 1, 0, 1, 1],
        normals: List<double>.filled(24, 0.0),
        uvs: List<double>.filled(16, 0.0),
        indices: [0, 1, 2, 0, 2, 3, 4, 6, 5, 4, 7, 6],
        materialIndex: 0,
      ),
    ],
  );
}

InstancedNode makeLeaf(String name, String meshResourceId, {List<double>? matrix, String guid = ''}) {
  return InstancedNode(
    name: name,
    matrix: matrix ?? identity,
    meshResourceId: meshResourceId,
    guid: guid,
  );
}

ffb.Model parseRaw(List<int> raw) => ffb.Model(raw);

void main() {
  group('Schema & runtime verification', () {
    test('produces a loadable model with correct item count', () {
      final scene = InstancedScene(
        meshResources: [makeBoxResource('mesh_0')],
        gltfMaterials: [{}],
        sceneHierarchy: InstancedNode(
          name: 'ROOT',
          matrix: identity,
          children: [makeLeaf('Box1', 'mesh_0')],
        ),
      );

      final raw = toFragments(scene, const FragmentExportOptions(raw: true));
      final model = parseRaw(raw);
      expect(model.localIds?.length, 1);
      expect(model.meshes, isNotNull);
      expect(model.meshes!.samples?.length, 1);
      expect(model.meshes!.shells?.length, 1);
    });

    test('deduplicates shared geometry across instances', () {
      final scene = InstancedScene(
        meshResources: [makeBoxResource('mesh_0')],
        gltfMaterials: [{}],
        sceneHierarchy: InstancedNode(
          name: 'ROOT',
          matrix: identity,
          children: [
            makeLeaf('Box1', 'mesh_0'),
            makeLeaf('Box2', 'mesh_0'),
            makeLeaf('Box3', 'mesh_0'),
          ],
        ),
      );

      final raw = toFragments(scene, const FragmentExportOptions(raw: true));
      final model = parseRaw(raw);
      final meshes = model.meshes!;
      expect(meshes.samples?.length, 3);
      expect(meshes.shells?.length, 1);
      expect(meshes.representations?.length, 1);
    });

    test('global transforms reflect each instance placement', () {
      final translated = List<double>.from(identity);
      translated[12] = 5.0;
      translated[13] = 10.0;
      translated[14] = 15.0;

      final scene = InstancedScene(
        meshResources: [makeBoxResource('mesh_0')],
        gltfMaterials: [{}],
        sceneHierarchy: InstancedNode(
          name: 'ROOT',
          matrix: identity,
          children: [makeLeaf('Box1', 'mesh_0', matrix: translated)],
        ),
      );

      final raw = toFragments(scene, const FragmentExportOptions(raw: true));
      final model = parseRaw(raw);
      final transform = model.meshes!.globalTransforms![0];
      expect(transform.position.x, closeTo(5.0, 1e-6));
      expect(transform.position.y, closeTo(10.0, 1e-6));
      expect(transform.position.z, closeTo(15.0, 1e-6));
    });

    test('guids and localIds have matching length and parity', () {
      final scene = InstancedScene(
        meshResources: [makeBoxResource('mesh_0')],
        gltfMaterials: [{}],
        sceneHierarchy: InstancedNode(
          name: 'ROOT',
          matrix: identity,
          children: [makeLeaf('Box1', 'mesh_0')],
        ),
      );

      final raw = toFragments(scene, const FragmentExportOptions(raw: true));
      final model = parseRaw(raw);
      expect(model.localIds!.length, 1);
      expect(model.guids!.length, 1);
      expect(model.guidsItems!.length, 1);
      expect(model.guidsItems![0], model.localIds![0]);
    });

    test('items without a source guid get a unique synthetic one', () {
      final scene = InstancedScene(
        meshResources: [makeBoxResource('mesh_0')],
        gltfMaterials: [{}],
        sceneHierarchy: InstancedNode(
          name: 'ROOT',
          matrix: identity,
          children: [makeLeaf('Box1', 'mesh_0'), makeLeaf('Box2', 'mesh_0')],
        ),
      );

      final raw = toFragments(scene, const FragmentExportOptions(raw: true));
      final model = parseRaw(raw);
      final guids = model.guids!.map((g) => g).toSet();
      expect(guids.every((g) => g.isNotEmpty), isTrue);
      expect(guids.length, model.guids!.length);
    });

    test('a real source guid is preserved exactly', () {
      const realGuid = 'F160C36229782F47A9857FC88DD1F2CB';
      final scene = InstancedScene(
        meshResources: [makeBoxResource('mesh_0')],
        gltfMaterials: [{}],
        sceneHierarchy: InstancedNode(
          name: 'ROOT',
          matrix: identity,
          children: [makeLeaf('Box', 'mesh_0', guid: realGuid)],
        ),
      );

      final raw = toFragments(scene, const FragmentExportOptions(raw: true));
      final model = parseRaw(raw);
      expect(model.guids!.length, 1);
      expect(model.guids![0], realGuid);
    });

    // openskp#290: SketchUp's own native Copy/Move+Copy/Array tools carry
    // an instance's attribute dictionaries - and whatever GUID a framing
    // plugin wrote into one - to every copy verbatim, so a real file can
    // have several DIFFERENT physical instances all sharing the exact
    // same non-empty InstancedNode.guid. The first instance to claim a
    // real GUID keeps it; every later instance sharing that same value
    // must fall back to a synthetic one instead of silently colliding.
    test('a duplicated source guid does not collide', () {
      const duplicatedGuid = 'F160C36229782F47A9857FC88DD1F2CB';
      final root = InstancedNode(name: 'ROOT', matrix: identity, children: [
        for (var i = 0; i < 3; i++) makeLeaf('Truss$i', 'mesh_0', guid: duplicatedGuid),
      ]);
      final scene = InstancedScene(
        meshResources: [makeBoxResource('mesh_0')],
        gltfMaterials: [{}],
        sceneHierarchy: root,
      );

      final raw = toFragments(scene, const FragmentExportOptions(raw: true));
      final model = parseRaw(raw);
      expect(model.guids!.length, 3);
      final guids = [for (var i = 0; i < 3; i++) model.guids![i]];
      expect(guids.toSet().length, 3, reason: 'guids must never collide');
      expect(guids.where((g) => g == duplicatedGuid).length, 1,
          reason: 'only the first claimant keeps the real value');
    });

    test('a named organizational wrapper with no geometry gets a tracked item; a generic one does not', () {
      final stud = makeLeaf('Stud1', 'mesh_0');
      final wrapper = InstancedNode(name: 'W-2', nameIsGenerated: false, matrix: identity, children: [stud]);

      final genericChild = makeLeaf('Stud2', 'mesh_0');
      final genericWrapper =
          InstancedNode(name: 'Component_5', nameIsGenerated: true, matrix: identity, children: [genericChild]);

      final root =
          InstancedNode(name: 'ROOT', matrix: identity, children: [wrapper, genericWrapper]);
      final scene = InstancedScene(
        meshResources: [makeBoxResource('mesh_0')],
        gltfMaterials: [{}],
        sceneHierarchy: root,
      );

      final raw = toFragments(scene, const FragmentExportOptions(raw: true));
      final model = parseRaw(raw);
      // wrapper (W-2, named, no geometry) + Stud1 + Stud2 = 3 tracked
      // items; genericWrapper itself (nameIsGenerated) is NOT tracked.
      expect(model.localIds!.length, 3);

      var foundW2 = false;
      for (final attr in model.attributes ?? const <ffb.Attribute>[]) {
        for (final d in attr.data ?? const <String>[]) {
          if (d.contains('W-2')) foundW2 = true;
        }
      }
      expect(foundW2, isTrue, reason: "the named wrapper's own real name must reach the exported Attribute data");
    });

    test('compressed output is smaller and actually decompresses', () {
      final scene = InstancedScene(
        meshResources: [makeBoxResource('mesh_0')],
        gltfMaterials: [{}],
        sceneHierarchy: InstancedNode(
          name: 'ROOT',
          matrix: identity,
          children: [makeLeaf('Box1', 'mesh_0')],
        ),
      );

      final raw = toFragments(scene, const FragmentExportOptions(raw: true));
      final compressed = toFragments(scene);
      expect(compressed, isNot(equals(raw)));
      // zlib (RFC 1950) header: 0x78 is the standard CMF byte for a 32K window.
      expect(compressed.length, greaterThanOrEqualTo(2));
      expect(compressed[0], 0x78);

      final decompressed = ZLibDecoder().convert(compressed);
      expect(decompressed, equals(raw));
    });
  });

  group('TRS decomposition and scale/mirror baking', () {
    test('decomposeTrs separates translation, rotation, scale, and mirror', () {
      const m = <double>[
        2, 0, 0, 0,
        0, 3, 0, 0,
        0, 0, 0.5, 0,
        10, 20, 30, 1,
      ];
      final trs = decomposeTrs(m);
      expect(trs.position, [10.0, 20.0, 30.0]);
      expect(trs.scale, [2.0, 3.0, 0.5]);
      expect(trs.mirrored, isFalse);
      expect(trs.xDir, [1.0, 0.0, 0.0]);
      expect(trs.yDir, [0.0, 1.0, 0.0]);
    });

    test('mirrored instance negates local X and reverses winding', () {
      const m = <double>[
        -1, 0, 0, 0,
        0, 1, 0, 0,
        0, 0, 1, 0,
        0, 0, 0, 1,
      ];
      final trs = decomposeTrs(m);
      expect(trs.mirrored, isTrue);
      expect(trs.xDir[0], closeTo(1.0, 1e-9));

      final prim = LocalPrimitive(
        positions: [0, 0, 0, 1, 0, 0, 0, 1, 0],
        normals: [],
        uvs: [],
        indices: [0, 1, 2],
        materialIndex: 0,
      );
      final baked = bakePrimitive(prim, trs.scale, trs.mirrored);
      expect(baked.triangles[0], [0, 2, 1]);
      expect(baked.points[1][0], -1.0);
    });

    test('instances sharing the same scale deduplicate to one shell', () {
      const scaled = <double>[2, 0, 0, 0, 0, 2, 0, 0, 0, 0, 2, 0, 0, 0, 0, 1];
      final scene = InstancedScene(
        meshResources: [makeBoxResource('mesh_0')],
        gltfMaterials: [{}],
        sceneHierarchy: InstancedNode(
          name: 'ROOT',
          matrix: identity,
          children: [
            makeLeaf('Box1', 'mesh_0', matrix: scaled),
            makeLeaf('Box2', 'mesh_0', matrix: scaled),
          ],
        ),
      );
      final raw = toFragments(scene, const FragmentExportOptions(raw: true));
      final model = parseRaw(raw);
      expect(model.meshes!.shells?.length, 1);
    });

    test('instances with different scales get separate shells', () {
      const scale2 = <double>[2, 0, 0, 0, 0, 2, 0, 0, 0, 0, 2, 0, 0, 0, 0, 1];
      const scale3 = <double>[3, 0, 0, 0, 0, 3, 0, 0, 0, 0, 3, 0, 0, 0, 0, 1];
      final scene = InstancedScene(
        meshResources: [makeBoxResource('mesh_0')],
        gltfMaterials: [{}],
        sceneHierarchy: InstancedNode(
          name: 'ROOT',
          matrix: identity,
          children: [
            makeLeaf('Box1', 'mesh_0', matrix: scale2),
            makeLeaf('Box2', 'mesh_0', matrix: scale3),
          ],
        ),
      );
      final raw = toFragments(scene, const FragmentExportOptions(raw: true));
      final model = parseRaw(raw);
      expect(model.meshes!.shells?.length, 2);
    });
  });

  group('Metadata and attributes', () {
    test('attributes carry item display names', () {
      final scene = InstancedScene(
        meshResources: [makeBoxResource('mesh_0')],
        gltfMaterials: [{}],
        sceneHierarchy: InstancedNode(
          name: 'ROOT',
          matrix: identity,
          children: [makeLeaf('Truss1', 'mesh_0')],
        ),
      );
      final raw = toFragments(scene, const FragmentExportOptions(raw: true));
      final model = parseRaw(raw);
      final attr = model.attributes![0];
      expect(attr.data?.length, 1);
      expect(attr.data![0], contains('Truss1'));
      expect(attr.data![0], contains('STRING'));
    });

    test('unnamed item gets an empty attribute, not a missing one', () {
      final scene = InstancedScene(
        meshResources: [makeBoxResource('mesh_0')],
        gltfMaterials: [{}],
        sceneHierarchy: InstancedNode(
          name: 'ROOT',
          matrix: identity,
          children: [makeLeaf('', 'mesh_0')],
        ),
      );
      final raw = toFragments(scene, const FragmentExportOptions(raw: true));
      final model = parseRaw(raw);
      expect(model.attributes?.length, 1);
      expect(model.attributes![0].data?.length ?? 0, 0);
    });

    test('metadata carries the source file layer_hidden state', () {
      final scene = InstancedScene(
        meshResources: [makeBoxResource('mesh_0')],
        gltfMaterials: [{}],
        sceneHierarchy: InstancedNode(
          name: 'ROOT',
          matrix: identity,
          children: [makeLeaf('Box1', 'mesh_0')],
        ),
        layerHidden: {'Layer0': false, 'wall_cladding': true},
      );
      final raw = toFragments(scene, const FragmentExportOptions(raw: true));
      final model = parseRaw(raw);
      expect(model.metadata, contains('"wall_cladding":true'));
      expect(model.metadata, contains('"Layer0":false'));
    });

    test('generated-name guids are listed in metadata', () {
      final scene = InstancedScene(
        meshResources: [makeBoxResource('mesh_0')],
        gltfMaterials: [{}],
        sceneHierarchy: InstancedNode(
          name: 'ROOT',
          matrix: identity,
          children: [
            InstancedNode(name: 'Component_7', nameIsGenerated: true, matrix: identity, meshResourceId: 'mesh_0'),
          ],
        ),
      );
      final raw = toFragments(scene, const FragmentExportOptions(raw: true));
      final model = parseRaw(raw);
      final guid = model.guids![0];
      expect(model.metadata, contains(guid));
      expect(model.metadata, contains('generated_name_guids'));
    });
  });

  group('Spatial structure', () {
    test('category reflects each node\'s own layer', () {
      final leaf = makeLeaf('Box1', 'mesh_0');
      leaf.layer = 'Studs';
      final root = InstancedNode(name: 'ROOT', matrix: identity, children: [leaf]);
      final scene = InstancedScene(
        meshResources: [makeBoxResource('mesh_0')],
        gltfMaterials: [{}],
        sceneHierarchy: root,
      );
      final raw = toFragments(scene, const FragmentExportOptions(raw: true));
      final model = parseRaw(raw);
      expect(model.categories![0], 'Studs');
    });
  });

  group('Real fixture round-trip', () {
    String fixturePath(String name) => '${Directory.current.path}/test/fixtures/$name';

    for (final fixtureName in ['SU_File.skp', 'capilla_quiroz_v17.skp', 'gondola_v20.skp']) {
      test('$fixtureName exports to a valid model', () {
        final scene = SkpFile.open(fixturePath(fixtureName)).buildInstancedScene();

        final raw = toFragments(scene, const FragmentExportOptions(raw: true));
        final model = parseRaw(raw);

        expect(model.localIds!.isNotEmpty, isTrue);
        expect(model.meshes, isNotNull);
        expect(model.meshes!.shells!.isNotEmpty, isTrue);
        expect(model.meshes!.samples!.isNotEmpty, isTrue);

        // Every sample's Shell must resolve via Representation.id (an
        // index into Meshes.shells), matching the real reader's own
        // convention - not the representation's own vector position.
        final meshes = model.meshes!;
        for (final sample in meshes.samples!) {
          final rep = meshes.representations![sample.representation];
          expect(rep.id, lessThan(meshes.shells!.length));
        }
      });
    }
  });

  test('exportFragments writes a file', () {
    final scene = InstancedScene(
      meshResources: [makeBoxResource('mesh_0')],
      gltfMaterials: [{}],
      sceneHierarchy: InstancedNode(
        name: 'ROOT',
        matrix: identity,
        children: [makeLeaf('Box1', 'mesh_0')],
      ),
    );
    final tmp = '${Directory.systemTemp.path}/openskp_fragments_test_${DateTime.now().microsecondsSinceEpoch}.frag';
    try {
      exportFragments(scene, tmp);
      final file = File(tmp);
      expect(file.existsSync(), isTrue);
      expect(file.lengthSync(), greaterThan(0));
    } finally {
      if (File(tmp).existsSync()) File(tmp).deleteSync();
    }
  });

  group('oversized shell splitting (openskp#285 / PR #355)', () {
    // A single shell (Fragments' term for one baked triangle mesh) has no
    // representation for more than 65535 triangles: profilesFaceIds is a
    // plain ushort array in the real schema (index.fbs), with no uint32
    // escape hatch the way POINTS get past 65535 via BigShellProfile. A
    // real production model with one 222,000+-triangle primitive (a large
    // flattened/dense mesh) hit this in practice on the Python port - see
    // PR #355 for the full incident. Unlike Python (which threw at write
    // time), this port's writeListUint16 (via dart:typed_data's
    // ByteData.setUint16) silently WRAPPED instead of throwing - a worse
    // bug in one sense: it wrote wrong/colliding face ids instead of
    // failing loudly. getOrBakeShell now splits an oversized primitive's
    // triangles into multiple shells instead, each within the ushort
    // limit.

    LocalPrimitive gridPrimitive(int cols, int rows) {
      final positions = List<double>.filled(cols * rows * 3, 0.0);
      for (var j = 0; j < rows; j++) {
        for (var i = 0; i < cols; i++) {
          final v = j * cols + i;
          positions[v * 3] = i.toDouble();
          positions[v * 3 + 1] = j.toDouble();
          positions[v * 3 + 2] = 0.0;
        }
      }
      final triCells = (cols - 1) * (rows - 1);
      final indices = List<int>.filled(triCells * 6, 0);
      var k = 0;
      for (var j = 0; j < rows - 1; j++) {
        for (var i = 0; i < cols - 1; i++) {
          final a = j * cols + i;
          final b = a + 1;
          final c = a + cols;
          final d = c + 1;
          indices[k++] = a; indices[k++] = b; indices[k++] = d;
          indices[k++] = a; indices[k++] = d; indices[k++] = c;
        }
      }
      return LocalPrimitive(
        positions: positions,
        normals: List<double>.filled(cols * rows * 3, 0.0),
        uvs: List<double>.filled(cols * rows * 2, 0.0),
        indices: indices,
        materialIndex: 0,
      );
    }

    InstancedScene sceneWithOnePrimitive(LocalPrimitive prim, String meshId) {
      return InstancedScene(
        meshResources: [
          InstancedMeshResource(
            id: meshId,
            definitionId: 1,
            definitionName: 'BigMesh',
            variantKey: '1|255,255,255',
            primitives: [prim],
          ),
        ],
        gltfMaterials: [{}],
        sceneHierarchy: InstancedNode(
          name: 'ROOT',
          matrix: identity,
          children: [makeLeaf('Big', meshId)],
        ),
      );
    }

    test('splits an oversized primitive and every face id across every sub-shell is correct and sequential', () {
      const cols = 210, rows = 165;
      final expectedTriangles = (cols - 1) * (rows - 1) * 2;
      expect(expectedTriangles, greaterThan(65535)); // sanity-check the fixture itself exceeds the limit under test

      final scene = sceneWithOnePrimitive(gridPrimitive(cols, rows), 'mesh_big');

      // This is the exact shape of call that, before the fix, silently
      // wrote wrapped-around (wrong) face ids - the primary regression
      // check is that the result is now actually correct, not just that
      // it doesn't throw (unlike Python's own crash-shaped version of
      // this bug).
      final raw = toFragments(scene, const FragmentExportOptions(raw: true));
      final model = parseRaw(raw);
      final meshes = model.meshes!;

      expect(meshes.shells!.length, greaterThan(1)); // confirms the split actually happened, not a no-op
      expect(meshes.samples!.length, meshes.shells!.length); // one sample per split shell, same item/material

      var totalTriangles = 0;
      for (final shell in meshes.shells!) {
        final faceIds = shell.profilesFaceIds!;
        expect(faceIds.length, lessThanOrEqualTo(65535)); // every sub-shell stays within the ushort limit
        expect(shell.profiles!.length, faceIds.length);
        // Every face id in a shell is a small, sequential, non-wrapped
        // 0..N-1 run - the exact thing the old raw uint16 write could
        // silently violate once a shell's own triangle count exceeded
        // 65535.
        for (var j = 0; j < faceIds.length; j++) {
          expect(faceIds[j], j);
        }
        totalTriangles += faceIds.length;
      }

      expect(totalTriangles, expectedTriangles);
    });

    test('a primitive within the limit still produces exactly one shell', () {
      // Regression guard on the split path itself: a normal, non-huge
      // primitive must not be needlessly split into multiple shells.
      final scene = sceneWithOnePrimitive(gridPrimitive(50, 50), 'mesh_small'); // 49*49*2 = 4802 triangles

      final raw = toFragments(scene, const FragmentExportOptions(raw: true));
      final model = parseRaw(raw);
      final meshes = model.meshes!;
      expect(meshes.shells!.length, 1);
      expect(meshes.samples!.length, 1);
    });
  });

  group('fromFragments (openskp#285: reading a .frag file back)', () {
    InstancedScene makeTwoInstanceScene() {
      final resource = InstancedMeshResource(
        id: 'mesh_0',
        definitionId: 1,
        definitionName: 'Box',
        variantKey: '1|255,255,255',
        primitives: [
          LocalPrimitive(
            positions: [0, 0, 0, 1, 0, 0, 1, 1, 0, 0, 1, 0, 0, 0, 1, 1, 0, 1, 1, 1, 1, 0, 1, 1],
            normals: List<double>.filled(24, 0.0),
            uvs: List<double>.filled(16, 0.0),
            indices: [0, 1, 2, 0, 2, 3, 4, 6, 5, 4, 7, 6],
            materialIndex: 0,
          ),
        ],
      );
      final nodeA = InstancedNode(name: 'Box_A', layer: 'Framing', matrix: identity, meshResourceId: 'mesh_0', guid: '');
      final nodeB = InstancedNode(name: 'Box_B', layer: 'Framing', matrix: identity, meshResourceId: 'mesh_0', guid: '');
      return InstancedScene(
        meshResources: [resource],
        gltfMaterials: [{}],
        sceneHierarchy: InstancedNode(name: 'ROOT', matrix: identity, children: [nodeA, nodeB]),
      );
    }

    test('a scene with an ODD number of distinct materials round-trips its colors exactly', () {
      // Regression guard for a real bug this reader found in the WRITER
      // (fragments_export.dart's own materials-vector build): Material is
      // 6 bytes/element, the only struct in this schema not a multiple of
      // 4, so an odd material count left package:flat_buffers's
      // writeListOfStructs+endStructVector inserting 2 bytes of alignment
      // padding between the vector's length prefix and its first element
      // - invisible to every OTHER existing test here (none previously
      // read materials back), but a real, silent corruption: reading it
      // back before the fix gave r=0/g=0/b=255/a=255/renderedFaces=-1
      // (an invalid enum value, threw) instead of the real
      // r=255/g=255/b=255/a=255/renderedFaces=ONE. 3 materials (18 bytes,
      // same misalignment class as 1) is the case exercised here; the
      // fix itself lives in fragments_export.dart right before
      // writeListOfStructs is called for materials.
      InstancedMeshResource singleTriResource(String id, int materialIndex) => InstancedMeshResource(
            id: id,
            definitionId: 1,
            definitionName: 'Tri',
            variantKey: '$materialIndex',
            primitives: [
              LocalPrimitive(
                positions: [0, 0, 0, 1, 0, 0, 0, 1, 0],
                normals: List<double>.filled(9, 0.0),
                uvs: List<double>.filled(6, 0.0),
                indices: [0, 1, 2],
                materialIndex: materialIndex,
              ),
            ],
          );
      final colors = [
        (255, 0, 0, 255), // red
        (0, 255, 0, 255), // green
        (0, 0, 255, 255), // blue
      ];
      final scene = InstancedScene(
        meshResources: [
          singleTriResource('mesh_0', 0),
          singleTriResource('mesh_1', 1),
          singleTriResource('mesh_2', 2),
        ],
        gltfMaterials: [
          for (final (r, g, b, a) in colors)
            {
              'pbrMetallicRoughness': {
                'baseColorFactor': [r / 255.0, g / 255.0, b / 255.0, a / 255.0],
              },
            },
        ],
        sceneHierarchy: InstancedNode(
          name: 'ROOT',
          matrix: identity,
          children: [
            InstancedNode(name: 'A', matrix: identity, meshResourceId: 'mesh_0', guid: ''),
            InstancedNode(name: 'B', matrix: identity, meshResourceId: 'mesh_1', guid: ''),
            InstancedNode(name: 'C', matrix: identity, meshResourceId: 'mesh_2', guid: ''),
          ],
        ),
      );

      final raw = toFragments(scene, const FragmentExportOptions(raw: true));
      final model = parseRaw(raw);
      final materials = model.meshes!.materials!;
      expect(materials.length, 3);
      for (var i = 0; i < 3; i++) {
        expect(materials[i].r, colors[i].$1);
        expect(materials[i].g, colors[i].$2);
        expect(materials[i].b, colors[i].$3);
        expect(materials[i].a, colors[i].$4);
        expect(materials[i].renderedFaces, ffb.RenderedFaces.ONE);
      }

      final back = fromFragments(raw);
      expect(back.gltfMaterials.length, 3);
      for (var i = 0; i < 3; i++) {
        final bcf = (back.gltfMaterials[i]['pbrMetallicRoughness'] as Map)['baseColorFactor'] as List;
        expect((bcf[0] * 255).round(), colors[i].$1);
        expect((bcf[1] * 255).round(), colors[i].$2);
        expect((bcf[2] * 255).round(), colors[i].$3);
      }
    });

    test('round-trips a basic two-instance scene through toFragments -> fromFragments', () {
      final scene = makeTwoInstanceScene();
      final raw = toFragments(scene, const FragmentExportOptions(raw: true));
      final back = fromFragments(raw);

      expect(back.meshResources.length, 1);
      expect(back.meshResources[0].primitives.length, 1);
      // The box resource above carries 8 vertices but only 4 triangles
      // (2 of the cube's 6 faces), matching makeBoxResource's own shape.
      expect(back.meshResources[0].primitives[0].positions.length, 8 * 3);
      expect(back.meshResources[0].primitives[0].indices.length, 4 * 3);

      expect(back.sceneHierarchy.children.length, 2);
      final a = back.sceneHierarchy.children[0];
      final b = back.sceneHierarchy.children[1];
      expect(a.name, 'Box_A');
      expect(b.name, 'Box_B');
      expect(a.layer, 'Framing');
      expect(a.meshResourceId, b.meshResourceId);
      expect(back.gltfMaterials, isNotEmpty);
    });

    test('round-trips both the raw and zlib-compressed wire formats, auto-detected', () {
      final scene = makeTwoInstanceScene();
      final rawBytes = toFragments(scene, const FragmentExportOptions(raw: true));
      final compressedBytes = toFragments(scene, const FragmentExportOptions(raw: false));
      expect(rawBytes, isNot(equals(compressedBytes)));

      final backFromRaw = fromFragments(rawBytes);
      final backFromCompressed = fromFragments(compressedBytes);

      expect(
        backFromRaw.meshResources[0].primitives[0].indices.length,
        backFromCompressed.meshResources[0].primitives[0].indices.length,
      );
      expect(
        backFromRaw.sceneHierarchy.children.map((c) => c.name).toList(),
        backFromCompressed.sceneHierarchy.children.map((c) => c.name).toList(),
      );
    });

    test('preserves real source guids and layer_hidden metadata across the round-trip', () {
      final scene = makeTwoInstanceScene();
      scene.sceneHierarchy.children[0].guid = 'F160C36229782F47A9857FC88DD1F2CB';
      scene.layerHidden = {'Framing': false, 'Cladding': true};
      final raw = toFragments(scene, const FragmentExportOptions(raw: true));
      final back = fromFragments(raw);

      expect(back.sceneHierarchy.children[0].guid, 'F160C36229782F47A9857FC88DD1F2CB');
      expect(back.layerHidden, {'Framing': false, 'Cladding': true});
    });

    test('reconstructs every triangle of a primitive split across multiple shells on export', () {
      // Same grid-primitive shape as the oversized-shell-splitting tests
      // above, forcing the export side to split into several shells - the
      // read side has to walk every sample for the item and reassemble
      // them into the ONE mesh resource, not just read the first shell.
      LocalPrimitive gridPrimitive(int cols, int rows) {
        final positions = List<double>.filled(cols * rows * 3, 0.0);
        for (var j = 0; j < rows; j++) {
          for (var i = 0; i < cols; i++) {
            final v = j * cols + i;
            positions[v * 3] = i.toDouble();
            positions[v * 3 + 1] = j.toDouble();
            positions[v * 3 + 2] = 0.0;
          }
        }
        final triCells = (cols - 1) * (rows - 1);
        final indices = List<int>.filled(triCells * 6, 0);
        var k = 0;
        for (var j = 0; j < rows - 1; j++) {
          for (var i = 0; i < cols - 1; i++) {
            final a = j * cols + i;
            final b = a + 1;
            final c = a + cols;
            final d = c + 1;
            indices[k++] = a; indices[k++] = b; indices[k++] = d;
            indices[k++] = a; indices[k++] = d; indices[k++] = c;
          }
        }
        return LocalPrimitive(
          positions: positions,
          normals: List<double>.filled(cols * rows * 3, 0.0),
          uvs: List<double>.filled(cols * rows * 2, 0.0),
          indices: indices,
          materialIndex: 0,
        );
      }

      const cols = 210, rows = 165; // 209*164*2 = 68,552 triangles - forces a split (> 65535)
      final prim = gridPrimitive(cols, rows);
      final expectedTriangles = (cols - 1) * (rows - 1) * 2;

      final scene = InstancedScene(
        meshResources: [
          InstancedMeshResource(
            id: 'mesh_big',
            definitionId: 1,
            definitionName: 'BigMesh',
            variantKey: '1|255,255,255',
            primitives: [prim],
          ),
        ],
        gltfMaterials: [{}],
        sceneHierarchy: InstancedNode(
          name: 'ROOT',
          matrix: identity,
          children: [InstancedNode(name: 'BigMesh1', matrix: identity, meshResourceId: 'mesh_big', guid: '')],
        ),
      );

      final raw = toFragments(scene, const FragmentExportOptions(raw: true));

      final model = parseRaw(raw);
      expect(model.meshes!.shells!.length, greaterThan(1));

      final back = fromFragments(raw);
      expect(back.meshResources.length, 1);
      final totalTrianglesBack = back.meshResources[0].primitives.fold<int>(0, (sum, p) => sum + p.indices.length ~/ 3);
      expect(totalTrianglesBack, expectedTriangles);
    });
  });
}
