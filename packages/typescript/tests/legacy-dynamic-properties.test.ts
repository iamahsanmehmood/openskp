import { describe, it, expect } from 'vitest';
import * as fs from 'fs';
import * as path from 'path';
import { extractLegacyDynamicProperties, stringifyAttrValue } from '../src/legacy';
import { buildScene } from '../src/index';

/**
 * Legacy (pre-2021 MFC) instances have produced empty properties for every
 * single file, because readInstance() was calling preamble(ar, r) - which
 * reads the instance's CAttributeContainer, correctly advancing the byte
 * cursor - and then discarding the return value entirely. This is the same
 * "already-decoded-but-discarded" shape as the earlier layer/face/
 * instance-hidden fixes, just one level deeper: a whole parsed sub-object
 * tree instead of a single byte.
 *
 * SketchUp's Dynamic Components extension stores its data under a
 * dictionary literally named "dynamic_attributes" (stable, publicly
 * documented Ruby API: Entity#attribute_dictionary("dynamic_attributes") -
 * not something reverse-engineered from a fixture).
 */

describe('stringifyAttrValue', () => {
  it('stringifies scalars', () => {
    expect(stringifyAttrValue(null)).toBe('');
    expect(stringifyAttrValue(undefined)).toBe('');
    expect(stringifyAttrValue(42)).toBe('42');
    expect(stringifyAttrValue(3.5)).toBe('3.5');
    expect(stringifyAttrValue('width')).toBe('width');
  });

  it('stringifies arrays by joining', () => {
    expect(stringifyAttrValue([1, 2, 3])).toBe('1,2,3');
    expect(stringifyAttrValue([1.0, 2.0, 3.0])).toBe('1,2,3');
  });
});

describe('extractLegacyDynamicProperties', () => {
  it('extracts the dynamic_attributes dict by name', () => {
    // Real shape from readAttrContainer/readAttrNamed: each child tuple's
    // first element is the ENTITY CLASS NAME (always 'CAttributeNamed',
    // from ar.readObject) - never the dictionary's own declared name,
    // which lives in the value's own `name` field.
    const attrs = {
      k: 'attrs',
      children: [
        ['CAttributeNamed', { k: 'dict', name: 'SU_DefinitionSet', entries: { unrelated: 1 } }],
        [
          'CAttributeNamed',
          {
            k: 'dict',
            name: 'dynamic_attributes',
            entries: { width: 10.0, _width_label: 'Width', count: 4 },
          },
        ],
      ],
    };
    expect(extractLegacyDynamicProperties(attrs)).toEqual({
      width: '10',
      _width_label: 'Width',
      count: '4',
    });
  });

  it('returns {} when no dynamic_attributes dict is present', () => {
    const attrs = {
      k: 'attrs',
      children: [['CAttributeNamed', { k: 'dict', name: 'SU_DefinitionSet', entries: { a: 1 } }]],
    };
    expect(extractLegacyDynamicProperties(attrs)).toEqual({});
  });

  it('returns {} for no attribute container at all', () => {
    expect(extractLegacyDynamicProperties(null)).toEqual({});
    expect(extractLegacyDynamicProperties(undefined)).toEqual({});
  });
});

describe('legacy real-fixture wiring', () => {
  it('does not crash and reports {} for a fixture with no Dynamic Component data', () => {
    // capilla_quiroz_v17.skp (a plain chapel model) has no Dynamic
    // Component data on any of its 3 instances - confirmed by direct
    // inspection of the raw attribute-container reads before writing this
    // fix - so this proves the plumbing fix doesn't break or crash on
    // entities that render no attributes, not the dictionary-lookup logic
    // itself (covered above with synthetic data).
    const filePath = path.join(__dirname, 'fixtures', 'capilla_quiroz_v17.skp');
    const buf = fs.readFileSync(filePath);
    const arrayBuffer = buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength) as ArrayBuffer;
    const scene = buildScene(arrayBuffer);

    function walk(node: { properties: Record<string, string>; children: any[] }): void {
      expect(node.properties).toEqual({});
      for (const child of node.children) walk(child);
    }
    walk(scene.sceneHierarchy);
  });
});
