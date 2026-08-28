import 'dart:io';

import 'package:openskp/openskp.dart';
import 'package:test/test.dart';

/// Tests for `edit.dart` - loading an existing legacy `.skp` file and
/// rebuilding it as a new [SkpBuilder] (see that library's own doc comment
/// for the exact scope and known fidelity gaps this suite exercises). The
/// Dart port of Python's `test_edit.py`.
void main() {
  final fixturesDir = '${Directory.current.path}/test/fixtures';

  const square = [
    (0.0, 0.0, 0.0), (100.0, 0.0, 0.0), (100.0, 100.0, 0.0), (0.0, 100.0, 0.0),
  ];

  /// Build [builder]'s bytes into a temp file and hand back its path -
  /// [openExisting] needs a real path (it reads the file's own head bytes
  /// directly), not an in-memory buffer.
  String saveToTempFile(SkpBuilder builder, Directory tmpDir, [String name = 'source.skp']) {
    final path = '${tmpDir.path}${Platform.pathSeparator}$name';
    builder.save(path);
    return path;
  }

  group('openExisting', () {
    test('rejects a modern (VFF) source file', () {
      expect(
        () => openExisting('$fixturesDir/SU_File.skp'),
        throwsA(isA<SkpWriteError>().having((e) => e.message, 'message', contains('not a legacy-format'))),
      );
    });

    test('rejects a nonexistent file', () {
      expect(
        () => openExisting('$fixturesDir/does_not_exist.skp'),
        throwsA(isA<FileSystemException>()),
      );
    });

    test('round-trips a simple file end to end', () {
      final tmpDir = Directory.systemTemp.createTempSync('openskp_edit_test_');
      try {
        final builder = create();
        final red = builder.addMaterial('Red', [255, 0, 0]);
        final roof = builder.addLayer('Roof');
        final chair = builder.addComponentDefinition('Chair', (def) {
          def.addFace([(0.0, 0.0, 0.0), (20.0, 0.0, 0.0), (20.0, 20.0, 0.0), (0.0, 20.0, 0.0)], material: red);
        });
        builder.addInstance(chair, translation: (0.0, 0.0, 0.0));
        builder.addInstance(chair, translation: (50.0, 0.0, 0.0));
        builder.addFace(square, material: red, layer: roof);
        final src = saveToTempFile(builder, tmpDir);

        final result = openExisting(src);
        final rebuilt = SkpFile.fromBuffer(result.builder.toBytes()).parse();

        expect(rebuilt.root.faces.length, 1);
        expect(rebuilt.root.instances.length, 2);
        expect(rebuilt.definitions.length, 1);
        final chairDefn = rebuilt.definitions.values.first;
        expect(chairDefn.name, 'Chair');
        expect(chairDefn.faces.length, 1);
        expect(rebuilt.materials.map((m) => m.name).toList(), ['Red']);
        expect(rebuilt.layers.map((l) => l.name), contains('Roof'));
      } finally {
        tmpDir.deleteSync(recursive: true);
      }
    });

    test('a genuinely empty instance name is preserved, not replaced', () {
      // Found via cross-language analysis (2026-08-28): addInstance's own
      // name-defaults-to-definition-name fallback and _replayInstance's
      // own `inst.name.isNotEmpty ? inst.name : null` both silently
      // replaced a genuinely empty instance name with its definition's
      // name - a real difference, not cosmetic (a later rename of the
      // definition would no longer show through). No dedicated
      // regression test for this existed for Dart specifically, unlike
      // Python/TypeScript.
      final tmpDir = Directory.systemTemp.createTempSync('openskp_edit_test_');
      try {
        final builder = create();
        final box = builder.addComponentDefinition('Box', (def) {
          def.addFace([(0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (10.0, 10.0, 0.0), (0.0, 10.0, 0.0)]);
        });
        builder.addInstance(box, name: '', translation: (0.0, 0.0, 0.0));
        final src = saveToTempFile(builder, tmpDir);

        final source = SkpFile.fromBuffer(File(src).readAsBytesSync()).parse();
        expect(source.root.instances[0].name, '');

        final result = openExisting(src);
        final rebuilt = SkpFile.fromBuffer(result.builder.toBytes()).parse();
        expect(rebuilt.root.instances[0].name, '');
      } finally {
        tmpDir.deleteSync(recursive: true);
      }
    });

    test('a genuinely empty definition name is preserved, not replaced', () {
      // Found via cross-language analysis (2026-08-28), same bug class as
      // the empty instance name case above: `defn.name.isNotEmpty ?
      // defn.name : 'Definition$defId'` silently replaced a genuinely
      // empty definition name with a fabricated one. SketchUp Groups are
      // internally just unnamed component definitions (unlike
      // Components, which SketchUp auto-names), so an empty name is
      // common in real files.
      final tmpDir = Directory.systemTemp.createTempSync('openskp_edit_test_');
      try {
        final builder = create();
        final box = builder.addComponentDefinition('', (def) {
          def.addFace([(0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (10.0, 10.0, 0.0), (0.0, 10.0, 0.0)]);
        });
        builder.addInstance(box, translation: (0.0, 0.0, 0.0));
        final src = saveToTempFile(builder, tmpDir);

        final source = SkpFile.fromBuffer(File(src).readAsBytesSync()).parse();
        expect(source.definitions.values.first.name, '');

        final result = openExisting(src);
        final rebuilt = SkpFile.fromBuffer(result.builder.toBytes()).parse();
        expect(rebuilt.definitions.values.first.name, '');
      } finally {
        tmpDir.deleteSync(recursive: true);
      }
    });

    test('preserves instance translation', () {
      final tmpDir = Directory.systemTemp.createTempSync('openskp_edit_test_');
      try {
        final builder = create();
        final post = builder.addComponentDefinition('Post', (def) {
          def.addFace([(0.0, 0.0, 0.0), (5.0, 0.0, 0.0), (5.0, 5.0, 0.0), (0.0, 5.0, 0.0)]);
        });
        builder.addInstance(post, translation: (37.5, -12.25, 8.0));
        final src = saveToTempFile(builder, tmpDir);

        final result = openExisting(src);
        final rebuilt = SkpFile.fromBuffer(result.builder.toBytes()).parse();
        final inst = rebuilt.root.instances.first;
        expect(inst.matrix[9], closeTo(37.5, 1e-9));
        expect(inst.matrix[10], closeTo(-12.25, 1e-9));
        expect(inst.matrix[11], closeTo(8.0, 1e-9));
      } finally {
        tmpDir.deleteSync(recursive: true);
      }
    });

    test('preserves instance hidden flag and layer color', () {
      final tmpDir = Directory.systemTemp.createTempSync('openskp_edit_test_');
      try {
        final builder = create();
        final roof = builder.addLayer('Roof', color: [150, 75, 30], hidden: true);
        final post = builder.addComponentDefinition('Post', (def) {
          def.addFace([(0.0, 0.0, 0.0), (5.0, 0.0, 0.0), (5.0, 5.0, 0.0), (0.0, 5.0, 0.0)]);
        });
        builder.addInstance(post, hidden: true, layer: roof);
        final src = saveToTempFile(builder, tmpDir);

        final result = openExisting(src);
        final rebuilt = SkpFile.fromBuffer(result.builder.toBytes()).parse();
        expect(rebuilt.root.instances.first.hidden, isTrue);
        final roofLayer = rebuilt.layers.firstWhere((l) => l.name == 'Roof');
        expect((roofLayer.colorR, roofLayer.colorG, roofLayer.colorB), (150, 75, 30));
        expect(roofLayer.hidden, isTrue);
      } finally {
        tmpDir.deleteSync(recursive: true);
      }
    });

    test('nested definitions replay in dependency order', () {
      // Car nests 2 instances of Wheel - Wheel must be fully built and
      // closed before Car opens (write order matters for this format).
      final tmpDir = Directory.systemTemp.createTempSync('openskp_edit_test_');
      try {
        final builder = create();
        final wheel = builder.addComponentDefinition('Wheel', (def) {
          def.addFace([(0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (10.0, 10.0, 0.0), (0.0, 10.0, 0.0)]);
        });
        final car = builder.addComponentDefinition('Car', (def) {
          def.addFace([(0.0, 0.0, 0.0), (100.0, 0.0, 0.0), (100.0, 50.0, 0.0), (0.0, 50.0, 0.0)]);
          def.addInstance(wheel, translation: (10.0, 10.0, 0.0));
          def.addInstance(wheel, translation: (80.0, 10.0, 0.0));
        });
        builder.addInstance(car);
        final src = saveToTempFile(builder, tmpDir);

        final result = openExisting(src);
        final rebuilt = SkpFile.fromBuffer(result.builder.toBytes()).parse();
        final byName = {for (final d in rebuilt.definitions.values) d.name: d};
        expect(byName.keys.toSet(), {'Wheel', 'Car'});
        expect(byName['Car']!.instances.length, 2);
      } finally {
        tmpDir.deleteSync(recursive: true);
      }
    });

    test('materials and layers are reusable on new geometry after replay', () {
      final tmpDir = Directory.systemTemp.createTempSync('openskp_edit_test_');
      try {
        final builder = create();
        final red = builder.addMaterial('Red', [255, 0, 0]);
        final roof = builder.addLayer('Roof');
        builder.addFace(square, material: red, layer: roof);
        final src = saveToTempFile(builder, tmpDir);

        final result = openExisting(src);
        expect(result.builder.materialsByName, contains('Red'));
        expect(result.builder.layersByName, contains('Roof'));
        result.builder.addFace(
          [(300.0, 0.0, 0.0), (310.0, 0.0, 0.0), (310.0, 10.0, 0.0), (300.0, 10.0, 0.0)],
          material: result.builder.materialsByName['Red'],
          layer: result.builder.layersByName['Roof'],
        );
        final rebuilt = SkpFile.fromBuffer(result.builder.toBytes()).parse();
        expect(rebuilt.materials.length, 1); // still just "Red" - reused, not duplicated
        expect(rebuilt.root.faces.length, 2);
      } finally {
        tmpDir.deleteSync(recursive: true);
      }
    });

    test('definitions are returned by name and reusable for new placements', () {
      final tmpDir = Directory.systemTemp.createTempSync('openskp_edit_test_');
      try {
        final builder = create();
        final wheel = builder.addComponentDefinition('Wheel', (def) {
          def.addFace([(0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (10.0, 10.0, 0.0), (0.0, 10.0, 0.0)]);
        });
        builder.addInstance(wheel, translation: (0.0, 0.0, 0.0));
        final src = saveToTempFile(builder, tmpDir);

        final result = openExisting(src);
        expect(result.definitions, contains('Wheel'));
        result.builder.addInstance(result.definitions['Wheel']!, translation: (100.0, 0.0, 0.0));
        final rebuilt = SkpFile.fromBuffer(result.builder.toBytes()).parse();
        expect(rebuilt.definitions.length, 1); // still just one Wheel definition
        expect(rebuilt.root.instances.length, 2); // but now placed twice
      } finally {
        tmpDir.deleteSync(recursive: true);
      }
    });

    test('a new material/layer/definition/group is rejected after replay', () {
      // Documented, tested constraint (not a bug): replaying a source
      // file's own root-level geometry already finalizes the writer's
      // materials/layers/definitions sections, per the same file-format
      // ordering requirement every create() builder has always had.
      final tmpDir = Directory.systemTemp.createTempSync('openskp_edit_test_');
      try {
        final builder = create();
        builder.addFace(square);
        final src = saveToTempFile(builder, tmpDir);

        final result = openExisting(src);
        expect(
          () => result.builder.addMaterial('Chrome', [180, 180, 185]),
          throwsA(isA<SkpWriteError>().having((e) => e.message, 'message', contains('addFace'))),
        );
        expect(
          () => result.builder.addLayer('Extra'),
          throwsA(isA<SkpWriteError>().having((e) => e.message, 'message', contains('addFace'))),
        );
        expect(
          () => result.builder.addComponentDefinition('New', (def) {}),
          throwsA(isA<SkpWriteError>()),
        );
        expect(
          () => result.builder.addGroup((def) {}, name: 'NewGroup'),
          throwsA(isA<SkpWriteError>()),
        );
      } finally {
        tmpDir.deleteSync(recursive: true);
      }
    });

    test('a positioned texture round-trips', () {
      final tmpDir = Directory.systemTemp.createTempSync('openskp_edit_test_');
      try {
        final pngBytes = [0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A, ...List.generate(48, (i) => i)];
        final pngPath = '${tmpDir.path}${Platform.pathSeparator}tex.png';
        File(pngPath).writeAsBytesSync(pngBytes);

        final builder = create();
        final brick = builder.addTextureMaterial('Brick', pngPath);
        builder.addFace(
          square,
          material: brick,
          frontUv: [
            ((0.0, 0.0, 0.0), (0.0, 0.0)),
            ((50.0, 0.0, 0.0), (1.0, 0.0)),
            ((0.0, 50.0, 0.0), (0.0, 1.0)),
          ],
        );
        final src = saveToTempFile(builder, tmpDir);

        final result = openExisting(src);
        final rebuilt = SkpFile.fromBuffer(result.builder.toBytes()).parse();
        final face = rebuilt.root.faces.values.first;
        expect(face.uvTransform, isNotNull);
        expect(rebuilt.materials.first.texture, isNotNull);
        expect(rebuilt.materials.first.texture!.data, equals(pngBytes));
      } finally {
        tmpDir.deleteSync(recursive: true);
      }
    });

    test('a face with a hole round-trips', () {
      // add_face's holes support means a multi-loop face is faithfully
      // replayed, not skipped.
      final tmpDir = Directory.systemTemp.createTempSync('openskp_edit_test_');
      try {
        const wall = [
          (0.0, 0.0, 0.0), (200.0, 0.0, 0.0), (200.0, 100.0, 0.0), (0.0, 100.0, 0.0),
        ];
        const window = [
          (80.0, 30.0, 0.0), (120.0, 30.0, 0.0), (120.0, 70.0, 0.0), (80.0, 70.0, 0.0),
        ];
        final builder = create();
        builder.addFace(wall, holes: [window]);
        final src = saveToTempFile(builder, tmpDir);

        final result = openExisting(src);
        expect(result.warnings.any((w) => w.contains('hole')), isFalse);
        final rebuilt = SkpFile.fromBuffer(result.builder.toBytes()).parse();
        final face = rebuilt.root.faces.values.first;
        expect(face.loops.length, 2);
      } finally {
        tmpDir.deleteSync(recursive: true);
      }
    });

    test('an empty (blank scaffold) source can still be saved after adding geometry', () {
      final result = openExisting('$fixturesDir/blank_v17.skp');
      expect(
        () => result.builder.toBytes(),
        throwsA(isA<SkpWriteError>().having((e) => e.message, 'message', contains('no geometry'))),
      );
      result.builder.addFace(square);
      final data = result.builder.toBytes();
      expect(data, isNotEmpty);
    });
  });

  group('RealWorldFixtures', () {
    // Round-trip real, non-writer-authored files - the true stress test
    // for this module, since every other test above only exercises
    // content this project's own writer already produces (a much
    // narrower subset of what real SketchUp files contain).
    for (final fixtureName in ['capilla_quiroz_v17.skp', 'gondola_v20.skp']) {
      test('$fixtureName round-trips without crashing', () {
        final path = '$fixturesDir/$fixtureName';
        if (!File(path).existsSync()) {
          markTestSkipped('fixture $fixtureName not present');
          return;
        }
        final result = openExisting(path);
        final data = result.builder.toBytes();
        // self-parses without throwing - the authoritative "is this
        // structurally valid" check for a file this large/real.
        SkpFile.fromBuffer(data).parse();
      }, timeout: const Timeout.factor(4));
    }

    test('capilla_quiroz_v17.skp preserves almost all geometry', () {
      final path = '$fixturesDir/capilla_quiroz_v17.skp';
      if (!File(path).existsSync()) {
        markTestSkipped('fixture not present');
        return;
      }
      final orig = SkpFile.open(path).parse();
      final result = openExisting(path);
      final rebuilt = SkpFile.fromBuffer(result.builder.toBytes()).parse();

      final origTotal = orig.definitions.values.fold(0, (a, d) => a + d.faces.length) + orig.root.faces.length;
      final rebuiltTotal =
          rebuilt.definitions.values.fold(0, (a, d) => a + d.faces.length) + rebuilt.root.faces.length;
      // At most a handful of faces (e.g. a degenerate UV correspondence)
      // are expected to be skipped, never a large fraction - a big drop
      // would indicate silent corruption, not a legitimately-scoped gap.
      expect(rebuiltTotal, greaterThanOrEqualTo(origTotal - 5));
      expect(rebuilt.root.instances.length, orig.root.instances.length);
      expect(rebuilt.definitions.length, orig.definitions.length);
    }, timeout: const Timeout.factor(4));
  });
}
