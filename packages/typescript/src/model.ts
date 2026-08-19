import { transformPoint, multiplyMatrices } from './transforms';
import { triangulateFace3D } from './triangulator';
import { reconstructLoopVertices, extractDynamicProperties, ParsedDefinition } from './geometry';
import { SkpParseError } from './errors';
import { ParseOptions, PROGRESS_INTERVAL, emitLog, emitProgress } from './observability';

export interface SkpModel {
  version: string;
  definitions: Map<number, Definition>;
  /** The implicit top-level model definition: its `instances` are the
   * entities placed directly in the model (not inside any component/
   * group), and its `vertices`/`edges`/`faces` are geometry drawn directly
   * at the top level. Corresponds to .NET/Dart's `Root`/`root`. */
  root: Definition;
  layers: Layer[];
  materials: Material[];
  materialsById: Map<number, Material>;
  styles: Style[];
  /** The model's unit-system string (e.g. "Millimeter"), read from
   * meta/meta.dat in modern (VFF) files. null for legacy (pre-2021 MFC)
   * files, which carry no equivalent container, or when the tag isn't
   * found. */
  units: string | null;
}

export interface SectionPlane {
  plane: [number, number, number, number];
  name: string;
  label: string;
  hidden: boolean;
}

export interface TextEntity {
  text: string;
  hidden: boolean;
}

export interface Dimension {
  text: string;
  hidden: boolean;
}

export interface Definition {
  id: number;
  guid: string;
  name: string;
  vertices: Vertex[];
  edges: Edge[];
  faces: Face[];
  instances: Instance[];
  sectionPlanes: SectionPlane[];
  texts: TextEntity[];
  dimensions: Dimension[];
  isImage: boolean;
  alwaysFacesCamera: boolean;
  shadowsFaceSun: boolean;
}

export interface Vertex {
  id: number;
  x: number;
  y: number;
  z: number;
}

export interface Edge {
  id: number;
  v1Id: number;
  v2Id: number;
  soft: boolean;
  smooth: boolean;
  hidden: boolean;
}

export interface Face {
  id: number;
  loops: CoEdge[][];
  normal: [number, number, number];
  /** Material of the face's FRONT side, or null. */
  materialId: number | null;
  /** Material of the face's BACK side, or null. */
  backMaterialId: number | null;
  /**
   * Per-face texture mapping for a positioned / photo-fitted texture
   * (SketchUp's pins), or null when the texture is untouched (default
   * projection applies). A 9-element array: a 3x3 row-major matrix mapping
   * texture space -> face plane. To compute the UV of a point p (inches):
   *
   * 1. Plane basis from the face normal n: xr = normalize(Z x n),
   *    yr = n x xr (for a vertical n: xr = X, yr = +-Y by the sign of n.Z).
   * 2. uvq = [p.xr, p.yr, 1] @ inv(M) (row-vector convention).
   * 3. u = uvq[0]/uvq[2] / tileW, v = uvq[1]/uvq[2] / tileH with the
   *    material texture's tile size in inches.
   *
   * When the texture is untouched (null), the default is
   * u = (p.xr)/tileW, v = (p.yr)/tileH. Distorted (4-pin) mappings are
   * projective: uvq[2] != 1.
   */
  uvTransform: number[] | null;
  /** Same for the face's back side, or null. */
  uvTransformBack: number[] | null;
  /** The texture is PROJECTED (e.g. the Add Location terrain drape): its
   * UVs run in the projection plane's frame, not the face frame. */
  uvProjected: boolean;
  /** Same for the face's back side. */
  uvProjectedBack: boolean;
  /** Whether the face is hidden (SketchUp's "Hide" on this specific face,
   * not a layer/tag visibility toggle). */
  hidden: boolean;
}

export interface CoEdge {
  edgeId: number;
  orientation: number;
}

/** A placed instance (component or group) inside a Definition's own instance list. */
export interface Instance {
  name: string;
  refIdx: number;
  guid: string;
  matrix: number[];
  /**
   * Material painted onto the instance itself (SketchUp's "paint the
   * component"), or null. Faces inside the placed definition whose own
   * Face.materialId is null inherit this material - consumers must resolve
   * that inheritance themselves, like the official SDK does on export.
   */
  materialId: number | null;
  /** Whether the instance itself is hidden (SketchUp's "Hide" on this
   * specific component/group placement, not a layer/tag visibility
   * toggle). */
  hidden: boolean;
}

export interface Layer {
  name: string;
  color: { r: number; g: number; b: number };
  /** Whether the layer's visibility is switched off. Only populated for
   * legacy (pre-2021 MFC) files, where the byte is read directly from the
   * layer record - modern (VFF) files derive layers from
   * `Layer_<name>`-prefixed materials, which carry no visibility data, so
   * this is always `false` there. */
  hidden: boolean;
}

/** A material's texture image, extracted from the SKP container. */
export interface Texture {
  filename: string;
  width: number;
  height: number;
  data: Uint8Array | null;
}

/** A rendering style bundled in the file (SketchUp's Styles browser). */
export interface Style {
  name: string;
  frontColor: [number, number, number] | null;
  backColor: [number, number, number] | null;
}

export interface Material {
  name: string;
  color: { r: number; g: number; b: number; a: number };
  transparency: number;
  id: number | null;
  texture: Texture | null;
  colorized: boolean;
  colorizeType: number;
}

export interface InstanceNode {
  name: string;
  definitionName: string;
  layer: string;
  positionMm: [number, number, number];
  properties: Record<string, string>;
  children: InstanceNode[];
}

export interface MeshMetadata {
  name: string;
  definitionName: string;
  layer: string;
  positionMm: [number, number, number];
  properties: Record<string, string>;
  path: string;
}

/** One triangulated, world-space mesh: all faces (or, for a face whose
 * front/back colors genuinely differ, all *one side* of those faces)
 * sharing a single resolved color from one flattened scene-graph position.
 * Ready to hand straight to a GLB/glTF exporter or any other renderer. */
export interface GlbPrimitive {
  /** Flat [x, y, z, x, y, z, ...] vertex positions, in metres, Y-up. */
  positions: Float32Array;
  /** Flat [x, y, z, ...] vertex normals, matching `positions` 1:1. */
  normals: Float32Array;
  /** Flat [u, v, u, v, ...] texture coordinates, matching `positions` 1:1.
   * Computed from each source face's `uvTransform` (or the default
   * face-plane projection when a face has none) - see `Face.uvTransform`'s
   * docs for the formula. A vertex shared by two faces that disagree on UV
   * is split, since indexed glTF meshes need position/normal/uv aligned
   * per vertex. Faces with a PROJECTED texture (terrain-drape textures,
   * e.g. Add Location) still use the face-plane formula here, since the
   * real projection-plane basis isn't captured in the parsed data - their
   * UVs will be approximate. */
  uvs: Float32Array;
  /** Triangle vertex indices into `positions`/`normals`/`uvs` (3 per
   * triangle). */
  indices: Uint32Array;
  /** Index into `gltfMaterials` for this primitive's resolved color. */
  materialIndex: number;
  /** Matches the corresponding key in `SkpScene.meshIndex`. */
  geomName: string;
}

/**
 * The result of baking a parsed file's placed instances into a flat,
 * world-space 3D scene: every instance's geometry triangulated and
 * transformed into its final position, ready for rendering or GLB export.
 *
 * This is deliberately a *separate*, opt-in step from {@link SkpModel} -
 * for a file with many repeated instances, baking the scene can produce far
 * more data than the file's raw (per-definition, un-instanced) geometry, so
 * callers who only need the raw model data never pay for it.
 */
export interface SkpScene {
  /** The root of the world-space instance tree. */
  sceneHierarchy: InstanceNode;
  /** Metadata for every baked mesh, keyed the same as `glbPrimitives`'
   * `geomName`. */
  meshIndex: Record<string, MeshMetadata>;
  /** The actual triangulated mesh data, one entry per unique
   * (definition, resolved color) combination actually placed in the scene. */
  glbPrimitives: GlbPrimitive[];
  /** glTF PBR material definitions referenced by `GlbPrimitive.materialIndex`.
   * A material whose source had a texture image carries a `baseColorTexture`
   * whose `index` points into {@link SkpScene.textures}. */
  gltfMaterials: unknown[];
  /** The distinct texture images the placed materials use, deduplicated by
   * source bytes. Empty when nothing placed in the scene is textured.
   *
   * Kept out of `gltfMaterials` so a caller can decide whether to pay for
   * them: {@link toGLB} embeds these only when asked, since a model with a
   * handful of photographic textures is several times larger with them than
   * without. */
  textures: SceneTexture[];
}

/**
 * Image type from the file's magic bytes. glTF only carries PNG and JPEG, so
 * anything else (TIFF from older SketchUp, say) reports null and the material
 * keeps its flat colour instead of embedding an image no viewer would read.
 */
export function sniffImageMime(data: Uint8Array): 'image/png' | 'image/jpeg' | null {
  if (data.length >= 3 && data[0] === 0xff && data[1] === 0xd8 && data[2] === 0xff) {
    return 'image/jpeg';
  }
  if (
    data.length >= 8 &&
    data[0] === 0x89 && data[1] === 0x50 && data[2] === 0x4e && data[3] === 0x47 &&
    data[4] === 0x0d && data[5] === 0x0a && data[6] === 0x1a && data[7] === 0x0a
  ) {
    return 'image/png';
  }
  return null;
}

/** One texture image referenced by {@link SkpScene.gltfMaterials}. */
export interface SceneTexture {
  /** The image file's raw bytes, exactly as they were stored in the .skp. */
  data: Uint8Array;
  /** Sniffed from the bytes, not from `filename`: SketchUp records the
   * authoring machine's path, whose extension can disagree with the content. */
  mimeType: 'image/png' | 'image/jpeg';
  /** The material's texture path as recorded in the file. Informational: it
   * is usually an absolute path on the machine that authored the model. */
  filename: string;
}

/** Raw parsed data, source-agnostic (populated by either the VFF/ZIP path
 * in index.ts or the legacy MFC walker in legacy.ts), that
 * {@link buildModelFromParsed} turns into the final public
 * {@link SkpModel} - including scene-hierarchy resolution and GLB
 * primitive building, which both formats share. */
export interface ParsedRawData {
  version: string;
  /** The model's unit-system string (e.g. "Millimeter"), read from
   * meta/meta.dat. null for legacy files or when the tag isn't found. */
  units: string | null;
  layerColors: Map<string, [number, number, number]>;
  layerHidden: Map<string, boolean>;
  layerIdToName: Map<number, string>;
  materialIdToName: Map<number, string>;
  materialsMap: Map<string, Material>;
  materialsByFolder: Map<string, Material>;
  styles: Style[];
  defsDict: Map<number | string, ParsedDefinition>;
}

export function buildModelFromParsed(parsed: ParsedRawData): SkpModel {
  const {
    version,
    units,
    layerColors,
    layerHidden,
    materialIdToName,
    materialsMap,
    materialsByFolder,
    styles,
    defsDict,
  } = parsed;

  // Join the TLV material IDs (what Face.materialId references) onto the
  // parsed materials, so callers can resolve face -> material.
  // materialsMap/materialsByFolder may share the same Material object
  // reference for an alias, so setting `.id` here is visible through both.
  const materialsById = new Map<number, Material>();
  for (const [mId, mName] of materialIdToName.entries()) {
    const mat = materialsMap.get(mName) || materialsByFolder.get(mName);
    if (!mat) continue;
    if (mat.id === null) {
      mat.id = mId;
    }
    materialsById.set(mId, mat);
  }

  const finalLayersList: Layer[] = Array.from(layerColors.entries()).map(([name, c]) => ({
    name,
    color: { r: c[0], g: c[1], b: c[2] },
    hidden: layerHidden.get(name) ?? false,
  }));

  const finalMaterialsList: Material[] = Array.from(materialsMap.values());

  const finalDefinitions = new Map<number, Definition>();
  let rootDefinition: Definition | null = null;
  for (const [id, d] of defsDict.entries()) {
    const defn = buildDefinition(typeof id === 'number' ? id : 0, d);
    if (typeof id === 'number') {
      finalDefinitions.set(id, defn);
    } else {
      rootDefinition = defn;
    }
  }

  return {
    version,
    definitions: finalDefinitions,
    // The implicit top-level model definition: its instances are the
    // entities placed directly in the model (not inside any component/
    // group). Kept out of `definitions` (which is numeric-ID-only, one
    // entry per real component/group definition) and exposed here instead,
    // matching the .NET and Dart ports' `Root`/`root` field.
    root: rootDefinition ?? { id: 0, guid: 'ROOT', name: 'ROOT_MODEL', vertices: [], edges: [], faces: [], instances: [], sectionPlanes: [], texts: [], dimensions: [], isImage: false, alwaysFacesCamera: false, shadowsFaceSun: false },
    layers: finalLayersList,
    materials: finalMaterialsList,
    materialsById,
    styles,
    units,
  };
}

function buildDefinition(id: number, d: ParsedDefinition): Definition {
  const vertices: Vertex[] = Array.from(d.builder.vertices.entries()).map(([vId, [x, y, z]]) => ({
    id: vId,
    x,
    y,
    z,
  }));
  const edges: Edge[] = Array.from(d.builder.edges.entries()).map(([eId, [v1, v2]]) => {
    const flags = d.builder.edgeFlags.get(eId) ?? 0;
    return {
      id: eId,
      v1Id: v1 ?? 0,
      v2Id: v2 ?? 0,
      soft: (flags & 0x08) !== 0,
      smooth: (flags & 0x10) !== 0,
      hidden: (flags & 0x01) !== 0,
    };
  });
  const faces: Face[] = Array.from(d.builder.faces.entries()).map(([fId, fData]) => ({
    id: fId,
    loops: fData.loops,
    normal: fData.normal,
    materialId: fData.materialId ?? null,
    backMaterialId: fData.backMaterialId ?? null,
    uvTransform: fData.uvTransform ?? null,
    uvTransformBack: fData.uvTransformBack ?? null,
    uvProjected: fData.uvProjected ?? false,
    uvProjectedBack: fData.uvProjectedBack ?? false,
    hidden: fData.hidden ?? false,
  }));
  const instances: Instance[] = d.builder.instances.map((inst) => ({
    name: inst.name,
    refIdx: inst.refIdx,
    guid: inst.refGuid,
    matrix: inst.matrix,
    materialId: inst.materialId,
    hidden: inst.hidden ?? false,
  }));

  const sectionPlanes: SectionPlane[] = (d.builder.sectionPlanes || []).map((sp) => ({
    plane: sp.plane || [0, 0, 1, 0],
    name: sp.name || '',
    label: sp.label || '',
    hidden: sp.hidden || false,
  }));
  const texts: TextEntity[] = (d.builder.texts || []).map((txt) => ({
    text: txt.text || '',
    hidden: txt.hidden || false,
  }));
  const dimensions: Dimension[] = (d.builder.dimensions || []).map((dim) => ({
    text: dim.text || '',
    hidden: dim.hidden || false,
  }));

  return {
    id,
    guid: d.guid,
    name: d.name,
    vertices,
    edges,
    faces,
    instances,
    sectionPlanes,
    texts,
    dimensions,
    isImage: d.isImage,
    alwaysFacesCamera: d.alwaysFacesCamera,
    shadowsFaceSun: d.shadowsFaceSun || false,
  };
}

/** Inverse of a row-major 3x3 matrix, via the cofactor/adjugate method. */
function invertMatrix3x3(m: number[]): number[] {
  const [a, b, c, d, e, f, g, h, i] = m;
  const det = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g);
  if (Math.abs(det) < 1e-12) {
    return [1, 0, 0, 0, 1, 0, 0, 0, 1];
  }
  const invDet = 1 / det;
  return [
    (e * i - f * h) * invDet, (c * h - b * i) * invDet, (b * f - c * e) * invDet,
    (f * g - d * i) * invDet, (a * i - c * g) * invDet, (c * d - a * f) * invDet,
    (d * h - e * g) * invDet, (b * g - a * h) * invDet, (a * e - b * d) * invDet,
  ];
}

/** Face-plane basis vectors (xr, yr) for UV projection, from a face
 * normal. See `Face.uvTransform`'s docs for the recipe this implements. */
function faceUvBasis(n: [number, number, number]): { xr: [number, number, number]; yr: [number, number, number] } {
  const [nx, ny, nz] = n;
  const cx = -ny;
  const cy = nx;
  const clen = Math.sqrt(cx * cx + cy * cy);
  let xr: [number, number, number];
  let yr: [number, number, number];
  if (clen < 1e-9) {
    xr = [1, 0, 0];
    yr = [0, nz >= 0 ? 1 : -1, 0];
  } else {
    xr = [cx / clen, cy / clen, 0];
    yr = [ny * xr[2] - nz * xr[1], nz * xr[0] - nx * xr[2], nx * xr[1] - ny * xr[0]];
  }
  return { xr, yr };
}

/** UV of point p (inches, local/object space) on a face with the given
 * plane basis, per-face uvTransform (or null for the default projection),
 * and material tile size (inches). */
function computeFaceUv(
  p: [number, number, number],
  xr: [number, number, number],
  yr: [number, number, number],
  uvTransform: number[] | null | undefined,
  tileW: number,
  tileH: number
): [number, number] {
  const px = p[0] * xr[0] + p[1] * xr[1] + p[2] * xr[2];
  const py = p[0] * yr[0] + p[1] * yr[1] + p[2] * yr[2];
  if (!uvTransform) {
    return [px / tileW, py / tileH];
  }
  const inv = invertMatrix3x3(uvTransform);
  const u = px * inv[0] + py * inv[3] + inv[6];
  const v = px * inv[1] + py * inv[4] + inv[7];
  let q = px * inv[2] + py * inv[5] + inv[8];
  if (Math.abs(q) < 1e-12) q = 1;
  return [u / q / tileW, v / q / tileH];
}

/**
 * Bake every instance actually placed in the model into world-space,
 * triangulated mesh data - SketchUp's component/group nesting fully
 * resolved and flattened, ready for a GLB export or any other renderer.
 *
 * This walks the *entire* placed scene graph, so for a file that reuses a
 * handful of definitions across many thousands of instances, the output
 * here can be far larger than the file's raw (un-instanced) geometry -
 * that's why it's a separate, opt-in step from {@link buildModelFromParsed}
 * rather than something every parse() pays for.
 */
export function buildSceneFromParsed(parsed: ParsedRawData, options?: ParseOptions): SkpScene {
  const t0 = Date.now();
  const { layerColors, layerIdToName, materialIdToName, materialsMap, materialsByFolder, defsDict } = parsed;

  emitLog(options, 'info', `Building scene: ${defsDict.size} definitions available`);
  const instanceCounter = { count: 0 };

  // Instantiate scene hierarchy and gather mesh metadata & GLB primitives
  const meshCounter = { count: 0 };
  const meshIndex: Record<string, MeshMetadata> = {};
  const glbPrimitives: any[] = [];

  const getLayerColor = (name: string) => {
    const c = layerColors.get(name) || [136, 136, 136];
    return { r: c[0], g: c[1], b: c[2] };
  };

  // Textures are deduplicated by their bytes: the same image routinely backs
  // several materials, and re-embedding it per material would multiply the
  // export size for nothing.
  const textures: SceneTexture[] = [];
  const textureIndexByKey = new Map<string, number>();

  function textureIndexFor(tex: Texture | null | undefined): number | null {
    if (!tex || !tex.data || tex.data.length === 0) return null;
    const mimeType = sniffImageMime(tex.data);
    if (mimeType === null) return null; // a format glTF cannot carry
    // length plus a short byte prefix is enough to tell real images apart
    // without hashing megabytes on every face
    const head = Array.from(tex.data.subarray(0, 16)).join(',');
    const key = `${tex.data.length}:${head}`;
    const hit = textureIndexByKey.get(key);
    if (hit !== undefined) return hit;
    const idx = textures.length;
    textures.push({ data: tex.data, mimeType, filename: tex.filename });
    textureIndexByKey.set(key, idx);
    return idx;
  }

  const colorToMaterialIndex = new Map<string, number>();
  const gltfMaterials: any[] = [];

  // Definitions currently being instantiated on the active recursion path
  // (not "ever visited" - the same definition legitimately reused by
  // sibling instances is fine). Guards against a component that directly
  // or transitively instances itself, which would otherwise recurse until
  // the stack overflows.
  const activeDefinitions = new Set<number | string>();

  function getMaterialIndex(
    color: { r: number; g: number; b: number },
    doubleSided: boolean,
    textureIndex: number | null
  ) {
    // The texture is part of the identity, not just the colour: two different
    // images can average to the same RGB (real files do this - two fabrics
    // both resolving to 141,141,141), and keying on colour alone would merge
    // them into one material and lose one of the images.
    const key = `${color.r},${color.g},${color.b},${doubleSided},${textureIndex ?? -1}`;
    if (colorToMaterialIndex.has(key)) {
      return colorToMaterialIndex.get(key)!;
    }
    const idx = gltfMaterials.length;
    const pbr: any = {
      baseColorFactor: [color.r / 255, color.g / 255, color.b / 255, 1.0],
      metallicFactor: 0.0,
      roughnessFactor: 0.8,
    };
    // baseColorFactor stays as the resolved colour even with a texture
    // attached: glTF multiplies the two, and SketchUp's own colorized
    // materials rely on exactly that tint. Overwriting it with white would
    // also drop the colour every existing consumer of this exporter reads.
    if (textureIndex !== null) {
      pbr.baseColorTexture = { index: textureIndex };
    }
    const material: any = { pbrMetallicRoughness: pbr };
    if (doubleSided) material.doubleSided = true;
    gltfMaterials.push(material);
    colorToMaterialIndex.set(key, idx);
    return idx;
  }

  function instantiate(
    defId: number | string,
    currentMatrix: number[],
    parentLayer: string = 'Layer0',
    pathName: string = 'ROOT',
    inheritedMaterial?: Material
  ): InstanceNode[] {
    const d = defsDict.get(defId);
    if (!d) return [];

    const builder = d.builder;

    if (builder.faces.size > 0) {
      // Group faces sharing a resolved (color, doubleSided) pair into one
      // mesh each - same grouping the C++ reference uses (the only port
      // that already had this before this port): a face whose front/back
      // resolve to the SAME color is emitted once, with its glTF material
      // marked doubleSided so it's visible from either side without
      // needing duplicate geometry; a face whose front/back genuinely
      // differ is emitted as TWO single-sided triangle sets - one
      // normal-wound using the front material, one reverse-wound using
      // the back material - so each side renders its own correct color
      // instead of the front material leaking onto (or the back
      // vanishing from) the far side.
      type FaceGroup = {
        color: { r: number; g: number; b: number };
        doubleSided: boolean;
        localVerts: [number, number, number][];
        localUvs: [number, number][];
        normalsAccum: number[][];
        localFaces: number[][];
        localVMap: Map<string, number>;
        textureIndex: number | null;
      };
      const faceGroups = new Map<string, FaceGroup>();

      const resolveMaterial = (matId: number | null | undefined): Material | undefined =>
        resolveMaterialFromMaps(matId, materialIdToName, materialsMap, materialsByFolder);

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
        const texIndex = textureIndexFor(mat?.texture);
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
        const fallbackColor = inheritedMaterial?.color ?? getLayerColor(parentLayer);

        // A face with no material of its own is painted by the instance
        // (SketchUp's "paint the component"). Inheriting the whole material -
        // not just its colour - is what gives computeFaceUv the texture's tile
        // size: without it tileW/tileH fall back to 1 and the UVs come out in
        // raw inches, so a 1.9 m decor sheet tiles ~150 times across a 600 mm
        // panel instead of covering a third of it.
        const frontMat = resolveMaterial((fData as any).materialId) ?? inheritedMaterial;
        const backMat = resolveMaterial((fData as any).backMaterialId) ?? inheritedMaterial;
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
            definitionId: defId,
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

      for (const group of faceGroups.values()) {
        if (group.localFaces.length === 0) continue;

        const isRoot = pathName === 'ROOT';
        const tx = isRoot ? 0 : (currentMatrix[9] ?? 0) * 25.4;
        const ty = isRoot ? 0 : (currentMatrix[10] ?? 0) * 25.4;
        const tz = isRoot ? 0 : (currentMatrix[11] ?? 0) * 25.4;

        let safePath = pathName.replace(/ \/ /g, '__').replace(/ /g, '_');
        if (safePath.length > 80) safePath = safePath.slice(0, 80);

        const colorSuffix =
          faceGroups.size > 1
            ? `_${group.color.r}_${group.color.g}_${group.color.b}_${group.doubleSided ? 'ds' : 'ss'}`
            : '';
        const geomName = `mesh_${meshCounter.count}_${safePath}_${parentLayer}${colorSuffix}`;
        meshCounter.count++;

        meshIndex[geomName] = {
          name: isRoot ? 'ROOT' : pathName.split(' / ').pop() || '',
          definitionName: d.name || '',
          layer: parentLayer,
          positionMm: [Math.round(tx * 100) / 100, Math.round(ty * 100) / 100, Math.round(tz * 100) / 100],
          properties: {},
          path: pathName,
        };

        const scale = 0.0254;
        const positions = new Float32Array(group.localVerts.length * 3);
        const normals = new Float32Array(group.localVerts.length * 3);
        const uvs = new Float32Array(group.localVerts.length * 2);
        const vertexNormalsAccum = group.normalsAccum;

        for (let i = 0; i < group.localVerts.length; i++) {
          const v = group.localVerts[i];
          const pt = transformPoint(currentMatrix, v);
          positions[i * 3] = pt[0] * scale;
          positions[i * 3 + 1] = pt[2] * scale;
          positions[i * 3 + 2] = -pt[1] * scale;

          uvs[i * 2] = group.localUvs[i][0];
          uvs[i * 2 + 1] = group.localUvs[i][1];

          const rawNorm = vertexNormalsAccum[i];
          const normLen = Math.sqrt(rawNorm[0] ** 2 + rawNorm[1] ** 2 + rawNorm[2] ** 2);
          const n = normLen > 1e-6 ? [rawNorm[0] / normLen, rawNorm[1] / normLen, rawNorm[2] / normLen] : [0, 0, 1];

          const nx = currentMatrix[0] * n[0] + currentMatrix[1] * n[1] + currentMatrix[2] * n[2];
          const ny = currentMatrix[3] * n[0] + currentMatrix[4] * n[1] + currentMatrix[5] * n[2];
          const nz = currentMatrix[6] * n[0] + currentMatrix[7] * n[1] + currentMatrix[8] * n[2];

          const l = Math.sqrt(nx * nx + ny * ny + nz * nz);
          if (l > 1e-6) {
            normals[i * 3] = nx / l;
            normals[i * 3 + 1] = nz / l;
            normals[i * 3 + 2] = -ny / l;
          } else {
            normals[i * 3] = 0;
            normals[i * 3 + 1] = 1;
            normals[i * 3 + 2] = 0;
          }
        }

        const indices = new Uint32Array(group.localFaces.length * 3);
        for (let i = 0; i < group.localFaces.length; i++) {
          indices[i * 3] = group.localFaces[i][0];
          indices[i * 3 + 1] = group.localFaces[i][1];
          indices[i * 3 + 2] = group.localFaces[i][2];
        }

        const materialIndex = getMaterialIndex(group.color, group.doubleSided, group.textureIndex);

        glbPrimitives.push({
          positions,
          normals,
          uvs,
          indices,
          materialIndex,
          geomName,
        });
      }
    }

    const childInstancesInfo: InstanceNode[] = [];

    for (const inst of builder.instances) {
      const refIdx = inst.refIdx;
      const instMatrix = inst.matrix;
      const newMatrix = multiplyMatrices(currentMatrix, instMatrix);

      let lName = parentLayer;
      let instMaterial = inheritedMaterial;
      // Legacy (pre-2021 MFC) instances carry a precomputed `properties`
      // record (see legacy.ts's extractLegacyDynamicProperties) - VFF
      // instances don't set this, so this stays {} for them and gets
      // overwritten below via the D007/DC05 TLV walk instead.
      let properties: Record<string, string> = { ...(inst.properties || {}) };

      // Layer and instance-material resolution use the fields already
      // extracted onto the builder instance (same source data for VFF -
      // D007/D207/D107 - read once in geometry.ts; legacy files populate
      // the same fields directly since they have no TLV children).
      if (inst.layerId !== null && inst.layerId !== undefined) {
        lName = layerIdToName.get(inst.layerId) || parentLayer;
      }

      if (inst.materialId !== null && inst.materialId !== undefined) {
        const matName = materialIdToName.get(inst.materialId);
        if (matName) {
          const mat = materialsMap.get(matName) || materialsByFolder.get(matName);
          if (mat) {
            instMaterial = mat;
          }
        }
      }

      // D007/DC05 TLV walk (VFF only); inst.children is always empty for
      // legacy instances, so this is a no-op there and the precomputed
      // `properties` seeded above survives unchanged.
      const d007 = inst.children.find((c) => c.tag === 'D007');
      if (d007) {
        try {
          properties = extractDynamicProperties(d007, options);
        } catch (e) {
          emitLog(
            options, 'debug',
            `Failed to extract dynamic properties for instance ${inst.name ?? ''} (refIdx=${refIdx}): ${(e as Error).message}`
          );
        }
      }

      const instName = inst.name || `Component_${refIdx}`;
      const fullPathName = `${pathName} / ${instName}`;
      instanceCounter.count++;
      if (instanceCounter.count % PROGRESS_INTERVAL === 0) {
        emitProgress(options, 'build_scene', instanceCounter.count, instanceCounter.count);
        emitLog(options, 'debug', `Processed ${instanceCounter.count} placed instances`);
      }
      if (activeDefinitions.has(refIdx)) {
        throw new SkpParseError('Recursive component definition', {
          stage: 'build_scene',
          definitionId: refIdx,
        });
      }
      activeDefinitions.add(refIdx);
      const childNodes = instantiate(refIdx, newMatrix, lName, fullPathName, instMaterial);
      activeDefinitions.delete(refIdx);

      const tx = (newMatrix[9] ?? 0) * 25.4;
      const ty = (newMatrix[10] ?? 0) * 25.4;
      const tz = (newMatrix[11] ?? 0) * 25.4;

      const instInfo: InstanceNode = {
        name: inst.name || '',
        definitionName: defsDict.get(refIdx)?.name || '',
        layer: lName,
        positionMm: [
          Math.round(tx * 100) / 100,
          Math.round(ty * 100) / 100,
          Math.round(tz * 100) / 100,
        ],
        properties: properties,
        children: childNodes,
      };
      childInstancesInfo.push(instInfo);

      let safeChildPath = fullPathName.replace(/ \/ /g, '__').replace(/ /g, '_');
      if (safeChildPath.length > 80) safeChildPath = safeChildPath.slice(0, 80);

      for (const geomName of Object.keys(meshIndex)) {
        if (geomName.includes(safeChildPath)) {
          const existing = meshIndex[geomName];
          if (existing) {
            existing.properties = properties;
            existing.name = inst.name || '';
          }
        }
      }
    }

    return childInstancesInfo;
  }

  const identityMat = [1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1.0];
  const rootChildren = instantiate('ROOT', identityMat);

  // Fill in missing root meshes
  for (const geomName of Object.keys(meshIndex)) {
    const existing = meshIndex[geomName];
    if (existing && existing.path === 'ROOT') {
      existing.name = 'ROOT';
      existing.definitionName = 'ROOT_MODEL';
      existing.layer = 'Layer0';
      existing.positionMm = [0, 0, 0];
      existing.properties = {};
    }
  }

  const sceneHierarchy: InstanceNode = {
    name: 'ROOT',
    definitionName: 'ROOT_MODEL',
    layer: 'Layer0',
    positionMm: [0, 0, 0],
    properties: {},
    children: rootChildren,
  };

  emitLog(
    options,
    'info',
    `Scene build complete: ${instanceCounter.count} instances, ${Object.keys(meshIndex).length} meshes, ` +
      `${glbPrimitives.length} primitives (${((Date.now() - t0) / 1000).toFixed(2)}s)`
  );

  return { sceneHierarchy, meshIndex, glbPrimitives, gltfMaterials, textures };
}

export function resolveMaterialFromMaps(
  matId: number | null | undefined,
  materialIdToName: Map<number, string>,
  materialsMap: Map<string, Material>,
  materialsByFolder: Map<string, Material>
): Material | undefined {
  if (matId === undefined || matId === null) return undefined;
  const matName = materialIdToName.get(matId);
  if (!matName) return undefined;
  return materialsMap.get(matName) || materialsByFolder.get(matName);
}
