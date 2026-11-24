import { describe, it, expect } from 'vitest';
import * as fs from 'fs';
import * as path from 'path';
import { parseSkp, buildScene, buildInstancedScene, create } from '../src/index';
import { buildModelFromParsed, ParsedRawData, InstanceNode } from '../src/model';
import { InstancedNode } from '../src/instanced';
import { GeometryBuilder, extractGeometryFromNodes } from '../src/geometry';
import { parseTlvRecursive } from '../src/parser';
import {
  flattenBakedScene,
  flattenInstancedScene,
  compareTrianglesInOrder,
} from './helpers/flatten-instanced';

function tlv(tag: string, body: Uint8Array): Uint8Array {
  const out = new Uint8Array(6 + body.length);
  out[0] = parseInt(tag.slice(0, 2), 16);
  out[1] = parseInt(tag.slice(2, 4), 16);
  const dv = new DataView(out.buffer);
  dv.setUint32(2, body.length, true);
  out.set(body, 6);
  return out;
}

function concatBytes(...chunks: Uint8Array[]): Uint8Array {
  const total = chunks.reduce((s, c) => s + c.length, 0);
  const out = new Uint8Array(total);
  let off = 0;
  for (const c of chunks) {
    out.set(c, off);
    off += c.length;
  }
  return out;
}

function readFixtureBuffer(filePath: string): ArrayBuffer {
  const buf = fs.readFileSync(filePath);
  return buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength);
}

function toArrayBuffer(u8: Uint8Array): ArrayBuffer {
  return u8.buffer.slice(u8.byteOffset, u8.byteOffset + u8.byteLength) as ArrayBuffer;
}

function collectNodes(node: InstanceNode | InstancedNode): Array<[string, string]> {
  const out: Array<[string, string]> = [[node.name, node.layer]];
  for (const c of node.children) out.push(...collectNodes(c));
  return out;
}

describe('Per-edge and per-face layer reading and scene fallback (#615)', () => {
  it('resolves VFF D207 edge and face layer IDs to layer names', () => {
    const edgeLayered = tlv(
      'B80B',
      concatBytes(tlv('DE05', new Uint8Array([10])), tlv('D007', tlv('D207', new Uint8Array([42]))))
    );
    const edgeDefault = tlv('B80B', tlv('DE05', new Uint8Array([11])));
    const faceLayered = tlv(
      'AC0D',
      concatBytes(tlv('DE05', new Uint8Array([20])), tlv('D007', tlv('D207', new Uint8Array([42]))))
    );
    const faceDefault = tlv('AC0D', tlv('DE05', new Uint8Array([21])));

    const buf = concatBytes(edgeLayered, edgeDefault, faceLayered, faceDefault);
    const builder = new GeometryBuilder();
    extractGeometryFromNodes(parseTlvRecursive(buf, 0, buf.length), builder);

    const parsed: ParsedRawData = {
      version: '25.0.0',
      units: null,
      layerColors: new Map([
        ['Layer0', [136, 136, 136]],
        ['Sky Dome', [0, 0, 255]],
      ]),
      layerHidden: new Map(),
      layerIdToName: new Map([
        [1, 'Layer0'],
        [42, 'Sky Dome'],
      ]),
      pages: [],
      dimensions: [],
      materialIdToName: new Map(),
      materialsMap: new Map(),
      materialsByFolder: new Map(),
      styles: [],
      defsDict: new Map([
        [
          'ROOT',
          {
            guid: 'ROOT',
            name: 'ROOT_MODEL',
            isImage: false,
            alwaysFacesCamera: false,
            builder,
          },
        ],
      ]),
    };

    const model = buildModelFromParsed(parsed);
    expect(model.root.edges.find((e) => e.id === 10)?.layer).toBe('Sky Dome');
    expect(model.root.edges.find((e) => e.id === 11)?.layer).toBe('');
    expect(model.root.faces.find((f) => f.id === 20)?.layer).toBe('Sky Dome');
    expect(model.root.faces.find((f) => f.id === 21)?.layer).toBe('');
  });

  it('reads edge and face layers from classic files', () => {
    const gondola = parseSkp(readFixtureBuffer(path.join(__dirname, 'fixtures', 'gondola_v20.skp')));
    let edgesOnLayer = 0;
    let facesOnLayer = 0;
    let facesDefault = 0;
    const visit = (def: typeof gondola.root) => {
      for (const e of def.edges) {
        if (e.layer === 'Gondulas Laterais') edgesOnLayer++;
      }
      for (const f of def.faces) {
        if (f.layer === 'Gondulas Laterais') facesOnLayer++;
        else if (f.layer === '') facesDefault++;
      }
    };
    visit(gondola.root);
    for (const def of gondola.definitions.values()) visit(def);

    expect(edgesOnLayer).toBeGreaterThan(0);
    expect(facesOnLayer).toBe(229);
    expect(facesDefault).toBe(1658);

    const lineStyles = parseSkp(
      readFixtureBuffer(path.join(__dirname, 'fixtures', 'sdk_saved_tag_line_styles_2020.skp'))
    );
    expect(lineStyles.root.faces.map((f) => f.layer)).toEqual([
      'Tag 0 ',
      'Tag 1 Dash',
      'Tag 2 ',
      'Tag 3 Dash',
      'Tag 4 Dot',
      'Tag 5 Dash',
    ]);
  });

  it('uses face tag colours with unpaintedFaces="tag" and uniform leaf layers on chair_and_table.skp', () => {
    const ab = readFixtureBuffer(
      path.resolve(__dirname, '..', '..', '..', 'examples', 'web-viewer', 'samples', 'chair_and_table.skp')
    );
    const baked = buildScene(ab, { unpaintedFaces: 'tag' });
    const instanced = buildInstancedScene(ab, { unpaintedFaces: 'tag' });

    const rgbSet = (mats: any[]) =>
      new Set(
        mats.map((m) => {
          const bcf = m.pbrMetallicRoughness.baseColorFactor;
          return `${Math.round(bcf[0] * 255)},${Math.round(bcf[1] * 255)},${Math.round(bcf[2] * 255)}`;
        })
      );

    const bakedColors = rgbSet(baked.gltfMaterials);
    const instancedColors = rgbSet(instanced.gltfMaterials);
    expect(bakedColors.has('255,84,84')).toBe(false);
    expect(instancedColors.has('255,84,84')).toBe(false);
    expect(bakedColors.has('120,150,190')).toBe(true);
    expect(bakedColors.has('190,150,120')).toBe(true);
    expect(instancedColors).toEqual(bakedColors);

    const bakedTris = flattenBakedScene(baked);
    const instTris = flattenInstancedScene(instanced);
    expect(instTris.length).toBe(bakedTris.length);
    const cmp = compareTrianglesInOrder(instTris, bakedTris, {
      actual: instanced.gltfMaterials,
      expected: baked.gltfMaterials,
    });
    expect(cmp.firstMismatch).toBeNull();
    expect(cmp.materialMismatches).toBe(0);

    const bakedMap = new Map(collectNodes(baked.sceneHierarchy));
    expect(bakedMap.get('Chair')).toBe('Layer0');
    expect(bakedMap.get('Table')).toBe('Layer0');
    expect(bakedMap.get('Leg_Chair')).toBe('Chair');
    expect(bakedMap.get('Leg_Table')).toBe('Table');
    expect(collectNodes(instanced.sceneHierarchy)).toEqual(collectNodes(baked.sceneHierarchy));
  });

  it('does not move 20 untagged faces or an untagged nested child onto 1 hidden face tag', () => {
    const b = create();
    const hiddenTag = b.addLayer('HiddenTag', { color: [10, 20, 30], hidden: true });
    const childDef = b.addComponentDefinition('Child', (d) => {
      d.addFace([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]]);
    });
    const parentDef = b.addComponentDefinition('MixedParent', (d) => {
      for (let k = 0; k < 20; k++) {
        const x = k * 2;
        d.addFace([[x, 0, 0], [x + 1, 0, 0], [x + 1, 1, 0], [x, 1, 0]]);
      }
      d.addFace([[0, 5, 0], [1, 5, 0], [1, 6, 0], [0, 6, 0]], { layer: hiddenTag });
      d.addInstance(childDef, { name: 'NestedChild', translation: [0, 10, 0] });
    });
    b.addInstance(parentDef, { name: 'MixedPlacement' });

    const ab = toArrayBuffer(b.toBytes());
    const baked = buildScene(ab);
    const instanced = buildInstancedScene(ab);

    for (const meta of Object.values(baked.meshIndex)) {
      expect(meta.layer).toBe('Layer0');
    }
    const expectedNodes: Array<[string, string]> = [
      ['ROOT', 'Layer0'],
      ['MixedPlacement', 'Layer0'],
      ['NestedChild', 'Layer0'],
    ];
    expect(collectNodes(baked.sceneHierarchy)).toEqual(expectedNodes);
    expect(collectNodes(instanced.sceneHierarchy)).toEqual(expectedNodes);
  });

  it('applies uniform face tags to own meshes, root meshes, and childless nodes without leaking to children', () => {
    const b = create();
    const roof = b.addLayer('Roof', { color: [100, 120, 140] });
    const leafTag = b.addLayer('LeafTag', { color: [40, 80, 120] });
    const childDef = b.addComponentDefinition('UntaggedChild', (d) => {
      d.addFace([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]]);
    });
    const parentDef = b.addComponentDefinition('UniformParent', (d) => {
      d.addFace([[0, 0, 0], [2, 0, 0], [2, 2, 0], [0, 2, 0]], { layer: roof });
      d.addInstance(childDef, { name: 'ChildInst', translation: [5, 0, 0] });
    });
    const leafDef = b.addComponentDefinition('LeafDef', (d) => {
      d.addFace([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]], { layer: leafTag });
    });
    b.addFace([[0, 20, 0], [2, 20, 0], [2, 22, 0], [0, 22, 0]], { layer: roof });
    b.addInstance(parentDef, { name: 'ParentInst' });
    b.addInstance(leafDef, { name: 'LeafInst', translation: [10, 0, 0] });

    const ab = toArrayBuffer(b.toBytes());
    const baked = buildScene(ab);
    const instanced = buildInstancedScene(ab);

    const layersByPath = new Map<string, Set<string>>();
    for (const meta of Object.values(baked.meshIndex)) {
      if (!layersByPath.has(meta.path)) layersByPath.set(meta.path, new Set());
      layersByPath.get(meta.path)!.add(meta.layer);
    }
    expect([...layersByPath.get('ROOT')!]).toEqual(['Roof']);
    expect([...layersByPath.get('ROOT / ParentInst')!]).toEqual(['Roof']);
    expect([...layersByPath.get('ROOT / ParentInst / ChildInst')!]).toEqual(['Layer0']);
    expect([...layersByPath.get('ROOT / LeafInst')!]).toEqual(['LeafTag']);

    const expectedNodes: Array<[string, string]> = [
      ['ROOT', 'Layer0'],
      ['ParentInst', 'Layer0'],
      ['ChildInst', 'Layer0'],
      ['LeafInst', 'LeafTag'],
    ];
    expect(collectNodes(baked.sceneHierarchy)).toEqual(expectedNodes);
    expect(collectNodes(instanced.sceneHierarchy)).toEqual(expectedNodes);
  });

  it('keeps an own tag or inherited parent tag instead of overriding with a uniform face tag', () => {
    const b = create();
    const wood = b.addLayer('Wood', { color: [160, 110, 60] });
    const furnitureHidden = b.addLayer('FurnitureHidden', { color: [40, 40, 40], hidden: true });
    const groupTag = b.addLayer('GroupTag', { color: [30, 140, 90] });

    const woodPanel = b.addComponentDefinition('WoodPanel', (d) => {
      d.addFace([[0, 0, 0], [2, 0, 0], [2, 2, 0], [0, 2, 0]], { layer: wood });
    });
    const parentGroup = b.addComponentDefinition('ParentGroup', (d) => {
      d.addInstance(woodPanel, { name: 'UntaggedInGroup', translation: [5, 0, 0] });
    });
    b.addInstance(woodPanel, { name: 'HiddenFurniture', layer: furnitureHidden });
    b.addInstance(parentGroup, { name: 'TaggedGroup', layer: groupTag, translation: [10, 0, 0] });

    const ab = toArrayBuffer(b.toBytes());
    const baked = buildScene(ab, { unpaintedFaces: 'tag' });
    const instanced = buildInstancedScene(ab, { unpaintedFaces: 'tag' });

    const meshLayers = new Map<string, string>();
    for (const meta of Object.values(baked.meshIndex)) {
      meshLayers.set(meta.path, meta.layer);
    }
    expect(meshLayers.get('ROOT / HiddenFurniture')).toBe('FurnitureHidden');
    expect(meshLayers.get('ROOT / TaggedGroup / UntaggedInGroup')).toBe('GroupTag');

    const expectedNodes: Array<[string, string]> = [
      ['ROOT', 'Layer0'],
      ['HiddenFurniture', 'FurnitureHidden'],
      ['TaggedGroup', 'GroupTag'],
      ['UntaggedInGroup', 'GroupTag'],
    ];
    expect(collectNodes(baked.sceneHierarchy)).toEqual(expectedNodes);
    expect(collectNodes(instanced.sceneHierarchy)).toEqual(expectedNodes);

    const rgbSet = (mats: any[]) =>
      new Set(
        mats.map((m) => {
          const bcf = m.pbrMetallicRoughness.baseColorFactor;
          return `${Math.round(bcf[0] * 255)},${Math.round(bcf[1] * 255)},${Math.round(bcf[2] * 255)}`;
        })
      );
    expect(rgbSet(baked.gltfMaterials).has('160,110,60')).toBe(true);
    expect(rgbSet(instanced.gltfMaterials).has('160,110,60')).toBe(true);
  });
});
