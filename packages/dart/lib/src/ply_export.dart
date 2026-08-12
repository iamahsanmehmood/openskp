import 'dart:io';
import 'dart:typed_data';

import 'scene.dart';

(int, int, int, int) _getMaterialRgba(Scene scene, int matIdx) {
  if (matIdx >= 0 && matIdx < scene.gltfMaterials.length) {
    final mat = scene.gltfMaterials[matIdx];
    final color = mat['baseColorFactor'];
    if (color is List && color.length >= 4) {
      final r = (color[0] * 255.0).round().clamp(0, 255);
      final g = (color[1] * 255.0).round().clamp(0, 255);
      final b = (color[2] * 255.0).round().clamp(0, 255);
      final a = (color[3] * 255.0).round().clamp(0, 255);
      return (r, g, b, a);
    }
  }
  return (200, 200, 200, 255);
}

/// Convert a baked [Scene] into ASCII PLY text format.
String toPlyAscii(Scene scene) {
  var totalVertices = 0;
  var totalFaces = 0;
  for (final prim in scene.glbPrimitives) {
    totalVertices += prim.positions.length ~/ 3;
    totalFaces += prim.indices.length ~/ 3;
  }

  final buffer = StringBuffer();
  buffer.writeln('ply');
  buffer.writeln('format ascii 1.0');
  buffer.writeln('comment Created by OpenSKP');
  buffer.writeln('element vertex $totalVertices');
  buffer.writeln('property float x');
  buffer.writeln('property float y');
  buffer.writeln('property float z');
  buffer.writeln('property float nx');
  buffer.writeln('property float ny');
  buffer.writeln('property float nz');
  buffer.writeln('property float u');
  buffer.writeln('property float v');
  buffer.writeln('property uchar red');
  buffer.writeln('property uchar green');
  buffer.writeln('property uchar blue');
  buffer.writeln('property uchar alpha');
  buffer.writeln('element face $totalFaces');
  buffer.writeln('property list uchar int vertex_indices');
  buffer.writeln('end_header');

  for (final prim in scene.glbPrimitives) {
    final rgba = _getMaterialRgba(scene, prim.materialIndex);
    final vertCount = prim.positions.length ~/ 3;
    for (var i = 0; i < vertCount; i++) {
      final px = prim.positions[i * 3].toStringAsFixed(6);
      final py = prim.positions[i * 3 + 1].toStringAsFixed(6);
      final pz = prim.positions[i * 3 + 2].toStringAsFixed(6);

      final nx = (i * 3 < prim.normals.length ? prim.normals[i * 3] : 0.0).toStringAsFixed(6);
      final ny = (i * 3 + 1 < prim.normals.length ? prim.normals[i * 3 + 1] : 0.0).toStringAsFixed(6);
      final nz = (i * 3 + 2 < prim.normals.length ? prim.normals[i * 3 + 2] : 0.0).toStringAsFixed(6);

      final u = (i * 2 < prim.uvs.length ? prim.uvs[i * 2] : 0.0).toStringAsFixed(6);
      final v = (i * 2 + 1 < prim.uvs.length ? prim.uvs[i * 2 + 1] : 0.0).toStringAsFixed(6);

      buffer.writeln('$px $py $pz $nx $ny $nz $u $v ${rgba.$1} ${rgba.$2} ${rgba.$3} ${rgba.$4}');
    }
  }

  var vertOffset = 0;
  for (final prim in scene.glbPrimitives) {
    final triCount = prim.indices.length ~/ 3;
    for (var i = 0; i < triCount; i++) {
      final i0 = prim.indices[i * 3] + vertOffset;
      final i1 = prim.indices[i * 3 + 1] + vertOffset;
      final i2 = prim.indices[i * 3 + 2] + vertOffset;
      buffer.writeln('3 $i0 $i1 $i2');
    }
    vertOffset += prim.positions.length ~/ 3;
  }

  return buffer.toString();
}

/// Convert a baked [Scene] into Little-Endian Binary PLY byte array.
Uint8List toPlyBinary(Scene scene) {
  var totalVertices = 0;
  var totalFaces = 0;
  for (final prim in scene.glbPrimitives) {
    totalVertices += prim.positions.length ~/ 3;
    totalFaces += prim.indices.length ~/ 3;
  }

  final headerText =
      'ply\n'
      'format binary_little_endian 1.0\n'
      'comment Created by OpenSKP\n'
      'element vertex $totalVertices\n'
      'property float x\n'
      'property float y\n'
      'property float z\n'
      'property float nx\n'
      'property float ny\n'
      'property float nz\n'
      'property float u\n'
      'property float v\n'
      'property uchar red\n'
      'property uchar green\n'
      'property uchar blue\n'
      'property uchar alpha\n'
      'element face $totalFaces\n'
      'property list uchar int vertex_indices\n'
      'end_header\n';

  final headerBytes = Uint8List.fromList(headerText.codeUnits);
  final vertexBytesSize = totalVertices * 36; // 8x float32 + 4x uint8
  final faceBytesSize = totalFaces * 13; // 1x uint8 + 3x int32

  final bufferSize = headerBytes.length + vertexBytesSize + faceBytesSize;
  final bytes = Uint8List(bufferSize);
  final bdata = ByteData.view(bytes.buffer);

  // Copy header
  bytes.setRange(0, headerBytes.length, headerBytes);

  var offset = headerBytes.length;

  for (final prim in scene.glbPrimitives) {
    final rgba = _getMaterialRgba(scene, prim.materialIndex);
    final vertCount = prim.positions.length ~/ 3;
    for (var i = 0; i < vertCount; i++) {
      final px = prim.positions[i * 3];
      final py = prim.positions[i * 3 + 1];
      final pz = prim.positions[i * 3 + 2];

      final nx = i * 3 < prim.normals.length ? prim.normals[i * 3] : 0.0;
      final ny = i * 3 + 1 < prim.normals.length ? prim.normals[i * 3 + 1] : 0.0;
      final nz = i * 3 + 2 < prim.normals.length ? prim.normals[i * 3 + 2] : 0.0;

      final u = i * 2 < prim.uvs.length ? prim.uvs[i * 2] : 0.0;
      final v = i * 2 + 1 < prim.uvs.length ? prim.uvs[i * 2 + 1] : 0.0;

      bdata.setFloat32(offset, px, Endian.little);
      bdata.setFloat32(offset + 4, py, Endian.little);
      bdata.setFloat32(offset + 8, pz, Endian.little);

      bdata.setFloat32(offset + 12, nx, Endian.little);
      bdata.setFloat32(offset + 16, ny, Endian.little);
      bdata.setFloat32(offset + 20, nz, Endian.little);

      bdata.setFloat32(offset + 24, u, Endian.little);
      bdata.setFloat32(offset + 28, v, Endian.little);

      bytes[offset + 32] = rgba.$1;
      bytes[offset + 33] = rgba.$2;
      bytes[offset + 34] = rgba.$3;
      bytes[offset + 35] = rgba.$4;

      offset += 36;
    }
  }

  var vertOffset = 0;
  for (final prim in scene.glbPrimitives) {
    final triCount = prim.indices.length ~/ 3;
    for (var i = 0; i < triCount; i++) {
      final i0 = prim.indices[i * 3] + vertOffset;
      final i1 = prim.indices[i * 3 + 1] + vertOffset;
      final i2 = prim.indices[i * 3 + 2] + vertOffset;

      bytes[offset] = 3;
      bdata.setInt32(offset + 1, i0, Endian.little);
      bdata.setInt32(offset + 5, i1, Endian.little);
      bdata.setInt32(offset + 9, i2, Endian.little);

      offset += 13;
    }
    vertOffset += prim.positions.length ~/ 3;
  }

  return bytes;
}

/// Export a baked [Scene] to a PLY file at [path].
void exportPly(Scene scene, String path, {bool binary = false}) {
  final file = File(path);
  final dir = file.parent;
  if (!dir.existsSync()) {
    dir.createSync(recursive: true);
  }

  if (binary) {
    final data = toPlyBinary(scene);
    file.writeAsBytesSync(data);
  } else {
    final text = toPlyAscii(scene);
    file.writeAsStringSync(text);
  }
}
