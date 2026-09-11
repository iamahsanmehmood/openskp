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
 * Mirrors Python's and C++'s collect_leaves exactly.
 */
function collectLeaves(
  node: InstancedNode,
  root: InstancedNode,
  parentMatrix: number[],
  out: Leaf[]
): void {
  const world = multiplyMatrices(parentMatrix, node.matrix);
  const isNamedWrapper = node !== root && Boolean(node.name && node.name !== '' && node.name !== 'ROOT');
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
  const shellKeyToIndex = new Map<string, number>();
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

  const getOrBakeShell = (
    resourceId: string,
    primIdx: number,
    prim: LocalPrimitive,
    scale: [number, number, number],
    mirrored: boolean
  ): number => {
    const sk = scaleCacheKey(mirrored, scale);
    const key = `${resourceId}:${primIdx}:${sk}`;
    if (shellKeyToIndex.has(key)) {
      return shellKeyToIndex.get(key)!;
    }

    const baked = bakePrimitive(prim, scale, mirrored);
    const isBig = baked.points.length > USHORT_MAX;

    const profileOffsets: flatbuffers.Offset[] = [];
    const bigProfileOffsets: flatbuffers.Offset[] = [];

    for (const tri of baked.triangles) {
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

    Shell.startPointsVector(builder, baked.points.length);
    for (let i = baked.points.length - 1; i >= 0; i--) {
      FloatVector.createFloatVector(
        builder,
        baked.points[i][0],
        baked.points[i][1],
        baked.points[i][2]
      );
    }
    const pointsVec = builder.endVector();

    const faceIds = new Uint16Array(baked.triangles.length);
    for (let i = 0; i < baked.triangles.length; i++) {
      faceIds[i] = i & 0xffff;
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
    for (const p of baked.points) {
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

    shellKeyToIndex.set(key, index);
    return index;
  };

  const localIds: number[] = [];
  const categories: string[] = [];
  const names: string[] = [];
  const guids: string[] = [];
  const sampleMaterial: number[] = [];
  const sampleRepresentation: number[] = [];
  const meshesItems: number[] = [];
  const globalTransformData: {
    pos: [number, number, number];
    xDir: [number, number, number];
    yDir: [number, number, number];
  }[] = [];
  const itemIndexByNode = new Map<InstancedNode, number>();
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

    const rawGuid = (node as any).guid || '';
    const itemGuid =
      rawGuid && !seenGuids.has(rawGuid) ? rawGuid : `openskp-${itemIndex}`;
    seenGuids.add(itemGuid);
    guids.push(itemGuid);

    itemIndexByNode.set(node, itemIndex);

    if (res !== undefined) {
      const trs = decomposeTrs(world);
      for (let primIdx = 0; primIdx < res.primitives.length; primIdx++) {
        const prim = res.primitives[primIdx];
        sampleMaterial.push(getMaterialIndex(prim.materialIndex));
        sampleRepresentation.push(
          getOrBakeShell(resourceId!, primIdx, prim, trs.scale, trs.mirrored)
        );
        meshesItems.push(itemIndex);
        globalTransformData.push({
          pos: trs.position,
          xDir: trs.xDir,
          yDir: trs.yDir,
        });
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
      layer_hidden: (scene as any).layerHidden || {},
      generated_name_guids: [],
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
