import 'dart:io';

import 'scene.dart';

String _sanitizeMaterialName(String name) {
  final clean = name.trim().replaceAll(RegExp(r'[^\w\.-]'), '_');
  return clean.isEmpty ? 'default_material' : clean;
}

List<String> _materialNames(Scene scene) {
  final bases = <String>[];
  for (var i = 0; i < scene.gltfMaterials.length; i++) {
    final mat = scene.gltfMaterials[i];
    bases.add(_sanitizeMaterialName(mat['name'] as String? ?? 'Material_' + i.toString()));
  }
  final reserved = bases.toSet();
  final used = <String>{};
  final names = <String>[];
  for (var i = 0; i < bases.length; i++) {
    final base = bases[i];
    var name = base;
    if (used.contains(name)) {
      final stem = base + '_' + (i + 1).toString();
      name = stem;
      var suffix = 1;
      while (used.contains(name) || reserved.contains(name)) {
        name = stem + '_' + suffix.toString();
        suffix++;
      }
    }
    used.add(name);
    names.add(name);
  }
  return names;
}

/// Convert a baked [Scene]'s materials into Wavefront MTL text representation.
/// Names are case-sensitive. Collisions gain _<1-based material index>
/// and, when needed, an additional _<n> suffix. Other names stay unchanged.
String toMtl(Scene scene) {
  final buffer = StringBuffer();
  buffer.writeln('# OpenSKP MTL Material Library Export');
  buffer.writeln('# Materials: ${scene.gltfMaterials.length}');
  buffer.writeln();

  final names = _materialNames(scene);
  for (var idx = 0; idx < scene.gltfMaterials.length; idx++) {
    final mat = scene.gltfMaterials[idx];
    final matName = names[idx];

    final pbr = mat['pbrMetallicRoughness'] as Map<String, dynamic>? ?? {};
    final baseColor = (pbr['baseColorFactor'] as List?)?.cast<num>() ?? [0.8, 0.8, 0.8, 1.0];
    final r = (baseColor.isNotEmpty ? baseColor[0] : 0.8).toDouble().toStringAsFixed(6);
    final g = (baseColor.length > 1 ? baseColor[1] : 0.8).toDouble().toStringAsFixed(6);
    final b = (baseColor.length > 2 ? baseColor[2] : 0.8).toDouble().toStringAsFixed(6);
    final a = (baseColor.length > 3 ? baseColor[3] : 1.0).toDouble().toStringAsFixed(6);

    buffer.writeln('newmtl $matName');
    buffer.writeln('Ka 1.000000 1.000000 1.000000');
    buffer.writeln('Kd $r $g $b');
    buffer.writeln('Ks 0.200000 0.200000 0.200000');
    buffer.writeln('Ns 32.000000');
    buffer.writeln('d $a');
    buffer.writeln('illum 2');

    final texturePath = mat['texture_path'] as String?;
    if (texturePath != null) {
      final texName = texturePath.split(RegExp(r'[/\\]')).last;
      buffer.writeln('map_Kd $texName');
    }

    buffer.writeln();
  }

  return buffer.toString();
}

/// Convert a baked [Scene] into Wavefront OBJ text representation.
/// Names are case-sensitive. Collisions gain _<1-based material index>
/// and, when needed, an additional _<n> suffix. Other names stay unchanged.
String toObj(Scene scene, {String? mtlFilename}) {
  final buffer = StringBuffer();
  buffer.writeln('# OpenSKP OBJ Export');
  buffer.writeln('# Primitives: ${scene.glbPrimitives.length}');
  if (mtlFilename != null && mtlFilename.isNotEmpty) {
    buffer.writeln('mtllib $mtlFilename');
  }
  buffer.writeln();

  final names = _materialNames(scene);
  var vertOffset = 1;
  var uvOffset = 1;
  var normOffset = 1;

  for (final prim in scene.glbPrimitives) {
    buffer.writeln('o ${prim.geomName}');

    final vertCount = prim.positions.length ~/ 3;
    for (var i = 0; i < vertCount; i++) {
      final x = prim.positions[i * 3].toStringAsFixed(6);
      final y = prim.positions[i * 3 + 1].toStringAsFixed(6);
      final z = prim.positions[i * 3 + 2].toStringAsFixed(6);
      buffer.writeln('v $x $y $z');
    }

    final uvs = prim.uvs;
    final uvCount = uvs.isEmpty ? 0 : uvs.length ~/ 2;
    for (var i = 0; i < uvCount; i++) {
      final u = uvs[i * 2].toStringAsFixed(6);
      final v = uvs[i * 2 + 1].toStringAsFixed(6);
      buffer.writeln('vt $u $v');
    }

    final normals = prim.normals;
    final normCount = normals.isEmpty ? 0 : normals.length ~/ 3;
    for (var i = 0; i < normCount; i++) {
      final nx = normals[i * 3].toStringAsFixed(6);
      final ny = normals[i * 3 + 1].toStringAsFixed(6);
      final nz = normals[i * 3 + 2].toStringAsFixed(6);
      buffer.writeln('vn $nx $ny $nz');
    }

    final matIdx = prim.materialIndex;
    if (matIdx >= 0 && matIdx < scene.gltfMaterials.length) {
      buffer.writeln('usemtl ${names[matIdx]}');
    }

    final triCount = prim.indices.length ~/ 3;
    final hasUvs = uvCount == vertCount;
    final hasNormals = normCount == vertCount;

    for (var i = 0; i < triCount; i++) {
      final i0 = prim.indices[i * 3];
      final i1 = prim.indices[i * 3 + 1];
      final i2 = prim.indices[i * 3 + 2];

      final v0 = i0 + vertOffset;
      final v1 = i1 + vertOffset;
      final v2 = i2 + vertOffset;

      if (hasUvs && hasNormals) {
        final vt0 = i0 + uvOffset;
        final vt1 = i1 + uvOffset;
        final vt2 = i2 + uvOffset;
        final vn0 = i0 + normOffset;
        final vn1 = i1 + normOffset;
        final vn2 = i2 + normOffset;
        buffer.writeln('f $v0/$vt0/$vn0 $v1/$vt1/$vn1 $v2/$vt2/$vn2');
      } else if (hasUvs) {
        final vt0 = i0 + uvOffset;
        final vt1 = i1 + uvOffset;
        final vt2 = i2 + uvOffset;
        buffer.writeln('f $v0/$vt0 $v1/$vt1 $v2/$vt2');
      } else if (hasNormals) {
        final vn0 = i0 + normOffset;
        final vn1 = i1 + normOffset;
        final vn2 = i2 + normOffset;
        buffer.writeln('f $v0//$vn0 $v1//$vn1 $v2//$vn2');
      } else {
        buffer.writeln('f $v0 $v1 $v2');
      }
    }

    vertOffset += vertCount;
    if (hasUvs) uvOffset += uvCount;
    if (hasNormals) normOffset += normCount;

    buffer.writeln();
  }

  return buffer.toString();
}

/// Export a baked [Scene] to a Wavefront OBJ file at [path] and optional companion MTL file.
void exportObj(Scene scene, String path, {bool exportMtl = true}) {
  final file = File(path);
  final dir = file.parent;
  if (!dir.existsSync()) {
    dir.createSync(recursive: true);
  }

  final filename = file.uri.pathSegments.last;
  final dotIdx = filename.lastIndexOf('.');
  final stem = dotIdx != -1 ? filename.substring(0, dotIdx) : filename;
  final mtlName = exportMtl ? '$stem.mtl' : null;

  final text = toObj(scene, mtlFilename: mtlName);
  file.writeAsStringSync(text);

  if (exportMtl && mtlName != null) {
    final mtlFile = File('${dir.path}/$mtlName');
    mtlFile.writeAsStringSync(toMtl(scene));
  }
}
