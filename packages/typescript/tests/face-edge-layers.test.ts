import { describe, it, expect } from 'vitest';
import * as fs from 'fs';
import * as path from 'path';
import { parseSkp, buildScene, buildInstancedScene } from '../src/index';
import { buildModelFromParsed, ParsedRawData } from '../src/model';
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

describe('Per-edge and per-face layer reading (#285 / #526 / #543)', () => {
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

  it('uses majority face layer in buildScene and buildInstancedScene for chair_and_table.skp (#543)', () => {
    const ab = readFixtureBuffer(
      path.resolve(__dirname, '..', '..', '..', 'examples', 'web-viewer', 'samples', 'chair_and_table.skp')
    );
    const baked = buildScene(ab);
    const instanced = buildInstancedScene(ab);

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
    const instancedTris = flattenInstancedScene(instanced);
    expect(instancedTris.length).toBe(bakedTris.length);
    const { firstMismatch, materialMismatches } = compareTrianglesInOrder(instancedTris, bakedTris, {
      actual: instanced.gltfMaterials,
      expected: baked.gltfMaterials,
    });
    expect(firstMismatch).toBeNull();
    expect(materialMismatches).toBe(0);

    const collectLayers = (n: { name: string; layer: string; children: any[] }): [string, string][] => [
      [n.name, n.layer],
      ...n.children.flatMap(collectLayers),
    ];
    expect(collectLayers(instanced.sceneHierarchy)).toEqual(collectLayers(baked.sceneHierarchy));
  });
});
