import fs from 'node:fs';
import path from 'node:path';
import { describe, it, expect } from 'vitest';
import { parseSkp } from '../src/index';
import { Archive, R, READERS, readEntityList } from '../src/legacy';

/**
 * Legacy (MFC) text labels anchored to an entity - port of the Python fix.
 *
 * 16 bytes after its font, a CText carries the connection block dimensions
 * use, [u32 type][u32 4][point3d]: type 0 or 1 when the label is anchored to
 * nothing (1 for a leader pointing at a free point stored inline), other
 * types (2, 4, 5 and 7 seen) for a label anchored to an entity. As in a
 * dimension, the block is followed by the anchored entity's ref and its
 * instance paths, null and empty for types 0 and 1. MFC writes an object in
 * full the first time it is referenced, so a path can hold a whole NEW
 * instance: the component a label is anchored to, when the label comes first
 * in its entity list. The reader skipped the block as bytes, so that instance
 * never got its store-map slots and every later reference was off by them
 * ("class-ref to non-class slot"): the auditorium (CR4) models of the BRAS
 * database, whose loudspeaker labels are anchored to the loudspeakers, did
 * not open, and two of its coupled-room (CR1) models lost root entities (one
 * read 5 of its 318). The entity list's declared count holds such an
 * instance, but SketchUp does not always list it again with a back-reference.
 */

const TEXT = 1, FONT_CLASS = 2, FONT = 3, THING = 4; // pre-registered slots
const FIRST_FREE = 5;

const u16 = (n: number): number[] => [n & 0xff, (n >> 8) & 0xff];
const u32 = (n: number): number[] => [n & 0xff, (n >> 8) & 0xff, (n >> 16) & 0xff, (n >>> 24) & 0xff];
const zeros = (n: number): number[] => new Array(n).fill(0);
function f64s(...xs: number[]): number[] {
  const b = new Uint8Array(8 * xs.length);
  const view = new DataView(b.buffer);
  xs.forEach((x, i) => view.setFloat64(8 * i, x, true));
  return Array.from(b);
}
function utf16(text: string): number[] {
  const out = [0xff, 0xfe, 0xff, text.length];
  for (const ch of text) out.push(...u16(ch.charCodeAt(0)));
  return out;
}

/** A SketchUp 2015 archive that knows CText, a font and a stand-in class for
 * the anchored instance, whose objects are 3 bytes long. */
function archive(data: number[]): Archive {
  const ar = new Archive(new Uint8Array(data), 15);
  Object.assign(ar.readers, READERS);
  ar.readers['CThing'] = (_ar, r) => ({ k: 'thing', b: Array.from(r.raw(3)) });
  ar.slots.set(TEXT, ['class', 'CText', 0]);
  ar.slots.set(FONT_CLASS, ['class', 'CSkFont', 0]);
  ar.slots.set(FONT, ['obj', 'CSkFont', {}]);
  ar.slots.set(THING, ['class', 'CThing', 0]);
  ar.classSlot.set('CText', TEXT);
  ar.classSlot.set('CSkFont', FONT_CLASS);
  ar.classSlot.set('CThing', THING);
  ar.nextSlot = FIRST_FREE;
  return ar;
}

/** A CText's bytes after its class tag. `refs` is what follows the
 * connection block: the anchored entity's ref and its instance paths. */
function textRecord(kind: number, point = [0, 0, 0], refs: number[] = [], label = [0, 0, 0]): number[] {
  return [
    ...zeros(2), // no attribute container
    ...zeros(8), ...zeros(2), // draw block, layer 0
    ...u16(FONT), // the font, a back-reference
    ...zeros(16),
    ...u32(kind), ...u32(4), ...f64s(...point),
    ...(refs.length ? refs : zeros(12)),
    ...f64s(...label),
    ...f64s(-0.577, -0.577, -0.577),
    ...u32(2),
    0x01, 0x00, 0x00, 0x00, 0x01, 0x00, 0x03, 0x00, 0x00, 0x00, 0x01, // arrow 3
    ...utf16('LS1'),
    ...zeros(5),
  ];
}

const newThing = (): number[] => [...u16(0x8000 | THING), 0x4f, 0x42, 0x4a]; // "OBJ"
/** Anchored to entity 9 inside an instance written here in full. */
const pathsWithNewInstance = (): number[] => [...u16(9), 0, 0, ...u32(1), ...newThing(), ...u32(0)];
/** No relationships, then a GUID and the definition's name. */
const definitionTail = (): number[] => [...zeros(4), ...zeros(2), ...zeros(16), ...utf16('LS')];
const TAIL = [0x54, 0x41, 0x49, 0x4c]; // "TAIL"

describe('a legacy text label anchored to an entity', () => {
  it.each([
    [2, [0, 0, 0]],
    [4, [0, 0, 0]],
    [5, [12.5, -3.0, 40.0]],
    [7, [1.0, 2.0, 3.0]],
  ])('type %i reads the instance written in its path', (kind, point) => {
    const data = [...textRecord(kind, point, pathsWithNewInstance()), ...TAIL];
    const ar = archive(data);
    const r = new R(new Uint8Array(data), 0);
    const out = READERS['CText'](ar, r);
    expect(out.text).toBe('LS1');
    expect(out.connect).toBe(9);
    expect(out.paths).toEqual([null, [FIRST_FREE], []]);
    expect(ar.slots.get(FIRST_FREE)).toEqual(['obj', 'CThing', { k: 'thing', b: [0x4f, 0x42, 0x4a] }]);
    expect(data.slice(r.pos)).toEqual(TAIL);
  });

  it('a free label reads as before', () => {
    const data = [...textRecord(1, [1, 2, 3], [], [4, 5, 6]), ...TAIL];
    const r = new R(new Uint8Array(data), 0);
    const out = READERS['CText'](archive(data), r);
    expect(out.text).toBe('LS1');
    expect(out.point).toEqual([1, 2, 3]);
    expect(out.labelPoint).toEqual([4, 5, 6]);
    expect(out.paths).toBeNull();
    expect(data.slice(r.pos)).toEqual(TAIL);
  });

  it('a vertex-anchored label resolves only without extra or instance paths', () => {
    const directRefs = [...u16(9), 0, 0, ...zeros(8)];
    const direct = [...textRecord(2, [0, 0, 0], directRefs), ...TAIL];
    const r1 = new R(new Uint8Array(direct), 0);
    const out1 = READERS['CText'](archive(direct), r1);
    expect(out1.vertex).toBe(9);
    expect(direct.slice(r1.pos)).toEqual(TAIL);

    const extraRefs = [...u16(9), ...u16(8), ...zeros(8)];
    const withExtra = [...textRecord(2, [0, 0, 0], extraRefs), ...TAIL];
    const r2 = new R(new Uint8Array(withExtra), 0);
    const out2 = READERS['CText'](archive(withExtra), r2);
    expect(out2.paths).toEqual([8, [], []]);
    expect(out2.vertex).toBeNull();
    expect(withExtra.slice(r2.pos)).toEqual(TAIL);
  });

  it('text delimiter and unanchored vertex scan respect record bounds', () => {
    const inlineFont = [...u16(0x8000 | FONT_CLASS), 0x01, 0x00, 0x00, 0x00, 0x01, 0x00, 0x03, 0x00, 0x00, 0x00, 0x01];
    const earlyMarker = [0xff, 0xfe, 0xff, 0x00, ...zeros(12)];
    const rec1 = [
      ...zeros(2), ...zeros(8), ...zeros(2), ...inlineFont, ...earlyMarker,
      ...u32(1), ...u32(4), ...f64s(1, 2, 3), ...zeros(12),
      ...f64s(4, 5, 6), ...f64s(-0.577, -0.577, -0.577), ...u32(2),
      0x01, 0x00, 0x00, 0x00, 0x01, 0x00, 0x03, 0x00, 0x00, 0x00, 0x01,
      ...utf16('LS1'), ...zeros(5), ...TAIL,
    ];
    const ar1 = archive(rec1);
    ar1.readers['CSkFont'] = (_ar, rr) => rr.raw(11);
    const r1 = new R(new Uint8Array(rec1), 0);
    const out1 = READERS['CText'](ar1, r1);
    expect(out1.text).toBe('LS1');
    expect(out1.point).toEqual([1, 2, 3]);
    expect(rec1.slice(r1.pos)).toEqual(TAIL);

    const craftedLabelXBytes = [0x09, 0x00, 0x00, 0x00, 0x00, 0x00, 0x10, 0x40];
    const craftedLabelX = new DataView(new Uint8Array(craftedLabelXBytes).buffer).getFloat64(0, true);
    const rec2 = [
      ...zeros(2), ...zeros(8), ...zeros(2), ...u16(FONT),
      ...zeros(6), ...u32(2), ...u32(4), ...f64s(0, 0, 0),
      ...craftedLabelXBytes, ...f64s(5, 6),
      ...f64s(-0.577, -0.577, -0.577), ...u32(2),
      0x01, 0x00, 0x00, 0x00, 0x01, 0x00, 0x03, 0x00, 0x00, 0x00, 0x01,
      ...utf16('LS1'), ...zeros(5), ...TAIL,
    ];
    const r2 = new R(new Uint8Array(rec2), 0);
    const out2 = READERS['CText'](archive(rec2), r2);
    expect(out2.vertex).toBeNull();
    expect(out2.labelPoint).toEqual([craftedLabelX, 5, 6]);
    expect(rec2.slice(r2.pos)).toEqual(TAIL);
  });
});

describe('an entity list holding an anchored label', () => {
  it('keeps an instance written in a label and never listed again', () => {
    const data = [...u16(0x8000 | TEXT), ...textRecord(2, [0, 0, 0], pathsWithNewInstance()), ...definitionTail()];
    const ar = archive(data);
    const r = new R(new Uint8Array(data), 0);
    const ents = readEntityList(ar, r, 2, 'def');
    expect(ents.map(([slot, name]) => [slot, name])).toEqual([[FIRST_FREE, 'CText'], [FIRST_FREE + 1, 'CThing']]);
    expect(data.slice(r.pos)).toEqual(definitionTail());
  });

  it('lists an instance written in a label and listed again once', () => {
    const data = [
      ...u16(0x8000 | TEXT), ...textRecord(2, [0, 0, 0], pathsWithNewInstance()),
      ...u16(FIRST_FREE + 1), // its back-reference
      ...definitionTail(),
    ];
    const ar = archive(data);
    const r = new R(new Uint8Array(data), 0);
    const ents = readEntityList(ar, r, 2, 'def');
    expect(ents.map(([slot, name]) => [slot, name])).toEqual([[FIRST_FREE, 'CText'], [FIRST_FREE + 1, 'CThing']]);
    expect(data.slice(r.pos)).toEqual(definitionTail());
  });

  it("only the outermost instance of a path is one of the list's items", () => {
    // A path runs from the outermost instance in; the inner ones live in
    // other definitions.
    const refs = [...u16(9), 0, 0, ...u32(2), ...newThing(), ...newThing(), ...u32(0)];
    const data = [...u16(0x8000 | TEXT), ...textRecord(2, [0, 0, 0], refs), ...definitionTail()];
    const ar = archive(data);
    const r = new R(new Uint8Array(data), 0);
    const ents = readEntityList(ar, r, 2, 'def');
    expect(ents.map(([slot, name]) => [slot, name])).toEqual([[FIRST_FREE, 'CText'], [FIRST_FREE + 1, 'CThing']]);
    expect(ar.slots.get(FIRST_FREE + 2)?.[1]).toBe('CThing'); // read, not listed
    expect(data.slice(r.pos)).toEqual(definitionTail());
  });
});

describe('classic leader and screen text fixtures (#559)', () => {
  it('matches SketchUp SDK anchors, label vectors, and screen positions', () => {
    const anchorsRaw = new Uint8Array(
      fs.readFileSync(path.resolve(__dirname, 'fixtures', 'classic_text_anchors_2017.skp'))
    );
    const model = parseSkp(anchorsRaw);
    expect(model.root.texts.map((t) => t.text)).toEqual([
      'Free callout',
      'Vertex callout',
      'View callout',
      'Screen note',
    ]);

    const [free, vertex, view, screen] = model.root.texts;
    expect(free).toMatchObject({
      text: 'Free callout',
      hidden: false,
      layer: '',
      point: [1, 2, 3],
      labelPoint: [5, 7, 9],
      screenPosition: [0, 0],
      leaderType: 2,
      arrowType: 3,
      displayLeader: true,
    });
    expect(vertex).toMatchObject({
      text: 'Vertex callout',
      hidden: false,
      layer: '',
      point: [20, 30, 0],
      labelPoint: [10, 12, 14],
      screenPosition: [0.5, 0.5],
      leaderType: 2,
      arrowType: 4,
      displayLeader: true,
    });
    expect(view).toMatchObject({
      text: 'View callout',
      hidden: false,
      layer: '',
      point: [3, 4, 5],
      labelPoint: [6, 7, 8],
      screenPosition: [0.5, 0.5],
      leaderType: 1,
      arrowType: 1,
      displayLeader: true,
    });
    expect(screen.point).toEqual([0, 0, 0]);
    expect(screen.labelPoint).toEqual([0, 0, 0]);
    expect(screen.screenPosition?.[0]).toBeCloseTo(0.35, 9);
    expect(screen.screenPosition?.[1]).toBeCloseTo(0.65, 9);
    expect(screen.leaderType).toBe(0);
    expect(screen.arrowType).toBe(0);
    expect(screen.displayLeader).toBe(true);
    expect(screen.hidden).toBe(false);
    expect(screen.layer).toBe('');

    const v20Raw = new Uint8Array(
      fs.readFileSync(path.resolve(__dirname, 'fixtures', 'sdk_saved_leaderless_text_edges_2020.skp'))
    );
    const v20 = parseSkp(v20Raw);
    expect(v20.root.texts).toEqual([
      {
        text: 'Screen title',
        hidden: false,
        layer: '',
        point: [0, 0, 0],
        labelPoint: [0, 0, 0],
        screenPosition: [0, 0],
        leaderType: 0,
        arrowType: 3,
        displayLeader: false,
      },
      {
        text: 'Callout',
        hidden: false,
        layer: '',
        point: [5, 5, 0],
        labelPoint: [7, 7, 4],
        screenPosition: [0, 0],
        leaderType: 2,
        arrowType: 3,
        displayLeader: true,
      },
    ]);
  });

  it('resolves face-anchored texts (conn_kind == 7 / 0x5209 == 7) to SUTextGetPoint', () => {
    const expectCloseVec3 = (actual: [number, number, number] | null, expected: [number, number, number]) => {
      expect(actual).not.toBeNull();
      expect(actual![0]).toBeCloseTo(expected[0], 11);
      expect(actual![1]).toBeCloseTo(expected[1], 11);
      expect(actual![2]).toBeCloseTo(expected[2], 11);
    };

    for (const fixtureName of [
      'classic_face_text_anchors_2017.skp',
      'vff_face_text_anchors_2025.skp',
    ]) {
      const raw = new Uint8Array(fs.readFileSync(path.resolve(__dirname, 'fixtures', fixtureName)));
      const model = parseSkp(raw);
      expect(model.root.texts.map((t) => t.text)).toEqual([
        'Root untextured',
        'Root textured',
        'On component',
        'On group',
      ]);
      const [rootUntex, rootTex, onComp, onGrp] = model.root.texts;
      expectCloseVec3(rootUntex.point, [9, 10, 2]);
      expectCloseVec3(rootUntex.labelPoint, [3, 4, 5]);
      expectCloseVec3(rootTex.point, [64.5, 3.6, 0]);
      expectCloseVec3(rootTex.labelPoint, [4, 5, 6]);
      expectCloseVec3(onComp.point, [90, 56, 12]);
      expectCloseVec3(onComp.labelPoint, [5, 6, 7]);
      expectCloseVec3(onGrp.point, [56, 32, 11]);
      expectCloseVec3(onGrp.labelPoint, [6, 7, 8]);

      const ownDefs = [...model.definitions.values()].filter((d) => d.name === 'OwnTextPanel');
      expect(ownDefs).toHaveLength(1);
      expect(ownDefs[0].texts.map((t) => t.text)).toEqual(['In component']);
      expectCloseVec3(ownDefs[0].texts[0].point, [3, 5, 1]);
      expectCloseVec3(ownDefs[0].texts[0].labelPoint, [2, 3, 4]);
    }

    const capillaRaw = new Uint8Array(
      fs.readFileSync(path.resolve(__dirname, 'fixtures', 'capilla_quiroz_v17.skp'))
    );
    const capilla = parseSkp(capillaRaw);
    const areaTexts = capilla.root.texts.filter((t) => t.text === '4.06 m²');
    expect(areaTexts).toHaveLength(1);
    expectCloseVec3(areaTexts[0].point, [
      19.346940925615975,
      154.92910008345638,
      112.92862002631385,
    ]);
  });
});
