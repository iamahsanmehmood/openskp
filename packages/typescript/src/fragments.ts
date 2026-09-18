import * as flatbuffers from 'flatbuffers';
import * as fflate from 'fflate';
import type { InstancedScene, InstancedNode, LocalPrimitive, InstancedMeshResource } from './instanced';
import { multiplyMatrices } from './transforms';
import {
  Model,
  Meshes,
  Shell,
  ShellProfile,
  BigShellProfile,
  FloatVector,
  DoubleVector,
  Representation,
  RepresentationClass,
  Transform,
  Material,
  Sample,
  SpatialStructure,
  ShellType,
  RenderedFaces,
  Stroke,
  Attribute,
} from './_fragments_fb/index';

/** Options for {@link toFragments}. */
export interface FragmentExportOptions {
  /** Model GUID / identifier. Default: "00000000-0000-0000-0000-000000000000". */
  modelId?: string;
  /** If true, returns raw uncompressed FlatBuffers buffer instead of zlib-deflated. Default: false. */
  raw?: boolean;
  /** Whether to set materials to double-sided. Default: false. */
  doubleSided?: boolean;
}

const USHORT_MAX = 65535;
const SCALE_ROUND_NDIGITS = 4;

const IDENTITY_MATRIX = [
  1.0, 0.0, 0.0, 0.0,
  0.0, 1.0, 0.0, 0.0,
  0.0, 0.0, 1.0, 0.0,
  0.0, 0.0, 0.0, 1.0,
];

interface Leaf {
  node: InstancedNode;
  world: number[];
}

/**
 * Walk the instanced scene's tree, accumulating each node's GLOBAL (world)
 * transform, and return every node worth tracking as its own item - a leaf
 * carrying geometry or a named organizational wrapper with no geometry.
 * Mirrors Python's and C++'s collect_leaves exactly, using the real
 * nameIsGenerated flag (InstancedNode now carries it - see instanced.ts) -
 * not an approximation of "has some non-empty name", which over-tracks
 * every node that only ever got a synthetic "Component_N"/definition-name
 * fallback, not a real one.
 */
function collectLeaves(
  node: InstancedNode,
  root: InstancedNode,
  parentMatrix: number[],
  out: Leaf[]
): void {
  const world = multiplyMatrices(parentMatrix, node.matrix);
  const isNamedWrapper = node !== root && !node.nameIsGenerated;
  if (node.meshResourceId !== undefined || isNamedWrapper) {
    out.push({ node, world });
  }
  if (node.children) {
    for (const child of node.children) {
      collectLeaves(child, root, world, out);
    }
  }
}

export interface TrsDecomposition {
  position: [number, number, number];
  xDir: [number, number, number];
  yDir: [number, number, number];
  scale: [number, number, number];
  mirrored: boolean;
}

/**
 * Decompose a column-major 4x4 instance transform into
 * (position, x_direction, y_direction, scale, mirrored).
 *
 * x_direction/y_direction are normalized unit vectors - everything Fragments'
 * Transform struct can hold. Scale (all positive magnitudes) and mirrored
 * (whether the frame is left-handed) cannot be held in Transform; they are
 * baked into geometry variants via bakePrimitive.
 */
export function decomposeTrs(m: number[]): TrsDecomposition {
  const xAxis: [number, number, number] = [m[0], m[1], m[2]];
  const yAxis: [number, number, number] = [m[4], m[5], m[6]];
  const zAxis: [number, number, number] = [m[8], m[9], m[10]];
  const pos: [number, number, number] = [m[12], m[13], m[14]];

  const norm = (v: [number, number, number]) => {
    const s = Math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2]);
    return s > 0 ? s : 1.0;
  };

  const sx = norm(xAxis);
  const sy = norm(yAxis);
  const sz = norm(zAxis);

  const det =
    xAxis[0] * (yAxis[1] * zAxis[2] - yAxis[2] * zAxis[1]) -
    xAxis[1] * (yAxis[0] * zAxis[2] - yAxis[2] * zAxis[0]) +
    xAxis[2] * (yAxis[0] * zAxis[1] - yAxis[1] * zAxis[0]);
  const mirrored = det < 0;

  const cleanZero = (v: number) => (v === 0 ? 0 : v);
  let xDir: [number, number, number] = [
    cleanZero(xAxis[0] / sx),
    cleanZero(xAxis[1] / sx),
    cleanZero(xAxis[2] / sx),
  ];
  if (mirrored) {
    xDir = [cleanZero(-xDir[0]), cleanZero(-xDir[1]), cleanZero(-xDir[2])];
  }
  const yDir: [number, number, number] = [
    cleanZero(yAxis[0] / sy),
    cleanZero(yAxis[1] / sy),
    cleanZero(yAxis[2] / sy),
  ];

  return {
    position: pos,
    xDir,
    yDir,
    scale: [sx, sy, sz],
    mirrored,
  };
}

export interface BakedGeometry {
  points: [number, number, number][];
  triangles: [number, number, number][];
}

/**
 * Apply an instance's scale/mirror directly to a copy of its resource's
 * LOCAL points and triangle winding, so the resulting geometry is correct
 * when placed by a purely rigid Transform.
 */
export function bakePrimitive(
  prim: LocalPrimitive,
  scale: [number, number, number],
  mirrored: boolean
): BakedGeometry {
  const sx = mirrored ? -scale[0] : scale[0];
  const sy = scale[1];
  const sz = scale[2];

  const positions = prim.positions;
  const nVerts = Math.floor(positions.length / 3);
  const points: [number, number, number][] = new Array(nVerts);
  for (let i = 0; i < nVerts; i++) {
    points[i] = [
      positions[i * 3] * sx,
      positions[i * 3 + 1] * sy,
      positions[i * 3 + 2] * sz,
    ];
  }

  const indices = prim.indices;
  const nTris = Math.floor(indices.length / 3);
  const triangles: [number, number, number][] = new Array(nTris);
  for (let i = 0; i < nTris; i++) {
    const a = indices[i * 3];
    const b = indices[i * 3 + 1];
    const c = indices[i * 3 + 2];
    triangles[i] = mirrored ? [a, c, b] : [a, b, c];
  }

  return { points, triangles };
}

/**
 * Round a (mirrored, scale) pair to a stable cache key. The mirror flag
 * folds into the X component's sign, since bakePrimitive only ever negates
 * X for a mirrored instance.
 */
export function scaleCacheKey(mirrored: boolean, scale: [number, number, number]): string {
  const mult = Math.pow(10, SCALE_ROUND_NDIGITS);
  const rnd = (v: number) => Math.round(v * mult) / mult;
  const sx = mirrored ? -scale[0] : scale[0];
  return `${rnd(sx)}_${rnd(scale[1])}_${rnd(scale[2])}`;
}

/**
 * Export an {@link InstancedScene} (from {@link buildInstancedScene}) directly to
 * ThatOpen Fragments (.frag) binary format.
 *
 * Uses official FlatBuffers bindings generated from ThatOpen's index.fbs schema,
 * with TRS matrix decomposition and scale/mirror geometry baking for full visual parity.
 *
 * @param scene - The instanced scene from buildInstancedScene()
 * @param options - Fragment export options
 * @returns Binary .frag file as Uint8Array (zlib deflated or raw FlatBuffers)
 */
export function toFragments(
  scene: InstancedScene,
  options: FragmentExportOptions = {}
): Uint8Array {
  const leaves: Leaf[] = [];
  collectLeaves(scene.sceneHierarchy, scene.sceneHierarchy, IDENTITY_MATRIX, leaves);

  const resourceById = new Map<string, InstancedMeshResource>();
  for (const r of scene.meshResources) {
    resourceById.set(r.id, r);
  }

  const builder = new flatbuffers.Builder(1024 * 64);

  // Shells, representations, materials built lazily as leaves are walked
  const shellKeyToIndex = new Map<string, number[]>();
  const shellOffsets: flatbuffers.Offset[] = [];
  const representationBounds: {
    min: [number, number, number];
    max: [number, number, number];
  }[] = [];
  const materialKeyToIndex = new Map<number, number>();
  const materialRgba: [number, number, number, number][] = [];

  const getMaterialIndex = (matIdx: number): number => {
    if (materialKeyToIndex.has(matIdx)) {
      return materialKeyToIndex.get(matIdx)!;
    }
    let r = 255, g = 255, b = 255, a = 255;
    if (matIdx >= 0 && matIdx < scene.gltfMaterials.length) {
      const gltfMat = scene.gltfMaterials[matIdx] as any;
      const pbr = gltfMat?.pbrMetallicRoughness;
      if (pbr?.baseColorFactor && Array.isArray(pbr.baseColorFactor)) {
        const c = pbr.baseColorFactor;
        r = Math.min(255, Math.max(0, Math.round((c[0] ?? 1.0) * 255)));
        g = Math.min(255, Math.max(0, Math.round((c[1] ?? 1.0) * 255)));
        b = Math.min(255, Math.max(0, Math.round((c[2] ?? 1.0) * 255)));
        a = Math.min(255, Math.max(0, Math.round((c[3] ?? 1.0) * 255)));
      }
    }
    const idx = materialRgba.length;
    materialRgba.push([r, g, b, a]);
    materialKeyToIndex.set(matIdx, idx);
    return idx;
  };

  const bakeOneShell = (
    points: [number, number, number][],
    triangles: [number, number, number][]
  ): number => {
    const isBig = points.length > USHORT_MAX;

    const profileOffsets: flatbuffers.Offset[] = [];
    const bigProfileOffsets: flatbuffers.Offset[] = [];

    for (const tri of triangles) {
      if (isBig) {
        BigShellProfile.startIndicesVector(builder, 3);
        builder.addInt32(tri[2]);
        builder.addInt32(tri[1]);
        builder.addInt32(tri[0]);
        const indicesVec = builder.endVector();
        BigShellProfile.startBigShellProfile(builder);
        BigShellProfile.addIndices(builder, indicesVec);
        bigProfileOffsets.push(BigShellProfile.endBigShellProfile(builder));
      } else {
        ShellProfile.startIndicesVector(builder, 3);
        builder.addInt16(tri[2]);
        builder.addInt16(tri[1]);
        builder.addInt16(tri[0]);
        const indicesVec = builder.endVector();
        ShellProfile.startShellProfile(builder);
        ShellProfile.addIndices(builder, indicesVec);
        profileOffsets.push(ShellProfile.endShellProfile(builder));
      }
    }

    const profilesVec = Shell.createProfilesVector(builder, isBig ? [] : profileOffsets);
    const bigProfilesVec = Shell.createBigProfilesVector(builder, isBig ? bigProfileOffsets : []);

    Shell.startHolesVector(builder, 0);
    const holesVec = builder.endVector();
    Shell.startBigHolesVector(builder, 0);
    const bigHolesVec = builder.endVector();

    Shell.startPointsVector(builder, points.length);
    for (let i = points.length - 1; i >= 0; i--) {
      FloatVector.createFloatVector(builder, points[i][0], points[i][1], points[i][2]);
    }
    const pointsVec = builder.endVector();

    // Sequential per-shell profile ids (0..triangles.length-1, not tied to
    // any upstream SketchUp face identity - see the caller for how a
    // too-large triangle list is chunked before reaching here), so
    // re-numbering from 0 per shell is exactly consistent with the
    // single-shell behavior this is a straight extraction of. Safe to
    // store as Uint16Array unmasked now that triangles.length is always
    // <= USHORT_MAX by construction.
    const faceIds = new Uint16Array(triangles.length);
    for (let i = 0; i < triangles.length; i++) {
      faceIds[i] = i;
    }
    const faceIdsVec = Shell.createProfilesFaceIdsVector(builder, faceIds);

    Shell.startShell(builder);
    Shell.addProfiles(builder, profilesVec);
    Shell.addBigProfiles(builder, bigProfilesVec);
    Shell.addHoles(builder, holesVec);
    Shell.addBigHoles(builder, bigHolesVec);
    Shell.addPoints(builder, pointsVec);
    Shell.addType(builder, isBig ? ShellType.BIG : ShellType.NONE);
    Shell.addProfilesFaceIds(builder, faceIdsVec);
    const shellOff = Shell.endShell(builder);

    const index = shellOffsets.length;
    shellOffsets.push(shellOff);

    let minX = Infinity, minY = Infinity, minZ = Infinity;
    let maxX = -Infinity, maxY = -Infinity, maxZ = -Infinity;
    for (const p of points) {
      if (p[0] < minX) minX = p[0]; if (p[0] > maxX) maxX = p[0];
      if (p[1] < minY) minY = p[1]; if (p[1] > maxY) maxY = p[1];
      if (p[2] < minZ) minZ = p[2]; if (p[2] > maxZ) maxZ = p[2];
    }
    if (minX === Infinity) {
      minX = minY = minZ = 0;
      maxX = maxY = maxZ = 0;
    }
    representationBounds.push({
      min: [minX, minY, minZ],
      max: [maxX, maxY, maxZ],
    });

    return index;
  };

  const getOrBakeShell = (
    resourceId: string,
    primIdx: number,
    prim: LocalPrimitive,
    scale: [number, number, number],
    mirrored: boolean
  ): number[] => {
    const sk = scaleCacheKey(mirrored, scale);
    const key = `${resourceId}:${primIdx}:${sk}`;
    if (shellKeyToIndex.has(key)) {
      return shellKeyToIndex.get(key)!;
    }

    const baked = bakePrimitive(prim, scale, mirrored);

    // `profilesFaceIds` (written above) has no "big"/uint32 counterpart
    // anywhere in the real Fragments schema (index.fbs only declares
    // `profiles_face_ids: [ushort]` - unlike points, which DO get a
    // BigShellProfile/uint32-index escape hatch past 65535 of them). A
    // single shell genuinely cannot represent more than 65535 triangles no
    // matter how points are encoded - see Python's own fix (openskp#285/
    // PR #355) for the real production incident this was ported from.
    // Splitting into multiple shells, each within the ushort limit, is the
    // only way to represent this - the format has no cap on shell COUNT,
    // just per-shell triangle count. Each sub-shell duplicates the full
    // (shared) points array rather than remapping to a local subset:
    // simpler and lower-risk than a vertex-remapping pass, at the cost of
    // some extra file size in this rare oversized-mesh case.
    let indices: number[];
    if (baked.triangles.length > USHORT_MAX) {
      indices = [];
      for (let i = 0; i < baked.triangles.length; i += USHORT_MAX) {
        indices.push(bakeOneShell(baked.points, baked.triangles.slice(i, i + USHORT_MAX)));
      }
    } else {
      indices = [bakeOneShell(baked.points, baked.triangles)];
    }

    shellKeyToIndex.set(key, indices);
    return indices;
  };

  const localIds: number[] = [];
  const categories: string[] = [];
  const names: string[] = [];
  const guids: string[] = [];
  // GUIDs of items whose name is a fallback this project generated (no
  // real name anywhere in the source file), not something a person or
  // plugin actually named - see InstancedNode.nameIsGenerated. Carried in
  // Model.metadata below, same mechanism as layerHidden, since the public
  // Fragments schema has no field for this either.
  const generatedNameGuids: string[] = [];
  const sampleMaterial: number[] = [];
  const sampleRepresentation: number[] = [];
  const meshesItems: number[] = [];
  const globalTransformData: {
    pos: [number, number, number];
    xDir: [number, number, number];
    yDir: [number, number, number];
  }[] = [];
  const itemIndexByNode = new Map<InstancedNode, number>();
  // Real-world SketchUp files can carry a non-unique per-instance GUID:
  // SketchUp's own native Copy/Move+Copy/Array tools carry an instance's
  // attribute dictionaries - and whatever GUID a plugin wrote into one - to
  // every copy verbatim, so several DIFFERENT physical instances can share
  // the exact same non-empty InstancedNode.guid (openskp#290). The first
  // instance to claim a real GUID keeps it; every later instance sharing
  // that same value falls back to a synthetic one instead of silently
  // colliding. Mirrors Python's/C++'s own seenGuids handling exactly.
  const seenGuids = new Set<string>();

  for (let itemIndex = 0; itemIndex < leaves.length; itemIndex++) {
    const { node, world } = leaves[itemIndex];
    const resourceId = node.meshResourceId;
    const res = resourceId !== undefined ? resourceById.get(resourceId) : undefined;

    if (resourceId !== undefined && res === undefined) {
      continue;
    }

    localIds.push(itemIndex);
    categories.push(node.layer || 'Layer0');
    names.push(node.name || '');

    const rawGuid = node.guid || '';
    const itemGuid =
      rawGuid && !seenGuids.has(rawGuid) ? rawGuid : `openskp-${itemIndex}`;
    seenGuids.add(itemGuid);
    guids.push(itemGuid);
    if (node.nameIsGenerated) generatedNameGuids.push(itemGuid);

    itemIndexByNode.set(node, itemIndex);

    if (res !== undefined) {
      const trs = decomposeTrs(world);
      for (let primIdx = 0; primIdx < res.primitives.length; primIdx++) {
        const prim = res.primitives[primIdx];
        const materialIndex = getMaterialIndex(prim.materialIndex);
        // Normally exactly one shell; more than one only when the
        // primitive's own triangle count exceeded what a single shell can
        // represent (see getOrBakeShell) - each extra shell becomes its
        // own additional Sample of the same item/material/transform, the
        // same pattern this loop already uses for multiple primitives of
        // one item.
        for (const shellIndex of getOrBakeShell(resourceId!, primIdx, prim, trs.scale, trs.mirrored)) {
          sampleMaterial.push(materialIndex);
          sampleRepresentation.push(shellIndex);
          meshesItems.push(itemIndex);
          globalTransformData.push({
            pos: trs.position,
            xDir: trs.xDir,
            yDir: trs.yDir,
          });
        }
      }
    }
  }

  const nSamples = sampleMaterial.length;

  Meshes.startShellsVector(builder, shellOffsets.length);
  for (let i = shellOffsets.length - 1; i >= 0; i--) {
    builder.addOffset(shellOffsets[i]);
  }
  const shellsVec = builder.endVector();

  const renderedFaces = (options.doubleSided ?? false)
    ? RenderedFaces.TWO
    : RenderedFaces.ONE;

  Meshes.startMaterialsVector(builder, materialRgba.length);
  for (let i = materialRgba.length - 1; i >= 0; i--) {
    const rgba = materialRgba[i];
    Material.createMaterial(
      builder,
      rgba[0],
      rgba[1],
      rgba[2],
      rgba[3],
      renderedFaces,
      Stroke.DEFAULT
    );
  }
  const materialsVec = builder.endVector();

  Meshes.startRepresentationsVector(builder, representationBounds.length);
  for (let i = representationBounds.length - 1; i >= 0; i--) {
    const { min, max } = representationBounds[i];
    Representation.createRepresentation(
      builder,
      i,
      min[0], min[1], min[2],
      max[0], max[1], max[2],
      RepresentationClass.SHELL
    );
  }
  const representationsVec = builder.endVector();

  Meshes.startSamplesVector(builder, nSamples);
  for (let i = nSamples - 1; i >= 0; i--) {
    Sample.createSample(builder, i, sampleMaterial[i], sampleRepresentation[i], 0);
  }
  const samplesVec = builder.endVector();

  Meshes.startMeshesItemsVector(builder, nSamples);
  for (let i = nSamples - 1; i >= 0; i--) {
    builder.addInt32(meshesItems[i]);
  }
  const meshesItemsVec = builder.endVector();

  Meshes.startGlobalTransformsVector(builder, nSamples);
  for (let i = nSamples - 1; i >= 0; i--) {
    const gt = globalTransformData[i];
    Transform.createTransform(
      builder,
      gt.pos[0], gt.pos[1], gt.pos[2],
      gt.xDir[0], gt.xDir[1], gt.xDir[2],
      gt.yDir[0], gt.yDir[1], gt.yDir[2]
    );
  }
  const globalTransformsVec = builder.endVector();

  Meshes.startLocalTransformsVector(builder, 1);
  Transform.createTransform(builder, 0, 0, 0, 1, 0, 0, 0, 1, 0);
  const localTransformsVec = builder.endVector();

  Meshes.startCircleExtrusionsVector(builder, 0);
  const circleExtrusionsVec = builder.endVector();

  const coordsOffset = Transform.createTransform(builder, 0, 0, 0, 1, 0, 0, 0, 1, 0);

  Meshes.startMeshes(builder);
  Meshes.addCoordinates(builder, coordsOffset);
  Meshes.addMeshesItems(builder, meshesItemsVec);
  Meshes.addSamples(builder, samplesVec);
  Meshes.addRepresentations(builder, representationsVec);
  Meshes.addMaterials(builder, materialsVec);
  Meshes.addCircleExtrusions(builder, circleExtrusionsVec);
  Meshes.addShells(builder, shellsVec);
  Meshes.addLocalTransforms(builder, localTransformsVec);
  Meshes.addGlobalTransforms(builder, globalTransformsVec);
  const meshesOff = Meshes.endMeshes(builder);

  const catOffsets = categories.map((c) => builder.createString(c));
  const categoriesVec = Model.createCategoriesVector(builder, catOffsets);

  const localIdsVec = Model.createLocalIdsVector(builder, localIds);

  const guidStr = builder.createString(options.modelId || '00000000-0000-0000-0000-000000000000');

  const guidOffsets = guids.map((g) => builder.createString(g));
  const guidsVec = Model.createGuidsVector(builder, guidOffsets);

  const guidsItemsVec = Model.createGuidsItemsVector(builder, localIds);

  const attributeOffsets: flatbuffers.Offset[] = [];
  for (const name of names) {
    const dataOffsets: flatbuffers.Offset[] = [];
    if (name) {
      const entry = JSON.stringify(['Name', name, 'STRING']);
      dataOffsets.push(builder.createString(entry));
    }
    const dataVec = Attribute.createDataVector(builder, dataOffsets);
    Attribute.startAttribute(builder);
    Attribute.addData(builder, dataVec);
    attributeOffsets.push(Attribute.endAttribute(builder));
  }
  const attributesVec = Model.createAttributesVector(builder, attributeOffsets);

  const metadataOff = builder.createString(
    JSON.stringify({
      layer_hidden: scene.layerHidden || {},
      generated_name_guids: generatedNameGuids,
    })
  );

  function buildSpatialNode(node: InstancedNode): flatbuffers.Offset {
    const childOffsets: flatbuffers.Offset[] = [];
    if (node.children) {
      for (const child of node.children) {
        childOffsets.push(buildSpatialNode(child));
      }
    }
    const childrenVec = SpatialStructure.createChildrenVector(builder, childOffsets);
    const categoryOffset = node.layer ? builder.createString(node.layer) : null;
    const itemIndex = itemIndexByNode.get(node);

    SpatialStructure.startSpatialStructure(builder);
    if (itemIndex !== undefined) {
      SpatialStructure.addLocalId(builder, itemIndex);
    }
    if (categoryOffset !== null) {
      SpatialStructure.addCategory(builder, categoryOffset);
    }
    SpatialStructure.addChildren(builder, childrenVec);
    return SpatialStructure.endSpatialStructure(builder);
  }

  const rootSpatial = buildSpatialNode(scene.sceneHierarchy);

  Model.startModel(builder);
  Model.addMeshes(builder, meshesOff);
  Model.addLocalIds(builder, localIdsVec);
  Model.addCategories(builder, categoriesVec);
  Model.addAttributes(builder, attributesVec);
  Model.addGuids(builder, guidsVec);
  Model.addGuidsItems(builder, guidsItemsVec);
  Model.addGuid(builder, guidStr);
  Model.addMaxLocalId(builder, localIds.length);
  Model.addSpatialStructure(builder, rootSpatial);
  Model.addMetadata(builder, metadataOff);
  const modelOff = Model.endModel(builder);

  Model.finishModelBuffer(builder, modelOff);
  const rawBytes = builder.asUint8Array();

  return options.raw ? rawBytes : fflate.zlibSync(rawBytes);
}

// RepresentationClass values this module can read geometry for today -
// mirrors Python's own `_SUPPORTED_REPRESENTATION_CLASSES` and its note on
// why (only SHELL is written by any of this project's own exporters yet).
const SUPPORTED_REPRESENTATION_CLASSES = new Set<number>([RepresentationClass.SHELL]);

function cross3(a: [number, number, number], b: [number, number, number]): [number, number, number] {
  return [
    a[1] * b[2] - a[2] * b[1],
    a[2] * b[0] - a[0] * b[2],
    a[0] * b[1] - a[1] * b[0],
  ];
}

/** One normal per vertex, flat-shaded: each triangle's own face normal,
 * duplicated across its 3 vertices - the most a Shell (points + indices,
 * nothing else) can ever give back. Mirrors Python's `_compute_flat_normals`. */
function computeFlatNormals(
  points: [number, number, number][],
  triangles: [number, number, number][]
): [number, number, number][] {
  const normals: [number, number, number][] = points.map(() => [0, 0, 1]);
  for (const [a, b, c] of triangles) {
    const pa = points[a];
    const pb = points[b];
    const pc = points[c];
    const u: [number, number, number] = [pb[0] - pa[0], pb[1] - pa[1], pb[2] - pa[2]];
    const v: [number, number, number] = [pc[0] - pa[0], pc[1] - pa[1], pc[2] - pa[2]];
    let n = cross3(u, v);
    const length = Math.sqrt(n[0] * n[0] + n[1] * n[1] + n[2] * n[2]);
    if (length > 1e-12) {
      n = [n[0] / length, n[1] / length, n[2] / length];
    }
    normals[a] = n;
    normals[b] = n;
    normals[c] = n;
  }
  return normals;
}

/** Reassemble a glTF-style column-major 4x4 matrix from a Fragments
 * `Transform` struct (position + x/y direction unit vectors). The struct
 * never stores a Z direction - reconstructed here as `xDir cross yDir`,
 * matching the same right-handed orthonormal frame {@link decomposeTrs}
 * produces on export. Mirrors Python's `_transform_to_matrix16`. */
function transformToMatrix16(transform: Transform): number[] {
  const pos = transform.position(new DoubleVector())!;
  const xDirS = transform.xDirection(new FloatVector())!;
  const yDirS = transform.yDirection(new FloatVector())!;
  const xDir: [number, number, number] = [xDirS.x(), xDirS.y(), xDirS.z()];
  const yDir: [number, number, number] = [yDirS.x(), yDirS.y(), yDirS.z()];
  const zDir = cross3(xDir, yDir);
  return [
    xDir[0], xDir[1], xDir[2], 0.0,
    yDir[0], yDir[1], yDir[2], 0.0,
    zDir[0], zDir[1], zDir[2], 0.0,
    pos.x(), pos.y(), pos.z(), 1.0,
  ];
}

/** Pull the `["Name", value, "STRING"]` entry out of an item's
 * `Attribute.data`, matching the exact convention {@link toFragments} (and
 * the real `IfcImporter`) writes. Mirrors Python's `_extract_name_attribute`. */
function extractNameAttribute(attribute: Attribute | null): string {
  if (attribute === null) return '';
  for (let i = 0; i < attribute.dataLength(); i++) {
    const raw = attribute.data(i);
    try {
      const triple = JSON.parse(raw);
      if (Array.isArray(triple) && triple.length >= 2 && triple[0] === 'Name') {
        return String(triple[1]);
      }
    } catch {
      continue;
    }
  }
  return '';
}

/**
 * Parse a real `.frag` file's bytes into an {@link InstancedScene} - the
 * mirror of {@link toFragments}. Accepts either the zlib-compressed wire
 * format (the default `toFragments`/real loader convention) or raw,
 * uncompressed FlatBuffers bytes; detected automatically the same way the
 * real `@thatopen/fragments` loader does, by attempting zlib inflation
 * first.
 *
 * Known gaps, matching Python's own read-side exactly (not independently
 * improved on here - see openskp#285): `positionMm`/`properties`/
 * `attributeDictionaries` on each node are left at their defaults, since
 * the Fragments format itself doesn't carry them separately from the
 * `Name` attribute this function does extract.
 */
export function fromFragments(data: Uint8Array): InstancedScene {
  let rawBytes: Uint8Array;
  try {
    rawBytes = fflate.unzlibSync(data);
  } catch {
    rawBytes = data;
  }

  const bb = new flatbuffers.ByteBuffer(rawBytes);
  const model = Model.getRootAsModel(bb);
  const meshes = model.meshes();

  // ---- Materials (flat RGBA - no texture concept exists in this format
  // at all). ----
  const gltfMaterials: Record<string, unknown>[] = [];
  if (meshes !== null) {
    for (let i = 0; i < meshes.materialsLength(); i++) {
      const mat = meshes.materials(i)!;
      gltfMaterials.push({
        pbrMetallicRoughness: {
          baseColorFactor: [mat.r() / 255.0, mat.g() / 255.0, mat.b() / 255.0, mat.a() / 255.0],
          metallicFactor: 0.0,
          roughnessFactor: 1.0,
        },
        doubleSided: mat.renderedFaces() !== RenderedFaces.ONE,
      });
    }
  }
  if (gltfMaterials.length === 0) {
    gltfMaterials.push({
      pbrMetallicRoughness: { baseColorFactor: [0.8, 0.8, 0.8, 1.0], metallicFactor: 0.0, roughnessFactor: 1.0 },
    });
  }

  // ---- Shells -> (points, triangles), decoded once per shell index,
  // reused by every sample referencing it. ----
  const nShells = meshes !== null ? meshes.shellsLength() : 0;
  const shellGeometry: ([number, number, number][] | null)[] = new Array(nShells).fill(null);
  const shellTriangles: [number, number, number][][] = new Array(nShells).fill(null);

  function decodeShell(shellIdx: number): { points: [number, number, number][]; triangles: [number, number, number][] } {
    if (shellGeometry[shellIdx] !== null) {
      return { points: shellGeometry[shellIdx]!, triangles: shellTriangles[shellIdx] };
    }
    const shell = meshes!.shells(shellIdx)!;
    const nPoints = shell.pointsLength();
    const points: [number, number, number][] = [];
    for (let j = 0; j < nPoints; j++) {
      const p = shell.points(j)!;
      points.push([p.x(), p.y(), p.z()]);
    }

    const isBig = shell.type() === ShellType.BIG;
    const triangles: [number, number, number][] = [];
    if (isBig) {
      for (let j = 0; j < shell.bigProfilesLength(); j++) {
        const profile = shell.bigProfiles(j)!;
        if (profile.indicesLength() >= 3) {
          triangles.push([profile.indices(0)!, profile.indices(1)!, profile.indices(2)!]);
        }
      }
    } else {
      for (let j = 0; j < shell.profilesLength(); j++) {
        const profile = shell.profiles(j)!;
        if (profile.indicesLength() >= 3) {
          triangles.push([profile.indices(0)!, profile.indices(1)!, profile.indices(2)!]);
        }
      }
    }

    shellGeometry[shellIdx] = points;
    shellTriangles[shellIdx] = triangles;
    return { points, triangles };
  }

  // ---- Group samples by item (Meshes.meshesItems[k] -> item index, NOT
  // Sample.item() - see toFragments's own comment on why the two differ;
  // Sample.item() is just the sample's own position, always). ----
  const nSamples = meshes !== null ? meshes.samplesLength() : 0;
  const samplesByItem = new Map<number, number[]>();
  for (let k = 0; k < nSamples; k++) {
    const itemIdx = meshes!.meshesItems(k)!;
    if (!samplesByItem.has(itemIdx)) samplesByItem.set(itemIdx, []);
    samplesByItem.get(itemIdx)!.push(k);
  }

  // ---- Per-item metadata: name, guid, category, world transform.
  // localIds[i] IS the item index space - see toFragments's own
  // `localIds.push(itemIndex)`. ----
  const nItems = model.localIdsLength();
  const localIdToItemIndex = new Map<number, number>();
  for (let i = 0; i < nItems; i++) {
    localIdToItemIndex.set(model.localIds(i)!, i);
  }

  const guidByItem = new Map<number, string>();
  const nGuidItems = model.guidsItemsLength();
  for (let i = 0; i < nGuidItems; i++) {
    const lid = model.guidsItems(i)!;
    const itemIdx = localIdToItemIndex.get(lid);
    if (itemIdx !== undefined && i < model.guidsLength()) {
      guidByItem.set(itemIdx, model.guids(i) as string);
    }
  }

  let metadata: Record<string, unknown> = {};
  const metadataRaw = model.metadata();
  if (metadataRaw) {
    try {
      metadata = JSON.parse(metadataRaw as string);
    } catch {
      metadata = {};
    }
  }
  const layerHidden: Record<string, boolean> = { ...((metadata.layer_hidden as Record<string, boolean>) || {}) };
  const generatedNameGuids = new Set<string>((metadata.generated_name_guids as string[]) || []);

  const meshResources: InstancedMeshResource[] = [];
  const resourceBySignature = new Map<string, string>();
  let warnedUnsupported = false;

  function buildResourceForItem(itemIdx: number, itemName: string): string {
    const sampleIndices = samplesByItem.get(itemIdx) || [];
    const signature: [number, number][] = [];
    const primitives: LocalPrimitive[] = [];
    for (const k of sampleIndices) {
      const sample = meshes!.samples(k)!;
      const repIdx = sample.representation();
      const matIdx = sample.material();
      const representation = repIdx < meshes!.representationsLength() ? meshes!.representations(repIdx) : null;
      const repClass = representation !== null ? representation.representationClass() : RepresentationClass.SHELL;
      if (!SUPPORTED_REPRESENTATION_CLASSES.has(repClass)) {
        if (!warnedUnsupported) {
          console.warn(
            `openskp fromFragments: skipping a sample with unsupported RepresentationClass=${repClass} ` +
            '(only SHELL is read today).'
          );
          warnedUnsupported = true;
        }
        continue;
      }
      signature.push([repIdx, matIdx]);
      // Representation.id() is the index into Meshes.shells - NOT the
      // representation's own position in the representations vector. See
      // Python's from_fragments's own comment on why this must follow
      // id(), matching the real reader's own fetch-functions.ts.
      const shellIdx = representation!.id();
      if (shellIdx >= nShells) continue;
      const { points, triangles } = decodeShell(shellIdx);
      const normals = computeFlatNormals(points, triangles);
      const positions = new Float32Array(points.length * 3);
      const normalsArr = new Float32Array(points.length * 3);
      for (let i = 0; i < points.length; i++) {
        positions[i * 3] = points[i][0];
        positions[i * 3 + 1] = points[i][1];
        positions[i * 3 + 2] = points[i][2];
        normalsArr[i * 3] = normals[i][0];
        normalsArr[i * 3 + 1] = normals[i][1];
        normalsArr[i * 3 + 2] = normals[i][2];
      }
      const uvs = new Float32Array(points.length * 2);
      const indices = new Uint32Array(triangles.length * 3);
      for (let i = 0; i < triangles.length; i++) {
        indices[i * 3] = triangles[i][0];
        indices[i * 3 + 1] = triangles[i][1];
        indices[i * 3 + 2] = triangles[i][2];
      }
      primitives.push({ positions, normals: normalsArr, uvs, indices, materialIndex: matIdx < gltfMaterials.length ? matIdx : 0 });
    }

    const sigKey = signature.map(([r, m]) => `${r}:${m}`).join(',');
    if (sigKey && resourceBySignature.has(sigKey)) {
      return resourceBySignature.get(sigKey)!;
    }

    const resourceId = `frag-${itemIdx}`;
    meshResources.push({
      id: resourceId,
      definitionId: itemIdx,
      definitionName: itemName || resourceId,
      variantKey: 'default',
      primitives,
    });
    if (sigKey) resourceBySignature.set(sigKey, resourceId);
    return resourceId;
  }

  // ---- Spatial structure -> InstancedNode tree. ----
  function buildNode(spatial: SpatialStructure): InstancedNode {
    const localId = spatial.localId();
    const category = spatial.category() || '';

    let name = '';
    let guid = '';
    let meshResourceId: string | undefined;
    let matrix: number[] = IDENTITY_MATRIX;
    let nameIsGenerated = false;

    if (localId !== null) {
      const itemIdx = localIdToItemIndex.get(localId);
      if (itemIdx !== undefined) {
        const attribute = itemIdx < model.attributesLength() ? model.attributes(itemIdx) : null;
        name = extractNameAttribute(attribute);
        guid = guidByItem.get(itemIdx) || '';
        nameIsGenerated = Boolean(guid) && generatedNameGuids.has(guid);
        if (samplesByItem.has(itemIdx)) {
          meshResourceId = buildResourceForItem(itemIdx, name || category);
          const transform = meshes!.globalTransforms(samplesByItem.get(itemIdx)![0])!;
          matrix = transformToMatrix16(transform);
        }
      }
    }

    const children: InstancedNode[] = [];
    for (let i = 0; i < spatial.childrenLength(); i++) {
      children.push(buildNode(spatial.children(i)!));
    }

    return {
      name,
      nameIsGenerated,
      definitionName: category,
      layer: category,
      matrix,
      positionMm: [0, 0, 0],
      properties: {},
      attributeDictionaries: {},
      guid,
      meshResourceId,
      children,
    };
  }

  const rootSpatial = model.spatialStructure();
  const sceneHierarchy: InstancedNode = rootSpatial !== null
    ? buildNode(rootSpatial)
    : {
        name: 'ROOT',
        nameIsGenerated: false,
        definitionName: 'ROOT',
        layer: '',
        matrix: IDENTITY_MATRIX,
        positionMm: [0, 0, 0],
        properties: {},
        attributeDictionaries: {},
        guid: '',
        children: [],
      };

  return {
    bounds: null,
    sceneHierarchy,
    meshResources,
    gltfMaterials,
    textures: [],
    layerHidden,
  };
}
