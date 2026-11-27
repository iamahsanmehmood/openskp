import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:openskp/openskp.dart';
import 'package:openskp/src/legacy.dart';
import 'package:openskp/src/legacy_pages.dart';
import 'package:openskp/src/pages_dimensions.dart';
import 'package:openskp/src/tlv.dart';
import 'package:test/test.dart';

/// VFF scenes ("pages") and linear dimensions - ported from Python's
/// test_pages_dimensions.py (PR #190).
///
/// Dimensions are exercised against the repository's own Untitled.skp
/// fixture (drawn in SketchUp 2025, it carries 13 linear dimensions); scenes
/// have no fixture yet, so their parser is exercised on a synthetic "0702"
/// record byte-for-byte shaped like the real ones (the layout was decoded
/// from production survey files and calibrated against the scene thumbnails
/// SketchUp embeds in the .skp itself).

// ── helpers: build TLV runs in the flat (u16-LE tag, u32 len) form ────────

Uint8List tlv(int tag, Uint8List payload) {
  final result = Uint8List(6 + payload.length);
  result[0] = tag & 0xFF;
  result[1] = (tag >> 8) & 0xFF;
  final len = payload.length;
  result[2] = len & 0xFF;
  result[3] = (len >> 8) & 0xFF;
  result[4] = (len >> 16) & 0xFF;
  result[5] = (len >> 24) & 0xFF;
  result.setRange(6, 6 + payload.length, payload);
  return result;
}

Uint8List f64le(double v) {
  final buf = Uint8List(8);
  ByteData.sublistView(buf).setFloat64(0, v, Endian.little);
  return buf;
}

Uint8List u32le(int v) {
  final buf = Uint8List(4);
  ByteData.sublistView(buf).setUint32(0, v, Endian.little);
  return buf;
}

Uint8List vec3(double x, double y, double z) =>
    concatBytes([f64le(x), f64le(y), f64le(z)]);

Uint8List concatBytes(List<Uint8List> parts) {
  final total = parts.fold<int>(0, (s, p) => s + p.length);
  final result = Uint8List(total);
  int off = 0;
  for (final p in parts) {
    result.setRange(off, off + p.length, p);
    off += p.length;
  }
  return result;
}

void main() {
  final fixturesDir = '${Directory.current.path}/test/fixtures';

  group('linear dimensions', () {
    test('the Untitled.skp fixture has 13 dimensions', () {
      final model = SkpFile.open('$fixturesDir/Untitled.skp').parse();
      expect(model.dimensions.length, 13);
      for (final d in model.dimensions) {
        expect(d.a, isNotNull);
        expect(d.b, isNotNull);
        final (ax, ay, az) = d.a!;
        final (bx, by, bz) = d.b!;
        final dx = ax - bx, dy = ay - by, dz = az - bz;
        expect(dx * dx + dy * dy + dz * dz,
            greaterThan(0.0)); // a real measured segment
        expect(d.normal, isNotNull);
        expect(d.planeX, isNotNull);
      }
    });

    test('parses two free (world-space) connection points', () {
      // A 5BCC record with two type-1 (free, world-space) connection points.
      Uint8List pointBlock(int wrapTag, double x, double y, double z) {
        final inner =
            concatBytes([tlv(0x5209, u32le(1)), tlv(0x520A, vec3(x, y, z))]);
        return tlv(wrapTag, tlv(0x5208, inner));
      }

      final body = concatBytes([
        pointBlock(0x5BCD, 0.0, 0.0, 0.0),
        pointBlock(0x5BCE, 100.0, 0.0, 0.0),
        tlv(0x5BCF, vec3(1.0, 0.0, 0.0)), // 5BCF: read into planeX
        tlv(0x5BD0, vec3(0.0, 0.0, 1.0)), // 5BD0: read into normal
        tlv(0x5BD2, f64le(15.5)), // offset
      ]);
      final blob = concatBytes([Uint8List(8), tlv(0x5BCC, body), Uint8List(8)]);

      final dims = parseDimensions(blob, {}, {});
      expect(dims.length, 1);
      final d = dims[0];
      expect(d.a, (0.0, 0.0, 0.0));
      expect(d.b, (100.0, 0.0, 0.0));
      expect(d.offset, 15.5);
      expect(d.planeX, (1.0, 0.0, 0.0));
      expect(d.normal, (0.0, 0.0, 1.0));
      expect(d.offsetVector, isNull);
    });

    test('constrained offsets preserve plane side and endpoint order', () {
      Uint8List pointBlock(int wrapTag, (double, double, double) p) {
        final inner =
            concatBytes([tlv(0x5209, u32le(1)), tlv(0x520A, vec3(p.$1, p.$2, p.$3))]);
        return tlv(wrapTag, tlv(0x5208, inner));
      }

      const a = (1.0, 2.0, 3.0);
      const b = (1.0, 2.0, 13.0);
      for (final axis in [1, 2]) {
        for (final distance in [-4.0, 4.0]) {
          for (final reversed in [false, true]) {
            final p1 = reversed ? b : a;
            final p2 = reversed ? a : b;
            final body = concatBytes([
              pointBlock(0x5BCD, p1),
              pointBlock(0x5BCE, p2),
              tlv(0x5BCF, vec3(0, -1, 0)),
              tlv(0x5BD0, vec3(1, 0, 0)),
              tlv(0x5BD1, u32le(axis)),
              tlv(0x5BD2, f64le(distance)),
            ]);
            final dims = parseDimensions(tlv(0x5BCC, body), {}, {});
            expect(dims, hasLength(1));
            final expected =
                axis == 1 ? (0.0, 0.0, distance) : (-distance, 0.0, 0.0);
            expect(dims[0].offsetVector, expected);
            expect(dims[0].a, p1);
            expect(dims[0].b, p2);
          }
        }
      }
    });

    test('unsupported or malformed constraint keeps scalar fallback', () {
      Uint8List pointBlock(int wrapTag, (double, double, double) p) {
        final inner =
            concatBytes([tlv(0x5209, u32le(1)), tlv(0x520A, vec3(p.$1, p.$2, p.$3))]);
        return tlv(wrapTag, tlv(0x5208, inner));
      }

      for (final axisPayload in <Uint8List>[
        Uint8List(0),
        Uint8List.fromList([2]),
        u32le(0),
        u32le(3),
      ]) {
        var body = concatBytes([
          pointBlock(0x5BCD, (1, 2, 3)),
          pointBlock(0x5BCE, (1, 2, 13)),
          tlv(0x5BCF, vec3(0, -1, 0)),
          tlv(0x5BD0, vec3(1, 0, 0)),
          tlv(0x5BD2, f64le(-4.0)),
        ]);
        if (axisPayload.isNotEmpty) {
          body = concatBytes([body, tlv(0x5BD1, axisPayload)]);
        }
        final dims = parseDimensions(tlv(0x5BCC, body), {}, {});
        expect(dims, hasLength(1));
        expect(dims[0].offsetVector, isNull);
        expect(dims[0].offset, -4.0);
      }
    });

    test(
        'resolves a connected point through its instance transform, and drops an unresolvable one',
        () {
      // A type-2 connection (vertex id + instance id): the vertex position
      // is definition-local and must be lifted to world by the instance's
      // transform. An unresolvable reference drops the dimension
      // (fail-safe).
      final vid = Uint8List.fromList([0xAA, 0xBB, 0x01]);
      final iid = Uint8List.fromList([0xCC, 0xDD, 0x02]);

      Uint8List connected(int wrapTag) {
        final idLenPrefixed = concatBytes([
          Uint8List.fromList([iid.length]),
          iid,
        ]);
        final refTlv = tlv(0x53FC,
            concatBytes([tlv(0x53FD, vid), tlv(0x53FE, idLenPrefixed)]));
        final inner = concatBytes([tlv(0x5209, u32le(2)), tlv(0x520B, refTlv)]);
        return tlv(wrapTag, tlv(0x5208, inner));
      }

      Uint8List free(int wrapTag) {
        final inner = concatBytes(
            [tlv(0x5209, u32le(1)), tlv(0x520A, vec3(0.0, 0.0, 0.0))]);
        return tlv(wrapTag, tlv(0x5208, inner));
      }

      final body = concatBytes(
          [connected(0x5BCD), free(0x5BCE), tlv(0x5BD2, f64le(0.0))]);
      final blob = tlv(0x5BCC, body);

      // Identity-ish transform that translates by (10, 20, 30).
      final world = <double>[
        1.0,
        0.0,
        0.0,
        0.0,
        1.0,
        0.0,
        0.0,
        0.0,
        1.0,
        10.0,
        20.0,
        30.0,
        1.0
      ];
      final id2pos = {Tlv.toHexUpper(vid): (1.0, 2.0, 3.0)};
      final instWorld = {Tlv.toHexUpper(iid): world};

      final dims = parseDimensions(blob, id2pos, instWorld);
      expect(dims.length, 1);
      expect(dims[0].a, (11.0, 22.0, 33.0)); // local + translation

      // Same record, but the vertex id is unknown: the dimension is dropped.
      final emptyDims = parseDimensions(blob, {}, {});
      expect(emptyDims, isEmpty);
    });
  });

  group('scenes (pages)', () {
    Uint8List pageRecord(String name, bool parallel,
        [List<int> hiddenIds = const []]) {
      final cam = concatBytes([
        tlv(0x34BD, vec3(100.0, -200.0, 50.0)), // eye
        tlv(0x34BE, vec3(0.0, 0.0, 0.0)), // target
        tlv(0x34BF, vec3(0.0, 0.0, 1.0)), // up
        tlv(0x34C4, f64le(35.0)), // fov
        tlv(0x34C2, Uint8List.fromList([parallel ? 0 : 1])),
        tlv(0x34C3, f64le(240.0)), // ortho height
      ]);
      final hiddenParts = [
        for (final i in hiddenIds) Uint8List.fromList([1, i])
      ];
      final hidden = concatBytes(hiddenParts);
      final body = concatBytes([
        tlv(0x6F54, tlv(0x6F55, Uint8List.fromList(utf8.encode(name)))),
        tlv(0x714A, tlv(0x34BC, cam)),
        tlv(0x7150, hidden),
      ]);
      return tlv(0x7148, body);
    }

    test('parses a synthetic 0702 record', () {
      final payload = tlv(
          0x6D60,
          tlv(
              0x6D61,
              concatBytes([
                pageRecord('Planta', true, [2]),
                pageRecord('Vista 3D', false)
              ])));
      final node = TlvNode(
          offset: 0, tag: '0702', size: payload.length, payload: payload);
      final pages = parsePages(node);

      expect(pages.map((p) => p.name).toList(), ['Planta', 'Vista 3D']);
      final planta = pages[0];
      expect(planta.parallel, isTrue);
      expect(planta.orthoHeight, 240.0);
      expect(planta.eye, (100.0, -200.0, 50.0));
      expect(planta.up, (0.0, 0.0, 1.0));
      expect(planta.hiddenLayerIds, [2]);
      expect(pages[1].parallel, isFalse);
      expect(pages[1].fov, 35.0);
    });

    test('is empty when absent', () {
      expect(parsePages(null), isEmpty);
    });

    test('a file with no pages parses with an empty pages list', () {
      final model = SkpFile.open('$fixturesDir/SU_File.skp').parse();
      expect(model.pages, isEmpty);
    });

    test('recovers classic scene names and hidden layers via narrow scan fallback', () {
      Uint8List legacyPageRecord(String name, int flags,
          [List<int> hiddenIds = const []]) {
        final marker = Uint8List.fromList([0xFF, 0xFE, 0xFF]);
        final utf16 = Uint8List(2 * name.length);
        for (var i = 0; i < name.length; i++) {
          final c = name.codeUnitAt(i);
          utf16[2 * i] = c & 0xFF;
          utf16[2 * i + 1] = (c >> 8) & 0xFF;
        }
        final parts = <Uint8List>[
          marker,
          Uint8List.fromList([name.length]),
          utf16,
          marker,
          Uint8List.fromList([0]),
          u32le(flags),
          Uint8List.fromList([0, 0]), // camera: null ref
          Uint8List.fromList([0, 0]), // thumbnail dib: null ref
        ];
        if ((flags & 0x20) != 0) {
          for (final hid in hiddenIds) {
            parts.add(Uint8List.fromList([hid & 0xFF, (hid >> 8) & 0xFF]));
          }
          parts.add(u32le(1));
        }
        return concatBytes(parts);
      }

      final data = concatBytes([
        legacyPageRecord('Baseline', 0xC00),
        legacyPageRecord('WithHidden', 0xC20, [5, 6]),
        legacyPageRecord('TooFull', 0xFFF, [5]),
      ]);
      final ar = Archive(data, 17);
      ar.slots[5] = SlotEntry(kind: 'obj', name: 'CLayer');
      ar.slots[6] = SlotEntry(kind: 'obj', name: 'CLayer');

      final pages = scanPages(data, ar);
      expect(pages.map((p) => p.name).toList(), ['Baseline', 'WithHidden']);
      expect(pages[0].hiddenLayerIds, isEmpty);
      expect(pages[1].hiddenLayerIds, [5, 6]);
      expect(pages[0].captureFlags, isNull);
      expect(pages[1].captureFlags, isNull);

      final noise = Uint8List.fromList(
          [255, 254, 255, 5, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10]);
      expect(scanPages(noise, Archive(noise, 17)), isEmpty);

      final model = SkpFile.open(
        '$fixturesDir/classic_narrow_scan_pages_2017.skp',
      ).parse();
      expect(model.pages.length, 2);
      expect(model.pages[0].name, 'Baseline');
      expect(model.pages[0].hiddenLayers, isEmpty);
      expect(model.pages[0].captureFlags, isNull);
      expect(model.pages[0].eye, isNull);
      expect(model.pages[1].name, 'WithHidden');
      expect(model.pages[1].hiddenLayers, ['Plan', 'Roof']);
      expect(model.pages[1].captureFlags, isNull);
      expect(model.pages[1].eye, isNull);
    });
  });
}
