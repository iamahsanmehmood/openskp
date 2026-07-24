import 'dart:io';
import 'dart:typed_data';

import 'package:openskp/openskp.dart';
import 'package:test/test.dart';

/// openskp exposes progress via an optional ParseOptions (onProgress/onLog)
/// - silent by default - and throws SkpParseException with structured
/// location context (stage, recordIndex, tag, ...) on failure, so a
/// production pipeline can trace exactly where a model got stuck instead of
/// a bare stack trace.
void main() {
  final fixturePath = '${Directory.current.path}/test/fixtures/capilla_quiroz_v17.skp';

  test('is silent by default (no options)', () {
    final model = SkpFile.open(fixturePath).parse();
    expect(model.version, '{17.0.18899}');
  });

  test('reports onLog messages for a real parse', () {
    final messages = <(SkpLogLevel, String)>[];
    final options = ParseOptions(onLog: (level, message) => messages.add((level, message)));

    SkpFile.open(fixturePath).parse(options);

    expect(messages.any((m) => m.$2.contains('Parsing legacy')), isTrue);
    expect(messages.any((m) => m.$2.contains('Parse complete')), isTrue);
  });

  test('reports onLog messages for buildScene', () {
    final messages = <(SkpLogLevel, String)>[];
    final options = ParseOptions(onLog: (level, message) => messages.add((level, message)));

    SkpFile.open(fixturePath).buildScene(options);

    expect(messages.any((m) => m.$2.contains('Building scene')), isTrue);
    expect(messages.any((m) => m.$2.contains('Scene build complete')), isTrue);
  });

  test('throws SkpParseException with stage="header" for a corrupt file', () {
    final bad = Uint8List.fromList(List.filled(200, 0x41)); // "AAAA..." - not a valid header
    final skp = SkpFile.fromBuffer(bad);

    expect(() => skp.parse(), throwsA(isA<SkpParseException>()));
    try {
      skp.parse();
      fail('expected parse() to throw');
    } on SkpParseException catch (e) {
      expect(e.stage, 'header');
    }
  });

  test('SkpParseException message includes structured context', () {
    final err = SkpParseException('boom', stage: 'tlv_walk', recordIndex: 3, totalRecords: 10, tag: 'F601');
    final text = err.toString();
    expect(text, contains('stage=tlv_walk'));
    expect(text, contains('record=3/10'));
    expect(text, contains('tag=F601'));
  });

  test('SkpParseException preserves the original error as cause', () {
    final original = StateError('inner failure');
    final wrapped = SkpParseException('wrapped', stage: 'tlv_walk', cause: original);
    expect(wrapped.cause, same(original));
  });
}
