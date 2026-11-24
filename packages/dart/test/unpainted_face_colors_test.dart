import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:openskp/openskp.dart';
import 'package:openskp/src/core.dart';
import 'package:openskp/src/instanced_scene.dart';
import 'package:openskp/src/scene.dart';
import 'package:openskp/src/tlv.dart';
import 'package:test/test.dart';

const _front = (11, 122, 233);
const _back = (201, 102, 3);
const _versions = ['v4', 'v5', 'v6', '2016', '2017', '2018', '2019', '2020', '2021'];

String _fixture(String name) => '${Directory.current.path}/test/fixtures/$name';

(int, int, int) _rgbOf(List<Map<String, dynamic>> gltfMaterials, int matIdx) {
  final pbr = gltfMaterials[matIdx]['pbrMetallicRoughness'] as Map<String, dynamic>;
  final f = (pbr['baseColorFactor'] as List).cast<num>();
  return (
    (f[0].toDouble() * 255).round(),
    (f[1].toDouble() * 255).round(),
    (f[2].toDouble() * 255).round(),
  );
}

bool _doubleSidedOf(List<Map<String, dynamic>> gltfMaterials, int matIdx) =>
    gltfMaterials[matIdx]['doubleSided'] == true;

double _r5(double v) => (v * 1e5).roundToDouble() / 1e5;

List<((double, double, double), (double, double, double), (double, double, double))> _corners(
  List<double> positions,
  List<int> indices,
) {
  final pts = <(double, double, double)>[];
  for (var i = 0; i < positions.length; i += 3) {
    pts.add((_r5(positions[i]), _r5(positions[i + 1]), _r5(positions[i + 2])));
  }
  final out = <((double, double, double), (double, double, double), (double, double, double))>[];
  for (var t = 0; t < indices.length; t += 3) {
    out.add((pts[indices[t]], pts[indices[t + 1]], pts[indices[t + 2]]));
  }
  return out;
}

bool _sameWinding(
  ((double, double, double), (double, double, double), (double, double, double)) a,
  ((double, double, double), (double, double, double), (double, double, double)) b,
) {
  return b == a || b == (a.$2, a.$3, a.$1) || b == (a.$3, a.$1, a.$2);
}

List<((int, int, int), bool)> _glbMaterials(Uint8List glbBytes) {
  final bdata = ByteData.sublistView(glbBytes);
  final jsonLen = bdata.getUint32(12, Endian.little);
  final jsonText = utf8.decode(glbBytes.sublist(20, 20 + jsonLen));
  final parsed = jsonDecode(jsonText) as Map<String, dynamic>;
  final mats = (parsed['materials'] as List).cast<Map<String, dynamic>>();
  final list = [
    for (final m in mats)
      (
        () {
          final pbr = m['pbrMetallicRoughness'] as Map<String, dynamic>;
          final f = (pbr['baseColorFactor'] as List).cast<num>();
          return (
            (f[0].toDouble() * 255).round(),
            (f[1].toDouble() * 255).round(),
            (f[2].toDouble() * 255).round(),
          );
        }(),
        m['doubleSided'] == true,
      ),
  ];
  list.sort((a, b) {
    final c1 = a.$1.$1.compareTo(b.$1.$1);
    if (c1 != 0) return c1;
    final c2 = a.$1.$2.compareTo(b.$1.$2);
    if (c2 != 0) return c2;
    return a.$1.$3.compareTo(b.$1.$3);
  });
  return list;
}

Uint8List _boxBytes() {
  final builder = create();
  final pts = <Point3>[
    for (final z in [0.0, 10.0])
      for (final y in [0.0, 10.0])
        for (final x in [0.0, 10.0]) (x, y, z),
  ];
  for (final (a, b, c, d) in [
    (0, 2, 3, 1),
    (4, 5, 7, 6),
    (0, 1, 5, 4),
    (1, 3, 7, 5),
    (3, 2, 6, 7),
    (2, 0, 4, 6),
  ]) {
    builder.addFace([pts[a], pts[b], pts[c], pts[d]]);
  }
  return Uint8List.fromList(builder.toBytes());
}

void main() {
  group('Unpainted face colours in scenes and exports (#533)', () {
    for (final version in _versions) {
      test('shows model front and back face colours on face_colors_$version.skp', () {
        final skp = SkpFile.open(_fixture('face_colors_$version.skp'));
        final baked = skp.buildScene();
        final instanced = skp.buildInstancedScene();

        expect(baked.glbPrimitives.length, 2);
        final bakedSorted = [...baked.glbPrimitives]
          ..sort((a, b) => (a.unpaintedBack ? 1 : 0).compareTo(b.unpaintedBack ? 1 : 0));
        expect(bakedSorted[0].unpaintedBack, isFalse);
        expect(bakedSorted[1].unpaintedBack, isTrue);
        expect(_rgbOf(baked.gltfMaterials, bakedSorted[0].materialIndex), _front);
        expect(_rgbOf(baked.gltfMaterials, bakedSorted[1].materialIndex), _back);
        expect(_doubleSidedOf(baked.gltfMaterials, bakedSorted[0].materialIndex), isFalse);
        expect(_doubleSidedOf(baked.gltfMaterials, bakedSorted[1].materialIndex), isFalse);

        final fronts = _corners(bakedSorted[0].positions, bakedSorted[0].indices);
        final backs = _corners(bakedSorted[1].positions, bakedSorted[1].indices);
        expect(fronts, isNotEmpty);
        expect(fronts.length, backs.length);
        for (var i = 0; i < fronts.length; i++) {
          expect(
            {fronts[i].$1, fronts[i].$2, fronts[i].$3},
            {backs[i].$1, backs[i].$2, backs[i].$3},
          );
          expect(_sameWinding(fronts[i], backs[i]), isFalse);
        }

        final instPrims = [for (final r in instanced.meshResources) ...r.primitives]
          ..sort((a, b) => (a.unpaintedBack ? 1 : 0).compareTo(b.unpaintedBack ? 1 : 0));
        expect(instPrims.length, 2);
        expect(instPrims[0].unpaintedBack, isFalse);
        expect(instPrims[1].unpaintedBack, isTrue);
        expect(_rgbOf(instanced.gltfMaterials, instPrims[0].materialIndex), _front);
        expect(_rgbOf(instanced.gltfMaterials, instPrims[1].materialIndex), _back);
        expect(_doubleSidedOf(instanced.gltfMaterials, instPrims[0].materialIndex), isFalse);
        expect(_doubleSidedOf(instanced.gltfMaterials, instPrims[1].materialIndex), isFalse);
      });
    }

    for (final version in ['v4', '2017', '2021']) {
      test('unpaintedFaces="tag" keeps old tag-colour behaviour on $version', () {
        final skp = SkpFile.open(_fixture('face_colors_$version.skp'));
        final layer = skp.parse().layers.first;
        final layerRgb = (layer.colorR, layer.colorG, layer.colorB);
        const opts = ParseOptions(unpaintedFaces: 'tag');

        final baked = skp.buildScene(opts);
        expect(baked.glbPrimitives.length, 1);
        expect(_rgbOf(baked.gltfMaterials, baked.glbPrimitives.first.materialIndex), layerRgb);
        expect(_doubleSidedOf(baked.gltfMaterials, baked.glbPrimitives.first.materialIndex), isTrue);
        expect(baked.glbPrimitives.first.unpaintedBack, isFalse);

        final instanced = skp.buildInstancedScene(opts);
        final instPrims = [for (final r in instanced.meshResources) ...r.primitives];
        expect(instPrims.length, 1);
        expect(_rgbOf(instanced.gltfMaterials, instPrims.first.materialIndex), layerRgb);
        expect(_doubleSidedOf(instanced.gltfMaterials, instPrims.first.materialIndex), isTrue);
        expect(instPrims.first.unpaintedBack, isFalse);
      });
    }

    test('falls back to tag colour on a file without model face colours (SketchUp 3)', () {
      final skp = SkpFile.open(_fixture('legacy_v3_synthetic.skp'));
      expect(skp.parse().faceFrontColor, isNull);

      final styleBaked = skp.buildScene();
      final tagBaked = skp.buildScene(const ParseOptions(unpaintedFaces: 'tag'));
      expect(
        [for (final p in styleBaked.glbPrimitives) _rgbOf(styleBaked.gltfMaterials, p.materialIndex)],
        [for (final p in tagBaked.glbPrimitives) _rgbOf(tagBaked.gltfMaterials, p.materialIndex)],
      );
      expect(styleBaked.glbPrimitives.any((p) => p.unpaintedBack), isFalse);

      final styleInst = skp.buildInstancedScene();
      final tagInst = skp.buildInstancedScene(const ParseOptions(unpaintedFaces: 'tag'));
      final styleInstPrims = [for (final r in styleInst.meshResources) ...r.primitives];
      final tagInstPrims = [for (final r in tagInst.meshResources) ...r.primitives];
      expect(
        [for (final p in styleInstPrims) _rgbOf(styleInst.gltfMaterials, p.materialIndex)],
        [for (final p in tagInstPrims) _rgbOf(tagInst.gltfMaterials, p.materialIndex)],
      );
      expect(styleInstPrims.any((p) => p.unpaintedBack), isFalse);
    });

    test('throws ArgumentError on unknown unpaintedFaces option', () {
      final skp = SkpFile.open(_fixture('face_colors_2017.skp'));
      const badOpts = ParseOptions(unpaintedFaces: 'layer');
      expect(() => skp.buildScene(badOpts), throwsArgumentError);
      expect(() => skp.buildInstancedScene(badOpts), throwsArgumentError);
    });

    test('painted face sides and painted components win over model face colours', () {
      final builder = create();
      final red = builder.addMaterial('Red', [200, 10, 10, 255]);
      const square = <Point3>[
        (0.0, 0.0, 0.0),
        (10.0, 0.0, 0.0),
        (10.0, 10.0, 0.0),
        (0.0, 10.0, 0.0),
      ];
      final comp = builder.addComponentDefinition('Painted', (def) {
        def.addFace([for (final p in square) (p.$1, p.$2, 9.0)]);
      });
      builder.addFace(square, material: red);
      builder.addFace([for (final p in square) (p.$1, p.$2, 5.0)], backMaterial: red);
      builder.addInstance(comp, material: red);

      final parsed = Core.fullParse(Uint8List.fromList(builder.toBytes()));
      expect(parsed.faceColors, isNotNull);
      final (front, back) = parsed.faceColors!;
      expect(front, isNot(back));

      final redMatId = parsed.materialIdToName.entries.firstWhere((e) => e.value == 'Red').key;
      parsed.root.builder.instances.first.children = [
        TlvNode(
          offset: 0,
          tag: 'D007',
          size: 0,
          children: [
            TlvNode(offset: 0, tag: 'D107', size: 1, payload: Uint8List.fromList([redMatId])),
          ],
        ),
      ];

      final baked = SceneBuilder.build(parsed);
      final bakedSides = [
        for (final p in baked.glbPrimitives)
          (_rgbOf(baked.gltfMaterials, p.materialIndex), _doubleSidedOf(baked.gltfMaterials, p.materialIndex), p.unpaintedBack),
      ];
      expect(bakedSides, contains(((200, 10, 10), true, false)));
      expect(bakedSides, contains((front, false, false)));
      expect(bakedSides, contains((back, false, false)));
      expect(baked.glbPrimitives.any((p) => p.unpaintedBack), isFalse);

      final instanced = InstancedSceneBuilder.build(parsed);
      final instPrims = [for (final r in instanced.meshResources) ...r.primitives];
      final instSides = [
        for (final p in instPrims)
          (_rgbOf(instanced.gltfMaterials, p.materialIndex), _doubleSidedOf(instanced.gltfMaterials, p.materialIndex), p.unpaintedBack),
      ];
      expect(instSides, contains(((200, 10, 10), true, false)));
      expect(instSides, contains((front, false, false)));
      expect(instSides, contains((back, false, false)));
      expect(instPrims.any((p) => p.unpaintedBack), isFalse);
    });

    test('emits one instanced mesh resource when an unpainted component sits on two tags', () {
      final builder = create();
      final tagA = builder.addLayer('A', color: [255, 0, 0]);
      final tagB = builder.addLayer('B', color: [0, 0, 255]);
      final part = builder.addComponentDefinition('Part', (def) {
        def.addFace([(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 1.0, 0.0), (0.0, 1.0, 0.0)]);
      });
      builder.addInstance(part, layer: tagA);
      builder.addInstance(part, layer: tagB, translation: (5.0, 0.0, 0.0));

      final parsed = Core.fullParse(Uint8List.fromList(builder.toBytes()));
      final layerAId = parsed.layerIdToName.entries.firstWhere((e) => e.value == 'A').key;
      final layerBId = parsed.layerIdToName.entries.firstWhere((e) => e.value == 'B').key;
      parsed.root.builder.instances[0].children = [
        TlvNode(
          offset: 0,
          tag: 'D007',
          size: 0,
          children: [
            TlvNode(offset: 0, tag: 'D207', size: 1, payload: Uint8List.fromList([layerAId])),
          ],
        ),
      ];
      parsed.root.builder.instances[1].children = [
        TlvNode(
          offset: 0,
          tag: 'D007',
          size: 0,
          children: [
            TlvNode(offset: 0, tag: 'D207', size: 1, payload: Uint8List.fromList([layerBId])),
          ],
        ),
      ];

      expect(InstancedSceneBuilder.build(parsed).meshResources.length, 1);
      expect(
        InstancedSceneBuilder.build(parsed, const ParseOptions(unpaintedFaces: 'tag')).meshResources.length,
        2,
      );
    });

    test('single-colour exports keep one copy of each unpainted face', () {
      final skp = SkpFile.fromBuffer(_boxBytes());
      final style = skp.buildScene();
      final tag = skp.buildScene(const ParseOptions(unpaintedFaces: 'tag'));

      int sumTris(List<GlbPrimitive> prims) =>
          prims.fold(0, (acc, p) => acc + p.indices.length ~/ 3);
      expect(sumTris(style.glbPrimitives), 24);
      expect(sumTris(style.surfacePrimitives), 12);
      expect(identical(style.surfacePrimitives.first, style.glbPrimitives.first), isTrue);

      expect(toStlAscii(style), toStlAscii(tag));
      final stlBin = toStlBinary(style);
      expect(ByteData.sublistView(stlBin).getUint32(80, Endian.little), 12);
      expect('\nf '.allMatches(toObj(style)).length, 12);
      expect('\nf '.allMatches(toObj(tag)).length, 12);
      expect(toPlyAscii(style), contains('element face 12\n'));

      final dxfStyle = '3DFACE'.allMatches(toDxf(style, mode: '3dface')).length;
      final dxfTag = '3DFACE'.allMatches(toDxf(tag, mode: '3dface')).length;
      expect(dxfStyle, greaterThan(0));
      expect(dxfStyle, dxfTag);
      expect('IFCTRIANGULATEDFACESET'.allMatches(toIfc(style)).length, 1);
      expect('IFCTRIANGULATEDFACESET'.allMatches(toIfc(tag)).length, 1);
    });

    test('GLB and instanced GLB exports show both unpainted sides by default', () {
      final skp = SkpFile.open(_fixture('face_colors_2017.skp'));
      final layer = skp.parse().layers.first;
      final layerRgb = (layer.colorR, layer.colorG, layer.colorB);
      final expectedStyle = [(_front, false), (_back, false)]
        ..sort((a, b) => a.$1.$1.compareTo(b.$1.$1));

      expect(_glbMaterials(toGlb(skp.buildScene())), expectedStyle);
      expect(
        _glbMaterials(toGlb(skp.buildScene(const ParseOptions(unpaintedFaces: 'tag')))),
        [(layerRgb, true)],
      );

      expect(_glbMaterials(toInstancedGlb(skp.buildInstancedScene())), expectedStyle);
      expect(
        _glbMaterials(toInstancedGlb(skp.buildInstancedScene(const ParseOptions(unpaintedFaces: 'tag')))),
        [(layerRgb, true)],
      );
    });
  });
}
