import { extractSkpContents } from './vff';
import { parseTlvRecursive, readU32, parseVarInt } from './parser';
import {
  GeometryBuilder,
  collectLayers,
  collectDefs,
  extractGeometryFromNodes,
  parseMaterialXml,
  parseStyleXml,
  resolveTextureBytes,
  findChildTag,
} from './geometry';
import {
  SkpModel,
  Style,
  Material,
  Texture,
  InstanceNode,
  ParsedRawData,
  buildModelFromParsed,
} from './model';
import { isLegacy, parseLegacySkp } from './legacy';

export * from './model';

declare const process: any;
declare const require: any;

/**
 * Parse a SketchUp (.skp) file from an ArrayBuffer.
 *
 * Transparently handles both the modern VFF/ZIP container (SketchUp 2021+)
 * and the classic pre-2021 MFC CArchive container (SketchUp 2013-2020).
 *
 * @param buffer - The raw file contents as an ArrayBuffer
 * @returns Parsed SkpModel with full geometry and metadata
 */
export function parseSkp(buffer: ArrayBuffer): SkpModel {
  const data = new Uint8Array(buffer);

  if (isLegacy(data)) {
    return parseLegacySkp(data);
  }

  // 1. Extract SKP contents from VFF/ZIP container
  const contents = extractSkpContents(data);
  const version = contents.version;
  const modelData = contents.modelData;
  const materialFiles = contents.materialFiles;

  // 2. Parse XML materials to populate layer colors and materials
  const layerColors = new Map<string, [number, number, number]>();
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
            color: { r: parsedMat.r, g: parsedMat.g, b: parsedMat.b },
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
          }
        }
      } catch (e) {
        // Ignore XML errors
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
        // Ignore XML errors
      }
    }
  }

  // 3. Parse TLV recursively starting at offset 0, handling the F401 container tag wrapper
  let elements = parseTlvRecursive(modelData, 0, modelData.length);
  if (elements.length === 1 && elements[0].tag === 'F401') {
    elements = elements[0].children;
  }

  // 4. Collect layer ID to name mapping
  const layerIdToName = collectLayers(elements);
  if (!layerIdToName.has(1)) {
    layerIdToName.set(1, 'Layer0');
  }
  if (!layerColors.has('Layer0')) {
    layerColors.set('Layer0', [136, 136, 136]);
  }

  // 4b. Collect material ID to name mapping
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
            mName = decoder.decode(nameNode.payload).replace(/\0/g, '').trim();
          } catch (e) {
            // Ignore
          }
          if (mName) {
            materialIdToName.set(mId, mName);
          }
        }
      }
      if (el.children && el.children.length > 0) {
        collectMaterialIds(el.children);
      }
    }
  }
  collectMaterialIds(elements);

  // 5. Collect component definitions
  const defsDict = collectDefs(elements);

  // 6. Collect root geometry
  const rootBuilder = new GeometryBuilder();
  for (const el of elements) {
    if (el.tag === 'F601') {
      extractGeometryFromNodes(el.children, rootBuilder);
    }
  }
  defsDict.set('ROOT', {
    guid: 'ROOT',
    name: 'ROOT_MODEL',
    isImage: false,
    alwaysFacesCamera: false,
    builder: rootBuilder,
  });

  const parsed: ParsedRawData = {
    version,
    layerColors,
    layerIdToName,
    materialIdToName,
    materialsMap,
    materialsByFolder,
    styles,
    defsDict,
  };
  return buildModelFromParsed(parsed);
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
 * Export a parsed SkpModel to GLB (binary glTF 2.0) format.
 *
 * @param model - Parsed SkpModel
 * @returns GLB file as Uint8Array
 */
export function toGLB(model: SkpModel): Uint8Array {
  const prims = (model as any)._glbPrimitives || [];
  const gltfMaterials = (model as any)._gltfMaterials || [];

  let totalBinaryLength = 0;
  for (const prim of prims) {
    totalBinaryLength += prim.positions.byteLength;
    totalBinaryLength += prim.normals.byteLength;
    totalBinaryLength += prim.indices.byteLength;
  }

  const binaryBuffer = new Uint8Array(totalBinaryLength);
  const bufferViews: any[] = [];
  const accessors: any[] = [];
  const gltfPrimitives: any[] = [];

  let byteOffset = 0;

  for (const prim of prims) {
    const posByteOffset = byteOffset;
    binaryBuffer.set(new Uint8Array(prim.positions.buffer, prim.positions.byteOffset, prim.positions.byteLength), posByteOffset);
    byteOffset += prim.positions.byteLength;

    const normByteOffset = byteOffset;
    binaryBuffer.set(new Uint8Array(prim.normals.buffer, prim.normals.byteOffset, prim.normals.byteLength), normByteOffset);
    byteOffset += prim.normals.byteLength;

    const indByteOffset = byteOffset;
    binaryBuffer.set(new Uint8Array(prim.indices.buffer, prim.indices.byteOffset, prim.indices.byteLength), indByteOffset);
    byteOffset += prim.indices.byteLength;

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

    const indBufferViewIdx = bufferViews.length;
    bufferViews.push({
      buffer: 0,
      byteOffset: indByteOffset,
      byteLength: prim.indices.byteLength,
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

    const indAccessorIdx = accessors.length;
    accessors.push({
      bufferView: indBufferViewIdx,
      byteOffset: 0,
      componentType: 5125, // UNSIGNED_INT
      count: prim.indices.length,
      type: 'SCALAR',
    });

    gltfPrimitives.push({
      attributes: {
        POSITION: posAccessorIdx,
        NORMAL: normAccessorIdx,
      },
      indices: indAccessorIdx,
      material: prim.materialIndex,
    });
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
    materials: gltfMaterials,
    buffers: [
      {
        byteLength: totalBinaryLength,
      },
    ],
    bufferViews,
    accessors,
  };

  return createGlb(gltfJson, binaryBuffer);
}

/**
 * Export a parsed SkpModel to a metadata JSON object.
 *
 * @param model - Parsed SkpModel
 * @returns Metadata object
 */
export function toJSON(model: SkpModel): Record<string, unknown> {
  const definitionsObj: Record<string, any> = {};
  for (const [id, defn] of model.definitions.entries()) {
    definitionsObj[id] = {
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
    };
  }

  const layersList = model.layers.map((l) => ({
    name: l.name,
    color: l.color,
  }));

  const materialsList = model.materials.map((m) => ({
    name: m.name,
    color: m.color,
    transparency: m.transparency,
  }));

  const serializeInstanceNode = (node: InstanceNode): any => {
    return {
      name: node.name,
      definitionName: node.definitionName,
      layer: node.layer,
      positionMm: node.positionMm,
      properties: node.properties,
      children: node.children.map(serializeInstanceNode),
    };
  };

  return {
    format_version: '1.0',
    sketchup_version: model.version,
    total_definitions: model.definitions.size,
    total_meshes: Object.keys(model.meshIndex).length,
    total_layers: model.layers.length,
    layers: layersList,
    materials: materialsList,
    mesh_index: model.meshIndex,
    scene_hierarchy: serializeInstanceNode(model.sceneHierarchy),
    definitions: definitionsObj,
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
      const buffer = fs.readFileSync(filePath);
      const arrayBuffer = buffer.buffer.slice(buffer.byteOffset, buffer.byteOffset + buffer.byteLength);
      return new SkpFile(arrayBuffer);
    } else {
      throw new Error('SkpFile.open is only supported in Node.js environment');
    }
  }

  parse(): SkpModel {
    return parseSkp(this.buffer);
  }
}
