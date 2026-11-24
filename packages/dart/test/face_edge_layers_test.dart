import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:archive/archive.dart';
import 'package:openskp/openskp.dart';
import 'package:test/test.dart';

String _fixturePath(String name) =>
    '${Directory.current.path}/test/fixtures/$name';

String _chairAndTablePath() =>
    '${Directory.current.path}/../../examples/web-viewer/samples/chair_and_table.skp';

Uint8List _tlvRec(String tag, List<Uint8List> parts) {
  final bodyLen = parts.fold<int>(0, (s, p) => s + p.length);
  final out = Uint8List(6 + bodyLen);
  out[0] = int.parse(tag.substring(0, 2), radix: 16);
  out[1] = int.parse(tag.substring(2, 4), radix: 16);
  final bd = ByteData.sublistView(out);
  bd.setUint32(2, bodyLen, Endian.little);
  var off = 6;
  for (final p in parts) {
    out.setRange(off, off + p.length, p);
    off += p.length;
  }
  return out;
}

Uint8List _buildSyntheticVffSkp(Uint8List modelDat) {
  final refBytes = File(_fixturePath('arcs_2021.skp')).readAsBytesSync();
  var pk = -1;
  for (var i = 0; i + 4 <= refBytes.length; i++) {
    if (refBytes[i] == 0x50 &&
        refBytes[i + 1] == 0x4B &&
        refBytes[i + 2] == 0x03 &&
        refBytes[i + 3] == 0x04) {
      pk = i;
      break;
    }
  }
  expect(pk, greaterThan(0));
  final ar = Archive()
    ..addFile(ArchiveFile('model.dat', modelDat.length, modelDat));
  final zipBytes = ZipEncoder().encode(ar);
  final out = BytesBuilder(copy: false)
    ..add(Uint8List.sublistView(refBytes, 0, pk))
    ..add(zipBytes);
  return out.toBytes();
}

List<(String, String)> _collectBaked(InstanceNode n) => [
      (n.name, n.layer),
      for (final c in n.children) ..._collectBaked(c),
    ];

List<(String, String)> _collectInstanced(InstancedNode n) => [
      (n.name, n.layer),
      for (final c in n.children) ..._collectInstanced(c),
    ];

void main() {
  group('Per-edge and per-face layer reading and scene fallback (#615)', () {
    test('resolves VFF D207 edge and face layer IDs to layer names', () {
      final layers = _tlvRec('993A', [
        _tlvRec('8C3C', [
          _tlvRec('DC05', [Uint8List.fromList([1])]),
          _tlvRec('8D3C', [Uint8List.fromList(utf8.encode('Layer0'))]),
        ]),
        _tlvRec('8C3C', [
          _tlvRec('DC05', [Uint8List.fromList([42])]),
          _tlvRec('8D3C', [Uint8List.fromList(utf8.encode('Sky Dome'))]),
        ]),
      ]);
      final edgeLayered = _tlvRec('B80B', [
        _tlvRec('DE05', [Uint8List.fromList([10])]),
        _tlvRec('D007', [
          _tlvRec('D207', [Uint8List.fromList([42])]),
        ]),
      ]);
      final edgeDefault = _tlvRec('B80B', [
        _tlvRec('DE05', [Uint8List.fromList([11])]),
      ]);
      final faceLayered = _tlvRec('AC0D', [
        _tlvRec('DE05', [Uint8List.fromList([20])]),
        _tlvRec('D007', [
          _tlvRec('D207', [Uint8List.fromList([42])]),
        ]),
      ]);
      final faceDefault = _tlvRec('AC0D', [
        _tlvRec('DE05', [Uint8List.fromList([21])]),
      ]);
      final root = _tlvRec(
        'F601',
        [edgeLayered, edgeDefault, faceLayered, faceDefault],
      );
      final modelDat = _tlvRec('F401', [layers, root]);

      final model = SkpFile.fromBuffer(_buildSyntheticVffSkp(modelDat)).parse();
      expect(model.root.edges[10]!.layer, 'Sky Dome');
      expect(model.root.edges[11]!.layer, '');
      expect(model.root.faces[20]!.layer, 'Sky Dome');
      expect(model.root.faces[21]!.layer, '');
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

    test('uses face tag colours with unpaintedFaces="tag" and uniform leaf layers on chair_and_table.skp', () {
      final skp = SkpFile.open(_chairAndTablePath());
      const opts = ParseOptions(unpaintedFaces: 'tag');
      final baked = skp.buildScene(opts);
      final instanced = skp.buildInstancedScene(opts);

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

      final bakedNodes = _collectBaked(baked.sceneHierarchy);
      expect(bakedNodes, contains(('Chair', 'Layer0')));
      expect(bakedNodes, contains(('Table', 'Layer0')));
      expect(bakedNodes, contains(('Leg_Chair', 'Chair')));
      expect(bakedNodes, contains(('Leg_Table', 'Table')));
      expect(_collectInstanced(instanced.sceneHierarchy), bakedNodes);
    });

    test('does not move 20 untagged faces or an untagged nested child onto 1 hidden face tag', () {
      final b = create();
      final hiddenTag = b.addLayer('HiddenTag', color: [10, 20, 30], hidden: true);
      final childDef = b.addComponentDefinition('Child', (d) {
        d.addFace([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)]);
      });
      final parentDef = b.addComponentDefinition('MixedParent', (d) {
        for (var k = 0; k < 20; k++) {
          final x = k * 2.0;
          d.addFace([(x, 0, 0), (x + 1, 0, 0), (x + 1, 1, 0), (x, 1, 0)]);
        }
        d.addFace([(0, 5, 0), (1, 5, 0), (1, 6, 0), (0, 6, 0)], layer: hiddenTag);
        d.addInstance(childDef, name: 'NestedChild', translation: (0, 10, 0));
      });
      b.addInstance(parentDef, name: 'MixedPlacement');

      final skp = SkpFile.fromBuffer(b.toBytes());
      final baked = skp.buildScene();
      final instanced = skp.buildInstancedScene();

      for (final meta in baked.meshIndex.values) {
        expect(meta.layer, 'Layer0');
      }
      const expectedNodes = [
        ('ROOT', 'Layer0'),
        ('MixedPlacement', 'Layer0'),
        ('NestedChild', 'Layer0'),
      ];
      expect(_collectBaked(baked.sceneHierarchy), expectedNodes);
      expect(_collectInstanced(instanced.sceneHierarchy), expectedNodes);
    });

    test('applies uniform face tags to own meshes, root meshes, and childless nodes without leaking to children', () {
      final b = create();
      final roof = b.addLayer('Roof', color: [100, 120, 140]);
      final leafTag = b.addLayer('LeafTag', color: [40, 80, 120]);
      final childDef = b.addComponentDefinition('UntaggedChild', (d) {
        d.addFace([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)]);
      });
      final parentDef = b.addComponentDefinition('UniformParent', (d) {
        d.addFace([(0, 0, 0), (2, 0, 0), (2, 2, 0), (0, 2, 0)], layer: roof);
        d.addInstance(childDef, name: 'ChildInst', translation: (5, 0, 0));
      });
      final leafDef = b.addComponentDefinition('LeafDef', (d) {
        d.addFace([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)], layer: leafTag);
      });
      b.addFace([(0, 20, 0), (2, 20, 0), (2, 22, 0), (0, 22, 0)], layer: roof);
      b.addInstance(parentDef, name: 'ParentInst');
      b.addInstance(leafDef, name: 'LeafInst', translation: (10, 0, 0));

      final skp = SkpFile.fromBuffer(b.toBytes());
      final baked = skp.buildScene();
      final instanced = skp.buildInstancedScene();

      final layersByPath = <String, Set<String>>{};
      for (final meta in baked.meshIndex.values) {
        layersByPath.putIfAbsent(meta.path, () => <String>{}).add(meta.layer);
      }
      expect(layersByPath['ROOT'], {'Roof'});
      expect(layersByPath['ROOT / ParentInst'], {'Roof'});
      expect(layersByPath['ROOT / ParentInst / ChildInst'], {'Layer0'});
      expect(layersByPath['ROOT / LeafInst'], {'LeafTag'});

      const expectedNodes = [
        ('ROOT', 'Layer0'),
        ('ParentInst', 'Layer0'),
        ('ChildInst', 'Layer0'),
        ('LeafInst', 'LeafTag'),
      ];
      expect(_collectBaked(baked.sceneHierarchy), expectedNodes);
      expect(_collectInstanced(instanced.sceneHierarchy), expectedNodes);
    });

    test('keeps an own tag or inherited parent tag over a uniform face tag', () {
      final b = create();
      final wood = b.addLayer('Wood', color: [160, 110, 60]);
      final furnitureHidden =
          b.addLayer('FurnitureHidden', color: [40, 40, 40], hidden: true);
      final groupTag = b.addLayer('GroupTag', color: [30, 140, 90]);

      final woodPanel = b.addComponentDefinition('WoodPanel', (d) {
        d.addFace([(0, 0, 0), (2, 0, 0), (2, 2, 0), (0, 2, 0)], layer: wood);
      });
      final parentGroup = b.addComponentDefinition('ParentGroup', (d) {
        d.addInstance(woodPanel, name: 'UntaggedInGroup', translation: (5, 0, 0));
      });
      b.addInstance(woodPanel, name: 'HiddenFurniture', layer: furnitureHidden);
      b.addInstance(
        parentGroup,
        name: 'TaggedGroup',
        layer: groupTag,
        translation: (10, 0, 0),
      );

      final skp = SkpFile.fromBuffer(b.toBytes());
      const opts = ParseOptions(unpaintedFaces: 'tag');
      final baked = skp.buildScene(opts);
      final instanced = skp.buildInstancedScene(opts);

      final meshLayers = {
        for (final meta in baked.meshIndex.values) meta.path: meta.layer,
      };
      expect(meshLayers['ROOT / HiddenFurniture'], 'FurnitureHidden');
      expect(meshLayers['ROOT / TaggedGroup / UntaggedInGroup'], 'GroupTag');

      const expectedNodes = [
        ('ROOT', 'Layer0'),
        ('HiddenFurniture', 'FurnitureHidden'),
        ('TaggedGroup', 'GroupTag'),
        ('UntaggedInGroup', 'GroupTag'),
      ];
      expect(_collectBaked(baked.sceneHierarchy), expectedNodes);
      expect(_collectInstanced(instanced.sceneHierarchy), expectedNodes);

      bool hasWood(List<Map<String, dynamic>> mats) {
        for (final m in mats) {
          final pbr = m['pbrMetallicRoughness'] as Map<String, dynamic>;
          final bcf = pbr['baseColorFactor'] as List;
          if (((bcf[0] as num) * 255).round() == 160 &&
              ((bcf[1] as num) * 255).round() == 110 &&
              ((bcf[2] as num) * 255).round() == 60) {
            return true;
          }
        }
        return false;
      }

      expect(hasWood(baked.gltfMaterials), isTrue);
      expect(hasWood(instanced.gltfMaterials), isTrue);
    });
  });
}
