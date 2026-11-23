import 'dart:io';
import 'dart:typed_data';

import 'package:openskp/openskp.dart';
import 'package:openskp/src/geometry.dart';
import 'package:openskp/src/tlv.dart';
import 'package:test/test.dart';

String _fixturePath(String name) =>
    '${Directory.current.path}/test/fixtures/$name';

String _chairAndTablePath() =>
    '${Directory.current.path}/../../examples/web-viewer/samples/chair_and_table.skp';

TlvNode _node(String tag, {Uint8List? payload, List<TlvNode>? children}) =>
    TlvNode(
      offset: 0,
      tag: tag,
      size: payload?.length ?? 0,
      payload: payload ?? Uint8List(0),
      children: children ?? const [],
    );

void main() {
  group('Per-edge and per-face layer reading (#285 / #526 / #543)', () {
    test('resolves VFF D207 edge and face layer IDs to layer names', () {
      final edgeLayered = _node('B80B', children: [
        _node('DE05', payload: Uint8List.fromList([10])),
        _node('D007', children: [
          _node('D207', payload: Uint8List.fromList([42])),
        ]),
      ]);
      final edgeDefault = _node('B80B', children: [
        _node('DE05', payload: Uint8List.fromList([11])),
      ]);
      final faceLayered = _node('AC0D', children: [
        _node('DE05', payload: Uint8List.fromList([20])),
        _node('D007', children: [
          _node('D207', payload: Uint8List.fromList([42])),
        ]),
      ]);
      final faceDefault = _node('AC0D', children: [
        _node('DE05', payload: Uint8List.fromList([21])),
      ]);

      final builder = GeometryBuilder();
      Geometry.extractGeometryFromNodes(
        [edgeLayered, edgeDefault, faceLayered, faceDefault],
        builder,
      );

      final rootDef = SkpFile.buildDefinition(
        0,
        RawDefinition(guid: 'ROOT', name: 'ROOT_MODEL', builder: builder),
        {1: 'Layer0', 42: 'Sky Dome'},
      );

      expect(rootDef.edges[10]!.layer, 'Sky Dome');
      expect(rootDef.edges[11]!.layer, '');
      expect(rootDef.faces[20]!.layer, 'Sky Dome');
      expect(rootDef.faces[21]!.layer, '');
    });

    test('reads edge and face layers from classic files', () {
      final gondola = SkpFile.open(_fixturePath('gondola_v20.skp')).parse();
      var edgesOnLayer = 0;
      var facesOnLayer = 0;
      var facesDefault = 0;

      void visit(Definition d) {
        for (final e in d.edges.values) {
          if (e.layer == 'Gondulas Laterais') edgesOnLayer++;
        }
        for (final f in d.faces.values) {
          if (f.layer == 'Gondulas Laterais') {
            facesOnLayer++;
          } else if (f.layer == '') {
            facesDefault++;
          }
        }
      }

      visit(gondola.root);
      for (final d in gondola.definitions.values) {
        visit(d);
      }

      expect(edgesOnLayer, greaterThan(0));
      expect(facesOnLayer, 229);
      expect(facesDefault, 1658);

      final lineStyles =
          SkpFile.open(_fixturePath('sdk_saved_tag_line_styles_2020.skp'))
              .parse();
      expect(
        [for (final f in lineStyles.root.faces.values) f.layer],
        ['Tag 0 ', 'Tag 1 Dash', 'Tag 2 ', 'Tag 3 Dash', 'Tag 4 Dot', 'Tag 5 Dash'],
      );
    });

    test('uses majority face layer in buildScene and buildInstancedScene for chair_and_table.skp (#543)', () {
      final skp = SkpFile.open(_chairAndTablePath());
      final baked = skp.buildScene();
      final instanced = skp.buildInstancedScene();

      Set<(int, int, int)> rgbSet(List<Map<String, dynamic>> mats) {
        final s = <(int, int, int)>{};
        for (final m in mats) {
          final pbr = m['pbrMetallicRoughness'] as Map<String, dynamic>;
          final bcf = pbr['baseColorFactor'] as List;
          s.add((
            ((bcf[0] as num) * 255).round(),
            ((bcf[1] as num) * 255).round(),
            ((bcf[2] as num) * 255).round(),
          ));
        }
        return s;
      }

      final bakedColors = rgbSet(baked.gltfMaterials);
      final instancedColors = rgbSet(instanced.gltfMaterials);

      expect(bakedColors.contains((255, 84, 84)), isFalse);
      expect(instancedColors.contains((255, 84, 84)), isFalse);
      expect(bakedColors.contains((120, 150, 190)), isTrue);
      expect(bakedColors.contains((190, 150, 120)), isTrue);
      expect(instancedColors, bakedColors);

      List<(String, String)> collectBaked(InstanceNode n) => [
            (n.name, n.layer),
            for (final c in n.children) ...collectBaked(c),
          ];
      List<(String, String)> collectInstanced(InstancedNode n) => [
            (n.name, n.layer),
            for (final c in n.children) ...collectInstanced(c),
          ];

      expect(
        collectInstanced(instanced.sceneHierarchy),
        collectBaked(baked.sceneHierarchy),
      );
    });
  });
}
