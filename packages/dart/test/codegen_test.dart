import 'dart:io';
import 'dart:typed_data';

import 'package:openskp/openskp.dart';
import 'package:test/test.dart';

/// Tests for codegen.dart's toDartCode - generates Dart source that
/// rebuilds a parsed model via the writer API.
///
/// Found via diffing a real, large file (jeff.skp: 2713 definitions,
/// 113643 faces) against its own regenerated output (via the TypeScript
/// port this mirrors, toTypeScriptCode): an early prototype dropped
/// instance-level paint (95% of that file's instances) and instance names
/// entirely, and never emitted textured materials at all.
///
/// Unlike Python/TypeScript, Dart can't eval() a source string directly -
/// the tests below actually run the generated code for real (via `dart
/// run` on the generated file, in this package's own directory so
/// `package:openskp` resolves), the same way a real caller running this
/// code would.
void main() {
  final packageRoot = Directory.current.path;
  final fixturesDir = '$packageRoot/test/fixtures';

  List<int> makeFakePng() =>
      [0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A, ...List.generate(32, (i) => i)];

  /// Writes `code` to a temp .dart file (inside this package's own tree so
  /// `package:openskp` resolves) with a `main()` appended that calls
  /// `build()` and writes the result to `outPath`, runs it via `dart run`,
  /// and returns the bytes it produced.
  List<int> runGeneratedCode(String code, String outPath) {
    final tmpDir = Directory('$packageRoot/.tmp_codegen_test_${DateTime.now().microsecondsSinceEpoch}');
    tmpDir.createSync();
    try {
      final runnerPath = '${tmpDir.path}/runner.dart';
      final escapedOut = outPath.replaceAll('\\', '\\\\');
      // dart:io imported here unconditionally (not relying on the
      // generated code's own conditional import, which only appears when
      // at least one textured material needs it) - this wrapper's own
      // main() always needs File regardless of what the generated code
      // itself requires.
      File(runnerPath).writeAsStringSync('''
import 'dart:io' as _test_io;

$code

void main() {
  final bytes = build();
  _test_io.File('$escapedOut').writeAsBytesSync(bytes);
}
''');
      final result = Process.runSync('dart', ['run', runnerPath], workingDirectory: packageRoot);
      if (result.exitCode != 0) {
        fail('generated code failed to run:\nstdout: ${result.stdout}\nstderr: ${result.stderr}');
      }
      return File(outPath).readAsBytesSync();
    } finally {
      tmpDir.deleteSync(recursive: true);
    }
  }

  group('toDartCode', () {
    test('reproduces solid materials, instance-level paint, and instance names', () {
      final b = create();
      final red = b.addMaterial('Red', [255, 0, 0]);
      final box = b.addComponentDefinition('Box', (d) {
        d.addFace(
          [(0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (10.0, 10.0, 0.0), (0.0, 10.0, 0.0)],
          material: red,
        );
      });
      b.addInstance(box, translation: (0.0, 0.0, 0.0), material: red, name: 'PaintedBox');
      b.addInstance(box, translation: (50.0, 0.0, 0.0), name: 'PlainBox');

      final original = SkpFile.fromBuffer(Uint8List.fromList(b.toBytes())).parse();
      final code = toDartCode(original);

      final tmpOut = '$packageRoot/.tmp_codegen_out_${DateTime.now().microsecondsSinceEpoch}.skp';
      try {
        final regenBytes = runGeneratedCode(code, tmpOut);
        final regen = SkpFile.fromBuffer(Uint8List.fromList(regenBytes)).parse();

        expect(regen.materials.map((m) => m.name).toList(), original.materials.map((m) => m.name).toList());
        expect(regen.root.instances.length, 2);
        final byName = {for (final i in regen.root.instances) i.name: i};
        expect(byName['PaintedBox']!.materialId, isNotNull);
        expect(byName['PlainBox']!.materialId, isNull);
      } finally {
        final f = File(tmpOut);
        if (f.existsSync()) f.deleteSync();
      }
    });

    test('reproduces a genuinely empty definition name', () {
      // Found via cross-language analysis (2026-08-28), same bug class as
      // the empty-instance-name case above: `defn.name.isNotEmpty ?
      // defn.name : 'Def$defId'` silently replaced a genuinely empty
      // definition name with a fabricated one. SketchUp Groups are
      // internally just unnamed component definitions (unlike
      // Components, which SketchUp auto-names), so an empty name is
      // common in real files.
      final b = create();
      final box = b.addComponentDefinition('', (d) {
        d.addFace([(0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (10.0, 10.0, 0.0), (0.0, 10.0, 0.0)]);
      });
      b.addInstance(box, translation: (0.0, 0.0, 0.0));

      final original = SkpFile.fromBuffer(Uint8List.fromList(b.toBytes())).parse();
      expect(original.definitions.values.first.name, '');
      final code = toDartCode(original);

      final tmpOut = '$packageRoot/.tmp_codegen_out_${DateTime.now().microsecondsSinceEpoch}.skp';
      try {
        final regenBytes = runGeneratedCode(code, tmpOut);
        final regen = SkpFile.fromBuffer(Uint8List.fromList(regenBytes)).parse();

        expect(regen.definitions.values.first.name, '');
      } finally {
        final f = File(tmpOut);
        if (f.existsSync()) f.deleteSync();
      }
    });

    test('reproduces a textured material with default projection', () {
      final tmpDir = Directory.systemTemp.createTempSync('openskp_codegen_tex_');
      final pngPath = '${tmpDir.path}${Platform.pathSeparator}brick.png';
      File(pngPath).writeAsBytesSync(makeFakePng());
      final b = create();
      final tex = b.addTextureMaterial('Brick', pngPath, appliedHeight: 1.0);
      b.addFace(
        [(0.0, 0.0, 0.0), (100.0, 0.0, 0.0), (100.0, 100.0, 0.0), (0.0, 100.0, 0.0)],
        material: tex,
      );

      final original = SkpFile.fromBuffer(Uint8List.fromList(b.toBytes())).parse();
      final code = toDartCode(original);

      final tmpOut = '$packageRoot/.tmp_codegen_out_${DateTime.now().microsecondsSinceEpoch}.skp';
      try {
        final regenBytes = runGeneratedCode(code, tmpOut);
        final regen = SkpFile.fromBuffer(Uint8List.fromList(regenBytes)).parse();

        final origMat = original.materials.firstWhere((m) => m.name == 'Brick');
        final regenMat = regen.materials.firstWhere((m) => m.name == 'Brick');
        expect(regenMat.texture, isNotNull);
        expect(regenMat.texture!.data, equals(origMat.texture!.data));

        final origFace = original.root.faces.values.first;
        final regenFace = regen.root.faces.values.first;
        expect(origFace.uvTransform, isNull);
        expect(regenFace.uvTransform, isNotNull);
      } finally {
        tmpDir.deleteSync(recursive: true);
        final f = File(tmpOut);
        if (f.existsSync()) f.deleteSync();
      }
    });

    test('reproduces a textured material with an explicit UV pin', () {
      final tmpDir = Directory.systemTemp.createTempSync('openskp_codegen_tex_');
      final pngPath = '${tmpDir.path}${Platform.pathSeparator}brick.png';
      File(pngPath).writeAsBytesSync(makeFakePng());
      final b = create();
      final tex = b.addTextureMaterial('Brick', pngPath, appliedHeight: 1.0);
      b.addFace(
        [(0.0, 0.0, 0.0), (100.0, 0.0, 0.0), (100.0, 100.0, 0.0), (0.0, 100.0, 0.0)],
        material: tex,
        frontUv: [
          ((0.0, 0.0, 0.0), (0.0, 0.0)),
          ((100.0, 0.0, 0.0), (1.0, 0.0)),
          ((0.0, 100.0, 0.0), (0.0, 1.0)),
        ],
      );

      final original = SkpFile.fromBuffer(Uint8List.fromList(b.toBytes())).parse();
      final code = toDartCode(original);

      final tmpOut = '$packageRoot/.tmp_codegen_out_${DateTime.now().microsecondsSinceEpoch}.skp';
      try {
        final regenBytes = runGeneratedCode(code, tmpOut);
        final regen = SkpFile.fromBuffer(Uint8List.fromList(regenBytes)).parse();

        expect(regen.root.faces.length, 1);
        final origFace = original.root.faces.values.first;
        final regenFace = regen.root.faces.values.first;
        expect(regenFace.uvTransform, equals(origFace.uvTransform));
      } finally {
        tmpDir.deleteSync(recursive: true);
        final f = File(tmpOut);
        if (f.existsSync()) f.deleteSync();
      }
    });

    test('reproduces a textured material with applied width, height, and opacity', () {
      final tmpDir = Directory.systemTemp.createTempSync('openskp_codegen_tex_');
      final pngPath = '${tmpDir.path}${Platform.pathSeparator}brick.png';
      File(pngPath).writeAsBytesSync(makeFakePng());
      final b = create();
      final tex = b.addTextureMaterial('Brick', pngPath, appliedHeight: 12.0, appliedWidth: 24.0, opacity: 0.7);
      b.addFace(
        [(0.0, 0.0, 0.0), (100.0, 0.0, 0.0), (100.0, 100.0, 0.0), (0.0, 100.0, 0.0)],
        material: tex,
      );

      final original = SkpFile.fromBuffer(Uint8List.fromList(b.toBytes())).parse();
      final code = toDartCode(original);
      expect(code, contains('appliedHeight: 12.0'));
      expect(code, contains('appliedWidth: 24.0'));
      expect(code, contains('opacity: 0.7'));

      final tmpOut = '$packageRoot/.tmp_codegen_out_${DateTime.now().microsecondsSinceEpoch}.skp';
      try {
        final regenBytes = runGeneratedCode(code, tmpOut);
        final regen = SkpFile.fromBuffer(Uint8List.fromList(regenBytes)).parse();

        final regenMat = regen.materials.firstWhere((m) => m.name == 'Brick');
        expect(regenMat.texture, isNotNull);
        expect(regenMat.texture!.width, closeTo(24.0, 1e-4));
        expect(regenMat.texture!.height, closeTo(12.0, 1e-4));
        expect(regenMat.transparency, closeTo(0.7, 1e-4));
      } finally {
        tmpDir.deleteSync(recursive: true);
        final f = File(tmpOut);
        if (f.existsSync()) f.deleteSync();
      }
    });

    test('preserves original and empty texture filenames without embedding temp folder', () {
      bool containsBytes(List<int> haystack, List<int> needle) {
        if (needle.isEmpty || haystack.length < needle.length) return false;
        for (var i = 0; i <= haystack.length - needle.length; i++) {
          var ok = true;
          for (var j = 0; j < needle.length; j++) {
            if (haystack[i + j] != needle[j]) {
              ok = false;
              break;
            }
          }
          if (ok) return true;
        }
        return false;
      }

      final tempRoot = Directory.systemTemp.path.replaceAll(RegExp(r'[/\\]+$'), '');
      final asciiNeedle = tempRoot.codeUnits;
      final utf16Needle = <int>[
        for (final unit in tempRoot.codeUnits) ...[unit & 0xff, (unit >> 8) & 0xff],
      ];

      final tmpDir = Directory.systemTemp.createTempSync('openskp_codegen_667_');
      final pngPath = '${tmpDir.path}${Platform.pathSeparator}staged.png';
      File(pngPath).writeAsBytesSync(makeFakePng());
      final b = create();
      final m1 = b.addTextureMaterial(
        'Concrete',
        pngPath,
        filename: 'Polished Concrete Old.jpg',
      );
      final m2 = b.addTextureMaterial('Nameless', pngPath, filename: '');
      b.addFace(
        [(0.0, 0.0, 0.0), (100.0, 0.0, 0.0), (100.0, 100.0, 0.0), (0.0, 100.0, 0.0)],
        material: m1,
      );
      b.addFace(
        [(200.0, 0.0, 0.0), (300.0, 0.0, 0.0), (300.0, 100.0, 0.0), (200.0, 100.0, 0.0)],
        material: m2,
      );
      final original = SkpFile.fromBuffer(Uint8List.fromList(b.toBytes())).parse();
      final idx = original.materials.indexWhere((m) => m.name == 'Nameless');
      final oldMat = original.materials[idx];
      final replaced = Material(
        name: oldMat.name,
        color: oldMat.color,
        transparency: oldMat.transparency,
        id: oldMat.id,
        colorized: oldMat.colorized,
        colorizeType: oldMat.colorizeType,
        texture: Texture(
          filename: '',
          width: oldMat.texture!.width,
          height: oldMat.texture!.height,
          data: oldMat.texture!.data,
        ),
      );
      original.materials[idx] = replaced;
      if (oldMat.id != null) {
        original.materialsById[oldMat.id!] = replaced;
      }
      final code = toDartCode(original);
      expect(code, contains("filename: 'Polished Concrete Old.jpg'"));
      expect(code, contains("filename: ''"));

      final tmpOut = '$packageRoot/.tmp_codegen_667_${DateTime.now().microsecondsSinceEpoch}.skp';
      try {
        final regenBytes = runGeneratedCode(code, tmpOut);
        expect(containsBytes(regenBytes, asciiNeedle), isFalse);
        expect(containsBytes(regenBytes, utf16Needle), isFalse);
        final regen = SkpFile.fromBuffer(Uint8List.fromList(regenBytes)).parse();
        expect(
          regen.materials.firstWhere((m) => m.name == 'Concrete').texture!.filename,
          'Polished Concrete Old.jpg',
        );
        expect(
          regen.materials.firstWhere((m) => m.name == 'Nameless').texture!.filename,
          'Nameless.png',
        );
      } finally {
        tmpDir.deleteSync(recursive: true);
        final f = File(tmpOut);
        if (f.existsSync()) f.deleteSync();
      }
    });

    test('generated code preserves scaled texture dimensions and pinned UVs', () {
      final tmpDir = Directory.systemTemp.createTempSync('openskp_codegen_uv_');
      final tmpOut = '$packageRoot/.tmp_codegen_uv_${DateTime.now().microsecondsSinceEpoch}.skp';
      try {
        final pngPath = '${tmpDir.path}/tex.png';
        File(pngPath).writeAsBytesSync([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A, 1, 2, 3, 4]);
        final b = create();
        final frontTex = b.addTextureMaterial('FrontTex', pngPath, appliedHeight: 20.0, appliedWidth: 10.0, filename: 'tex.png');
        final backTex = b.addTextureMaterial('BackTex', pngPath, appliedHeight: 8.0, appliedWidth: 4.0, filename: 'tex.png');
        b.addFace(
          [(50.0, 0.0, 0.0), (50.0, 40.0, 0.0), (0.0, 40.0, 0.0), (0.0, 0.0, 0.0)],
          material: frontTex,
          backMaterial: backTex,
          frontUv: [
            ((0.0, 0.0, 0.0), (0.0, 0.0)),
            ((50.0, 0.0, 0.0), (2.5, 0.0)),
            ((0.0, 40.0, 0.0), (0.0, 1.0)),
          ],
          backUv: [
            ((0.0, 0.0, 0.0), (0.25, 0.5)),
            ((50.0, 0.0, 0.0), (2.75, 0.5)),
            ((0.0, 40.0, 0.0), (0.25, 2.5)),
          ],
        );
        final orig = SkpFile.fromBuffer(b.toBytes()).parse();
        final regenBytes = runGeneratedCode(toDartCode(orig), tmpOut);
        final regen = SkpFile.fromBuffer(Uint8List.fromList(regenBytes)).parse();

        final fm = regen.materials.firstWhere((m) => m.name == 'FrontTex');
        final bm = regen.materials.firstWhere((m) => m.name == 'BackTex');
        expect((fm.texture!.width, fm.texture!.height), (10.0, 20.0));
        expect((bm.texture!.width, bm.texture!.height), (4.0, 8.0));

        final origFace = orig.root.faces.values.single;
        final regenFace = regen.root.faces.values.single;
        for (var i = 0; i < 9; i++) {
          expect(regenFace.uvTransform![i], closeTo(origFace.uvTransform![i], 1e-3));
          expect(regenFace.uvTransformBack![i], closeTo(origFace.uvTransformBack![i], 1e-3));
        }
      } finally {
        tmpDir.deleteSync(recursive: true);
        final f = File(tmpOut);
        if (f.existsSync()) f.deleteSync();
      }
    });
  });

  // single_material_v17.skp is deliberately excluded: it declares one
  // material used by zero faces anywhere - a real file the reader parses
  // fine, but not one toBytes() can ever re-save (this writer requires at
  // least one face), independent of anything toDartCode does.
  const realFixtures = ['SU_File.skp', 'Untitled.skp', 'capilla_quiroz_v17.skp', 'gondola_v20.skp'];

  group('toDartCode - real fixtures', () {
    for (final name in realFixtures) {
      test("reproduces $name's materials, layers, instance paint/names", () {
        final original = SkpFile.fromBuffer(Uint8List.fromList(File('$fixturesDir/$name').readAsBytesSync())).parse();
        final code = toDartCode(original);

        final tmpOut = '$packageRoot/.tmp_codegen_out_${DateTime.now().microsecondsSinceEpoch}.skp';
        try {
          final regenBytes = runGeneratedCode(code, tmpOut);
          final regen = SkpFile.fromBuffer(Uint8List.fromList(regenBytes)).parse();

          final origMatNames = original.materials.map((m) => m.name).toList()..sort();
          final regenMatNames = regen.materials.map((m) => m.name).toList()..sort();
          expect(regenMatNames, origMatNames);

          final origLayerNames = original.layers.map((l) => l.name).toList()..sort();
          final regenLayerNames = regen.layers.map((l) => l.name).toList()..sort();
          expect(regenLayerNames, origLayerNames);

          String instKey(Instance i) => '${i.name} ${i.materialId != null}';
          final origKeys = original.root.instances.map(instKey).toList()..sort();
          final regenKeys = regen.root.instances.map(instKey).toList()..sort();
          expect(regenKeys, origKeys);
        } finally {
          final f = File(tmpOut);
          if (f.existsSync()) f.deleteSync();
        }
      }, timeout: const Timeout(Duration(seconds: 60)));
    }
  });
}
