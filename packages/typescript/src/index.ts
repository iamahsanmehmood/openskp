import { extractSkpContents } from './vff';
import { parseTlvRecursive, readF64, readU32, parseVarInt } from './parser';
import { transformPoint, multiplyMatrices } from './transforms';
import { triangulateFace3D } from './triangulator';
import {
  GeometryBuilder,
  collectLayers,
  collectDefs,
  extractGeometryFromNodes,
  extractDynamicProperties,
  reconstructLoopVertices,
  parseMaterialXml,
  findChildTag,
} from './geometry';

declare const process: any;
declare const require: any;

export interface SkpModel {
  version: string;
  definitions: Map<number, Definition>;
  layers: Layer[];
  materials: Material[];
  sceneHierarchy: InstanceNode;
  meshIndex: Record<string, MeshMetadata>;
}

export interface Definition {
  id: number;
  guid: string;
  name: string;
  vertices: Vertex[];
  edges: Edge[];
  faces: Face[];
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
}

export interface Face {
  id: number;
  loops: CoEdge[][];
  normal: [number, number, number];
}

export interface CoEdge {
  edgeId: number;
  orientation: number;
}

export interface Layer {
  name: string;
  color: { r: number; g: number; b: number };
}

export interface Material {
  name: string;
  color: { r: number; g: number; b: number };
  transparency: number;
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

/**
 * Parse a SketchUp (.skp) file from an ArrayBuffer.
 *
 * @param buffer - The raw file contents as an ArrayBuffer
 * @returns Parsed SkpModel with full geometry and metadata
 */
export function parseSkp(buffer: ArrayBuffer): SkpModel {
  const data = new Uint8Array(buffer);

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
          const matObj = {
            name: parsedMat.name,
            color: { r: parsedMat.r, g: parsedMat.g, b: parsedMat.b },
            transparency: parsedMat.trans,
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
            const decoder = new TextDecoder('ascii');
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
    builder: rootBuilder,
  });

  // 7. Instantiate scene hierarchy and gather mesh metadata & GLB primitives
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

        const triangles = triangulateFace3D(builder.vertices, loops, fData.normal);
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
      const d007 = inst.children.find((c) => c.tag === 'D007');
      let properties: Record<string, string> = {};

      if (d007) {
        const d207 = d007.children.find((c) => c.tag === 'D207');
        if (d207 && d207.payload.length > 0) {
          const p = d207.payload;
          let lId: number;
          if (p.length === 1) {
            lId = p[0];
          } else {
            lId = parseVarInt(p, 0, p.length);
          }
          lName = layerIdToName.get(lId) || parentLayer;
        }

        const d107 = d007.children.find((c) => c.tag === 'D107');
        if (d107) {
          const instMatId = parseVarInt(d107.payload, 0, d107.payload.length);
          const matName = materialIdToName.get(instMatId);
          if (matName) {
            const mat = materialsMap.get(matName) || materialsByFolder.get(matName);
            if (mat) {
              instColor = mat.color;
            }
          }
        }

        try {
          properties = extractDynamicProperties(d007);
        } catch (e) {
          // Ignore
        }
      }

      const instName = inst.name || `Component_${refIdx}`;
      const fullPathName = `${pathName} / ${instName}`;
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

  const finalLayersList: Layer[] = Array.from(layerColors.entries()).map(([name, c]) => ({
    name,
    color: { r: c[0], g: c[1], b: c[2] },
  }));

  const finalMaterialsList: Material[] = Array.from(materialsMap.values());

  const sceneHierarchy: InstanceNode = {
    name: 'ROOT',
    definitionName: 'ROOT_MODEL',
    layer: 'Layer0',
    positionMm: [0, 0, 0],
    properties: {},
    children: rootChildren,
  };

  const finalDefinitions = new Map<number, Definition>();
  for (const [id, d] of defsDict.entries()) {
    if (typeof id === 'number') {
      const vertices: Vertex[] = Array.from(d.builder.vertices.entries()).map(([vId, [x, y, z]]) => ({
        id: vId,
        x,
        y,
        z,
      }));
      const edges: Edge[] = Array.from(d.builder.edges.entries()).map(([eId, [v1, v2]]) => ({
        id: eId,
        v1Id: v1 ?? 0,
        v2Id: v2 ?? 0,
      }));
      const faces: Face[] = Array.from(d.builder.faces.entries()).map(([fId, fData]) => ({
        id: fId,
        loops: fData.loops,
        normal: fData.normal,
      }));

      finalDefinitions.set(id, {
        id,
        guid: d.guid,
        name: d.name,
        vertices,
        edges,
        faces,
      });
    }
  }

  const model: SkpModel = {
    version,
    definitions: finalDefinitions,
    layers: finalLayersList,
    materials: finalMaterialsList,
    sceneHierarchy,
    meshIndex,
  };

  // Attach internal GLB data
  (model as any)._glbPrimitives = glbPrimitives;
  (model as any)._gltfMaterials = gltfMaterials;

  return model;
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
