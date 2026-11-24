import * as path from 'path';
import { describe, expect, it } from 'vitest';
import { SkpFile, SkpWriteError, create } from '../src';
import { CONTAINER_TAGS, parseTlvRecursive } from '../src/parser';
import {
  GeometryBuilder,
  collectDefs,
  extractAttributeDictionaries,
  extractGeometryFromNodes,
} from '../src/geometry';

const FIXTURES = path.join(__dirname, 'fixtures');
const SQUARE_PTS: [number, number, number][] = [
  [0, 0, 0],
  [10, 0, 0],
  [10, 10, 0],
  [0, 10, 0],
];

function tlv(tagHex: string, payload: Uint8Array): Uint8Array {
  const b0 = parseInt(tagHex.slice(0, 2), 16);
  const b1 = parseInt(tagHex.slice(2, 4), 16);
  const out = new Uint8Array(6 + payload.length);
  out[0] = b0;
  out[1] = b1;
  new DataView(out.buffer).setUint32(2, payload.length, true);
  out.set(payload, 6);
  return out;
}

function concat(parts: Uint8Array[]): Uint8Array {
  const total = parts.reduce((acc, p) => acc + p.length, 0);
  const out = new Uint8Array(total);
  let pos = 0;
  for (const p of parts) {
    out.set(p, pos);
    pos += p.length;
  }
  return out;
}

function utf8(s: string): Uint8Array {
  return new TextEncoder().encode(s);
}

function f64Bytes(values: number[]): Uint8Array {
  const out = new Uint8Array(values.length * 8);
  const dv = new DataView(out.buffer);
  values.forEach((v, i) => dv.setFloat64(i * 8, v, true));
  return out;
}

function i32Bytes(value: number): Uint8Array {
  const out = new Uint8Array(4);
  new DataView(out.buffer).setInt32(0, value, true);
  return out;
}

function entryValue(inner: Uint8Array): Uint8Array {
  return tlv('A438', inner);
}

function entry(key: string, inner: Uint8Array): Uint8Array {
  return concat([tlv('B636', utf8(key)), entryValue(inner)]);
}

function namedDict(name: string, entries: Uint8Array): Uint8Array {
  return concat([tlv('B436', utf8(name)), tlv('B536', entries)]);
}

function containsSubarray(haystack: Uint8Array, needle: Uint8Array): boolean {
  outer: for (let i = 0; i <= haystack.length - needle.length; i++) {
    for (let j = 0; j < needle.length; j++) {
      if (haystack[i + j] !== needle[j]) continue outer;
    }
    return true;
  }
  return false;
}

describe('component identities and typed definition attributes (#478)', () => {
  it('matches component GUIDs across classic and VFF containers', () => {
    for (const filename of ['nesting_2017.skp', 'nesting_2020.skp', 'nesting_2021.skp']) {
      const model = SkpFile.open(path.join(FIXTURES, filename)).parse();
      const shelves = [...model.definitions.values()].filter((d) => d.name === 'Shelf');
      expect(shelves).toHaveLength(1);
      expect(shelves[0].guid).toBe('1614E9C58C0B40338881DDE62E6E2D80');
      let references = 0;
      for (const defn of [model.root, ...model.definitions.values()]) {
        for (const inst of defn.instances) {
          if (inst.refIdx === null) continue;
          expect(model.definitions.has(inst.refIdx)).toBe(true);
          references++;
        }
      }
      expect(references).toBeGreaterThan(0);
      if (filename === 'nesting_2021.skp') {
        expect(model.root.instances).toHaveLength(1);
        expect(model.root.instances[0].guid).toBe('0BFF75653E97458E9B7238A6E9CF1A99');
      }
    }
  });

  it('uses canonical UUID order for VFF definition and instance GUIDs', () => {
    const native = new Uint8Array([
      0xc5, 0xe9, 0x14, 0x16, 0x0b, 0x8c, 0x33, 0x40,
      0x88, 0x81, 0xdd, 0xe6, 0x2e, 0x6e, 0x2d, 0x80,
    ]);
    const canonical = '1614E9C58C0B40338881DDE62E6E2D80';
    const defBytes = tlv(
      '7C15',
      concat([tlv('DE05', new Uint8Array([1])), tlv('7D15', native), tlv('7E15', utf8('Shelf'))])
    );
    const defs = collectDefs(parseTlvRecursive(defBytes, 0, defBytes.length, CONTAINER_TAGS));
    expect(defs.get(1)?.guid).toBe(canonical);

    const instBytes = tlv('6419', tlv('6819', native));
    const builder = new GeometryBuilder();
    extractGeometryFromNodes(
      parseTlvRecursive(instBytes, 0, instBytes.length, CONTAINER_TAGS),
      builder
    );
    expect(builder.instances).toHaveLength(1);
    expect(builder.instances[0].refGuid).toBe(canonical);
    expect(builder.instances[0].refIdx).toBe(-1);
  });

  it('keeps native definition attribute dictionary types without mixing child attributes', () => {
    const values = concat([
      entry('count', tlv('A738', i32Bytes(-7))),
      entry('flag', tlv('AA38', new Uint8Array([1]))),
      entry('disabled', tlv('AA38', new Uint8Array([0]))),
      entry('nothing', new Uint8Array(0)),
      entry(
        'values',
        tlv('AE38', concat([entryValue(tlv('A938', f64Bytes([2.5]))), entryValue(tlv('AD38', utf8('abc')))]))
      ),
    ]);
    const own = tlv('8813', tlv('D007', tlv('DC05', namedDict('Plugin', values))));
    const child = tlv(
      '6419',
      concat([
        tlv('6719', new Uint8Array([2])),
        tlv('D007', tlv('DC05', namedDict('Plugin', entry('count', tlv('A738', i32Bytes(99)))))),
      ])
    );
    const rawBytes = concat([
      tlv('7C15', concat([tlv('DE05', new Uint8Array([1])), own, child])),
      tlv('7C15', concat([tlv('DE05', new Uint8Array([2])), child])),
    ]);
    const defs = collectDefs(parseTlvRecursive(rawBytes, 0, rawBytes.length, CONTAINER_TAGS));
    expect(defs.size).toBe(2);
    expect(defs.get(1)?.attributeDictionaries).toEqual({
      Plugin: {
        count: -7,
        flag: true,
        disabled: false,
        nothing: null,
        values: [2.5, 'abc'],
      },
    });
    expect(defs.get(2)?.attributeDictionaries).toEqual({});
    const childD007 = defs.get(1)!.builder.instances[0].children.find((c) => c.tag === 'D007')!;
    expect(extractAttributeDictionaries(childD007)).toEqual({
      Plugin: { count: 99 },
    });
  });

  it('writes explicit model GUID in native Windows byte order', () => {
    const builder = create();
    builder.addFace(SQUARE_PTS);
    const native = new Uint8Array([
      0x33, 0x22, 0x11, 0x00, 0x55, 0x44, 0x77, 0x46,
      0x88, 0x99, 0xaa, 0xbb, 0xcc, 0xdd, 0xee, 0xff,
    ]);
    for (const guid of [
      '00112233-4455-4677-8899-aabbccddeeff',
      '00112233445546778899AABBCCDDEEFF',
    ]) {
      builder.setGuid(guid);
      const data = builder.toBytes();
      expect(data.length).toBeGreaterThanOrEqual(68);
      expect(data.slice(52, 68)).toEqual(native);
    }
    expect(() => builder.setGuid('not-a-guid')).toThrow(SkpWriteError);
    expect(() => builder.setGuid('00112233445546778899AABBCCDDEEFZ')).toThrow(SkpWriteError);
    expect(() => builder.setGuid('00112233-4455-4677-8899a-abbccddeeff')).toThrow(SkpWriteError);
    expect(builder.toBytes().slice(52, 68)).toEqual(native);

    builder.setGuid('');
    const reset = builder.toBytes();
    expect(reset.slice(52, 68)).not.toEqual(native);
    expect(reset.slice(52, 68)).toEqual(builder.toBytes().slice(52, 68));
    expect(reset[52 + 7] & 0xf0).toBe(0x40);
    expect(reset[52 + 8] & 0xc0).toBe(0x80);
  });

  it('preserves definition GUID in archive and public model', () => {
    const native = new Uint8Array([
      0x33, 0x22, 0x11, 0x00, 0x55, 0x44, 0x77, 0x46,
      0x88, 0x99, 0xaa, 0xbb, 0xcc, 0xdd, 0xee, 0xff,
    ]);
    for (const guid of [
      '00112233-4455-4677-8899-aabbccddeeff',
      '00112233445546778899AABBCCDDEEFF',
    ]) {
      const builder = create();
      const def = builder.addComponentDefinition(
        'Identified',
        (d) => {
          d.addFace(SQUARE_PTS);
        },
        { guid }
      );
      builder.addInstance(def);
      const data = builder.toBytes();
      expect(containsSubarray(data, native)).toBe(true);
      const model = SkpFile.fromBuffer(data).parse();
      expect(model.definitions.size).toBe(1);
      const onlyDef = [...model.definitions.values()][0];
      expect(onlyDef.guid).toBe('00112233445546778899AABBCCDDEEFF');
      expect(model.root.instances).toHaveLength(1);
      const refIdx = model.root.instances[0].refIdx;
      expect(refIdx).not.toBeNull();
      expect(model.definitions.get(refIdx!)?.guid).toBe('00112233445546778899AABBCCDDEEFF');
    }
  });

  it('does not partially add definition when GUID is invalid', () => {
    const builder = create();
    expect(() =>
      builder.addComponentDefinition(
        'Invalid',
        (d) => {
          d.addFace(SQUARE_PTS);
        },
        { guid: 'not-a-guid' }
      )
    ).toThrow(SkpWriteError);
    const good = builder.addComponentDefinition('Valid', (d) => {
      d.addFace(SQUARE_PTS);
    });
    builder.addInstance(good);
    const model = SkpFile.fromBuffer(builder.toBytes()).parse();
    expect(model.definitions.size).toBe(1);
    expect([...model.definitions.values()][0].name).toBe('Valid');
  });

  it('generates distinct and stable default definition GUIDs', () => {
    const builder = create();
    const a = builder.addComponentDefinition('Same name', (d) => {
      d.addFace(SQUARE_PTS);
    });
    const b = builder.addComponentDefinition('Same name', (d) => {
      d.addFace(SQUARE_PTS);
    });
    builder.addInstance(a);
    builder.addInstance(b);
    const first = SkpFile.fromBuffer(builder.toBytes()).parse();
    const second = SkpFile.fromBuffer(builder.toBytes()).parse();
    expect(first.definitions.size).toBe(2);
    const defs = [...first.definitions.values()];
    expect(defs[0].guid).not.toBe(defs[1].guid);
    for (const [id, defn] of first.definitions) {
      expect(defn.guid).toBe(second.definitions.get(id)?.guid);
    }
  });
});
