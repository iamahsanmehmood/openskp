import 'dart:io';

import 'package:openskp/openskp.dart';
import 'package:test/test.dart';

/// Real-file regression tests for the modern (2021+) VFF/ZIP reader,
/// mirroring the values already cross-validated against Python in the
/// TypeScript and C# ports' equivalent integration tests.
void main() {
  String fixture(String name) =>
      '${Directory.current.path}/test/fixtures/$name';

  test('parses Untitled.skp correctly', () {
    final model = SkpFile.open(fixture('Untitled.skp')).parse();

    expect(model.version, '{25.0.575}');

    expect(model.layers.length, 14);
    const expectedLayers = [
      'Layer0',
      'BottomPlate',
      'TopPlate',
      'Stud',
      'Nog',
      'KingStud',
      'HeaderJackStud',
      'HeaderPlate1',
      'HeaderPlate2',
      'SillPlate1',
      'VerticalHeaderStud',
      'generic_frame',
      'dimension',
      'Hat Sections',
    ];
    final parsedLayers = model.layers.map((l) => l.name).toList();
    for (final name in expectedLayers) {
      expect(parsedLayers, contains(name));
    }

    expect(model.materials.length, 15);
    const expectedMaterials = [
      '*',
      'Layer_Layer0',
      'Layer_BottomPlate',
      'Layer_TopPlate',
      'Layer_Stud',
      'Layer_Nog',
      'Layer_KingStud',
      'Layer_HeaderJackStud',
      'Layer_HeaderPlate1',
      'Layer_HeaderPlate2',
      'Layer_SillPlate1',
      'Layer_VerticalHeaderStud',
      'Layer_generic_frame',
      'Layer_dimension',
      'Layer_Hat Sections',
    ];
    final parsedMaterials = model.materials.map((m) => m.name).toList();
    for (final name in expectedMaterials) {
      expect(parsedMaterials, contains(name));
    }

    final matLayer0 =
        model.materials.firstWhere((m) => m.name == 'Layer_Layer0');
    // Real data: none of this fixture's materials have useTrans="1" set, so
    // all correctly read fully opaque.
    expect(matLayer0.transparency, 1.0);
    expect(matLayer0.id, isNull);
    expect(matLayer0.texture, isNull);
    expect(matLayer0.colorized, isFalse);
    expect(matLayer0.colorizeType, 0);

    expect(model.definitions.length, 46);

    final def66 = model.definitions[66];
    expect(def66, isNotNull);
    expect(def66!.name, 'Group200#2');
    expect(def66.guid.length, 32);

    expect(def66.vertices.length, 136);
    expect(def66.edges.length, 158);
    expect(def66.faces.length, 26);

    final firstFace = def66.faces.values.first;
    expect(firstFace.loops, isNotEmpty);
    expect(firstFace.loops[0], isNotEmpty);
    expect(firstFace.backMaterialId, isNull);
    expect(firstFace.uvTransform, isNull);
    expect(firstFace.uvTransformBack, isNull);
    expect(firstFace.normal, isNotNull);

    final firstEdge = def66.edges.values.first;
    expect(firstEdge.soft, isFalse);
    expect(firstEdge.smooth, isFalse);
    expect(firstEdge.hidden, isFalse);

    expect(def66.isImage, isFalse);
    expect(def66.alwaysFacesCamera, isFalse);

    // materialsById join: TLV material ID 26180 resolves to the default "*"
    // material, and the resolved object is the SAME instance held in
    // model.materials (the join shares identity).
    final joined = model.materialsById[26180];
    expect(joined, isNotNull);
    expect(joined!.name, '*');
    expect(identical(model.materials.firstWhere((m) => m.name == '*'), joined),
        isTrue);
    expect(joined.id, 26180);

    // Real style data: this fixture bundles two style.xml files (the second
    // is SketchUp's "_1" duplicate-naming convention), both named
    // "[Construction Documentation Style]" with the same front/back colors.
    expect(model.styles.length, 2);
    expect(model.styles[0].name, '[Construction Documentation Style]');
    expect(model.styles[0].frontColor, (255, 255, 255));
    expect(model.styles[0].backColor, (208, 209, 189));
  });

  test('parses SU_File.skp correctly', () {
    final model = SkpFile.open(fixture('SU_File.skp')).parse();

    expect(model.version, '{25.0.575}');

    expect(model.layers.length, 1);
    expect(model.layers[0].name, 'Layer0');

    expect(model.materials.length, 1);
    expect(model.materials[0].name, 'Layer_Layer0');

    // Only ROOT holds geometry in this fixture, so the numeric definitions
    // map (which excludes ROOT) is empty.
    expect(model.definitions, isEmpty);
    expect(model.root.name, 'ROOT_MODEL');
  });
}
