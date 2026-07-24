import { transformPoint, multiplyMatrices } from './transforms';
import { triangulateFace3D } from './triangulator';
import { reconstructLoopVertices, extractDynamicProperties, ParsedDefinition } from './geometry';
import { SkpParseError } from './errors';
import { ParseOptions, PROGRESS_INTERVAL, emitLog, emitProgress } from './observability';

export interface SkpModel {
  version: string;
  definitions: Map<number, Definition>;
  layers: Layer[];
  materials: Material[];
  materialsById: Map<number, Material>;
  styles: Style[];
}

export interface Definition {
  id: number;
  guid: string;
  name: string;
  vertices: Vertex[];
  edges: Edge[];
  faces: Face[];
  instances: Instance[];
  isImage: boolean;
  alwaysFacesCamera: boolean;
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
}

export interface Layer {
  name: string;
  color: { r: number; g: number; b: number };
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
  color: { r: number; g: number; b: number };
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

/** One triangulated, world-space mesh: all faces sharing a single resolved
 * color from one flattened scene-graph position. Ready to hand straight to
 * a GLB/glTF exporter or any other renderer. */
export interface GlbPrimitive {
  /** Flat [x, y, z, x, y, z, ...] vertex positions, in metres, Y-up. */
  positions: Float32Array;
  /** Flat [x, y, z, ...] vertex normals, matching `positions` 1:1. */
  normals: Float32Array;
  /** Triangle vertex indices into `positions`/`normals` (3 per triangle). */
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
  /** glTF PBR material definitions referenced by `GlbPrimitive.materialIndex`. */
  gltfMaterials: unknown[];
}

/** Raw parsed data, source-agnostic (populated by either the VFF/ZIP path
 * in index.ts or the legacy MFC walker in legacy.ts), that
 * {@link buildModelFromParsed} turns into the final public
 * {@link SkpModel} - including scene-hierarchy resolution and GLB
 * primitive building, which both formats share. */
export interface ParsedRawData {
  version: string;
  layerColors: Map<string, [number, number, number]>;
  layerIdToName: Map<number, string>;
  materialIdToName: Map<number, string>;
  materialsMap: Map<string, Material>;
  materialsByFolder: Map<string, Material>;
  styles: Style[];
  defsDict: Map<number | string, ParsedDefinition>;
}

export function buildModelFromParsed(parsed: ParsedRawData): SkpModel {
  const { version, layerColors, materialIdToName, materialsMap, materialsByFolder, styles, defsDict } = parsed;

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
  }));

  const finalMaterialsList: Material[] = Array.from(materialsMap.values());

  const finalDefinitions = new Map<number, Definition>();
  for (const [id, d] of defsDict.entries()) {
    if (typeof id === 'number') {
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
      }));
      const instances: Instance[] = d.builder.instances.map((inst) => ({
        name: inst.name,
        refIdx: inst.refIdx,
        guid: inst.refGuid,
        matrix: inst.matrix,
        materialId: inst.materialId,
      }));

      finalDefinitions.set(id, {
        id,
        guid: d.guid,
        name: d.name,
        vertices,
        edges,
        faces,
        instances,
        isImage: d.isImage,
        alwaysFacesCamera: d.alwaysFacesCamera,
      });
    }
  }

  return {
    version,
    definitions: finalDefinitions,
    layers: finalLayersList,
    materials: finalMaterialsList,
    materialsById,
    styles,
  };
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

  const colorToMaterialIndex = new Map<string, number>();
  const gltfMaterials: any[] = [];

  function getMaterialIndex(color: { r: number; g: number; b: number }) {
    const key = `${color.r},${color.g},${color.b}`;
    if (colorToMaterialIndex.has(key)) {
      return colorToMaterialIndex.get(key)!;
    }
    const idx = gltfMaterials.length;
    gltfMaterials.push({
      pbrMetallicRoughness: {
        baseColorFactor: [color.r / 255, color.g / 255, color.b / 255, 1.0],
        metallicFactor: 0.0,
        roughnessFactor: 0.8,
      },
    });
    colorToMaterialIndex.set(key, idx);
    return idx;
  }

  function instantiate(
    defId: number | string,
    currentMatrix: number[],
    parentLayer: string = 'Layer0',
    pathName: string = 'ROOT',
    inheritedMaterialColor?: { r: number; g: number; b: number }
  ): InstanceNode[] {
    const d = defsDict.get(defId);
    if (!d) return [];

    const builder = d.builder;

    if (builder.faces.size > 0) {
      const faceGroups = new Map<string, {
        color: { r: number; g: number; b: number };
        localVerts: [number, number, number][];
        localFaces: number[][];
        localVMap: Map<number, number>;
        faceList: { fId: number; fData: any; localFacesStart: number; localFacesEnd: number }[];
      }>();

      for (const [fId, fData] of builder.faces.entries()) {
        let faceColor = inheritedMaterialColor;
        const faceMatId = (fData as any).materialId;
        if (faceMatId !== undefined && faceMatId !== null) {
          const matName = materialIdToName.get(faceMatId);
          if (matName) {
            const mat = materialsMap.get(matName) || materialsByFolder.get(matName);
            if (mat) {
              faceColor = mat.color;
            }
          }
        }
        if (!faceColor) {
          faceColor = getLayerColor(parentLayer);
        }

        const colorKey = `${faceColor.r},${faceColor.g},${faceColor.b}`;
        let group = faceGroups.get(colorKey);
        if (!group) {
          group = {
            color: faceColor,
            localVerts: [],
            localFaces: [],
            localVMap: new Map<number, number>(),
            faceList: [],
          };
          faceGroups.set(colorKey, group);
        }

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
        const startFaceIdx = group.localFaces.length;
        for (const tri of triangles) {
          const faceIndices: number[] = [];
          for (const vId of tri) {
            if (builder.vertices.has(vId)) {
              let idx = group.localVMap.get(vId);
              if (idx === undefined) {
                const pt = builder.vertices.get(vId)!;
                group.localVerts.push(pt);
                idx = group.localVerts.length - 1;
                group.localVMap.set(vId, idx);
              }
              faceIndices.push(idx);
            }
          }
          if (faceIndices.length === 3) {
            group.localFaces.push(faceIndices);
          }
        }
        const endFaceIdx = group.localFaces.length;
        group.faceList.push({ fId, fData, localFacesStart: startFaceIdx, localFacesEnd: endFaceIdx });
      }

      for (const [colorKey, group] of faceGroups.entries()) {
        if (group.localFaces.length === 0) continue;

        const isRoot = pathName === 'ROOT';
        const tx = isRoot ? 0 : (currentMatrix[9] ?? 0) * 25.4;
        const ty = isRoot ? 0 : (currentMatrix[10] ?? 0) * 25.4;
        const tz = isRoot ? 0 : (currentMatrix[11] ?? 0) * 25.4;

        let safePath = pathName.replace(/ \/ /g, '__').replace(/ /g, '_');
        if (safePath.length > 80) safePath = safePath.slice(0, 80);

        const colorSuffix = faceGroups.size > 1 ? `_${colorKey.replace(/,/g, '_')}` : '';
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

        const vertexNormalsAccum = new Array(group.localVerts.length).fill(null).map(() => [0, 0, 0]);
        for (const faceItem of group.faceList) {
          const loops: number[][] = [];
          for (const loop of faceItem.fData.loops) {
            const loopVerts = reconstructLoopVertices(loop, builder.edges);
            if (loopVerts.length > 0) {
              loops.push(loopVerts);
            }
          }
          if (loops.length === 0) continue;

          const fn = faceItem.fData.normal;
          for (const loop of loops) {
            for (const vId of loop) {
              const idx = group.localVMap.get(vId);
              if (idx !== undefined) {
                vertexNormalsAccum[idx][0] += fn[0];
                vertexNormalsAccum[idx][1] += fn[1];
                vertexNormalsAccum[idx][2] += fn[2];
              }
            }
          }
        }

        for (let i = 0; i < group.localVerts.length; i++) {
          const v = group.localVerts[i];
          const pt = transformPoint(currentMatrix, v);
          positions[i * 3] = pt[0] * scale;
          positions[i * 3 + 1] = pt[2] * scale;
          positions[i * 3 + 2] = -pt[1] * scale;

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

        const materialIndex = getMaterialIndex(group.color);

        glbPrimitives.push({
          positions,
          normals,
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
      let instColor = inheritedMaterialColor;
      let properties: Record<string, string> = {};

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
            instColor = mat.color;
          }
        }
      }

      // Dynamic properties are TLV-specific (no legacy equivalent decoded
      // yet); inst.children is empty for legacy instances, so this is a
      // no-op there.
      const d007 = inst.children.find((c) => c.tag === 'D007');
      if (d007) {
        try {
          properties = extractDynamicProperties(d007);
        } catch (e) {
          // Ignore
        }
      }

      const instName = inst.name || `Component_${refIdx}`;
      const fullPathName = `${pathName} / ${instName}`;
      instanceCounter.count++;
      if (instanceCounter.count % PROGRESS_INTERVAL === 0) {
        emitProgress(options, 'build_scene', instanceCounter.count, instanceCounter.count);
        emitLog(options, 'debug', `Processed ${instanceCounter.count} placed instances`);
      }
      const childNodes = instantiate(refIdx, newMatrix, lName, fullPathName, instColor);

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

  return { sceneHierarchy, meshIndex, glbPrimitives, gltfMaterials };
}
