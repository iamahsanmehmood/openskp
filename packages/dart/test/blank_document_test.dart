import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:crypto/crypto.dart';
import 'package:openskp/openskp.dart';
import 'package:openskp/src/blank_document.dart';
import 'package:test/test.dart';

bool _containsSubsequence(List<int> haystack, List<int> needle) {
  if (needle.isEmpty || haystack.length < needle.length) return false;
  final first = needle[0];
  final limit = haystack.length - needle.length;
  for (var i = 0; i <= limit; i++) {
    if (haystack[i] != first) continue;
    var ok = true;
    for (var j = 1; j < needle.length; j++) {
      if (haystack[i + j] != needle[j]) {
        ok = false;
        break;
      }
    }
    if (ok) return true;
  }
  return false;
}

int _hexVal(int codeUnit) {
  if (codeUnit >= 0x30 && codeUnit <= 0x39) return codeUnit - 0x30;
  if (codeUnit >= 0x61 && codeUnit <= 0x66) return codeUnit - 0x61 + 10;
  if (codeUnit >= 0x41 && codeUnit <= 0x46) return codeUnit - 0x41 + 10;
  return -1;
}

List<int> _extractHexByteTokens(String text) {
  final out = <int>[];
  final units = text.codeUnits;
  for (var i = 0; i + 3 < units.length; i++) {
    if (units[i] == 0x30 && (units[i + 1] == 0x78 || units[i + 1] == 0x58)) {
      final hi = _hexVal(units[i + 2]);
      final lo = _hexVal(units[i + 3]);
      final nextIsHex = (i + 4 < units.length) && _hexVal(units[i + 4]) >= 0;
      if (hi >= 0 && lo >= 0 && !nextIsHex) {
        out.add((hi << 4) | lo);
        i += 3;
      }
    }
  }
  return out;
}

void main() {
  final pkgRoot = Directory.current.path;
  final fixturePath = '$pkgRoot/test/fixtures/blank_v17.skp';
  final libDir = Directory('$pkgRoot/lib');

  test('buildBlankV17 matches fixture byte-for-byte and expected SHA-256', () {
    final doc = buildBlankV17();
    final expected = File(fixturePath).readAsBytesSync();

    expect(doc.data.length, 5796);
    expect(sha256.convert(doc.data).toString(), scaffoldSha256);
    expect(doc.data, equals(expected));

    expect(doc.modelGuidOffset, 52);
    expect(doc.pidCounterOffset, 1987);
    expect(doc.nextPid, 18);
    expect(doc.noScaleMaskOffset, 2008);
    expect(doc.tailPos, 3515);
    expect(doc.scaffoldNextSlot, 11);
    expect(versionMapV17.length, 58);
  });

  test('built blank document reads back cleanly in classic reader', () {
    final doc = buildBlankV17();
    final bd = ByteData.sublistView(doc.data);
    var pos = 0;

    String readUtf16() {
      expect(doc.data[pos], 0xFF);
      expect(doc.data[pos + 1], 0xFE);
      expect(doc.data[pos + 2], 0xFF);
      final charLen = doc.data[pos + 3];
      pos += 4;
      final units = List<int>.generate(
        charLen,
        (i) => bd.getUint16(pos + i * 2, Endian.little),
      );
      pos += charLen * 2;
      return String.fromCharCodes(units);
    }

    expect(readUtf16(), 'SketchUp Model');
    expect(readUtf16(), '{17.0.1}');
    pos += 16; // model GUID
    expect(readUtf16(), '');
    pos += 4; // save timestamp
    expect(bd.getUint16(pos, Endian.little), 0xFFFF);
    expect(bd.getUint16(pos + 2, Endian.little), 0);
    final vmapNameLen = bd.getUint16(pos + 4, Endian.little);
    pos += 6;
    expect(ascii.decode(doc.data.sublist(pos, pos + vmapNameLen)), 'CVersionMap');
    pos += vmapNameLen;

    final parsedVmap = <(String, int)>[];
    while (true) {
      final cls = readUtf16();
      if (cls == 'End-Of-Version-Map') break;
      final schema = bd.getUint32(pos, Endian.little);
      pos += 4;
      parsedVmap.add((cls, schema));
    }
    expect(parsedVmap, equals(versionMapV17));

    final model = SkpFile.fromBuffer(doc.data).parse();
    expect(model.version, '{17.0.1}');
    expect(model.layers.map((l) => l.name).toList(), ['Layer0']);
    expect(model.layers[0].hidden, isFalse);
    expect(
      (model.layers[0].colorR, model.layers[0].colorG, model.layers[0].colorB),
      (255, 84, 84),
    );
    expect(model.materials, isEmpty);
    expect(model.definitions, isEmpty);
    expect(model.root.faces, isEmpty);
    expect(model.root.edges, isEmpty);
    expect(model.root.vertices, isEmpty);
    expect(model.root.instances, isEmpty);
    expect(model.pages, isEmpty);
    expect(model.unreadRootEntities, 0);
    expect(model.faceFrontColor, (225, 225, 200));
    expect(model.faceBackColor, (128, 128, 200));
    expect(model.colorByTag, isFalse);
    expect(model.attributeDictionaries, isEmpty);
  });

  test('lib/ contains no .skp files and no embedded template bytes', () {
    final templateBytes = buildBlankV17().data;
    final templateHex = templateBytes
        .map((b) => b.toRadixString(16).padLeft(2, '0'))
        .join();
    final templateBase64 = base64.encode(templateBytes);

    final files = libDir
        .listSync(recursive: true)
        .whereType<File>()
        .toList();
    expect(files, isNotEmpty);

    for (final file in files) {
      expect(
        file.path.toLowerCase().endsWith('.skp'),
        isFalse,
        reason: 'Unexpected .skp file under lib/: ${file.path}',
      );
      final raw = file.readAsBytesSync();
      expect(
        _containsSubsequence(raw, templateBytes),
        isFalse,
        reason: 'Raw template bytes found in ${file.path}',
      );
      final text = utf8.decode(raw, allowMalformed: true);
      final compactBuf = StringBuffer();
      for (final cu in text.codeUnits) {
        if ((cu >= 0x30 && cu <= 0x39) ||
            (cu >= 0x41 && cu <= 0x5A) ||
            (cu >= 0x61 && cu <= 0x7A) ||
            cu == 0x2B ||
            cu == 0x2F ||
            cu == 0x3D) {
          compactBuf.writeCharCode(cu);
        }
      }
      final compact = compactBuf.toString();
      expect(compact.contains(templateBase64), isFalse, reason: file.path);
      expect(
        compact.toLowerCase().contains(templateHex),
        isFalse,
        reason: file.path,
      );
      expect(
        _containsSubsequence(_extractHexByteTokens(text), templateBytes),
        isFalse,
        reason: 'Hex byte array of template found in ${file.path}',
      );
    }
  });
}
