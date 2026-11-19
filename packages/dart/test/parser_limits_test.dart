import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:archive/archive.dart';
import 'package:openskp/openskp.dart';
import 'package:openskp/src/tlv.dart';
import 'package:openskp/src/vff.dart';
import 'package:test/test.dart';

/// Hostile-input limits. A model.dat that nests TLV containers tens of
/// thousands of levels deep overflowed the stack (StackOverflowError, which
/// is not an Exception and so escaped every handler); a meta.dat wrapped
/// 100,000 times did the same; a truncated model.dat parsed as a model with
/// no geometry and no error.

Uint8List record(int tag0, int tag1, List<int> payload) {
  final out = Uint8List(6 + payload.length);
  out[0] = tag0;
  out[1] = tag1;
  ByteData.sublistView(out).setUint32(2, payload.length, Endian.little);
  out.setRange(6, out.length, payload);
  return out;
}

/// `levels` records of the tag, each inside the previous, around [leaf].
/// Built in one pass: wrapping level by level would copy the payload each
/// time and take quadratic time for the 100,000-level cases.
Uint8List nested(int tag0, int tag1, int levels, [List<int> leaf = const []]) {
  final out = Uint8List(6 * levels + leaf.length);
  final view = ByteData.sublistView(out);
  for (var i = 0; i < levels; i++) {
    out[6 * i] = tag0;
    out[6 * i + 1] = tag1;
    view.setUint32(6 * i + 2, leaf.length + 6 * (levels - 1 - i), Endian.little);
  }
  out.setRange(6 * levels, out.length, leaf);
  return out;
}

Uint8List skp(Uint8List modelDat, [Uint8List? metaDat]) {
  final archive = Archive();
  archive.add(ArchiveFile.bytes('model.dat', modelDat));
  if (metaDat != null) archive.add(ArchiveFile.bytes('meta/meta.dat', metaDat));
  final zip = ZipEncoder().encodeBytes(archive);
  final header = Uint8List(32)..setRange(0, 4, [0xFF, 0xFE, 0xFF, 0x0E]);
  return Uint8List.fromList([...header, ...zip]);
}

String fixture(String name) => '${Directory.current.path}/test/fixtures/$name';

Uint8List fixtureModelDat(String name) {
  final data = File(fixture(name)).readAsBytesSync();
  final zip = Vff.openZip(data, Vff.findZipOffset(data));
  return Uint8List.fromList(zip.findFile('model.dat')!.content as List<int>);
}

Matcher parseError(String contains_) => throwsA(isA<SkpParseException>().having(
    (e) => e.message, 'message', contains(contains_)));

final inches = record(0x6D, 0x00, utf8.encode('Inches'));

void main() {
  group('TLV nesting depth', () {
    test('follows the deepest nesting the limit allows', () {
      final data = nested(0xF4, 0x01, Tlv.maxDepth);
      expect(Tlv.parseRecursive(data, 0, data.length), hasLength(1));
    });

    for (final levels in [Tlv.maxDepth + 2, 5000, 200000]) {
      test('rejects $levels levels with a parse error', () {
        final data = nested(0xF4, 0x01, levels);
        expect(
          () => Tlv.parseRecursive(data, 0, data.length),
          throwsA(isA<SkpParseException>()
              .having((e) => e.stage, 'stage', 'tlv_walk')
              .having((e) => e.tag, 'tag', 'F401')
              .having((e) => e.message, 'message', contains('nest deeper'))),
        );
      });

      test('SkpFile rejects $levels levels', () {
        final model = record(0xF4, 0x01, nested(0xF4, 0x01, levels));
        expect(() => SkpFile.fromBuffer(skp(model)).parse(),
            parseError('nest deeper'));
      });
    }

    test('parses moderate nesting', () {
      final model = record(0xF4, 0x01, nested(0xF4, 0x01, 40));
      expect(SkpFile.fromBuffer(skp(model)).parse().units, isNull);
    });
  });

  group('meta.dat wrappers', () {
    test('reads the units from inside a few wrappers', () {
      var meta = inches;
      for (var i = 0; i < 3; i++) {
        meta = record(0x64, 0x00, meta);
      }
      expect(Vff.readMetaUnits(meta), 'Inches');
    });

    for (final levels in [Tlv.maxDepth + 5, 100000]) {
      test('gives unknown units for $levels wrappers without recursing', () {
        expect(Vff.readMetaUnits(nested(0x64, 0x00, levels, inches)), isNull);
      });
    }

    test('does not fail the whole parse', () {
      final meta = nested(0x64, 0x00, 100000, inches);
      final model = SkpFile.fromBuffer(
          skp(record(0xF4, 0x01, const []), meta)).parse();
      expect(model.units, isNull);
    });
  });

  group('truncated model.dat', () {
    for (final name in ['SU_File.skp', 'Untitled.skp']) {
      test('$name ends exactly on its top-level record', () {
        final model = fixtureModelDat(name);
        expect(6 + ByteData.sublistView(model).getUint32(2, Endian.little),
            model.length);
        expect(() => SkpFile.open(fixture(name)).parse(), returnsNormally);
      });
    }

    for (final keep in [0.5, 0.99]) {
      test('a model.dat cut to $keep of its length is an error', () {
        final model = fixtureModelDat('SU_File.skp');
        final cut = Uint8List.sublistView(model, 0, (model.length * keep).floor());
        expect(
          () => SkpFile.fromBuffer(skp(cut)).parse(),
          throwsA(isA<SkpParseException>()
              .having((e) => e.message, 'message', contains('truncated'))
              .having((e) => e.stage, 'stage', 'tlv_walk')
              .having((e) => e.tag, 'tag', 'F401')
              .having((e) => e.offset, 'offset', 0)),
        );
      });
    }

    test('ignores trailing bytes shorter than a record header', () {
      final model = fixtureModelDat('SU_File.skp');
      final padded = Uint8List(model.length + 3)..setRange(0, model.length, model);
      expect(() => SkpFile.fromBuffer(skp(padded)).parse(), returnsNormally);
    });

    test('still parses an empty model.dat', () {
      expect(() => SkpFile.fromBuffer(skp(Uint8List(0))).parse(), returnsNormally);
    });
  });
}
