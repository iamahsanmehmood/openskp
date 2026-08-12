import 'dart:io';

import 'scene.dart';

/// Convert a baked [Scene] into Wavefront OBJ text representation.
String toObj(Scene scene) {
  final buffer = StringBuffer();
  buffer.writeln('# OpenSKP OBJ Export');
  buffer.writeln('# Primitives: ${scene.glbPrimitives.length}');
  buffer.writeln();

  var vertOffset = 1; // OBJ indices are 1-based
  for (final prim in scene.glbPrimitives) {
    buffer.writeln('o ${prim.geomName}');

    final vertCount = prim.positions.length ~/ 3;
    for (var i = 0; i < vertCount; i++) {
      final x = prim.positions[i * 3].toStringAsFixed(6);
      final y = prim.positions[i * 3 + 1].toStringAsFixed(6);
      final z = prim.positions[i * 3 + 2].toStringAsFixed(6);
      buffer.writeln('v $x $y $z');
    }

    final triCount = prim.indices.length ~/ 3;
    for (var i = 0; i < triCount; i++) {
      final i0 = prim.indices[i * 3] + vertOffset;
      final i1 = prim.indices[i * 3 + 1] + vertOffset;
      final i2 = prim.indices[i * 3 + 2] + vertOffset;
      buffer.writeln('f $i0 $i1 $i2');
    }

    vertOffset += vertCount;
    buffer.writeln();
  }

  return buffer.toString();
}

/// Export a baked [Scene] to a Wavefront OBJ file at [path].
void exportObj(Scene scene, String path) {
  final text = toObj(scene);
  final file = File(path);
  final dir = file.parent;
  if (!dir.existsSync()) {
    dir.createSync(recursive: true);
  }
  file.writeAsStringSync(text);
}
