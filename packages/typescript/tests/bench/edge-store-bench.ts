/**
 * Edge storage: memory and lookup cost of the three id -> index choices.
 *
 * Run with:  npm run bench:edge-store
 *
 * Deliberately not a vitest test - these are timings and RSS readings,
 * which vary by machine and would be flaky as assertions. The stores'
 * correctness is asserted in tests/edge-store.test.ts instead.
 *
 * Memory is `heapUsed + arrayBuffers` after a forced GC: a typed array's
 * backing store lives outside the V8 heap, so heapUsed alone would show the
 * stores as nearly free, and RSS depends on what earlier measurements left
 * mapped (V8 does not return freed pages), which made the order of the
 * rows change the numbers. Values are retained in `keep` so nothing is
 * optimised away. Run node with --expose-gc.
 *
 * Densities are the ones measured on real files (edges / id span, per
 * definition): ~20% on legacy files, where edge ids are archive slots
 * interleaved with vertices, edge-uses, loops and faces; ~50% on 2021+
 * files, where ids alternate edge / vertex.
 */

import * as fs from 'fs';
import * as path from 'path';
import { DenseEdgeStore, MapEdgeStore, SortedEdgeStore, type EdgeStore } from '../../src/edge-store';
import { parseSkp, buildInstancedScene } from '../../src/index';

const keep: unknown[] = [];
const gc = (globalThis as any).gc as (() => void) | undefined;

function liveMb(): number {
  if (gc) {
    gc();
    gc();
  }
  const m = process.memoryUsage();
  return (m.heapUsed + m.arrayBuffers) / 1048576;
}

function median(values: number[]): number {
  const sorted = values.slice().sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 === 0 ? (sorted[mid - 1] + sorted[mid]) / 2 : sorted[mid];
}

const IMPLS: [string, new () => EdgeStore][] = [
  ['Map<id,index>', MapEdgeStore],
  ['sorted Uint32Array', SortedEdgeStore],
  ['dense Int32Array', DenseEdgeStore],
];

/**
 * Definitions are small: the 411 MB legacy file averages ~600 edges per
 * definition, so one store per definition (as the readers build them) is
 * measured, not one huge store - per-store overhead and growth slack are
 * part of the real cost.
 */
const EDGES = 1_000_000;
const PER_DEF = 600;

for (const [label, step] of [
  ['~20% (legacy)', 5],
  ['~50% (2021+)', 2],
] as const) {
  console.log(`\nMemory, ${EDGES.toLocaleString()} edges in stores of ${PER_DEF}, id density ${label} (live heap + array buffers)\n`);
  console.log('| storage | MB | bytes/edge | vs Map-of-pairs |');
  console.log('|---|---:|---:|---:|');

  let baselineMb = 0;
  {
    const before = liveMb();
    const stores: Map<number, [number | null, number | null]>[] = [];
    for (let d = 0; d < EDGES / PER_DEF; d++) {
      const m = new Map<number, [number | null, number | null]>();
      const base = d * PER_DEF * step;
      for (let i = 0; i < PER_DEF; i++) m.set(base + i * step, [i, i + 1]);
      stores.push(m);
    }
    keep.push(stores);
    baselineMb = liveMb() - before;
    console.log(
      `| today: \`Map<number,[v1,v2]>\` | ${baselineMb.toFixed(1)} | ${((baselineMb * 1048576) / EDGES).toFixed(0)} | 1.0x |`
    );
  }

  for (const [name, Impl] of IMPLS) {
    const before = liveMb();
    const stores: EdgeStore[] = [];
    for (let d = 0; d < EDGES / PER_DEF; d++) {
      const s = new Impl();
      const base = d * PER_DEF * step;
      for (let i = 0; i < PER_DEF; i++) s.set(base + i * step, [i, i + 1]);
      stores.push(s);
    }
    keep.push(stores);
    const mb = liveMb() - before;
    console.log(
      `| \`${name}\` | ${mb.toFixed(1)} | ${((mb * 1048576) / EDGES).toFixed(0)} | ${(baselineMb / mb).toFixed(1)}x |`
    );
  }
}

// ---------------------------------------------------------------- lookup

/**
 * The hot path: face-groups.ts reconstructs every face loop once per
 * instantiation, reading one endpoint per edge use. Today that is
 * `Map.get` + tuple destructure; with a store it is `v1(id)`/`v2(id)`.
 */
const LOOKUPS = 4_000_000;
const REPEATS = 5;
const N = 100_000;
const STEP = 5;

console.log(`\nLookup, ${LOOKUPS.toLocaleString()} random endpoint reads over ${N.toLocaleString()} edges (median of ${REPEATS})\n`);
console.log('| storage | ms | ns/lookup |');
console.log('|---|---:|---:|');

function timeLookups(read: (id: number, o: number) => number | null | undefined): number {
  const samples: number[] = [];
  for (let r = 0; r < REPEATS; r++) {
    let seed = 13579;
    let sink = 0;
    const t0 = performance.now();
    for (let i = 0; i < LOOKUPS; i++) {
      seed = (seed * 1103515245 + 12345) & 0x7fffffff;
      const v = read((seed % N) * STEP, seed & 1);
      if (v != null) sink += v;
    }
    samples.push(performance.now() - t0);
    keep.push(sink);
  }
  return median(samples);
}

{
  const m = new Map<number, [number | null, number | null]>();
  for (let i = 0; i < N; i++) m.set(i * STEP, [i, i + 1]);
  const ms = timeLookups((id, o) => {
    const e = m.get(id);
    if (!e) return undefined;
    const [v1, v2] = e;
    return o === 1 ? v1 : v2;
  });
  console.log(`| today: \`Map<number,[v1,v2]>\` | ${ms.toFixed(0)} | ${((ms * 1e6) / LOOKUPS).toFixed(1)} |`);
}

for (const [name, Impl] of IMPLS) {
  const s = new Impl();
  for (let i = 0; i < N; i++) s.set(i * STEP, [i, i + 1]);
  const ms = timeLookups((id, o) => (o === 1 ? s.v1(id) : s.v2(id)));
  console.log(`| \`${name}\` | ${ms.toFixed(0)} | ${((ms * 1e6) / LOOKUPS).toFixed(1)} |`);
}

// ------------------------------------------------------------ real files

const FIXTURES = ['SU_File.skp', 'Untitled.skp', 'capilla_quiroz_v17.skp', 'gondola_v20.skp'];

console.log('\nEnd-to-end on real fixtures, with the default store wired in (median of 3)\n');
console.log('| fixture | edges | parseSkp ms | buildInstancedScene ms |');
console.log('|---|---:|---:|---:|');

for (const name of FIXTURES) {
  const file = path.join(__dirname, '..', 'fixtures', name);
  const buf = fs.readFileSync(file);
  const ab = buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength);

  const model = parseSkp(ab);
  let edges = model.root.edges.length;
  for (const [, d] of model.definitions) edges += d.edges.length;

  const parseSamples: number[] = [];
  const sceneSamples: number[] = [];
  for (let r = 0; r < 3; r++) {
    let t = performance.now();
    keep.push(parseSkp(ab));
    parseSamples.push(performance.now() - t);
    t = performance.now();
    keep.push(buildInstancedScene(ab));
    sceneSamples.push(performance.now() - t);
  }

  console.log(
    `| ${name} | ${edges.toLocaleString()} | ${median(parseSamples).toFixed(0)} | ${median(sceneSamples).toFixed(0)} |`
  );
}
