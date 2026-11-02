import { describe, it, expect } from 'vitest';
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
    expect(out.paths).toBeNull();
    expect(data.slice(r.pos)).toEqual(TAIL);
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
