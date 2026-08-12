import 'dart:io';
import 'dart:math' as math;
import 'dart:typed_data';

import 'scene.dart';

(double, double, double) _calculateNormal(
  (double, double, double) v0,
  (double, double, double) v1,
  (double, double, double) v2,
) {
  final e1x = v1.$1 - v0.$1;
  final e1y = v1.$2 - v0.$2;
  final e1z = v1.$3 - v0.$3;

  final e2x = v2.$1 - v0.$1;
  final e2y = v2.$2 - v0.$2;
  final e2z = v2.$3 - v0.$3;

  final nx = e1y * e2z - e1z * e2y;
  final ny = e1z * e2x - e1x * e2z;
  final nz = e1x * e2y - e1y * e2x;

  final len = math.sqrt(nx * nx + ny * ny + nz * nz);
  if (len > 1e-12) {
    return (nx / len, ny / len, nz / len);
  }
  return (0.0, 0.0, 0.0);
}

/// Convert a baked [Scene] into ASCII STL text format.
String toStlAscii(Scene scene, {double scale = 1.0}) {
  final buffer = StringBuffer();
  buffer.writeln('solid OpenSKP_Model');

  for (final prim in scene.glbPrimitives) {
    final triCount = prim.indices.length ~/ 3;
    for (var i = 0; i < triCount; i++) {
      final i0 = prim.indices[i * 3];
      final i1 = prim.indices[i * 3 + 1];
      final i2 = prim.indices[i * 3 + 2];

      final v0 = (
        prim.positions[i0 * 3] * scale,
        prim.positions[i0 * 3 + 1] * scale,
        prim.positions[i0 * 3 + 2] * scale,
      );
      final v1 = (
        prim.positions[i1 * 3] * scale,
        prim.positions[i1 * 3 + 1] * scale,
        prim.positions[i1 * 3 + 2] * scale,
      );
      final v2 = (
        prim.positions[i2 * 3] * scale,
        prim.positions[i2 * 3 + 1] * scale,
        prim.positions[i2 * 3 + 2] * scale,
      );

      final normal = _calculateNormal(v0, v1, v2);

      buffer.writeln('  facet normal ${normal.$1.toStringAsFixed(6)} ${normal.$2.toStringAsFixed(6)} ${normal.$3.toStringAsFixed(6)}');
      buffer.writeln('    outer loop');
      buffer.writeln('      vertex ${v0.$1.toStringAsFixed(6)} ${v0.$2.toStringAsFixed(6)} ${v0.$3.toStringAsFixed(6)}');
      buffer.writeln('      vertex ${v1.$1.toStringAsFixed(6)} ${v1.$2.toStringAsFixed(6)} ${v1.$3.toStringAsFixed(6)}');
      buffer.writeln('      vertex ${v2.$1.toStringAsFixed(6)} ${v2.$2.toStringAsFixed(6)} ${v2.$3.toStringAsFixed(6)}');
      buffer.writeln('    endloop');
      buffer.writeln('  endfacet');
    }
  }

  buffer.writeln('endsolid OpenSKP_Model');
  return buffer.toString();
}

/// Convert a baked [Scene] into Little-Endian Binary STL byte array.
Uint8List toStlBinary(Scene scene, {double scale = 1.0}) {
  var totalTriangles = 0;
  for (final prim in scene.glbPrimitives) {
    totalTriangles += prim.indices.length ~/ 3;
  }

  final bufferSize = 80 + 4 + totalTriangles * 50;
  final bytes = Uint8List(bufferSize);
  final bdata = ByteData.view(bytes.buffer);

  // Write 80-byte header
  const headerText = '# OpenSKP Binary STL Export';
  for (var i = 0; i < 80; i++) {
    bytes[i] = i < headerText.length ? headerText.codeUnitAt(i) : 0;
  }

  // Write triangle count (uint32 Little-Endian)
  bdata.setUint32(80, totalTriangles, Endian.little);

  var offset = 84;
  for (final prim in scene.glbPrimitives) {
    final triCount = prim.indices.length ~/ 3;
    for (var i = 0; i < triCount; i++) {
      final i0 = prim.indices[i * 3];
      final i1 = prim.indices[i * 3 + 1];
      final i2 = prim.indices[i * 3 + 2];

      final v0 = (
        prim.positions[i0 * 3] * scale,
        prim.positions[i0 * 3 + 1] * scale,
        prim.positions[i0 * 3 + 2] * scale,
      );
      final v1 = (
        prim.positions[i1 * 3] * scale,
        prim.positions[i1 * 3 + 1] * scale,
        prim.positions[i1 * 3 + 2] * scale,
      );
      final v2 = (
        prim.positions[i2 * 3] * scale,
        prim.positions[i2 * 3 + 1] * scale,
        prim.positions[i2 * 3 + 2] * scale,
      );

      final normal = _calculateNormal(v0, v1, v2);

      // Normal (3x Float32)
      bdata.setFloat32(offset, normal.$1, Endian.little);
      bdata.setFloat32(offset + 4, normal.$2, Endian.little);
      bdata.setFloat32(offset + 8, normal.$3, Endian.little);

      // Vertex 0 (3x Float32)
      bdata.setFloat32(offset + 12, v0.$1, Endian.little);
      bdata.setFloat32(offset + 16, v0.$2, Endian.little);
      bdata.setFloat32(offset + 20, v0.$3, Endian.little);

      // Vertex 1 (3x Float32)
      bdata.setFloat32(offset + 24, v1.$1, Endian.little);
      bdata.setFloat32(offset + 28, v1.$2, Endian.little);
      bdata.setFloat32(offset + 32, v1.$3, Endian.little);

      // Vertex 2 (3x Float32)
      bdata.setFloat32(offset + 36, v2.$1, Endian.little);
      bdata.setFloat32(offset + 40, v2.$2, Endian.little);
      bdata.setFloat32(offset + 44, v2.$3, Endian.little);

      // Attribute byte count (Uint16)
      bdata.setUint16(offset + 48, 0, Endian.little);

      offset += 50;
    }
  }

  return bytes;
}

/// Export a baked [Scene] to an STL file at [path].
void exportStl(Scene scene, String path, {bool binary = false, double scale = 1.0}) {
  final file = File(path);
  final dir = file.parent;
  if (!dir.existsSync()) {
    dir.createSync(recursive: true);
  }

  if (binary) {
    final data = toStlBinary(scene, scale: scale);
    file.writeAsBytesSync(data);
  } else {
    final text = toStlAscii(scene, scale: scale);
    file.writeAsStringSync(text);
  }
}
