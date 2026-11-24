import { describe, it, expect } from 'vitest';
import { create, SkpWriteError, Point3 } from '../src/create';
import { parseSkp } from '../src/index';

const KA: Point3 = [1, 2, 3];
const KB: Point3 = [4, 6, 3];
const KC: Point3 = [11, 12, 13];
const KD: Point3 = [14, 16, 13];

const VERTEX_CLASS = new Uint8Array([0xff, 0xff, 0, 0, 7, 0, 0x43, 0x56, 0x65, 0x72, 0x74, 0x65, 0x78]);

function toBuffer(bytes: Uint8Array): ArrayBuffer {
  return bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer;
}

function u32(n: number): Uint8Array {
  const b = new Uint8Array(4);
  new DataView(b.buffer).setUint32(0, n >>> 0, true);
  return b;
}

function f64s(vals: number[]): Uint8Array {
  const b = new Uint8Array(vals.length * 8);
  const dv = new DataView(b.buffer);
  vals.forEach((v, i) => dv.setFloat64(i * 8, v, true));
  return b;
}

function concat(...arrays: Uint8Array[]): Uint8Array {
  const total = arrays.reduce((acc, a) => acc + a.length, 0);
  const out = new Uint8Array(total);
  let pos = 0;
  for (const a of arrays) {
    out.set(a, pos);
    pos += a.length;
  }
  return out;
}

function ref(slot: number, large = false): Uint8Array {
  if (large) return concat(new Uint8Array([0xff, 0x7f]), u32(slot));
  return new Uint8Array([slot & 0xff, (slot >> 8) & 0xff]);
}

function pointBytes(p: Point3): Uint8Array {
  return f64s([p[0], p[1], p[2]]);
}

function connection(p: Point3): Uint8Array {
  return concat(u32(1), u32(4), pointBytes(p));
}

function locate(bytes: Uint8Array, needle: Uint8Array): number {
  outer: for (let i = 0; i + needle.length <= bytes.length; i++) {
    for (let j = 0; j < needle.length; j++) {
      if (bytes[i + j] !== needle[j]) continue outer;
    }
    return i;
  }
  throw new Error('dimension test record not found');
}

function fixture(): Uint8Array {
  const builder = create();
  builder.addDimension(KA, KB, -2);
  builder.addDimension(KC, KD, 3);
  builder.addConstructionPoint([101, 102, 103]);
  return builder.toBytes();
}

function fontSlot(bytes: Uint8Array): number {
  const at = locate(bytes, connection(KC)) - 7;
  return bytes[at] | (bytes[at + 1] << 8);
}

function vertex(p: Point3, tag: Uint8Array): Uint8Array {
  return concat(tag, new Uint8Array([0, 0, 0]), pointBytes(p));
}

function attach(bytes: Uint8Array, original: Point3, objectBytes: Uint8Array): Uint8Array {
  const at = locate(bytes, connection(original));
  const patched = new Uint8Array(bytes);
  patched[at] = 2;
  patched.fill(0, at + 8, at + 32);
  return concat(patched.subarray(0, at + 32), objectBytes, patched.subarray(at + 34));
}

function expectPoint(actual: [number, number, number] | null | undefined, expected: Point3): void {
  expect(actual).toBeDefined();
  expect(actual).not.toBeNull();
  for (let i = 0; i < 3; i++) {
    expect(actual![i]).toBeCloseTo(expected[i], 9);
  }
}

function expectTail(model: ReturnType<typeof parseSkp>): void {
  expect(model.root.constructionPoints).toHaveLength(1);
  expect(model.root.constructionPoints[0].position).toEqual([101, 102, 103]);
}

describe('legacy dimension geometry and vector offsets (#481)', () => {
  it('free endpoints and frame survive parsing', () => {
    const model = parseSkp(toBuffer(fixture()));
    expect(model.root.dimensions).toHaveLength(2);
    const first = model.root.dimensions[0];
    expectPoint(first.a, KA);
    expectPoint(first.b, KB);
    expect(first.offset).toBe(-2);
    expectPoint(first.planeX, [1, 0, 0]);
    expectPoint(first.normal, [0, 0, 1]);
    expect(first.offsetVector).toBeNull();
    expectPoint(model.root.dimensions[1].a, KC);
    expectPoint(model.root.dimensions[1].b, KD);
    expectTail(model);
  });

  it('inline vertices and later backrefs keep archive aligned', () => {
    for (const large of [false, true]) {
      let bytes = fixture();
      const font = fontSlot(bytes);
      bytes = attach(bytes, KD, ref(font + 3, large));
      bytes = attach(bytes, KC, ref(font + 2, large));
      bytes = attach(bytes, KB, vertex(KB, ref(((large ? 0x80000000 : 0x8000) | (font + 1)) >>> 0, large)));
      bytes = attach(bytes, KA, vertex(KA, VERTEX_CLASS));
      const model = parseSkp(toBuffer(bytes));
      expect(model.root.dimensions).toHaveLength(2);
      for (const dim of model.root.dimensions) {
        expectPoint(dim.a, KA);
        expectPoint(dim.b, KB);
      }
      expectTail(model);
    }
  });

  it('forward vertex references resolve after archive walk', () => {
    let bytes = fixture();
    const font = fontSlot(bytes);
    bytes = attach(bytes, KC, vertex(KC, ref(0x8000 | (font + 1))));
    bytes = attach(bytes, KB, vertex(KB, VERTEX_CLASS));
    bytes = attach(bytes, KA, ref(font + 4, true));
    const model = parseSkp(toBuffer(bytes));
    expect(model.root.dimensions).toHaveLength(2);
    expectPoint(model.root.dimensions[0].a, KC);
    expectPoint(model.root.dimensions[0].b, KB);
    expectPoint(model.root.dimensions[1].a, KC);
    expectTail(model);
  });

  it('plane axes preserve signed offset with reversed endpoints', () => {
    for (const axis of [1, 2, 3]) {
      for (const reversed of [false, true]) {
        const builder = create();
        const p1 = reversed ? KB : KA;
        const p2 = reversed ? KA : KB;
        builder.addDimension(p1, p2, -2);
        const bytes = new Uint8Array(builder.toBytes());
        const at = locate(bytes, connection(p2)) + 32 + 2 + 10;
        const frame = concat(f64s([0, -1, 0, 1, 0, 0]), u32(axis));
        bytes.set(frame, at);
        const model = parseSkp(toBuffer(bytes));
        expect(model.root.dimensions).toHaveLength(1);
        const dim = model.root.dimensions[0];
        expectPoint(dim.normal, [0, -1, 0]);
        expectPoint(dim.planeX, [1, 0, 0]);
        if (axis === 1) expectPoint(dim.offsetVector, [0, 0, -2]);
        else if (axis === 2) expectPoint(dim.offsetVector, [2, 0, 0]);
        else expect(dim.offsetVector).toBeNull();
      }
    }
  });

  it('nested paths are consumed without publishing local coordinates', () => {
    const raw = fixture();
    const at = locate(raw, connection(KA)) + 32 + 2 + 2;
    const patched = new Uint8Array(raw);
    patched[at] = 1;
    const path = ref(1234, true);
    const bytes = concat(patched.subarray(0, at + 4), path, patched.subarray(at + 4));
    const model = parseSkp(toBuffer(bytes));
    expect(model.root.dimensions).toHaveLength(2);
    expect(model.root.dimensions[0].a).toBeNull();
    expectPoint(model.root.dimensions[0].b, KB);
    expectPoint(model.root.dimensions[1].a, KC);
    expectTail(model);
  });

  it('vector offsets survive root and nested round trips', () => {
    const samples: Array<{ a: Point3; b: Point3; offset: Point3; expected: Point3 }> = [
      { a: [1, 2, 3], b: [11, 2, 3], offset: [0, 0, 4], expected: [0, 0, 4] },
      { a: [11, 2, 3], b: [1, 2, 3], offset: [0, 0, -4], expected: [0, 0, -4] },
      { a: [1, 2, 3], b: [1, 2, 13], offset: [-4, 0, 0], expected: [-4, 0, 0] },
      { a: [1, 2, 3], b: [4, 6, 8], offset: [-4, 3, 0], expected: [-4, 3, 0] },
      { a: [1, 2, 3], b: [11, 2, 3], offset: [7, -4, 0], expected: [0, -4, 0] },
      { a: [1, 2, 3], b: [1, 2, 13], offset: [0, 0, 0], expected: [0, 0, 0] },
      { a: [1, 2, 3], b: [11, 2, 3], offset: [7, 0, 0], expected: [0, 0, 0] },
      { a: [1, 2, 3], b: [4, 6, 8], offset: [3, 4, 5], expected: [0, 0, 0] },
      { a: [1, 2, 3], b: [11, 2, 3], offset: [0, 0, 1e-12], expected: [0, 0, 1e-12] },
    ];
    const builder = create();
    const defn = builder.addComponentDefinition('Dimensions', (def) => {
      for (const sample of samples) def.addDimension(sample.a, sample.b, sample.offset);
    });
    builder.addInstance(defn);
    for (const sample of samples) builder.addDimension(sample.a, sample.b, sample.offset);

    const model = parseSkp(toBuffer(builder.toBytes()));
    expect(model.root.dimensions).toHaveLength(samples.length);
    expect(model.definitions.size).toBe(1);
    const defDims = Array.from(model.definitions.values())[0].dimensions;
    expect(defDims).toHaveLength(samples.length);
    for (const dimensions of [model.root.dimensions, defDims]) {
      for (let i = 0; i < samples.length; i++) {
        expectPoint(dimensions[i].a, samples[i].a);
        expectPoint(dimensions[i].b, samples[i].b);
        expectPoint(dimensions[i].offsetVector, samples[i].expected);
      }
    }
  });

  it('invalid vector dimension does not corrupt following records', () => {
    const builder = create();
    for (const badVec of [[1, 2], [1, 2, 3, 4], []] as any[]) {
      expect(() => builder.addDimension(KA, KB, badVec)).toThrow(SkpWriteError);
      expect(() => builder.addDimension(badVec, KB, 5)).toThrow(SkpWriteError);
    }
    const tag = builder.addLayer('AfterRejected');
    const defn = builder.addComponentDefinition('Dimensions', (def) => {
      for (const bad of [Infinity, NaN]) {
        expect(() => def.addDimension(KA, KB, [bad, 0, 0])).toThrow(SkpWriteError);
        expect(() => builder.addDimension([bad, 0, 0], KB, [0, 0, 1])).toThrow(SkpWriteError);
      }
      expect(() => def.addDimension(KA, KB, [1, 2] as any)).toThrow(SkpWriteError);
      expect(() => def.addDimension(KA, KA, [0, 0, 1])).toThrow(SkpWriteError);
      def.addDimension(KA, KB, [0, 0, 2]);
    });
    builder.addInstance(defn, { layer: tag });
    for (const bad of [Infinity, NaN]) {
      expect(() => builder.addDimension([bad, 0, 0], KB, [0, 0, 1])).toThrow(SkpWriteError);
    }
    builder.addDimension(KA, KB, [0, 0, 3]);

    const model = parseSkp(toBuffer(builder.toBytes()));
    expect(model.root.dimensions).toHaveLength(1);
    expect(model.definitions.size).toBe(1);
    const defDims = Array.from(model.definitions.values())[0].dimensions;
    expect(defDims).toHaveLength(1);
    expectPoint(model.root.dimensions[0].offsetVector, [0, 0, 3]);
    expectPoint(defDims[0].offsetVector, [0, 0, 2]);
  });
});
