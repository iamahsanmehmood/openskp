import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:openskp/openskp.dart';
import 'package:openskp/src/geometry.dart';
import 'package:openskp/src/legacy.dart';
import 'package:openskp/src/tlv.dart';
import 'package:test/test.dart';

/// Dart never implemented Dynamic Component property extraction at all -
/// neither the VFF-side D007/DC05/B636/AD38 TLV walk (Python/TypeScript/
/// C++ already had it) nor the legacy-side attribute-container plumbing.
/// This file covers both halves of that port in one go.
///
/// Legacy (pre-2021 MFC) instances have produced empty properties for
/// every single file, because LegacyReaders.readInstance was calling
/// preamble(ar, r) - which reads the instance's CAttributeContainer,
/// correctly advancing the byte cursor - and then discarding the return
/// value entirely. Same "already-decoded-but-discarded" shape as the
/// earlier layer/face/instance-hidden fixes, just one level deeper.
///
/// SketchUp's Dynamic Components extension stores its data under a
/// dictionary literally named "dynamic_attributes" (stable, publicly
/// documented Ruby API: Entity#attribute_dictionary("dynamic_attributes") -
/// not something reverse-engineered from a fixture).

Uint8List _tlvBytes(String tagHex, Uint8List payload) {
  final tag = [
    int.parse(tagHex.substring(0, 2), radix: 16),
    int.parse(tagHex.substring(2, 4), radix: 16),
  ];
  final out = Uint8List(6 + payload.length);
  out[0] = tag[0];
  out[1] = tag[1];
  final bd = ByteData.sublistView(out);
  bd.setUint32(2, payload.length, Endian.little);
  out.setRange(6, 6 + payload.length, payload);
  return out;
}

Uint8List _concatBytes(List<Uint8List> parts) {
  final total = parts.fold<int>(0, (n, p) => n + p.length);
  final out = Uint8List(total);
  var offset = 0;
  for (final p in parts) {
    out.setRange(offset, offset + p.length, p);
    offset += p.length;
  }
  return out;
}

void main() {
  group('stringifyAttrValue', () {
    test('stringifies scalars', () {
      expect(LegacyReaders.stringifyAttrValue(null), '');
      expect(LegacyReaders.stringifyAttrValue(42), '42');
      expect(LegacyReaders.stringifyAttrValue(3.5), '3.5');
      expect(LegacyReaders.stringifyAttrValue('width'), 'width');
    });

    test('stringifies lists by joining', () {
      expect(LegacyReaders.stringifyAttrValue([1, 2, 3]), '1,2,3');
      expect(LegacyReaders.stringifyAttrValue([1.0, 2.0, 3.0]), '1.0,2.0,3.0');
    });
  });

  group('extractLegacyDynamicProperties', () {
    test('extracts the dynamic_attributes dict by name', () {
      // Real shape from readAttrContainer/readAttrNamed: each tuple's
      // first element is the ENTITY CLASS NAME (always 'CAttributeNamed',
      // from Archive.readObject) - never the dictionary's own declared
      // name, which lives in DictRec.name.
      final attrs = AttrsRec([
        ('CAttributeNamed', DictRec('SU_DefinitionSet', {'unrelated': 1})),
        (
          'CAttributeNamed',
          DictRec('dynamic_attributes', {'width': 10.0, '_width_label': 'Width', 'count': 4}),
        ),
      ]);
      final props = LegacyReaders.extractLegacyDynamicProperties(attrs);
      expect(props, {'width': '10.0', '_width_label': 'Width', 'count': '4'});
    });

    test('returns {} when no dynamic_attributes dict is present', () {
      final attrs = AttrsRec([
        ('CAttributeNamed', DictRec('SU_DefinitionSet', {'a': 1})),
      ]);
      expect(LegacyReaders.extractLegacyDynamicProperties(attrs), {});
    });

    test('returns {} for no attribute container at all', () {
      expect(LegacyReaders.extractLegacyDynamicProperties(null), {});
    });
  });

  group('Geometry.extractDynamicProperties (VFF-side, new in Dart)', () {
    test('extracts a key/value pair from a DC05 payload', () {
      final dc05Payload = _concatBytes([
        _tlvBytes('B636', utf8.encode('width')),
        _tlvBytes('AD38', utf8.encode('10')),
      ]);
      final dc05 = TlvNode(offset: 0, tag: 'DC05', size: dc05Payload.length, payload: dc05Payload);
      final d007 = TlvNode(offset: 0, tag: 'D007', size: 0, children: [dc05]);

      expect(Geometry.extractDynamicProperties(d007), {'width': '10'});
    });

    test('returns {} when D007 has no DC05 child', () {
      final d007 = TlvNode(offset: 0, tag: 'D007', size: 0, children: const []);
      expect(Geometry.extractDynamicProperties(d007), {});
    });

    test('returns {} for an empty DC05 payload', () {
      final dc05 = TlvNode(offset: 0, tag: 'DC05', size: 0, payload: Uint8List(0));
      final d007 = TlvNode(offset: 0, tag: 'D007', size: 0, children: [dc05]);
      expect(Geometry.extractDynamicProperties(d007), {});
    });
  });

  group('legacy real-fixture wiring', () {
    test('does not crash and reports {} for a fixture with no Dynamic Component data', () {
      // capilla_quiroz_v17.skp (a plain chapel model) has no Dynamic
      // Component data on any of its 3 instances - confirmed by direct
      // inspection of the raw attribute-container reads before writing
      // this fix - so this proves the plumbing fix doesn't break or crash
      // on entities that render no attributes, not the dictionary-lookup
      // logic itself (covered above with synthetic data).
      final fixturePath =
          '${Directory.current.path}/test/fixtures/capilla_quiroz_v17.skp';
      final scene = SkpFile.open(fixturePath).buildScene();

      void walk(InstanceNode node) {
        expect(node.properties, {});
        for (final child in node.children) walk(child);
      }

      walk(scene.sceneHierarchy);
    });
  });
}
