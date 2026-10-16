import 'dart:typed_data';
import 'package:openskp/openskp.dart';
import 'package:test/test.dart';

void main() {
  test('PLY writes nested glTF RGBA in ASCII and binary', () {
    final scene = Scene(
      sceneHierarchy: InstanceNode(),
      meshIndex: {},
      glbPrimitives: [
        GlbPrimitive(
          geomName: 'Red',
          materialIndex: 0,
          positions: Float32List.fromList([0, 0, 0]),
          normals: Float32List.fromList([0, 0, 1]),
          uvs: Float32List.fromList([0, 0]),
          indices: Uint32List(0),
        ),
      ],
      gltfMaterials: [
        {'pbrMetallicRoughness': {'baseColorFactor': [1.0, 0.0, 0.0, 0.5]}}
      ],
    );

    final vertex = toPlyAscii(scene).split('end_header\n')[1].split('\n')[0];
    expect(vertex.endsWith('255 0 0 128'), isTrue);

    final bytes = toPlyBinary(scene);
    final start = String.fromCharCodes(bytes).indexOf('end_header\n') +
        'end_header\n'.length;
    expect(bytes.sublist(start + 32, start + 36), [255, 0, 0, 128]);
  });
}
