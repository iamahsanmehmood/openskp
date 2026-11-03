import 'dart:typed_data';

import 'package:openskp/openskp.dart';
import 'package:test/test.dart';

/// A face's stored plane satisfies n.p + d = 0 for the face's own vertices.
///
/// SketchUp stores the plane that way (all 481 faces of two real files
/// checked: gondola_v20.skp and capilla_quiroz_v17.skp). The writer used to
/// store d as +n.centroid, i.e. with the wrong sign: openskp's own reader only
/// uses the normal, so a round trip never noticed, and the SDK's
/// SUFaceGetPlane recomputes the plane, so it does not either - the raw record
/// has to be read, as here (C++ fixed the same in #477).
void main() {
  // ff ff 03 00 05 00 "CFace"
  const marker = [0xFF, 0xFF, 3, 0, 5, 0, 0x43, 0x46, 0x61, 0x63, 0x65];
  final polygons = <List<(double, double, double)>>[
    [(0.0, 0.0, 3.0), (2.0, 0.0, 3.0), (2.0, 2.0, 3.0), (0.0, 2.0, 3.0)],
    [(0.0, 2.0, 3.0), (2.0, 2.0, 3.0), (2.0, 0.0, 3.0), (0.0, 0.0, 3.0)],
    [(-7.0, 0.0, 0.0), (-7.0, 2.0, 0.0), (-7.0, 2.0, 2.0), (-7.0, 0.0, 2.0)],
    [(10.0, 20.0, 35.0), (12.0, 20.0, 37.0), (12.0, 22.0, 39.0), (10.0, 22.0, 37.0)],
  ];

  int indexOf(Uint8List data) {
    for (var i = 0; i + marker.length <= data.length; i++) {
      var match = true;
      for (var j = 0; j < marker.length && match; j++) {
        match = data[i + j] == marker[j];
      }
      if (match) return i;
    }
    return -1;
  }

  List<double> storedPlane(Uint8List data) {
    final view = ByteData.sublistView(data);
    var pos = indexOf(data) + marker.length;
    expect(view.getUint16(pos, Endian.little), 0); // null attribute ref
    pos += 2;
    final mask = data[pos++];
    for (var bit = 0; bit < 8; bit++) {
      // the persistent-id bytes the mask announces
      if ((mask & (1 << bit)) != 0) pos++;
    }
    pos += 10; // material and drawing flags
    return [
      for (var i = 0; i < 4; i++) view.getFloat64(pos + 8 * i, Endian.little)
    ];
  }

  for (final nested in [false, true]) {
    for (var i = 0; i < polygons.length; i++) {
      final polygon = polygons[i];
      final where = nested ? 'in a component definition' : 'at the root';
      test('the stored plane agrees with the vertices (polygon $i, $where)', () {
        final builder = create();
        if (nested) {
          final def =
              builder.addComponentDefinition('offset', (d) => d.addFace(polygon));
          builder.addInstance(def);
        } else {
          builder.addFace(polygon);
        }
        final p = storedPlane(builder.toBytes());
        for (final (x, y, z) in polygon) {
          expect(p[0] * x + p[1] * y + p[2] * z + p[3], closeTo(0, 1e-9));
        }
      });
    }
  }
}
