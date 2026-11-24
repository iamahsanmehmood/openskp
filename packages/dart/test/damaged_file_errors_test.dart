import 'dart:io';
import 'dart:typed_data';

import 'package:openskp/openskp.dart';
import 'package:test/test.dart';

int _findSubsequence(Uint8List haystack, List<int> needle) {
  for (int i = 0; i + needle.length <= haystack.length; i++) {
    bool match = true;
    for (int j = 0; j < needle.length; j++) {
      if (haystack[i + j] != needle[j]) {
        match = false;
        break;
      }
    }
    if (match) return i;
  }
  return -1;
}

void main() {
  group('Damaged files raise SkpParseException (#588)', () {
    for (final fraction in [0.1, 0.5]) {
      test('truncated VFF ZIP ($fraction) raises SkpParseException', () {
        final raw = File('${Directory.current.path}/test/fixtures/SU_File.skp')
            .readAsBytesSync();
        final cut = (raw.length * fraction).toInt();
        final truncated =
            Uint8List.fromList(raw.sublist(0, cut < 64 ? 64 : cut));

        final skp = SkpFile.fromBuffer(truncated);
        for (final op in <Object Function()>[
          skp.parse,
          skp.buildScene,
          skp.buildInstancedScene,
        ]) {
          expect(
            op,
            throwsA(
              isA<SkpParseException>().having(
                (e) => e.stage,
                'stage',
                anyOf('zip_extract', 'tlv_walk'),
              ),
            ),
          );
        }
      });
    }

    test(
        'corrupted VFF entry size triggers ArchiveLimitException wrapped in SkpParseException',
        () {
      final raw = Uint8List.fromList(
        File('${Directory.current.path}/test/fixtures/SU_File.skp')
            .readAsBytesSync(),
      );
      final firstName = _findSubsequence(raw, 'model.dat'.codeUnits);
      expect(firstName, greaterThan(8));
      for (var i = 0; i < 4; i++) {
        raw[firstName - 8 + i] = i == 0 ? 1 : 0;
      }
      final tail = Uint8List.sublistView(raw, firstName + 9);
      final relSecond = _findSubsequence(tail, 'model.dat'.codeUnits);
      if (relSecond >= 22) {
        final secondName = firstName + 9 + relSecond;
        for (var i = 0; i < 4; i++) {
          raw[secondName - 22 + i] = i == 0 ? 1 : 0;
        }
      }

      final skp = SkpFile.fromBuffer(raw);
      for (final op in <Object Function()>[
        skp.parse,
        skp.buildScene,
        skp.buildInstancedScene,
      ]) {
        expect(
          op,
          throwsA(
            isA<SkpParseException>()
                .having((e) => e.stage, 'stage', 'zip_extract')
                .having((e) => e.cause, 'cause', isNotNull),
          ),
        );
      }
    });

    for (final fraction in [0.1, 0.5]) {
      test(
          'truncated classic file ($fraction) wraps RangeError/LegacyParseError into SkpParseException',
          () {
        final raw = File(
          '${Directory.current.path}/test/fixtures/capilla_quiroz_v17.skp',
        ).readAsBytesSync();
        final cut = (raw.length * fraction).toInt();
        final truncated =
            Uint8List.fromList(raw.sublist(0, cut < 64 ? 64 : cut));

        final skp = SkpFile.fromBuffer(truncated);
        for (final op in <Object Function()>[
          skp.parse,
          skp.buildScene,
          skp.buildInstancedScene,
        ]) {
          expect(
            op,
            throwsA(
              isA<SkpParseException>()
                  .having((e) => e.stage, 'stage', 'legacy_walk')
                  .having((e) => e.cause, 'cause', isNotNull),
            ),
          );
        }
      });
    }

    test('missing file raises FileSystemException instead of SkpParseException',
        () {
      final tmpDir = Directory.systemTemp.createTempSync('openskp_missing_');
      try {
        final missing = '${tmpDir.path}/does_not_exist.skp';
        expect(() => SkpFile.open(missing), throwsA(isA<FileSystemException>()));

        final vanishing = File('${tmpDir.path}/vanishing.skp');
        vanishing.writeAsBytesSync(
          File('${Directory.current.path}/test/fixtures/SU_File.skp')
              .readAsBytesSync(),
        );
        final skp = SkpFile.open(vanishing.path);
        vanishing.deleteSync();
        for (final op in <Object Function()>[
          skp.parse,
          skp.buildScene,
          skp.buildInstancedScene,
        ]) {
          expect(op, throwsA(isA<FileSystemException>()));
        }
      } finally {
        if (tmpDir.existsSync()) {
          tmpDir.deleteSync(recursive: true);
        }
      }
    });

    test('classic fixture cut at every offset returns or raises SkpParseException',
        () {
      final raw = File(
        '${Directory.current.path}/test/fixtures/legacy_v4_textured.skp',
      ).readAsBytesSync();
      for (var cut = 0; cut <= raw.length; cut++) {
        final slice = Uint8List.sublistView(raw, 0, cut);
        try {
          SkpFile.fromBuffer(slice).parse();
        } on SkpParseException {
          // Expected on truncated cuts.
        }
      }
    });

    test('partial classic root read is preserved and not hidden', () {
      final builder = create()
        ..addFace([
          (0.0, 0.0, 0.0),
          (10.0, 0.0, 0.0),
          (10.0, 10.0, 0.0),
          (0.0, 10.0, 0.0),
        ])
        ..addText('First', (0.0, 0.0, 0.0))
        ..addText('Broken', (5.0, 0.0, 0.0));

      final bytes = Uint8List.fromList(builder.toBytes());
      final at = _findSubsequence(
        bytes,
        const [0x01, 0xFF, 0xFE, 0xFF, 0x06, 0x42, 0x00],
      );
      expect(at, greaterThanOrEqualTo(0));
      for (var i = 0; i < 4; i++) {
        bytes[at + i] = 0;
      }

      final model = SkpFile.fromBuffer(bytes).parse();
      expect(model.root.texts, hasLength(1));
      expect(model.unreadRootEntities, greaterThan(0));
    });
  });
}
