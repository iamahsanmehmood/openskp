import { GeometryBuilder, type ParsedDefinition } from '../../src/geometry';
import type { Material, ParsedRawData } from '../../src/model';

/**
 * Test-only synthetic fixtures, built directly as ParsedRawData - the same
 * approach tests/instance-material-uv.test.ts already uses. Deterministic
 * and independent of any binary .skp, so structural scaling assertions and
 * the benchmark measure geometry handling, not file parsing.
 */

/** A square panel in the XY plane, `size` inches on a side. */
export function panelDefinition(
  size = 24,
  name = 'Panel',
  opts: { materialId?: number | null; backMaterialId?: number | null } = {}
): ParsedDefinition {
  const builder = new GeometryBuilder();
  builder.vertices.set(1, [0, 0, 0]);
  builder.vertices.set(2, [size, 0, 0]);
  builder.vertices.set(3, [size, size, 0]);
  builder.vertices.set(4, [0, size, 0]);
  builder.edges.set(11, [1, 2]);
  builder.edges.set(12, [2, 3]);
  builder.edges.set(13, [3, 4]);
  builder.edges.set(14, [4, 1]);
  builder.faces.set(21, {
    loops: [
      [
        { edgeId: 11, orientation: 0 },
        { edgeId: 12, orientation: 0 },
        { edgeId: 13, orientation: 0 },
        { edgeId: 14, orientation: 0 },
      ],
    ],
    normal: [0, 0, 1],
    materialId: opts.materialId ?? null,
    backMaterialId: opts.backMaterialId ?? null,
  });
  return { guid: name.toUpperCase(), name, isImage: false, alwaysFacesCamera: false, builder };
}

/**
 * A box-ish definition with `faceCount` stacked square faces - a
 * "nontrivial component" for the benchmark, still fully deterministic.
 */
export function stackedPanelsDefinition(faceCount: number, name = 'Widget'): ParsedDefinition {
  const builder = new GeometryBuilder();
  let vId = 1;
  let eId = 1000;
  let fId = 5000;
  for (let i = 0; i < faceCount; i++) {
    const z = i * 2;
    const v1 = vId++, v2 = vId++, v3 = vId++, v4 = vId++;
    builder.vertices.set(v1, [0, 0, z]);
    builder.vertices.set(v2, [24, 0, z]);
    builder.vertices.set(v3, [24, 24, z]);
    builder.vertices.set(v4, [0, 24, z]);
    const e1 = eId++, e2 = eId++, e3 = eId++, e4 = eId++;
    builder.edges.set(e1, [v1, v2]);
    builder.edges.set(e2, [v2, v3]);
    builder.edges.set(e3, [v3, v4]);
    builder.edges.set(e4, [v4, v1]);
    builder.faces.set(fId++, {
      loops: [
        [
          { edgeId: e1, orientation: 0 },
          { edgeId: e2, orientation: 0 },
          { edgeId: e3, orientation: 0 },
          { edgeId: e4, orientation: 0 },
        ],
      ],
      normal: [0, 0, 1],
      materialId: null,
      backMaterialId: null,
    });
  }
  return { guid: name.toUpperCase(), name, isImage: false, alwaysFacesCamera: false, builder };
}

/** A square panel with a square hole in it, to exercise multi-loop faces. */
export function panelWithHoleDefinition(name = 'HolePanel'): ParsedDefinition {
  const builder = new GeometryBuilder();
  builder.vertices.set(1, [0, 0, 0]);
  builder.vertices.set(2, [24, 0, 0]);
  builder.vertices.set(3, [24, 24, 0]);
  builder.vertices.set(4, [0, 24, 0]);
  builder.vertices.set(5, [8, 8, 0]);
  builder.vertices.set(6, [16, 8, 0]);
  builder.vertices.set(7, [16, 16, 0]);
  builder.vertices.set(8, [8, 16, 0]);
  builder.edges.set(11, [1, 2]);
  builder.edges.set(12, [2, 3]);
  builder.edges.set(13, [3, 4]);
  builder.edges.set(14, [4, 1]);
  builder.edges.set(15, [5, 6]);
  builder.edges.set(16, [6, 7]);
  builder.edges.set(17, [7, 8]);
  builder.edges.set(18, [8, 5]);
  builder.faces.set(21, {
    loops: [
      [
        { edgeId: 11, orientation: 0 },
        { edgeId: 12, orientation: 0 },
        { edgeId: 13, orientation: 0 },
        { edgeId: 14, orientation: 0 },
      ],
      [
        { edgeId: 15, orientation: 0 },
        { edgeId: 16, orientation: 0 },
        { edgeId: 17, orientation: 0 },
        { edgeId: 18, orientation: 0 },
      ],
    ],
    normal: [0, 0, 1],
    materialId: null,
    backMaterialId: null,
  });
  return { guid: name.toUpperCase(), name, isImage: false, alwaysFacesCamera: false, builder };
}

export function texturedMaterial(
  name = 'Decor',
  color = { r: 10, g: 20, b: 30, a: 255 },
  bytes: number[] = [0xff, 0xd8, 0xff, 0x01]
): Material {
  return {
    name,
    color,
    transparency: 1,
    id: null,
    texture: {
      filename: `${name.toLowerCase()}.jpg`,
      width: 24,
      height: 12,
      data: new Uint8Array(bytes),
    },
    colorized: false,
    colorizeType: 0,
  };
}

export function plainMaterial(
  name: string,
  color: { r: number; g: number; b: number; a: number }
): Material {
  return {
    name,
    color,
    transparency: 1,
    id: null,
    texture: null,
    colorized: false,
    colorizeType: 0,
  };
}

/** Identity 3x3 plus a translation, in the 12-number layout the builder uses. */
export function translation(x: number, y: number, z: number): number[] {
  return [1, 0, 0, 0, 1, 0, 0, 0, 1, x, y, z];
}

/** A non-uniform scale with translation. */
export function scaleMatrix(sx: number, sy: number, sz: number, t: [number, number, number] = [0, 0, 0]): number[] {
  return [sx, 0, 0, 0, sy, 0, 0, 0, sz, t[0], t[1], t[2]];
}

/** Rotation about Z by `deg`, with translation. */
export function rotationZ(deg: number, t: [number, number, number] = [0, 0, 0]): number[] {
  const r = (deg * Math.PI) / 180;
  const c = Math.cos(r);
  const s = Math.sin(r);
  // Row-major 3x3 in the builder's layout.
  return [c, -s, 0, s, c, 0, 0, 0, 1, t[0], t[1], t[2]];
}

export interface SceneSpec {
  /** Definitions keyed by ref index. */
  defs: Map<number | string, ParsedDefinition>;
  /** Instances placed at the root. */
  rootInstances: {
    refIdx: number;
    name?: string;
    matrix: number[];
    materialId?: number | null;
    layerId?: number | null;
  }[];
  /** Geometry drawn loose at the root, if any. */
  rootGeometry?: ParsedDefinition;
  materials?: Map<string, Material>;
  materialIdToName?: Map<number, string>;
  layerColors?: Map<string, [number, number, number]>;
  layerIdToName?: Map<number, string>;
}

/** Assemble a full ParsedRawData from a compact spec. */
export function makeParsed(spec: SceneSpec): ParsedRawData {
  const root = spec.rootGeometry?.builder ?? new GeometryBuilder();

  for (const inst of spec.rootInstances) {
    root.instances.push({
      offset: 0,
      refGuid: String(inst.refIdx),
      refIdx: inst.refIdx,
      name: inst.name ?? `Instance_${inst.refIdx}`,
      matrix: inst.matrix,
      materialId: inst.materialId ?? null,
      layerId: inst.layerId ?? null,
      children: [],
    });
  }

  const defsDict = new Map<number | string, ParsedDefinition>(spec.defs);
  defsDict.set('ROOT', {
    guid: 'ROOT',
    name: 'ROOT_MODEL',
    isImage: false,
    alwaysFacesCamera: false,
    builder: root,
  });

  return {
    version: '1.0',
    units: 'Inches',
    layerColors: spec.layerColors ?? new Map([['Layer0', [255, 255, 255]]]),
    layerHidden: new Map(),
    layerIdToName: spec.layerIdToName ?? new Map(),
    materialIdToName: spec.materialIdToName ?? new Map(),
    materialsMap: spec.materials ?? new Map(),
    materialsByFolder: new Map(),
    styles: [],
    defsDict,
  };
}

/**
 * The benchmark/scaling scene: one nontrivial definition placed `count`
 * times in a deterministic grid.
 */
export function repeatedComponentScene(count: number, facesPerComponent = 12): ParsedRawData {
  const def = stackedPanelsDefinition(facesPerComponent);
  const rootInstances = [];
  for (let i = 0; i < count; i++) {
    rootInstances.push({
      refIdx: 1,
      name: `Widget_${i}`,
      matrix: translation((i % 32) * 40, Math.floor(i / 32) * 40, 0),
    });
  }
  return makeParsed({
    defs: new Map<number | string, ParsedDefinition>([[1, def]]),
    rootInstances,
  });
}
