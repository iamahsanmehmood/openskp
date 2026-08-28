import { SkpModel, Definition, Face, Material, computeFaceUv, faceUvBasis } from './model';

// Manual base64 (no Buffer - this package also targets the browser, same
// reason addTextureMaterial takes bytes directly rather than a file path).
const BASE64_CHARS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/';
function toBase64(bytes: Uint8Array): string {
  let out = '';
  for (let i = 0; i < bytes.length; i += 3) {
    const b0 = bytes[i];
    const b1 = i + 1 < bytes.length ? bytes[i + 1] : undefined;
    const b2 = i + 2 < bytes.length ? bytes[i + 2] : undefined;
    out += BASE64_CHARS[b0 >> 2];
    out += BASE64_CHARS[((b0 & 0x03) << 4) | (b1 === undefined ? 0 : b1 >> 4)];
    out += b1 === undefined ? '=' : BASE64_CHARS[((b1 & 0x0f) << 2) | (b2 === undefined ? 0 : b2 >> 6)];
    out += b2 === undefined ? '=' : BASE64_CHARS[b2 & 0x3f];
  }
  return out;
}

/**
 * Generate TypeScript source that, when run, rebuilds `model` from scratch
 * via `create()`/`SkpBuilder` - a faithful, human-readable, re-runnable
 * transcript of the model as writer API calls, not a serialized dump.
 *
 * Handles: materials (solid and textured, including default-projection and
 * explicitly-pinned UVs), layers, component/group definitions (built in
 * dependency order), faces (front/back material, holes), instances
 * (transform, instance-level paint, instance-level name).
 *
 * Found and fixed via diffing a real, large file (jeff.skp: 2713
 * definitions, 113643 faces) against its own regenerated output - an
 * earlier prototype this module replaced silently dropped instance-level
 * paint (95% of that file's instances) and every instance's own name
 * entirely, and never emitted textured materials at all.
 *
 * Only reproduces geometry reachable by walking faces (`Definition.faces`)
 * - a real file's standalone/construction edges and curves that don't
 * bound any face are NOT reproduced (found via the same real-fixture
 * diffing: one real file's "shelf2B" definition turned out to be 4088 of
 * its 5196 edges loose reference geometry, none of it visible surface
 * area). This does not affect materials, textures, instance paint, or any
 * face/surface geometry - only invisible construction/reference lines.
 *
 * Also not yet handled (matching this project's established disclosure
 * pattern for known gaps): colorized material tint, per-face
 * hidden/soft/smooth edge flags, section planes, text/dimension entities.
 * A model using any of these round-trips its geometry/materials/instances
 * correctly; those specific facts are silently dropped.
 *
 * A face a few millionths of an inch off its own fitted plane (common in
 * real files - floating-point noise, not a modeling error) is
 * auto-triangulated rather than rejected, mirroring real SketchUp's own
 * tolerance - matches the input's face count unless triangulation was
 * actually needed, in which case one input face becomes 2+ (visually
 * identical, more triangles internally).
 *
 * Every textured face is emitted with explicit `frontUv`/`backUv` (3 real
 * vertices + their actual rendered UV, via `computeFaceUv`/`faceUvBasis` -
 * the same formula the reader/renderer use), never left to the writer's
 * default projection - this reproduces the source file's rendering exactly
 * regardless of whether it originally used a pin or the default
 * projection, and sidesteps `addTextureMaterial`'s default applied-height
 * sentinel corrupting the result (see `addTextureMaterial`'s own note in
 * create.ts). A 4-pin *projective* (non-affine) source mapping can't be
 * reproduced exactly this way - front_uv is an affine (3-point) fit, the
 * same limitation the writer itself already has.
 */
export function toTypeScriptCode(model: SkpModel): string {
  const lines: string[] = [];
  const push = (s: string): void => {
    lines.push(s);
  };

  function round(n: number): number {
    const r = Math.round(n * 10000) / 10000;
    return Object.is(r, -0) ? 0 : r;
  }
  function pointStr(p: readonly [number, number, number]): string {
    return `[${round(p[0])}, ${round(p[1])}, ${round(p[2])}]`;
  }
  function matrix3x3Str(m9: readonly number[]): string {
    return `[${m9.map(round).join(', ')}]`;
  }

  function reconstructLoopVertices(
    loop: readonly { edgeId: number; orientation: number }[],
    edgesById: Map<number, { id: number; v1Id: number; v2Id: number }>
  ): number[] {
    const verts: number[] = [];
    for (const { edgeId, orientation } of loop) {
      const e = edgesById.get(edgeId);
      if (!e) continue;
      const vStart = orientation === 1 ? e.v1Id : e.v2Id;
      if (verts.length === 0 || verts[verts.length - 1] !== vStart) verts.push(vStart);
    }
    if (verts.length > 1 && verts[0] === verts[verts.length - 1]) verts.pop();
    return verts;
  }

  // --- materials ---
  const matVar = new Map<string, string>();
  const texturedMats = new Set<string>(); // faces using these always get explicit front_uv/back_uv
  let matCounter = 0;
  push(`import { create } from 'openskp';`);
  push(``);
  push(`export function build() {`);
  push(`  const builder = create();`);
  push(``);
  push(`  // --- Materials (${model.materials.length}) ---`);
  for (const mat of model.materials) {
    const varName = `mat${matCounter++}`;
    matVar.set(mat.name, varName);
    if (mat.texture && mat.texture.data) {
      texturedMats.add(mat.name);
      const b64 = toBase64(mat.texture.data);
      // appliedHeight: 1.0 - see addTextureMaterial's own note on why the
      // library default (an internal sentinel) corrupts ANY face using
      // this material. Every face using a textured material is written
      // below with explicit frontUv/backUv, never left to default
      // projection, so the original applied width/height never needs to
      // be reproduced.
      //
      // atob/charCodeAt (not Buffer) so the generated code runs in a
      // browser too, matching addTextureMaterial's own byte-based (not
      // file-path) design for this package.
      push(`  const ${varName} = builder.addTextureMaterial(`);
      push(`    ${JSON.stringify(mat.name)},`);
      push(`    Uint8Array.from(atob(${JSON.stringify(b64)}), (c) => c.charCodeAt(0)),`);
      push(`    ${JSON.stringify(mat.texture.filename)}, 1.0`);
      push(`  );`);
    } else {
      const rgba = [mat.color.r, mat.color.g, mat.color.b, mat.color.a];
      push(`  const ${varName} = builder.addMaterial(${JSON.stringify(mat.name)}, [${rgba.join(', ')}]);`);
    }
  }

  // --- layers ---
  push(``);
  push(`  // --- Layers (${model.layers.length}) ---`);
  const layerVar = new Map<string, string>();
  let layerCounter = 0;
  for (const layer of model.layers) {
    const varName = `layer${layerCounter++}`;
    layerVar.set(layer.name, varName);
    const colorStr = `[${layer.color.r}, ${layer.color.g}, ${layer.color.b}]`;
    push(
      `  const ${varName} = builder.addLayer(${JSON.stringify(layer.name)}, { color: ${colorStr}, hidden: ${layer.hidden} });`
    );
  }

  // front_uv/back_uv need exactly 3 correspondences whose (u, v) values are
  // NOT collinear (an affine fit is impossible otherwise) - real faces can
  // have a "flat" vertex (three consecutive vertices genuinely collinear in
  // 3D, seen on real building geometry, e.g. a wall edge with an extra
  // point inserted mid-span for some CAD reason), which points[0..2] alone
  // isn't guaranteed to avoid. Search for the first non-collinear triple
  // instead of assuming the first 3 vertices work.
  function pickNonCollinearTriple(points: readonly [number, number, number][]): [number, number, number] | null {
    for (let i = 0; i < points.length; i++) {
      for (let j = i + 1; j < points.length; j++) {
        for (let k = j + 1; k < points.length; k++) {
          const [ax, ay, az] = points[i];
          const [bx, by, bz] = points[j];
          const [cx, cy, cz] = points[k];
          const e1 = [bx - ax, by - ay, bz - az];
          const e2 = [cx - ax, cy - ay, cz - az];
          const cross = [
            e1[1] * e2[2] - e1[2] * e2[1],
            e1[2] * e2[0] - e1[0] * e2[2],
            e1[0] * e2[1] - e1[1] * e2[0],
          ];
          const mag2 = cross[0] * cross[0] + cross[1] * cross[1] + cross[2] * cross[2];
          if (mag2 > 1e-9) return [i, j, k];
        }
      }
    }
    return null;
  }

  function uvTripleStr(
    points: readonly [number, number, number][],
    normal: readonly [number, number, number],
    uvTransform: readonly number[] | null,
    tileW: number,
    tileH: number
  ): string | null {
    if (points.length < 3) return null;
    const idxs = pickNonCollinearTriple(points);
    if (!idxs) return null; // every vertex triple collinear - a sliver face; caller falls back to no UV pin
    const { xr, yr } = faceUvBasis(normal as [number, number, number]);
    const triple = idxs.map((i) => {
      const p = points[i];
      const [u, v] = computeFaceUv(p as [number, number, number], xr, yr, uvTransform as number[] | null, tileW, tileH);
      return `[${pointStr(p)}, [${round(u)}, ${round(v)}]]`;
    });
    return `[${triple.join(', ')}]`;
  }

  function materialOptsStr(
    face: Face,
    points: readonly [number, number, number][],
    materialsById: Map<number, Material>
  ): { str: string; hasUv: boolean } {
    const parts: string[] = [];
    let hasUv = false;
    if (face.materialId != null) {
      const m = materialsById.get(face.materialId);
      if (m) {
        parts.push(`material: ${matVar.get(m.name)}`);
        if (texturedMats.has(m.name)) {
          const triple = uvTripleStr(points, face.normal, face.uvTransform, m.texture?.width || 1, m.texture?.height || 1);
          if (triple) {
            parts.push(`frontUv: ${triple}`);
            hasUv = true;
          }
        }
      }
    }
    if (face.backMaterialId != null) {
      const m = materialsById.get(face.backMaterialId);
      if (m) {
        parts.push(`backMaterial: ${matVar.get(m.name)}`);
        if (texturedMats.has(m.name)) {
          const triple = uvTripleStr(points, face.normal, face.uvTransformBack, m.texture?.width || 1, m.texture?.height || 1);
          if (triple) {
            parts.push(`backUv: ${triple}`);
            hasUv = true;
          }
        }
      }
    }
    return { str: parts.length ? `{ ${parts.join(', ')} }` : '', hasUv };
  }

  const materialsById = new Map(model.materials.map((m) => [m.id ?? -1, m]));

  let facesSkippedDegenerate = 0;

  function loopPoints(
    loop: readonly { edgeId: number; orientation: number }[],
    edgesById: Map<number, { id: number; v1Id: number; v2Id: number }>,
    vertsById: Map<number, { id: number; x: number; y: number; z: number }>
  ): [number, number, number][] | null {
    const vertIds = reconstructLoopVertices(loop, edgesById);
    if (vertIds.length < 3) return null;
    const points = vertIds.map((id) => vertsById.get(id)).filter((v): v is NonNullable<typeof v> => v != null);
    if (points.length < 3) return null;
    return points.map((p) => [p.x, p.y, p.z]);
  }

  function emitFaces(def: Definition, targetVarName: string, indent: string): void {
    const vertsById = new Map(def.vertices.map((v) => [v.id, v]));
    const edgesById = new Map(def.edges.map((e) => [e.id, e]));
    for (const face of def.faces) {
      if (face.loops.length === 0) continue;
      const pointsArr = loopPoints(face.loops[0], edgesById, vertsById);
      if (!pointsArr) {
        facesSkippedDegenerate++;
        continue;
      }
      // Independent cut-out loops (SketchUp's own "hole in a wall" shape) -
      // loops[0] is always the outer boundary, any further loop is a hole.
      // A hole that itself fails to reconstruct is dropped rather than
      // dropping the whole face - a filled-in hole is a closer match to
      // the source than losing the face's material/geometry entirely.
      const holes: [number, number, number][][] = [];
      for (let i = 1; i < face.loops.length; i++) {
        const holePts = loopPoints(face.loops[i], edgesById, vertsById);
        if (holePts) holes.push(holePts);
      }
      const { str: matStr, hasUv } = materialOptsStr(face, pointsArr, materialsById);
      const pointsStr = pointsArr.map(pointStr).join(', ');
      const extraParts: string[] = [];
      // autoTriangulate: true - mirrors real SketchUp's own tolerance for a
      // not-quite-flat polygon (real files can have a face a few
      // millionths of an inch off its own fitted plane); incompatible with
      // frontUv/backUv (see addFace's own note), so only added when this
      // face has neither. Harmless alongside holes - the writer takes the
      // direct (non-triangulated) path whenever holes are present either way.
      if (!hasUv) extraParts.push('autoTriangulate: true');
      if (holes.length > 0) {
        const holesStr = holes.map((h) => `[${h.map(pointStr).join(', ')}]`).join(', ');
        extraParts.push(`holes: [${holesStr}]`);
      }
      let optsStr = matStr;
      if (extraParts.length > 0) {
        optsStr = matStr ? matStr.replace(/\s*}$/, `, ${extraParts.join(', ')} }`) : `{ ${extraParts.join(', ')} }`;
      }
      push(`${indent}${targetVarName}.addFace([${pointsStr}]${optsStr ? `, ${optsStr}` : ''});`);
    }
  }

  // Instance-level paint (SketchUp lets you paint a whole component/group
  // once instead of painting every internal face - unpainted child faces
  // inherit it at render time) and the instance's own given name
  // (independent of its definition's name - e.g. two placements of the
  // same "Wall" definition named "North Wall" / "South Wall").
  function instanceOptsStr(inst: { materialId: number | null; name: string }, defName: string): string[] {
    const parts: string[] = [];
    if (inst.materialId != null) {
      const m = materialsById.get(inst.materialId);
      if (m) parts.push(`material: ${matVar.get(m.name)}`);
    }
    // Explicit even when inst.name is empty: addInstance defaults an
    // OMITTED name to the definition's own name (options.name ?? def.name),
    // so a source instance with a genuinely empty name (SketchUp shows the
    // definition's name in the Outliner as a UI-level fallback, without
    // actually storing it on the instance) would otherwise come out with
    // that name baked in for real - a real difference, not cosmetic (a
    // later rename of the definition would no longer show through). Found
    // via a real fixture where every one of 23 root instances had an empty
    // stored name.
    if (inst.name !== defName) parts.push(`name: ${JSON.stringify(inst.name)}`);
    return parts;
  }

  // --- definitions, built in dependency order (children before parents) ---
  const defVar = new Map<number, string>();
  let defCounter = 0;

  function getOrBuildDef(defId: number, visiting: Set<number>): string | null {
    const existing = defVar.get(defId);
    if (existing) return existing;
    if (visiting.has(defId)) return null; // self/mutually-referencing definition
    visiting.add(defId);

    const def = model.definitions.get(defId);
    if (!def || (def.faces.length === 0 && def.instances.length === 0)) {
      return null;
    }

    for (const inst of def.instances) getOrBuildDef(inst.refIdx as number, visiting);

    const varName = `def${defCounter++}`;
    const defName = def.name || `Def${defId}`;
    defVar.set(defId, varName);

    push(``);
    push(`  // "${def.name}" - ${def.faces.length} faces, ${def.instances.length} nested instances`);
    push(`  const ${varName} = builder.addComponentDefinition(${JSON.stringify(defName)}, (${varName}) => {`);
    emitFaces(def, varName, '    ');
    for (const inst of def.instances) {
      const childVar = inst.refIdx != null ? defVar.get(inst.refIdx) : undefined;
      if (!childVar) continue;
      const m9 = inst.matrix.slice(0, 9);
      const t = inst.matrix.slice(9, 12);
      const extra = instanceOptsStr(inst, defName);
      const optsStr = [`translation: ${pointStr(t as [number, number, number])}`, `matrix3x3: ${matrix3x3Str(m9)}`, ...extra].join(', ');
      push(`    ${varName}.addInstance(${childVar}, { ${optsStr} });`);
    }
    push(`  });`);
    return varName;
  }

  for (const [defId] of model.definitions) {
    getOrBuildDef(defId, new Set());
  }

  // --- root ---
  push(``);
  push(`  // --- Root instances (${model.root.instances.length}) ---`);
  for (const inst of model.root.instances) {
    const childVar = inst.refIdx != null ? defVar.get(inst.refIdx) : undefined;
    if (!childVar) continue;
    const childDefName = inst.refIdx != null ? (model.definitions.get(inst.refIdx)?.name ?? '') : '';
    const m9 = inst.matrix.slice(0, 9);
    const t = inst.matrix.slice(9, 12);
    const extra = instanceOptsStr(inst, childDefName);
    const optsStr = [`translation: ${pointStr(t as [number, number, number])}`, `matrix3x3: ${matrix3x3Str(m9)}`, ...extra].join(', ');
    push(`  builder.addInstance(${childVar}, { ${optsStr} });`);
  }
  emitFaces(model.root, 'builder', '  ');

  push(``);
  push(`  return builder.toBytes();`);
  push(`}`);

  if (facesSkippedDegenerate > 0) {
    lines.splice(
      1,
      0,
      `// ${facesSkippedDegenerate} degenerate face(s) (fewer than 3 resolvable vertices) were skipped during generation.`
    );
  }

  return lines.join('\n');
}
