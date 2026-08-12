import { SkpScene } from './model';

declare const process: any;
declare const require: any;

export const METRES_TO_INCHES = 39.37007874015748;

const IFC_BASE64 = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_$';

export function generateIFCGUID(): string {
  let result = '';
  for (let i = 0; i < 22; i++) {
    const r = Math.floor(Math.random() * 64);
    result += IFC_BASE64[r];
  }
  return result;
}

export function sanitizeName(name: string | null | undefined): string {
  if (!name) return 'Unnamed';
  const clean = name.replace(/'/g, "''").replace(/\\/g, '\\\\').trim();
  return clean.length > 0 ? clean : 'Unnamed';
}

export function classifyElement(geomName: string): [string, string] {
  const l = geomName.toLowerCase();
  if (l.includes('wall')) return ['IFCWALL', 'IfcWall'];
  if (l.includes('door')) return ['IFCDOOR', 'IfcDoor'];
  if (l.includes('window')) return ['IFCWINDOW', 'IfcWindow'];
  if (l.includes('slab') || l.includes('floor')) return ['IFCSLAB', 'IfcSlab'];
  if (l.includes('column') || l.includes('pillar')) return ['IFCCOLUMN', 'IfcColumn'];
  if (l.includes('beam') || l.includes('joist')) return ['IFCBEAM', 'IfcBeam'];
  if (l.includes('roof')) return ['IFCROOF', 'IfcRoof'];
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

/**
 * Serialize a baked SkpScene to ISO-10303-21 STEP ASCII IFC4 format.
 */
export function toIFC(
  scene: SkpScene,
  scale: number = METRES_TO_INCHES,
  schema: string = 'IFC4'
): string {
  if (!scene || !scene.glbPrimitives) {
    throw new Error('toIFC requires a valid SkpScene instance');
  }

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
    'DATA;'
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
    `#${ownerHistId}=IFCOWNERHISTORY(#${personOrgId},#${appId},$,.READWRITE.,$,$,$,${timestampEpoch});`
  );

  const lengthUnitId = nextId();
  lines.push(`#${lengthUnitId}=IFCSIUNIT(*,.LENGTHUNIT.,.MILLI.,.METRE.);`);

  const angleUnitId = nextId();
  lines.push(`#${angleUnitId}=IFCSIUNIT(*,.PLANEANGLEUNIT.,$,.RADIAN.);`);

  const solidUnitId = nextId();
  lines.push(`#${solidUnitId}=IFCSIUNIT(*,.STERADIANUNIT.,$,.STERADIAN.);`);

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
  const layerItems: Map<string, number[]> = new Map();
  const matStyleCache: Map<string, number> = new Map();

  for (const prim of scene.glbPrimitives) {
    const triCount = Math.floor(prim.indices.length / 3);
    const vCount = Math.floor(prim.positions.length / 3);
    if (triCount === 0 || vCount === 0) continue;

    const geomName = sanitizeName(prim.geomName);
    const meta = scene.meshIndex ? scene.meshIndex[prim.geomName] : undefined;
    const layerName = sanitizeName(meta && meta.layer ? meta.layer : 'Layer0');
    const [stepType] = classifyElement(geomName);

    const ptCoords: string[] = [];
    for (let i = 0; i < vCount; i++) {
      const vx = (prim.positions[i * 3] * scale).toFixed(6);
      const vy = (prim.positions[i * 3 + 1] * scale).toFixed(6);
      const vz = (prim.positions[i * 3 + 2] * scale).toFixed(6);
      ptCoords.push(`(${vx},${vy},${vz})`);
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

    const faceSetId = nextId();
    lines.push(
      `#${faceSetId}=IFCTRIANGULATEDFACESET(#${ptListId},$,.TRUE.,(${faceIndices.join(',')}),$);`
    );

    if (!layerItems.has(layerName)) {
      layerItems.set(layerName, []);
    }
    layerItems.get(layerName)!.push(faceSetId);

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
      lines.push(`#${styleId}=IFCSURFACESTYLE('${geomName}_Material',.BOTH.,(#${renderingId}));`);

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
    const prodGuid = generateIFCGUID();
    if (stepType === 'IFCBUILDINGELEMENTPROXY') {
      lines.push(
        `#${productId}=${stepType}('${prodGuid}',#${ownerHistId},'${geomName}',$,$,#${prodPlacementId},#${prodShapeId},$,.NOTDEFINED.);`
      );
    } else {
      lines.push(
        `#${productId}=${stepType}('${prodGuid}',#${ownerHistId},'${geomName}',$,$,#${prodPlacementId},#${prodShapeId},$,$);`
      );
    }
    productIds.push(productId);

    if (meta && meta.properties && typeof meta.properties === 'object') {
      const propValIds: number[] = [];
      for (const [pk, pv] of Object.entries(meta.properties)) {
        const cleanK = sanitizeName(String(pk));
        const cleanV = sanitizeName(String(pv));
        const propId = nextId();
        lines.push(`#${propId}=IFCPROPERTYSINGLEVALUE('${cleanK}',$,IFCTEXT('${cleanV}'),$);`);
        propValIds.push(propId);
      }

      if (propValIds.length > 0) {
        const psetId = nextId();
        const propRefs = propValIds.map((pid) => `#${pid}`).join(',');
        lines.push(
          `#${psetId}=IFCPROPERTYSET('${generateIFCGUID()}',#${ownerHistId},'Pset_CustomProperties',$,(${propRefs}));`
        );

        lines.push(
          `#${nextId()}=IFCRELDEFINESBYPROPERTIES('${generateIFCGUID()}',#${ownerHistId},$,$,(#${productId}),#${psetId});`
        );
      }
    }
  }

  const sortedLayerNames = Array.from(layerItems.keys()).sort();
  for (const lName of sortedLayerNames) {
    const itemIds = layerItems.get(lName)!;
    if (itemIds.length > 0) {
      const itemRefs = itemIds.map((iid) => `#${iid}`).join(',');
      lines.push(
        `#${nextId()}=IFCPRESENTATIONLAYERASSIGNMENT('${lName}',$,(${itemRefs}),$);`
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
  scale: number = METRES_TO_INCHES,
  schema: string = 'IFC4'
): void {
  if (typeof process !== 'undefined' && process.versions && process.versions.node) {
    const fs = require('fs');
    const path = require('path');
    const dir = path.dirname(outputPath);
    if (!fs.existsSync(dir)) {
      fs.mkdirSync(dir, { recursive: true });
    }
    const text = toIFC(scene, scale, schema);
    fs.writeFileSync(outputPath, text, 'utf8');
  } else {
    throw new Error('exportIFC is only supported in Node.js environment');
  }
}
