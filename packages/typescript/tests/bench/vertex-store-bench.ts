/**
 * Vertex storage: memory and lookup cost, Map-index vs sorted-array.
 *
 * Run with:  npm run bench:vertex-store
 *
 * Deliberately not a vitest test - these are timings and RSS readings,
 * which vary by machine and would be flaky as assertions. The stores'
 * correctness is asserted in tests/vertex-store.test.ts instead.
 *
 * Memory is read from RSS, not heapUsed: a typed array's backing store
 * lives outside the V8 heap that heapUsed reports, which makes the typed
 * cases look like literally 0 MB. Values are retained in `keep` so nothing
 * is optimised away.
 *
 * Run node with --expose-gc for stable memory numbers.
 */

import * as fs from 'fs';
import * as path from 'path';
import { MapVertexStore, SortedVertexStore, type VertexStore } from '../../src/vertex-store';
import { parseSkp, buildScene } from '../../src/index';

const keep: unknown[] = [];
const gc = (globalThis as any).gc as (() => void) | undefined;

function rssMb(): number {
  if (gc) gc();
  return process.memoryUsage().rss / 1048576;
}

function median(values: number[]): number {
  const sorted = values.slice().sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 === 0 ? (sorted[mid - 1] + sorted[mid]) / 2 : sorted[mid];
}

const IMPLS: [string, new () => VertexStore][] = [
  ['Map<id,index>', MapVertexStore],
  ['sorted Uint32Array', SortedVertexStore],
];

// ---------------------------------------------------------------- memory

const N = 1_000_000;
/** Ids at ~33% density, matching what the fixtures actually contain. */
const idFor = (i: number) => i * 3;

console.log(`\nMemory, ${N.toLocaleString()} vertices at ~33% id density (RSS delta)\n`);
console.log('| storage | MB | vs Map-of-arrays |');
console.log('|---|---:|---:|');

let baselineMb = 0;
{
  const before = rssMb();
  const m = new Map<number, [number, number, number]>();
  for (let i = 0; i < N; i++) m.set(idFor(i), [i * 1.1, i * 2.2, i * 3.3]);
  keep.push(m);
  baselineMb = rssMb() - before;
  console.log(`| today: \`Map<number,[x,y,z]>\` | ${baselineMb.toFixed(1)} | 1.0x |`);
}

const memory: Record<string, number> = {};
for (const [name, Impl] of IMPLS) {
  const before = rssMb();
  const store = new Impl();
  for (let i = 0; i < N; i++) store.set(idFor(i), [i * 1.1, i * 2.2, i * 3.3]);
  keep.push(store);
  const mb = rssMb() - before;
  memory[name] = mb;
  console.log(`| \`${name}\` | ${mb.toFixed(1)} | ${(baselineMb / mb).toFixed(1)}x |`);
}

// ---------------------------------------------------------------- lookup

/**
 * The hot path this choice actually matters for: face-groups.ts calls
 * `builder.vertices.get(vId)` once per triangle corner, so lookups vastly
 * outnumber writes.
 */
const LOOKUPS = 2_000_000;
const REPEATS = 5;

console.log(`\nLookup, ${LOOKUPS.toLocaleString()} random gets over ${N.toLocaleString()} vertices (median of ${REPEATS})\n`);
console.log('| storage | ms | ns/lookup |');
console.log('|---|---:|---:|');

{
  const m = new Map<number, [number, number, number]>();
  for (let i = 0; i < N; i++) m.set(idFor(i), [i * 1.1, i * 2.2, i * 3.3]);
  const samples: number[] = [];
  for (let r = 0; r < REPEATS; r++) {
    let seed = 13579;
    let sink = 0;
    const t0 = performance.now();
    for (let i = 0; i < LOOKUPS; i++) {
      seed = (seed * 1103515245 + 12345) & 0x7fffffff;
      const v = m.get(idFor(seed % N));
      if (v) sink += v[0];
    }
    samples.push(performance.now() - t0);
    keep.push(sink);
  }
  const ms = median(samples);
  console.log(`| today: \`Map<number,[x,y,z]>\` | ${ms.toFixed(0)} | ${((ms * 1e6) / LOOKUPS).toFixed(1)} |`);
}

for (const [name, Impl] of IMPLS) {
  const store = new Impl();
  for (let i = 0; i < N; i++) store.set(idFor(i), [i * 1.1, i * 2.2, i * 3.3]);
  const samples: number[] = [];
  for (let r = 0; r < REPEATS; r++) {
    let seed = 13579;
    let sink = 0;
    const t0 = performance.now();
    for (let i = 0; i < LOOKUPS; i++) {
      seed = (seed * 1103515245 + 12345) & 0x7fffffff;
      const v = store.get(idFor(seed % N));
      if (v) sink += v[0];
    }
    samples.push(performance.now() - t0);
    keep.push(sink);
  }
  const ms = median(samples);
  console.log(`| \`${name}\` | ${ms.toFixed(0)} | ${((ms * 1e6) / LOOKUPS).toFixed(1)} |`);
}

// ------------------------------------------------------------ real files

const FIXTURES = ['SU_File.skp', 'Untitled.skp', 'capilla_quiroz_v17.skp', 'gondola_v20.skp'];

console.log('\nEnd-to-end on real fixtures, with the store actually wired in (median of 3)\n');
console.log('| fixture | vertices | parseSkp ms | buildScene ms |');
console.log('|---|---:|---:|---:|');

for (const name of FIXTURES) {
  const file = path.join(__dirname, '..', 'fixtures', name);
  const buf = fs.readFileSync(file);
  const ab = buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength);

  const model = parseSkp(ab);
  let verts = model.root.vertices.length;
  for (const [, d] of model.definitions) verts += d.vertices.length;

  const parseSamples: number[] = [];
  const sceneSamples: number[] = [];
  for (let r = 0; r < 3; r++) {
    let t = performance.now();
    keep.push(parseSkp(ab));
    parseSamples.push(performance.now() - t);
    t = performance.now();
    keep.push(buildScene(ab));
    sceneSamples.push(performance.now() - t);
  }

  console.log(
    `| ${name} | ${verts.toLocaleString()} | ${median(parseSamples).toFixed(0)} | ${median(sceneSamples).toFixed(0)} |`
  );
}

console.log(
  `\nStores agree on results; correctness is covered by tests/vertex-store.test.ts` +
    ` and the existing fixture-parity suite.\n`
);
