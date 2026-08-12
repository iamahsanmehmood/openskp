import { SkpScene } from './model';

declare const process: any;
declare const require: any;

function getMaterialRGBA(
  scene: SkpScene,
  matIdx: number
): [number, number, number, number] {
  if (
    matIdx >= 0 &&
    scene.gltfMaterials &&
    matIdx < scene.gltfMaterials.length
  ) {
    const mat: any = scene.gltfMaterials[matIdx];
    if (mat && mat.baseColorFactor && Array.isArray(mat.baseColorFactor) && mat.baseColorFactor.length >= 4) {
      const r = Math.max(0, Math.min(255, Math.round(mat.baseColorFactor[0] * 255)));
      const g = Math.max(0, Math.min(255, Math.round(mat.baseColorFactor[1] * 255)));
      const b = Math.max(0, Math.min(255, Math.round(mat.baseColorFactor[2] * 255)));
      const a = Math.max(0, Math.min(255, Math.round(mat.baseColorFactor[3] * 255)));
      return [r, g, b, a];
    }
  }
  return [200, 200, 200, 255];
}

/**
 * Serialize a baked SkpScene into ASCII PLY text format.
 *
 * @param scene The result of SkpFile.buildScene()
 * @returns The formatted ASCII PLY string.
 */
export function toPLYAscii(scene: SkpScene): string {
  if (!scene || !scene.glbPrimitives) {
    throw new Error('toPLYAscii requires a valid SkpScene instance');
  }

  let totalVertices = 0;
  let totalFaces = 0;

  for (const prim of scene.glbPrimitives) {
    totalVertices += Math.floor(prim.positions.length / 3);
    totalFaces += Math.floor(prim.indices.length / 3);
  }

  const lines: string[] = [
    'ply',
    'format ascii 1.0',
    'comment Created by OpenSKP',
    `element vertex ${totalVertices}`,
    'property float x',
    'property float y',
    'property float z',
    'property float nx',
    'property float ny',
    'property float nz',
    'property float u',
    'property float v',
    'property uchar red',
    'property uchar green',
    'property uchar blue',
    'property uchar alpha',
    `element face ${totalFaces}`,
    'property list uchar int vertex_indices',
    'end_header',
  ];

  for (const prim of scene.glbPrimitives) {
    const [r, g, b, a] = getMaterialRGBA(scene, prim.materialIndex);
    const vertCount = Math.floor(prim.positions.length / 3);
    for (let i = 0; i < vertCount; i++) {
      const px = prim.positions[i * 3].toFixed(6);
      const py = prim.positions[i * 3 + 1].toFixed(6);
      const pz = prim.positions[i * 3 + 2].toFixed(6);

      const nx = (i * 3 < prim.normals.length ? prim.normals[i * 3] : 0).toFixed(6);
      const ny = (i * 3 + 1 < prim.normals.length ? prim.normals[i * 3 + 1] : 0).toFixed(6);
      const nz = (i * 3 + 2 < prim.normals.length ? prim.normals[i * 3 + 2] : 0).toFixed(6);

      const u = (i * 2 < prim.uvs.length ? prim.uvs[i * 2] : 0).toFixed(6);
      const v = (i * 2 + 1 < prim.uvs.length ? prim.uvs[i * 2 + 1] : 0).toFixed(6);

      lines.push(`${px} ${py} ${pz} ${nx} ${ny} ${nz} ${u} ${v} ${r} ${g} ${b} ${a}`);
    }
  }

  let vertOffset = 0;
  for (const prim of scene.glbPrimitives) {
    const triCount = Math.floor(prim.indices.length / 3);
    for (let i = 0; i < triCount; i++) {
      const i0 = prim.indices[i * 3] + vertOffset;
      const i1 = prim.indices[i * 3 + 1] + vertOffset;
      const i2 = prim.indices[i * 3 + 2] + vertOffset;
      lines.push(`3 ${i0} ${i1} ${i2}`);
    }
    vertOffset += Math.floor(prim.positions.length / 3);
  }

  return lines.join('\n') + '\n';
}

/**
 * Serialize a baked SkpScene into Little-Endian Binary PLY format.
 *
 * @param scene The result of SkpFile.buildScene()
 * @returns Packed Little-Endian Uint8Array.
 */
export function toPLYBinary(scene: SkpScene): Uint8Array {
  if (!scene || !scene.glbPrimitives) {
    throw new Error('toPLYBinary requires a valid SkpScene instance');
  }

  let totalVertices = 0;
  let totalFaces = 0;

  for (const prim of scene.glbPrimitives) {
    totalVertices += Math.floor(prim.positions.length / 3);
    totalFaces += Math.floor(prim.indices.length / 3);
  }

  const headerText =
    `ply\n` +
    `format binary_little_endian 1.0\n` +
    `comment Created by OpenSKP\n` +
    `element vertex ${totalVertices}\n` +
    `property float x\n` +
    `property float y\n` +
    `property float z\n` +
    `property float nx\n` +
    `property float ny\n` +
    `property float nz\n` +
    `property float u\n` +
    `property float v\n` +
    `property uchar red\n` +
    `property uchar green\n` +
    `property uchar blue\n` +
    `property uchar alpha\n` +
    `element face ${totalFaces}\n` +
    `property list uchar int vertex_indices\n` +
    `end_header\n`;

  const encoder = new TextEncoder();
  const headerBytes = encoder.encode(headerText);

  const vertexBytesSize = totalVertices * 36; // 8x float32 (32) + 4x uint8 (4)
  const faceBytesSize = totalFaces * 13; // 1x uint8 (1) + 3x int32 (12)

  const buffer = new ArrayBuffer(headerBytes.length + vertexBytesSize + faceBytesSize);
  const view = new DataView(buffer);
  const uint8View = new Uint8Array(buffer);

  // Write header
  uint8View.set(headerBytes, 0);

  let offset = headerBytes.length;

  for (const prim of scene.glbPrimitives) {
    const [r, g, b, a] = getMaterialRGBA(scene, prim.materialIndex);
    const vertCount = Math.floor(prim.positions.length / 3);
    for (let i = 0; i < vertCount; i++) {
      const px = prim.positions[i * 3];
      const py = prim.positions[i * 3 + 1];
      const pz = prim.positions[i * 3 + 2];

      const nx = i * 3 < prim.normals.length ? prim.normals[i * 3] : 0;
      const ny = i * 3 + 1 < prim.normals.length ? prim.normals[i * 3 + 1] : 0;
      const nz = i * 3 + 2 < prim.normals.length ? prim.normals[i * 3 + 2] : 0;

      const u = i * 2 < prim.uvs.length ? prim.uvs[i * 2] : 0;
      const v = i * 2 + 1 < prim.uvs.length ? prim.uvs[i * 2 + 1] : 0;

      view.setFloat32(offset, px, true);
      view.setFloat32(offset + 4, py, true);
      view.setFloat32(offset + 8, pz, true);

      view.setFloat32(offset + 12, nx, true);
      view.setFloat32(offset + 16, ny, true);
      view.setFloat32(offset + 20, nz, true);

      view.setFloat32(offset + 24, u, true);
      view.setFloat32(offset + 28, v, true);

      uint8View[offset + 32] = r;
      uint8View[offset + 33] = g;
      uint8View[offset + 34] = b;
      uint8View[offset + 35] = a;

      offset += 36;
    }
  }

  let vertOffset = 0;
  for (const prim of scene.glbPrimitives) {
    const triCount = Math.floor(prim.indices.length / 3);
    for (let i = 0; i < triCount; i++) {
      const i0 = prim.indices[i * 3] + vertOffset;
      const i1 = prim.indices[i * 3 + 1] + vertOffset;
      const i2 = prim.indices[i * 3 + 2] + vertOffset;

      uint8View[offset] = 3;
      view.setInt32(offset + 1, i0, true);
      view.setInt32(offset + 5, i1, true);
      view.setInt32(offset + 9, i2, true);

      offset += 13;
    }
    vertOffset += Math.floor(prim.positions.length / 3);
  }

  return uint8View;
}

/**
 * Export a baked SkpScene directly to a PLY file.
 * Node.js environment only.
 *
 * @param scene The result of SkpFile.buildScene()
 * @param outputPath Destination file path (.ply)
 * @param options Export options (binary format flag)
 */
export function exportPLY(
  scene: SkpScene,
  outputPath: string,
  options?: { binary?: boolean }
): void {
  if (typeof process !== 'undefined' && process.versions && process.versions.node) {
    const fs = require('fs');
    const path = require('path');
    const binary = options?.binary ?? false;

    const dir = path.dirname(outputPath);
    if (dir && !fs.existsSync(dir)) {
      fs.mkdirSync(dir, { recursive: true });
    }

    if (binary) {
      const data = toPLYBinary(scene);
      fs.writeFileSync(outputPath, data);
    } else {
      const text = toPLYAscii(scene);
      fs.writeFileSync(outputPath, text, 'utf-8');
    }
  } else {
    throw new Error('exportPLY file writing is only supported in Node.js environment');
  }
}
