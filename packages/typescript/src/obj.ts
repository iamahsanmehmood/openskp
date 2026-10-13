import { SkpScene } from './model';

declare const process: any;
declare const require: any;

function sanitizeMaterialName(name: string): string {
  const clean = name.trim().replace(/[^\w.-]/g, '_');
  return clean || 'default_material';
}

function materialNames(scene: SkpScene): string[] {
  const bases = scene.gltfMaterials.map((mat: any, i) =>
    sanitizeMaterialName(mat?.name || `Material_${i}`)
  );
  const reserved = new Set(bases);
  const used = new Set<string>();
  return bases.map((base, i) => {
    let name = base;
    if (used.has(name)) {
      const stem = `${base}_${i + 1}`;
      name = stem;
      let suffix = 1;
      while (used.has(name) || reserved.has(name)) {
        name = `${stem}_${suffix++}`;
      }
    }
    used.add(name);
    return name;
  });
}

/**
 * Serialize a baked SkpScene's materials into Wavefront MTL text format.
 * Names are case-sensitive. Collisions gain _<1-based material index>
 * and, when needed, an additional _<n> suffix. Other names stay unchanged.
 *
 * @param scene The result of SkpFile.buildScene()
 * @returns The formatted MTL text string.
 */
export function toMTL(scene: SkpScene): string {
  const lines: string[] = [
    '# OpenSKP MTL Material Library Export',
    `# Materials: ${scene.gltfMaterials.length}`,
    '',
  ];

  const names = materialNames(scene);
  for (let idx = 0; idx < scene.gltfMaterials.length; idx++) {
    const mat = scene.gltfMaterials[idx];
    const matName = names[idx];

    const pbr = (mat as any).pbrMetallicRoughness || {};
    const baseColor = pbr.baseColorFactor || [0.8, 0.8, 0.8, 1.0];
    const r = (baseColor[0] ?? 0.8).toFixed(6);
    const g = (baseColor[1] ?? 0.8).toFixed(6);
    const b = (baseColor[2] ?? 0.8).toFixed(6);
    const a = (baseColor[3] ?? 1.0).toFixed(6);

    lines.push(
      `newmtl ${matName}`,
      'Ka 1.000000 1.000000 1.000000',
      `Kd ${r} ${g} ${b}`,
      'Ks 0.200000 0.200000 0.200000',
      'Ns 32.000000',
      `d ${a}`,
      'illum 2'
    );

    const texturePath = (mat as any).texture_path;
    if (texturePath) {
      const texName = texturePath.split(/[/\\]/).pop();
      lines.push(`map_Kd ${texName}`);
    }

    lines.push('');
  }

  return lines.join('\n');
}

/**
 * Serialize a baked SkpScene into Wavefront OBJ text format.
 * Names are case-sensitive. Collisions gain _<1-based material index>
 * and, when needed, an additional _<n> suffix. Other names stay unchanged.
 *
 * @param scene The result of SkpFile.buildScene()
 * @param mtlFilename Optional companion .mtl filename to reference.
 * @returns The formatted OBJ text string.
 */
export function toOBJ(scene: SkpScene, mtlFilename?: string): string {
  const lines: string[] = [
    '# OpenSKP OBJ Export',
    `# Primitives: ${scene.glbPrimitives.length}`,
  ];

  if (mtlFilename) {
    lines.push(`mtllib ${mtlFilename}`);
  }

  lines.push('');

  const names = materialNames(scene);
  let vertOffset = 1;
  let uvOffset = 1;
  let normOffset = 1;

  for (const prim of scene.glbPrimitives) {
    lines.push(`o ${prim.geomName}`);

    const vertCount = Math.floor(prim.positions.length / 3);
    for (let i = 0; i < vertCount; i++) {
      const x = prim.positions[i * 3].toFixed(6);
      const y = prim.positions[i * 3 + 1].toFixed(6);
      const z = prim.positions[i * 3 + 2].toFixed(6);
      lines.push(`v ${x} ${y} ${z}`);
    }

    const uvCount = prim.uvs ? Math.floor(prim.uvs.length / 2) : 0;
    for (let i = 0; i < uvCount; i++) {
      const u = prim.uvs[i * 2].toFixed(6);
      const v = prim.uvs[i * 2 + 1].toFixed(6);
      lines.push(`vt ${u} ${v}`);
    }

    const normCount = prim.normals ? Math.floor(prim.normals.length / 3) : 0;
    for (let i = 0; i < normCount; i++) {
      const nx = prim.normals[i * 3].toFixed(6);
      const ny = prim.normals[i * 3 + 1].toFixed(6);
      const nz = prim.normals[i * 3 + 2].toFixed(6);
      lines.push(`vn ${nx} ${ny} ${nz}`);
    }

    const matIdx = prim.materialIndex;
    if (matIdx >= 0 && matIdx < scene.gltfMaterials.length) {
      lines.push(`usemtl ${names[matIdx]}`);
    }

    const triCount = Math.floor(prim.indices.length / 3);
    const hasUvs = uvCount === vertCount;
    const hasNormals = normCount === vertCount;

    for (let i = 0; i < triCount; i++) {
      const i0 = prim.indices[i * 3];
      const i1 = prim.indices[i * 3 + 1];
      const i2 = prim.indices[i * 3 + 2];

      const v0 = i0 + vertOffset;
      const v1 = i1 + vertOffset;
      const v2 = i2 + vertOffset;

      if (hasUvs && hasNormals) {
        const vt0 = i0 + uvOffset;
        const vt1 = i1 + uvOffset;
        const vt2 = i2 + uvOffset;
        const vn0 = i0 + normOffset;
        const vn1 = i1 + normOffset;
        const vn2 = i2 + normOffset;
        lines.push(`f ${v0}/${vt0}/${vn0} ${v1}/${vt1}/${vn1} ${v2}/${vt2}/${vn2}`);
      } else if (hasUvs) {
        const vt0 = i0 + uvOffset;
        const vt1 = i1 + uvOffset;
        const vt2 = i2 + uvOffset;
        lines.push(`f ${v0}/${vt0} ${v1}/${vt1} ${v2}/${vt2}`);
      } else if (hasNormals) {
        const vn0 = i0 + normOffset;
        const vn1 = i1 + normOffset;
        const vn2 = i2 + normOffset;
        lines.push(`f ${v0}//${vn0} ${v1}//${vn1} ${v2}//${vn2}`);
      } else {
        lines.push(`f ${v0} ${v1} ${v2}`);
      }
    }

    vertOffset += vertCount;
    if (hasUvs) uvOffset += uvCount;
    if (hasNormals) normOffset += normCount;

    lines.push('');
  }

  return lines.join('\n');
}

/**
 * Export a baked SkpScene directly to a Wavefront OBJ file and optional companion .mtl file.
 * Node.js environment only.
 *
 * @param scene The result of SkpFile.buildScene()
 * @param outputPath Destination file path (.obj)
 * @param exportMtl Whether to export companion .mtl file alongside .obj
 */
export function exportOBJ(scene: SkpScene, outputPath: string, exportMtl: boolean = true): void {
  if (typeof process !== 'undefined' && process.versions && process.versions.node) {
    const fs = require('fs');
    const path = require('path');

    const mtlName = exportMtl ? `${path.basename(outputPath, path.extname(outputPath))}.mtl` : undefined;
    const text = toOBJ(scene, mtlName);
    const dir = path.dirname(outputPath);

    if (dir && !fs.existsSync(dir)) {
      fs.mkdirSync(dir, { recursive: true });
    }

    fs.writeFileSync(outputPath, text, 'utf-8');

    if (exportMtl && mtlName) {
      const mtlPath = path.join(dir, mtlName);
      fs.writeFileSync(mtlPath, toMTL(scene), 'utf-8');
    }
  } else {
    throw new Error('exportOBJ file writing is only supported in Node.js environment');
  }
}
