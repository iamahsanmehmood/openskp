import { describe, it, expect } from 'vitest';
import * as fs from 'fs';
import * as path from 'path';
import { parseSkp, buildScene, toJSON } from '../src/index';

const pythonFixture = (name: string) =>
  path.join(__dirname, '..', '..', 'python', 'tests', 'fixtures', name);

const readBytes = (fullPath: string) => {
  const buf = fs.readFileSync(fullPath);
  return buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength);
};

const EXPECTED_ATTRS: Record<string, string> = {
  v_padded: '  leading and trailing  ',
  v_tab_nl: '\t<render>\n  <exposure value="1.5"/>\n</render>\n',
  v_crlf: 'line1\r\nline2\r\n',
  v_empty: '',
  v_spaces: '   ',
  '  padded_key  ': 'val_padded_key',
  '\ttab_nl_key\n': 'val_tab_nl_key',
  'crlf_key\r\n': 'val_crlf_key',
};

const EXPECTED_SPECIAL_DICTS: Record<string, Record<string, string>> = {
  '  padded_dict  ': { probe: 'ok' },
  '\ttab_dict\n': { probe: 'ok' },
  '   ': { probe: 'ok' },
  '': { probe: 'ok' },
};

describe('attribute string whitespace and entity names (openskp#285)', () => {
  describe.each(['2021', '2017'])('SDK-written %s fixture', (version) => {
    const fixturePath = pythonFixture(`attribute_string_whitespace_${version}.skp`);

    it('preserves attribute values, keys, and dictionary names verbatim', () => {
      const buf = readBytes(fixturePath);
      const model = parseSkp(buf);

      for (const ownerDicts of [model.attributeDictionaries, model.root.attributeDictionaries]) {
        expect(ownerDicts['whitespace_attrs']).toEqual(EXPECTED_ATTRS);
        expect(ownerDicts['dynamic_attributes']).toEqual(EXPECTED_ATTRS);
        for (const [dname, entries] of Object.entries(EXPECTED_SPECIAL_DICTS)) {
          expect(ownerDicts[dname]).toEqual(entries);
        }
      }

      const carrierDefs = Array.from(model.definitions.values()).filter(
        (d) => d.name === 'AttrCarrierDef'
      );
      expect(carrierDefs).toHaveLength(1);
      expect(carrierDefs[0].attributeDictionaries['whitespace_attrs']).toEqual(EXPECTED_ATTRS);
      expect(carrierDefs[0].attributeDictionaries['dynamic_attributes']).toEqual(EXPECTED_ATTRS);
      for (const [dname, entries] of Object.entries(EXPECTED_SPECIAL_DICTS)) {
        expect(carrierDefs[0].attributeDictionaries[dname]).toEqual(entries);
      }

      const scene = buildScene(buf);
      const carrierNodes = scene.sceneHierarchy.children.filter(
        (n) => n.definitionName === 'AttrCarrierDef'
      );
      expect(carrierNodes).toHaveLength(1);
      const node = carrierNodes[0];
      expect(node.properties).toEqual(EXPECTED_ATTRS);
      expect(node.attributeDictionaries['whitespace_attrs']).toEqual(EXPECTED_ATTRS);
      for (const [dname, entries] of Object.entries(EXPECTED_SPECIAL_DICTS)) {
        expect(node.attributeDictionaries[dname]).toEqual(entries);
      }
      expect(node.definitionAttributeDictionaries['whitespace_attrs']).toEqual(EXPECTED_ATTRS);
      expect(node.definitionAttributeDictionaries['dynamic_attributes']).toEqual(EXPECTED_ATTRS);

      const exported = toJSON(model, scene) as any;
      const exportedNode = exported.scene_hierarchy.children.find(
        (n: any) => n.definition_name === 'AttrCarrierDef'
      );
      expect(exportedNode.attribute_dictionaries['whitespace_attrs']).toEqual(EXPECTED_ATTRS);
    });

    it('preserves material, layer, definition, and instance names matching SketchUp', () => {
      const model = parseSkp(readBytes(fixturePath));

      const expectedMaterials = [
        '  Padded Mat  ',
        '\tTabbed\nMat\n',
        version === '2021' ? 'CR\nLF Mat\n' : 'CR\r\nLF Mat\r\n',
        '   ',
      ].sort();
      expect(model.materials.map((m) => m.name).sort()).toEqual(expectedMaterials);

      const expectedLayers = [
        'Layer0',
        '  Padded Tag  ',
        '\tTabbed\nTag\n',
        'CR\r\nLF Tag\r\n',
        '   ',
      ].sort();
      expect(model.layers.map((l) => l.name).sort()).toEqual(expectedLayers);

      const expectedDefs = [
        'AttrCarrierDef',
        '  Padded Def  ',
        '\tTabbed\nDef\n',
        'CR\r\nLF Def\r\n',
        '   ',
      ].sort();
      expect(
        Array.from(model.definitions.values())
          .map((d) => d.name)
          .sort()
      ).toEqual(expectedDefs);

      const expectedInstNames = [
        'AttrCarrierInst',
        'Padded Inst',
        'Tabbed\nInst',
        'CR\r\nLF Inst',
        '',
      ].sort();
      expect(model.root.instances.map((i) => i.name).sort()).toEqual(expectedInstNames);
    });
  });

  it('preserves raw VFF empty/spaces-only keys, untrimmed 6519 instance name, and explicit empty material name', () => {
    const buf = readBytes(pythonFixture('attribute_string_whitespace_raw_vff.skp'));
    const model = parseSkp(buf);

    expect(model.attributeDictionaries['raw_keys']).toEqual({
      '': 'empty_key_val',
      '   ': 'spaces_key_val',
      '  k  ': '  v\n',
    });
    expect(model.materials.map((m) => m.name)).toEqual(['']);
    expect(model.root.instances).toHaveLength(1);
    expect(model.root.instances[0].name).toBe('  Raw Padded Inst\n');

    const scene = buildScene(buf);
    expect(scene.sceneHierarchy.children).toHaveLength(1);
    expect(scene.sceneHierarchy.children[0].name).toBe('  Raw Padded Inst\n');
    expect(scene.sceneHierarchy.children[0].attributeDictionaries['raw_keys']).toEqual({
      '': 'inst_empty_k',
      '   ': 'inst_spaces_k',
    });
  });
});
