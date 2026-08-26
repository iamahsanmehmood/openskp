import { describe, it, expect } from 'vitest';
import { buildModelFromParsed, ParsedRawData } from '../src/model';

/**
 * Layer.hidden - the on/off visibility bit. Already correctly extracted
 * from legacy MFC files (legacy.ts's readLayer) but previously discarded
 * before reaching the public model; now wired through a layerHidden map
 * alongside the existing layerColors/layerIdToName maps. VFF files carry
 * no known visibility tag, so they always default to false.
 */

function parsed(layerColors: Map<string, [number, number, number]>, layerHidden: Map<string, boolean>): ParsedRawData {
  return {
    version: 'test',
    units: null,
    layerColors,
    layerHidden,
    layerIdToName: new Map(),
    pages: [],
    dimensions: [],
    materialIdToName: new Map(),
    materialsMap: new Map(),
    materialsByFolder: new Map(),
    styles: [],
    defsDict: new Map(),
  };
}

describe('buildModelFromParsed layer hidden', () => {
  it('reports a hidden layer as hidden', () => {
    const layerColors = new Map<string, [number, number, number]>([
      ['Layer0', [136, 136, 136]],
      ['Furniture', [200, 50, 50]],
    ]);
    const layerHidden = new Map<string, boolean>([
      ['Layer0', false],
      ['Furniture', true],
    ]);

    const model = buildModelFromParsed(parsed(layerColors, layerHidden));
    const byName = new Map(model.layers.map((l) => [l.name, l]));

    expect(byName.get('Layer0')?.hidden).toBe(false);
    expect(byName.get('Furniture')?.hidden).toBe(true);
  });

  it('defaults to visible when the layerHidden map has no entry', () => {
    const layerColors = new Map<string, [number, number, number]>([['Layer0', [136, 136, 136]]]);
    const layerHidden = new Map<string, boolean>();

    const model = buildModelFromParsed(parsed(layerColors, layerHidden));

    expect(model.layers[0].hidden).toBe(false);
  });
});
