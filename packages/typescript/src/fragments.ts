import * as flatbuffers from 'flatbuffers';
import * as fflate from 'fflate';
import type { InstancedScene, InstancedNode } from './instanced';
import {
  Model,
  Meshes,
  Shell,
  ShellProfile,
  BigShellProfile,
  FloatVector,
  Representation,
  Transform,
  Material,
  Sample,
  SpatialStructure,
  ShellType,
} from './fragments-schema';

/** Options for {@link toFragments}. */
export interface FragmentExportOptions {
  /** Model GUID / identifier. If omitted, a random UUID is generated. */
  modelId?: string;
  /** If true, returns raw uncompressed FlatBuffers buffer instead of zlib-deflated. Default: false. */
  raw?: boolean;
  /** Whether to set materials to double-sided. Default: true. */
  doubleSided?: boolean;
}

/** Multiply two 4x4 column-major matrices: out = a * b */
function multiply4x4(a: number[], b: number[]): number[] {
  const out = new Array<number>(16);
  for (let i = 0; i < 4; i++) {
    for (let j = 0; j < 4; j++) {
      out[j * 4 + i] =
        a[i] * b[j * 4] +
        a[4 + i] * b[j * 4 + 1] +
        a[8 + i] * b[j * 4 + 2] +
        a[12 + i] * b[j * 4 + 3];
    }
  }
  return out;
}

function generateUUID(): string {
  const hex = '0123456789abcdef';
  let s = '';
  for (let i = 0; i < 36; i++) {
    if (i === 8 || i === 13 || i === 18 || i === 23) {
      s += '-';
    } else if (i === 14) {
      s += '4';
    } else {
      s += hex[(Math.random() * 16) | 0];
    }
  }
  return s;
}

/**
 * Export an {@link InstancedScene} (from {@link buildInstancedScene}) directly to
 * ThatOpen Fragments (.frag) binary format.
 *
 * This performs zero ASCII string conversions and bypasses intermediate IFC generation,
 * enabling high-performance GPU-instanced 60 FPS rendering in ThatOpen Engine / IFC.js viewers.
 *
 * @param scene - The instanced scene from buildInstancedScene()
 * @param options - Fragment export options
 * @returns Binary .frag file as Uint8Array (zlib deflated or raw FlatBuffers)
 */
export function toFragments(
  scene: InstancedScene,
  options: FragmentExportOptions = {}
): Uint8Array {
  const builder = new flatbuffers.Builder(1024 * 1024);
  const doubleSided = options.doubleSided ?? true;
  let nextId = 1;

  // 1. Process Materials
  const materialMap = new Map<number, number>();
  const materialsList: { r: number; g: number; b: number; a: number }[] = [];

  const getMaterialIndex = (gltfMatIdx: number): number => {
    if (materialMap.has(gltfMatIdx)) return materialMap.get(gltfMatIdx)!;

    let r = 200, g = 200, b = 200, a = 255;
    const gltfMat = scene.gltfMaterials?.[gltfMatIdx] as any;
    if (gltfMat?.pbrMetallicRoughness?.baseColorFactor) {
      const col = gltfMat.pbrMetallicRoughness.baseColorFactor;
      r = Math.min(255, Math.max(0, Math.round((col[0] ?? 0.8) * 255)));
      g = Math.min(255, Math.max(0, Math.round((col[1] ?? 0.8) * 255)));
      b = Math.min(255, Math.max(0, Math.round((col[2] ?? 0.8) * 255)));
      a = Math.min(255, Math.max(0, Math.round((col[3] ?? 1.0) * 255)));
    }
    const idx = materialsList.length;
    materialsList.push({ r, g, b, a });
    materialMap.set(gltfMatIdx, idx);
    return idx;
  };

  // Ensure default fallback material exists
  if (scene.gltfMaterials.length === 0) {
    getMaterialIndex(0);
  }

  // 2. Process Geometries (Shells & Representations)
  interface CompiledGeom {
    shellOffset: flatbuffers.Offset;
    bbox: { minX: number; minY: number; minZ: number; maxX: number; maxY: number; maxZ: number };
    materialIdx: number;
    reprIndex: number;
  }

  const resourceGeomMap = new Map<string, CompiledGeom[]>();
  const allRepresentations: CompiledGeom[] = [];

  for (const res of scene.meshResources) {
    const compiledList: CompiledGeom[] = [];

    for (const prim of res.primitives) {
      const positions = prim.positions;
      const indices = prim.indices;
      const vertexCount = positions.length / 3;
      if (vertexCount === 0 || indices.length === 0) continue;

      const isBig = vertexCount > 65535;

      // Compute AABB Bounding Box
      let minX = Infinity, minY = Infinity, minZ = Infinity;
      let maxX = -Infinity, maxY = -Infinity, maxZ = -Infinity;

      for (let i = 0; i < positions.length; i += 3) {
        const x = positions[i], y = positions[i + 1], z = positions[i + 2];
        if (x < minX) minX = x; if (x > maxX) maxX = x;
        if (y < minY) minY = y; if (y > maxY) maxY = y;
        if (z < minZ) minZ = z; if (z > maxZ) maxZ = z;
      }
      if (minX === Infinity) {
        minX = minY = minZ = 0;
        maxX = maxY = maxZ = 0.1;
      }

      // Build Points Vector (backwards as required by FlatBuffers)
      Shell.startPointsVector(builder, vertexCount);
      for (let i = vertexCount - 1; i >= 0; i--) {
        FloatVector.createFloatVector(
          builder,
          positions[i * 3],
          positions[i * 3 + 1],
          positions[i * 3 + 2]
        );
      }
      const pointsOffset = builder.endVector();

      // Build Profiles (each triangle is a profile of 3 indices)
      const triangleCount = Math.floor(indices.length / 3);
      const profileOffsets: flatbuffers.Offset[] = [];
      const bigProfileOffsets: flatbuffers.Offset[] = [];

      for (let t = 0; t < triangleCount; t++) {
        const triIndices = [indices[t * 3], indices[t * 3 + 1], indices[t * 3 + 2]];
        if (isBig) {
          const idxVector = BigShellProfile.createIndicesVector(builder, triIndices);
          bigProfileOffsets.push(BigShellProfile.createBigShellProfile(builder, idxVector));
        } else {
          const idxVector = ShellProfile.createIndicesVector(builder, triIndices);
          profileOffsets.push(ShellProfile.createShellProfile(builder, idxVector));
        }
      }

      const profilesVector = Shell.createProfilesVector(builder, profileOffsets);
      const bigProfilesVector = Shell.createBigProfilesVector(builder, bigProfileOffsets);
      const holesVector = Shell.createHolesVector(builder, []);
      const bigHolesVector = Shell.createBigHolesVector(builder, []);
      const faceIdsVector = Shell.createProfilesFaceIdsVector(
        builder,
        new Int16Array(triangleCount)
      );

      Shell.startShell(builder);
      Shell.addProfiles(builder, profilesVector);
      Shell.addHoles(builder, holesVector);
      Shell.addPoints(builder, pointsOffset);
      Shell.addBigProfiles(builder, bigProfilesVector);
      Shell.addBigHoles(builder, bigHolesVector);
      Shell.addType(builder, isBig ? ShellType.BIG : ShellType.NONE);
      Shell.addProfilesFaceIds(builder, faceIdsVector);
      const shellOffset = Shell.endShell(builder);

      const geomItem: CompiledGeom = {
        shellOffset,
        bbox: { minX, minY, minZ, maxX, maxY, maxZ },
        materialIdx: getMaterialIndex(prim.materialIndex),
        reprIndex: allRepresentations.length,
      };

      compiledList.push(geomItem);
      allRepresentations.push(geomItem);
    }

    resourceGeomMap.set(res.id, compiledList);
  }

  // 3. Walk Scene Graph & Collect Placed Instances
  interface PlacedInstance {
    itemId: number;
    matrix: number[];
    resourceId: string;
    name: string;
  }

  const placedInstances: PlacedInstance[] = [];
  let itemCounter = 0;

  function walk(node: InstancedNode, parentMatrix: number[]): void {
    const worldMatrix = multiply4x4(parentMatrix, node.matrix);

    if (node.meshResourceId && resourceGeomMap.has(node.meshResourceId)) {
      placedInstances.push({
        itemId: itemCounter++,
        matrix: worldMatrix,
        resourceId: node.meshResourceId,
        name: node.name || node.definitionName || `Item_${itemCounter}`,
      });
    }

    if (node.children) {
      for (const child of node.children) {
        walk(child, worldMatrix);
      }
    }
  }

  const identity = [
    1, 0, 0, 0,
    0, 1, 0, 0,
    0, 0, 1, 0,
    0, 0, 0, 1,
  ];
  walk(scene.sceneHierarchy, identity);

  // If no instances were placed (loose geometry fallback)
  if (placedInstances.length === 0 && allRepresentations.length > 0) {
    for (const [resId] of resourceGeomMap) {
      placedInstances.push({
        itemId: itemCounter++,
        matrix: identity,
        resourceId: resId,
        name: 'Root_Geometry',
      });
    }
  }

  // 4. Global Transforms Vector (Struct Vector: size 48 per transform)
  const gtCount = placedInstances.length;
  const gtLocalIds = new Array<number>(gtCount);
  for (let i = 0; i < gtCount; i++) {
    gtLocalIds[i] = nextId++;
  }

  Meshes.startGlobalTransformsVector(builder, gtCount);
  for (let i = gtCount - 1; i >= 0; i--) {
    const inst = placedInstances[i];
    const m = inst.matrix;
    // m is column-major:
    // col 0: [m[0], m[1], m[2]] -> xDirection
    // col 1: [m[4], m[5], m[6]] -> yDirection
    // col 3: [m[12], m[13], m[14]] -> position
    Transform.createTransform(
      builder,
      m[12], m[13], m[14],
      m[0], m[1], m[2],
      m[4], m[5], m[6]
    );
  }
  const globalTransforms = builder.endVector();

  // 5. Local Transforms (At least 1 identity transform for base samples)
  Meshes.startLocalTransformsVector(builder, 1);
  const ltLocalIds = [nextId++];
  Transform.createTransform(builder, 0, 0, 0, 1, 0, 0, 0, 1, 0);
  const localTransforms = builder.endVector();

  // 6. Shells Vector (Offsets)
  Meshes.startShellsVector(builder, allRepresentations.length);
  for (let i = allRepresentations.length - 1; i >= 0; i--) {
    builder.addOffset(allRepresentations[i].shellOffset);
  }
  const shellsVector = builder.endVector();

  // 7. Representations Vector (Struct Vector: size 32 per representation)
  const reprCount = allRepresentations.length;
  const reprLocalIds = new Array<number>(reprCount);
  for (let i = 0; i < reprCount; i++) {
    reprLocalIds[i] = nextId++;
  }

  Meshes.startRepresentationsVector(builder, reprCount);
  for (let i = reprCount - 1; i >= 0; i--) {
    const geom = allRepresentations[i];
    Representation.createRepresentation(
      builder,
      i, // geomIndex
      geom.bbox.minX, geom.bbox.minY, geom.bbox.minZ,
      geom.bbox.maxX, geom.bbox.maxY, geom.bbox.maxZ,
      1 // RepresentationClass.SHELL
    );
  }
  const representationsVector = builder.endVector();

  // 8. Materials Vector (Struct Vector: size 6 per material)
  const matCount = materialsList.length;
  const matLocalIds = new Array<number>(matCount);
  for (let i = 0; i < matCount; i++) {
    matLocalIds[i] = nextId++;
  }

  Meshes.startMaterialsVector(builder, matCount);
  for (let i = matCount - 1; i >= 0; i--) {
    const m = materialsList[i];
    Material.createMaterial(
      builder,
      m.r, m.g, m.b, m.a,
      doubleSided ? 2 : 1, // 2 = RenderedFaces.TWO
      0
    );
  }
  const materialsVector = builder.endVector();

  // 9. Samples Vector (Struct Vector: size 16 per sample)
  let totalSamples = 0;
  for (let i = 0; i < gtCount; i++) {
    const geoms = resourceGeomMap.get(placedInstances[i].resourceId);
    if (geoms) totalSamples += geoms.length;
  }

  const sampleLocalIds = new Array<number>(totalSamples);
  for (let i = 0; i < totalSamples; i++) {
    sampleLocalIds[i] = nextId++;
  }

  Meshes.startSamplesVector(builder, totalSamples);
  for (let instIdx = gtCount - 1; instIdx >= 0; instIdx--) {
    const inst = placedInstances[instIdx];
    const geoms = resourceGeomMap.get(inst.resourceId);
    if (!geoms) continue;

    for (let g = geoms.length - 1; g >= 0; g--) {
      const geom = geoms[g];
      Sample.createSample(
        builder,
        inst.itemId,
        geom.materialIdx,
        geom.reprIndex,
        0 // localTransform index 0
      );
    }
  }
  const samplesVector = builder.endVector();

  // 10. Meshes items vector (itemId indices: 0..placedInstances.length-1)
  const itemIdsArray: number[] = [];
  for (let i = 0; i < placedInstances.length; i++) {
    itemIdsArray.push(i);
  }
  const meshesItemsVector = Meshes.createMeshesItemsVector(builder, itemIdsArray);

  // Representation IDs, Sample IDs, Material IDs, Transform IDs
  const reprIdsVector = Meshes.createRepresentationIdsVector(builder, reprLocalIds);
  const sampleIdsVector = Meshes.createSampleIdsVector(builder, sampleLocalIds);
  const matIdsVector = Meshes.createMaterialIdsVector(builder, matLocalIds);
  const ltIdsVector = Meshes.createLocalTransformIdsVector(builder, ltLocalIds);
  const gtIdsVector = Meshes.createGlobalTransformIdsVector(builder, gtLocalIds);

  // Circle extrusions vector (empty vector required by Meshes table)
  const circleExtrusionsVector = Meshes.createCircleExtrusionsVector(builder, []);

  // Default coordinate frame (must be serialized immediately before Meshes.startMeshes)
  const coordsOffset = Transform.createTransform(builder, 0, 0, 0, 1, 0, 0, 0, 1, 0);

  // Meshes table
  Meshes.startMeshes(builder);
  Meshes.addCoordinates(builder, coordsOffset);
  Meshes.addMeshesItems(builder, meshesItemsVector);
  Meshes.addSamples(builder, samplesVector);
  Meshes.addRepresentations(builder, representationsVector);
  Meshes.addMaterials(builder, materialsVector);
  Meshes.addCircleExtrusions(builder, circleExtrusionsVector);
  Meshes.addShells(builder, shellsVector);
  Meshes.addLocalTransforms(builder, localTransforms);
  Meshes.addGlobalTransforms(builder, globalTransforms);
  Meshes.addMaterialIds(builder, matIdsVector);
  Meshes.addRepresentationIds(builder, reprIdsVector);
  Meshes.addSampleIds(builder, sampleIdsVector);
  Meshes.addLocalTransformIds(builder, ltIdsVector);
  Meshes.addGlobalTransformIds(builder, gtIdsVector);
  const meshesOffset = Meshes.endMeshes(builder);

  // 11. Spatial Structure Table
  const catProject = builder.createString('IFCPROJECT');
  SpatialStructure.startSpatialStructure(builder);
  SpatialStructure.addCategory(builder, catProject);
  SpatialStructure.addLocalId(builder, nextId++);
  const spatialStructureOffset = SpatialStructure.endSpatialStructure(builder);

  // 12. Model Table (Root)
  const guidStr = options.modelId || generateUUID();
  const guidOffset = builder.createString(guidStr);
  const metadataOffset = builder.createString(
    JSON.stringify({ name: 'OpenSKP Export', schema: 'Fragments 3.4', instances: placedInstances.length })
  );

  const instCount = placedInstances.length;
  const localIdsArray = new Array<number>(instCount);
  const guidOffsets = new Array<flatbuffers.Offset>(instCount);
  const guidItemsArray = new Array<number>(instCount);
  for (let i = 0; i < instCount; i++) {
    const locId = nextId++;
    localIdsArray[i] = locId;
    guidOffsets[i] = builder.createString(generateUUID());
    guidItemsArray[i] = locId;
  }
  const localIdsVector = Model.createLocalIdsVector(builder, localIdsArray);
  const guidsVector = Model.createGuidsVector(builder, guidOffsets);
  const guidsItemsVector = Model.createGuidsItemsVector(builder, guidItemsArray);

  const catSkp = builder.createString('SKPOBJECT');
  const categoriesVector = Model.createCategoriesVector(builder, [catSkp]);

  Model.startModel(builder);
  Model.addMeshes(builder, meshesOffset);
  Model.addMetadata(builder, metadataOffset);
  Model.addGuid(builder, guidOffset);
  Model.addGuids(builder, guidsVector);
  Model.addGuidsItems(builder, guidsItemsVector);
  Model.addLocalIds(builder, localIdsVector);
  Model.addCategories(builder, categoriesVector);
  Model.addSpatialStructure(builder, spatialStructureOffset);
  Model.addMaxLocalId(builder, nextId);
  const modelOffset = Model.endModel(builder);

  Model.finishModelBuffer(builder, modelOffset);
  const rawBytes = builder.asUint8Array();

  return options.raw ? rawBytes : fflate.zlibSync(rawBytes);
}
