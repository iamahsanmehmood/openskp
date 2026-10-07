import { describe, it, expect } from "vitest";
import * as fs from "fs";
import * as path from "path";
import { parseSkp } from "../src/index";
import { isLegacy } from "../src/legacy";

/**
 * Regression tests for the pre-2014 legacy layouts found while chasing
 * openskp#284 - ports of the Python reader's fixes (#310 instance GUID gate,
 * #385 CAttributeNamed trailer, #407 SketchUp 3 layout) to this reader.
 *
 * Fixtures are one small synthetic model (4 component definitions
 * InnerFrame / Frame / Panel / Post; InnerFrame placed as a nested group
 * inside Panel; 10 root instances; a custom "Roof" layer; Frame's face
 * painted flat red) built with the OpenSKP writer and saved down through a
 * real SketchUp version-downgrade export - to v3, v6, v7, and left at 2014.
 * SketchUp's default template rides along as extra materials/layers. No
 * private content.
 *
 * Each of these used to either throw or - worse - "succeed" with most of the
 * model silently missing, so the tests assert parsed structure, not just the
 * absence of an exception.
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
const defsByName = (m: ReturnType<typeof parse>) =>
  new Map([...m.definitions.values()].map((d) => [d.name, d]));
const instanceNames = (m: ReturnType<typeof parse>) =>
  m.root.instances.map((i) => i.name);

// Entity collections are Maps or arrays depending on the kind.
const count = (c: any): number =>
  c instanceof Map
    ? c.size
    : Array.isArray(c)
      ? c.length
      : Object.keys(c).length;
const counts = (d: any) => [count(d.faces), count(d.edges), count(d.vertices)];

const SIBLING_NAMES = [
  "",
  "P-1",
  "P-2",
  "P-3",
  "P-4",
  "P-5",
  "P-6",
  "P-7",
  "P-8",
  "P-9",
];

describe("pre-2014 instance GUID gate (version >= 14, not class schema)", () => {
  it("detects the fixtures as legacy containers", () => {
    expect(isLegacy(load("legacy_v7_synthetic.skp").data)).toBe(true);
    expect(isLegacy(load("legacy_v2014_synthetic.skp").data)).toBe(true);
  });

  it("v7 parses and places all 10 root instances (was: class-ref to non-class slot / truncated to 1)", () => {
    const model = parse("legacy_v7_synthetic.skp");
    expect(model.version).toBe("{7.0.1}");
    expect(instanceNames(model)).toEqual(SIBLING_NAMES);
  });

  it("v7 and 2014 parse to the same structure", () => {
    const v7 = parse("legacy_v7_synthetic.skp");
    const v14 = parse("legacy_v2014_synthetic.skp");
    expect(v7.definitions.size).toBe(3);
    expect(v14.definitions.size).toBe(3);
    expect(instanceNames(v7)).toEqual(instanceNames(v14));
    const d7 = defsByName(v7);
    const d14 = defsByName(v14);
    expect([...d7.keys()].sort()).toEqual(["Frame", "Panel", "Post"]);
    expect([...d14.keys()].sort()).toEqual(["Frame", "Panel", "Post"]);
    for (const [name, def] of d7) {
      const other = d14.get(name)!;
      expect(def.faces.size).toBe(other.faces.size);
      expect(def.instances.map((i) => i.name)).toEqual(
        other.instances.map((i) => i.name),
      );
    }
  });

  it("keeps the nested group inside Panel", () => {
    const panel = defsByName(parse("legacy_v7_synthetic.skp")).get("Panel")!;
    expect(panel.instances.map((i) => i.name)).toEqual(["InnerFrame"]);
  });

  it("2014 files still read their trailing instance GUIDs", () => {
    const model = parse("legacy_v2014_synthetic.skp");
    expect(model.version).toBe("{14.0.1}");
    expect(model.root.instances.length).toBe(10);
  });
});

describe("SketchUp 6 CAttributeNamed has no trailing field", () => {
  it("v6 parses (was: back-ref to unwalked slot 256)", () => {
    const model = parse("legacy_v6_synthetic.skp");
    expect(model.version).toBe("{6.0.1}");
    expect(instanceNames(model)).toEqual(SIBLING_NAMES);
    expect([...defsByName(model).keys()].sort()).toEqual([
      "Frame",
      "InnerFrame",
      "Panel",
      "Post",
    ]);
    expect(
      defsByName(model)
        .get("Panel")!
        .instances.map((i) => i.name),
    ).toEqual(["InnerFrame"]);
  });
});

describe("SketchUp 3 layout", () => {
  it("detects the fixture as legacy", () => {
    expect(isLegacy(load("legacy_v3_synthetic.skp").data)).toBe(true);
  });

  it("parses every definition with its geometry (was: expected a string record)", () => {
    const model = parse("legacy_v3_synthetic.skp");
    expect(model.version).toBe("{3.0.1}");
    const defs = defsByName(model);
    expect([...defs.keys()].sort()).toEqual([
      "Frame",
      "InnerFrame",
      "Panel",
      "Post",
    ]);
    for (const d of defs.values()) {
      expect(counts(d)).toEqual([1, 4, 4]);
    }
    const frame = defs.get("Frame")!;
    const xs = [...new Set([...frame.vertices.values()].map((v) => v.x))].sort(
      (a, b) => a - b,
    );
    const ys = [...new Set([...frame.vertices.values()].map((v) => v.y))].sort(
      (a, b) => a - b,
    );
    expect([xs, ys]).toEqual([
      [0, 20],
      [0, 20],
    ]);
  });

  it("places every root instance with its translation", () => {
    const model = parse("legacy_v3_synthetic.skp");
    expect(model.root.instances.length).toBe(10);
    const expected = [
      "Frame",
      "Panel",
      "Post",
      "Frame",
      "Panel",
      "Post",
      "Frame",
      "Panel",
      "Post",
      "Frame",
    ];
    model.root.instances.forEach((inst, i) => {
      expect(model.definitions.get(inst.refIdx)!.name).toBe(expected[i]);
      expect(inst.matrix[9]).toBe(i * 40);
    });
  });

  it("places the nested group inside Panel", () => {
    const model = parse("legacy_v3_synthetic.skp");
    const panel = defsByName(model).get("Panel")!;
    expect(
      panel.instances.map((i) => model.definitions.get(i.refIdx)!.name),
    ).toEqual(["InnerFrame"]);
  });

  it("takes a face material from its own leading pointer and reports nothing hidden", () => {
    const model = parse("legacy_v3_synthetic.skp");
    const byId = new Map(model.materials.map((m) => [m.id, m]));
    const defs = defsByName(model);
    const painted = [...defs.get("Frame")!.faces.values()][0];
    expect(byId.get(painted.materialId!)!.name).toBe("Red");
    const c = byId.get(painted.materialId!)!.color;
    expect([c.r, c.g, c.b]).toEqual([255, 0, 0]);
    for (const name of ["Panel", "Post", "InnerFrame"]) {
      expect(
        [...defs.get(name)!.faces.values()][0].materialId ?? null,
      ).toBeNull();
    }
    for (const d of model.definitions.values()) {
      for (const f of d.faces.values()) expect(f.hidden).toBe(false);
    }
  });

  it("keeps the custom layer", () => {
    const names = parse("legacy_v3_synthetic.skp").layers.map((l) => l.name);
    expect(names).toContain("Layer0");
    expect(names).toContain("Roof");
  });

  it("reads the embedded template textures as inline JPEGs", () => {
    const model = parse("legacy_v3_synthetic.skp");
    expect(model.materials.length).toBe(35);
    const textured = model.materials.filter((m) => m.texture);
    expect(textured.length).toBe(10);
    for (const m of textured) {
      const data = m.texture!.data;
      expect(Array.from(data.slice(0, 3))).toEqual([0xff, 0xd8, 0xff]);
      expect(Array.from(data.slice(-2))).toEqual([0xff, 0xd9]);
    }
  });

  it("matches the v6 file built from the same source", () => {
    const v3 = parse("legacy_v3_synthetic.skp");
    const v6 = parse("legacy_v6_synthetic.skp");
    const d3 = defsByName(v3);
    const d6 = defsByName(v6);
    expect([...d3.keys()].sort()).toEqual([...d6.keys()].sort());
    for (const [name, a] of d3) {
      const b = d6.get(name)!;
      expect(counts(a)).toEqual(counts(b));
    }
    v3.root.instances.forEach((a, i) => {
      const b = v6.root.instances[i];
      expect(v3.definitions.get(a.refIdx)!.name).toBe(
        v6.definitions.get(b.refIdx)!.name,
      );
      a.matrix.forEach((x, k) =>
        expect(Math.abs(x - b.matrix[k])).toBeLessThan(1e-9),
      );
    });
  });
});
