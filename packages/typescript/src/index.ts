import { extractSkpContents, readMetaUnits } from './vff';
import { iterTopLevelLazy, readU32, parseVarInt } from './parser';
import { SkpParseError } from './errors';
import { ParseOptions, PROGRESS_INTERVAL, emitLog, emitProgress } from './observability';
import {
  GeometryBuilder,
  collectLayers,
  collectDefs,
  extractGeometryFromNodes,
  parseMaterialXml,
  parseStyleXml,
  resolveTextureBytes,
  findChildTag,
  ParsedDefinition,
} from './geometry';
import {
  SkpModel,
  SkpScene,
  Style,
  Material,
  Texture,
  Instance,
  Definition,
  InstanceNode,
  MeshMetadata,
  ParsedRawData,
  buildModelFromParsed,
  buildSceneFromParsed,
  SceneOptions,
} from './model';
import { isLegacy, parseLegacyToRaw } from './legacy';
import { encodeIndices } from './gltf-indices';
import { buildInstancedSceneFromParsed, InstancedScene } from './instanced';
import { extractThumbnail, SkpThumbnail } from './thumbnail';

export * from './model';
export type {
  InstancedScene,
  InstancedNode,
  InstancedMeshResource,
  LocalPrimitive,
} from './instanced';
export { toInstancedGLB } from './instanced-glb';
export { extractThumbnail } from './thumbnail';
export type { SkpThumbnail } from './thumbnail';
export type { InstancedGlbOptions } from './instanced-glb';
export * from './errors';
export * from './observability';
export { toOBJ, toMTL, exportOBJ } from './obj';
export * from './stl';
export * from './ply';
export * from './dxf';
export { toIFC, exportIFC, generateIFCGUID, classifyElement } from './ifc';
export {
  create,
  SkpBuilder,
  ComponentDefinitionBuilder,
  SkpWriteError,
} from './create';
export type {
  Point3,
  Matrix3x3,
  UvPair,
  Rotation,
  AttributeValue,
  AttributeDict,
  AddFaceOptions,
  AddCircleOptions,
  AddArcOptions,
  AddPolylineOptions,
  AddInstanceOptions,
  AddGroupInstanceOptions,
  AddComponentDefinitionOptions,
  AddGroupOptions,
} from './create';
export { openExisting } from './edit';

declare const process: any;
declare const require: any;

/**
 * Parse a SketchUp (.skp) file from an ArrayBuffer into its raw,
 * source-agnostic form - shared by parseSkp() (the light public model) and
 * buildScene() (the opt-in, heavier triangulated-scene builder), so neither
 * one pays for work only the other needs.
 *
 * Transparently handles both the modern VFF/ZIP container (SketchUp 2021+)
 * and the classic pre-2021 MFC CArchive container (SketchUp 2013-2020).
 */
function parseToRaw(buffer: ArrayBuffer, options?: ParseOptions): ParsedRawData {
  const t0 = Date.now();
  const data = new Uint8Array(buffer);
  emitLog(options, 'info', `Parsing buffer (${data.length} bytes)`);

  // Both the legacy (pre-2021 MFC) and modern (VFF/ZIP) containers share
  // this 4-byte magic - checked upfront, matching every other port, so a
  // file that isn't a SketchUp file at all fails here with stage: 'header'
  // instead of falling through to the ZIP extractor and getting mislabeled
  // stage: 'zip_extract' for a problem that has nothing to do with ZIP.
  if (!(data.length >= 4 && data[0] === 0xff && data[1] === 0xfe && data[2] === 0xff && data[3] === 0x0e)) {
    throw new SkpParseError('Not a valid SketchUp file (bad header magic)', { stage: 'header' });
  }

  if (isLegacy(data)) {
    emitLog(options, 'debug', 'Detected legacy MFC container; routing to legacy walker');
    return parseLegacyToRaw(data, options);
  }

  // 1. Extract SKP contents from VFF/ZIP container
  let contents;
  try {
    contents = extractSkpContents(data, options);
  } catch (e) {
    throw new SkpParseError(`Failed to extract SKP contents: ${(e as Error).message}`, {
      stage: 'zip_extract',
      cause: e,
    });
  }
  const version = contents.version;
  const modelData = contents.modelData;
  const materialFiles = contents.materialFiles;
  emitLog(options, 'debug', `Detected version ${version} (VFF/ZIP container)`);

  // meta/meta.dat carries the model's unit-system string (e.g.
  // "Millimeter") - legacy (pre-2021 MFC) files carry no equivalent
  // container, so units stays null there.
  const units = contents.metaData ? readMetaUnits(contents.metaData) : null;

  // 2. Parse XML materials to populate layer colors and materials
  const layerColors = new Map<string, [number, number, number]>();
  // Modern (VFF) files derive layers from Layer_<name>-prefixed materials,
  // which carry no visibility flag of their own - unlike legacy MFC files,
  // there is currently no known tag exposing a VFF layer's hidden state,
  // so every VFF layer defaults to visible here.
  const layerHidden = new Map<string, boolean>();
  const materialsMap = new Map<string, Material>();
  const materialsByFolder = new Map<string, Material>();

  for (const [name, xmlBytes] of Object.entries(materialFiles)) {
    const lowerName = name.toLowerCase();
    if (lowerName.endsWith('material.xml') && lowerName.startsWith('materials/')) {
      try {
        const decoder = new TextDecoder('utf-8');
        const xmlText = decoder.decode(xmlBytes);
        const parsedMat = parseMaterialXml(xmlText);
        if (parsedMat) {
          const folderName = name.split('/')[1] || '';
          let texture: Texture | null = null;
          if (parsedMat.hasTexture) {
            const resolved = resolveTextureBytes(
              materialFiles,
              name,
              parsedMat.textureFilename,
              parsedMat.imagePath
            );
            texture = {
              filename: resolved.filename,
              width: parsedMat.xScale,
              height: parsedMat.yScale,
              data: resolved.data,
            };
          }
          const matObj: Material = {
            name: parsedMat.name,
            // The VFF material XML record has no alpha attribute - only a
            // separate transparency value (parsedMat.trans, above). Always
            // opaque here; real per-channel alpha only exists in the legacy
            // MFC record (see legacy.ts's own material color assignment).
            color: { r: parsedMat.r, g: parsedMat.g, b: parsedMat.b, a: 255 },
            transparency: parsedMat.trans,
            id: null,
            texture,
            colorized: parsedMat.colorized,
            colorizeType: parsedMat.colorizeType,
          };
          materialsMap.set(parsedMat.name, matObj);
          if (folderName) {
            materialsByFolder.set(folderName, matObj);
          }
          if (parsedMat.name.startsWith('Layer_')) {
            layerColors.set(parsedMat.name.slice(6), [parsedMat.r, parsedMat.g, parsedMat.b]);
            layerHidden.set(parsedMat.name.slice(6), false);
          }
        }
      } catch (e) {
        emitLog(options, 'debug', `Failed to parse material.xml ${name}: ${(e as Error).message}`);
      }
    }
  }

  // 2b. Parse styles/*/style.xml: face colors for unpainted faces, stored as
  // signed-int32 ARGB variants under item id 4000 (front) / 4001 (back).
  const styles: Style[] = [];
  for (const [name, xmlBytes] of Object.entries(materialFiles)) {
    const lowerName = name.toLowerCase();
    if (lowerName.startsWith('styles/') && lowerName.endsWith('style.xml')) {
      try {
        const decoder = new TextDecoder('utf-8');
        const xmlText = decoder.decode(xmlBytes);
        const parsedStyle = parseStyleXml(xmlText);
        if (parsedStyle) {
          styles.push({
            name: parsedStyle.name,
            frontColor: parsedStyle.frontColor,
            backColor: parsedStyle.backColor,
          });
        }
      } catch (e) {
        emitLog(options, 'debug', `Failed to parse style.xml ${name}: ${(e as Error).message}`);
      }
    }
  }

  // 3. Walk the TLV tree one top-level record at a time (instead of
  // building the whole file's tree at once) so peak memory is bounded by
  // the single largest definition/layer-manager/material-manager/root
  // block, not by the file's total node count. Real production files can
  // have 100k+ separate component definitions; materializing all of them
  // simultaneously is what actually exhausts memory on large files - not
  // the (comparatively modest, ~1x) cost of decompressing model.dat itself.
  const layerIdToName = new Map<number, string>();
  const materialIdToName = new Map<number, string>();
  function collectMaterialIds(nodes: any[]) {
    for (const el of nodes) {
      if (el.tag === 'C832') {
        const dc05 = findChildTag(el.children, 'DC05');
        const nameNode = findChildTag(el.children, 'CC32');
        if (dc05 && nameNode) {
          const payload = dc05.payload;
          let mId: number;
          if (payload.length >= 6 && payload[0] === 0xDE && payload[1] === 0x05) {
            const de05Len = readU32(payload, 2);
            mId = parseVarInt(payload, 6, de05Len);
          } else {
            mId = parseVarInt(payload, 0, payload.length);
          }
          let mName = '';
          try {
            const decoder = new TextDecoder('utf-8');
            mName = decoder.decode(nameNode.payload);
          } catch (e) {
            emitLog(options, 'debug', `Failed to decode material name for id ${mId}: ${(e as Error).message}`);
          }
          materialIdToName.set(mId, mName);
        }
      }
      if (el.children && el.children.length > 0) {
        collectMaterialIds(el.children);
      }
    }
  }

  emitLog(options, 'debug', `Parsed ${materialsMap.size} materials, ${styles.length} styles`);

  const defsDict = new Map<number | string, ParsedDefinition>();
  const rootBuilder = new GeometryBuilder();

  for (const { index, total, node: el } of iterTopLevelLazy(modelData, 0, modelData.length)) {
    try {
      collectLayers([el], layerIdToName, options);
      collectMaterialIds([el]);
      collectDefs([el], defsDict, options);
      if (el.tag === 'F601') {
        extractGeometryFromNodes(el.children, rootBuilder, options);
      }
    } catch (e) {
      throw new SkpParseError(`Failed while processing top-level record: ${(e as Error).message}`, {
        stage: 'tlv_walk',
        recordIndex: index,
        totalRecords: total,
        tag: el.tag,
        cause: e,
      });
    }
    // `el` (and its whole subtree) is now unreferenced and eligible for
    // garbage collection before the next top-level record is built.
    if (index % PROGRESS_INTERVAL === 0 || index === total - 1) {
      emitProgress(options, 'tlv_walk', index + 1, total);
      emitLog(options, 'debug', `Processed ${index + 1}/${total} top-level records`);
    }
  }

  emitLog(
    options,
    'info',
    `Parse complete: ${defsDict.size} defs (${((Date.now() - t0) / 1000).toFixed(2)}s)`
  );

  if (!layerIdToName.has(1)) {
    layerIdToName.set(1, 'Layer0');
  }
  if (!layerColors.has('Layer0')) {
    layerColors.set('Layer0', [136, 136, 136]);
  }
  if (!layerHidden.has('Layer0')) {
    layerHidden.set('Layer0', false);
  }

  defsDict.set('ROOT', {
    guid: 'ROOT',
    name: 'ROOT_MODEL',
    isImage: false,
    alwaysFacesCamera: false,
    shadowsFaceSun: false,
    builder: rootBuilder,
  });

  return {
    version,
    units,
    layerColors,
    layerHidden,
    layerIdToName,
    materialIdToName,
    materialsMap,
    materialsByFolder,
    styles,
    defsDict,
  };
}

/**
 * Parse a SketchUp (.skp) file from an ArrayBuffer.
 *
 * Transparently handles both the modern VFF/ZIP container (SketchUp 2021+)
 * and the classic pre-2021 MFC CArchive container (SketchUp 2013-2020).
 *
 * Fast and memory-light regardless of file size: this returns each
 * definition's raw geometry exactly once, with no scene-graph instancing
 * resolved. For a flattened, triangulated, world-space scene ready for
 * rendering or GLB export, see {@link buildScene} - a separate, opt-in
 * step, since baking every placed instance can produce far more data than
 * the file's raw geometry.
 *
 * @param buffer - The raw file contents as an ArrayBuffer
 * @param options - Optional progress/log callbacks (see {@link ParseOptions})
 * @returns Parsed SkpModel with full geometry and metadata
 */
export function parseSkp(buffer: ArrayBuffer, options?: ParseOptions): SkpModel {
  return buildModelFromParsed(parseToRaw(buffer, options));
}

/**
 * Bake every instance actually placed in the model into world-space,
 * triangulated mesh data, ready for a GLB export or any other renderer.
 * See {@link buildSceneFromParsed} for the full explanation of why this is
 * separate from {@link parseSkp}.
 *
 * Independent of parseSkp(): calling both re-parses the raw TLV data once
 * per call rather than sharing it, trading a bit of extra CPU time for
 * keeping each call's memory footprint no larger than what it actually
 * needs.
 *
 * @param options - Optional progress/log callbacks (see {@link ParseOptions})
 */
export function buildScene(
  buffer: ArrayBuffer,
  options?: ParseOptions & SceneOptions
): SkpScene {
  return buildSceneFromParsed(parseToRaw(buffer, options), options);
}

/**
 * Build the placed scene graph with SketchUp's INSTANCING PRESERVED: each
 * distinct definition is triangulated once, in its own local space, and
 * every placement is a node carrying the transform that puts it there.
 *
 * Use this instead of {@link buildScene} when a model reuses components:
 * buildScene() bakes each placement into its own world-space vertex
 * buffers, so its output grows with `definition geometry x placement
 * count`, while this grows with `unique geometry + instance transforms`. A
 * chair placed 1,000 times costs one copy of the chair here.
 *
 * Losslessly: this performs NO decimation, quantisation or any other
 * approximation. The triangles, UVs, normals, materials and metadata are
 * the ones {@link buildScene} produces - stored once and referenced, rather
 * than copied per placement.
 *
 * Units and axes match {@link buildScene}'s GLB output: geometry and node
 * matrices are in metres, glTF Y-up (`InstancedNode.matrix` is a
 * 16-element column-major, parent-relative glTF matrix). The one exception
 * is `positionMm`, kept in millimetres on SketchUp's Z-up axes so it lines
 * up with the baked path's own metadata field.
 *
 * Independent of {@link parseSkp} and {@link buildScene}: this re-parses the
 * raw TLV data, consistent with the existing two-tier API, so callers who
 * never ask for it never pay for it.
 *
 * @param buffer - The raw file contents as an ArrayBuffer
 * @param options - Optional progress/log callbacks (see {@link ParseOptions})
 */
export function buildInstancedScene(
  buffer: ArrayBuffer,
  options?: ParseOptions & SceneOptions
): InstancedScene {
  return buildInstancedSceneFromParsed(parseToRaw(buffer, options), options);
}

function createGlb(json: any, binaryBuffer: Uint8Array): Uint8Array {
  let jsonString = JSON.stringify(json);
  const jsonRemainder = jsonString.length % 4;
  if (jsonRemainder !== 0) {
    jsonString += ' '.repeat(4 - jsonRemainder);
  }
  const jsonBuffer = new TextEncoder().encode(jsonString);

  let paddedBinaryBuffer = binaryBuffer;
  const binaryRemainder = binaryBuffer.length % 4;
  if (binaryRemainder !== 0) {
    const padLength = 4 - binaryRemainder;
    paddedBinaryBuffer = new Uint8Array(binaryBuffer.length + padLength);
    paddedBinaryBuffer.set(binaryBuffer);
  }

  const totalLength = 12 + 8 + jsonBuffer.length + 8 + paddedBinaryBuffer.length;
  const glb = new Uint8Array(totalLength);
  const view = new DataView(glb.buffer);

  // Magic 'glTF', version 2, total length
  view.setUint32(0, 0x46546C67, true);
  view.setUint32(4, 2, true);
  view.setUint32(8, totalLength, true);

  // JSON chunk
  view.setUint32(12, jsonBuffer.length, true);
  view.setUint32(16, 0x4E4F534A, true);
  glb.set(jsonBuffer, 20);

  // Binary chunk
  const binHeaderOffset = 20 + jsonBuffer.length;
  view.setUint32(binHeaderOffset, paddedBinaryBuffer.length, true);
  view.setUint32(binHeaderOffset + 4, 0x004E4942, true);
  glb.set(paddedBinaryBuffer, binHeaderOffset + 8);

  return glb;
}

/**
 * Export a baked SkpScene (see {@link buildScene}) to GLB (binary glTF 2.0)
 * format.
 *
 * @param scene - The result of buildScene()
 * @returns GLB file as Uint8Array
 */
/** Options for {@link toGLB}. */
export interface GlbOptions {
  /** Embed the scene's texture images in the GLB and point each textured
   * material's `baseColorTexture` at them.
   *
   * Off by default because it is not free: a model with photographic
   * textures can be several times larger with the images than without, and
   * the geometry alone is what most callers are after. When off, textured
   * materials fall back to their averaged base colour, which is what this
   * exporter has always produced.
   */
  textures?: boolean;
}

/**
 * Drops `baseColorTexture` from materials when the images are not being
 * embedded. The reference would otherwise dangle - `textures[0]` would not
 * exist - and a strict glTF reader rejects the file outright.
 */
function stripTextureRefs(materials: unknown[]): unknown[] {
  let needsCopy = false;
  for (const mat of materials) {
    if ((mat as any)?.pbrMetallicRoughness?.baseColorTexture) {
      needsCopy = true;
      break;
    }
  }
  if (!needsCopy) return materials;
  return materials.map((mat) => {
    const pbr = (mat as any)?.pbrMetallicRoughness;
    if (!pbr?.baseColorTexture) return mat;
    const restPbr = { ...pbr };
    delete restPbr.baseColorTexture;
    return { ...(mat as any), pbrMetallicRoughness: restPbr };
  });
}

export function toGLB(scene: SkpScene, options?: GlbOptions): Uint8Array {
  const prims = scene.glbPrimitives || [];
  const gltfMaterials = scene.gltfMaterials || [];
  // Off by default: embedding the images makes the export several times
  // larger, and a caller who only wants geometry should not pay for it.
  const sceneTextures = options?.textures ? scene.textures || [] : [];

  // Narrow each index buffer to UNSIGNED_SHORT where every index fits,
  // which is the overwhelmingly common case and halves the index data.
  // Computed up front because the sizes decide the binary layout below.
  const encodedIndices = prims.map((prim) => encodeIndices(prim.indices));

  let totalBinaryLength = 0;
  for (let i = 0; i < prims.length; i++) {
    const prim = prims[i];
    totalBinaryLength += prim.positions.byteLength;
    totalBinaryLength += prim.normals.byteLength;
    totalBinaryLength += prim.uvs.byteLength;
    totalBinaryLength += encodedIndices[i].data.byteLength;
    // A 16-bit index buffer of odd length leaves the offset 2-byte
    // aligned; the next primitive's POSITION accessor is float32 and glTF
    // requires 4-byte alignment, so pad here rather than emitting an
    // invalid file.
    totalBinaryLength += (4 - (totalBinaryLength % 4)) % 4;
  }

  // images go after the vertex data, each aligned to 4 bytes as glTF requires
  const imagePlacements: { offset: number; length: number }[] = [];
  for (const tex of sceneTextures) {
    totalBinaryLength += (4 - (totalBinaryLength % 4)) % 4;
    imagePlacements.push({ offset: totalBinaryLength, length: tex.data.length });
    totalBinaryLength += tex.data.length;
  }

  const binaryBuffer = new Uint8Array(totalBinaryLength);
  const bufferViews: any[] = [];
  const accessors: any[] = [];
  const gltfPrimitives: any[] = [];

  let byteOffset = 0;

  for (let primIndex = 0; primIndex < prims.length; primIndex++) {
    const prim = prims[primIndex];
    const posByteOffset = byteOffset;
    binaryBuffer.set(new Uint8Array(prim.positions.buffer, prim.positions.byteOffset, prim.positions.byteLength), posByteOffset);
    byteOffset += prim.positions.byteLength;

    const normByteOffset = byteOffset;
    binaryBuffer.set(new Uint8Array(prim.normals.buffer, prim.normals.byteOffset, prim.normals.byteLength), normByteOffset);
    byteOffset += prim.normals.byteLength;

    const uvByteOffset = byteOffset;
    binaryBuffer.set(new Uint8Array(prim.uvs.buffer, prim.uvs.byteOffset, prim.uvs.byteLength), uvByteOffset);
    byteOffset += prim.uvs.byteLength;

    const encoded = encodedIndices[primIndex];
    const indByteOffset = byteOffset;
    binaryBuffer.set(
      new Uint8Array(encoded.data.buffer, encoded.data.byteOffset, encoded.data.byteLength),
      indByteOffset
    );
    byteOffset += encoded.data.byteLength;
    byteOffset += (4 - (byteOffset % 4)) % 4;

    const posBufferViewIdx = bufferViews.length;
    bufferViews.push({
      buffer: 0,
      byteOffset: posByteOffset,
      byteLength: prim.positions.byteLength,
      target: 34962, // ARRAY_BUFFER
    });

    const normBufferViewIdx = bufferViews.length;
    bufferViews.push({
      buffer: 0,
      byteOffset: normByteOffset,
      byteLength: prim.normals.byteLength,
      target: 34962, // ARRAY_BUFFER
    });

    const uvBufferViewIdx = bufferViews.length;
    bufferViews.push({
      buffer: 0,
      byteOffset: uvByteOffset,
      byteLength: prim.uvs.byteLength,
      target: 34962, // ARRAY_BUFFER
    });

    const indBufferViewIdx = bufferViews.length;
    bufferViews.push({
      buffer: 0,
      byteOffset: indByteOffset,
      byteLength: encoded.data.byteLength,
      target: 34963, // ELEMENT_ARRAY_BUFFER
    });

    const posAccessorIdx = accessors.length;
    let minX = Infinity, minY = Infinity, minZ = Infinity;
    let maxX = -Infinity, maxY = -Infinity, maxZ = -Infinity;
    for (let i = 0; i < prim.positions.length; i += 3) {
      const x = prim.positions[i];
      const y = prim.positions[i + 1];
      const z = prim.positions[i + 2];
      if (x < minX) minX = x; if (x > maxX) maxX = x;
      if (y < minY) minY = y; if (y > maxY) maxY = y;
      if (z < minZ) minZ = z; if (z > maxZ) maxZ = z;
    }

    if (minX === Infinity) {
      minX = minY = minZ = 0;
      maxX = maxY = maxZ = 0;
    }

    accessors.push({
      bufferView: posBufferViewIdx,
      byteOffset: 0,
      componentType: 5126, // FLOAT
      count: prim.positions.length / 3,
      type: 'VEC3',
      min: [minX, minY, minZ],
      max: [maxX, maxY, maxZ],
    });

    const normAccessorIdx = accessors.length;
    accessors.push({
      bufferView: normBufferViewIdx,
      byteOffset: 0,
      componentType: 5126, // FLOAT
      count: prim.normals.length / 3,
      type: 'VEC3',
    });

    const uvAccessorIdx = accessors.length;
    accessors.push({
      bufferView: uvBufferViewIdx,
      byteOffset: 0,
      componentType: 5126, // FLOAT
      count: prim.uvs.length / 2,
      type: 'VEC2',
    });

    const indAccessorIdx = accessors.length;
    accessors.push({
      bufferView: indBufferViewIdx,
      byteOffset: 0,
      componentType: encoded.componentType,
      count: prim.indices.length,
      type: 'SCALAR',
    });

    gltfPrimitives.push({
      attributes: {
        POSITION: posAccessorIdx,
        NORMAL: normAccessorIdx,
        TEXCOORD_0: uvAccessorIdx,
      },
      indices: indAccessorIdx,
      material: prim.materialIndex,
    });
  }

  const gltfImages: any[] = [];
  const gltfTextures: any[] = [];
  for (let i = 0; i < sceneTextures.length; i++) {
    const tex = sceneTextures[i];
    const place = imagePlacements[i];
    binaryBuffer.set(tex.data, place.offset);
    const viewIdx = bufferViews.length;
    bufferViews.push({ buffer: 0, byteOffset: place.offset, byteLength: place.length });
    gltfImages.push({ bufferView: viewIdx, mimeType: tex.mimeType });
    // one sampler for all of them: SketchUp tiles a material's texture across
    // the face, which is REPEAT on both axes
    gltfTextures.push({ sampler: 0, source: gltfImages.length - 1 });
  }

  const gltfMeshes: any[] = [];
  if (gltfPrimitives.length > 0) {
    gltfMeshes.push({
      primitives: gltfPrimitives,
    });
  }

  const gltfJson = {
    asset: {
      version: '2.0',
      generator: 'OpenSKP TypeScript Exporter',
    },
    scene: 0,
    scenes: [
      {
        nodes: gltfMeshes.length > 0 ? [0] : [],
      },
    ],
    nodes: gltfMeshes.length > 0 ? [
      {
        mesh: 0,
      },
    ] : [],
    meshes: gltfMeshes,
    materials: sceneTextures.length > 0 ? gltfMaterials : stripTextureRefs(gltfMaterials),
    buffers: [
      {
        byteLength: totalBinaryLength,
      },
    ],
    bufferViews,
    accessors,
    ...(gltfImages.length > 0
      ? {
          images: gltfImages,
          textures: gltfTextures,
          samplers: [{ wrapS: 10497, wrapT: 10497 }], // REPEAT / REPEAT
        }
      : {}),
  };

  return createGlb(gltfJson, binaryBuffer);
}

/**
 * openskp's canonical JSON export schema, shared with the Python port's
 * `to_dict` (and, from there, Dart/.NET/C++). It used to diverge from
 * Python's in two real ways: this function never included `root` or any
 * per-definition `instances` tree at all, while Python kept only
 * vertex/edge/face *counts*, dropping the full `edges`/`faces` arrays
 * this function always included - so a consumer switching between the
 * two ports got a genuinely different shape, not just missing/extra
 * fields. Both now match this one schema (snake_case keys throughout,
 * including `scene_hierarchy`/`mesh_index` entries, which this function
 * used to emit in TS's own camelCase instead).
 *
 * Note `Instance.layer`/`Instance.properties`/`Instance.children` on the
 * *raw* (pre-bake) `instances` list are deliberately not part of this
 * schema at all - TypeScript's `Instance` type doesn't declare any of
 * them (a definition's placed instances are always a flat list at parse
 * time here), and they're always empty defaults in Python's/Dart's/
 * .NET's parsed model too (never assigned during parsing; only C++
 * actually populates layer/properties - see item 17). The *resolved*,
 * genuinely nested per-instance tree (with correct layer/properties) is
 * available via `scene_hierarchy` (pass the result of {@link buildScene}
 * as `scene`).
 *
 * Export a parsed SkpModel to a metadata JSON object. Pass the result of
 * {@link buildScene} as `scene` to also include mesh/scene-hierarchy data;
 * omit it for a lighter summary covering just the raw model.
 *
 * @param model - Parsed SkpModel
 * @param scene - Optional result of buildScene()
 * @returns Metadata object
 */
export function toJSON(model: SkpModel, scene?: SkpScene): Record<string, unknown> {
  const serializeInstance = (inst: Instance): any => ({
    name: inst.name,
    ref_idx: inst.refIdx,
    guid: inst.guid,
    matrix: inst.matrix,
  });

  const serializeDefinition = (defn: Definition): any => ({
    id: defn.id,
    guid: defn.guid,
    name: defn.name,
    vertex_count: defn.vertices.length,
    edge_count: defn.edges.length,
    face_count: defn.faces.length,
    vertices: defn.vertices.map((v) => ({ id: v.id, x: v.x, y: v.y, z: v.z })),
    edges: defn.edges.map((e) => ({ id: e.id, v1_id: e.v1Id, v2_id: e.v2Id })),
    faces: defn.faces.map((f) => ({
      id: f.id,
      loops: f.loops.map((loop) =>
        loop.map((ce) => ({ edge_id: ce.edgeId, orientation: ce.orientation }))
      ),
      normal: f.normal,
    })),
    instances: defn.instances.map(serializeInstance),
  });

  const definitionsObj: Record<string, any> = {};
  for (const [id, defn] of model.definitions.entries()) {
    definitionsObj[id] = serializeDefinition(defn);
  }

  const layersList = model.layers.map((l) => ({
    name: l.name,
    color: l.color,
    hidden: l.hidden,
  }));

  const materialsList = model.materials.map((m) => ({
    name: m.name,
    color: m.color,
    transparency: m.transparency,
  }));

  const serializeInstanceNode = (node: InstanceNode): any => {
    return {
      name: node.name,
      definition_name: node.definitionName,
      layer: node.layer,
      position_mm: node.positionMm,
      properties: node.properties,
      children: node.children.map(serializeInstanceNode),
    };
  };

  const serializeMeshMetadata = (m: MeshMetadata): any => ({
    name: m.name,
    definition_name: m.definitionName,
    layer: m.layer,
    position_mm: m.positionMm,
    properties: m.properties,
    path: m.path,
  });

  const meshIndexObj: Record<string, any> = {};
  if (scene) {
    for (const [name, m] of Object.entries(scene.meshIndex)) {
      meshIndexObj[name] = serializeMeshMetadata(m);
    }
  }

  return {
    format_version: '1.0',
    sketchup_version: model.version,
    units: model.units,
    total_definitions: model.definitions.size,
    total_layers: model.layers.length,
    total_meshes: scene ? Object.keys(scene.meshIndex).length : 0,
    root: serializeDefinition(model.root),
    definitions: definitionsObj,
    layers: layersList,
    materials: materialsList,
    mesh_index: meshIndexObj,
    scene_hierarchy: scene ? serializeInstanceNode(scene.sceneHierarchy) : null,
  };
}

/**
 * SkpFile wrapper class.
 */
export class SkpFile {
  private buffer: ArrayBuffer;

  constructor(buffer: ArrayBuffer) {
    this.buffer = buffer;
  }

  static fromBuffer(buffer: ArrayBuffer): SkpFile {
    return new SkpFile(buffer);
  }

  static open(filePath: string): SkpFile {
    if (typeof process !== 'undefined' && process.versions && process.versions.node) {
      const fs = require('fs');
      const path = require('path');
      if (!fs.existsSync(filePath)) {
        throw new Error(`File not found: ${filePath}`);
      }
      const ext = path.extname(filePath);
      if (ext.toLowerCase() !== '.skp') {
        throw new Error(`Expected a .skp file, got: ${ext}`);
      }
      const buffer = fs.readFileSync(filePath);
      const arrayBuffer = buffer.buffer.slice(buffer.byteOffset, buffer.byteOffset + buffer.byteLength);
      return new SkpFile(arrayBuffer);
    } else {
      throw new Error('SkpFile.open is only supported in Node.js environment');
    }
  }

  /** Fast, memory-light parse: raw per-definition geometry, no scene-graph
   * instancing resolved. See {@link buildScene} for a triangulated,
   * world-space scene ready for rendering or GLB export.
   * @param options - Optional progress/log callbacks (see {@link ParseOptions}) */
  parse(options?: ParseOptions): SkpModel {
    return parseSkp(this.buffer, options);
  }

  /** Bake every placed instance into world-space, triangulated mesh data.
   * Independent of parse() - re-parses the raw TLV data on its own rather
   * than reusing a prior parse() call, so calling only parse() never pays
   * for this heavier computation.
   * @param options - Optional progress/log callbacks (see {@link ParseOptions}) */
  buildScene(options?: ParseOptions & SceneOptions): SkpScene {
    return buildScene(this.buffer, options);
  }

  /** Build the placed scene graph with instancing PRESERVED: unique
   * geometry once, plus one transform per placement. See
   * {@link buildInstancedScene} for when to prefer this over buildScene().
   * @param options - Optional progress/log callbacks (see {@link ParseOptions}) */
  buildInstancedScene(options?: ParseOptions & SceneOptions): InstancedScene {
    return buildInstancedScene(this.buffer, options);
  }

  /** The preview image SketchUp stored in the file, or null when it has
   * none. Cheap: reads container metadata only, never geometry.
   * @param options - Optional progress/log callbacks */
  thumbnail(options?: ParseOptions): SkpThumbnail | null {
    return extractThumbnail(this.buffer, options);
  }
}
