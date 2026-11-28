import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:archive/archive.dart';
import 'package:openskp/openskp.dart';
import 'package:test/test.dart';

const _square = [
  (0.0, 0.0, 0.0),
  (10.0, 0.0, 0.0),
  (10.0, 10.0, 0.0),
  (0.0, 10.0, 0.0),
];

SkpFile _paintedModel() {
  final builder = create();
  final red = builder.addMaterial('Red', [200, 10, 10, 255]);
  final blue = builder.addMaterial('Blue', [10, 10, 200, 128]);
  final leaf = builder.addComponentDefinition('Leaf', (def) {
    def.addFace(_square);
  });
  final outer = builder.addComponentDefinition('Outer', (def) {
    def.addInstance(leaf, name: 'InheritsPaint');
    def.addInstance(leaf, name: 'OwnPaint', material: blue, translation: (20.0, 0.0, 0.0));
  });
  builder.addInstance(outer, name: 'Painted', material: red);
  return SkpFile.fromBuffer(Uint8List.fromList(builder.toBytes()));
}

void main() {
  test('paint is applied and inherited by nested components', () {
    final skp = _paintedModel();
    final baked = skp.buildScene();
    final instanced = skp.buildInstancedScene();

    final cases = [
      (baked.gltfMaterials, [for (final p in baked.glbPrimitives) p.materialIndex]),
      (
        instanced.gltfMaterials,
        [for (final r in instanced.meshResources) for (final p in r.primitives) p.materialIndex],
      ),
    ];

    for (final (gltfMats, matIndices) in cases) {
      final summaries = <(double, double, double, double, bool, String)>{};
      for (final idx in matIndices) {
        final m = gltfMats[idx];
        final pbr = m['pbrMetallicRoughness'] as Map<String, dynamic>;
        final bcf = (pbr['baseColorFactor'] as List).cast<num>();
        double r6(num v) => (v.toDouble() * 1e6).roundToDouble() / 1e6;
        summaries.add((
          r6(bcf[0]),
          r6(bcf[1]),
          r6(bcf[2]),
          r6(bcf[3]),
          m['doubleSided'] == true,
          (m['alphaMode'] as String?) ?? 'OPAQUE',
        ));
      }
      double r6(double v) => (v * 1e6).roundToDouble() / 1e6;
      expect(summaries, {
        (r6(200 / 255), r6(10 / 255), r6(10 / 255), 1.0, true, 'OPAQUE'),
        (r6(10 / 255), r6(10 / 255), r6(200 / 255), r6(128 / 255), true, 'BLEND'),
      });
    }
  });

  test('each paint is its own instanced mesh', () {
    final scene = _paintedModel().buildInstancedScene();
    final nodes = {for (final n in scene.sceneHierarchy.children[0].children) n.name: n};
    expect(nodes['InheritsPaint']!.meshResourceId, isNotNull);
    expect(nodes['OwnPaint']!.meshResourceId, isNotNull);
    expect(nodes['InheritsPaint']!.meshResourceId, isNot(nodes['OwnPaint']!.meshResourceId));
  });

  test("tag is the instance's own", () {
    final builder = create();
    final walls = builder.addLayer('Walls');
    final part = builder.addComponentDefinition('Part', (def) {
      def.addFace(_square);
    });
    builder.addInstance(part, name: 'Tagged', layer: walls);
    builder.addInstance(part, name: 'Untagged', translation: (20.0, 0.0, 0.0));

    final skp = SkpFile.fromBuffer(Uint8List.fromList(builder.toBytes()));
    final baked = skp.buildScene();
    final instanced = skp.buildInstancedScene();

    expect(
      {for (final n in baked.sceneHierarchy.children) n.name: n.layer},
      {'Tagged': 'Walls', 'Untagged': 'Layer0'},
    );
    expect(
      {for (final n in instanced.sceneHierarchy.children) n.name: n.layer},
      {'Tagged': 'Walls', 'Untagged': 'Layer0'},
    );
  });

  const png1x1 = <int>[
    0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A, 0x00, 0x00, 0x00, 0x0D, 0x49, 0x48,
    0x44, 0x52, 0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x01, 0x08, 0x02, 0x00, 0x00,
    0x00, 0x90, 0x77, 0x53, 0xDE, 0x00, 0x00, 0x00, 0x0C, 0x49, 0x44, 0x41, 0x54, 0x78,
    0x9C, 0x63, 0x38, 0x91, 0x62, 0x04, 0x00, 0x03, 0x56, 0x01, 0x5F, 0xE8, 0x17, 0x84,
    0x52, 0x00, 0x00, 0x00, 0x00, 0x49, 0x45, 0x4E, 0x44, 0xAE, 0x42, 0x60, 0x82,
  ];

  const png2x1 = <int>[
    0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A, 0x00, 0x00, 0x00, 0x0D, 0x49, 0x48,
    0x44, 0x52, 0x00, 0x00, 0x00, 0x02, 0x00, 0x00, 0x00, 0x01, 0x08, 0x02, 0x00, 0x00,
    0x00, 0x7B, 0x40, 0xE8, 0xDD, 0x00, 0x00, 0x00, 0x0F, 0x49, 0x44, 0x41, 0x54, 0x78,
    0x9C, 0x63, 0x30, 0x4A, 0x39, 0x61, 0x94, 0x72, 0x02, 0x00, 0x08, 0x6D, 0x02, 0xBD,
    0xEB, 0xAE, 0x9B, 0xE7, 0x00, 0x00, 0x00, 0x00, 0x49, 0x45, 0x4E, 0x44, 0xAE, 0x42,
    0x60, 0x82,
  ];

  (double, double, double, double) uvBounds(List<double> uvs) {
    var minU = uvs[0], maxU = uvs[0], minV = uvs[1], maxV = uvs[1];
    for (var i = 0; i + 1 < uvs.length; i += 2) {
      if (uvs[i] < minU) minU = uvs[i];
      if (uvs[i] > maxU) maxU = uvs[i];
      if (uvs[i + 1] < minV) minV = uvs[i + 1];
      if (uvs[i + 1] > maxV) maxV = uvs[i + 1];
    }
    double r4(double v) => (v * 1e4).roundToDouble() / 1e4;
    return (r4(minU), r4(maxU), r4(minV), r4(maxV));
  }

  test('SDK classic and 2021+ fixtures preserve textured instance paint, UVs, and tags (#559)', () {
    for (final fname in ['instance_textured_paint_2020.skp', 'instance_textured_paint_2021.skp']) {
      final skp = SkpFile.open('${Directory.current.path}/test/fixtures/$fname');
      final baked = skp.buildScene();
      final instanced = skp.buildInstancedScene();

      expect(baked.textures.length, 2);
      expect(instanced.textures.length, 2);
      expect(baked.glbPrimitives.length, 10);
      expect(instanced.meshResources.length, 6);

      const expectedTopLayers = {
        'BrickPanel': 'ExteriorTag',
        'WoodPanel': 'InteriorTag',
        'PlainPanel': 'Layer0',
        'BrickGroup': 'ExteriorTag',
        'OuterInst': 'ExteriorTag',
      };
      expect({for (final n in baked.sceneHierarchy.children) n.name: n.layer}, expectedTopLayers);
      expect(
        {for (final n in instanced.sceneHierarchy.children) n.name: n.layer},
        expectedTopLayers,
      );

      const expectedNestedLayers = {
        'ChildInherits': 'ExteriorTag',
        'ChildOverrides': 'InteriorTag',
      };
      final bakedOuter =
          baked.sceneHierarchy.children.firstWhere((n) => n.name == 'OuterInst');
      final instOuter =
          instanced.sceneHierarchy.children.firstWhere((n) => n.name == 'OuterInst');
      expect({for (final n in bakedOuter.children) n.name: n.layer}, expectedNestedLayers);
      expect({for (final n in instOuter.children) n.name: n.layer}, expectedNestedLayers);

      final byId = {for (final r in instanced.meshResources) r.id: r};
      final top = {for (final n in instanced.sceneHierarchy.children) n.name: n};
      final nested = {for (final n in instOuter.children) n.name: n};

      int? texIndexOf(LocalPrimitive p) {
        final m = instanced.gltfMaterials[p.materialIndex];
        final pbr = m['pbrMetallicRoughness'] as Map<String, dynamic>;
        final tex = pbr['baseColorTexture'] as Map<String, dynamic>?;
        return tex?['index'] as int?;
      }

      final grpPrim = byId[top['BrickGroup']!.meshResourceId!]!.primitives.single;
      final inhPrim = byId[nested['ChildInherits']!.meshResourceId!]!.primitives.single;
      final ovrPrim = byId[nested['ChildOverrides']!.meshResourceId!]!.primitives.single;

      expect(texIndexOf(grpPrim), isNotNull);
      expect(texIndexOf(ovrPrim), isNotNull);
      expect(texIndexOf(grpPrim), isNot(texIndexOf(ovrPrim)));
      expect(uvBounds(grpPrim.uvs), (0.0, 1.0, 0.0, 1.0));
      expect(uvBounds(inhPrim.uvs), (0.0, 1.0, 0.0, 1.0));
      expect(uvBounds(ovrPrim.uvs), (0.0, 2.5, 0.0, 2.5));
    }
  });

  test('writer unnamed ("") and named textured materials resolve on instances and groups (#559)', () {
    final tmpDir = Directory.systemTemp.createTempSync('openskp-dart-559-');
    try {
      final tex1 = '${tmpDir.path}/a.png';
      final tex2 = '${tmpDir.path}/b.png';
      File(tex1).writeAsBytesSync(png1x1);
      File(tex2).writeAsBytesSync(png2x1);

      const rect = [
        (0.0, 0.0, 0.0),
        (10.0, 0.0, 0.0),
        (10.0, 20.0, 0.0),
        (0.0, 20.0, 0.0),
      ];
      final builder = create();
      final namedMat =
          builder.addTextureMaterial('Brick', tex1, appliedHeight: 20.0, appliedWidth: 10.0);
      final unnamedMat =
          builder.addTextureMaterial('', tex2, appliedHeight: 8.0, appliedWidth: 4.0);
      final ext = builder.addLayer('Ext');
      final inter = builder.addLayer('Int');
      final panel = builder.addComponentDefinition('Panel', (def) {
        def.addFace(rect);
      });
      builder.addGroup(
        (grp) {
          grp.addFace(rect);
        },
        name: 'PaintedGroup',
        translation: (40.0, 0.0, 0.0),
        material: namedMat,
        layer: ext,
      );
      builder.addInstance(panel, name: 'NamedInst', material: namedMat, layer: ext);
      builder.addInstance(
        panel,
        name: 'UnnamedInst',
        translation: (20.0, 0.0, 0.0),
        material: unnamedMat,
        layer: inter,
      );

      final skp = SkpFile.fromBuffer(Uint8List.fromList(builder.toBytes()));
      final baked = skp.buildScene();
      final instanced = skp.buildInstancedScene();

      expect(baked.textures.length, 2);
      expect(instanced.textures.length, 2);
      const expectedLayers = {
        'PaintedGroup': 'Ext',
        'NamedInst': 'Ext',
        'UnnamedInst': 'Int',
      };
      expect({for (final n in baked.sceneHierarchy.children) n.name: n.layer}, expectedLayers);
      expect({for (final n in instanced.sceneHierarchy.children) n.name: n.layer}, expectedLayers);

      final byId = {for (final r in instanced.meshResources) r.id: r};
      final nodes = {for (final n in instanced.sceneHierarchy.children) n.name: n};
      expect(nodes['NamedInst']!.meshResourceId, isNot(nodes['UnnamedInst']!.meshResourceId));
      expect(uvBounds(byId[nodes['NamedInst']!.meshResourceId!]!.primitives[0].uvs), (0.0, 1.0, 0.0, 1.0));
      expect(uvBounds(byId[nodes['UnnamedInst']!.meshResourceId!]!.primitives[0].uvs), (0.0, 2.5, 0.0, 2.5));
    } finally {
      tmpDir.deleteSync(recursive: true);
    }
  });

  test('instanced scene isolates same-name same-RGB inherited material variants (#559)', () {
    final raw = File(
      '${Directory.current.path}/test/fixtures/instance_textured_paint_2021.skp',
    ).readAsBytesSync();
    var pkPos = 0;
    for (var i = 0; i + 4 <= raw.length; i++) {
      if (raw[i] == 0x50 && raw[i + 1] == 0x4B && raw[i + 2] == 0x03 && raw[i + 3] == 0x04) {
        pkPos = i;
        break;
      }
    }

    final archive = ZipDecoder().decodeBytes(raw.sublist(pkPos));
    final outArchive = Archive();
    for (final entry in archive.files) {
      var data = entry.content as List<int>;
      if (entry.name == 'materials/BrickTex/material.xml' ||
          entry.name == 'materials/WoodTex/material.xml') {
        var xml = utf8.decode(data)
            .replaceAll('name="BrickTex"', 'name="Shared"')
            .replaceAll('name="WoodTex"', 'name="Shared"')
            .replaceAll(RegExp(r'colorRed="\d+"'), 'colorRed="170"')
            .replaceAll(RegExp(r'colorGreen="\d+"'), 'colorGreen="58"')
            .replaceAll(RegExp(r'colorBlue="\d+"'), 'colorBlue="40"');
        data = utf8.encode(xml);
      }
      outArchive.addFile(ArchiveFile(entry.name, data.length, data));
    }
    final zipBytes = ZipEncoder().encode(outArchive);
    final fullBuf = Uint8List(pkPos + zipBytes.length)
      ..setRange(0, pkPos, raw)
      ..setRange(pkPos, pkPos + zipBytes.length, zipBytes);

    final instanced = SkpFile.fromBuffer(fullBuf).buildInstancedScene();
    final byId = {for (final r in instanced.meshResources) r.id: r};
    final top = {for (final n in instanced.sceneHierarchy.children) n.name: n};
    final nested = {for (final n in top['OuterInst']!.children) n.name: n};

    expect(top['BrickPanel']!.meshResourceId, isNot(top['WoodPanel']!.meshResourceId));
    expect(nested['ChildInherits']!.meshResourceId, isNot(nested['ChildOverrides']!.meshResourceId));
    expect(
      uvBounds(byId[nested['ChildInherits']!.meshResourceId!]!.primitives[0].uvs),
      (0.0, 1.0, 0.0, 1.0),
    );
    expect(
      uvBounds(byId[nested['ChildOverrides']!.meshResourceId!]!.primitives[0].uvs),
      (0.0, 2.5, 0.0, 2.5),
    );
  });

  const pngBrick4x4 = <int>[
    0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A, 0x00, 0x00, 0x00, 0x0D, 0x49, 0x48,
    0x44, 0x52, 0x00, 0x00, 0x00, 0x04, 0x00, 0x00, 0x00, 0x04, 0x08, 0x02, 0x00, 0x00,
    0x00, 0x26, 0x93, 0x09, 0x29, 0x00, 0x00, 0x00, 0x18, 0x49, 0x44, 0x41, 0x54, 0x78,
    0xDA, 0x63, 0xD8, 0xE0, 0x62, 0x00, 0x41, 0x97, 0xCE, 0x1E, 0x66, 0x80, 0x50, 0x10,
    0x3E, 0x03, 0x4E, 0x19, 0x00, 0x5A, 0xCA, 0x17, 0x39, 0xF4, 0x43, 0xAB, 0x79, 0x00,
    0x00, 0x00, 0x00, 0x49, 0x45, 0x4E, 0x44, 0xAE, 0x42, 0x60, 0x82,
  ];

  SkpFile buildGroundTruthWriterSkp() {
    final tmpDir = Directory.systemTemp.createTempSync('openskp-dart-gt-559-');
    try {
      final brickPng = '${tmpDir.path}/brick.png';
      File(brickPng).writeAsBytesSync(pngBrick4x4);

      final b = create();
      final plainRed = b.addMaterial('PlainRed', [210, 50, 40, 255]);
      final brickTex = b.addTextureMaterial(
        'BrickTex',
        brickPng,
        appliedHeight: 20.0,
        appliedWidth: 10.0,
        filename: 'brick.png',
      );
      final ownGold = b.addMaterial('OwnGold', [230, 180, 30, 255]);
      final tag = b.addLayer('PaintedTag', color: [40, 120, 200]);

      const fUnp = [
        (0.0, 0.0, 0.0),
        (10.0, 0.0, 0.0),
        (10.0, 20.0, 0.0),
        (0.0, 20.0, 0.0),
      ];
      const fOwn = [
        (0.0, 30.0, 0.0),
        (10.0, 30.0, 0.0),
        (10.0, 50.0, 0.0),
        (0.0, 50.0, 0.0),
      ];

      final panel = b.addComponentDefinition('PanelDef', (def) {
        def.addFace(fUnp);
        def.addFace(fOwn, material: ownGold, backMaterial: ownGold);
      });
      final outer = b.addComponentDefinition('OuterDef', (def) {
        def.addInstance(panel, name: 'InnerUnpainted', translation: (5.0, 0.0, 0.0));
      });
      b.addGroup(
        (g) {
          g.addFace(fUnp);
          g.addFace(fOwn, material: ownGold, backMaterial: ownGold);
        },
        name: 'PlainGroup',
        translation: (60.0, 0.0, 0.0),
        material: plainRed,
        layer: tag,
      );
      b.addGroup(
        (g) {
          g.addFace(fUnp);
          g.addFace(fOwn, material: ownGold, backMaterial: ownGold);
        },
        name: 'TexturedGroup',
        translation: (90.0, 0.0, 0.0),
        material: brickTex,
        layer: tag,
      );
      b.addInstance(
        panel,
        name: 'PlainComp',
        translation: (0.0, 0.0, 0.0),
        material: plainRed,
        layer: tag,
      );
      b.addInstance(
        panel,
        name: 'TexturedComp',
        translation: (30.0, 0.0, 0.0),
        material: brickTex,
        layer: tag,
      );
      b.addInstance(
        outer,
        name: 'OuterTextured',
        translation: (120.0, 0.0, 0.0),
        material: brickTex,
        layer: tag,
      );
      return SkpFile.fromBuffer(Uint8List.fromList(b.toBytes()));
    } finally {
      tmpDir.deleteSync(recursive: true);
    }
  }

  test('matches SketchUp ground truth across 2017, 2020, 2021+ and writer-built files (#559)', () {
    final fixturesDir = '${Directory.current.path}/test/fixtures';
    final sources = <(String, SkpFile)>[
      ('2017', SkpFile.open('$fixturesDir/sdk_scene_instance_paint_2017.skp')),
      ('2020', SkpFile.open('$fixturesDir/sdk_scene_instance_paint_2020.skp')),
      ('2021', SkpFile.open('$fixturesDir/sdk_scene_instance_paint_2021.skp')),
      ('writer', buildGroundTruthWriterSkp()),
    ];

    const expectedParse = <String, (bool, String, String?, bool)>{
      'PlainComp': (false, 'PaintedTag', 'PlainRed', false),
      'TexturedComp': (false, 'PaintedTag', 'BrickTex', true),
      'PlainGroup': (true, 'PaintedTag', 'PlainRed', false),
      'TexturedGroup': (true, 'PaintedTag', 'BrickTex', true),
      'OuterTextured': (false, 'PaintedTag', 'BrickTex', true),
      'OuterTextured / InnerUnpainted': (false, '', null, false),
    };
    final expectedNodeLayers = {for (final k in expectedParse.keys) k: 'PaintedTag'};
    const plainParts = <(String, (int, int, int, int), bool, (double, double, double, double))>[
      ('OwnGold', (230, 180, 30, 255), false, (0.0, 10.0, 30.0, 50.0)),
      ('PlainRed', (210, 50, 40, 255), false, (0.0, 10.0, 0.0, 20.0)),
    ];

    for (final (label, skp) in sources) {
      final brickRgba = label == 'writer' ? (255, 255, 255, 255) : (184, 102, 84, 255);
      final texturedParts =
          <(String, (int, int, int, int), bool, (double, double, double, double))>[
        ('BrickTex', brickRgba, true, (0.0, 1.0, 0.0, 1.0)),
        ('OwnGold', (230, 180, 30, 255), false, (0.0, 10.0, 30.0, 50.0)),
      ];
      final expectedLeafParts =
          <String, List<(String, (int, int, int, int), bool, (double, double, double, double))>>{
        'PlainComp': plainParts,
        'PlainGroup': plainParts,
        'TexturedComp': texturedParts,
        'TexturedGroup': texturedParts,
        'OuterTextured / InnerUnpainted': texturedParts,
      };
      final model = skp.parse();
      final parsedNodes = <String, (bool, String, String?, bool)>{};
      void walkModel(Definition defn, String prefix) {
        for (final ins in defn.instances) {
          final cdef = model.definitions[ins.refIdx!]!;
          final p = prefix.isEmpty ? ins.name : '$prefix / ${ins.name}';
          final mat = ins.materialId != null ? model.materialsById[ins.materialId!] : null;
          final texData = mat?.texture?.data;
          parsedNodes[p] = (
            cdef.isGroup,
            ins.layer,
            mat?.name,
            texData != null && texData.isNotEmpty,
          );
          walkModel(cdef, p);
        }
      }

      walkModel(model.root, '');
      expect(parsedNodes, expectedParse, reason: label);

      final baked = skp.buildScene();
      final instanced = skp.buildInstancedScene();
      expect(baked.textures.length, 1, reason: label);
      expect(instanced.textures.length, 1, reason: label);
      expect(baked.glbPrimitives.length, 10, reason: label);
      expect(instanced.meshResources.length, 4, reason: label);

      final bakedLayers = <String, String>{};
      void walkBaked(InstanceNode node, String prefix) {
        for (final ch in node.children) {
          final p = prefix.isEmpty ? ch.name : '$prefix / ${ch.name}';
          bakedLayers[p] = ch.layer;
          walkBaked(ch, p);
        }
      }

      walkBaked(baked.sceneHierarchy, '');
      expect(bakedLayers, expectedNodeLayers, reason: label);

      final instLayers = <String, String>{};
      final instNodes = <String, InstancedNode>{};
      void walkInst(InstancedNode node, String prefix) {
        for (final ch in node.children) {
          final p = prefix.isEmpty ? ch.name : '$prefix / ${ch.name}';
          instLayers[p] = ch.layer;
          instNodes[p] = ch;
          walkInst(ch, p);
        }
      }

      walkInst(instanced.sceneHierarchy, '');
      expect(instLayers, expectedNodeLayers, reason: label);

      (String, (int, int, int, int), bool, (double, double, double, double)) partTuple(
        Map<String, dynamic> gm,
        List<double> uvs,
      ) {
        final pbr = gm['pbrMetallicRoughness'] as Map<String, dynamic>;
        final bcf = (pbr['baseColorFactor'] as List).cast<num>();
        final rgba = (
          (bcf[0].toDouble() * 255).round(),
          (bcf[1].toDouble() * 255).round(),
          (bcf[2].toDouble() * 255).round(),
          (bcf[3].toDouble() * 255).round(),
        );
        final texMap = pbr['baseColorTexture'] as Map<String, dynamic>?;
        expect(gm['doubleSided'], isTrue, reason: label);
        return (
          (gm['name'] as String?) ?? '',
          rgba,
          texMap != null && texMap['index'] == 0,
          uvBounds(uvs),
        );
      }

      final bakedByPath =
          <String, List<(String, (int, int, int, int), bool, (double, double, double, double))>>{};
      for (final p in baked.glbPrimitives) {
        final meta = baked.meshIndex[p.geomName]!;
        expect(meta.layer, 'PaintedTag', reason: label);
        final rel = meta.path.startsWith('ROOT / ') ? meta.path.substring(7) : meta.path;
        bakedByPath.putIfAbsent(rel, () => []).add(partTuple(baked.gltfMaterials[p.materialIndex], p.uvs));
      }
      for (final list in bakedByPath.values) {
        list.sort((a, b) => a.$1.compareTo(b.$1));
      }
      expect(bakedByPath, expectedLeafParts, reason: label);

      expect(instNodes['OuterTextured']!.meshResourceId, isNull, reason: label);
      expect(
        instNodes['TexturedComp']!.meshResourceId,
        instNodes['OuterTextured / InnerUnpainted']!.meshResourceId,
        reason: label,
      );
      expect(
        instNodes['PlainComp']!.meshResourceId,
        isNot(instNodes['TexturedComp']!.meshResourceId),
        reason: label,
      );

      final byId = {for (final r in instanced.meshResources) r.id: r};
      final instByPath =
          <String, List<(String, (int, int, int, int), bool, (double, double, double, double))>>{};
      for (final k in expectedLeafParts.keys) {
        final res = byId[instNodes[k]!.meshResourceId!]!;
        final parts = [
          for (final pr in res.primitives) partTuple(instanced.gltfMaterials[pr.materialIndex], pr.uvs),
        ]..sort((a, b) => a.$1.compareTo(b.$1));
        instByPath[k] = parts;
      }
      expect(instByPath, expectedLeafParts, reason: label);
    }
  });
}
