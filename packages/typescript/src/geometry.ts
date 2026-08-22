import { TlvNode, readF64, readU32, parseVarInt, parseTlvRecursive } from './parser';
import { ParseOptions, emitLog } from './observability';
import { EdgeFlagStore } from './edge-flags';

export interface GeometryBuilderInstance {
  offset: number;
  refGuid: string;
  refIdx: number;
  name: string;
  matrix: number[];
  materialId: number | null;
  /** Layer ID this instance belongs to (D007 -> D207), or null. Internal -
   * used for scene-graph layer inheritance, not part of the public API. */
  layerId?: number | null;
  hidden?: boolean;
  children: TlvNode[];
  /** Dynamic Component properties precomputed for legacy (pre-2021 MFC)
   * instances (see legacy.ts's extractLegacyDynamicProperties) - VFF
   * instances don't set this, since their properties come from a lazy
   * D007/DC05 TLV walk over `children` instead (see model.ts). */
  properties?: Record<string, string>;
}

export interface GeometryBuilderFace {
  loops: { edgeId: number; orientation: number }[][];
  normal: [number, number, number];
  materialId?: number | null;
  backMaterialId?: number | null;
  uvTransform?: number[] | null;
  uvTransformBack?: number[] | null;
  uvProjected?: boolean;
  uvProjectedBack?: boolean;
  hidden?: boolean;
}

export class GeometryBuilder {
  vertices = new Map<number, [number, number, number]>(); // id -> [x, y, z]
  edges = new Map<number, [number | null, number | null]>(); // id -> [v1, v2]
  /** Edge id -> display flag byte (D307). Backed by a Uint8Array rather
   * than a Map: the value is one byte, and a Map entry costs ~30. */
  edgeFlags = new EdgeFlagStore();
  faces = new Map<number, GeometryBuilderFace>(); // id -> face data
  instances: GeometryBuilderInstance[] = [];
  sectionPlanes: { plane: [number, number, number, number]; name: string; label: string; hidden: boolean }[] = [];
  texts: { text: string; hidden: boolean }[] = [];
  dimensions: { text: string; hidden: boolean }[] = [];
}

export interface ParsedDefinition {
  guid: string;
  name: string;
  isImage: boolean;
  alwaysFacesCamera: boolean;
  shadowsFaceSun?: boolean;
  sectionPlanes?: { plane: [number, number, number, number]; name: string; label: string; hidden: boolean }[];
  texts?: { text: string; hidden: boolean }[];
  dimensions?: { text: string; hidden: boolean }[];
  builder: GeometryBuilder;
}

export function findChildTag(nodes: TlvNode[], target: string): TlvNode | null {
  for (const n of nodes) {
    if (n.tag === target) {
      return n;
    }
    const res = findChildTag(n.children, target);
    if (res) return res;
  }
  return null;
}

export function findAllNodesRec(nodes: TlvNode[], targetTag: string, results: TlvNode[]): void {
  for (const n of nodes) {
    if (n.tag === targetTag) {
      results.push(n);
    }
    findAllNodesRec(n.children, targetTag, results);
  }
}

export function extractEntityId(node: TlvNode): number | null {
  for (const child of node.children) {
    if (child.tag === 'DE05') {
      return parseVarInt(child.payload, 0, child.payload.length);
    }
    if (child.tag === 'DC05') {
      const payload = child.payload;
      if (payload.length >= 6 && payload[0] === 0xDE && payload[1] === 0x05) {
        const de05Len = readU32(payload, 2);
        return parseVarInt(payload, 6, de05Len);
      } else {
        return parseVarInt(payload, 0, payload.length);
      }
    }
  }
  for (const child of node.children) {
    const res = extractEntityId(child);
    if (res !== null) return res;
  }
  return null;
}

/** Walk a raw payload as a flat TLV sequence; returns [tag, body] pairs. */
function tlvFlat(payload: Uint8Array): [string, Uint8Array][] {
  let pos = 0;
  const out: [string, Uint8Array][] = [];
  while (pos <= payload.length - 6) {
    const tagBytes = payload.subarray(pos, pos + 2);
    let tagHex = '';
    for (let i = 0; i < 2; i++) {
      const h = tagBytes[i].toString(16).toUpperCase();
      tagHex += h.length === 1 ? '0' + h : h;
    }
    const size = readU32(payload, pos + 2);
    if (pos + 6 + size > payload.length) break;
    out.push([tagHex, payload.subarray(pos + 6, pos + 6 + size)]);
    pos += 6 + size;
  }
  return out;
}

function findFlat(seq: [string, Uint8Array][], tag: string): Uint8Array | null {
  for (const [t, body] of seq) {
    if (t === tag) return body;
  }
  return null;
}

/**
 * Per-face texture-mapping matrices from a face's DC05 entity-info blob.
 *
 * A positioned / photo-fitted texture stores its mapping per face under
 * DC05 -> DD05 -> B136 -> B236 -> 1027 -> 1127 (front) / 1227 (back)
 * -> 1327 -> 1527: a 3x3 row-major matrix of f64 that maps
 * texture space -> face plane; consumers invert it to get UVs
 * (see the Face.uvTransform docs in index.ts for the exact recipe).
 *
 * Returns [front, back] - each a 9-element array of numbers, or null when
 * the face carries no positioned mapping on that side.
 */
export function extractUvTransforms(
  dc05Payload: Uint8Array
): [number[] | null, number[] | null] {
  const dd05 = findFlat(tlvFlat(dc05Payload), 'DD05');
  if (dd05 === null) return [null, null];
  const b136 = findFlat(tlvFlat(dd05), 'B136');
  if (b136 === null) return [null, null];
  const b236 = findFlat(tlvFlat(b136), 'B236');
  if (b236 === null) return [null, null];
  const t1027 = findFlat(tlvFlat(b236), '1027');
  if (t1027 === null) return [null, null];
  const sides = tlvFlat(t1027);
  const result: (number[] | null)[] = [];
  for (const sideTag of ['1127', '1227']) {
    const side = findFlat(sides, sideTag);
    let mat: number[] | null = null;
    if (side !== null) {
      const t1327 = findFlat(tlvFlat(side), '1327');
      if (t1327 !== null) {
        const t1527 = findFlat(tlvFlat(t1327), '1527');
        if (t1527 !== null && t1527.length === 72) {
          mat = [];
          for (let i = 0; i < 9; i++) {
            mat.push(readF64(t1527, i * 8));
          }
        }
      }
    }
    result.push(mat);
  }
  return [result[0], result[1]];
}

export function extractGeometryFromNodes(
  elements: TlvNode[],
  builder: GeometryBuilder,
  options?: ParseOptions
): void {
  for (const el of elements) {
    const tag = el.tag;

    if (tag === 'C409') {
      const vId = extractEntityId(el);
      const c509 = findChildTag(el.children, 'C509');
      if (vId !== null && c509 && c509.payload.length >= 24) {
        const x = readF64(c509.payload, 0);
        const y = readF64(c509.payload, 8);
        const z = readF64(c509.payload, 16);
        builder.vertices.set(vId, [x, y, z]);
      }
    } else if (tag === 'B80B') {
      const eId = extractEntityId(el);
      if (eId !== null) {
        const v1Node = findChildTag(el.children, 'B90B');
        const v2Node = findChildTag(el.children, 'BA0B');
        const v1 = v1Node ? parseVarInt(v1Node.payload, 0, v1Node.payload.length) : null;
        const v2 = v2Node ? parseVarInt(v2Node.payload, 0, v2Node.payload.length) : null;
        builder.edges.set(eId, [v1, v2]);

        // D007 -> D307 = edge display flags: base 0x06, plus 0x01 hidden,
        // 0x08|0x10 soft/smooth.
        const d007 = el.children.find((c) => c.tag === 'D007');
        if (d007) {
          const d307 = d007.children.find((c) => c.tag === 'D307');
          if (d307 && d307.payload.length > 0) {
            builder.edgeFlags.set(eId, d307.payload[0]);
          }
        }
      }
    } else if (tag === 'AC0D') {
      const fId = extractEntityId(el);
      if (fId !== null) {
        let normal: [number, number, number] = [0.0, 0.0, 1.0];
        const ad0d = findChildTag(el.children, 'AD0D');
        if (ad0d && ad0d.payload.length >= 24) {
          const nx = readF64(ad0d.payload, 0);
          const ny = readF64(ad0d.payload, 8);
          const nz = readF64(ad0d.payload, 16);
          normal = [nx, ny, nz];
        }

        const ae0d = findChildTag(el.children, 'AE0D');
        const loops: { edgeId: number; orientation: number }[][] = [];
        if (ae0d) {
          const loopNodes: TlvNode[] = [];
          findAllNodesRec(ae0d.children, '9411', loopNodes);
          for (const ln of loopNodes) {
            const coEdges: { edgeId: number; orientation: number }[] = [];
            const coNodes: TlvNode[] = [];
            findAllNodesRec(ln.children, 'A00F', coNodes);
            for (const cn of coNodes) {
              const payload = cn.payload;
              let edgeId: number | null = null;
              let orient: number | null = null;
              let subPos = 0;
              while (subPos < payload.length - 6) {
                const subSize = readU32(payload, subPos + 2);
                if (subPos + 6 + subSize <= payload.length) {
                  const val = parseVarInt(payload, subPos + 6, subSize);
                  if (payload[subPos] === 0xA1 && payload[subPos + 1] === 0x0F) {
                    edgeId = val;
                  } else if (payload[subPos] === 0xA2 && payload[subPos + 1] === 0x0F) {
                    orient = val;
                  }
                }
                subPos += 6 + subSize;
              }
              if (edgeId !== null && orient !== null) {
                coEdges.push({ edgeId, orientation: orient });
              }
            }
            if (coEdges.length > 0) {
              loops.push(coEdges);
            }
          }
        }
        let faceMatId: number | null = null;
        let uvFront: number[] | null = null;
        let uvBack: number[] | null = null;
        let faceHidden = false;
        const d007 = el.children.find((c) => c.tag === 'D007');
        if (d007) {
          const d107 = d007.children.find((c) => c.tag === 'D107');
          if (d107) {
            faceMatId = parseVarInt(d107.payload, 0, d107.payload.length);
          }
          const dc05 = d007.children.find((c) => c.tag === 'DC05');
          if (dc05) {
            [uvFront, uvBack] = extractUvTransforms(dc05.payload);
          }
          // D307 = display flags, same record edges already read (base
          // 0x06, +0x01 hidden) - faces carry the identical tag under
          // their own D007 container.
          const d307 = d007.children.find((c) => c.tag === 'D307');
          if (d307 && d307.payload.length > 0) {
            faceHidden = (d307.payload[0] & 0x01) !== 0;
          }
        }
        // Back-side material: the AF0D child of the face node (a face
        // painted only on its back - common when the author paints the
        // visible side of a downward-facing cap - carries AF0D but no
        // D107).
        let backMatId: number | null = null;
        const af0d = el.children.find((c) => c.tag === 'AF0D');
        if (af0d && af0d.payload.length > 0) {
          backMatId = parseVarInt(af0d.payload, 0, af0d.payload.length);
        }
        builder.faces.set(fId, {
          loops,
          normal,
          materialId: faceMatId,
          backMaterialId: backMatId,
          uvTransform: uvFront,
          uvTransformBack: uvBack,
          hidden: faceHidden,
        });
      }
    } else if (tag === '6419') {
      const nodesToSearch = el.children.length > 0 ? el.children : [el];
      let guid: string | null = null;
      let defIdx: number | null = null;
      let name: string | null = null;
      const matrix: number[] = [];

      const guidNode = findChildTag(nodesToSearch, '6819');
      if (guidNode && guidNode.payload.length === 16) {
        let hex = '';
        for (let i = 0; i < 16; i++) {
          const h = guidNode.payload[i].toString(16).toUpperCase();
          hex += h.length === 1 ? '0' + h : h;
        }
        guid = hex;
      }

      const defIdxNode = findChildTag(nodesToSearch, '6719');
      if (defIdxNode) {
        defIdx = parseVarInt(defIdxNode.payload, 0, defIdxNode.payload.length);
      }

      const nameNode = findChildTag(nodesToSearch, '6519');
      if (nameNode) {
        try {
          const decoder = new TextDecoder('utf-8');
          name = decoder.decode(nameNode.payload).replace(/\0/g, '').trim();
        } catch (e) {
          name = '';
          emitLog(options, 'debug', `Failed to decode instance name: ${(e as Error).message}`);
        }
      }

      const matNode = findChildTag(nodesToSearch, '6619');
      if (matNode && matNode.payload.length >= 104) {
        for (let idx = 0; idx < 13; idx++) {
          matrix.push(readF64(matNode.payload, idx * 8));
        }
      }

      // Instance-level material (SketchUp "paint the component"): same
      // D007/D107 structure faces use. Faces whose own materialId is null
      // inherit this - the SDK resolves that inheritance when exporting,
      // so consumers need the raw value to do the same.
      let instMatId: number | null = null;
      let instLayerId: number | null = null;
      let instHidden = false;
      const d007 = el.children.find((c) => c.tag === 'D007');
      if (d007) {
        const d107 = d007.children.find((c) => c.tag === 'D107');
        if (d107) {
          instMatId = parseVarInt(d107.payload, 0, d107.payload.length);
        }
        const d207 = d007.children.find((c) => c.tag === 'D207');
        if (d207 && d207.payload.length > 0) {
          const p = d207.payload;
          instLayerId = p.length === 1 ? p[0] : parseVarInt(p, 0, p.length);
        }
        // D307 = display flags, same record edges/faces already read
        // (base 0x06, +0x01 hidden).
        const d307 = d007.children.find((c) => c.tag === 'D307');
        if (d307 && d307.payload.length > 0) {
          instHidden = (d307.payload[0] & 0x01) !== 0;
        }
      }

      builder.instances.push({
        offset: el.offset,
        refGuid: guid || '',
        refIdx: defIdx !== null ? defIdx : -1,
        name: name || '',
        matrix: matrix,
        materialId: instMatId,
        layerId: instLayerId,
        hidden: instHidden,
        children: el.children,
      });
    } else if (el.children && el.children.length > 0) {
      extractGeometryFromNodes(el.children, builder, options);
    }
  }
}

export function collectLayers(
  nodes: TlvNode[],
  layerIdToName: Map<number, string> = new Map(),
  options?: ParseOptions
): Map<number, string> {
  for (const el of nodes) {
    if (el.tag === '993A') {
      for (const child of el.children) {
        if (child.tag === '8C3C') {
          const dc05 = findChildTag(child.children, 'DC05');
          const nameNode = findChildTag(child.children, '8D3C');
          if (dc05 && nameNode) {
            const payload = dc05.payload;
            let lId: number;
            if (payload.length >= 6 && payload[0] === 0xDE && payload[1] === 0x05) {
              const de05Len = readU32(payload, 2);
              lId = parseVarInt(payload, 6, de05Len);
            } else {
              lId = parseVarInt(payload, 0, payload.length);
            }
            let lName = '';
            try {
              const decoder = new TextDecoder('utf-8');
              lName = decoder.decode(nameNode.payload).replace(/\0/g, '').trim();
            } catch (e) {
              emitLog(options, 'debug', `Failed to decode layer name for id ${lId}: ${(e as Error).message}`);
            }
            layerIdToName.set(lId, lName);
          }
        }
      }
    }
    if (el.children && el.children.length > 0) {
      collectLayers(el.children, layerIdToName, options);
    }
  }
  return layerIdToName;
}

export function collectDefs(
  nodes: TlvNode[],
  defsDict: Map<number | string, ParsedDefinition> = new Map(),
  options?: ParseOptions
): Map<number | string, ParsedDefinition> {
  for (const el of nodes) {
    if (el.tag === '7C15') {
      let guid: string | null = null;
      let name: string | null = null;
      let isImage = false;
      let facesCamera = false;
      let shadowsFaceSun = false;
      for (const child of el.children) {
        if (child.tag === '7D15' && child.payload.length === 16) {
          let hex = '';
          for (let i = 0; i < 16; i++) {
            const h = child.payload[i].toString(16).toUpperCase();
            hex += h.length === 1 ? '0' + h : h;
          }
          guid = hex;
        } else if (child.tag === '7E15') {
          try {
            const decoder = new TextDecoder('utf-8');
            name = decoder.decode(child.payload).replace(/\0/g, '').trim();
          } catch (e) {
            name = '';
            emitLog(options, 'debug', `Failed to decode definition name: ${(e as Error).message}`);
          }
        } else if (child.tag === '8315' && child.payload.length > 0) {
          // Definition kind: observed 0/1 for ordinary component/group
          // definitions, 2 for the quad definition backing an Image entity.
          isImage = parseVarInt(child.payload, 0, child.payload.length) === 2;
        } else if (child.tag === '581B') {
          // Component behavior flags: sub-TLV 5D1B == 1 marks "always
          // faces camera" (2D people/tree cut-outs); its companion 5E1B
          // is "shadows face sun".
          let pos = 0;
          const pl = child.payload;
          while (pos <= pl.length - 6) {
            const subSize = readU32(pl, pos + 2);
            if (pos + 6 + subSize > pl.length) break;
            // Tag 0x5d 0x1b contains component definition flags (1 = always faces camera)
            if (pl[pos] === 0x5d && pl[pos + 1] === 0x1b && subSize >= 1) {
              facesCamera = parseVarInt(pl, pos + 6, subSize) === 1;
            } else if (pl[pos] === 0x5e && pl[pos + 1] === 0x1b && subSize >= 1) {
              shadowsFaceSun = parseVarInt(pl, pos + 6, subSize) === 1;
            }
            pos += 6 + subSize;
          }
        }
      }
      const entId = extractEntityId(el);
      if (entId !== null) {
        const builder = new GeometryBuilder();
        extractGeometryFromNodes(el.children, builder, options);
        defsDict.set(entId, {
          guid: guid || '',
          name: name || '',
          isImage,
          alwaysFacesCamera: facesCamera,
          shadowsFaceSun,
          builder,
        });
      }
    }
    if (el.children && el.children.length > 0) {
      collectDefs(el.children, defsDict, options);
    }
  }
  return defsDict;
}

/**
 * Extract Dynamic Component attribute key-value pairs from a D007 container node.
 *
 * Dynamic properties are stored in a nested TLV hierarchy under the DC05 tag:
 * - Container tags (DD05, B536, B136, B236, B336, B036, A438) wrap property sub-trees.
 * - Tag B636 contains the attribute key name (UTF-8 string).
 * - Tag AD38 contains the attribute value (UTF-8 string).
 */
export function extractDynamicProperties(d007: TlvNode, options?: ParseOptions): Record<string, string> {
  const dc05 = d007.children.find((c) => c.tag === 'DC05');
  if (!dc05) {
    return {};
  }
  // TLV container tags nesting the dynamic property key/value nodes
  const propContainerTags = new Set<string>([
    'DD05',
    'B536',
    'B136',
    'B236',
    'B336',
    'B036',
    'A438',
  ]);
  const propElements = parseTlvRecursive(
    dc05.payload,
    0,
    dc05.payload.length,
    propContainerTags
  );
  const properties: Record<string, string> = {};
  let currentKey: string | null = null;

  function extractProps(nodes: TlvNode[]) {
    for (const n of nodes) {
      const tag = n.tag;
      if (tag === 'B636') {
        // Property key name (UTF-8 string)
        try {
          const decoder = new TextDecoder('utf-8');
          currentKey = decoder.decode(n.payload).replace(/\0/g, '').trim();
        } catch (e) {
          currentKey = null;
          emitLog(options, 'debug', `Failed to decode dynamic property key: ${(e as Error).message}`);
        }
      } else if (tag === 'AD38' && currentKey) {
        // Property value (UTF-8 string) matching preceding key
        try {
          const decoder = new TextDecoder('utf-8');
          const val = decoder.decode(n.payload).replace(/\0/g, '').trim();
          properties[currentKey] = val;
        } catch (e) {
          emitLog(
            options, 'debug',
            `Failed to decode dynamic property value for key ${currentKey}: ${(e as Error).message}`
          );
        }
        currentKey = null;
      }
      if (n.children && n.children.length > 0) {
        extractProps(n.children);
      }
    }
  }

  extractProps(propElements);
  return properties;
}

export function reconstructLoopVertices(
  loop: { edgeId: number; orientation: number }[],
  edges: Map<number, [number | null, number | null]>
): number[] {
  const loopVerts: number[] = [];
  for (const { edgeId, orientation } of loop) {
    const edge = edges.get(edgeId);
    if (edge) {
      const [v1, v2] = edge;
      const vStart = orientation === 1 ? v1 : v2;
      if (vStart !== null) {
        if (loopVerts.length === 0 || loopVerts[loopVerts.length - 1] !== vStart) {
          loopVerts.push(vStart);
        }
      }
    }
  }
  if (loopVerts.length > 1 && loopVerts[0] === loopVerts[loopVerts.length - 1]) {
    loopVerts.pop();
  }
  return loopVerts;
}

export function parseMaterialXml(xmlText: string): {
  name: string;
  r: number;
  g: number;
  b: number;
  trans: number;
  colorized: boolean;
  colorizeType: number;
  hasTexture: boolean;
  textureFilename: string;
  xScale: number;
  yScale: number;
  imagePath: string;
} | null {
  const match = xmlText.match(/<(?:[a-zA-Z0-9_]+:)?material\b([^>]*)\/?>/);
  if (!match) return null;
  const attrsString = match[1];

  const getAttr = (name: string): string | null => {
    const attrRegex = new RegExp(`\\b${name}\\s*=\\s*(?:"([^"]*)"|'([^']*)')`);
    const m = attrsString.match(attrRegex);
    return m ? (m[1] !== undefined ? m[1] : m[2]) : null;
  };

  const name = getAttr('name') || 'unknown';
  const colorRed = parseInt(getAttr('colorRed') || '128', 10);
  const colorGreen = parseInt(getAttr('colorGreen') || '128', 10);
  const colorBlue = parseInt(getAttr('colorBlue') || '128', 10);

  // 'trans' is a TRANSPARENCY (0 = opaque, 1 = fully transparent) and only
  // applies when useTrans="1"; otherwise it's a leftover default and the
  // material is fully opaque. Expose the resulting OPACITY as 1 - trans
  // (e.g. SketchUp's "Translucent Glass Blue", 70% opacity, stores
  // trans="0.3").
  let trans: number;
  if (getAttr('useTrans') === '1') {
    const rawTrans = parseFloat(getAttr('trans') || '0');
    trans = Math.min(Math.max(1.0 - rawTrans, 0.0), 1.0);
  } else {
    trans = 1.0;
  }

  // type="2" marks a colourized copy ("[Name]1" materials SketchUp creates
  // when you re-colour a textured material); colorizeType 0 = hue shift,
  // 1 = tint.
  const colorized = getAttr('type') === '2';
  const colorizeTypeRaw = parseInt(getAttr('colorizeType') || '0', 10);
  const colorizeType = Number.isNaN(colorizeTypeRaw) ? 0 : colorizeTypeRaw;

  // <mat:texture ...> sub-element, if the material carries hasTexture="1".
  const textureMatch = xmlText.match(/<(?:[a-zA-Z0-9_]+:)?texture\b([^>]*)\/?>/);
  let hasTexture = false;
  let textureFilename = '';
  let xScale = 0.0;
  let yScale = 0.0;
  if (textureMatch) {
    hasTexture = true;
    const texAttrs = textureMatch[1];
    const getTexAttr = (n: string): string | null => {
      const r = new RegExp(`\\b${n}\\s*=\\s*(?:"([^"]*)"|'([^']*)')`);
      const m = texAttrs.match(r);
      return m ? (m[1] !== undefined ? m[1] : m[2]) : null;
    };
    textureFilename = getTexAttr('textureFilename') || '';
    const xs = parseFloat(getTexAttr('xScale') || '0');
    xScale = Number.isNaN(xs) ? 0.0 : xs;
    const ys = parseFloat(getTexAttr('yScale') || '0');
    yScale = Number.isNaN(ys) ? 0.0 : ys;
  }

  // <mat:images>/<mat:image path="..."> - colourized copies keep no image
  // of their own; this points into the SOURCE material's folder.
  const imageMatch = xmlText.match(
    /<(?:[a-zA-Z0-9_]+:)?image\b[^>]*\bpath\s*=\s*(?:"([^"]*)"|'([^']*)')[^>]*\/?>/
  );
  const imagePath = imageMatch ? (imageMatch[1] !== undefined ? imageMatch[1] : imageMatch[2]) : '';

  return {
    name,
    r: colorRed,
    g: colorGreen,
    b: colorBlue,
    trans,
    colorized,
    colorizeType,
    hasTexture,
    textureFilename,
    xScale,
    yScale,
    imagePath,
  };
}

/**
 * Parse a styles/*\/style.xml document: face colors live as signed-int32
 * ARGB variants under item id 4000 (front / default face color) and 4001
 * (back face color). Viewers need them to shade unpainted faces the way
 * SketchUp does.
 */
export function parseStyleXml(xmlText: string): {
  name: string;
  frontColor: [number, number, number] | null;
  backColor: [number, number, number] | null;
} | null {
  const styleMatch = xmlText.match(/<(?:[a-zA-Z0-9_]+:)?style\b([^>]*)>/);
  if (!styleMatch) return null;
  const attrsString = styleMatch[1];
  const nameMatch = attrsString.match(/\bname\s*=\s*(?:"([^"]*)"|'([^']*)')/);
  const name = nameMatch ? (nameMatch[1] !== undefined ? nameMatch[1] : nameMatch[2]) : '';

  const colors: Record<string, [number, number, number]> = {};
  const itemRegex =
    /<(?:[a-zA-Z0-9_]+:)?item\s+id\s*=\s*(?:"(\d+)"|'(\d+)')[^>]*>([\s\S]*?)<\/(?:[a-zA-Z0-9_]+:)?item>/g;
  let m: RegExpExecArray | null;
  while ((m = itemRegex.exec(xmlText)) !== null) {
    const id = m[1] !== undefined ? m[1] : m[2];
    if (id !== '4000' && id !== '4001') continue;
    const inner = m[3];
    const variantMatch = inner.match(/<(?:[a-zA-Z0-9_]+:)?variant\b[^>]*>(-?\d+)<\/(?:[a-zA-Z0-9_]+:)?variant>/);
    if (!variantMatch) continue;
    const raw = parseInt(variantMatch[1], 10);
    if (Number.isNaN(raw)) continue;
    const v = raw >>> 0; // reinterpret as unsigned 32-bit, matches Python's `& 0xFFFFFFFF`
    colors[id] = [(v >> 16) & 255, (v >> 8) & 255, v & 255];
  }

  return {
    name,
    frontColor: colors['4000'] ?? null,
    backColor: colors['4001'] ?? null,
  };
}

/** Strip any leading run of characters in `chars` (Python str.lstrip semantics). */
function lstripChars(s: string, chars: string): string {
  let i = 0;
  while (i < s.length && chars.includes(s[i])) i++;
  return s.slice(i);
}

/**
 * Resolve a material's texture image bytes from the material files map
 * (the flat "filename -> bytes" view of the embedded ZIP).
 *
 * SketchUp stores the image next to material.xml (materials/<folder>/<image>).
 * The stored image name can differ from textureFilename (observed:
 * "..._Safety.jpg" in the XML vs "..._Saftey.jpg" on disk) - the folder's
 * non-XML sibling is used as fallback. Colourized copies ("[Name]1",
 * type="2") keep no image of their own - their <mat:image path> points into
 * the SOURCE material's folder, sometimes prefixed "./".
 */
export function resolveTextureBytes(
  materialFiles: Record<string, Uint8Array>,
  xmlName: string,
  filename: string,
  imagePath: string
): { data: Uint8Array | null; filename: string } {
  const names = Object.keys(materialFiles);
  const slashIdx = xmlName.lastIndexOf('/');
  const folder = slashIdx >= 0 ? xmlName.slice(0, slashIdx) : '';

  let data: Uint8Array | null = null;
  let resolvedFilename = filename;

  const candidate = filename ? `${folder}/${filename}` : null;
  if (candidate && names.includes(candidate)) {
    data = materialFiles[candidate];
  } else {
    for (const entry of names) {
      if (
        entry.startsWith(folder + '/') &&
        entry !== xmlName &&
        !entry.toLowerCase().endsWith('.xml')
      ) {
        data = materialFiles[entry];
        if (!resolvedFilename) {
          resolvedFilename = entry.split('/').pop() || '';
        }
        break;
      }
    }
  }

  if (data === null) {
    const imgPath = lstripChars(imagePath, './');
    const candidates = [imgPath, folder ? `${folder}/${imgPath}` : imgPath];
    for (const cand of candidates) {
      if (cand && names.includes(cand)) {
        data = materialFiles[cand];
        if (!resolvedFilename) {
          resolvedFilename = cand.split('/').pop() || '';
        }
        break;
      }
    }
  }

  return { data, filename: resolvedFilename };
}
