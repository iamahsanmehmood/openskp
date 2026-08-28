import { describe, it, expect } from 'vitest';
import * as fs from 'fs';
import * as path from 'path';
import { openExisting } from '../src/edit';
import { create, SkpWriteError, Point3 } from '../src/create';
import { parseSkp } from '../src/index';
import { SkpModel, faceUvBasis, computeFaceUv } from '../src/model';
import { reconstructLoopVertices } from '../src/geometry';

/**
 * Tests for edit.ts - loading an existing legacy .skp file and rebuilding
 * it as a new SkpBuilder, ported from Python's test_edit.py (see edit.ts's
 * own docstring for the exact scope and known fidelity gaps this suite
 * exercises).
 */

const FIXTURES = path.join(__dirname, 'fixtures');
const SQUARE: Point3[] = [
  [0.0, 0.0, 0.0],
  [100.0, 0.0, 0.0],
  [100.0, 100.0, 0.0],
  [0.0, 100.0, 0.0],
];

function toBuffer(bytes: Uint8Array): ArrayBuffer {
  return bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer;
}

describe('openExisting', () => {
  it('rejects a VFF (modern) source', () => {
    expect(() => openExisting(path.join(FIXTURES, 'SU_File.skp'))).toThrow(/not a legacy-format/);
  });

  it('rejects a nonexistent file', () => {
    expect(() => openExisting(path.join(FIXTURES, 'does_not_exist.skp'))).toThrow();
  });

  it('round-trips a simple file (materials, layers, a definition, 2 instances, a root face)', () => {
    const builder = create();
    const red = builder.addMaterial('Red', [255, 0, 0]);
    const roof = builder.addLayer('Roof');
    const chair = builder.addComponentDefinition('Chair', (def) => {
      def.addFace([[0, 0, 0], [20, 0, 0], [20, 20, 0], [0, 20, 0]], { material: red });
    });
    builder.addInstance(chair, { translation: [0, 0, 0] });
    builder.addInstance(chair, { translation: [50, 0, 0] });
    builder.addFace(SQUARE, { material: red, layer: roof });

    const { builder: newBuilder } = openExisting(toBuffer(builder.toBytes()));
    const rebuilt = parseSkp(toBuffer(newBuilder.toBytes()));

    expect(rebuilt.root.faces.length).toBe(1);
    expect(rebuilt.root.instances.length).toBe(2);
    expect(rebuilt.definitions.size).toBe(1);
    const chairDefn = [...rebuilt.definitions.values()][0];
    expect(chairDefn.name).toBe('Chair');
    expect(chairDefn.faces.length).toBe(1);
    expect(rebuilt.materials.map((m) => m.name)).toEqual(['Red']);
    expect(rebuilt.layers.map((l) => l.name)).toContain('Roof');
  });

  it('preserves instance translation', () => {
    const builder = create();
    const post = builder.addComponentDefinition('Post', (def) => {
      def.addFace([[0, 0, 0], [5, 0, 0], [5, 5, 0], [0, 5, 0]]);
    });
    builder.addInstance(post, { translation: [37.5, -12.25, 8.0] });

    const { builder: newBuilder } = openExisting(toBuffer(builder.toBytes()));
    const rebuilt = parseSkp(toBuffer(newBuilder.toBytes()));

    const inst = rebuilt.root.instances[0];
    expect(inst.matrix[9]).toBeCloseTo(37.5, 9);
    expect(inst.matrix[10]).toBeCloseTo(-12.25, 9);
    expect(inst.matrix[11]).toBeCloseTo(8.0, 9);
  });

  it('preserves instance hidden state and layer color', () => {
    const builder = create();
    const roof = builder.addLayer('Roof', { color: [150, 75, 30], hidden: true });
    const post = builder.addComponentDefinition('Post', (def) => {
      def.addFace([[0, 0, 0], [5, 0, 0], [5, 5, 0], [0, 5, 0]]);
    });
    builder.addInstance(post, { hidden: true, layer: roof });

    const { builder: newBuilder } = openExisting(toBuffer(builder.toBytes()));
    const rebuilt = parseSkp(toBuffer(newBuilder.toBytes()));

    expect(rebuilt.root.instances[0].hidden).toBe(true);
    const roofLayer = rebuilt.layers.find((l) => l.name === 'Roof')!;
    expect(roofLayer.color).toEqual({ r: 150, g: 75, b: 30 });
    expect(roofLayer.hidden).toBe(true);
  });

  it('replays nested definitions in dependency order (Wheel before Car)', () => {
    const builder = create();
    const wheel = builder.addComponentDefinition('Wheel', (def) => {
      def.addFace([[0, 0, 0], [10, 0, 0], [10, 10, 0], [0, 10, 0]]);
    });
    const car = builder.addComponentDefinition('Car', (def) => {
      def.addFace([[0, 0, 0], [100, 0, 0], [100, 50, 0], [0, 50, 0]]);
      def.addInstance(wheel, { translation: [10, 10, 0] });
      def.addInstance(wheel, { translation: [80, 10, 0] });
    });
    builder.addInstance(car);

    const { builder: newBuilder } = openExisting(toBuffer(builder.toBytes()));
    const rebuilt = parseSkp(toBuffer(newBuilder.toBytes()));

    const byName = new Map([...rebuilt.definitions.values()].map((d) => [d.name, d]));
    expect(new Set(byName.keys())).toEqual(new Set(['Wheel', 'Car']));
    expect(byName.get('Car')!.instances.length).toBe(2);
  });

  it('materials and layers are reusable (not duplicated) after replay', () => {
    const builder = create();
    const red = builder.addMaterial('Red', [255, 0, 0]);
    const roof = builder.addLayer('Roof');
    builder.addFace(SQUARE, { material: red, layer: roof });

    const { builder: newBuilder } = openExisting(toBuffer(builder.toBytes()));
    expect(newBuilder.materialsByName.has('Red')).toBe(true);
    expect(newBuilder.layersByName.has('Roof')).toBe(true);
    newBuilder.addFace(
      [[300, 0, 0], [310, 0, 0], [310, 10, 0], [300, 10, 0]],
      { material: newBuilder.materialsByName.get('Red'), layer: newBuilder.layersByName.get('Roof') }
    );
    const rebuilt = parseSkp(toBuffer(newBuilder.toBytes()));
    expect(rebuilt.materials.length).toBe(1); // still just "Red" - reused, not duplicated
    expect(rebuilt.root.faces.length).toBe(2);
  });

  it('returns definitions by name, directly usable for a new placement', () => {
    const builder = create();
    const wheel = builder.addComponentDefinition('Wheel', (def) => {
      def.addFace([[0, 0, 0], [10, 0, 0], [10, 10, 0], [0, 10, 0]]);
    });
    builder.addInstance(wheel, { translation: [0, 0, 0] });

    const { builder: newBuilder, definitions } = openExisting(toBuffer(builder.toBytes()));
    expect(definitions.has('Wheel')).toBe(true);
    newBuilder.addInstance(definitions.get('Wheel')!, { translation: [100, 0, 0] });
    const rebuilt = parseSkp(toBuffer(newBuilder.toBytes()));
    expect(rebuilt.definitions.size).toBe(1); // still just one Wheel definition
    expect(rebuilt.root.instances.length).toBe(2); // but now placed twice
  });

  it('a genuinely new material/layer/definition/group is rejected after replay', () => {
    const builder = create();
    builder.addFace(SQUARE);

    const { builder: newBuilder } = openExisting(toBuffer(builder.toBytes()));
    expect(() => newBuilder.addMaterial('Chrome', [180, 180, 185])).toThrow(SkpWriteError);
    expect(() => newBuilder.addLayer('Extra')).toThrow(SkpWriteError);
    expect(() => newBuilder.addComponentDefinition('New', () => {})).toThrow(SkpWriteError);
    expect(() => newBuilder.addGroup(() => {}, { name: 'NewGroup' })).toThrow(SkpWriteError);
  });

  // Minimal 4x4 solid-color PNG (same generator as create.test.ts's Textures
  // suite - kept local here to avoid a cross-file test dependency). Shared
  // by every test in this describe block that needs a real texture.
  function makeTestPng(): Uint8Array {
    function crc32(buf: number[]): number {
      const table: number[] = [];
      for (let n = 0; n < 256; n++) {
        let c = n;
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
    const size = 4;
    const rgb = [200, 50, 50];
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
    deflate.push(1, rawData.length & 0xff, (rawData.length >> 8) & 0xff, ~rawData.length & 0xff, (~rawData.length >> 8) & 0xff, ...rawData);
    const zlibStream = [0x78, 0x01, ...deflate, (adler2 >> 8) & 0xff, adler2 & 0xff, (adler1 >> 8) & 0xff, adler1 & 0xff];
    const sig = [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a];
    const ihdr = chunk([0x49, 0x48, 0x44, 0x52], [...u32be(size), ...u32be(size), 8, 2, 0, 0, 0]);
    const idat = chunk([0x49, 0x44, 0x41, 0x54], zlibStream);
    const iend = chunk([0x49, 0x45, 0x4e, 0x44], []);
    return Uint8Array.from([...sig, ...ihdr, ...idat, ...iend]);
  }

  it('a positioned texture round-trips', () => {
    const builder = create();
    const png = makeTestPng();
    const brick = builder.addTextureMaterial('Brick', png, 'brick.png');
    builder.addFace(SQUARE, {
      material: brick,
      frontUv: [
        [[0.0, 0.0, 0.0], [0.0, 0.0]],
        [[50.0, 0.0, 0.0], [1.0, 0.0]],
        [[0.0, 50.0, 0.0], [0.0, 1.0]],
      ],
    });

    const { builder: newBuilder } = openExisting(toBuffer(builder.toBytes()));
    const rebuilt = parseSkp(toBuffer(newBuilder.toBytes()));

    const face = rebuilt.root.faces[0];
    expect(face.uvTransform).not.toBeNull();
    expect(rebuilt.materials[0].texture).not.toBeNull();
    expect(Array.from(rebuilt.materials[0].texture!.data as Uint8Array)).toEqual(Array.from(png));
  });

  it('a face with a hole round-trips without a "hole" warning', () => {
    const wall: Point3[] = [[0, 0, 0], [200, 0, 0], [200, 100, 0], [0, 100, 0]];
    const window: Point3[] = [[80, 30, 0], [120, 30, 0], [120, 70, 0], [80, 70, 0]];
    const builder = create();
    builder.addFace(wall, { holes: [window] });

    const { builder: newBuilder, warnings } = openExisting(toBuffer(builder.toBytes()));
    expect(warnings.some((w) => w.includes('hole'))).toBe(false);
    const rebuilt = parseSkp(toBuffer(newBuilder.toBytes()));
    expect(rebuilt.root.faces[0].loops.length).toBe(2);
  });

  it('an empty source can still be saved after adding geometry', () => {
    const { builder: newBuilder } = openExisting(path.join(FIXTURES, 'blank_v17.skp'));
    expect(() => newBuilder.toBytes()).toThrow(/no geometry/);
    newBuilder.addFace(SQUARE);
    expect(newBuilder.toBytes().length).toBeGreaterThan(0);
  });

  it('per-edge flags collapse to a per-face approximation (documented gap)', () => {
    const builder = create();
    builder.addFace(SQUARE, { hiddenEdges: true, softEdges: true, smoothEdges: true });
    const { builder: newBuilder } = openExisting(toBuffer(builder.toBytes()));
    const rebuilt = parseSkp(toBuffer(newBuilder.toBytes()));
    expect(rebuilt.root.edges.every((e) => e.hidden && e.soft && e.smooth)).toBe(true);
  });

  it('a default-projected texture UV matches the source (not left corrupted)', () => {
    // Found via cross-language analysis (2026-08-28): replayMaterials wrote
    // every rebuilt texture material with the library's (until now)
    // corrupted default applied height, and replayUv only replayed a
    // face's UV when it already had an explicit uvTransform - leaving a
    // DEFAULT-projected textured face's material (and thus its real
    // SketchUp rendering) corrupted end to end. Both are fixed; this
    // checks the actual rendered UV at every vertex matches, not just
    // "some texture data round-tripped".
    const png = makeTestPng();
    const builder = create();
    const brick = builder.addTextureMaterial('Brick', png, 'brick.png');
    builder.addFace(SQUARE, { material: brick }); // no frontUv - default projection

    const source = parseSkp(toBuffer(builder.toBytes()));
    const { builder: newBuilder, warnings } = openExisting(toBuffer(builder.toBytes()));
    expect(warnings.some((w) => w.includes('tile size'))).toBe(false);
    const rebuilt = parseSkp(toBuffer(newBuilder.toBytes()));

    function faceUvs(model: SkpModel): [number, number][] {
      const mat = model.materials[0];
      const face = model.root.faces[0];
      const edges = new Map<number, [number | null, number | null]>();
      for (const e of model.root.edges) edges.set(e.id, [e.v1Id, e.v2Id]);
      const vids = reconstructLoopVertices(face.loops[0], edges);
      const verticesById = new Map(model.root.vertices.map((v) => [v.id, v]));
      const pts: Point3[] = vids.map((v) => {
        const vertex = verticesById.get(v)!;
        return [vertex.x, vertex.y, vertex.z];
      });
      const { xr, yr } = faceUvBasis(face.normal as [number, number, number]);
      const tileW = mat.texture!.width || 1.0;
      const tileH = mat.texture!.height || 1.0;
      return pts.map((p) => computeFaceUv(p, xr, yr, face.uvTransform, tileW, tileH));
    }

    const sourceUvs = faceUvs(source);
    const rebuiltUvs = faceUvs(rebuilt);
    expect(rebuiltUvs.length).toBe(sourceUvs.length);
    for (let i = 0; i < sourceUvs.length; i++) {
      expect(rebuiltUvs[i][0]).toBeCloseTo(sourceUvs[i][0], 6);
      expect(rebuiltUvs[i][1]).toBeCloseTo(sourceUvs[i][1], 6);
    }
  });

  it('a genuinely empty instance name is preserved, not replaced', () => {
    // Found via cross-language analysis (2026-08-28): addInstance's own
    // `name ?? definition.name` fallback and replayInstance's
    // `inst.name || undefined` both silently replaced a genuinely empty
    // instance name with its definition's name - a real difference, not
    // cosmetic (a later rename of the definition would no longer show
    // through).
    const builder = create();
    const box = builder.addComponentDefinition('Box', (def) => {
      def.addFace([[0, 0, 0], [10, 0, 0], [10, 10, 0], [0, 10, 0]]);
    });
    builder.addInstance(box, { name: '', translation: [0, 0, 0] });

    const source = parseSkp(toBuffer(builder.toBytes()));
    expect(source.root.instances[0].name).toBe('');

    const { builder: newBuilder } = openExisting(toBuffer(builder.toBytes()));
    const rebuilt = parseSkp(toBuffer(newBuilder.toBytes()));
    expect(rebuilt.root.instances[0].name).toBe('');
  });
});

describe('Real-world fixtures (non-writer-authored)', () => {
  // The true stress test for edit.ts, since every test above only
  // exercises content this package's own writer already produces (a much
  // narrower subset of what real SketchUp files contain).
  const fixtures = ['capilla_quiroz_v17.skp', 'gondola_v20.skp'];

  for (const fixtureName of fixtures) {
    it(`round-trips ${fixtureName} without crashing`, () => {
      const fixturePath = path.join(FIXTURES, fixtureName);
      if (!fs.existsSync(fixturePath)) return; // skip if not present, same as Python's suite
      const { builder: newBuilder } = openExisting(fixturePath);
      const data = newBuilder.toBytes();
      // Self-parses without throwing - the authoritative "is this
      // structurally valid" check for a file this large/real.
      expect(() => parseSkp(toBuffer(data))).not.toThrow();
    }, 30000);
  }

  it('capilla_quiroz_v17.skp preserves almost all geometry', () => {
    const fixturePath = path.join(FIXTURES, 'capilla_quiroz_v17.skp');
    if (!fs.existsSync(fixturePath)) return;
    const origBuf = fs.readFileSync(fixturePath);
    const orig = parseSkp(toBuffer(new Uint8Array(origBuf.buffer, origBuf.byteOffset, origBuf.byteLength)));
    const { builder: newBuilder } = openExisting(fixturePath);
    const rebuilt = parseSkp(toBuffer(newBuilder.toBytes()));

    let origTotal = orig.root.faces.length;
    for (const d of orig.definitions.values()) origTotal += d.faces.length;
    let rebuiltTotal = rebuilt.root.faces.length;
    for (const d of rebuilt.definitions.values()) rebuiltTotal += d.faces.length;

    // At most a handful of faces (e.g. a degenerate UV correspondence) are
    // expected to be skipped, never a large fraction - a big drop would
    // indicate silent corruption, not a legitimately-scoped gap.
    expect(rebuiltTotal).toBeGreaterThanOrEqual(origTotal - 5);
    expect(rebuilt.root.instances.length).toBe(orig.root.instances.length);
    expect(rebuilt.definitions.size).toBe(orig.definitions.size);
  }, 30000);
});
