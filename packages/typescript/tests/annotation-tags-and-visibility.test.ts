import * as fs from 'node:fs';
import * as path from 'node:path';
import { describe, expect, it } from 'vitest';
import { create, parseSkp, SkpWriteError } from '../src';
import { GeometryBuilder, ParsedDefinition } from '../src/geometry';
import { composeInstanceWorld, parseDimensions } from '../src/pages-dimensions';
import { transformPoint } from '../src/transforms';

const FIXTURES = path.join(__dirname, 'fixtures');

function tlv(tag: number, payload: Uint8Array): Uint8Array {
  const out = new Uint8Array(6 + payload.length);
  const view = new DataView(out.buffer);
  view.setUint16(0, tag, true);
  view.setUint32(2, payload.length, true);
  out.set(payload, 6);
  return out;
}

function concatBytes(parts: Uint8Array[]): Uint8Array {
  let total = 0;
  for (const p of parts) total += p.length;
  const out = new Uint8Array(total);
  let off = 0;
  for (const p of parts) {
    out.set(p, off);
    off += p.length;
  }
  return out;
}

function u32Bytes(v: number): Uint8Array {
  const out = new Uint8Array(4);
  new DataView(out.buffer).setUint32(0, v, true);
  return out;
}

function f64s(vals: number[]): Uint8Array {
  const out = new Uint8Array(vals.length * 8);
  const view = new DataView(out.buffer);
  for (let i = 0; i < vals.length; i++) view.setFloat64(i * 8, vals[i], true);
  return out;
}

function makeParsedDef(builder: GeometryBuilder): ParsedDefinition {
  return {
    guid: '',
    name: '',
    isImage: false,
    alwaysFacesCamera: false,
    builder,
  };
}

describe('annotation tags and visibility (#488)', () => {
  it('composeInstanceWorld nests parent times local', () => {
    const panel = new GeometryBuilder();
    const cabinet = new GeometryBuilder();
    cabinet.instances.push({
      persistentId: 'AA',
      offset: 0,
      refGuid: '',
      refIdx: 2,
      name: '',
      matrix: [1, 0, 0, 0, 1, 0, 0, 0, 1, 1, 2, 3, 1],
      materialId: null,
      children: [],
    });
    const root = new GeometryBuilder();
    root.instances.push({
      persistentId: 'BB',
      offset: 0,
      refGuid: '',
      refIdx: 1,
      name: '',
      matrix: [1, 0, 0, 0, 1, 0, 0, 0, 1, 10, 20, 30, 1],
      materialId: null,
      children: [],
    });
    const defs = new Map<number | string, ParsedDefinition>([
      [1, makeParsedDef(cabinet)],
      [2, makeParsedDef(panel)],
    ]);
    const world = new Map<string, number[] | null>();
    composeInstanceWorld(root, defs, world);
    expect(transformPoint(world.get('BB')!, [0, 0, 0])).toEqual([10, 20, 30]);
    expect(transformPoint(world.get('AA')!, [0, 0, 0])).toEqual([11, 22, 33]);
  });

  it('dimension connected point uses composed nested world', () => {
    const vid = new Uint8Array([0xaa, 0xbb, 0x01]);
    const iid = new Uint8Array([0xcc, 0xdd, 0x02]);
    const idLenPrefixed = concatBytes([new Uint8Array([iid.length]), iid]);
    const refTlv = tlv(0x53fc, concatBytes([tlv(0x53fd, vid), tlv(0x53fe, idLenPrefixed)]));
    const connA = tlv(0x5bcd, tlv(0x5208, concatBytes([tlv(0x5209, u32Bytes(2)), tlv(0x520b, refTlv)])));
    const connB = tlv(0x5bce, tlv(0x5208, concatBytes([tlv(0x5209, u32Bytes(1)), tlv(0x520a, f64s([0, 0, 0]))])));
    const blob = tlv(0x5bcc, concatBytes([connA, connB, tlv(0x5bd2, f64s([0]))]));

    const leaf = new GeometryBuilder();
    const parentDef = new GeometryBuilder();
    parentDef.instances.push({
      persistentId: 'CCDD02',
      offset: 0,
      refGuid: '',
      refIdx: 2,
      name: '',
      matrix: [1, 0, 0, 0, 1, 0, 0, 0, 1, 1, 2, 3, 1],
      materialId: null,
      children: [],
    });
    const root = new GeometryBuilder();
    root.instances.push({
      persistentId: 'PARENT',
      offset: 0,
      refGuid: '',
      refIdx: 1,
      name: '',
      matrix: [1, 0, 0, 0, 1, 0, 0, 0, 1, 10, 20, 30, 1],
      materialId: null,
      children: [],
    });
    const defs = new Map<number | string, ParsedDefinition>([
      [1, makeParsedDef(parentDef)],
      [2, makeParsedDef(leaf)],
    ]);
    const world = new Map<string, number[] | null>();
    composeInstanceWorld(root, defs, world);
    const dims = parseDimensions(blob, new Map([['AABB01', [0, 0, 0]]]), world);
    expect(dims).toHaveLength(1);
    expect(dims[0].a).toEqual([11, 22, 33]);
  });

  it('dimension connected point uses innermost instance in path', () => {
    const vid = new Uint8Array([0xaa, 0xbb, 0x01]);
    const outerId = new Uint8Array([0x11, 0x01]);
    const innerId = new Uint8Array([0x22, 0x02]);
    const instPath = concatBytes([
      new Uint8Array([outerId.length]),
      outerId,
      new Uint8Array([innerId.length]),
      innerId,
    ]);
    const refTlv = tlv(0x53fc, concatBytes([tlv(0x53fd, vid), tlv(0x53fe, instPath)]));
    const connA = tlv(0x5bcd, tlv(0x5208, concatBytes([tlv(0x5209, u32Bytes(2)), tlv(0x520b, refTlv)])));
    const connB = tlv(0x5bce, tlv(0x5208, concatBytes([tlv(0x5209, u32Bytes(1)), tlv(0x520a, f64s([0, 0, 0]))])));
    const blob = tlv(0x5bcc, concatBytes([connA, connB, tlv(0x5bd2, f64s([0]))]));

    const leaf = new GeometryBuilder();
    const parentDef = new GeometryBuilder();
    parentDef.instances.push({
      persistentId: '2202',
      offset: 0,
      refGuid: '',
      refIdx: 2,
      name: '',
      matrix: [1, 0, 0, 0, 1, 0, 0, 0, 1, 100, 0, 0, 1],
      materialId: null,
      children: [],
    });
    const root = new GeometryBuilder();
    root.instances.push({
      persistentId: '1101',
      offset: 0,
      refGuid: '',
      refIdx: 1,
      name: '',
      matrix: [1, 0, 0, 0, 1, 0, 0, 0, 1, 10, 0, 0, 1],
      materialId: null,
      children: [],
    });
    const defs = new Map<number | string, ParsedDefinition>([
      [1, makeParsedDef(parentDef)],
      [2, makeParsedDef(leaf)],
    ]);
    const world = new Map<string, number[] | null>();
    composeInstanceWorld(root, defs, world);
    const dims = parseDimensions(blob, new Map([['AABB01', [0, 0, 0]]]), world);
    expect(dims).toHaveLength(1);
    expect(dims[0].a).toEqual([110, 0, 0]);
  });

  it.each(['annotation_layers_legacy.skp', 'annotation_layers_vff.skp'])(
    'SDK annotation tags retain owner and visibility (%s)',
    (name) => {
      const bytes = fs.readFileSync(path.join(FIXTURES, name));
      const buf = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength);
      const model = parseSkp(buf);

      const verify = (owner: typeof model.root, nested: boolean) => {
        const tagged = (
          entities: { layer: string; hidden: boolean }[],
          tag: string,
          count: number,
          hiddenCount: number
        ) => {
          expect(entities).toHaveLength(count);
          let actualHidden = 0;
          for (const ent of entities) {
            expect(ent.layer).toBe(tag);
            if (ent.hidden) actualHidden += 1;
          }
          expect(actualHidden).toBe(hiddenCount);
        };

        tagged(owner.dimensions, '尺寸标注', 2, 1);
        expect(owner.constructionPoints).toHaveLength(2);
        expect(owner.constructionLines).toHaveLength(2);
        tagged(owner.texts, '引注文字', nested ? 1 : 3, nested ? 0 : 1);
        for (const txt of owner.texts) {
          expect(txt.displayLeader).toBe(txt.text !== 'Dot');
          if (nested) {
            expect(txt.leaderType).toBe(0);
            expect(txt.screenPosition).not.toBeNull();
            expect(txt.screenPosition![0]).toBeCloseTo(0.23, 9);
            expect(txt.screenPosition![1]).toBeCloseTo(0.67, 9);
          } else {
            expect(txt.point).not.toBeNull();
            expect(txt.labelPoint).not.toBeNull();
            for (let axis = 0; axis < 3; axis++) {
              expect(txt.labelPoint![axis] - txt.point![axis]).toBeCloseTo(axis === 2 ? 10 : 0, 9);
            }
          }
        }
      };

      verify(model.root, false);
      let annotated = 0;
      for (const defn of model.definitions.values()) {
        if (defn.texts.length === 0) continue;
        verify(defn, true);
        annotated += 1;
      }
      expect(annotated).toBe(1);
      if (name === 'annotation_layers_vff.skp') {
        expect(model.dimensions).toHaveLength(4);
        for (const dim of model.dimensions) {
          expect(dim.layer).toBe('尺寸标注');
        }
      }
    }
  );

  it('drawing properties come from dimension not its connections', () => {
    const freePoint = (x: number, y: number, z: number) =>
      tlv(
        0x5208,
        concatBytes([
          tlv(0x5209, u32Bytes(1)),
          tlv(0x520a, f64s([x, y, z])),
          tlv(0x07d0, tlv(0x07d2, new Uint8Array([99]))),
        ])
      );

    const body = concatBytes([
      tlv(0x59d8, tlv(0x07d0, concatBytes([tlv(0x07d2, new Uint8Array([12])), tlv(0x07d3, new Uint8Array([7]))]))),
      tlv(0x5bcd, freePoint(0, 0, 0)),
      tlv(0x5bce, freePoint(10, 0, 0)),
      tlv(0x5bd2, f64s([3])),
    ]);
    const dims = parseDimensions(tlv(0x5bcc, body), new Map(), new Map());
    expect(dims).toHaveLength(1);
    expect(dims[0].layer).toBe('12');
    expect(dims[0].hidden).toBe(true);
  });

  it('annotation drawing properties round trip', () => {
    const builder = create();
    const layer = builder.addLayer('Annotations');
    const nested = builder.addComponentDefinition('TextOnly', (def) => {
      def.addText('Screen label', [0, 0, 0], [0, 0, 0], {
        layer,
        hidden: true,
        screenPosition: [0.23, 0.67],
      });
    });
    builder.addInstance(nested);
    builder.addText('Root label', [2, 3, 4], [0, 0, 5], { layer, hidden: true });
    builder.addText('Default label', [1, 1, 1], [0, 0, 2]);
    builder.addDimension([0, 0, 0], [10, 0, 0], [0, 3, 0], { layer, hidden: true });
    builder.addDimension([0, 5, 0], [10, 5, 0], [0, 3, 0]);

    const raw = builder.toBytes();
    const model = parseSkp(raw.buffer.slice(raw.byteOffset, raw.byteOffset + raw.byteLength));
    expect(model.root.dimensions).toHaveLength(2);
    expect(model.root.dimensions[0].layer).toBe('Annotations');
    expect(model.root.dimensions[0].hidden).toBe(true);
    expect(model.root.dimensions[1].layer).toBe('');
    expect(model.root.dimensions[1].hidden).toBe(false);

    expect(model.root.texts).toHaveLength(2);
    expect(model.root.texts[0].layer).toBe('Annotations');
    expect(model.root.texts[0].hidden).toBe(true);
    expect(model.root.texts[0].point).toEqual([2, 3, 4]);
    expect(model.root.texts[0].labelPoint).toEqual([2, 3, 9]);
    expect(model.root.texts[1].layer).toBe('');
    expect(model.root.texts[1].hidden).toBe(false);

    const defn = [...model.definitions.values()].find((d) => d.name === 'TextOnly')!;
    expect(defn.texts).toHaveLength(1);
    expect(defn.texts[0].layer).toBe('Annotations');
    expect(defn.texts[0].hidden).toBe(true);
    expect(defn.texts[0].leaderType).toBe(0);
    expect(defn.texts[0].screenPosition).toEqual([0.23, 0.67]);
  });

  it('leaderless model text retains both points and visibility', () => {
    const builder = create();
    const addAll = (sink: { addText: typeof builder.addText }) => {
      for (const leaderType of [0, 1, 2]) {
        for (const displayLeader of [false, true]) {
          sink.addText('Model label', [5, 6, 7], [2, 3, 4], { leaderType, displayLeader });
        }
      }
      sink.addText('Screen label', [0, 0, 0], [0, 0, 0], { screenPosition: [0.23, 0.67] });
    };

    const nested = builder.addComponentDefinition('Leader visibility', (def) => {
      addAll(def);
    });
    builder.addInstance(nested);
    addAll(builder);

    const raw = builder.toBytes();
    const model = parseSkp(raw.buffer.slice(raw.byteOffset, raw.byteOffset + raw.byteLength));

    const verify = (owner: typeof model.root) => {
      expect(owner.texts).toHaveLength(7);
      for (let i = 0; i < 6; i++) {
        const txt = owner.texts[i];
        expect(txt.point).toEqual([5, 6, 7]);
        expect(txt.labelPoint).toEqual([7, 9, 11]);
        expect(txt.leaderType).not.toBe(0);
        expect(txt.displayLeader).toBe(i === 3 || i === 5);
      }
      expect(owner.texts[6].leaderType).toBe(0);
      expect(owner.texts[6].screenPosition).toEqual([0.23, 0.67]);
    };

    verify(model.root);
    expect(model.definitions.size).toBe(1);
    verify([...model.definitions.values()][0]);
  });

  it('invalid annotation options leave archive usable', () => {
    const builder = create();
    expect(() => builder.addDimension([0, 0, 0], [10, 0, 0], [0, 3, 0], { layer: 999 })).toThrow(SkpWriteError);
    expect(() => builder.addText('Bad layer', [0, 0, 0], [1, 0, 0], { layer: 999 })).toThrow(SkpWriteError);
    expect(() =>
      builder.addText('Bad position', [0, 0, 0], [1, 0, 0], { screenPosition: [-1, 0.5] })
    ).toThrow(SkpWriteError);
    builder.addText('Valid', [0, 0, 0], [1, 0, 0]);

    const raw = builder.toBytes();
    const model = parseSkp(raw.buffer.slice(raw.byteOffset, raw.byteOffset + raw.byteLength));
    expect(model.root.dimensions).toHaveLength(0);
    expect(model.root.texts).toHaveLength(1);
    expect(model.root.texts[0].text).toBe('Valid');
  });
});
