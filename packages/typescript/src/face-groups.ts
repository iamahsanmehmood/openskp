import { triangulateFace3D } from './triangulator';
import { reconstructLoopVertices } from './geometry';
import type { GeometryBuilder } from './geometry';
import { SkpParseError } from './errors';
import { faceUvBasis, computeFaceUv } from './model';
import type { Material, Texture } from './model';

/**
 * One group of faces sharing a single resolved (colour, doubleSided,
 * texture) identity, still in DEFINITION-LOCAL space (inches, SketchUp
 * Z-up) - i.e. exactly what the baked scene builder assembles just before
 * it applies an instance's world matrix.
 *
 * Shared by the baked path (`buildSceneFromParsed`, which then transforms
 * these into world space) and the instanced path
 * (`buildInstancedSceneFromParsed`, which keeps them local and puts the
 * transform on the node instead). Keeping one implementation is what makes
 * the two paths agree on triangulation, UV seams, normals and front/back
 * handling by construction rather than by parallel maintenance.
 */
export interface LocalFaceGroup {
  color: { r: number; g: number; b: number };
  doubleSided: boolean;
  /** Local-space vertex positions, inches, SketchUp Z-up. */
  localVerts: [number, number, number][];
  localUvs: [number, number][];
  /** Un-normalised summed face normals per vertex, local space. */
  normalsAccum: number[][];
  localFaces: number[][];
  localVMap: Map<string, number>;
  textureIndex: number | null;
}

/** Everything the grouping needs from its caller that isn't the builder
 * itself: how a material id resolves, how a texture maps to a scene
 * texture index, and the colour to fall back to for an unpainted face. */
export interface FaceGroupContext {
  resolveMaterial: (matId: number | null | undefined) => Material | undefined;
  textureIndexFor: (tex: Texture | null | undefined) => number | null;
  /** Painted-instance material inherited from the enclosing instance, if any. */
  inheritedMaterial?: Material;
  /** Colour an unpainted face falls back to when nothing is inherited
   * (the effective layer's colour). */
  fallbackLayerColor: { r: number; g: number; b: number };
  /** Identifies the definition in a triangulation failure. */
  definitionId: number | string;
}

/**
 * Group a definition's faces by resolved material identity, in local space.
 *
 * A face whose front/back resolve to the SAME colour is emitted once with
 * `doubleSided` set; a face whose sides genuinely differ is emitted as two
 * single-sided triangle sets (one normal-wound front, one reverse-wound
 * back) so each side keeps its own colour.
 */
export function buildLocalFaceGroups(
  builder: GeometryBuilder,
  ctx: FaceGroupContext
): Map<string, LocalFaceGroup> {
  const faceGroups = new Map<string, LocalFaceGroup>();

  const addSide = (
    triangles: number[][],
    fn: [number, number, number],
    color: { r: number; g: number; b: number },
    doubleSided: boolean,
    reverse: boolean,
    mat: Material | undefined,
    uvTransform: number[] | null | undefined,
    xr: [number, number, number],
    yr: [number, number, number]
  ) => {
    // faces are batched per emitted material, so the texture has to be
    // part of the key too - otherwise two differently-textured faces with
    // the same average colour end up in one primitive with one image
    const texIndex = ctx.textureIndexFor(mat?.texture);
    const colorKey = `${color.r},${color.g},${color.b},${doubleSided},${texIndex ?? -1}`;
    let group = faceGroups.get(colorKey);
    if (!group) {
      group = {
        color,
        doubleSided,
        textureIndex: texIndex,
        localVerts: [],
        localUvs: [],
        normalsAccum: [],
        localFaces: [],
        localVMap: new Map<string, number>(),
      };
      faceGroups.set(colorKey, group);
    }

    const tex = mat?.texture;
    const tileW = tex && tex.width > 1e-9 ? tex.width : 1;
    const tileH = tex && tex.height > 1e-9 ? tex.height : 1;
    const sideNormal: [number, number, number] = reverse
      ? [-fn[0], -fn[1], -fn[2]]
      : fn;

    // Vertices are deduped per (vId, uv) rather than just vId: UVs are
    // inherently per-face, so a vertex position shared by two faces
    // that disagree on texture mapping must become two distinct
    // output vertices (glTF requires position/normal/uv aligned per
    // index).
    const faceLocalMap = new Map<number, number>();
    for (const tri of triangles) {
      const triIds = reverse ? [tri[0], tri[2], tri[1]] : tri;
      const faceIndices: number[] = [];
      for (const vId of triIds) {
        if (!builder.vertices.has(vId)) continue;
        let idx = faceLocalMap.get(vId);
        if (idx === undefined) {
          const p = builder.vertices.get(vId)!;
          const [u, v] = computeFaceUv(p, xr, yr, uvTransform, tileW, tileH);
          const key = `${vId},${u},${v}`;
          idx = group.localVMap.get(key);
          if (idx === undefined) {
            group.localVerts.push(p);
            group.localUvs.push([u, v]);
            group.normalsAccum.push([sideNormal[0], sideNormal[1], sideNormal[2]]);
            idx = group.localVerts.length - 1;
            group.localVMap.set(key, idx);
          } else {
            const accum = group.normalsAccum[idx];
            accum[0] += sideNormal[0];
            accum[1] += sideNormal[1];
            accum[2] += sideNormal[2];
          }
          faceLocalMap.set(vId, idx);
        }
        faceIndices.push(idx);
      }
      if (faceIndices.length === 3) {
        group.localFaces.push(faceIndices);
      }
    }
  };

  for (const [_fId, fData] of builder.faces.entries()) {
    const fallbackColor = ctx.inheritedMaterial?.color ?? ctx.fallbackLayerColor;

    // A face with no material of its own is painted by the instance
    // (SketchUp's "paint the component"). Inheriting the whole material -
    // not just its colour - is what gives computeFaceUv the texture's tile
    // size: without it tileW/tileH fall back to 1 and the UVs come out in
    // raw inches, so a 1.9 m decor sheet tiles ~150 times across a 600 mm
    // panel instead of covering a third of it.
    const frontMat = ctx.resolveMaterial((fData as any).materialId) ?? ctx.inheritedMaterial;
    const backMat = ctx.resolveMaterial((fData as any).backMaterialId) ?? ctx.inheritedMaterial;
    const frontColor = frontMat?.color ?? fallbackColor;
    const backColor = backMat?.color ?? fallbackColor;

    const loops: number[][] = [];
    for (const loop of fData.loops) {
      const loopVerts = reconstructLoopVertices(loop, builder.edges);
      if (loopVerts.length > 0) {
        loops.push(loopVerts);
      }
    }
    if (loops.length === 0) continue;

    let triangles;
    try {
      triangles = triangulateFace3D(builder.vertices, loops, fData.normal);
    } catch (e) {
      throw new SkpParseError(`Failed to triangulate face: ${(e as Error).message}`, {
        stage: 'build_scene',
        definitionId: ctx.definitionId,
        cause: e,
      });
    }

    const fn = fData.normal as [number, number, number];
    const { xr, yr } = faceUvBasis(fn);
    const uvTransform = (fData as any).uvTransform as number[] | null | undefined;
    const uvTransformBack = (fData as any).uvTransformBack as number[] | null | undefined;

    const sameColor =
      frontColor.r === backColor.r && frontColor.g === backColor.g && frontColor.b === backColor.b;
    if (sameColor) {
      addSide(triangles, fn, frontColor, true, false, frontMat, uvTransform, xr, yr);
    } else {
      addSide(triangles, fn, frontColor, false, false, frontMat, uvTransform, xr, yr);
      addSide(triangles, fn, backColor, false, true, backMat, uvTransformBack, xr, yr);
    }
  }

  return faceGroups;
}
