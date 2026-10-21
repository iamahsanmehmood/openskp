import 'dart:io';
import 'dart:typed_data';

import 'package:openskp/openskp.dart';
import 'package:openskp/src/legacy.dart';
import 'package:test/test.dart';

/// Regression tests for SketchUp 4, 5 and 2019 files - port of the Python
/// reader's fixes (openskp#284, #440) to this reader.
///
/// Fixtures are small synthetic models, saved down through the installed
/// SketchUp library from the same sources as the existing legacy fixtures
/// (legacy_v6_synthetic.skp and the one-face textured model behind
/// legacy_v3_textured.skp); no private content:
///
/// * legacy_v4/v5/v2019_synthetic.skp: 4 definitions (InnerFrame, Frame,
///   Panel, Post), 10 root instances, a "Roof" layer, 24 materials.
/// * legacy_v4/v5_textured.skp: one painted face with an 8x8 PNG, stored as a
///   bare inline image before SketchUp 6 (not a CDib object).
/// * legacy_v2020_textured.skp: the same model at SketchUp 2020 - a model with
///   NO component definitions, whose root entities used to be read as
///   "definitions" and silently dropped.
///
/// Parsing without an exception is not enough (an early V3 attempt
/// "succeeded" with one definition of four), so these assert parsed structure
/// and cross-check it against the V6 file from the same source.

/// One real CDimensionLinear record per version, taken from the repository's
/// own Untitled.skp re-saved at SketchUp 4, 5 and 6 (record bytes only - from
/// the entity preamble to the end of the trailing block). All three describe
/// the same dimension, so each must be consumed exactly and decode to the same
/// text.
const _dimRecords = <int, String>{
  4:
      '00000f0000010100001d00fffeff000100000004000000000000000000000000'
      '0000000000000000000000000000000000000000000000000000000100000004'
      '00000000000000000000007e210ac0556f564000000000000000000000000000'
      '00000000000000000000000000f0bf0000000000000000000000000000000000'
      '00000000000080000000000000f0bf000000000000008001000000b95c2e97cb'
      'e512c00000000000000000',
  5:
      '00000f000001010000001d00fffeff0001000000040000000000000000000000'
      '0000000000000000000000000000000000000000000000000000000001000000'
      '0400000000000000000000007e210ac0556f5640000000000000000000000000'
      '0000000000000000000000000000f0bf00000000000000000000000000000000'
      '0000000000000080000000000000f0bf000000000000008001000000b95c2e97'
      'cbe512c00000000000000000',
  6:
      '00000f000001010000001d00fffeff00ffff0100070043536b466f6e740000ff'
      'feff065400610068006f006d00610000000c0000000000000000000024400003'
      '0000000100000004000000000000000000000000000000000000000000000000'
      '000000000000000000000000000000010000000400000000000000000000007e'
      '210ac0556f564000000000000000000000000000000000000000000000000000'
      '00f0bf0000000000000000000000000000000000000000000000800000000000'
      '00f0bf000000000000008001000000b95c2e97cbe512c0000000000000000003'
      '000000',
};

Uint8List _hex(String s) => Uint8List.fromList([
      for (var i = 0; i < s.length; i += 2) int.parse(s.substring(i, i + 2), radix: 16)
    ]);

void main() {
  String path(String name) => '${Directory.current.path}/test/fixtures/$name';
  SkpModel parse(String name) => SkpFile.open(path(name)).parse();

  String round(Iterable<double> xs, [int n = 5]) =>
      xs.map((x) => x.toStringAsFixed(n)).join(',');

  /// Everything that matters about a model, keyed by names and coordinates
  /// (ids differ between versions).
  String canonical(SkpModel m, bool instanceNames) {
    final matName = {for (final x in m.materials) x.id: x.name};
    String defName(int? idx) => m.definitions[idx]?.name ?? '?';
    String inst(Instance i) =>
        '${defName(i.refIdx)}|${round(i.matrix)}|${instanceNames ? i.name : ''}';
    final sb = StringBuffer();
    sb.write('layers:${(m.layers.map((l) => l.name).toList()..sort()).join(',')};');
    sb.write('materials:${(m.materials.map((x) => x.name).toList()..sort()).join(',')};');
    final defs = m.definitions.values.toList()..sort((a, b) => a.name.compareTo(b.name));
    for (final d in defs) {
      final verts = d.vertices.values.map((v) => round([v.x, v.y, v.z], 6)).toList()..sort();
      final faces = d.faces.values
          .map((f) => '${matName[f.materialId] ?? '-'}/${f.hidden}')
          .toList()
        ..sort();
      final insts = d.instances.map(inst).toList()..sort();
      sb.write('def ${d.name}:v=${verts.join(';')} e=${d.edges.length} '
          'f=${faces.join(';')} i=${insts.join(';')};');
    }
    final root = m.root.instances.map(inst).toList()..sort();
    sb.write('root:${root.join(';')}');
    return sb.toString();
  }

  const synthetic = [
    ['legacy_v4_synthetic.skp', '{4.0.1}', false], // SketchUp 4 stores no instance names
    ['legacy_v5_synthetic.skp', '{5.0.1}', true],
    ['legacy_v2019_synthetic.skp', '{19.0.1}', true],
  ];

  group('SketchUp 4, 5 and 2019 synthetic model', () {
    for (final row in synthetic) {
      final file = row[0] as String;
      final version = row[1] as String;
      final names = row[2] as bool;

      group(file, () {
        test('is detected as legacy and reports its version', () {
          expect(Legacy.isLegacy(File(path(file)).readAsBytesSync()), isTrue);
          expect(parse(file).version, version);
        });

        test('matches the V6 file from the same source', () {
          expect(canonical(parse(file), names),
              canonical(parse('legacy_v6_synthetic.skp'), names));
        });

        test('is complete, not just error-free', () {
          final m = parse(file);
          final defs = {for (final d in m.definitions.values) d.name: d};
          expect(defs.keys.toList()..sort(), ['Frame', 'InnerFrame', 'Panel', 'Post']);
          for (final d in defs.values) {
            expect([d.faces.length, d.edges.length, d.vertices.length], [1, 4, 4]);
          }
          expect(m.root.instances.length, 10);
          expect(m.materials.length, 24);
          final layers = m.layers.map((l) => l.name).toSet();
          expect(layers.containsAll(['Layer0', 'Roof']), isTrue);
          expect(defs['Panel']!.instances.map((i) => m.definitions[i.refIdx]!.name),
              ['InnerFrame']);
        });
      });
    }

    test('SketchUp 4 has no instance names and SketchUp 5 does', () {
      final v6 = parse('legacy_v6_synthetic.skp');
      expect(v6.root.instances.any((i) => i.name.isNotEmpty), isTrue);
      expect(parse('legacy_v4_synthetic.skp').root.instances.every((i) => i.name == ''),
          isTrue);
      expect(parse('legacy_v5_synthetic.skp').root.instances.map((i) => i.name).toList(),
          v6.root.instances.map((i) => i.name).toList());
    });
  });

  group('textured models', () {
    Texture? textureOf(SkpModel m) {
      for (final x in m.materials) {
        if (x.texture?.data != null) return x.texture;
      }
      return null;
    }

    test('SketchUp 4 and 5 read the bare inline texture (was: not a dib)', () {
      final reference = textureOf(parse('legacy_v3_textured.skp'))!;
      for (final file in ['legacy_v4_textured.skp', 'legacy_v5_textured.skp']) {
        final m = parse(file);
        final tex = textureOf(m);
        expect(tex, isNotNull);
        expect(tex!.data, reference.data); // the same PNG, byte for byte
        expect(tex.data!.sublist(0, 4), [0x89, 0x50, 0x4E, 0x47]);
        expect(m.root.faces.length, 1);
      }
    });

    test('a SketchUp 2020 model without definitions keeps its root entities', () {
      // The root entity count was handed over as a "definition count", so the
      // face and its four edges were consumed as definitions and the model
      // came out empty - with no error.
      final m = parse('legacy_v2020_textured.skp');
      expect([m.root.faces.length, m.root.edges.length, m.root.vertices.length],
          [1, 4, 4]);
      expect(textureOf(m), isNotNull);
    });
  });

  group('dimension record layout', () {
    ({DimLinearRec rec, int consumed, int size}) readDimension(int ver) {
      final record = _hex(_dimRecords[ver]!);
      final data = Uint8List.fromList([
        ...record,
        0xFF, 0x7F, 0x00, 0x00, 0x00, 0x80, // the next entity's big class-ref tag
      ]);
      final ar = Archive(data, ver);
      ar.readers.addAll(LegacyReaders.readers);
      ar.nextSlot = 100;
      ar.walkBase = 100;
      final r = LR(data, 0);
      final rec = LegacyReaders.readDimLinear(ar, r) as DimLinearRec;
      return (rec: rec, consumed: r.pos, size: record.length);
    }

    for (final ver in [4, 5, 6]) {
      test('SketchUp $ver: the record is consumed exactly', () {
        final d = readDimension(ver);
        expect(d.consumed, d.size);
      });
    }

    test('SketchUp 4 and 5 decode the same dimension as 6', () {
      final want = readDimension(6).rec.text;
      for (final ver in [4, 5]) {
        expect(readDimension(ver).rec.text, want);
      }
    });
  });
}
