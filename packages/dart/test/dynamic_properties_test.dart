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

  group('Geometry.extractAttributeDictionaries (openskp#254/#285)', () {
    // Real B436(name)/B536(entries) dictionary-boundary shape - the TLV
    // structure extractAttributeDictionaries needs but
    // extractDynamicProperties never required, since it flattens
    // regardless of dictionary boundaries. Confirmed byte-for-byte against
    // a real FrameBuilder-authored production file (see Python's
    // TestVffAttributeDictionaries for the full verification history this
    // mirrors, also ported identically to .NET/TypeScript).
    Uint8List entryValue(Uint8List innerTlv) => _tlvBytes('A438', innerTlv);
    Uint8List entry(String key, Uint8List innerValueTlv) =>
        _concatBytes([_tlvBytes('B636', utf8.encode(key)), entryValue(innerValueTlv)]);
    Uint8List namedDict(String name, Uint8List entriesPayload) => _concatBytes([
          _tlvBytes('B436', utf8.encode(name)),
          _tlvBytes('B536', entriesPayload),
        ]);
    TlvNode makeD007(Uint8List dc05Payload) {
      final dc05 = TlvNode(offset: 0, tag: 'DC05', size: dc05Payload.length, payload: dc05Payload);
      return TlvNode(offset: 0, tag: 'D007', size: 0, children: [dc05]);
    }

    Uint8List f64(double n) {
      final b = Uint8List(8);
      ByteData.sublistView(b).setFloat64(0, n, Endian.little);
      return b;
    }

    Uint8List i32(int n) {
      final b = Uint8List(4);
      ByteData.sublistView(b).setInt32(0, n, Endian.little);
      return b;
    }

    test('groups entries by their dictionary name', () {
      final d007 = makeD007(namedDict('fbd-einfo', entry('code', _tlvBytes('AD38', utf8.encode('Ks')))));
      final dicts = Geometry.extractAttributeDictionaries(d007);
      expect(dicts['fbd-einfo'], {'code': 'Ks'});
    });

    test('keeps two dictionaries distinct', () {
      final dc05 = _concatBytes([
        namedDict('dynamic_attributes', entry('width', _tlvBytes('AD38', utf8.encode('10')))),
        namedDict('FrameBuilder', entry('name', _tlvBytes('AD38', utf8.encode('W-2')))),
      ]);
      final dicts = Geometry.extractAttributeDictionaries(makeD007(dc05));
      expect(dicts['dynamic_attributes'], {'width': '10'});
      expect(dicts['FrameBuilder'], {'name': 'W-2'});
      expect(dicts['dynamic_attributes']!.containsKey('name'), isFalse);
    });

    test('returns {} when D007 has no DC05 child', () {
      final d007 = TlvNode(offset: 0, tag: 'D007', size: 0, children: const []);
      expect(Geometry.extractAttributeDictionaries(d007), {});
    });

    test('decodes AF38 (Length) and A938 (plain Float) as distinct tags, both f64', () {
      final entries = _concatBytes([
        entry('depth', _tlvBytes('AF38', f64(15.5))),
        entry('price', _tlvBytes('A938', f64(120.0))),
      ]);
      final dicts = Geometry.extractAttributeDictionaries(makeD007(namedDict('fbd-einfo', entries)));
      expect(dicts['fbd-einfo'], {'depth': 15.5, 'price': 120.0});
    });

    test('decodes A738 as a round-tripped integer', () {
      final entries = entry('angle', _tlvBytes('A738', i32(-7)));
      final dicts = Geometry.extractAttributeDictionaries(makeD007(namedDict('fbd-einfo', entries)));
      expect(dicts['fbd-einfo'], {'angle': -7});
    });

    test('decodes an A438 with no children as null', () {
      final entries = entry('child_thickness', Uint8List(0));
      final dicts = Geometry.extractAttributeDictionaries(makeD007(namedDict('fbd-einfo', entries)));
      expect(dicts['fbd-einfo'], {'child_thickness': null});
    });

    test('decodes B438 Point3d and B538 Vector3d as flat 3-element lists', () {
      final pointBytes = _tlvBytes('B438', _concatBytes([f64(0.0), f64(0.807085), f64(14.6551)]));
      final vectorBytes = _tlvBytes('B538', _concatBytes([f64(1.0), f64(0.0), f64(0.0)]));
      final entries = _concatBytes([entry('end_pos', pointBytes), entry('vector_new', vectorBytes)]);
      final dicts = Geometry.extractAttributeDictionaries(makeD007(namedDict('fbd-einfo', entries)));
      expect(dicts['fbd-einfo'], {
        'end_pos': [0.0, 0.807085, 14.6551],
        'vector_new': [1.0, 0.0, 0.0],
      });
    });

    test('decodes an empty AE38 array as []', () {
      final entries = entry('added_bolt_holes', _tlvBytes('AE38', Uint8List(0)));
      final dicts = Geometry.extractAttributeDictionaries(makeD007(namedDict('fbd-einfo', entries)));
      expect(dicts['fbd-einfo'], {'added_bolt_holes': []});
    });

    test('decodes an AE38 array of floats', () {
      final elems = _concatBytes([
        entryValue(_tlvBytes('A938', f64(0.728))),
        entryValue(_tlvBytes('A938', f64(11.358))),
        entryValue(_tlvBytes('A938', f64(14.655))),
      ]);
      final entries = entry('flangeholes', _tlvBytes('AE38', elems));
      final dicts = Geometry.extractAttributeDictionaries(makeD007(namedDict('fbd-einfo', entries)));
      expect(dicts['fbd-einfo'], {
        'flangeholes': [0.728, 11.358, 14.655],
      });
    });

    test('decodes a nested AE38 array (openskp#253 mirrored on the read side)', () {
      final inner1 = _concatBytes([
        entryValue(_tlvBytes('A938', f64(25.17))),
        entryValue(_tlvBytes('A938', f64(0.07))),
      ]);
      final inner2 = _concatBytes([
        entryValue(_tlvBytes('A938', f64(25.17))),
        entryValue(_tlvBytes('A938', f64(15.35))),
      ]);
      final outer = _concatBytes([entryValue(_tlvBytes('AE38', inner1)), entryValue(_tlvBytes('AE38', inner2))]);
      final entries = entry('lip_side1_cords', _tlvBytes('AE38', outer));
      final dicts = Geometry.extractAttributeDictionaries(makeD007(namedDict('fbd-einfo', entries)));
      expect(dicts['fbd-einfo'], {
        'lip_side1_cords': [
          [25.17, 0.07],
          [25.17, 15.35],
        ],
      });
    });

    test('leaves an unrecognized value tag as null rather than guessing', () {
      final entries = entry('mystery', _tlvBytes('EE99', Uint8List.fromList([0x01, 0x02])));
      final dicts = Geometry.extractAttributeDictionaries(makeD007(namedDict('fbd-einfo', entries)));
      expect(dicts['fbd-einfo'], {'mystery': null});
    });
  });

  group('Geometry.isGenericDefinitionName', () {
    test('matches SketchUp\'s own auto-generated placeholder pattern', () {
      expect(Geometry.isGenericDefinitionName('Group#1'), isTrue);
      expect(Geometry.isGenericDefinitionName('Component#12'), isTrue);
      expect(Geometry.isGenericDefinitionName('W-2'), isFalse);
      expect(Geometry.isGenericDefinitionName('Truss1'), isFalse);
      expect(Geometry.isGenericDefinitionName(''), isFalse);
    });
  });

  group('Geometry.findNameOverride', () {
    test('skips dynamic_attributes and SU_InstanceSet', () {
      final dicts = <String, Map<String, Object?>>{
        'dynamic_attributes': {'name': 'should-be-ignored'},
        'SU_InstanceSet': {'label': 'also-ignored'},
        'FrameBuilder': {'name': 'W-2'},
      };
      expect(Geometry.findNameOverride(dicts), 'W-2');
    });

    test('returns null when no override is present', () {
      expect(Geometry.findNameOverride(null), isNull);
      expect(
        Geometry.findNameOverride(<String, Map<String, Object?>>{
          'dynamic_attributes': {'width': '10'},
        }),
        isNull,
      );
    });
  });

  group('attributeDictionaries - real fixture (openskp#285)', () {
    // Real-fixture coverage for InstanceNode/InstancedNode/MeshMetadata's
    // attributeDictionaries field (the "multiple dictionaries per entity"
    // reader side). Untitled.skp is a genuine SteelFramer-authored file
    // whose "W1" instance carries a "steelframer-dict" dictionary - NOT
    // SketchUp's own "dynamic_attributes" - cross-checked against Python's
    // own test_untitled_skp ground truth (packages/python/tests/
    // test_parser.py), also mirrored in the .NET/TypeScript ports.
    final fixturePath = '${Directory.current.path}/test/fixtures/Untitled.skp';

    InstanceNode? findByName(InstanceNode node, String name) {
      if (node.name == name) return node;
      for (final child in node.children) {
        final found = findByName(child, name);
        if (found != null) return found;
      }
      return null;
    }

    InstancedNode? findInstancedByName(InstancedNode node, String name) {
      if (node.name == name) return node;
      for (final child in node.children) {
        final found = findInstancedByName(child, name);
        if (found != null) return found;
      }
      return null;
    }

    test('Scene: exposes the third-party dictionary by its own name on the baked tree', () {
      final scene = SkpFile.open(fixturePath).buildScene();
      final w1 = findByName(scene.sceneHierarchy, 'W1');

      expect(w1, isNotNull);
      // NOTE: unlike Python (whose properties is scoped to only the
      // "dynamic_attributes" dict), Dart's properties is populated via the
      // pre-existing, deliberately flatten-everything
      // extractDynamicProperties - a real, established divergence
      // predating this change. attributeDictionaries is the actually-
      // correct, dictionary-scoped way to reach steelframer-dict's data.
      expect(w1!.attributeDictionaries['steelframer-dict'], isNotNull);
      expect(w1.attributeDictionaries['steelframer-dict']!['generator'],
          'SteelFramer::Engine::PanelGenerator');
      expect(w1.attributeDictionaries['steelframer-dict']!['profile'], '362S200-43');
    });

    test('InstancedScene: exposes the third-party dictionary by its own name on the instanced tree', () {
      final instanced = SkpFile.open(fixturePath).buildInstancedScene();
      final w1 = findInstancedByName(instanced.sceneHierarchy, 'W1');

      expect(w1, isNotNull);
      expect(w1!.attributeDictionaries['steelframer-dict'], isNotNull);
      expect(w1.attributeDictionaries['steelframer-dict']!['generator'],
          'SteelFramer::Engine::PanelGenerator');
      expect(w1.attributeDictionaries['steelframer-dict']!['profile'], '362S200-43');
    });

    test('meshIndex: MeshMetadata also carries a non-empty steelframer-dict somewhere in the tree', () {
      // Several distinct instances each carry their own "steelframer-dict"
      // (with different keys per part type); W1 itself is a geometry-less
      // organizational wrapper (no mesh sits at its own path), so -
      // matching Python's own equally loose check - this only confirms
      // SOME mesh carries a non-empty "steelframer-dict", not necessarily
      // W1's own.
      final scene = SkpFile.open(fixturePath).buildScene();
      final meshWithDict = scene.meshIndex.values.where(
        (m) => (m.attributeDictionaries['steelframer-dict'] ?? {}).isNotEmpty,
      );

      expect(meshWithDict, isNotEmpty);
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
