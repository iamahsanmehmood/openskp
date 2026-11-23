import { describe, it, expect } from 'vitest';
import * as fs from 'fs';
import * as path from 'path';
import {
  parseSkp,
  buildScene,
  buildInstancedScene,
  fromFragments,
  toFragments,
  toOBJ,
  type SkpScene,
  type InstancedScene,
} from '../src/index';

function loadFixture(name: string): ArrayBuffer {
  const bytes = fs.readFileSync(path.join(__dirname, 'fixtures', name));
  return bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer;
}

describe('Scene and InstancedScene pages (#593)', () => {
  it.each([
    ['gondola_v20.skp', ['Cena1']],
    ['sdk_saved_2020.skp', ['With camera', 'No camera', 'Plain']],
    ['scene_without_camera_2021.skp', ['With camera', 'No camera', 'Plain']],
    ['nesting_2017.skp', []],
  ])('matches parseSkp(buf).pages on %s', (name, expectedNames) => {
    const ab = loadFixture(name);
    const model = parseSkp(ab);
    const scene = buildScene(ab);
    const instanced = buildInstancedScene(ab);

    expect(model.pages.map((p) => p.name)).toEqual(expectedNames);
    expect(scene.pages).toEqual(model.pages);
    expect(instanced.pages).toEqual(model.pages);
  });

  it('keeps native SketchUp Z-up inches and hidden layers on scene pages', () => {
    const ab = loadFixture('scene_without_camera_2021.skp');
    for (const pages of [buildScene(ab).pages, buildInstancedScene(ab).pages]) {
      expect(pages).toBeDefined();
      expect(pages![0].eye).toEqual([100, -200, 80]);
      expect(pages![0].target).toEqual([5, 5, 0]);
      expect(pages![1].eye).toBeNull();
      expect(pages![1].hiddenLayers).toEqual(['Furniture']);
    }
  });

  it('reports empty pages on scenes loaded from .frag and keeps pages optional on literals', () => {
    const instanced = buildInstancedScene(loadFixture('scene_without_camera_2021.skp'));
    expect(instanced.pages).toHaveLength(3);
    expect(fromFragments(toFragments(instanced)).pages).toEqual([]);

    const manualScene: SkpScene = {
      sceneHierarchy: {
        name: 'ROOT',
        definitionName: 'ROOT_MODEL',
        layer: 'Layer0',
        positionMm: [0, 0, 0],
        properties: {},
        attributeDictionaries: {},
        children: [],
        path: 'ROOT',
        nameIsGenerated: false,
      },
      meshIndex: {},
      glbPrimitives: [],
      gltfMaterials: [],
      textures: [],
      bounds: null,
    };
    const manualInstanced: InstancedScene = {
      bounds: null,
      sceneHierarchy: {
        name: 'ROOT',
        nameIsGenerated: false,
        definitionName: 'ROOT_MODEL',
        layer: 'Layer0',
        guid: '',
        matrix: [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1],
        positionMm: [0, 0, 0],
        properties: {},
        attributeDictionaries: {},
        meshResourceId: null,
        children: [],
      },
      meshResources: [],
      gltfMaterials: [],
      textures: [],
      layerHidden: {},
    };
    expect(toOBJ(manualScene)).toContain('#');
    expect(toFragments(manualInstanced).byteLength).toBeGreaterThan(0);
  });
});
