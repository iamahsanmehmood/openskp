import { multiplyMatrices } from './transforms';
import { extractDynamicProperties } from './geometry';
import { ParseOptions, PROGRESS_INTERVAL, emitLog, emitProgress } from './observability';
import { SkpParseError } from './errors';
import { buildLocalFaceGroups } from './face-groups';
import {
  Material,
  Texture,
  SceneTexture,
  ParsedRawData,
  sniffImageMime,
  resolveMaterialFromMaps,
} from './model';

/**
 * One reusable, DEFINITION-LOCAL triangulated mesh: the instanced
 * counterpart of {@link GlbPrimitive}, minus the world transform.
 *
 * Positions and normals stay in the definition's own local frame, so N
 * placements of the same definition share this one buffer set instead of
 * getting N transformed copies of it.
 *
 * Coordinates here are already converted to glTF conventions - metres,
 * Y-up - exactly like {@link GlbPrimitive}, so a consumer applies
 * {@link InstancedNode.matrix} (also glTF-space) and nothing else. The
 * SketchUp-space (inches, Z-up) values are never exposed on this type.
 */
export interface LocalPrimitive {
  /** Flat [x, y, z, ...] positions in DEFINITION-LOCAL space, metres, Y-up. */
  positions: Float32Array;
  /** Flat [x, y, z, ...] local-space vertex normals, matching `positions` 1:1.
   *
   * Local, i.e. NOT transformed by any instance matrix: normal
   * transformation is deferred to the consumer/renderer, which derives it
   * from the node transform the same way glTF requires (inverse-transpose
   * of the upper-left 3x3). That is what keeps non-uniform and mirrored
   * scales correct without baking a per-instance copy of the buffer. */
  normals: Float32Array;
  /** Flat [u, v, ...] texture coordinates, matching `positions` 1:1.
   * Identical to the baked path's, since UVs are computed in local space
   * and an instance transform never changes them. */
  uvs: Float32Array;
  /** Triangle vertex indices (3 per triangle). */
  indices: Uint32Array;
  /** Index into {@link InstancedScene.gltfMaterials}. */
  materialIndex: number;
}

/**
 * A definition's geometry, resolved for one specific rendering context and
 * ready to be referenced by any number of {@link InstancedNode}s.
 *
 * One SketchUp definition can yield MORE than one resource: the same
 * component painted with two different materials, or placed on two layers
 * with different fallback colours, renders differently and therefore needs
 * a separate variant. See {@link InstancedMeshResource.variantKey}.
 */
export interface InstancedMeshResource {
  /** Stable, deterministic id (`mesh_<n>`), assigned in first-encounter
   * order of the scene walk. Referenced by {@link InstancedNode.meshResourceId}. */
  id: string;
  /** The source definition's key in the parsed model (`'ROOT'` for
   * top-level loose geometry). */
  definitionId: number | string;
  definitionName: string;
  /** The rendering context that produced this variant - the effective
   * inherited material and layer fallback colour. Two placements sharing
   * this key share the resource; two that differ get separate variants.
   * Exposed for debugging and for callers that want to reason about why a
   * definition produced more than one resource. */
  variantKey: string;
  /** One entry per resolved material within the definition, mirroring the
   * baked path's per-(definition, colour) primitive split. */
  primitives: LocalPrimitive[];
}

/**
 * One placed node in the instanced scene graph.
 *
 * Carries the transform that places its {@link meshResourceId} (and its
 * whole subtree) into the scene, instead of that transform having been
 * baked into vertex data.
 */
export interface InstancedNode {
  /** The instance's own name, `''` when unnamed (`'ROOT'` for the root). */
  name: string;
  definitionName: string;
  /** Effective layer, with SketchUp's inheritance already resolved. */
  layer: string;
  /**
   * This node's transform RELATIVE TO ITS PARENT, as a 16-element
   * column-major glTF matrix (metres, Y-up) - directly usable as a glTF
   * node `matrix`, or as THREE.Matrix4.fromArray().
   *
   * Relative, not absolute: a consumer composes the chain by walking the
   * tree, exactly as glTF and every scene graph already do. The root node's
   * matrix is the identity.
   *
   * This is the ONLY place an instance's placement lives - the geometry it
   * points at stays in definition-local space.
   */
  matrix: number[];
  /** Absolute world position in millimetres, SketchUp axes (Z-up), rounded
   * to 2 decimals - the same value, in the same frame, that the baked
   * path's `InstanceNode.positionMm` reports, so metadata comparisons
   * between the two APIs line up. */
  positionMm: [number, number, number];
  /** Dynamic Component attributes attached to this instance, or `{}`. */
  properties: Record<string, string>;
  /** The mesh resource this node renders, or undefined for a node that
   * only groups children. */
  meshResourceId?: string;
  children: InstancedNode[];
}

/**
 * The result of {@link buildInstancedScene}: the placed scene graph with
 * SketchUp's instancing PRESERVED rather than baked out.
 *
 * Where {@link SkpScene} emits one world-space vertex buffer per placement,
 * this emits each distinct definition+context once ({@link meshResources})
 * and refers to it from every placement ({@link sceneHierarchy}). Scene
 * size therefore scales with *unique geometry + instance transforms*
 * instead of *definition geometry x placement count*.
 *
 * This is lossless: no decimation, quantisation or geometry approximation
 * of any kind. The triangles are the same triangles the baked path
 * produces, just stored once and referenced N times.
 */
export interface InstancedScene {
  /** Root of the placed instance tree (identity transform). */
  sceneHierarchy: InstancedNode;
  /** Every distinct (definition, rendering-context) mesh actually placed,
   * in deterministic first-encounter order. */
  meshResources: InstancedMeshResource[];
  /** glTF PBR materials referenced by {@link LocalPrimitive.materialIndex}.
   * Same shape and construction as {@link SkpScene.gltfMaterials}. */
  gltfMaterials: unknown[];
  /** Distinct texture images, deduplicated by source bytes - same as
   * {@link SkpScene.textures}. */
  textures: SceneTexture[];
}

/**
 * Build an instanced scene from already-parsed raw data.
 *
 * Walks the same placed scene graph as {@link buildSceneFromParsed} and
 * resolves layers, instance materials and dynamic properties identically -
 * but emits each definition's triangulated geometry ONCE per distinct
 * rendering context, with the placement kept on the node.
 */
export function buildInstancedSceneFromParsed(
  parsed: ParsedRawData,
  options?: ParseOptions
): InstancedScene {
  const t0 = Date.now();
  const { layerColors, layerIdToName, materialIdToName, materialsMap, materialsByFolder, defsDict } =
    parsed;

  emitLog(options, 'info', `Building instanced scene: ${defsDict.size} definitions available`);
  const instanceCounter = { count: 0 };

  const getLayerColor = (name: string) => {
    const c = layerColors.get(name) || [136, 136, 136];
    return { r: c[0], g: c[1], b: c[2] };
  };

  // Textures deduplicated by bytes, exactly as the baked path does.
  const textures: SceneTexture[] = [];
  const textureIndexByKey = new Map<string, number>();

  function textureIndexFor(tex: Texture | null | undefined): number | null {
    if (!tex || !tex.data || tex.data.length === 0) return null;
    const mimeType = sniffImageMime(tex.data);
    if (mimeType === null) return null; // a format glTF cannot carry
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

  function getMaterialIndex(
    color: { r: number; g: number; b: number },
    doubleSided: boolean,
    textureIndex: number | null
  ) {
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
    if (textureIndex !== null) {
      pbr.baseColorTexture = { index: textureIndex };
    }
    const material: any = { pbrMetallicRoughness: pbr };
    if (doubleSided) material.doubleSided = true;
    gltfMaterials.push(material);
    colorToMaterialIndex.set(key, idx);
    return idx;
  }

  const meshResources: InstancedMeshResource[] = [];
  const resourceIdByKey = new Map<string, string>();

  /**
   * Identity of a mesh resource. Caching on the definition id ALONE would
   * be wrong: the same definition renders differently depending on the
   * context it is placed in, and merging those would silently repaint
   * geometry. Everything that can change the rendered result is in the key:
   *
   * - the definition itself;
   * - the effective inherited (instance-painted) material, which decides
   *   both the colour of unpainted faces and, via its texture's tile size,
   *   their UVs - keyed by material name plus texture identity, since two
   *   different images can average to the same RGB;
   * - the effective layer, whose colour is the fallback for a face with no
   *   material anywhere in the chain.
   *
   * Front/back material resolution, per-face textures, UV mapping and
   * double-sided-vs-split geometry all derive deterministically from the
   * definition's own face data plus these two inputs, so they need no
   * separate key component - the same (definition, material, layer) always
   * produces byte-identical buffers.
   */
  function resourceVariantKey(
    defId: number | string,
    inheritedMaterial: Material | undefined,
    layer: string
  ): string {
    const tex = inheritedMaterial?.texture;
    const texKey = tex && tex.data && tex.data.length > 0
      ? `${tex.data.length}:${Array.from(tex.data.subarray(0, 16)).join(',')}:${tex.width}x${tex.height}`
      : '-';
    const matKey = inheritedMaterial
      ? `${inheritedMaterial.name}|${inheritedMaterial.color.r},${inheritedMaterial.color.g},${inheritedMaterial.color.b}|${texKey}`
      : '-';
    // The layer only matters through its fallback colour, so key on that
    // rather than the name: two layers that happen to share a colour
    // legitimately share geometry.
    const lc = getLayerColor(layer);
    return `${String(defId)}|${matKey}|${lc.r},${lc.g},${lc.b}`;
  }

  /** Build (or reuse) the local-space mesh resource for a definition
   * rendered in the given context. Returns undefined when the definition
   * has no face geometry of its own. */
  function meshResourceFor(
    defId: number | string,
    inheritedMaterial: Material | undefined,
    layer: string
  ): string | undefined {
    const d = defsDict.get(defId);
    if (!d || d.builder.faces.size === 0) return undefined;

    const key = resourceVariantKey(defId, inheritedMaterial, layer);
    const hit = resourceIdByKey.get(key);
    if (hit !== undefined) return hit;

    const faceGroups = buildLocalFaceGroups(d.builder, {
      resolveMaterial: (matId) =>
        resolveMaterialFromMaps(matId, materialIdToName, materialsMap, materialsByFolder),
      textureIndexFor,
      inheritedMaterial,
      fallbackLayerColor: getLayerColor(layer),
      definitionId: defId,
    });

    const primitives: LocalPrimitive[] = [];
    const scale = 0.0254;

    for (const group of faceGroups.values()) {
      if (group.localFaces.length === 0) continue;

      const positions = new Float32Array(group.localVerts.length * 3);
      const normals = new Float32Array(group.localVerts.length * 3);
      const uvs = new Float32Array(group.localVerts.length * 2);

      for (let i = 0; i < group.localVerts.length; i++) {
        const v = group.localVerts[i];
        // Local space, so no instance matrix is applied - only the
        // inches->metres scale and SketchUp Z-up -> glTF Y-up axis swap,
        // which are the same fixed conventions the baked path applies.
        positions[i * 3] = v[0] * scale;
        positions[i * 3 + 1] = v[2] * scale;
        positions[i * 3 + 2] = -v[1] * scale;

        uvs[i * 2] = group.localUvs[i][0];
        uvs[i * 2 + 1] = group.localUvs[i][1];

        const rawNorm = group.normalsAccum[i];
        const normLen = Math.sqrt(rawNorm[0] ** 2 + rawNorm[1] ** 2 + rawNorm[2] ** 2);
        const n = normLen > 1e-6
          ? [rawNorm[0] / normLen, rawNorm[1] / normLen, rawNorm[2] / normLen]
          : [0, 0, 1];
        // Same axis swap as positions. No instance-matrix normal transform
        // here: that belongs to the node, and deferring it is precisely
        // what keeps mirrored/non-uniform scales correct per placement.
        normals[i * 3] = n[0];
        normals[i * 3 + 1] = n[2];
        normals[i * 3 + 2] = -n[1];
      }

      const indices = new Uint32Array(group.localFaces.length * 3);
      for (let i = 0; i < group.localFaces.length; i++) {
        indices[i * 3] = group.localFaces[i][0];
        indices[i * 3 + 1] = group.localFaces[i][1];
        indices[i * 3 + 2] = group.localFaces[i][2];
      }

      primitives.push({
        positions,
        normals,
        uvs,
        indices,
        materialIndex: getMaterialIndex(group.color, group.doubleSided, group.textureIndex),
      });
    }

    if (primitives.length === 0) return undefined;

    const id = `mesh_${meshResources.length}`;
    meshResources.push({
      id,
      definitionId: defId,
      definitionName: d.name || '',
      variantKey: key,
      primitives,
    });
    resourceIdByKey.set(key, id);
    return id;
  }

  // Definitions on the ACTIVE recursion path, guarding self-instancing -
  // same rule as the baked path.
  const activeDefinitions = new Set<number | string>();

  /**
   * Convert one instance's 13-element SketchUp matrix (inches, Z-up) into a
   * 16-element column-major glTF matrix (metres, Y-up).
   *
   * The axis change is the similarity transform C * M * C^-1 with
   * C: (x, y, z) -> (x, z, -y), so it composes correctly through nesting:
   * converting each level and multiplying gives the same result as
   * converting the fully-composed SketchUp matrix. Translation is scaled to
   * metres; the rotation/scale block is unitless and is not.
   */
  function toGltfMatrix(m: number[]): number[] {
    const scale = 0.0254;
    // SketchUp basis (row-major 3x3 in elements 0..8, translation in 9..11).
    const a = m[0], b = m[1], c = m[2];
    const d = m[3], e = m[4], f = m[5];
    const g = m[6], h = m[7], i = m[8];
    const tx = m[9] ?? 0, ty = m[10] ?? 0, tz = m[11] ?? 0;

    // C * M * C^-1 where C maps (x,y,z) -> (x,z,-y).
    const r00 = a,  r01 = c,   r02 = -b;
    const r10 = g,  r11 = i,   r12 = -h;
    const r20 = -d, r21 = -f,  r22 = e;

    // glTF wants column-major: [col0(3), 0, col1(3), 0, col2(3), 0, t(3), 1]
    return [
      r00, r10, r20, 0,
      r01, r11, r21, 0,
      r02, r12, r22, 0,
      tx * scale, tz * scale, -ty * scale, 1,
    ];
  }

  const IDENTITY_GLTF = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1];

  /**
   * Walk a definition's placed instances, emitting one node each.
   *
   * `currentMatrix` is the accumulated SketchUp-space matrix and is used
   * ONLY to report each node's absolute `positionMm` (matching the baked
   * path's metadata); the geometry itself never sees it.
   */
  function walk(
    defId: number | string,
    currentMatrix: number[],
    parentLayer: string,
    inheritedMaterial: Material | undefined
  ): InstancedNode[] {
    const d = defsDict.get(defId);
    if (!d) return [];

    const nodes: InstancedNode[] = [];

    for (const inst of d.builder.instances) {
      const refIdx = inst.refIdx;
      const newMatrix = multiplyMatrices(currentMatrix, inst.matrix);

      let lName = parentLayer;
      let instMaterial = inheritedMaterial;
      let properties: Record<string, string> = { ...(inst.properties || {}) };

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

      // D007/DC05 TLV walk (VFF only); a no-op for legacy instances, whose
      // precomputed `properties` seeded above survives unchanged.
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
      const children = walk(refIdx, newMatrix, lName, instMaterial);
      activeDefinitions.delete(refIdx);

      const tx = (newMatrix[9] ?? 0) * 25.4;
      const ty = (newMatrix[10] ?? 0) * 25.4;
      const tz = (newMatrix[11] ?? 0) * 25.4;

      nodes.push({
        name: inst.name || '',
        definitionName: defsDict.get(refIdx)?.name || '',
        layer: lName,
        matrix: toGltfMatrix(inst.matrix),
        positionMm: [
          Math.round(tx * 100) / 100,
          Math.round(ty * 100) / 100,
          Math.round(tz * 100) / 100,
        ],
        properties,
        meshResourceId: meshResourceFor(refIdx, instMaterial, lName),
        children,
      });
    }

    return nodes;
  }

  const identityMat = [1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1.0];
  const rootChildren = walk('ROOT', identityMat, 'Layer0', undefined);

  // Loose geometry drawn straight into the model (not inside any
  // component/group) is kept, as the baked path keeps it: it becomes the
  // root node's own mesh resource.
  const rootMeshResourceId = meshResourceFor('ROOT', undefined, 'Layer0');

  const sceneHierarchy: InstancedNode = {
    name: 'ROOT',
    definitionName: 'ROOT_MODEL',
    layer: 'Layer0',
    matrix: [...IDENTITY_GLTF],
    positionMm: [0, 0, 0],
    properties: {},
    meshResourceId: rootMeshResourceId,
    children: rootChildren,
  };

  emitLog(
    options,
    'info',
    `Instanced scene build complete: ${instanceCounter.count} instances, ` +
      `${meshResources.length} mesh resources (${((Date.now() - t0) / 1000).toFixed(2)}s)`
  );

  return { sceneHierarchy, meshResources, gltfMaterials, textures };
}
