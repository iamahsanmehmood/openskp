import 'dart:convert';
import 'dart:typed_data';

import 'package:openskp/src/geometry.dart';
import 'package:openskp/src/tlv.dart';
import 'package:test/test.dart';

/// VFF (2021+) layers derive their COLOR from Layer_<name>-prefixed
/// materials, which carry no visibility flag of their own - real
/// visibility lives on the model.dat layer manager's own 993A/8C3C node,
/// as a single-byte 8E3C child sibling to the already-read DC05 (id) and
/// 8D3C (name): 1 = hidden, 0 = visible. Before this fix, collectLayers
/// never read 8E3C at all, so every VFF layer's hidden state silently
/// defaulted to visible regardless of the file's real Tags panel state.
/// Mirrors Python's own collect_layers/TestVffLayerHidden exactly
/// (openskp#285), byte shapes verified against a real production file's
/// own Tags panel (FrameSmart pipeline report, 2026-09-08), also mirrored
/// in the .NET/TypeScript ports.
TlvNode layerNode(int id, String name, bool? hidden) {
  final children = <TlvNode>[
    TlvNode(offset: 0, tag: 'DC05', size: 1, payload: Uint8List.fromList([id])),
    TlvNode(offset: 0, tag: '8D3C', size: 0, payload: utf8.encode(name)),
  ];
  if (hidden != null) {
    children.add(TlvNode(offset: 0, tag: '8E3C', size: 1, payload: Uint8List.fromList([hidden ? 1 : 0])));
  }
  return TlvNode(offset: 0, tag: '8C3C', size: 0, children: children);
}

TlvNode layerManager(List<TlvNode> layers) {
  return TlvNode(offset: 0, tag: '993A', size: 0, children: layers);
}

void main() {
  group('Geometry.collectLayers - VFF per-layer-hidden flag (openskp#285)', () {
    test('reads hidden and visible layers correctly', () {
      final root = layerManager([
        layerNode(5, 'wall_external_cladding_1', true),
        layerNode(6, 'wall', false),
      ]);

      final layerIdToName = <int, String>{};
      final layerHidden = <String, bool>{};
      Geometry.collectLayers([root], layerIdToName, layerHidden);

      expect(layerIdToName[5], 'wall_external_cladding_1');
      expect(layerIdToName[6], 'wall');
      expect(layerHidden['wall_external_cladding_1'], isTrue);
      expect(layerHidden['wall'], isFalse);
    });

    test('leaves layerHidden unset when there is no 8E3C tag', () {
      final root = layerManager([layerNode(1, 'Layer0', null)]);

      final layerIdToName = <int, String>{};
      final layerHidden = <String, bool>{};
      Geometry.collectLayers([root], layerIdToName, layerHidden);

      expect(layerIdToName[1], 'Layer0');
      expect(layerHidden.containsKey('Layer0'), isFalse);
    });

    test('the layerHidden parameter is optional', () {
      final root = layerManager([layerNode(1, 'Layer0', true)]);

      final layerIdToName = <int, String>{};
      expect(() => Geometry.collectLayers([root], layerIdToName), returnsNormally);
      expect(layerIdToName[1], 'Layer0');
    });
  });
}
