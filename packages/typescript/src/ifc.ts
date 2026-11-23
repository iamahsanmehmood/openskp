import { SkpScene, GlbPrimitive } from './model';
import { ifcAttrCount } from './ifc-attr-counts';

declare const process: any;
declare const require: any;

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
    if (cp < 128) {
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

export function sanitizeName(name: string | null | undefined): string {
  if (!name) return 'Unnamed';
  const clean = name.replace(/^[ \t\r\n]+|[ \t\r\n]+$/g, '');
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
 * Tries the component's own name first, then falls back to `layerName`, then
 * (when `classifyUsingFullPath` is set) to keyword-matching the component's
 * full ancestor hierarchy path, and finally to `IfcBuildingElementProxy`.
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

function weldCoordKey(v: number): string {
  const r = Math.round(v * 1e6) / 1e6;
  return (r === 0 ? 0 : r).toFixed(6);
}

function meshIsClosed(prim: GlbPrimitive): boolean {
  const vCount = Math.floor(prim.positions.length / 3);
  const triCount = Math.floor(prim.indices.length / 3);
  if (vCount === 0 || triCount === 0) return false;

  const weld = new Map<string, number>();
  const remap = new Uint32Array(vCount);
  for (let i = 0; i < vCount; i++) {
    const key = `${weldCoordKey(prim.positions[i * 3])},${weldCoordKey(prim.positions[i * 3 + 1])},${weldCoordKey(prim.positions[i * 3 + 2])}`;
    let id = weld.get(key);
    if (id === undefined) {
      id = weld.size;
      weld.set(key, id);
    }
    remap[i] = id;
  }

  const edgeUse = new Map<string, number>();
  for (let i = 0; i < triCount; i++) {
    const i0 = prim.indices[i * 3];
    const i1 = prim.indices[i * 3 + 1];
    const i2 = prim.indices[i * 3 + 2];
    if (i0 >= vCount || i1 >= vCount || i2 >= vCount) return false;
    const a = remap[i0];
    const b = remap[i1];
    const c = remap[i2];
    const pairs: [number, number][] = [
      [a, b],
      [b, c],
      [c, a],
    ];
    for (const [u, v] of pairs) {
      const ek = u < v ? `${u},${v}` : `${v},${u}`;
      edgeUse.set(ek, (edgeUse.get(ek) ?? 0) + 1);
    }
  }

  if (edgeUse.size === 0) return false;
  for (const count of edgeUse.values()) {
    if (count !== 2) return false;
  }
  return true;
}

function ifcCoord(x: number, y: number, z: number, scale: number): string {
  const vx = (x * scale).toFixed(6);
  const vy = (-z * scale).toFixed(6);
  const vz = (y * scale).toFixed(6);
  return `(${vx},${vy},${vz})`;
}

/**
 * Serialize a baked SkpScene to ISO-10303-21 STEP ASCII IFC4 format.
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
    const raw = rawLayer && rawLayer.length > 0 ? rawLayer : 'Layer0';
    const key = rawLayer && rawLayer.length > 0 ? sanitizeName(rawLayer) : 'Layer0';
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

  for (const prim of scene.glbPrimitives) {
    const triCount = Math.floor(prim.indices.length / 3);
    const vCount = Math.floor(prim.positions.length / 3);
    if (triCount === 0 || vCount === 0) continue;

    const meta = scene.meshIndex ? scene.meshIndex[prim.geomName] : undefined;
    const displayName =
      meta && meta.name ? sanitizeName(meta.name) : sanitizeName(prim.geomName);
    const rawLayer = meta && meta.layer ? meta.layer : '';
    const layerName = rawLayer ? sanitizeName(rawLayer) : 'Layer0';
    const pathName = meta && meta.path ? meta.path : '';
    const [stepType] = classify(displayName, layerName, pathName);

    const ptCoords: string[] = [];
    for (let i = 0; i < vCount; i++) {
      ptCoords.push(
        ifcCoord(
          prim.positions[i * 3],
          prim.positions[i * 3 + 1],
          prim.positions[i * 3 + 2],
          scale
        )
      );
    }

    const ptListId = nextId();
    lines.push(`#${ptListId}=IFCCARTESIANPOINTLIST3D((${ptCoords.join(',')}));`);

    const faceIndices: string[] = [];
    for (let i = 0; i < triCount; i++) {
      const idx0 = prim.indices[i * 3] + 1;
      const idx1 = prim.indices[i * 3 + 1] + 1;
      const idx2 = prim.indices[i * 3 + 2] + 1;
      faceIndices.push(`(${idx0},${idx1},${idx2})`);
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
      const layerOn =
        scene.layerHidden && scene.layerHidden[entry.rawName] ? '.F.' : '.T.';
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

  lines.push('ENDSEC;');
  lines.push('END-ISO-10303-21;');
  return lines.join('\r\n') + '\r\n';
}

/**
 * Node.js-only helper: serialize `scene` to IFC4 and write it to `outputPath`.
 */
export function exportIFC(
  scene: SkpScene,
  outputPath: string,
  scale: number = METRES_TO_MM,
  schema: string = 'IFC4',
  classifier?: (geomName: string, layerName: string) => [string, string],
  classifyUsingFullPath = false
): void {
  if (typeof process === 'undefined' || !process.versions || !process.versions.node) {
    throw new Error('exportIFC is only supported in Node.js environments. Use toIFC() in browsers.');
  }
  const fs = require('fs');
  const path = require('path');
  const dir = path.dirname(outputPath);
  if (dir && !fs.existsSync(dir)) {
    fs.mkdirSync(dir, { recursive: true });
  }
  const text = toIFC(scene, scale, schema, classifier, classifyUsingFullPath);
  fs.writeFileSync(outputPath, text, 'utf8');
}
