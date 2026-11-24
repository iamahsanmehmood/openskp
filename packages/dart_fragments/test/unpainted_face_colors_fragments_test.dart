import 'dart:typed_data';

import 'package:openskp/openskp.dart';
import 'package:openskp_fragments/openskp_fragments.dart';
import 'package:test/test.dart';

void main() {
  test('Fragments export keeps one copy of each unpainted face (#533)', () {
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
    final skp = SkpFile.fromBuffer(Uint8List.fromList(builder.toBytes()));

    int triangles(InstancedScene scene) {
      final roundTrip = fromFragments(toFragments(scene));
      var total = 0;
      for (final r in roundTrip.meshResources) {
        for (final p in r.primitives) {
          total += p.indices.length ~/ 3;
        }
      }
      return total;
    }

    expect(triangles(skp.buildInstancedScene()), 12);
    expect(triangles(skp.buildInstancedScene(const ParseOptions(unpaintedFaces: 'tag'))), 12);
  });
}
