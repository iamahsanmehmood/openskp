import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'instanced_scene.dart';
import 'scene.dart' show SceneTexture;

/// glTF's chunk-length fields are uint32 - a GLB file's total size (and
/// each individual chunk) is hard-capped at 4GB by the format itself, not
/// an arbitrary choice here.
const int _glbSizeLimit = 0xFFFFFFFF;

/// Serializes an [InstancedScene] to binary glTF 2.0 (GLB) bytes,
/// PRESERVING instancing: each mesh resource is written to the binary
/// buffer exactly once, and every placement is a glTF node whose `mesh`
/// points at it.
///
/// This is what [toGlb] cannot do from a baked Scene, whose primitives
/// already have the world transform folded into their vertex data - there
/// is nothing left to share. Here, a component placed 1,000 times
/// contributes one copy of its vertex/index buffers plus 1,000 node
/// transforms.
///
/// A definition that resolves to several materials becomes ONE glTF mesh
/// with several primitives (the normal glTF representation), not several
/// nodes - matching the TypeScript reference implementation
/// (instanced-glb.ts, openskp#200). [toGlb] is untouched and still
/// produces exactly what it always has.
///
/// [textures]: embed the scene's texture images in the GLB and point each
/// textured material's `baseColorTexture` at them. Off by default,
/// matching [toGlb]'s own `textures` parameter.
Uint8List toInstancedGlb(InstancedScene scene, {bool textures = false}) {
  final resources = scene.meshResources;
  final rawMaterials = scene.gltfMaterials;
  final sceneTextures = textures ? scene.textures : <SceneTexture>[];
  final materials = textures ? rawMaterials : _stripTextureRefs(rawMaterials);

  var totalBinaryLength = 0;
  for (final res in resources) {
    for (final prim in res.primitives) {
      totalBinaryLength += prim.positions.length * 4;
      totalBinaryLength += prim.normals.length * 4;
      totalBinaryLength += prim.uvs.length * 4;
      totalBinaryLength += prim.indices.length * 4;
    }
  }

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
  final gltfMeshes = <Map<String, dynamic>>[];
  final meshIndexById = <String, int>{};

  var byteOffset = 0;
  for (final res in resources) {
    final gltfPrimitives = <Map<String, dynamic>>[];

    for (final prim in res.primitives) {
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
        'buffer': 0, 'byteOffset': posByteOffset, 'byteLength': prim.positions.length * 4, 'target': 34962,
      });
      final normBufferViewIdx = bufferViews.length;
      bufferViews.add({
        'buffer': 0, 'byteOffset': normByteOffset, 'byteLength': prim.normals.length * 4, 'target': 34962,
      });
      final uvBufferViewIdx = bufferViews.length;
      bufferViews.add({
        'buffer': 0, 'byteOffset': uvByteOffset, 'byteLength': prim.uvs.length * 4, 'target': 34962,
      });
      final indBufferViewIdx = bufferViews.length;
      bufferViews.add({
        'buffer': 0, 'byteOffset': indByteOffset, 'byteLength': prim.indices.length * 4, 'target': 34963,
      });

      // Read back through binaryBuffer (already written above) rather
      // than prim.positions directly: positions are List<double> (64-bit)
      // here, but the accessor's actual binary data is float32, so
      // min/max must reflect the same rounded values that are actually in
      // the buffer.
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
        'bufferView': posBufferViewIdx, 'byteOffset': 0, 'componentType': 5126,
        'count': prim.positions.length ~/ 3, 'type': 'VEC3',
        'min': [minX, minY, minZ], 'max': [maxX, maxY, maxZ],
      });
      final normAccessorIdx = accessors.length;
      accessors.add({
        'bufferView': normBufferViewIdx, 'byteOffset': 0, 'componentType': 5126,
        'count': prim.normals.length ~/ 3, 'type': 'VEC3',
      });
      final uvAccessorIdx = accessors.length;
      accessors.add({
        'bufferView': uvBufferViewIdx, 'byteOffset': 0, 'componentType': 5126,
        'count': prim.uvs.length ~/ 2, 'type': 'VEC2',
      });
      final indAccessorIdx = accessors.length;
      accessors.add({
        'bufferView': indBufferViewIdx, 'byteOffset': 0, 'componentType': 5125,
        'count': prim.indices.length, 'type': 'SCALAR',
      });

      gltfPrimitives.add({
        'attributes': {
          'POSITION': posAccessorIdx, 'NORMAL': normAccessorIdx, 'TEXCOORD_0': uvAccessorIdx,
        },
        'indices': indAccessorIdx,
        'material': prim.materialIndex,
      });
    }

    if (gltfPrimitives.isEmpty) continue;
    meshIndexById[res.id] = gltfMeshes.length;
    gltfMeshes.add({
      'name': res.definitionName.isNotEmpty ? res.definitionName : res.id,
      'primitives': gltfPrimitives,
    });
  }

  // Flatten the instance tree into glTF nodes. Node transforms are already
  // parent-relative glTF matrices, so the hierarchy maps across directly
  // and each node keeps pointing at the ONE shared mesh.
  final gltfNodes = <Map<String, dynamic>>[];

  bool isIdentity(List<double> m) {
    if (m.length != 16) return false;
    for (var i = 0; i < 16; i++) {
      if ((m[i] - identityGltf[i]).abs() > 1e-12) return false;
    }
    return true;
  }

  int emitNode(InstancedNode node) {
    final idx = gltfNodes.length;
    final gltfNode = <String, dynamic>{};
    if (node.name.isNotEmpty) {
      gltfNode['name'] = node.name;
    } else if (node.definitionName.isNotEmpty) {
      gltfNode['name'] = node.definitionName;
    }

    // glTF treats an omitted matrix as the identity; writing it out
    // anyway just costs bytes on every node of a large scene.
    if (!isIdentity(node.matrix)) gltfNode['matrix'] = node.matrix;

    final meshIdx = node.meshResourceId != null ? meshIndexById[node.meshResourceId] : null;
    if (meshIdx != null) gltfNode['mesh'] = meshIdx;

    gltfNodes.add(gltfNode);

    if (node.children.isNotEmpty) {
      gltfNode['children'] = node.children.map(emitNode).toList();
    }
    return idx;
  }

  final rootIdx = emitNode(scene.sceneHierarchy);

  final gltfImages = <Map<String, dynamic>>[];
  final gltfTextures = <Map<String, dynamic>>[];
  final bufferBytes = binaryBuffer.buffer.asUint8List();
  for (var i = 0; i < sceneTextures.length; i++) {
    final tex = sceneTextures[i];
    final (offset, length) = imagePlacements[i];
    bufferBytes.setRange(offset, offset + length, tex.data);

    final imgBufferViewIdx = bufferViews.length;
    bufferViews.add({'buffer': 0, 'byteOffset': offset, 'byteLength': length});
    final imageIdx = gltfImages.length;
    gltfImages.add({'bufferView': imgBufferViewIdx, 'mimeType': tex.mimeType});
    gltfTextures.add({'sampler': 0, 'source': imageIdx});
  }

  final gltfJson = {
    'asset': {'version': '2.0', 'generator': 'OpenSKP Dart Instanced Exporter'},
    'scene': 0,
    'scenes': [
      {'nodes': [rootIdx]},
    ],
    'nodes': gltfNodes,
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
        {'wrapS': 10497, 'wrapT': 10497},
      ],
  };

  return _createGlb(gltfJson, bufferBytes);
}

/// Materials with every `baseColorTexture` reference removed - a copy,
/// never mutating the input. Mirrors glb.dart's own `_stripTextureRefs`
/// exactly (duplicated rather than shared, matching the TypeScript
/// reference's own instanced-glb.ts, which duplicates this from index.ts
/// too).
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

/// Serializes an [InstancedScene] to GLB and writes it to [path].
void exportInstancedGlb(InstancedScene scene, String path, {bool textures = false}) {
  File(path).writeAsBytesSync(toInstancedGlb(scene, textures: textures));
}

Uint8List _createGlb(Map<String, dynamic> json, Uint8List binaryBuffer) {
  var jsonBytes = Uint8List.fromList(utf8.encode(jsonEncode(json)));
  final jsonPad = (4 - jsonBytes.length % 4) % 4;
  if (jsonPad > 0) {
    final padded = Uint8List(jsonBytes.length + jsonPad);
    padded.setRange(0, jsonBytes.length, jsonBytes);
    for (var i = jsonBytes.length; i < padded.length; i++) {
      padded[i] = 0x20;
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
  view.setUint32(p, 0x46546C67, Endian.little);
  p += 4;
  view.setUint32(p, 2, Endian.little);
  p += 4;
  view.setUint32(p, totalLength, Endian.little);
  p += 4;

  view.setUint32(p, jsonBytes.length, Endian.little);
  p += 4;
  view.setUint32(p, 0x4E4F534A, Endian.little);
  p += 4;
  glbBytes.setRange(p, p + jsonBytes.length, jsonBytes);
  p += jsonBytes.length;

  view.setUint32(p, paddedBinary.length, Endian.little);
  p += 4;
  view.setUint32(p, 0x004E4942, Endian.little);
  p += 4;
  glbBytes.setRange(p, p + paddedBinary.length, paddedBinary);
  p += paddedBinary.length;

  return glbBytes;
}
