import { describe, it, expect } from 'vitest';
import { GeometryBuilder, type ParsedDefinition } from '../src/geometry';
import { buildSceneFromParsed, type Material, type ParsedRawData } from '../src/model';

/**
 * A face with no material of its own is painted by the instance around it
 * (SketchUp's "paint the component"). The scene builder resolved the inherited
 * COLOUR but not the material itself, so the UV maths never saw the inherited
 * texture's tile size and fell back to a tile of 1 inch: a 24" decor sheet
 * tiled 24 times across a 24" panel instead of covering it exactly once.
 */

const TILE_W = 24;
const TILE_H = 12;
const PANEL_INCHES = 24;

function texturedMaterial(): Material {
  return {
    name: 'Decor',
    color: { r: 10, g: 20, b: 30, a: 255 },
    transparency: 1,
    id: 7,
    texture: {
      filename: 'decor.jpg',
      width: TILE_W,
      height: TILE_H,
      data: new Uint8Array([0xff, 0xd8, 0xff]),
    },
    colorized: false,
    colorizeType: 0,
  };
}

/** One flat square panel in the XY plane, with no material on the face. */
function panelDefinition(): ParsedDefinition {
  const builder = new GeometryBuilder();
  builder.vertices.set(1, [0, 0, 0]);
  builder.vertices.set(2, [PANEL_INCHES, 0, 0]);
  builder.vertices.set(3, [PANEL_INCHES, PANEL_INCHES, 0]);
  builder.vertices.set(4, [0, PANEL_INCHES, 0]);
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
    materialId: null,
    backMaterialId: null,
  });
  return { guid: 'PANEL', name: 'Panel', isImage: false, alwaysFacesCamera: false, builder };
}

function parsedWith(instanceMaterialId: number | null): ParsedRawData {
  const root = new GeometryBuilder();
  root.instances.push({
    offset: 0,
    refGuid: 'PANEL',
    refIdx: 1,
    name: 'Panel instance',
    // Identity 3x3 + zero translation, in the 12-number layout the builder uses.
    matrix: [1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0],
    materialId: instanceMaterialId,
    children: [],
  });

  const material = texturedMaterial();
  return {
    version: '1.0',
    units: 'Inches',
    layerColors: new Map([['Layer0', [255, 255, 255]]]),
    layerHidden: new Map(),
    layerIdToName: new Map(),
    materialIdToName: new Map([[7, 'Decor']]),
    materialsMap: new Map([['Decor', material]]),
    materialsByFolder: new Map(),
    styles: [],
    defsDict: new Map<number | string, ParsedDefinition>([
      ['ROOT', { guid: 'ROOT', name: 'ROOT_MODEL', isImage: false, alwaysFacesCamera: false, builder: root }],
      [1, panelDefinition()],
    ]),
  };
}

function uvSpan(scene: ReturnType<typeof buildSceneFromParsed>) {
  const prim = scene.glbPrimitives[0];
  let minU = Infinity, maxU = -Infinity, minV = Infinity, maxV = -Infinity;
  for (let i = 0; i < prim.uvs.length; i += 2) {
    minU = Math.min(minU, prim.uvs[i]);
    maxU = Math.max(maxU, prim.uvs[i]);
    minV = Math.min(minV, prim.uvs[i + 1]);
    maxV = Math.max(maxV, prim.uvs[i + 1]);
  }
  return [maxU - minU, maxV - minV];
}

describe('a face inherits the instance material', () => {
  it('scales UVs by the inherited texture tile', () => {
    const scene = buildSceneFromParsed(parsedWith(7));
    const [spanU, spanV] = uvSpan(scene);

    // 24" panel with a 24x12" tile: exactly one repeat across, two down.
    expect(spanU).toBeCloseTo(PANEL_INCHES / TILE_W, 5);
    expect(spanV).toBeCloseTo(PANEL_INCHES / TILE_H, 5);
  });

  it('leaves an unpainted instance on the raw face frame', () => {
    const scene = buildSceneFromParsed(parsedWith(null));
    const [spanU] = uvSpan(scene);

    // No material anywhere, so there is no tile size to scale by.
    expect(spanU).toBeCloseTo(PANEL_INCHES, 5);
  });
});
