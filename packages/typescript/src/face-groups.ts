import { triangulateFace3D } from './triangulator';
import { reconstructLoopVertices } from './geometry';
import type { GeometryBuilder } from './geometry';
import { SkpParseError } from './errors';
import { emitLog, type LogLevel, type ParseOptions } from './observability';
import { faceUvBasis, computeFaceUv, sniffImageMime } from './model';
import type { Material, SceneTexture, Texture } from './model';

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
  /** The resolved material's opacity: `1.0` fully opaque, `0.0` fully
   * invisible. Carried through so the exported glTF material can declare a
   * real alpha instead of always claiming fully opaque. */
  transparency: number;
  doubleSided: boolean;
  /** True for the back copies of faces with no paint on either side. */
  unpaintedBack: boolean;
  colorized: boolean;
  colorizeType: number;
  materialName?: string;
  /** Local-space vertex positions, inches, SketchUp Z-up. */
  localVerts: [number, number, number][];
  localUvs: [number, number][];
  /** Un-normalised summed face normals per vertex, local space. A corner
   * on a smoothed surface carries that surface's averaged normal instead
   * (see {@link smoothCornerNormals}). */
  normalsAccum: number[][];
  localFaces: number[][];
  backTriangles: number[];
  localVMap: Map<string, number>;
  textureIndex: number | null;
}

/** Everything the grouping needs from its caller that isn't the builder
 * itself: how a material id resolves, how a texture maps to a scene
 * texture index, and the colour to fall back to for an unpainted face. */
export interface FaceGroupContext {
  resolveMaterial: (matId: number | null | undefined) => Material | undefined;
  textureIndexFor: (tex: Texture | null | undefined, materialName?: string) => number | null;
  /** Painted-instance material inherited from the enclosing instance, if any. */
  inheritedMaterial?: Material;
  /** Colour an unpainted face front side falls back to when nothing is
   * inherited (the model's front face colour, or the effective layer's
   * colour). */
  fallbackLayerColor: { r: number; g: number; b: number };
  /** Colour an unpainted face back side falls back to when nothing is
   * inherited (the model's back face colour). Defaults to
   * {@link fallbackLayerColor}. */
  fallbackBackColor?: { r: number; g: number; b: number };
  /** Per-face tag colour resolver used in `unpaintedFaces: 'tag'` mode (#697). */
  faceTagColor?: (layerId: number | null | undefined) => { r: number; g: number; b: number } | null;
  /** Identifies the definition in a triangulation failure. */
  definitionId: number | string;
  /** Skip faces SketchUp itself would not draw (see
   * {@link SceneOptions.respectEdgeVisibility}). Off by default, matching
   * the behaviour every existing consumer already relies on. */
  respectVisibility?: boolean;
  /** Emit each face once with its front material (see
   * {@link SceneOptions.frontSideOnly}). */
  frontSideOnly?: boolean;
}

export type UnpaintedFacesMode = 'style' | 'tag';

export function unpaintedStyleColors(
  faceColors: [[number, number, number], [number, number, number]] | null | undefined,
  unpaintedFaces: string = 'style'
): [{ r: number; g: number; b: number }, { r: number; g: number; b: number }] | null {
  if (unpaintedFaces !== 'style' && unpaintedFaces !== 'tag') {
    throw new RangeError(
      `unpaintedFaces must be "style" or "tag", not ${JSON.stringify(unpaintedFaces)}`
    );
  }
  if (unpaintedFaces !== 'style' || !faceColors) return null;
  const [f, b] = faceColors;
  return [
    { r: f[0], g: f[1], b: f[2] },
    { r: b[0], g: b[1], b: b[2] },
  ];
}

/** A corner (vertex of one face) on a smoothed surface: the surface it
 * belongs to and that surface's unit normal at the vertex. */
interface SmoothCorner {
  root: string;
  n: [number, number, number];
}

/** Edge display-flag bit SketchUp sets on an edge whose faces render as one
 * smooth surface (Soften/Smooth Edges' "Smooth normals"). */
const SMOOTH_EDGE = 0x10;

/**
 * Per-corner normals across smooth edges.
 *
 * SketchUp renders the faces either side of an edge flagged smooth as one
 * continuous surface: their shared vertices get the average of the faces'
 * normals, while a hard edge keeps a crease. Corners (vertex, face) joined
 * through a smooth edge between two faces that face the same way are merged
 * into one surface, and each surface's normal at a vertex is the normalised
 * sum of its faces' normals. Returns null when the definition has no smooth
 * edge, so a faceted model pays nothing.
 */
function smoothCornerNormals(
  builder: GeometryBuilder,
  ctx: FaceGroupContext
): ((vId: number, fId: number) => SmoothCorner | null) | null {
  const edgeFaces = new Map<number, number[]>();
  for (const [fId, face] of builder.faces.entries()) {
    if (ctx.respectVisibility && face.hidden) continue;
    for (const loop of face.loops) {
      for (const { edgeId } of loop) {
        if (((builder.edgeFlags.get(edgeId) ?? 0) & SMOOTH_EDGE) === 0) continue;
        let faces = edgeFaces.get(edgeId);
        if (!faces) edgeFaces.set(edgeId, (faces = []));
        if (!faces.includes(fId)) faces.push(fId);
      }
    }
  }
  if (edgeFaces.size === 0) return null;

  const parent = new Map<string, string>();
  const touch = (key: string): string => {
    if (!parent.has(key)) parent.set(key, key);
    return key;
  };
  const find = (key: string): string => {
    let root = key;
    while (parent.get(root) !== root) root = parent.get(root)!;
    while (parent.get(key) !== root) {
      const next = parent.get(key)!;
      parent.set(key, root);
      key = next;
    }
    return root;
  };
  for (const [edgeId, faces] of edgeFaces) {
    if (faces.length !== 2) continue;
    const ends = builder.edges.get(edgeId);
    if (!ends) continue;
    const a = builder.faces.get(faces[0])!.normal;
    const b = builder.faces.get(faces[1])!.normal;
    // Faces that face opposite ways across the edge (a fold or a reversed
    // face) would cancel each other's normal out.
    if (a[0] * b[0] + a[1] * b[1] + a[2] * b[2] <= 0) continue;
    for (const vId of ends) {
      if (vId === null) continue;
      const left = find(touch(`${vId}:${faces[0]}`));
      const right = find(touch(`${vId}:${faces[1]}`));
      if (left !== right) parent.set(left, right);
    }
  }

  const sums = new Map<string, [number, number, number]>();
  for (const key of parent.keys()) {
    const normal = builder.faces.get(Number(key.slice(key.indexOf(':') + 1)))!.normal;
    const root = find(key);
    const sum = sums.get(root);
    if (sum) {
      sum[0] += normal[0];
      sum[1] += normal[1];
      sum[2] += normal[2];
    } else {
      sums.set(root, [normal[0], normal[1], normal[2]]);
    }
  }
  return (vId, fId) => {
    const key = `${vId}:${fId}`;
    if (!parent.has(key)) return null;
    const root = find(key);
    const sum = sums.get(root)!;
    const length = Math.hypot(sum[0], sum[1], sum[2]) || 1;
    return { root, n: [sum[0] / length, sum[1] / length, sum[2] / length] };
  };
}

/**
 * Group a definition's faces by resolved material identity, in local space.
 *
 * A face whose front and back resolve to the same material, or are both
 * unpainted with the same colour, is emitted once with `doubleSided` set;
 * a face whose sides differ is emitted as two single-sided triangle sets
 * (one normal-wound front, one reverse-wound back) so each side keeps its
 * own material and colour - unless `ctx.frontSideOnly`, which always takes
 * the first branch with the front material.
 *
 * Vertices on a surface SketchUp smooths take that surface's normal, so
 * curved geometry shades smoothly instead of faceted.
 */
export function buildLocalFaceGroups(
  builder: GeometryBuilder,
  ctx: FaceGroupContext
): Map<string, LocalFaceGroup> {
  const faceGroups = new Map<string, LocalFaceGroup>();
  const smoothCorner = smoothCornerNormals(builder, ctx);

  const addSide = (
    triangles: number[][],
    fn: [number, number, number],
    color: { r: number; g: number; b: number; a?: number },
    doubleSided: boolean,
    reverse: boolean,
    mat: Material | undefined,
    uvTransform: number[] | null | undefined,
    xr: [number, number, number],
    yr: [number, number, number],
    fId: number,
    unpaintedBack = false
  ) => {
    // faces are batched per emitted material, so the texture has to be
    // part of the key too - otherwise two differently-textured faces with
    // the same average colour end up in one primitive with one image
    const texIndex = ctx.textureIndexFor(mat?.texture, mat?.name);
    // Two independent SketchUp mechanisms can reduce a material's opacity:
    // the plain RGBA colour record's alpha byte (older/simple materials,
    // and what the writer's addMaterial(rgba) sets), and the newer XML
    // material definition's own `trans`/`useTrans` attribute (surfaced as
    // Material.transparency, already resolved - see parser.test.ts's
    // "useTrans gating" tests). A real file only ever populates one of the
    // two for a given material, but multiplying both is safe either way:
    // the untouched one defaults to fully-opaque (255 / 1.0), so it never
    // silently darkens a material that only used the other mechanism.
    const colorAlpha = color.a !== undefined ? color.a / 255 : 1.0;
    const transparency = colorAlpha * (mat?.transparency ?? 1.0);
    const colorized = Boolean(mat?.colorized) && texIndex !== null;
    const colorizeType = colorized ? (mat?.colorizeType ?? 0) : 0;
    const materialName = mat?.name || undefined;
    const colorKey = `${color.r},${color.g},${color.b},${doubleSided},${texIndex ?? -1},${transparency},${unpaintedBack ? 1 : 0},${colorized ? 1 : 0},${colorizeType}\0${materialName ?? ''}`;
    let group = faceGroups.get(colorKey);
    if (!group) {
      group = {
        color,
        transparency,
        doubleSided,
        unpaintedBack,
        colorized,
        colorizeType,
        materialName,
        textureIndex: texIndex,
        localVerts: [],
        localUvs: [],
        normalsAccum: [],
        localFaces: [],
        backTriangles: [],
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
          // A smoothed corner shares its vertex only with corners of the
          // same surface: a hard edge between two smoothed surfaces still
          // splits the vertex so each side keeps its own normal.
          const smooth = smoothCorner?.(vId, fId) ?? null;
          const normal: [number, number, number] = smooth
            ? reverse
              ? [-smooth.n[0], -smooth.n[1], -smooth.n[2]]
              : smooth.n
            : sideNormal;
          const key = smooth ? `${vId},${u},${v},${smooth.root}` : `${vId},${u},${v}`;
          idx = group.localVMap.get(key);
          if (idx === undefined) {
            group.localVerts.push(p);
            group.localUvs.push([u, v]);
            group.normalsAccum.push([normal[0], normal[1], normal[2]]);
            idx = group.localVerts.length - 1;
            group.localVMap.set(key, idx);
          } else {
            const accum = group.normalsAccum[idx];
            accum[0] += normal[0];
            accum[1] += normal[1];
            accum[2] += normal[2];
          }
          faceLocalMap.set(vId, idx);
        }
        faceIndices.push(idx);
      }
      if (faceIndices.length === 3) {
        if (reverse) {
          group.backTriangles.push(group.localFaces.length);
        }
        group.localFaces.push(faceIndices);
      }
    }
  };

  for (const [fId, fData] of builder.faces.entries()) {
    // A face explicitly hidden in SketchUp. Skipped only on request: the
    // baked output every existing consumer reads includes these, so
    // filtering by default would silently change what they render.
    if (ctx.respectVisibility && fData.hidden) continue;

    const fallbackColor = ctx.inheritedMaterial?.color ?? ctx.fallbackLayerColor;
    const fallbackBackColor =
      ctx.inheritedMaterial?.color ?? ctx.fallbackBackColor ?? ctx.fallbackLayerColor;

    // A face with no material of its own is painted by the instance
    // (SketchUp's "paint the component"). Inheriting the whole material -
    // not just its colour - is what gives computeFaceUv the texture's tile
    // size: without it tileW/tileH fall back to 1 and the UVs come out in
    // raw inches, so a 1.9 m decor sheet tiles ~150 times across a 600 mm
    // panel instead of covering a third of it.
    const frontMat = ctx.resolveMaterial((fData as any).materialId) ?? ctx.inheritedMaterial;
    const backMat = ctx.resolveMaterial((fData as any).backMaterialId) ?? ctx.inheritedMaterial;
    const faceFallback =
      ctx.faceTagColor && (!frontMat || !backMat)
        ? ctx.faceTagColor((fData as any).layerId)
        : null;
    const frontColor = frontMat?.color ?? faceFallback ?? fallbackColor;
    const backColor = backMat?.color ?? faceFallback ?? fallbackBackColor;

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

    const sameSide =
      !frontMat && !backMat
        ? frontColor.r === backColor.r &&
          frontColor.g === backColor.g &&
          frontColor.b === backColor.b
        : frontMat === backMat;
    if (sameSide || ctx.frontSideOnly) {
      addSide(triangles, fn, frontColor, true, false, frontMat, uvTransform, xr, yr, fId);
    } else {
      addSide(triangles, fn, frontColor, false, false, frontMat, uvTransform, xr, yr, fId);
      addSide(
        triangles,
        fn,
        backColor,
        false,
        true,
        backMat,
        uvTransformBack,
        xr,
        yr,
        fId,
        !frontMat && !backMat
      );
    }
  }

  return faceGroups;
}

const ORIGINAL_IMAGE_MIME: Record<string, string> = {
  TIFF: 'image/tiff',
  GIF: 'image/gif',
  BMP: 'image/bmp',
  TGA: 'image/x-tga',
  WebP: 'image/webp',
  PSD: 'image/vnd.adobe.photoshop',
  DDS: 'image/vnd.ms-dds',
  'JPEG 2000': 'image/jp2',
};

function asciiStartsWith(data: Uint8Array, offset: number, text: string): boolean {
  if (offset + text.length > data.length) return false;
  for (let i = 0; i < text.length; i++) {
    if (data[offset + i] !== text.charCodeAt(i)) return false;
  }
  return true;
}

export function describeImageFormat(data: Uint8Array, filename = ''): string {
  if (
    data.length >= 4 &&
    ((data[0] === 0x49 && data[1] === 0x49 && data[2] === 0x2a && data[3] === 0x00) ||
      (data[0] === 0x4d && data[1] === 0x4d && data[2] === 0x00 && data[3] === 0x2a))
  ) {
    return 'TIFF';
  }
  if (asciiStartsWith(data, 0, 'BM')) return 'BMP';
  if (asciiStartsWith(data, 0, 'GIF87a') || asciiStartsWith(data, 0, 'GIF89a')) return 'GIF';
  if (asciiStartsWith(data, 0, 'RIFF') && asciiStartsWith(data, 8, 'WEBP')) return 'WebP';
  if (asciiStartsWith(data, 0, '8BPS')) return 'PSD';
  if (asciiStartsWith(data, 0, 'DDS ')) return 'DDS';
  if (
    data.length >= 8 &&
    data[0] === 0x00 &&
    data[1] === 0x00 &&
    data[2] === 0x00 &&
    data[3] === 0x0c &&
    asciiStartsWith(data, 4, 'jP  ')
  ) {
    return 'JPEG 2000';
  }
  const tailStart = Math.max(0, data.length - 26);
  for (let i = tailStart; i + 16 <= data.length; i++) {
    if (asciiStartsWith(data, i, 'TRUEVISION-XFILE')) return 'TGA';
  }
  const base = filename.replace(/\\/g, '/').split('/').pop() ?? '';
  const dot = base.lastIndexOf('.');
  const ext = dot >= 0 ? base.slice(dot + 1) : '';
  return ext.length > 0 && ext.length <= 5 ? ext.toUpperCase() : 'an unrecognized format';
}

export function originalImageMime(data: Uint8Array): string | null {
  return ORIGINAL_IMAGE_MIME[describeImageFormat(data)] ?? null;
}

export function warnSkippedTexture(
  options: ParseOptions | undefined,
  warned: Set<string>,
  materialName: string | undefined,
  tex: Texture,
  data: Uint8Array
): void {
  const filename = tex.filename ?? '';
  const fmt = describeImageFormat(data, filename);
  const warnKey = `${materialName ?? ''}\0${fmt}`;
  if (warned.has(warnKey)) return;
  warned.add(warnKey);
  const base = filename.replace(/\\/g, '/').split('/').pop() ?? '';
  const keepable = originalImageMime(data) !== null;
  const label = materialName ? `'${materialName}'` : "'(unnamed)'";
  const suffix = base ? ` (${base})` : '';
  const hint = keepable ? ' (pass originalTextures: true to keep it in its own format)' : '';
  emitLog(
    options,
    'warn',
    `Texture of material ${label}${suffix} is ${fmt}, which a scene cannot carry (only PNG and JPEG): ` +
      `the material is built without its texture${hint}`
  );
}

function fnv1a32(data: Uint8Array): number {
  let h = 0x811c9dc5;
  for (let i = 0; i < data.length; i++) {
    h ^= data[i];
    h = Math.imul(h, 0x01000193);
  }
  return h >>> 0;
}

function bytesEqual(a: Uint8Array, b: Uint8Array): boolean {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) {
    if (a[i] !== b[i]) return false;
  }
  return true;
}

/**
 * Returns a per-face `textureIndexFor` closure that deduplicates `textures` by
 * full byte content, checking `Uint8Array` object identity first so repeated
 * lookups for the same material stay O(1).
 */
export function createTextureIndexFor(
  textures: SceneTexture[],
  options: (ParseOptions & { originalTextures?: boolean }) | undefined
): (tex: Texture | null | undefined, materialName?: string) => number | null {
  const indexByObject = new WeakMap<Uint8Array, number>();
  const buckets = new Map<number, number[]>();
  const skippedTextures = new Set<string>();

  return function textureIndexFor(
    tex: Texture | null | undefined,
    materialName?: string
  ): number | null {
    if (!tex || !tex.data || tex.data.length === 0) return null;
    const data = tex.data;
    const cached = indexByObject.get(data);
    if (cached !== undefined) return cached;

    let mimeType: string | null = sniffImageMime(data);
    if (mimeType === null && options?.originalTextures) {
      mimeType = originalImageMime(data);
    }
    if (mimeType === null) {
      warnSkippedTexture(options, skippedTextures, materialName, tex, data);
      return null;
    }

    const hash = fnv1a32(data);
    let bucket = buckets.get(hash);
    if (bucket) {
      for (const cand of bucket) {
        if (bytesEqual(textures[cand].data, data)) {
          indexByObject.set(data, cand);
          return cand;
        }
      }
    } else {
      bucket = [];
      buckets.set(hash, bucket);
    }

    const idx = textures.length;
    textures.push({ data, mimeType, filename: tex.filename });
    bucket.push(idx);
    indexByObject.set(data, idx);
    return idx;
  };
}

/**
 * Assigns a deterministic per-run integer ID to each distinct `Uint8Array`
 * byte sequence (identity fast path + full-byte comparison) without side
 * effects on `scene.textures`.
 */
export function createByteContentKeyer(): (data: Uint8Array) => number {
  const idByObject = new WeakMap<Uint8Array, number>();
  const buckets = new Map<number, number[]>();
  const seen: Uint8Array[] = [];

  return (data: Uint8Array): number => {
    const cached = idByObject.get(data);
    if (cached !== undefined) return cached;
    const hash = fnv1a32(data);
    let bucket = buckets.get(hash);
    if (bucket) {
      for (const cand of bucket) {
        if (bytesEqual(seen[cand], data)) {
          idByObject.set(data, cand);
          return cand;
        }
      }
    } else {
      bucket = [];
      buckets.set(hash, bucket);
    }
    const id = seen.length;
    seen.push(data);
    bucket.push(id);
    idByObject.set(data, id);
    return id;
  };
}

/**
 * Convert one sRGB component in `[0, 1]` to linear light per IEC 61966-2-1
 * (glTF 2.0's `baseColorFactor` colour space).
 */
function srgbToLinear(c: number): number {
  if (c <= 0) return 0;
  if (c >= 1) return 1;
  return c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
}

function linearizeBaseColorFactor(pbr: any): any {
  const bcf = pbr?.baseColorFactor;
  if (!Array.isArray(bcf) || bcf.length < 3) return pbr;
  return {
    ...pbr,
    baseColorFactor: [
      srgbToLinear(bcf[0]),
      srgbToLinear(bcf[1]),
      srgbToLinear(bcf[2]),
      bcf.length > 3 ? bcf[3] : 1.0,
    ],
  };
}

function isColorizedMaterial(mat: any): boolean {
  return Boolean(mat?.extras?.sketchup?.colorized);
}

/**
 * Prepares `textures` and `materials` for GLB serialization without mutating
 * the input scene: strips all `baseColorTexture` references when `embed` is
 * off, omits any non-PNG/JPEG texture (such as TIFF or BMP kept via
 * `originalTextures: true`) when `embed` is on with remapped indices and one
 * warning per skipped image, converts untextured `baseColorFactor` RGB
 * components from sRGB to linear light (alpha unchanged), and sets
 * non-colorized embedded-texture materials' `baseColorFactor` to
 * `[1, 1, 1, alpha]` so the texture is not darkened by its average colour.
 */
export function filterGltfTextures(
  rawTextures: SceneTexture[],
  rawMaterials: unknown[],
  embed: boolean,
  onLog?: (level: LogLevel, message: string) => void
): { textures: SceneTexture[]; materials: unknown[] } {
  if (!embed || rawTextures.length === 0) {
    const stripped = rawMaterials.map((mat) => {
      const m = mat as any;
      const pbr = m?.pbrMetallicRoughness;
      if (!pbr) return mat;
      const nextPbr = linearizeBaseColorFactor(pbr);
      if (nextPbr.baseColorTexture) {
        const restPbr = { ...nextPbr };
        delete restPbr.baseColorTexture;
        return { ...m, pbrMetallicRoughness: restPbr };
      }
      return nextPbr === pbr ? mat : { ...m, pbrMetallicRoughness: nextPbr };
    });
    return { textures: [], materials: stripped };
  }

  const kept: SceneTexture[] = [];
  const remap: (number | null)[] = new Array(rawTextures.length);
  const warned = new Set<string>();

  for (let i = 0; i < rawTextures.length; i++) {
    const tex = rawTextures[i];
    if (tex.mimeType === 'image/png' || tex.mimeType === 'image/jpeg') {
      remap[i] = kept.length;
      kept.push(tex);
    } else {
      remap[i] = null;
      const filename = tex.filename ?? '';
      const base = filename.replace(/\\/g, '/').split('/').pop() ?? '';
      const fmt = describeImageFormat(tex.data, filename);
      const warnKey = `${base}\0${fmt}`;
      if (!warned.has(warnKey)) {
        warned.add(warnKey);
        const label = base ? ` '${base}'` : '';
        onLog?.(
          'warn',
          `Texture${label} is ${fmt}, which glTF 2.0 cannot carry (only PNG and JPEG): leaving it out of the GLB`
        );
      }
    }
  }

  const materials = rawMaterials.map((mat) => {
    const m = mat as any;
    const pbr = m?.pbrMetallicRoughness;
    if (!pbr) return mat;
    const texRef = pbr.baseColorTexture;
    if (!texRef || typeof texRef.index !== 'number') {
      const nextPbr = linearizeBaseColorFactor(pbr);
      return nextPbr === pbr ? mat : { ...m, pbrMetallicRoughness: nextPbr };
    }
    const mapped = texRef.index >= 0 && texRef.index < remap.length ? remap[texRef.index] : null;
    if (mapped === null) {
      const restPbr = linearizeBaseColorFactor(pbr);
      delete restPbr.baseColorTexture;
      const nextMat = { ...m, pbrMetallicRoughness: restPbr };
      if (nextMat.alphaMode === 'MASK') delete nextMat.alphaMode;
      return nextMat;
    }
    if (isColorizedMaterial(m)) {
      if (mapped === texRef.index) return mat;
      return {
        ...m,
        pbrMetallicRoughness: {
          ...pbr,
          baseColorTexture: { ...texRef, index: mapped },
        },
      };
    }
    const bcf = pbr.baseColorFactor;
    const alpha = Array.isArray(bcf) && bcf.length > 3 ? bcf[3] : 1.0;
    return {
      ...m,
      pbrMetallicRoughness: {
        ...pbr,
        baseColorFactor: [1.0, 1.0, 1.0, alpha],
        baseColorTexture: mapped === texRef.index ? texRef : { ...texRef, index: mapped },
      },
    };
  });

  return { textures: kept, materials };
}
