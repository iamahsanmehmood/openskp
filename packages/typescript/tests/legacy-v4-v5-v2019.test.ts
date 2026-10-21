import { describe, it, expect } from "vitest";
import * as fs from "fs";
import * as path from "path";
import { parseSkp } from "../src/index";
import { isLegacy, Archive, READERS, R, readDimLinear } from "../src/legacy";

/**
 * Regression tests for SketchUp 4, 5 and 2019 files - port of the Python
 * reader's fixes (openskp#284, #440) to this reader.
 *
 * Fixtures are small synthetic models, saved down through the installed
 * SketchUp library from the same sources as the existing legacy fixtures
 * (legacy_v6_synthetic.skp and the one-face textured model behind
 * legacy_v3_textured.skp); no private content:
 *
 * - legacy_v4/v5/v2019_synthetic.skp: 4 definitions (InnerFrame, Frame, Panel,
 *   Post), 10 root instances, a "Roof" layer, 24 materials.
 * - legacy_v4/v5_textured.skp: one painted face with an 8x8 PNG, stored as a
 *   bare inline image before SketchUp 6 (not a CDib object).
 * - legacy_v2020_textured.skp: the same model at SketchUp 2020 - a model with
 *   NO component definitions, whose root entities used to be read as
 *   "definitions" and silently dropped.
 *
 * Parsing without an exception is not enough (an early V3 attempt "succeeded"
 * with one definition of four), so these assert parsed structure and cross-
 * check it against the V6 file built from the same source.
 */
const FIXTURES = path.join(__dirname, "fixtures");

function load(name: string) {
  const buf = fs.readFileSync(path.join(FIXTURES, name));
  const arrayBuffer = buf.buffer.slice(
    buf.byteOffset,
    buf.byteOffset + buf.byteLength,
  ) as ArrayBuffer;
  return { data: new Uint8Array(arrayBuffer), arrayBuffer };
}

const parse = (name: string) => parseSkp(load(name).arrayBuffer);
type Model = ReturnType<typeof parse>;

const count = (c: any): number =>
  c instanceof Map ? c.size : Array.isArray(c) ? c.length : Object.keys(c).length;
const round = (xs: number[], n = 5) => xs.map((x) => Number(x.toFixed(n)));

/** Everything that matters about a model, keyed by names and coordinates
 * (ids differ between versions). */
function canonical(m: Model, instanceNames: boolean) {
  const materialName = new Map([...m.materials].map((x: any) => [x.id, x.name]));
  const defName = (idx: number) => m.definitions.get(idx)!.name;
  const defs: Record<string, unknown> = {};
  for (const d of m.definitions.values()) {
    const faces = [...(d.faces as any).values()]
      .map((f: any) => [materialName.get(f.materialId) ?? null, f.backMaterialId ?? null, !!f.hidden])
      .sort();
    const instances = d.instances
      .map((i: any) => [defName(i.refIdx), round(i.matrix), instanceNames ? i.name : ""])
      .sort();
    defs[d.name] = {
      vertices: [...(d.vertices as any).values()].map((v: any) => round([v.x, v.y, v.z], 6)).sort(),
      edges: count(d.edges),
      faces,
      instances,
    };
  }
  const root = m.root.instances
    .map((i: any) => [defName(i.refIdx), round(i.matrix), instanceNames ? i.name : ""])
    .sort();
  return {
    layers: [...m.layers].map((l: any) => l.name).sort(),
    materials: [...m.materials].map((x: any) => x.name).sort(),
    defs,
    root,
  };
}

const SYNTHETIC: Array<[string, string, boolean]> = [
  ["legacy_v4_synthetic.skp", "{4.0.1}", false], // SketchUp 4 stores no instance names
  ["legacy_v5_synthetic.skp", "{5.0.1}", true],
  ["legacy_v2019_synthetic.skp", "{19.0.1}", true],
];

describe("SketchUp 4, 5 and 2019 synthetic model", () => {
  for (const [file, version, names] of SYNTHETIC) {
    describe(file, () => {
      it("is detected as legacy and reports its version", () => {
        expect(isLegacy(load(file).data)).toBe(true);
        expect(parse(file).version).toBe(version);
      });

      it("matches the V6 file from the same source", () => {
        expect(canonical(parse(file), names)).toEqual(canonical(parse("legacy_v6_synthetic.skp"), names));
      });

      it("is complete, not just error-free", () => {
        const m = parse(file);
        const defs = new Map([...m.definitions.values()].map((d) => [d.name, d]));
        expect([...defs.keys()].sort()).toEqual(["Frame", "InnerFrame", "Panel", "Post"]);
        for (const d of defs.values()) {
          expect([count(d.faces), count(d.edges), count(d.vertices)]).toEqual([1, 4, 4]);
        }
        expect(m.root.instances.length).toBe(10);
        expect(m.materials.length).toBe(24);
        const layers = new Set([...m.layers].map((l: any) => l.name));
        expect(layers.has("Layer0") && layers.has("Roof")).toBe(true);
        expect(defs.get("Panel")!.instances.map((i: any) => m.definitions.get(i.refIdx)!.name)).toEqual([
          "InnerFrame",
        ]);
      });
    });
  }

  it("SketchUp 4 has no instance names and SketchUp 5 does", () => {
    const v6 = parse("legacy_v6_synthetic.skp");
    expect(v6.root.instances.some((i: any) => i.name)).toBe(true);
    expect(parse("legacy_v4_synthetic.skp").root.instances.every((i: any) => i.name === "")).toBe(true);
    expect(parse("legacy_v5_synthetic.skp").root.instances.map((i: any) => i.name)).toEqual(
      v6.root.instances.map((i: any) => i.name),
    );
  });
});

describe("textured models", () => {
  const textureOf = (m: Model) => (m.materials as any[]).find((x) => x.texture && x.texture.data)?.texture;

  it("SketchUp 4 and 5 read the bare inline texture (was: texture object is not a dib)", () => {
    const reference = textureOf(parse("legacy_v3_textured.skp"));
    for (const file of ["legacy_v4_textured.skp", "legacy_v5_textured.skp"]) {
      const m = parse(file);
      const tex = textureOf(m);
      expect(tex).toBeTruthy();
      expect(Buffer.from(tex.data).equals(Buffer.from(reference.data))).toBe(true); // same PNG, byte for byte
      expect(Buffer.from(tex.data).subarray(0, 4).toString("hex")).toBe("89504e47");
      expect(count(m.root.faces)).toBe(1);
    }
  });

  it("a SketchUp 2020 model without definitions keeps its root entities", () => {
    // The root entity count was handed over as a "definition count", so the
    // face and its four edges were consumed as definitions and the model came
    // out empty - with no error.
    const m = parse("legacy_v2020_textured.skp");
    expect([count(m.root.faces), count(m.root.edges), count(m.root.vertices)]).toEqual([1, 4, 4]);
    expect(textureOf(m)).toBeTruthy();
  });
});

// One real CDimensionLinear record per version, taken from the repository's own
// Untitled.skp re-saved at SketchUp 4, 5 and 6 (record bytes only - from the
// entity preamble to the end of the trailing block). All three describe the same
// dimension, so each must be consumed exactly and decode to the same references.
const DIM_RECORDS: Record<number, string> = {
  4:
    "00000f0000010100001d00fffeff00010000000400000000000000000000000000000000" +
    "000000000000000000000000000000000000000000000001000000040000000000000000" +
    "0000007e210ac0556f564000000000000000000000000000000000000000000000000000" +
    "00f0bf000000000000000000000000000000000000000000000080000000000000f0bf00" +
    "0000000000008001000000b95c2e97cbe512c00000000000000000",
  5:
    "00000f000001010000001d00fffeff000100000004000000000000000000000000000000" +
    "000000000000000000000000000000000000000000000000010000000400000000000000" +
    "000000007e210ac0556f5640000000000000000000000000000000000000000000000000" +
    "0000f0bf000000000000000000000000000000000000000000000080000000000000f0bf" +
    "000000000000008001000000b95c2e97cbe512c00000000000000000",
  6:
    "00000f000001010000001d00fffeff00ffff0100070043536b466f6e740000fffeff0654" +
    "00610068006f006d00610000000c00000000000000000000244000030000000100000004" +
    "000000000000000000000000000000000000000000000000000000000000000000000000" +
    "000000010000000400000000000000000000007e210ac0556f5640000000000000000000" +
    "0000000000000000000000000000000000f0bf0000000000000000000000000000000000" +
    "00000000000080000000000000f0bf000000000000008001000000b95c2e97cbe512c000" +
    "0000000000000003000000",
};

function readDimension(ver: number) {
  const record = Uint8Array.from(Buffer.from(DIM_RECORDS[ver], "hex"));
  const tail = Uint8Array.of(0xff, 0x7f, 0x00, 0x00, 0x00, 0x80); // the next entity's big class-ref tag
  const data = new Uint8Array(record.length + tail.length);
  data.set(record, 0);
  data.set(tail, record.length);
  const ar = new Archive(data, ver);
  Object.assign(ar.readers, READERS);
  ar.nextSlot = ar.walkBase = 100;
  const r = new R(data, 0);
  const out = readDimLinear(ar, r);
  return { out, consumed: r.pos, size: record.length };
}

describe("dimension record layout", () => {
  for (const ver of [4, 5, 6]) {
    it(`SketchUp ${ver}: the record is consumed exactly`, () => {
      const { consumed, size } = readDimension(ver);
      expect(consumed).toBe(size);
    });
  }

  it("SketchUp 4 and 5 decode the same references as SketchUp 6", () => {
    const want = readDimension(6).out;
    for (const ver of [4, 5]) {
      const got = readDimension(ver).out;
      expect(got.connect).toEqual(want.connect);
      expect(got.paths).toEqual(want.paths);
      expect(got.text).toBe(want.text);
    }
  });
});

