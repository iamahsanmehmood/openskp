import 'dart:typed_data';
import 'package:openskp/openskp.dart';
import 'package:test/test.dart';

void checkMaterialNames(List<String?> rawNames, List<String> expected) {
  final scene = Scene(
    sceneHierarchy: InstanceNode(),
    meshIndex: {},
    glbPrimitives: [
      for (var i = 0; i < rawNames.length; i++)
        GlbPrimitive(
          geomName: 'part' + i.toString(),
          materialIndex: i,
          positions: Float32List.fromList([0, 0, 0, 1, 0, 0, 0, 1, 0]),
          normals: Float32List(0),
          uvs: Float32List(0),
          indices: Uint32List.fromList([0, 1, 2]),
        )
    ],
    gltfMaterials: [
      for (final name in rawNames)
        {
          if (name != null) 'name': name,
          'pbrMetallicRoughness': {
            'baseColorFactor': [1, 0, 0, 1]
          }
        }
    ],
  );

  List<String> references(String text, String prefix) =>
      text.split('\n').where((line) => line.startsWith(prefix))
          .map((line) => line.substring(prefix.length).trimRight()).toList();

  final mtlNames = references(toMtl(scene), 'newmtl ');
  final objNames = references(toObj(scene), 'usemtl ');
  expect(mtlNames, expected);
  expect(objNames, expected);
  expect(mtlNames.toSet().length, expected.length);
}

void main() {
  test('sanitized and reserved material names stay distinct', () {
    checkMaterialNames(['Wood/Oak', 'Wood_Oak', 'Wood_Oak_2'],
        ['Wood_Oak', 'Wood_Oak_2_1', 'Wood_Oak_2']);
  });
  test('non-colliding names stay unchanged', () {
    checkMaterialNames(['Oak', 'Pine'], ['Oak', 'Pine']);
  });
  test('three identical names get distinct suffixes', () {
    checkMaterialNames(['A', 'A', 'A'], ['A', 'A_2', 'A_3']);
  });
  test('unnamed material does not reuse an existing fallback name', () {
    checkMaterialNames(['Material_1', null], ['Material_1', 'Material_1_2']);
  });
  test('blank trimmed names fall back to distinct identifiers', () {
    checkMaterialNames([' ', '   '], ['default_material', 'default_material_2']);
  });
  test('punctuation names are sanitized and disambiguated', () {
    checkMaterialNames(['!!!', '???'], ['___', '____2']);
  });
  test('case-distinct names remain unchanged', () {
    checkMaterialNames(['Oak', 'oak'], ['Oak', 'oak']);
  });
}
