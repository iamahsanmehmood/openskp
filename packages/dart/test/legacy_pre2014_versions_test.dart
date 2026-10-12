import 'dart:io';

import 'package:openskp/openskp.dart';
import 'package:openskp/src/legacy.dart';
import 'package:test/test.dart';

/// Regression tests for the pre-2014 legacy layouts found while chasing
/// openskp#284 - ports of the Python reader's fixes (#310 instance GUID gate,
/// #385 CAttributeNamed trailer, #407 SketchUp 3 layout) to this reader
/// (openskp#410).
///
/// Fixtures are one small synthetic model (4 component definitions
/// InnerFrame / Frame / Panel / Post; InnerFrame placed as a nested group
/// inside Panel; 10 root instances; a custom "Roof" layer; Frame's face
/// painted flat red) built with the OpenSKP writer. The v6 file is a real
/// SketchUp version-downgrade export of it; the v3, v7 and 2014 files are that
/// v6 file re-saved at each version through the installed SketchUp library, so
/// they are small (8-12 KB) and carry no default-template materials or
/// images. No private content.
///
/// Each of these used to either throw or - worse - "succeed" with most of
/// the model silently missing, so the tests assert parsed structure, not
/// just the absence of an exception.
void main() {
  String path(String name) => '${Directory.current.path}/test/fixtures/$name';
  SkpModel parse(String name) => SkpFile.open(path(name)).parse();
  Map<String, Definition> byName(SkpModel m) =>
      {for (final d in m.definitions.values) d.name: d};
  List<String> instanceNames(SkpModel m) =>
      m.root.instances.map((i) => i.name).toList();
  List<String> sorted(Iterable<String> xs) => xs.toList()..sort();

  const siblingNames = [
    '',
    'P-1',
    'P-2',
    'P-3',
    'P-4',
    'P-5',
    'P-6',
    'P-7',
    'P-8',
    'P-9',
  ];

  group('pre-2014 instance GUID gate (version >= 14, not class schema)', () {
    test('detects the fixtures as legacy containers', () {
      expect(
          Legacy.isLegacy(
              File(path('legacy_v7_synthetic.skp')).readAsBytesSync()),
          isTrue);
      expect(
          Legacy.isLegacy(
              File(path('legacy_v2014_synthetic.skp')).readAsBytesSync()),
          isTrue);
    });

    test('v7 parses and places all 10 root instances', () {
      // Was: class-ref to non-class slot, or silently truncated to 1.
      final model = parse('legacy_v7_synthetic.skp');
      expect(model.version, '{7.0.1}');
      expect(instanceNames(model), siblingNames);
    });

    test('v7 and 2014 parse to the same structure', () {
      final v7 = parse('legacy_v7_synthetic.skp');
      final v14 = parse('legacy_v2014_synthetic.skp');
      expect(v7.definitions.length, 4);
      expect(v14.definitions.length, 4);
      expect(instanceNames(v7), instanceNames(v14));
      final d7 = byName(v7);
      final d14 = byName(v14);
      expect(sorted(d7.keys), ['Frame', 'InnerFrame', 'Panel', 'Post']);
      expect(sorted(d14.keys), ['Frame', 'InnerFrame', 'Panel', 'Post']);
      d7.forEach((name, def) {
        expect(def.faces.length, d14[name]!.faces.length);
        expect(def.instances.map((i) => i.name).toList(),
            d14[name]!.instances.map((i) => i.name).toList());
      });
    });

    test('v7 keeps the nested group inside Panel', () {
      final panel = byName(parse('legacy_v7_synthetic.skp'))['Panel']!;
      expect(panel.instances.map((i) => i.name).toList(), ['InnerFrame']);
    });

    test('2014 files still read their trailing instance GUIDs', () {
      final model = parse('legacy_v2014_synthetic.skp');
      expect(model.version, '{14.0.1}');
      expect(model.root.instances.length, 10);
    });
  });

  group('SketchUp 6 CAttributeNamed has no trailing field', () {
    test('v6 parses', () {
      // Was: back-ref to unwalked slot 256.
      final model = parse('legacy_v6_synthetic.skp');
      expect(model.version, '{6.0.1}');
      expect(instanceNames(model), siblingNames);
      expect(
          sorted(byName(model).keys), ['Frame', 'InnerFrame', 'Panel', 'Post']);
      expect(byName(model)['Panel']!.instances.map((i) => i.name).toList(),
          ['InnerFrame']);
    });
  });

  group('SketchUp 3 layout', () {
    test('parses every definition with its geometry', () {
      // Was: expected a string record.
      final model = parse('legacy_v3_synthetic.skp');
      expect(model.version, '{3.0.1}');
      final defs = byName(model);
      expect(sorted(defs.keys), ['Frame', 'InnerFrame', 'Panel', 'Post']);
      for (final d in defs.values) {
        expect([d.faces.length, d.edges.length, d.vertices.length], [1, 4, 4]);
      }
      final frame = defs['Frame']!;
      expect(frame.vertices.values.map((v) => v.x).toSet().toList()..sort(),
          [0.0, 20.0]);
      expect(frame.vertices.values.map((v) => v.y).toSet().toList()..sort(),
          [0.0, 20.0]);
    });

    test('places every root instance', () {
      final model = parse('legacy_v3_synthetic.skp');
      expect(model.root.instances.length, 10);
      const expected = [
        'Frame',
        'Panel',
        'Post',
        'Frame',
        'Panel',
        'Post',
        'Frame',
        'Panel',
        'Post',
        'Frame',
      ];
      expect(
          model.root.instances
              .map((i) => model.definitions[i.refIdx]!.name)
              .toList(),
          expected);
    });

    test('places the nested group inside Panel', () {
      final model = parse('legacy_v3_synthetic.skp');
      final panel = byName(model)['Panel']!;
      expect(
          panel.instances
              .map((i) => model.definitions[i.refIdx]!.name)
              .toList(),
          ['InnerFrame']);
    });

    test('takes a face material from its own leading pointer', () {
      // SketchUp 3's face opens with its FRONT MATERIAL pointer, not an
      // attribute container: Frame's face was painted red, the others not.
      final model = parse('legacy_v3_synthetic.skp');
      final defs = byName(model);
      final painted = defs['Frame']!.faces.values.first;
      expect(painted.materialId, isNotNull);
      final red = model.materialsById[painted.materialId]!;
      expect(red.name, 'Red');
      expect([red.color.$1, red.color.$2, red.color.$3], [255, 0, 0]);
      for (final name in ['Panel', 'Post', 'InnerFrame']) {
        expect(defs[name]!.faces.values.first.materialId, isNull);
      }
      for (final d in model.definitions.values) {
        for (final f in d.faces.values) {
          expect(f.hidden, isFalse);
        }
      }
    });

    test('keeps the custom layer', () {
      final names =
          parse('legacy_v3_synthetic.skp').layers.map((l) => l.name).toList();
      expect(names, contains('Layer0'));
      expect(names, contains('Roof'));
    });

    test('matches the v6 file built from the same source', () {
      final v3 = parse('legacy_v3_synthetic.skp');
      final v6 = parse('legacy_v6_synthetic.skp');
      final d3 = byName(v3);
      final d6 = byName(v6);
      expect(sorted(d3.keys), sorted(d6.keys));
      d3.forEach((name, a) {
        final b = d6[name]!;
        expect([a.faces.length, a.edges.length, a.vertices.length],
            [b.faces.length, b.edges.length, b.vertices.length]);
      });
      expect(v3.root.instances.length, v6.root.instances.length);
      for (var i = 0; i < v3.root.instances.length; i++) {
        final a = v3.root.instances[i];
        final b = v6.root.instances[i];
        expect(v3.definitions[a.refIdx]!.name, v6.definitions[b.refIdx]!.name);
        expect(a.matrix.length, b.matrix.length);
        for (var k = 0; k < a.matrix.length; k++) {
          expect((a.matrix[k] - b.matrix[k]).abs(), lessThan(1e-9));
        }
      }
    });
  });

  group('SketchUp 3 single material (slot-base probe path)', () {
    // A V3 file with ONE material cannot use the two-material bootstrap; the
    // probe used to read a layer count V3 does not write and rejected every
    // such file ("implausible layer count in base probe"). Fixture: one 20x20
    // face painted with "Checker", an 8x8 PNG generated by this project (no
    // third-party image), saved down to SketchUp 3. 4 KB, no default template.
    test('parses its one material, inline PNG, and the painted face', () {
      final model = parse('legacy_v3_textured.skp');
      expect(model.version, '{3.0.1}');
      expect(model.materials.map((m) => m.name).toList(), ['Checker']);
      final data = model.materials.first.texture!.data!;
      expect(data.sublist(0, 8), [0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A]);
      expect(data.sublist(data.length - 8),
          [0x49, 0x45, 0x4E, 0x44, 0xAE, 0x42, 0x60, 0x82]);
      final faces = [model.root, ...model.definitions.values]
          .expand((d) => d.faces.values)
          .toList();
      expect(faces.length, 1);
      expect(faces.first.materialId, model.materials.first.id);
    });
  });
}
