import { SkpScene, GlbPrimitive, getSurfacePrimitives } from './model';
import { round6 } from './curves';
import { ifcAttrCount } from './ifc-attr-counts';
import { nodeFs, nodePath } from './node-builtins';

declare const process: any;

export const METRES_TO_INCHES = 39.37007874015748;
export const METRES_TO_MM = 1000.0;

const IFC_BASE64 = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_$';

export function generateIFCGUID(): string {
  const bytes = new Uint8Array(16);
  for (let i = 0; i < 16; i++) {
    bytes[i] = Math.floor(Math.random() * 256);
  }
  bytes[6] = (bytes[6] & 0x0f) | 0x40; // version 4
  bytes[8] = (bytes[8] & 0x3f) | 0x80; // RFC 4122 variant
  let u = 0n;
  for (let i = 0; i < 16; i++) {
    u = (u << 8n) | BigInt(bytes[i]);
  }
  const chars = new Array<string>(22);
  for (let i = 21; i >= 0; i--) {
    chars[i] = IFC_BASE64[Number(u & 63n)];
    u >>= 6n;
  }
  return chars.join('');
}

export function stepText(text: string): string {
  let out = '';
  const run: number[] = [];
  let wide = false;

  const flush = (): void => {
    if (run.length === 0) return;
    out += wide ? '\\X4\\' : '\\X2\\';
    const width = wide ? 8 : 4;
    for (const cp of run) {
      out += cp.toString(16).toUpperCase().padStart(width, '0');
    }
    out += '\\X0\\';
    run.length = 0;
  };

  for (const ch of text) {
    const cp = ch.codePointAt(0)!;
    if (cp >= 0x20 && cp <= 0x7e) {
      flush();
      if (ch === "'") {
        out += "''";
      } else if (ch === '\\') {
        out += '\\\\';
      } else {
        out += ch;
      }
    } else {
      run.push(cp);
      if (cp > 0xffff) {
        wide = true;
      }
    }
  }
  flush();
  return out;
}

function trimAsciiWs(s: string | null | undefined): string {
  if (!s) return '';
  return s.replace(/^[ \t\r\n]+|[ \t\r\n]+$/g, '');
}

export function sanitizeName(name: string | null | undefined): string {
  const clean = trimAsciiWs(name);
  if (clean.length === 0) return 'Unnamed';
  return stepText(clean);
}

function classifyByKeyword(name: string): [string, string] | null {
  const l = name.toLowerCase();
  if (l.includes('wall')) return ['IFCWALL', 'IfcWall'];
  if (l.includes('door')) return ['IFCDOOR', 'IfcDoor'];
  if (l.includes('window')) return ['IFCWINDOW', 'IfcWindow'];
  if (l.includes('slab') || l.includes('floor')) return ['IFCSLAB', 'IfcSlab'];
  if (l.includes('column') || l.includes('pillar')) return ['IFCCOLUMN', 'IfcColumn'];
  if (l.includes('beam') || l.includes('joist')) return ['IFCBEAM', 'IfcBeam'];
  if (l.includes('roof')) return ['IFCROOF', 'IfcRoof'];
  return null;
}

/**
 * Map a geometry/component name to an IFC4 entity type and constructor.
 *
 * Tries the component's own name first, then falls back to `layerName`
 * (many SketchUp-for-BIM workflows organize by tag/layer - "Walls",
 * "Doors" - even when individual components are never renamed away from
 * SketchUp's own defaults like "Component#109415"). If neither matches and
 * `classifyUsingFullPath` is set, falls back further to keyword-matching
 * the component's full ancestor hierarchy path, then finally to a generic,
 * untyped element if nothing matches.
 */
export function classifyElement(
  geomName: string,
  layerName = '',
  pathName = '',
  classifyUsingFullPath = false
): [string, string] {
  const byName = classifyByKeyword(geomName);
  if (byName) return byName;
  if (layerName) {
    const byLayer = classifyByKeyword(layerName);
    if (byLayer) return byLayer;
  }
  if (classifyUsingFullPath && pathName) {
    const byPath = classifyByKeyword(pathName);
    if (byPath) return byPath;
  }
  return ['IFCBUILDINGELEMENTPROXY', 'IfcBuildingElementProxy'];
}

function getPrimRgb(scene: SkpScene, primMatIdx: number): [number, number, number, number] {
  let r = 0.8, g = 0.8, b = 0.8, a = 1.0;
  if (scene.gltfMaterials && primMatIdx >= 0 && primMatIdx < scene.gltfMaterials.length) {
    const mat = scene.gltfMaterials[primMatIdx] as any;
    if (mat && mat.pbrMetallicRoughness && Array.isArray(mat.pbrMetallicRoughness.baseColorFactor)) {
      const vec = mat.pbrMetallicRoughness.baseColorFactor;
      if (vec.length >= 3) {
        r = Math.max(0.0, Math.min(1.0, Number(vec[0])));
        g = Math.max(0.0, Math.min(1.0, Number(vec[1])));
        b = Math.max(0.0, Math.min(1.0, Number(vec[2])));
        if (vec.length >= 4) {
          a = Math.max(0.0, Math.min(1.0, Number(vec[3])));
        }
      }
    }
  }
  return [r, g, b, a];
}

function weldVertices(
  positions: Float32Array,
  vCount: number
): { remap: Uint32Array; uCount: number } {
  let bucketCount = 16;
  const targetBuckets = Math.min(vCount, 1 << 23);
  while (bucketCount < targetBuckets) bucketCount <<= 1;
  const bucketMask = bucketCount - 1;

  const buckets = new Int32Array(bucketCount).fill(-1);
  const nextUnique = new Int32Array(vCount);
  const ux = new Float64Array(vCount);
  const uy = new Float64Array(vCount);
  const uz = new Float64Array(vCount);
  const remap = new Uint32Array(vCount);

  const scratchF64 = new Float64Array(3);
  const scratchU32 = new Uint32Array(scratchF64.buffer);
  let uCount = 0;

  for (let i = 0; i < vCount; i++) {
    let rx = round6(positions[i * 3]);
    let ry = round6(positions[i * 3 + 1]);
    let rz = round6(positions[i * 3 + 2]);
    if (rx === 0) rx = 0;
    if (ry === 0) ry = 0;
    if (rz === 0) rz = 0;

    scratchF64[0] = rx;
    scratchF64[1] = ry;
    scratchF64[2] = rz;
    let h = Math.imul(scratchU32[0] ^ scratchU32[1], 0x9e3779b1);
    h = Math.imul(h ^ scratchU32[2] ^ scratchU32[3], 0x85ebca6b);
    h = Math.imul(h ^ scratchU32[4] ^ scratchU32[5], 0xc2b2ae35);
    h ^= h >>> 16;
    const b = h & bucketMask;

    let u = buckets[b];
    while (u !== -1) {
      if (ux[u] === rx && uy[u] === ry && uz[u] === rz) break;
      u = nextUnique[u];
    }
    if (u === -1) {
      u = uCount++;
      ux[u] = rx;
      uy[u] = ry;
      uz[u] = rz;
      nextUnique[u] = buckets[b];
      buckets[b] = u;
    }
    remap[i] = u;
  }

  return { remap, uCount };
}

export function meshIsClosed(prim: GlbPrimitive): boolean {
  const positions = prim.positions;
  const indices = prim.indices;
  const vCount = Math.floor(positions.length / 3);
  const triCount = Math.floor(indices.length / 3);
  if (vCount === 0 || triCount === 0) return false;

  const triIndexCount = triCount * 3;
  for (let i = 0; i < triIndexCount; i++) {
    if (indices[i] >= vCount) return false;
  }

  const { remap, uCount } = weldVertices(positions, vCount);

  const offsets = new Uint32Array(uCount + 1);
  for (let i = 0; i < triIndexCount; i += 3) {
    const a = remap[indices[i]];
    const b = remap[indices[i + 1]];
    const c = remap[indices[i + 2]];
    offsets[(a < b ? a : b) + 1]++;
    offsets[(b < c ? b : c) + 1]++;
    offsets[(c < a ? c : a) + 1]++;
  }
  for (let u = 0; u < uCount; u++) {
    offsets[u + 1] += offsets[u];
  }

  const cursor = new Uint32Array(offsets.subarray(0, uCount));
  const adj = new Uint32Array(triIndexCount);
  for (let i = 0; i < triIndexCount; i += 3) {
    const a = remap[indices[i]];
    const b = remap[indices[i + 1]];
    const c = remap[indices[i + 2]];
    adj[cursor[a < b ? a : b]++] = a < b ? b : a;
    adj[cursor[b < c ? b : c]++] = b < c ? c : b;
    adj[cursor[c < a ? c : a]++] = c < a ? a : c;
  }

  const stamp = new Int32Array(uCount).fill(-1);
  const counts = new Uint32Array(uCount);
  for (let u = 0; u < uCount; u++) {
    const start = offsets[u];
    const end = offsets[u + 1];
    for (let p = start; p < end; p++) {
      const v = adj[p];
      if (stamp[v] !== u) {
        stamp[v] = u;
        counts[v] = 1;
      } else {
        counts[v]++;
      }
    }
    for (let p = start; p < end; p++) {
      if (counts[adj[p]] !== 2) return false;
    }
  }
  return true;
}

function fmtReal6(v: number): string {
  if (!Number.isFinite(v)) {
    throw new Error('IFC coordinates must be finite');
  }
  const s = v.toFixed(6);
  const eIdx = s.indexOf('e');
  if (eIdx === -1) return s;
  const mant = s.slice(0, eIdx);
  const exp = s.slice(eIdx + 1);
  return `${mant.includes('.') ? mant : `${mant}.0`}E${exp}`;
}

function ifcCoord(x: number, y: number, z: number, scale: number): string {
  if (!Number.isFinite(x) || !Number.isFinite(y) || !Number.isFinite(z)) {
    throw new Error('IFC coordinates must be finite');
  }
  const vx = fmtReal6(x * scale);
  const vy = fmtReal6(-z * scale);
  const vz = fmtReal6(y * scale);
  return `(${vx},${vy},${vz})`;
}

/**
 * Serialize a baked SkpScene to ISO-10303-21 STEP ASCII IFC4 format.
 *
 * @param classifier - Optional override for {@link classifyElement}, called
 * as `classifier(geomName, layerName)` and expected to return the same
 * `[STEP_ENTITY_TYPE, IFC_CLASS_NAME]` tuple - use this to supply your own
 * naming convention or metadata-driven typing instead of the built-in
 * keyword/layer heuristic.
 */
export function toIFC(
  scene: SkpScene,
  scale: number = METRES_TO_MM,
  schema: string = 'IFC4',
  classifier?: (geomName: string, layerName: string) => [string, string],
  classifyUsingFullPath = false
): string {
  if (!scene || !scene.glbPrimitives) {
    throw new Error('toIFC requires a valid SkpScene instance');
  }
  if (!Number.isFinite(scale)) {
    throw new Error('IFC scale must be finite');
  }

  const classify = classifier
    ? (name: string, layer: string, _path: string) => classifier(name, layer)
    : (name: string, layer: string, path: string) =>
        classifyElement(name, layer, path, classifyUsingFullPath);

  const schemaStr = (schema || 'IFC4').toUpperCase();
  const nowIso = new Date().toISOString().replace(/\.\d{3}Z$/, '');
  const timestampEpoch = Math.floor(Date.now() / 1000);

  const lines: string[] = [
    'ISO-10303-21;',
    'HEADER;',
    "FILE_DESCRIPTION(('ViewDefinition [CoordinationView]'),'2;1');",
    `FILE_NAME('model.ifc','${nowIso}',('OpenSKP Author'),('OpenSKP Organization'),'OpenSKP IFC Exporter','OpenSKP','');`,
    `FILE_SCHEMA(('${schemaStr}'));`,
    'ENDSEC;',
    'DATA;',
  ];

  let entityId = 1;
  const nextId = (): number => entityId++;

  const personId = nextId();
  lines.push(`#${personId}=IFCPERSON($,$,'OpenSKP User',$,$,$,$,$);`);

  const orgId = nextId();
  lines.push(`#${orgId}=IFCORGANIZATION($,'OpenSKP',$,$,$);`);

  const personOrgId = nextId();
  lines.push(`#${personOrgId}=IFCPERSONANDORGANIZATION(#${personId},#${orgId},$);`);

  const appId = nextId();
  lines.push(`#${appId}=IFCAPPLICATION(#${orgId},'0.3.1','OpenSKP Exporter','OpenSKP');`);

  const ownerHistId = nextId();
  lines.push(
    `#${ownerHistId}=IFCOWNERHISTORY(#${personOrgId},#${appId},$,.NOCHANGE.,$,$,$,${timestampEpoch});`
  );

  const lengthUnitId = nextId();
  lines.push(`#${lengthUnitId}=IFCSIUNIT(*,.LENGTHUNIT.,.MILLI.,.METRE.);`);

  const angleUnitId = nextId();
  lines.push(`#${angleUnitId}=IFCSIUNIT(*,.PLANEANGLEUNIT.,$,.RADIAN.);`);

  const solidUnitId = nextId();
  lines.push(`#${solidUnitId}=IFCSIUNIT(*,.SOLIDANGLEUNIT.,$,.STERADIAN.);`);

  const unitAssignId = nextId();
  lines.push(
    `#${unitAssignId}=IFCUNITASSIGNMENT((#${lengthUnitId},#${angleUnitId},#${solidUnitId}));`
  );

  const ptZeroId = nextId();
  lines.push(`#${ptZeroId}=IFCCARTESIANPOINT((0.0,0.0,0.0));`);

  const axisPlacementId = nextId();
  lines.push(`#${axisPlacementId}=IFCAXIS2PLACEMENT3D(#${ptZeroId},$,$);`);

  const geomCtxId = nextId();
  lines.push(
    `#${geomCtxId}=IFCGEOMETRICREPRESENTATIONCONTEXT($,'Model',3,1.0E-5,#${axisPlacementId},$);`
  );

  const annCtxId = nextId();
  lines.push(
    `#${annCtxId}=IFCGEOMETRICREPRESENTATIONSUBCONTEXT('Annotation','Model',*,*,*,*,#${geomCtxId},$,.MODEL_VIEW.,$);`
  );

  const projId = nextId();
  lines.push(
    `#${projId}=IFCPROJECT('${generateIFCGUID()}',#${ownerHistId},'OpenSKP Project',$,$,$,$,(#${geomCtxId}),#${unitAssignId});`
  );

  const sitePlacementId = nextId();
  lines.push(`#${sitePlacementId}=IFCLOCALPLACEMENT($,#${axisPlacementId});`);

  const siteId = nextId();
  lines.push(
    `#${siteId}=IFCSITE('${generateIFCGUID()}',#${ownerHistId},'Site',$,$,#${sitePlacementId},$,$,.ELEMENT.,$,$,$,$,$);`
  );

  const bldgPlacementId = nextId();
  lines.push(`#${bldgPlacementId}=IFCLOCALPLACEMENT(#${sitePlacementId},#${axisPlacementId});`);

  const bldgId = nextId();
  lines.push(
    `#${bldgId}=IFCBUILDING('${generateIFCGUID()}',#${ownerHistId},'Building',$,$,#${bldgPlacementId},$,$,.ELEMENT.,$,$,$);`
  );

  const storeyPlacementId = nextId();
  lines.push(`#${storeyPlacementId}=IFCLOCALPLACEMENT(#${bldgPlacementId},#${axisPlacementId});`);

  const storeyId = nextId();
  lines.push(
    `#${storeyId}=IFCBUILDINGSTOREY('${generateIFCGUID()}',#${ownerHistId},'Level 0',$,$,#${storeyPlacementId},$,$,.ELEMENT.,0.0);`
  );

  lines.push(
    `#${nextId()}=IFCRELAGGREGATES('${generateIFCGUID()}',#${ownerHistId},$,$,#${projId},(#${siteId}));`
  );
  lines.push(
    `#${nextId()}=IFCRELAGGREGATES('${generateIFCGUID()}',#${ownerHistId},$,$,#${siteId},(#${bldgId}));`
  );
  lines.push(
    `#${nextId()}=IFCRELAGGREGATES('${generateIFCGUID()}',#${ownerHistId},$,$,#${bldgId},(#${storeyId}));`
  );

  const productIds: number[] = [];
  const layerItems: Map<string, { rawName: string; itemIds: number[] }> = new Map();
  const matStyleCache: Map<string, number> = new Map();

  const addLayerItem = (rawLayer: string | null | undefined, itemId: number): void => {
    const trimmed = trimAsciiWs(rawLayer);
    const raw = trimmed.length > 0 ? rawLayer! : 'Layer0';
    const key = trimmed.length > 0 ? sanitizeName(rawLayer) : 'Layer0';
    let entry = layerItems.get(key);
    if (!entry) {
      entry = { rawName: raw, itemIds: [] };
      layerItems.set(key, entry);
    }
    entry.itemIds.push(itemId);
  };

  const writePset = (
    productId: number,
    psetName: string,
    props: Record<string, unknown> | null | undefined
  ): void => {
    if (!props || typeof props !== 'object') return;
    const entries = Object.entries(props);
    if (entries.length === 0) return;

    const propValIds: number[] = [];
    for (const [pk, pv] of entries) {
      const cleanK = sanitizeName(String(pk));
      const cleanV = sanitizeName(String(pv));
      const propId = nextId();
      lines.push(`#${propId}=IFCPROPERTYSINGLEVALUE('${cleanK}',$,IFCTEXT('${cleanV}'),$);`);
      propValIds.push(propId);
    }

    const psetId = nextId();
    const propRefs = propValIds.map((pid) => `#${pid}`).join(',');
    lines.push(
      `#${psetId}=IFCPROPERTYSET('${generateIFCGUID()}',#${ownerHistId},'${sanitizeName(psetName)}',$,(${propRefs}));`
    );
    lines.push(
      `#${nextId()}=IFCRELDEFINESBYPROPERTIES('${generateIFCGUID()}',#${ownerHistId},$,$,(#${productId}),#${psetId});`
    );
  };

  if (scene.curveSets) {
    for (const cs of scene.curveSets) {
      if (!cs.pointsM || cs.pointsM.length < 2) continue;
      for (const p of cs.pointsM) {
        if (!Number.isFinite(p[0]) || !Number.isFinite(p[1]) || !Number.isFinite(p[2])) {
          throw new Error('IFC coordinates must be finite');
        }
      }

      let curveId: number;
      if (cs.arc && cs.arc.points.length >= 3) {
        const segs = cs.arc.segments.map(
          (s) => `IFCARCINDEX((${s[0] + 1},${s[1] + 1},${s[2] + 1}))`
        );
        const coords = cs.arc.points.map((p) => ifcCoord(p[0], p[1], p[2], scale));
        const ptListId = nextId();
        lines.push(`#${ptListId}=IFCCARTESIANPOINTLIST3D((${coords.join(',')}));`);
        curveId = nextId();
        lines.push(`#${curveId}=IFCINDEXEDPOLYCURVE(#${ptListId},(${segs.join(',')}),.F.);`);
      } else {
        const coords = cs.pointsM.map((p) => ifcCoord(p[0], p[1], p[2], scale));
        if (cs.closed) {
          coords.push(coords[0]);
        }
        const ptIds: number[] = [];
        for (const c of coords) {
          const ptId = nextId();
          lines.push(`#${ptId}=IFCCARTESIANPOINT(${c});`);
          ptIds.push(ptId);
        }
        curveId = nextId();
        const ptRefs = ptIds.map((pid) => `#${pid}`).join(',');
        lines.push(`#${curveId}=IFCPOLYLINE((${ptRefs}));`);
      }

      const curveSetId = nextId();
      lines.push(`#${curveSetId}=IFCGEOMETRICCURVESET((#${curveId}));`);

      addLayerItem(cs.layer, curveId);

      const annName = sanitizeName(cs.name || 'Curve');
      const annRepId = nextId();
      lines.push(
        `#${annRepId}=IFCSHAPEREPRESENTATION(#${annCtxId},'Annotation','GeometricCurveSet',(#${curveSetId}));`
      );
      const annShapeId = nextId();
      lines.push(`#${annShapeId}=IFCPRODUCTDEFINITIONSHAPE($,$,(#${annRepId}));`);
      const annPlacementId = nextId();
      lines.push(`#${annPlacementId}=IFCLOCALPLACEMENT(#${storeyPlacementId},#${axisPlacementId});`);
      const annId = nextId();
      lines.push(
        `#${annId}=IFCANNOTATION('${generateIFCGUID()}',#${ownerHistId},'${annName}',$,$,#${annPlacementId},#${annShapeId});`
      );

      if (cs.attributeDictionaries && typeof cs.attributeDictionaries === 'object') {
        for (const [dictName, entries] of Object.entries(cs.attributeDictionaries)) {
          writePset(annId, `Pset_${dictName}`, entries);
        }
      }
      productIds.push(annId);
    }
  }

  for (const prim of getSurfacePrimitives(scene)) {
    const triCount = Math.floor(prim.indices.length / 3);
    const vCount = Math.floor(prim.positions.length / 3);
    if (triCount === 0 || vCount === 0) continue;

    const meta = scene.meshIndex ? scene.meshIndex[prim.geomName] : undefined;
    const rawNameSrc = meta && meta.name ? meta.name : prim.geomName;
    const rawName = trimAsciiWs(rawNameSrc) || 'Unnamed';
    const displayName = sanitizeName(rawName);
    const rawLayer = meta && meta.layer ? meta.layer : '';
    const classifyLayer = trimAsciiWs(rawLayer) || 'Layer0';
    const pathName = meta && meta.path ? meta.path : '';
    const [stepType] = classify(rawName, classifyLayer, pathName);

    const positions = prim.positions;
    const ptCoords = new Array<string>(vCount);
    for (let i = 0; i < vCount; i++) {
      const base = i * 3;
      ptCoords[i] = ifcCoord(positions[base], positions[base + 1], positions[base + 2], scale);
    }

    const ptListId = nextId();
    lines.push(`#${ptListId}=IFCCARTESIANPOINTLIST3D((${ptCoords.join(',')}));`);

    const indices = prim.indices;
    const faceIndices = new Array<string>(triCount);
    for (let i = 0; i < triCount; i++) {
      const base = i * 3;
      faceIndices[i] = `(${indices[base] + 1},${indices[base + 1] + 1},${indices[base + 2] + 1})`;
    }

    const closed = meshIsClosed(prim) ? '.T.' : '.F.';
    const faceSetId = nextId();
    lines.push(
      `#${faceSetId}=IFCTRIANGULATEDFACESET(#${ptListId},$,${closed},(${faceIndices.join(',')}),$);`
    );

    addLayerItem(rawLayer, faceSetId);

    const [r, g, b, a] = getPrimRgb(scene, prim.materialIndex);
    const rgbaKey = `${r.toFixed(4)},${g.toFixed(4)},${b.toFixed(4)},${a.toFixed(4)}`;
    let styleAssignId: number;
    if (!matStyleCache.has(rgbaKey)) {
      const colId = nextId();
      lines.push(`#${colId}=IFCCOLOURRGB($,${r.toFixed(4)},${g.toFixed(4)},${b.toFixed(4)});`);

      const transparency = (1.0 - a).toFixed(4);
      const renderingId = nextId();
      lines.push(
        `#${renderingId}=IFCSURFACESTYLERENDERING(#${colId},${transparency},$,$,$,$,$,$,.FLAT.);`
      );

      const styleId = nextId();
      lines.push(
        `#${styleId}=IFCSURFACESTYLE('${displayName}_Material',.BOTH.,(#${renderingId}));`
      );

      styleAssignId = nextId();
      lines.push(`#${styleAssignId}=IFCPRESENTATIONSTYLEASSIGNMENT((#${styleId}));`);
      matStyleCache.set(rgbaKey, styleAssignId);
    } else {
      styleAssignId = matStyleCache.get(rgbaKey)!;
    }

    const styledItemId = nextId();
    lines.push(`#${styledItemId}=IFCSTYLEDITEM(#${faceSetId},(#${styleAssignId}),$);`);

    const shapeRepId = nextId();
    lines.push(
      `#${shapeRepId}=IFCSHAPEREPRESENTATION(#${geomCtxId},'Body','Tessellation',(#${faceSetId}));`
    );

    const prodShapeId = nextId();
    lines.push(`#${prodShapeId}=IFCPRODUCTDEFINITIONSHAPE($,$,(#${shapeRepId}));`);

    const prodPlacementId = nextId();
    lines.push(`#${prodPlacementId}=IFCLOCALPLACEMENT(#${storeyPlacementId},#${axisPlacementId});`);

    const productId = nextId();
    const attrs: string[] = [
      `'${generateIFCGUID()}'`,
      `#${ownerHistId}`,
      `'${displayName}'`,
      '$',
      '$',
      `#${prodPlacementId}`,
      `#${prodShapeId}`,
      '$',
    ];
    const declared = ifcAttrCount(schemaStr, stepType);
    const total = Math.max(attrs.length, declared && declared > 0 ? declared : 9);
    while (attrs.length < total) {
      attrs.push('$');
    }
    if (stepType === 'IFCBUILDINGELEMENTPROXY' && total === 9) {
      attrs[8] = '.NOTDEFINED.';
    }
    lines.push(`#${productId}=${stepType}(${attrs.join(',')});`);
    productIds.push(productId);

    if (meta) {
      writePset(productId, 'Pset_CustomProperties', meta.properties);
      if (meta.attributeDictionaries && typeof meta.attributeDictionaries === 'object') {
        for (const [dictName, entries] of Object.entries(meta.attributeDictionaries)) {
          writePset(productId, `Pset_${dictName}`, entries);
        }
      }
    }
  }

  const sortedLayerNames = Array.from(layerItems.keys()).sort();
  for (const lName of sortedLayerNames) {
    const entry = layerItems.get(lName)!;
    if (entry.itemIds.length > 0) {
      const itemRefs = entry.itemIds.map((iid) => `#${iid}`).join(',');
      const layerOn = scene.layerHidden && scene.layerHidden[entry.rawName] ? '.F.' : '.T.';
      lines.push(
        `#${nextId()}=IFCPRESENTATIONLAYERWITHSTYLE('${lName}',$,(${itemRefs}),$,${layerOn},.F.,.F.,());`
      );
    }
  }

  if (productIds.length > 0) {
    const prodRefs = productIds.map((pid) => `#${pid}`).join(',');
    lines.push(
      `#${nextId()}=IFCRELCONTAINEDINSPATIALSTRUCTURE('${generateIFCGUID()}',#${ownerHistId},$,$,(${prodRefs}),#${storeyId});`
    );
  }

  lines.push('ENDSEC;', 'END-ISO-10303-21;');
  return lines.join('\r\n') + '\r\n';
}

/**
 * Export a baked SkpScene to an IFC4 file.
 */
export function exportIFC(
  scene: SkpScene,
  outputPath: string,
  scale: number = METRES_TO_MM,
  schema: string = 'IFC4',
  classifier?: (geomName: string, layerName: string) => [string, string],
  classifyUsingFullPath = false
): void {
  if (typeof process !== 'undefined' && process.versions && process.versions.node) {
    const text = toIFC(scene, scale, schema, classifier, classifyUsingFullPath);
    const fs = nodeFs();
    const path = nodePath();
    const dir = path.dirname(outputPath);
    if (dir && !fs.existsSync(dir)) {
      fs.mkdirSync(dir, { recursive: true });
    }
    fs.writeFileSync(outputPath, text, 'utf8');
  } else {
    throw new Error('exportIFC is only supported in Node.js environment');
  }
}
