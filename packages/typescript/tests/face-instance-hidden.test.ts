import { describe, it, expect } from 'vitest';
import { buildModelFromParsed, ParsedRawData } from '../src/model';
import { GeometryBuilder } from '../src/geometry';

/**
 * Face.hidden / Instance.hidden - the same per-element "Hide" bit edges
 * already exposed. Confirmed by directly scanning a real fixture
 * (Untitled.skp) that every single face (1588/1588) and instance (46/46)
 * carries a D307 child under its D007 container - the exact same
 * display-flags record edges already read (base 0x06, +0x01 hidden bit) -
 * just never looked up for these two entity types.
 */

function parsed(defsDict: Map<number | string, any>): ParsedRawData {
  return {
    version: 'test',
    units: null,
    layerColors: new Map(),
    layerHidden: new Map(),
    layerIdToName: new Map(),
    pages: [],
    dimensions: [],
    materialIdToName: new Map(),
    materialsMap: new Map(),
    materialsByFolder: new Map(),
    styles: [],
    defsDict,
  };
}

describe('buildModelFromParsed face/instance hidden', () => {
  it('reports a hidden face and instance as hidden', () => {
    const builder = new GeometryBuilder();
    builder.faces.set(1, { loops: [], normal: [0, 0, 1], hidden: true });
    builder.faces.set(2, { loops: [], normal: [0, 0, 1], hidden: false });
    builder.instances.push({
      offset: 0,
      refGuid: '',
      refIdx: -1,
      name: 'hidden_one',
      matrix: [],
      materialId: null,
      hidden: true,
      children: [],
    });
    builder.instances.push({
      offset: 0,
      refGuid: '',
      refIdx: -1,
      name: 'visible_one',
      matrix: [],
      materialId: null,
      hidden: false,
      children: [],
    });

    const defsDict = new Map<number | string, any>([
      ['ROOT', { guid: 'ROOT', name: 'ROOT_MODEL', isImage: false, alwaysFacesCamera: false, builder }],
    ]);

    const model = buildModelFromParsed(parsed(defsDict));

    expect(model.root.faces.find((f) => f.id === 1)?.hidden).toBe(true);
    expect(model.root.faces.find((f) => f.id === 2)?.hidden).toBe(false);
    expect(model.root.instances.find((i) => i.name === 'hidden_one')?.hidden).toBe(true);
    expect(model.root.instances.find((i) => i.name === 'visible_one')?.hidden).toBe(false);
  });

  it('defaults to visible when hidden is missing', () => {
    const builder = new GeometryBuilder();
    builder.faces.set(1, { loops: [], normal: [0, 0, 1] });
    builder.instances.push({
      offset: 0,
      refGuid: '',
      refIdx: -1,
      name: 'n',
      matrix: [],
      materialId: null,
      children: [],
    });

    const defsDict = new Map<number | string, any>([
      ['ROOT', { guid: 'ROOT', name: 'ROOT_MODEL', isImage: false, alwaysFacesCamera: false, builder }],
    ]);

    const model = buildModelFromParsed(parsed(defsDict));

    expect(model.root.faces[0].hidden).toBe(false);
    expect(model.root.instances[0].hidden).toBe(false);
  });
});
