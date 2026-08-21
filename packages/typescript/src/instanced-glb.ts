import type { InstancedScene, InstancedNode } from './instanced';

/** Options for {@link toInstancedGLB}. */
export interface InstancedGlbOptions {
  /** Embed the scene's texture images in the GLB and point each textured
   * material's `baseColorTexture` at them. Off by default, matching
   * {@link toGLB}: photographic textures can multiply the file size, and
   * the geometry alone is what most callers are after. */
  textures?: boolean;
}

/**
 * Drops `baseColorTexture` from materials when the images are not being
 * embedded, so the reference does not dangle and a strict glTF reader
 * still accepts the file. Mirrors {@link toGLB}'s own handling.
 */
function stripTextureRefs(materials: unknown[]): unknown[] {
  let needsCopy = false;
  for (const mat of materials) {
    if ((mat as any)?.pbrMetallicRoughness?.baseColorTexture) {
      needsCopy = true;
      break;
    }
  }
  if (!needsCopy) return materials;
  return materials.map((mat) => {
    const pbr = (mat as any)?.pbrMetallicRoughness;
    if (!pbr?.baseColorTexture) return mat;
    const restPbr = { ...pbr };
    delete restPbr.baseColorTexture;
    return { ...(mat as any), pbrMetallicRoughness: restPbr };
  });
}

function createGlb(json: any, binaryBuffer: Uint8Array): Uint8Array {
  let jsonString = JSON.stringify(json);
  const jsonRemainder = jsonString.length % 4;
  if (jsonRemainder !== 0) {
    jsonString += ' '.repeat(4 - jsonRemainder);
  }
  const jsonBuffer = new TextEncoder().encode(jsonString);

  let paddedBinaryBuffer = binaryBuffer;
  const binaryRemainder = binaryBuffer.length % 4;
  if (binaryRemainder !== 0) {
    const padLength = 4 - binaryRemainder;
    paddedBinaryBuffer = new Uint8Array(binaryBuffer.length + padLength);
    paddedBinaryBuffer.set(binaryBuffer);
  }

  const totalLength = 12 + 8 + jsonBuffer.length + 8 + paddedBinaryBuffer.length;
  const glb = new Uint8Array(totalLength);
  const view = new DataView(glb.buffer);

  view.setUint32(0, 0x46546c67, true); // 'glTF'
  view.setUint32(4, 2, true);
  view.setUint32(8, totalLength, true);

  view.setUint32(12, jsonBuffer.length, true);
  view.setUint32(16, 0x4e4f534a, true); // 'JSON'
  glb.set(jsonBuffer, 20);

  const binHeaderOffset = 20 + jsonBuffer.length;
  view.setUint32(binHeaderOffset, paddedBinaryBuffer.length, true);
  view.setUint32(binHeaderOffset + 4, 0x004e4942, true); // 'BIN'
  glb.set(paddedBinaryBuffer, binHeaderOffset + 8);

  return glb;
}

/**
 * Export an {@link InstancedScene} to GLB (binary glTF 2.0), PRESERVING
 * instancing: each mesh resource is written to the binary buffer exactly
 * once, and every placement is a glTF node whose `mesh` points at it.
 *
 * This is what {@link toGLB} cannot do from a baked {@link SkpScene}, whose
 * primitives already have the world transform folded into their vertex
 * data - there is nothing left to share. Here, a component placed 1,000
 * times contributes one copy of its vertex/index buffers plus 1,000 node
 * transforms.
 *
 * A definition that resolves to several materials becomes ONE glTF mesh
 * with several primitives (the normal glTF representation), not several
 * nodes.
 *
 * {@link toGLB} is untouched and still produces exactly what it always has.
 *
 * @param scene - The result of {@link buildInstancedScene}
 * @param options - See {@link InstancedGlbOptions}
 * @returns GLB file as Uint8Array
 */
export function toInstancedGLB(
  scene: InstancedScene,
  options?: InstancedGlbOptions
): Uint8Array {
  const resources = scene.meshResources || [];
  const gltfMaterials = scene.gltfMaterials || [];
  const sceneTextures = options?.textures ? scene.textures || [] : [];

  let totalBinaryLength = 0;
  for (const res of resources) {
    for (const prim of res.primitives) {
      totalBinaryLength += prim.positions.byteLength;
      totalBinaryLength += prim.normals.byteLength;
      totalBinaryLength += prim.uvs.byteLength;
      totalBinaryLength += prim.indices.byteLength;
    }
  }

  const imagePlacements: { offset: number; length: number }[] = [];
  for (const tex of sceneTextures) {
    totalBinaryLength += (4 - (totalBinaryLength % 4)) % 4;
    imagePlacements.push({ offset: totalBinaryLength, length: tex.data.length });
    totalBinaryLength += tex.data.length;
  }

  const binaryBuffer = new Uint8Array(totalBinaryLength);
  const bufferViews: any[] = [];
  const accessors: any[] = [];
  const gltfMeshes: any[] = [];
  /** mesh resource id -> index into `gltfMeshes`. */
  const meshIndexById = new Map<string, number>();

  let byteOffset = 0;

  for (const res of resources) {
    const gltfPrimitives: any[] = [];

    for (const prim of res.primitives) {
      const posByteOffset = byteOffset;
      binaryBuffer.set(
        new Uint8Array(prim.positions.buffer, prim.positions.byteOffset, prim.positions.byteLength),
        posByteOffset
      );
      byteOffset += prim.positions.byteLength;

      const normByteOffset = byteOffset;
      binaryBuffer.set(
        new Uint8Array(prim.normals.buffer, prim.normals.byteOffset, prim.normals.byteLength),
        normByteOffset
      );
      byteOffset += prim.normals.byteLength;

      const uvByteOffset = byteOffset;
      binaryBuffer.set(
        new Uint8Array(prim.uvs.buffer, prim.uvs.byteOffset, prim.uvs.byteLength),
        uvByteOffset
      );
      byteOffset += prim.uvs.byteLength;

      const indByteOffset = byteOffset;
      binaryBuffer.set(
        new Uint8Array(prim.indices.buffer, prim.indices.byteOffset, prim.indices.byteLength),
        indByteOffset
      );
      byteOffset += prim.indices.byteLength;

      const posBufferViewIdx = bufferViews.length;
      bufferViews.push({
        buffer: 0,
        byteOffset: posByteOffset,
        byteLength: prim.positions.byteLength,
        target: 34962, // ARRAY_BUFFER
      });

      const normBufferViewIdx = bufferViews.length;
      bufferViews.push({
        buffer: 0,
        byteOffset: normByteOffset,
        byteLength: prim.normals.byteLength,
        target: 34962,
      });

      const uvBufferViewIdx = bufferViews.length;
      bufferViews.push({
        buffer: 0,
        byteOffset: uvByteOffset,
        byteLength: prim.uvs.byteLength,
        target: 34962,
      });

      const indBufferViewIdx = bufferViews.length;
      bufferViews.push({
        buffer: 0,
        byteOffset: indByteOffset,
        byteLength: prim.indices.byteLength,
        target: 34963, // ELEMENT_ARRAY_BUFFER
      });

      let minX = Infinity, minY = Infinity, minZ = Infinity;
      let maxX = -Infinity, maxY = -Infinity, maxZ = -Infinity;
      for (let i = 0; i < prim.positions.length; i += 3) {
        const x = prim.positions[i];
        const y = prim.positions[i + 1];
        const z = prim.positions[i + 2];
        if (x < minX) minX = x; if (x > maxX) maxX = x;
        if (y < minY) minY = y; if (y > maxY) maxY = y;
        if (z < minZ) minZ = z; if (z > maxZ) maxZ = z;
      }
      if (minX === Infinity) {
        minX = minY = minZ = 0;
        maxX = maxY = maxZ = 0;
      }

      const posAccessorIdx = accessors.length;
      accessors.push({
        bufferView: posBufferViewIdx,
        byteOffset: 0,
        componentType: 5126, // FLOAT
        count: prim.positions.length / 3,
        type: 'VEC3',
        min: [minX, minY, minZ],
        max: [maxX, maxY, maxZ],
      });

      const normAccessorIdx = accessors.length;
      accessors.push({
        bufferView: normBufferViewIdx,
        byteOffset: 0,
        componentType: 5126,
        count: prim.normals.length / 3,
        type: 'VEC3',
      });

      const uvAccessorIdx = accessors.length;
      accessors.push({
        bufferView: uvBufferViewIdx,
        byteOffset: 0,
        componentType: 5126,
        count: prim.uvs.length / 2,
        type: 'VEC2',
      });

      const indAccessorIdx = accessors.length;
      accessors.push({
        bufferView: indBufferViewIdx,
        byteOffset: 0,
        componentType: 5125, // UNSIGNED_INT
        count: prim.indices.length,
        type: 'SCALAR',
      });

      gltfPrimitives.push({
        attributes: {
          POSITION: posAccessorIdx,
          NORMAL: normAccessorIdx,
          TEXCOORD_0: uvAccessorIdx,
        },
        indices: indAccessorIdx,
        material: prim.materialIndex,
      });
    }

    if (gltfPrimitives.length === 0) continue;

    meshIndexById.set(res.id, gltfMeshes.length);
    gltfMeshes.push({
      name: res.definitionName || res.id,
      primitives: gltfPrimitives,
    });
  }

  // Flatten the instance tree into glTF nodes. Node transforms are already
  // parent-relative glTF matrices, so the hierarchy maps across directly
  // and each node keeps pointing at the ONE shared mesh.
  const gltfNodes: any[] = [];

  const IDENTITY = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1];
  const isIdentity = (m: number[]) =>
    m.length === 16 && m.every((v, i) => Math.abs(v - IDENTITY[i]) < 1e-12);

  function emitNode(node: InstancedNode): number {
    const idx = gltfNodes.length;
    const gltfNode: any = {};
    if (node.name) gltfNode.name = node.name;
    else if (node.definitionName) gltfNode.name = node.definitionName;

    // glTF treats an omitted matrix as the identity; writing it out anyway
    // just costs bytes on every node of a large scene.
    if (!isIdentity(node.matrix)) {
      gltfNode.matrix = node.matrix;
    }

    const meshIdx =
      node.meshResourceId !== undefined ? meshIndexById.get(node.meshResourceId) : undefined;
    if (meshIdx !== undefined) {
      gltfNode.mesh = meshIdx;
    }

    gltfNodes.push(gltfNode);

    if (node.children.length > 0) {
      const childIndices = node.children.map(emitNode);
      // re-read: `gltfNodes[idx]` is the object pushed above
      gltfNodes[idx].children = childIndices;
    }
    return idx;
  }

  const rootIdx = emitNode(scene.sceneHierarchy);

  const gltfImages: any[] = [];
  const gltfTextures: any[] = [];
  for (let i = 0; i < sceneTextures.length; i++) {
    const tex = sceneTextures[i];
    const place = imagePlacements[i];
    binaryBuffer.set(tex.data, place.offset);
    const viewIdx = bufferViews.length;
    bufferViews.push({ buffer: 0, byteOffset: place.offset, byteLength: place.length });
    gltfImages.push({ bufferView: viewIdx, mimeType: tex.mimeType });
    gltfTextures.push({ sampler: 0, source: gltfImages.length - 1 });
  }

  const gltfJson = {
    asset: {
      version: '2.0',
      generator: 'OpenSKP TypeScript Instanced Exporter',
    },
    scene: 0,
    scenes: [{ nodes: [rootIdx] }],
    nodes: gltfNodes,
    meshes: gltfMeshes,
    materials: sceneTextures.length > 0 ? gltfMaterials : stripTextureRefs(gltfMaterials),
    buffers: [{ byteLength: totalBinaryLength }],
    bufferViews,
    accessors,
    ...(gltfImages.length > 0
      ? {
          images: gltfImages,
          textures: gltfTextures,
          samplers: [{ wrapS: 10497, wrapT: 10497 }], // REPEAT / REPEAT
        }
      : {}),
  };

  return createGlb(gltfJson, binaryBuffer);
}
