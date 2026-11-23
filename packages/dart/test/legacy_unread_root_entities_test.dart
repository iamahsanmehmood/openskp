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

/// The classic root reader keeps what it read before the first record it can't
/// parse and drops the rest of the root. [SkpModel.unreadRootEntities] says
/// how many entities that dropped and [ParseOptions.onLog] emits a
/// [SkpLogLevel.warning] when it is nonzero, so a partial read isn't silent.
void main() {
  test('complete files report 0 and emit no warning', () {
    for (final name in [
      'Untitled.skp',
      'capilla_quiroz_v17.skp',
      'gondola_v20.skp',
      'legacy_text_arrows_2017.skp',
      'nesting_2017.skp',
      'SU_File.skp',
    ]) {
      final path = '${Directory.current.path}/test/fixtures/$name';
      final logs = <(SkpLogLevel, String)>[];
      final model = SkpFile.open(path).parse(
        ParseOptions(onLog: (level, message) => logs.add((level, message))),
      );
      expect(model.unreadRootEntities, 0, reason: name);
      expect(logs.any((entry) => entry.$1 == SkpLogLevel.warning), isFalse,
          reason: name);
    }
  });

  test('an unreadable text drops the rest of the root and logs a warning', () {
    final builder = create();
    builder.addFace([
      (0.0, 0.0, 0.0),
      (10.0, 0.0, 0.0),
      (10.0, 10.0, 0.0),
      (0.0, 10.0, 0.0),
    ]);
    builder.addText('First', (0.0, 0.0, 0.0));
    builder.addText('Broken', (5.0, 0.0, 0.0));
    builder.addFace([
      (20.0, 0.0, 0.0),
      (30.0, 0.0, 0.0),
      (30.0, 10.0, 0.0),
      (20.0, 10.0, 0.0),
    ]);

    final bytes = Uint8List.fromList(builder.toBytes());
    final completeSkp = SkpFile.fromBuffer(bytes);
    final complete = completeSkp.parse();
    expect(complete.root.texts, hasLength(2));
    expect(complete.unreadRootEntities, 0);
    expect(completeSkp.buildScene().unreadRootEntities, 0);
    expect(completeSkp.buildInstancedScene().unreadRootEntities, 0);

    // Break the second text's string marker so the reader can't find its text.
    const marker = [0x01, 0xFF, 0xFE, 0xFF, 0x06, 0x42, 0x00];
    final at = _findSubsequence(bytes, marker);
    expect(at, greaterThanOrEqualTo(0));
    for (int i = 0; i < 4; i++) {
      bytes[at + i] = 0x00;
    }

    final logs = <(SkpLogLevel, String)>[];
    final brokenSkp = SkpFile.fromBuffer(bytes);
    final model = brokenSkp.parse(
      ParseOptions(onLog: (level, message) => logs.add((level, message))),
    );
    expect(model.root.texts, hasLength(1));
    expect(model.root.texts[0].text, 'First');
    expect(model.unreadRootEntities, greaterThan(0));
    expect(brokenSkp.buildScene().unreadRootEntities, model.unreadRootEntities);
    expect(brokenSkp.buildInstancedScene().unreadRootEntities, model.unreadRootEntities);
    expect(
      logs.any(
        (entry) =>
            entry.$1 == SkpLogLevel.warning &&
            entry.$2.contains('stopped early') &&
            entry.$2.contains('${model.unreadRootEntities} unread'),
      ),
      isTrue,
    );
  });
}
