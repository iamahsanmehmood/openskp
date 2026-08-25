import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'scene.dart';

/// glTF's chunk-length fields are uint32 - a GLB file's total size (and
/// each individual chunk) is hard-capped at 4GB by the format itself, not
/// an arbitrary choice here.
const int _glbSizeLimit = 0xFFFFFFFF;

/// Serializes a baked [Scene] to binary glTF 2.0 (GLB) bytes.
///
/// A from-scratch writer with no external dependency, matching how this
/// project has stayed dependency-light everywhere except C++'s bundled
/// TinyGLTF - `dart:convert`'s built-in [jsonEncode] covers the JSON
/// chunk directly, no custom serializer needed. Structurally ported from
/// the TypeScript reference implementation's `toGLB()`, with full
/// TEXCOORD_0 UV support.
///
/// [textures]: embed the scene's texture images in the GLB and point each
/// textured material's `baseColorTexture` at them. Off by default,
/// matching every other language's exporter: photographic textures can
/// multiply the file size, and the geometry alone is what most callers
/// are after.
Uint8List toGlb(Scene scene, {bool textures = false}) {
  final prims = scene.glbPrimitives;
  final rawMaterials = scene.gltfMaterials;
  final sceneTextures = textures ? scene.textures : <SceneTexture>[];

  // Materials always reference textures by index (buildScene() sets that
  // up unconditionally); embedding the actual image bytes is the opt-in
  // part. When not embedding, strip the reference so a strict glTF reader
  // never sees a baseColorTexture pointing at a textures[] array that was
  // never written - a copy, never mutating the Scene the caller owns.
  final materials = textures ? rawMaterials : _stripTextureRefs(rawMaterials);

  _validateScene(prims, materials);

  var totalBinaryLength = 0;
  for (final prim in prims) {
    totalBinaryLength += prim.positions.length * 4;
    totalBinaryLength += prim.normals.length * 4;
    totalBinaryLength += prim.uvs.length * 4;
    totalBinaryLength += prim.indices.length * 4;
  }

  // Each image is placed on its own 4-byte-aligned offset, as glTF
  // requires for bufferView data.
  final imagePlacements = <(int offset, int length)>[];
  for (final tex in sceneTextures) {
    totalBinaryLength += (4 - (totalBinaryLength % 4)) % 4;
    imagePlacements.add((totalBinaryLength, tex.data.length));
    totalBinaryLength += tex.data.length;
  }

  if (totalBinaryLength > _glbSizeLimit) {
    throw StateError("scene geometry exceeds GLB's 32-bit binary-buffer limit");
  }

  final binaryBuffer = ByteData(totalBinaryLength);
  final bufferViews = <Map<String, dynamic>>[];
  final accessors = <Map<String, dynamic>>[];
  final gltfPrimitives = <Map<String, dynamic>>[];

  var byteOffset = 0;
  for (final prim in prims) {
    final posByteOffset = byteOffset;
    for (final v in prim.positions) {
      binaryBuffer.setFloat32(byteOffset, v, Endian.little);
      byteOffset += 4;
    }

    final normByteOffset = byteOffset;
    for (final v in prim.normals) {
      binaryBuffer.setFloat32(byteOffset, v, Endian.little);
      byteOffset += 4;
    }

    final uvByteOffset = byteOffset;
    for (final v in prim.uvs) {
      binaryBuffer.setFloat32(byteOffset, v, Endian.little);
      byteOffset += 4;
    }

    final indByteOffset = byteOffset;
    for (final idx in prim.indices) {
      binaryBuffer.setUint32(byteOffset, idx, Endian.little);
      byteOffset += 4;
    }

    final posBufferViewIdx = bufferViews.length;
    bufferViews.add({
      'buffer': 0,
      'byteOffset': posByteOffset,
      'byteLength': prim.positions.length * 4,
      'target': 34962, // ARRAY_BUFFER
    });

    final normBufferViewIdx = bufferViews.length;
    bufferViews.add({
      'buffer': 0,
      'byteOffset': normByteOffset,
      'byteLength': prim.normals.length * 4,
      'target': 34962,
    });

    final uvBufferViewIdx = bufferViews.length;
    bufferViews.add({
      'buffer': 0,
      'byteOffset': uvByteOffset,
      'byteLength': prim.uvs.length * 4,
      'target': 34962,
    });

    final indBufferViewIdx = bufferViews.length;
    bufferViews.add({
      'buffer': 0,
      'byteOffset': indByteOffset,
      'byteLength': prim.indices.length * 4,
      'target': 34963, // ELEMENT_ARRAY_BUFFER
    });

    // Read back through binaryBuffer (already written above) rather than
    // prim.positions directly: positions are List<double> (64-bit) here,
    // but the accessor's actual binary data is float32, so min/max must
    // reflect the same rounded values that are actually in the buffer -
    // otherwise a strict glTF validator could see min/max bounds that
    // don't match what the POSITION accessor's own data contains.
    var minX = double.infinity, minY = double.infinity, minZ = double.infinity;
    var maxX = double.negativeInfinity, maxY = double.negativeInfinity, maxZ = double.negativeInfinity;
    for (var i = 0; i < prim.positions.length; i += 3) {
      final x = binaryBuffer.getFloat32(posByteOffset + i * 4, Endian.little);
      final y = binaryBuffer.getFloat32(posByteOffset + (i + 1) * 4, Endian.little);
      final z = binaryBuffer.getFloat32(posByteOffset + (i + 2) * 4, Endian.little);
      if (x < minX) minX = x;
      if (x > maxX) maxX = x;
      if (y < minY) minY = y;
      if (y > maxY) maxY = y;
      if (z < minZ) minZ = z;
      if (z > maxZ) maxZ = z;
    }

    final posAccessorIdx = accessors.length;
    accessors.add({
      'bufferView': posBufferViewIdx,
      'byteOffset': 0,
      'componentType': 5126, // FLOAT
      'count': prim.positions.length ~/ 3,
      'type': 'VEC3',
      'min': [minX, minY, minZ],
      'max': [maxX, maxY, maxZ],
    });

    final normAccessorIdx = accessors.length;
    accessors.add({
      'bufferView': normBufferViewIdx,
      'byteOffset': 0,
      'componentType': 5126,
      'count': prim.normals.length ~/ 3,
      'type': 'VEC3',
    });

    final uvAccessorIdx = accessors.length;
    accessors.add({
      'bufferView': uvBufferViewIdx,
      'byteOffset': 0,
      'componentType': 5126,
      'count': prim.uvs.length ~/ 2,
      'type': 'VEC2',
    });

    final indAccessorIdx = accessors.length;
    accessors.add({
      'bufferView': indBufferViewIdx,
      'byteOffset': 0,
      'componentType': 5125, // UNSIGNED_INT
      'count': prim.indices.length,
      'type': 'SCALAR',
    });

    gltfPrimitives.add({
      'attributes': {
        'POSITION': posAccessorIdx,
        'NORMAL': normAccessorIdx,
        'TEXCOORD_0': uvAccessorIdx,
      },
      'indices': indAccessorIdx,
      'material': prim.materialIndex,
    });
  }

  final gltfImages = <Map<String, dynamic>>[];
  final gltfTextures = <Map<String, dynamic>>[];
  final bufferBytes = binaryBuffer.buffer.asUint8List();
  for (var i = 0; i < sceneTextures.length; i++) {
    final tex = sceneTextures[i];
    final (offset, length) = imagePlacements[i];
    bufferBytes.setRange(offset, offset + length, tex.data);

    final imgBufferViewIdx = bufferViews.length;
    bufferViews.add({
      'buffer': 0,
      'byteOffset': offset,
      'byteLength': length,
    });

    final imageIdx = gltfImages.length;
    gltfImages.add({'bufferView': imgBufferViewIdx, 'mimeType': tex.mimeType});
    gltfTextures.add({'sampler': 0, 'source': imageIdx});
  }

  final gltfMeshes = <Map<String, dynamic>>[];
  if (gltfPrimitives.isNotEmpty) {
    gltfMeshes.add({'primitives': gltfPrimitives});
  }

  final gltfJson = {
    'asset': {'version': '2.0', 'generator': 'OpenSKP Dart Exporter'},
    'scene': 0,
    'scenes': [
      {'nodes': gltfMeshes.isNotEmpty ? [0] : <int>[]},
    ],
    'nodes': gltfMeshes.isNotEmpty ? [
      {'mesh': 0},
    ] : <Map<String, dynamic>>[],
    'meshes': gltfMeshes,
    'materials': materials,
    'buffers': [
      {'byteLength': totalBinaryLength},
    ],
    'bufferViews': bufferViews,
    'accessors': accessors,
    if (gltfImages.isNotEmpty) 'images': gltfImages,
    if (gltfImages.isNotEmpty) 'textures': gltfTextures,
    if (gltfImages.isNotEmpty)
      'samplers': [
        {'wrapS': 10497, 'wrapT': 10497}, // REPEAT / REPEAT
      ],
  };

  return _createGlb(gltfJson, bufferBytes);
}

/// Materials with every `baseColorTexture` reference removed - a copy,
/// never mutating the input. Used when not embedding images, so a strict
/// glTF reader never sees a reference into a textures[] array that was
/// never written.
List<Map<String, dynamic>> _stripTextureRefs(List<Map<String, dynamic>> materials) {
  final needsCopy = materials.any((m) {
    final pbr = m['pbrMetallicRoughness'] as Map<String, dynamic>?;
    return pbr != null && pbr.containsKey('baseColorTexture');
  });
  if (!needsCopy) return materials;

  return materials.map((m) {
    final pbr = m['pbrMetallicRoughness'] as Map<String, dynamic>?;
    if (pbr == null || !pbr.containsKey('baseColorTexture')) return m;
    final newPbr = Map<String, dynamic>.from(pbr)..remove('baseColorTexture');
    return Map<String, dynamic>.from(m)..['pbrMetallicRoughness'] = newPbr;
  }).toList();
}

/// Serializes a baked [Scene] to GLB and writes it to [path]. Does not
/// create missing parent directories - matching the C++ and .NET ports'
/// export_glb/ExportGlb.
void exportGlb(Scene scene, String path, {bool textures = false}) {
  File(path).writeAsBytesSync(toGlb(scene, textures: textures));
}

void _validateScene(List<GlbPrimitive> prims, List<Map<String, dynamic>> materials) {
  for (var i = 0; i < prims.length; i++) {
    final prim = prims[i];
    final prefix = 'primitive $i ';
    if (prim.positions.isEmpty) throw ArgumentError('${prefix}positions must not be empty');
    if (prim.positions.length % 3 != 0) throw ArgumentError('${prefix}positions must contain complete vec3 values');
    if (prim.normals.length != prim.positions.length) throw ArgumentError('${prefix}normals must match positions');
    if (prim.uvs.length != prim.positions.length ~/ 3 * 2) throw ArgumentError('${prefix}uvs must match positions');
    if (prim.indices.isEmpty) throw ArgumentError('${prefix}indices must not be empty');
    if (prim.indices.length % 3 != 0) throw ArgumentError('${prefix}indices must contain complete triangles');
    if (prim.materialIndex < 0 || prim.materialIndex >= materials.length) {
      throw ArgumentError('${prefix}references an invalid material');
    }

    final vertexCount = prim.positions.length ~/ 3;
    for (final v in prim.positions) {
      _checkFinite(v, '${prefix}position');
    }
    for (final v in prim.normals) {
      _checkFinite(v, '${prefix}normal');
    }
    for (final v in prim.uvs) {
      _checkFinite(v, '${prefix}uv');
    }
    for (final idx in prim.indices) {
      if (idx >= vertexCount) throw ArgumentError('${prefix}index is out of range');
    }
  }
}

void _checkFinite(double value, String field) {
  if (value.isNaN || value.isInfinite) {
    throw ArgumentError('$field must be finite');
  }
}

Uint8List _createGlb(Map<String, dynamic> json, Uint8List binaryBuffer) {
  var jsonBytes = Uint8List.fromList(utf8.encode(jsonEncode(json)));
  final jsonPad = (4 - jsonBytes.length % 4) % 4;
  if (jsonPad > 0) {
    final padded = Uint8List(jsonBytes.length + jsonPad);
    padded.setRange(0, jsonBytes.length, jsonBytes);
    for (var i = jsonBytes.length; i < padded.length; i++) {
      padded[i] = 0x20; // space
    }
    jsonBytes = padded;
  }

  final binPad = (4 - binaryBuffer.length % 4) % 4;
  var paddedBinary = binaryBuffer;
  if (binPad > 0) {
    paddedBinary = Uint8List(binaryBuffer.length + binPad);
    paddedBinary.setRange(0, binaryBuffer.length, binaryBuffer);
  }

  final totalLength = 12 + 8 + jsonBytes.length + 8 + paddedBinary.length;
  if (totalLength > _glbSizeLimit) {
    throw StateError("serialized GLB exceeds its 32-bit file-size limit");
  }

  final glbBytes = Uint8List(totalLength);
  final view = ByteData.sublistView(glbBytes);
  var p = 0;
  view.setUint32(p, 0x46546C67, Endian.little); // magic 'glTF'
  p += 4;
  view.setUint32(p, 2, Endian.little); // version
  p += 4;
  view.setUint32(p, totalLength, Endian.little);
  p += 4;

  view.setUint32(p, jsonBytes.length, Endian.little);
  p += 4;
  view.setUint32(p, 0x4E4F534A, Endian.little); // 'JSON'
  p += 4;
  glbBytes.setRange(p, p + jsonBytes.length, jsonBytes);
  p += jsonBytes.length;

  view.setUint32(p, paddedBinary.length, Endian.little);
  p += 4;
  view.setUint32(p, 0x004E4942, Endian.little); // 'BIN\0'
  p += 4;
  glbBytes.setRange(p, p + paddedBinary.length, paddedBinary);
  p += paddedBinary.length;

  return glbBytes;
}
