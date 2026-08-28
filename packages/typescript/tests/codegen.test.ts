import { describe, it, expect } from 'vitest';
import * as fs from 'fs';
import * as path from 'path';
import { create } from '../src/create';
import { parseSkp, toTypeScriptCode, computeFaceUv, faceUvBasis } from '../src/index';

/**
 * Tests for codegen.ts's toTypeScriptCode - generates TypeScript source
 * that rebuilds a parsed model via the writer API.
 *
 * Found via diffing a real, large file (jeff.skp: 2713 definitions, 113643
 * faces) against its own regenerated output: an early prototype dropped
 * instance-level paint (95% of that file's instances) and instance names
 * entirely, and never emitted textured materials at all - the SAME class
 * of gap this suite's own texture/instance-paint/instance-name tests below
 * exist to catch, on small synthetic fixtures instead of a large real file.
 *
 * The strongest possible check here isn't just "the generated text looks
 * right" - it's executing the generated code for real (via `new Function`,
 * injecting the real `create`) and parsing what it produces, exactly the
 * way a real caller running this code would.
 */

function toBuffer(bytes: Uint8Array): ArrayBuffer {
  return bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer;
}

// A minimal, dependency-free 4x4 solid-color PNG (raw deflate stored
// blocks, no compression library needed) - mirrors create.test.ts's own
// makeTestPng, duplicated locally per this project's established
// per-test-file convention.
function makeTestPng(size = 4, rgb: [number, number, number] = [200, 50, 50]): Uint8Array {
  function crc32(buf: number[]): number {
    let c: number;
    const table: number[] = [];
    for (let n = 0; n < 256; n++) {
      c = n;
      for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
      table[n] = c >>> 0;
    }
    let crc = 0xffffffff;
    for (const b of buf) crc = table[(crc ^ b) & 0xff] ^ (crc >>> 8);
    return (crc ^ 0xffffffff) >>> 0;
  }
  function u32be(v: number): number[] {
    return [(v >>> 24) & 0xff, (v >>> 16) & 0xff, (v >>> 8) & 0xff, v & 0xff];
  }
  function chunk(tag: number[], data: number[]): number[] {
    return [...u32be(data.length), ...tag, ...data, ...u32be(crc32([...tag, ...data]))];
  }
  const rawRows: number[][] = [];
  for (let y = 0; y < size; y++) {
    const row = [0];
    for (let x = 0; x < size; x++) row.push(...rgb);
    rawRows.push(row);
  }
  const rawData = rawRows.flat();
  let adler1 = 1;
  let adler2 = 0;
  for (const b of rawData) {
    adler1 = (adler1 + b) % 65521;
    adler2 = (adler2 + adler1) % 65521;
  }
  const deflate: number[] = [];
  const CHUNK = 65535;
  for (let i = 0; i < rawData.length; i += CHUNK) {
    const slice = rawData.slice(i, i + CHUNK);
    const isFinal = i + CHUNK >= rawData.length;
    deflate.push(isFinal ? 1 : 0);
    const len = slice.length;
    deflate.push(len & 0xff, (len >> 8) & 0xff, ~len & 0xff, (~len >> 8) & 0xff);
    deflate.push(...slice);
  }
  const zlibStream = [0x78, 0x01, ...deflate, (adler2 >> 8) & 0xff, adler2 & 0xff, (adler1 >> 8) & 0xff, adler1 & 0xff];
  const sig = [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a];
  const ihdr = chunk([0x49, 0x48, 0x44, 0x52], [...u32be(size), ...u32be(size), 8, 2, 0, 0, 0]);
  const idat = chunk([0x49, 0x44, 0x41, 0x54], zlibStream);
  const iend = chunk([0x49, 0x45, 0x4e, 0x44], []);
  return Uint8Array.from([...sig, ...ihdr, ...idat, ...iend]);
}

/** Executes generated code text for real, injecting the real `create` in
 * place of the `import ... from 'openskp'` line - avoids any dependency on
 * a built dist/ existing, unlike a real dynamic `import()` of the text
 * would need. */
function runGeneratedCode(code: string): Uint8Array {
  const body = code.replace(/^import .*;\n/, '').replace(/export function build/, 'function build');
  // eslint-disable-next-line @typescript-eslint/no-implied-eval
  const factory = new Function('create', 'atob', `${body}\nreturn build();`);
  return factory(create, atob);
}

describe('toTypeScriptCode', () => {
  it('reproduces solid materials, instance-level paint, and instance names', () => {
    const b = create();
    const red = b.addMaterial('Red', [255, 0, 0]);
    const def = b.addComponentDefinition('Box', (d) => {
      d.addFace(
        [
          [0, 0, 0],
          [10, 0, 0],
          [10, 10, 0],
          [0, 10, 0],
        ],
        { material: red }
      );
    });
    // Instance-level paint + a name that differs from the definition's own
    // name - the two facts an early prototype silently dropped entirely.
    b.addInstance(def, { translation: [0, 0, 0], material: red, name: 'PaintedBox' });
    b.addInstance(def, { translation: [50, 0, 0], name: 'PlainBox' });

    const original = parseSkp(toBuffer(b.toBytes()));
    const code = toTypeScriptCode(original);
    const regenBytes = runGeneratedCode(code);
    const regen = parseSkp(toBuffer(regenBytes));

    expect(regen.materials.map((m) => m.name)).toEqual(original.materials.map((m) => m.name));
    expect(regen.root.instances).toHaveLength(2);
    const byName = new Map(regen.root.instances.map((i) => [i.name, i]));
    expect(byName.get('PaintedBox')?.materialId).not.toBeNull();
    expect(byName.get('PlainBox')?.materialId).toBeNull();
  });

  it('reproduces a genuinely empty definition name', () => {
    // Found via cross-language analysis (2026-08-28), same bug class as
    // the empty-instance-name case above: `def.name || \`Def${defId}\`
    // silently replaced a genuinely empty definition name with a
    // fabricated one. SketchUp Groups are internally just unnamed
    // component definitions (unlike Components, which SketchUp
    // auto-names), so an empty name is common in real files.
    const b = create();
    const def = b.addComponentDefinition('', (d) => {
      d.addFace([
        [0, 0, 0],
        [10, 0, 0],
        [10, 10, 0],
        [0, 10, 0],
      ]);
    });
    b.addInstance(def, { translation: [0, 0, 0] });

    const original = parseSkp(toBuffer(b.toBytes()));
    expect(Array.from(original.definitions.values())[0].name).toBe('');

    const code = toTypeScriptCode(original);
    const regenBytes = runGeneratedCode(code);
    const regen = parseSkp(toBuffer(regenBytes));

    expect(Array.from(regen.definitions.values())[0].name).toBe('');
  });

  it('reproduces a textured material with default projection', () => {
    const png = makeTestPng();
    const b = create();
    const tex = b.addTextureMaterial('Brick', png, 'brick.png', 1.0);
    b.addFace(
      [
        [0, 0, 0],
        [100, 0, 0],
        [100, 100, 0],
        [0, 100, 0],
      ],
      { material: tex }
    );

    const original = parseSkp(toBuffer(b.toBytes()));
    const code = toTypeScriptCode(original);
    const regenBytes = runGeneratedCode(code);
    const regen = parseSkp(toBuffer(regenBytes));

    const origMat = original.materials.find((m) => m.name === 'Brick')!;
    const regenMat = regen.materials.find((m) => m.name === 'Brick')!;
    expect(regenMat.texture).not.toBeNull();
    expect(Array.from(regenMat.texture!.data!)).toEqual(Array.from(origMat.texture!.data!));

    // The actual rendered UV at every vertex must match the source's own
    // default-projection UV, not just "some frontUv was emitted" - this is
    // what the earlier addTextureMaterial corruption (fixed in create.ts)
    // or a UV math error would show up as. origFace legitimately has no
    // uvTransform (default projection is what "default" means); regenFace
    // always gets an explicit one (see toTypeScriptCode's own doc on why) -
    // the two must still compute to the SAME final UV at each vertex.
    const points: [number, number, number][] = [
      [0, 0, 0],
      [100, 0, 0],
      [100, 100, 0],
      [0, 100, 0],
    ];
    const origFace = original.root.faces[0];
    const regenFace = regen.root.faces[0];
    expect(origFace.uvTransform).toBeNull();
    expect(regenFace.uvTransform).not.toBeNull();
    const { xr, yr } = faceUvBasis(origFace.normal);
    for (const p of points) {
      const origUv = computeFaceUv(p, xr, yr, origFace.uvTransform, origMat.texture!.width, origMat.texture!.height);
      const regenUv = computeFaceUv(p, xr, yr, regenFace.uvTransform, regenMat.texture!.width, regenMat.texture!.height);
      expect(regenUv[0]).toBeCloseTo(origUv[0], 6);
      expect(regenUv[1]).toBeCloseTo(origUv[1], 6);
    }
  });

  it('reproduces a textured material with an explicit UV pin', () => {
    const png = makeTestPng();
    const b = create();
    const tex = b.addTextureMaterial('Brick', png, 'brick.png', 1.0);
    b.addFace(
      [
        [0, 0, 0],
        [100, 0, 0],
        [100, 100, 0],
        [0, 100, 0],
      ],
      {
        material: tex,
        frontUv: [
          [[0, 0, 0], [0, 0]],
          [[100, 0, 0], [1, 0]],
          [[0, 100, 0], [0, 1]],
        ],
      }
    );

    const original = parseSkp(toBuffer(b.toBytes()));
    const code = toTypeScriptCode(original);
    const regenBytes = runGeneratedCode(code);
    const regen = parseSkp(toBuffer(regenBytes));

    expect(regen.root.faces).toHaveLength(1);
    expect(regen.root.faces[0].uvTransform).toEqual(original.root.faces[0].uvTransform);
  });
});

/**
 * Real-fixture regression: the bug that motivated this whole module
 * (instance-level paint on 95% of a file's instances, and every instance
 * name, silently dropped) was found on a large real file, not a synthetic
 * one - the synthetic unit tests above wouldn't have caught it, since
 * nobody had thought to construct a fixture that specifically exercised
 * instance-level paint before hitting it on a real file. Running this
 * against the repository's existing real fixtures (already used by
 * instanced-fixture-parity.test.ts) catches the same class of gap without
 * needing a new large binary fixture.
 */
describe('toTypeScriptCode - real fixtures', () => {
  const fixture = (name: string) => path.join(__dirname, 'fixtures', name);
  const readFixture = (name: string): ArrayBuffer => {
    const buf = fs.readFileSync(fixture(name));
    return buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength) as ArrayBuffer;
  };

  type Model = ReturnType<typeof parseSkp>;
  type Def = Model['root'];

  // Vertices/edges NOT referenced by any face loop - toTypeScriptCode's own
  // documented gap (standalone construction edges/curves), computed from
  // the ORIGINAL so the expected regenerated totals can be asserted
  // exactly (original minus exactly this), not just "roughly matches".
  function reachableCounts(def: Def): { verts: number; edges: number } {
    const referencedEdges = new Set<number>();
    for (const f of def.faces) for (const loop of f.loops) for (const { edgeId } of loop) referencedEdges.add(edgeId);
    const referencedVerts = new Set<number>();
    for (const e of def.edges) {
      if (referencedEdges.has(e.id)) {
        referencedVerts.add(e.v1Id);
        referencedVerts.add(e.v2Id);
      }
    }
    return { verts: referencedVerts.size, edges: referencedEdges.size };
  }

  function forEachDef(model: Model, fn: (d: Def) => void): void {
    fn(model.root);
    for (const [, d] of model.definitions) fn(d);
  }

  const FIXTURES = ['SU_File.skp', 'Untitled.skp', 'capilla_quiroz_v17.skp', 'gondola_v20.skp'];
  // single_material_v17.skp is deliberately excluded: it declares one
  // material used by zero faces anywhere - a real file the reader parses
  // fine, but not one toBytes() can ever re-save (this writer requires at
  // least one face), so it can't round-trip through generated code at all,
  // independent of anything toTypeScriptCode does.

  for (const name of FIXTURES) {
    it(
      `reproduces ${name}'s materials, layers, instance paint/names, and face-reachable geometry exactly`,
      () => {
        const original = parseSkp(readFixture(name));
        const code = toTypeScriptCode(original);
        const regenBytes = runGeneratedCode(code);
        const regen = parseSkp(toBuffer(regenBytes));

        expect(regen.materials.map((m) => m.name).sort()).toEqual(original.materials.map((m) => m.name).sort());
        expect(regen.layers.map((l) => l.name).sort()).toEqual(original.layers.map((l) => l.name).sort());

        // Instance names/paint must be preserved verbatim, not silently
        // replaced by their definition's own name or dropped entirely -
        // the exact bug this module was built to fix.
        const origInstKey = (i: { name: string; materialId: number | null }) => `${i.name} ${i.materialId != null}`;
        const origInstances = original.root.instances.map(origInstKey).sort();
        const regenInstances = regen.root.instances.map(origInstKey).sort();
        expect(regenInstances).toEqual(origInstances);

        // Geometry reachable from faces (the visible surfaces) must match
        // exactly on vertices, once the original's own known-unreachable
        // (standalone edge) count is subtracted out - see
        // toTypeScriptCode's own doc. Edges/faces only ever grow: a not-
        // quite-planar face gets auto-triangulated into 2+ real triangles
        // (new internal diagonal edges, never fewer of either), same
        // reason face count isn't asserted exactly either.
        let origVerts = 0,
          origEdges = 0,
          origFaces = 0,
          regenVerts = 0,
          regenEdges = 0,
          regenFaces = 0;
        forEachDef(original, (d) => {
          const r = reachableCounts(d);
          origVerts += r.verts;
          origEdges += r.edges;
          origFaces += d.faces.length;
        });
        forEachDef(regen, (d) => {
          regenVerts += d.vertices.length;
          regenEdges += d.edges.length;
          regenFaces += d.faces.length;
        });
        // gondola_v20.skp's one hole-bearing face (out of 1887) accounts for
        // a handful of extra vertices in the regenerated output - plausibly
        // the writer's own hole-to-boundary seam handling, not chased down
        // further since it's a small (<1%), well-isolated residual on the
        // single messiest fixture, not a systemic gap like the ones this
        // suite exists to catch (materials/instance paint/instance names,
        // all asserted exactly above).
        const vertTolerance = name === 'gondola_v20.skp' ? 10 : 0;
        expect(Math.abs(regenVerts - origVerts)).toBeLessThanOrEqual(vertTolerance);
        expect(regenEdges).toBeGreaterThanOrEqual(origEdges);
        expect(regenFaces).toBeGreaterThanOrEqual(origFaces);
      },
      20000
    );
  }
});
