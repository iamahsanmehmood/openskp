import { describe, it, expect } from 'vitest';
import { create, SkpWriteError, Point3 } from '../src/create';
import { parseSkp } from '../src/index';

/**
 * Covers the writer side of openskp#285's "Writer: section planes" item -
 * addSectionPlane/addDimension/addText/addConstructionLine/
 * addConstructionPoint on SkpBuilder, ported byte-for-byte from create.py's
 * own methods of the same name (see that file's docstrings for the real-
 * SketchUp ground truth these record layouts were harvested from).
 *
 * SectionPlane/Text/Dimension round-trip fully since legacy.ts's own
 * readers for them already expose real data on model.root (fixed in a
 * prior PR). ConstructionLine/ConstructionPoint now round-trip fully too -
 * legacy.ts's readers for those two used to parse the geometry and then
 * discard it rather than exposing it on model.root at all, fixed alongside
 * adding this writer (openskp#285's "Writer + reader: construction
 * lines/points").
 */

const SQUARE: Point3[] = [
  [0.0, 0.0, 0.0],
  [10.0, 0.0, 0.0],
  [10.0, 10.0, 0.0],
  [0.0, 10.0, 0.0],
];

function toBuffer(bytes: Uint8Array): ArrayBuffer {
  return bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer;
}

describe('writer: section plane / text / dimension round trip', () => {
  it('round-trips section plane, text, and dimension', () => {
    const builder = create();
    builder.addFace(SQUARE);
    builder.addSectionPlane([10, 20, 30], [0, 0, 1]);
    builder.addText('Hello', [5, 5, 5]);
    builder.addDimension([0, 0, 0], [10, 0, 0]);

    const model = parseSkp(toBuffer(builder.toBytes()));

    expect(model.root.sectionPlanes).toHaveLength(1);
    const sp = model.root.sectionPlanes[0];
    expect(sp.plane[0]).toBeCloseTo(0.0, 6);
    expect(sp.plane[1]).toBeCloseTo(0.0, 6);
    expect(sp.plane[2]).toBeCloseTo(1.0, 6);
    expect(sp.plane[3]).toBeCloseTo(-30.0, 6);
    expect(sp.hidden).toBe(false);

    expect(model.root.texts).toHaveLength(1);
    expect(model.root.texts[0].text).toBe('Hello');
    expect(model.root.texts[0].hidden).toBe(false);

    expect(model.root.dimensions).toHaveLength(1);
  });

  it('normalizes a non-unit normal', () => {
    const builder = create();
    builder.addFace(SQUARE);
    builder.addSectionPlane([0, 0, 5], [0, 0, 2]);

    const model = parseSkp(toBuffer(builder.toBytes()));
    const sp = model.root.sectionPlanes[0];
    expect(sp.plane[2]).toBeCloseTo(1.0, 6);
    expect(sp.plane[3]).toBeCloseTo(-5.0, 6);
  });

  it('shares one embedded font across two dimensions', () => {
    // addDimension/addText only ever embed the CSkFont payload inline on
    // the FIRST call and back-ref it afterwards - exercise that shared-
    // state path without asserting on font bytes directly (the point here
    // is that a second dimension doesn't corrupt the archive's slot
    // numbering).
    const builder = create();
    builder.addFace(SQUARE);
    builder.addDimension([0, 0, 0], [10, 0, 0]);
    builder.addDimension([0, 5, 0], [10, 5, 0]);
    builder.addText('First', [1, 1, 1]);

    const model = parseSkp(toBuffer(builder.toBytes()));
    expect(model.root.dimensions).toHaveLength(2);
    expect(model.root.texts).toHaveLength(1);
  });

  it('construction line and point round trip', () => {
    // legacy.ts's readConstructionLine used to parse point/direction/
    // start/end into locals and then discard all of them (the same shape
    // as the SectionPlane/Text/Dimension bugs fixed in a prior PR, just
    // not yet ported for these two entities) - fixed alongside adding
    // this writer, matching C++'s own reader, which already exposed this
    // correctly.
    const builder = create();
    builder.addFace(SQUARE);
    builder.addConstructionPoint([1, 2, 3]);
    builder.addConstructionLine([0, 0, 0], { point2: [10, 0, 0] });
    builder.addConstructionLine([0, 0, 0], { direction: [0, 0, 1] });
    builder.addSectionPlane([10, 20, 30], [0, 0, 1]); // still readable afterwards

    const model = parseSkp(toBuffer(builder.toBytes()));

    expect(model.root.constructionPoints).toHaveLength(1);
    expect(model.root.constructionPoints[0].position).toEqual([1, 2, 3]);

    expect(model.root.constructionLines).toHaveLength(2);
    const bounded = model.root.constructionLines[0];
    expect(bounded.point).toEqual([0, 0, 0]);
    expect(bounded.direction).toEqual([1, 0, 0]);
    expect(bounded.start).toEqual([0, 0, 0]);
    expect(bounded.end).toEqual([10, 0, 0]);

    const unbounded = model.root.constructionLines[1];
    expect(unbounded.direction).toEqual([0, 0, 1]);
    expect(unbounded.start).toBeNull();
    expect(unbounded.end).toBeNull();

    expect(model.root.sectionPlanes[0].plane[3]).toBeCloseTo(-30.0, 6);
  });

  it('rejects a zero normal', () => {
    const builder = create();
    builder.addFace(SQUARE);
    expect(() => builder.addSectionPlane([0, 0, 0], [0, 0, 0])).toThrow(SkpWriteError);
  });

  it('requires exactly one of point2 or direction', () => {
    const builder = create();
    builder.addFace(SQUARE);
    expect(() => builder.addConstructionLine([0, 0, 0])).toThrow(SkpWriteError);
    expect(() =>
      builder.addConstructionLine([0, 0, 0], { point2: [1, 0, 0], direction: [0, 1, 0] })
    ).toThrow(SkpWriteError);
  });

  it('round-trips construction guides inside a nested definition', () => {
    // Construction line is last in the nested definition on purpose: its
    // nrel=0 tail used to calibrate clineTail to 7 on v17, desyncing the
    // archive walk and corrupting subsequent root-level guides.
    const builder = create();
    const nested = builder.addComponentDefinition('GuideGroup', (def) => {
      def.addFace(SQUARE);
      def.addConstructionPoint([4, 5, 6]);
      def.addConstructionLine([1, 1, 1], { point2: [2, 2, 2] });
    });
    builder.addInstance(nested);
    builder.addConstructionPoint([1, 2, 3]);
    builder.addConstructionLine([0, 0, 0], { point2: [12, 0, 0] });
    builder.addConstructionLine([0, 0, 4], { direction: [0, 0, 1] });

    const model = parseSkp(toBuffer(builder.toBytes()));
    expect(model.root.constructionPoints).toHaveLength(1);
    expect(model.root.constructionPoints[0].position).toEqual([1, 2, 3]);
    expect(model.root.constructionLines).toHaveLength(2);
    expect(model.root.constructionLines[0].end).toEqual([12, 0, 0]);
    expect(model.root.constructionLines[1].start).toBeNull();
    expect(model.root.constructionLines[1].end).toBeNull();
    expect(model.root.constructionLines[1].point).toEqual([0, 0, 4]);

    const group = [...model.definitions.values()].find((d) => d.name === 'GuideGroup')!;
    expect(group).toBeDefined();
    expect(group.constructionPoints).toHaveLength(1);
    expect(group.constructionPoints[0].position).toEqual([4, 5, 6]);
    expect(group.constructionLines).toHaveLength(1);
    expect(group.constructionLines[0].start).toEqual([1, 1, 1]);
    expect(group.constructionLines[0].end).toEqual([2, 2, 2]);
  });

  it('round-trips guide hidden flag and layer', () => {
    const builder = create();
    const guides = builder.addLayer('Guides');
    const nested = builder.addComponentDefinition('NestedGuides', (def) => {
      def.addConstructionPoint([1, 1, 1], { layer: guides, hidden: true });
      def.addConstructionLine([0, 0, 0], { point2: [5, 0, 0], hidden: true });
    });
    builder.addInstance(nested);
    builder.addConstructionPoint([1, 2, 3], { hidden: true });
    builder.addConstructionPoint([4, 5, 6], { layer: guides, hidden: false });
    builder.addConstructionLine([0, 0, 0], { point2: [10, 0, 0], layer: guides, hidden: true });
    builder.addConstructionLine([0, 0, 4], { direction: [0, 0, 1] });

    const model = parseSkp(toBuffer(builder.toBytes()));
    expect(model.root.constructionPoints).toHaveLength(2);
    expect(model.root.constructionPoints[0].hidden).toBe(true);
    expect(model.root.constructionPoints[0].layer).toBe('');
    expect(model.root.constructionPoints[1].hidden).toBe(false);
    expect(model.root.constructionPoints[1].layer).toBe('Guides');

    expect(model.root.constructionLines).toHaveLength(2);
    expect(model.root.constructionLines[0].hidden).toBe(true);
    expect(model.root.constructionLines[0].layer).toBe('Guides');
    expect(model.root.constructionLines[1].hidden).toBe(false);
    expect(model.root.constructionLines[1].layer).toBe('');

    const defn = [...model.definitions.values()].find((d) => d.name === 'NestedGuides')!;
    expect(defn.constructionPoints[0].layer).toBe('Guides');
    expect(defn.constructionPoints[0].hidden).toBe(true);
    expect(defn.constructionLines[0].layer).toBe('');
    expect(defn.constructionLines[0].hidden).toBe(true);
  });

  it('rejects a foreign layer handle on guides', () => {
    const builder = create();
    builder.addFace(SQUARE);
    expect(() => builder.addConstructionPoint([1, 2, 3], { layer: 42 })).toThrow(SkpWriteError);
    expect(() => builder.addConstructionLine([0, 0, 0], { point2: [10, 0, 0], layer: 42 })).toThrow(SkpWriteError);
  });
});
