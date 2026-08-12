import { SkpScene } from './model';

declare const process: any;
declare const require: any;

/**
 * Serialize a baked SkpScene into Wavefront OBJ text format.
 *
 * @param scene The result of SkpFile.buildScene()
 * @returns The formatted OBJ text string.
 */
export function toOBJ(scene: SkpScene): string {
  const lines: string[] = [
    '# OpenSKP OBJ Export',
    `# Primitives: ${scene.glbPrimitives.length}`,
    '',
  ];

  let vertOffset = 1; // OBJ indices are 1-based
  for (const prim of scene.glbPrimitives) {
    lines.push(`o ${prim.geomName}`);

    const vertCount = Math.floor(prim.positions.length / 3);
    for (let i = 0; i < vertCount; i++) {
      const x = prim.positions[i * 3].toFixed(6);
      const y = prim.positions[i * 3 + 1].toFixed(6);
      const z = prim.positions[i * 3 + 2].toFixed(6);
      lines.push(`v ${x} ${y} ${z}`);
    }

    const triCount = Math.floor(prim.indices.length / 3);
    for (let i = 0; i < triCount; i++) {
      const i0 = prim.indices[i * 3] + vertOffset;
      const i1 = prim.indices[i * 3 + 1] + vertOffset;
      const i2 = prim.indices[i * 3 + 2] + vertOffset;
      lines.push(`f ${i0} ${i1} ${i2}`);
    }

    vertOffset += vertCount;
    lines.push('');
  }

  return lines.join('\n');
}

/**
 * Export a baked SkpScene directly to a Wavefront OBJ file.
 * Node.js environment only.
 *
 * @param scene The result of SkpFile.buildScene()
 * @param outputPath Destination file path (.obj)
 */
export function exportOBJ(scene: SkpScene, outputPath: string): void {
  if (typeof process !== 'undefined' && process.versions && process.versions.node) {
    const fs = require('fs');
    const path = require('path');
    const text = toOBJ(scene);
    const dir = path.dirname(outputPath);
    if (dir && !fs.existsSync(dir)) {
      fs.mkdirSync(dir, { recursive: true });
    }
    fs.writeFileSync(outputPath, text, 'utf-8');
  } else {
    throw new Error('exportOBJ file writing is only supported in Node.js environment');
  }
}
