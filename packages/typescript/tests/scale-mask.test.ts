import * as fs from 'fs';
import * as path from 'path';
import { describe, expect, it } from 'vitest';
import { create, openExisting, parseSkp, toTypeScriptCode } from '../src/index';

const CPP_FIXTURES = path.resolve(__dirname, '../../cpp/tests/fixtures');

function readFixtureBuffer(name: string): ArrayBuffer {
  const buf = fs.readFileSync(path.join(CPP_FIXTURES, name));
  return buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength);
}

describe('noScaleMask (#519)', () => {
  it('round-trips native scale masks on root and definitions', () => {
    for (const mask of [0, 1, 2, 4, 8, 16, 32, 64, 127, 0x12345678]) {
      const builder = create();
      builder.setDocumentOptions({ noScaleMask: mask });
      const def = builder.addComponentDefinition(
        'MaskProbe',
        (d) => {
          d.addFace([
            [0, 0, 0],
            [10, 0, 0],
            [0, 10, 0],
          ]);
        },
        {
          alwaysFacesCamera: true,
          shadowsFaceSun: true,
          noScaleMask: mask,
        }
      );
      builder.addInstance(def);
      const bytes = builder.toBytes();
      const model = parseSkp(bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength));
      expect(model.definitions.size).toBe(1);
      const actual = [...model.definitions.values()][0];
      expect(actual.noScaleMask).toBe(mask);
      expect(model.root.noScaleMask).toBe(mask);
      expect(actual.alwaysFacesCamera).toBe(true);
      expect(actual.shadowsFaceSun).toBe(true);
      expect(actual.isGroup).toBe(false);
    }
  });

  it('reads SDK scale mask fixtures', () => {
    for (const name of ['behavior_mask_2017.skp', 'behavior_mask_2021.skp']) {
      const model = parseSkp(readFixtureBuffer(name));
      expect(model.definitions.size).toBe(1);
      const actual = [...model.definitions.values()][0];
      expect(actual.noScaleMask).toBe(127);
    }
  });

  it('reads SDK root scale mask fixtures', () => {
    for (const name of [
      'root_behavior_mask_2013.skp',
      'root_behavior_mask_2017.skp',
      'root_behavior_mask_2021.skp',
    ]) {
      const model = parseSkp(readFixtureBuffer(name));
      expect(model.root.noScaleMask).toBe(name.includes('2021') ? 127 : 2);
      expect(model.definitions.size).toBe(1);
      const actual = [...model.definitions.values()][0];
      expect(actual.noScaleMask).toBe(0);
    }
  });

  it('reports 0 scale mask on app-saved files with CDib thumbnails', () => {
    for (const name of [
      'capilla_quiroz_v17.skp',
      'gondola_v20.skp',
      'zero_material_custom_layer_v18.skp',
    ]) {
      const model = parseSkp(readFixtureBuffer(name));
      expect(model.root.noScaleMask, name).toBe(0);
      for (const defn of model.definitions.values()) {
        expect(defn.noScaleMask, name).toBe(0);
      }
    }
  });

  it('survives openExisting and toTypeScriptCode', () => {
    const builder = create();
    builder.setDocumentOptions({ noScaleMask: 16 });
    const def = builder.addComponentDefinition(
      'Masked',
      (d) => {
        d.addFace([
          [0, 0, 0],
          [10, 0, 0],
          [0, 10, 0],
        ]);
      },
      { noScaleMask: 64 }
    );
    builder.addInstance(def);

    const srcBytes = builder.toBytes();
    const srcAb = srcBytes.buffer.slice(srcBytes.byteOffset, srcBytes.byteOffset + srcBytes.byteLength);
    const edited = openExisting(srcAb);
    const outBytes = edited.builder.toBytes();
    const model = parseSkp(outBytes.buffer.slice(outBytes.byteOffset, outBytes.byteOffset + outBytes.byteLength));
    expect(model.definitions.size).toBe(1);
    const actual = [...model.definitions.values()][0];
    expect(actual.noScaleMask).toBe(64);
    expect(model.root.noScaleMask).toBe(16);

    const code = toTypeScriptCode(model);
    expect(code).toContain('noScaleMask: 64');
    expect(code).toContain('noScaleMask: 16');
  });
});
