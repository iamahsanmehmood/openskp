import { SkpScene } from './model';

declare const process: any;
declare const require: any;

function calculateNormal(
  v0: [number, number, number],
  v1: [number, number, number],
  v2: [number, number, number]
): [number, number, number] {
  const e1x = v1[0] - v0[0];
  const e1y = v1[1] - v0[1];
  const e1z = v1[2] - v0[2];

  const e2x = v2[0] - v0[0];
  const e2y = v2[1] - v0[1];
  const e2z = v2[2] - v0[2];

  const nx = e1y * e2z - e1z * e2y;
  const ny = e1z * e2x - e1x * e2z;
  const nz = e1x * e2y - e1y * e2x;

  const len = Math.sqrt(nx * nx + ny * ny + nz * nz);
  if (len > 1e-12) {
    return [nx / len, ny / len, nz / len];
  }
  return [0, 0, 0];
}

/**
 * Serialize a baked SkpScene into ASCII STL text format.
 *
 * @param scene The result of SkpFile.buildScene()
 * @param scale Optional scale factor (e.g. 1000.0 for mm)
 * @returns The formatted ASCII STL string.
 */
export function toSTLAscii(scene: SkpScene, scale = 1.0): string {
  if (!scene || !scene.glbPrimitives) {
    throw new Error('toSTLAscii requires a valid SkpScene instance');
  }

  const lines: string[] = ['solid OpenSKP_Model'];

  for (const prim of scene.glbPrimitives) {
    const triCount = Math.floor(prim.indices.length / 3);
    for (let i = 0; i < triCount; i++) {
      const i0 = prim.indices[i * 3];
      const i1 = prim.indices[i * 3 + 1];
      const i2 = prim.indices[i * 3 + 2];

      const v0: [number, number, number] = [
        prim.positions[i0 * 3] * scale,
        prim.positions[i0 * 3 + 1] * scale,
        prim.positions[i0 * 3 + 2] * scale,
      ];
      const v1: [number, number, number] = [
        prim.positions[i1 * 3] * scale,
        prim.positions[i1 * 3 + 1] * scale,
        prim.positions[i1 * 3 + 2] * scale,
      ];
      const v2: [number, number, number] = [
        prim.positions[i2 * 3] * scale,
        prim.positions[i2 * 3 + 1] * scale,
        prim.positions[i2 * 3 + 2] * scale,
      ];

      const [nx, ny, nz] = calculateNormal(v0, v1, v2);

      lines.push(
        `  facet normal ${nx.toFixed(6)} ${ny.toFixed(6)} ${nz.toFixed(6)}`
      );
      lines.push('    outer loop');
      lines.push(
        `      vertex ${v0[0].toFixed(6)} ${v0[1].toFixed(6)} ${v0[2].toFixed(6)}`
      );
      lines.push(
        `      vertex ${v1[0].toFixed(6)} ${v1[1].toFixed(6)} ${v1[2].toFixed(6)}`
      );
      lines.push(
        `      vertex ${v2[0].toFixed(6)} ${v2[1].toFixed(6)} ${v2[2].toFixed(6)}`
      );
      lines.push('    endloop');
      lines.push('  endfacet');
    }
  }

  lines.push('endsolid OpenSKP_Model\n');
  return lines.join('\n');
}

/**
 * Serialize a baked SkpScene into Little-Endian Binary STL format.
 *
 * @param scene The result of SkpFile.buildScene()
 * @param scale Optional scale factor (e.g. 1000.0 for mm)
 * @returns Packed Little-Endian Uint8Array.
 */
export function toSTLBinary(scene: SkpScene, scale = 1.0): Uint8Array {
  if (!scene || !scene.glbPrimitives) {
    throw new Error('toSTLBinary requires a valid SkpScene instance');
  }

  let totalTriangles = 0;
  for (const prim of scene.glbPrimitives) {
    totalTriangles += Math.floor(prim.indices.length / 3);
  }

  const bufferSize = 80 + 4 + totalTriangles * 50;
  const buffer = new ArrayBuffer(bufferSize);
  const view = new DataView(buffer);

  // Write 80-byte header
  const headerText = '# OpenSKP Binary STL Export';
  for (let i = 0; i < 80; i++) {
    const charCode = i < headerText.length ? headerText.charCodeAt(i) : 0;
    view.setUint8(i, charCode);
  }

  // Write triangle count
  view.setUint32(80, totalTriangles, true);

  let offset = 84;
  for (const prim of scene.glbPrimitives) {
    const triCount = Math.floor(prim.indices.length / 3);
    for (let i = 0; i < triCount; i++) {
      const i0 = prim.indices[i * 3];
      const i1 = prim.indices[i * 3 + 1];
      const i2 = prim.indices[i * 3 + 2];

      const v0: [number, number, number] = [
        prim.positions[i0 * 3] * scale,
        prim.positions[i0 * 3 + 1] * scale,
        prim.positions[i0 * 3 + 2] * scale,
      ];
      const v1: [number, number, number] = [
        prim.positions[i1 * 3] * scale,
        prim.positions[i1 * 3 + 1] * scale,
        prim.positions[i1 * 3 + 2] * scale,
      ];
      const v2: [number, number, number] = [
        prim.positions[i2 * 3] * scale,
        prim.positions[i2 * 3 + 1] * scale,
        prim.positions[i2 * 3 + 2] * scale,
      ];

      const [nx, ny, nz] = calculateNormal(v0, v1, v2);

      // Normal (3x Float32)
      view.setFloat32(offset, nx, true);
      view.setFloat32(offset + 4, ny, true);
      view.setFloat32(offset + 8, nz, true);

      // Vertex 0 (3x Float32)
      view.setFloat32(offset + 12, v0[0], true);
      view.setFloat32(offset + 16, v0[1], true);
      view.setFloat32(offset + 20, v0[2], true);

      // Vertex 1 (3x Float32)
      view.setFloat32(offset + 24, v1[0], true);
      view.setFloat32(offset + 28, v1[1], true);
      view.setFloat32(offset + 32, v1[2], true);

      // Vertex 2 (3x Float32)
      view.setFloat32(offset + 36, v2[0], true);
      view.setFloat32(offset + 40, v2[1], true);
      view.setFloat32(offset + 44, v2[2], true);

      // Attribute byte count (Uint16)
      view.setUint16(offset + 48, 0, true);

      offset += 50;
    }
  }

  return new Uint8Array(buffer);
}

/**
 * Export a baked SkpScene directly to an STL file.
 * Node.js environment only.
 *
 * @param scene The result of SkpFile.buildScene()
 * @param outputPath Destination file path (.stl)
 * @param options Export options (binary format flag, scale multiplier)
 */
export function exportSTL(
  scene: SkpScene,
  outputPath: string,
  options?: { binary?: boolean; scale?: number }
): void {
  if (typeof process !== 'undefined' && process.versions && process.versions.node) {
    const fs = require('fs');
    const path = require('path');
    const binary = options?.binary ?? false;
    const scale = options?.scale ?? 1.0;

    const dir = path.dirname(outputPath);
    if (dir && !fs.existsSync(dir)) {
      fs.mkdirSync(dir, { recursive: true });
    }

    if (binary) {
      const data = toSTLBinary(scene, scale);
      fs.writeFileSync(outputPath, data);
    } else {
      const text = toSTLAscii(scene, scale);
      fs.writeFileSync(outputPath, text, 'utf-8');
    }
  } else {
    throw new Error('exportSTL file writing is only supported in Node.js environment');
  }
}
