import { describe, it, expect } from 'vitest';
import * as fs from 'fs';
import * as path from 'path';
import { parseSkp, buildScene } from '../src/index';

const pythonFixture = (name: string) =>
  path.join(__dirname, '..', '..', 'python', 'tests', 'fixtures', name);

const localFixture = (name: string) => path.join(__dirname, 'fixtures', name);

const readBytes = (fullPath: string) => {
  const buf = fs.readFileSync(fullPath);
  return buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength);
};

const VFF_BOX_DYNAMIC = {
  _name: 'Box',
  _lenx_formula: 'Shelf!Width/2',
  _lenx_label: 'LenX',
  _lengthunits: 'CENTIMETERS',
  _copies_formula: '2',
  _hasbehaviors: 1,
  copies: 2,
  lenx: 10,
};

const LEGACY_BOX_DYNAMIC = {
  _name: 'Box',
  _lenx_formula: 'Shelf!Width/2',
  _lengthunits: 'CENTIMETERS',
  _copies_formula: '2',
  lenx: 10,
};

const SHELF_MODEL_DYNAMIC = {
  _name: 'Shelf',
  width: 25,
  _width_access: 'LIST',
  _width_options: '&Narrow=20&Wide=25&',
  _width_formlabel: 'Shelf width',
};

describe('definition and model attribute dictionaries (openskp#285)', () => {
  describe('VFF 2026 fixture (definition_attribute_dictionaries_2026.skp)', () => {
    const fixturePath = pythonFixture('definition_attribute_dictionaries_2026.skp');

    it('keeps typed dictionaries on definitions', () => {
      const model = parseSkp(readBytes(fixturePath));
      const boxes = Array.from(model.definitions.values()).filter((d) => d.name === 'dc_fixture_box');
      expect(boxes).toHaveLength(1);
      expect(boxes[0].attributeDictionaries.dynamic_attributes).toEqual(VFF_BOX_DYNAMIC);
      expect(boxes[0].attributeDictionaries.fixture_custom).toEqual({
        note: 'definition-level custom dictionary',
      });
    });

    it('keeps typed dictionaries on the model and shares them on root', () => {
      const model = parseSkp(readBytes(fixturePath));
      expect(model.attributeDictionaries.dynamic_attributes).toMatchObject(SHELF_MODEL_DYNAMIC);
      expect(model.root.attributeDictionaries).toEqual(model.attributeDictionaries);
    });

    it('keeps definition dictionaries separate from instance properties and stringifies them on InstanceNode', () => {
      const scene = buildScene(readBytes(fixturePath));
      expect(scene.sceneHierarchy.definitionAttributeDictionaries).toEqual({});
      expect(scene.sceneHierarchy.children).toHaveLength(1);
      const node = scene.sceneHierarchy.children[0];
      expect(node.definitionName).toBe('dc_fixture_box');
      expect(node.properties).toEqual({
        _hasbehaviors: '1',
        _lenx_formula: 'Shelf!Width/2',
        lenx: '12.5',
      });
      expect(node.definitionAttributeDictionaries.dynamic_attributes).toEqual({
        _name: 'Box',
        _lenx_formula: 'Shelf!Width/2',
        _lenx_label: 'LenX',
        _lengthunits: 'CENTIMETERS',
        _copies_formula: '2',
        _hasbehaviors: '1',
        copies: '2',
        lenx: '10',
      });
      expect(node.definitionAttributeDictionaries.fixture_custom).toEqual({
        note: 'definition-level custom dictionary',
      });
    });

    it('gives empty dictionaries when a VFF file has none', () => {
      const buf = readBytes(localFixture('nesting_2021.skp'));
      const model = parseSkp(buf);
      for (const def of model.definitions.values()) {
        expect(def.attributeDictionaries.dynamic_attributes).toBeUndefined();
      }
      const scene = buildScene(buf);
      for (const child of scene.sceneHierarchy.children) {
        expect(child.definitionAttributeDictionaries).toEqual({});
      }
    });
  });

  describe.each(['2017', '2018'])('classic %s fixture (legacy_attribute_dictionaries_%s.skp)', (version) => {
    const fixturePath = pythonFixture(`legacy_attribute_dictionaries_${version}.skp`);

    it('keeps typed dictionaries on classic definitions', () => {
      const model = parseSkp(readBytes(fixturePath));
      const boxes = Array.from(model.definitions.values()).filter((d) => d.name === 'dc_fixture_box');
      expect(boxes).toHaveLength(1);
      expect(boxes[0].attributeDictionaries.dynamic_attributes).toEqual(LEGACY_BOX_DYNAMIC);
      expect(boxes[0].attributeDictionaries.fixture_custom).toEqual({
        note: 'definition-level custom dictionary',
      });
    });

    it('keeps typed dictionaries on the classic model, shares them on root, and excludes ModelProperties', () => {
      const model = parseSkp(readBytes(fixturePath));
      expect(model.attributeDictionaries.dynamic_attributes).toEqual(SHELF_MODEL_DYNAMIC);
      expect(model.attributeDictionaries.ModelProperties).toBeUndefined();
      expect(model.root.attributeDictionaries).toEqual(model.attributeDictionaries);
    });

    it('populates InstanceNode.definitionAttributeDictionaries stringified on classic scenes', () => {
      const scene = buildScene(readBytes(fixturePath));
      expect(scene.sceneHierarchy.definitionAttributeDictionaries).toEqual({});
      expect(scene.sceneHierarchy.children).toHaveLength(1);
      const node = scene.sceneHierarchy.children[0];
      expect(node.definitionName).toBe('dc_fixture_box');
      expect(node.properties).toEqual({ lenx: '12.5' });
      expect(node.definitionAttributeDictionaries.dynamic_attributes).toEqual({
        _name: 'Box',
        _lenx_formula: 'Shelf!Width/2',
        _lengthunits: 'CENTIMETERS',
        _copies_formula: '2',
        lenx: '10',
      });
      expect(node.definitionAttributeDictionaries.fixture_custom).toEqual({
        note: 'definition-level custom dictionary',
      });
    });
  });

  it('excludes ModelProperties and dynamic_attributes on classic files without user dictionaries', () => {
    const model = parseSkp(readBytes(localFixture('legacy_v2019_synthetic.skp')));
    expect(model.attributeDictionaries.ModelProperties).toBeUndefined();
    expect(model.attributeDictionaries.dynamic_attributes).toBeUndefined();
    for (const def of model.definitions.values()) {
      expect(def.attributeDictionaries.dynamic_attributes).toBeUndefined();
    }
  });
});
