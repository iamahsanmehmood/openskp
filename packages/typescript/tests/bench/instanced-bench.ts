/**
 * Deterministic benchmark for the baked vs instanced scene paths.
 *
 * Run with:  npm run bench:instanced
 *
 * Deliberately NOT a vitest test: timings vary with machine and load, and
 * asserting on them in CI produces flaky failures. The structural claims
 * (one resource per definition, buffer bytes flat as instance count grows)
 * are asserted in tests/instanced-scene.test.ts instead; this script only
 * measures and reports.
 *
 * The scene is synthetic and fully deterministic - one nontrivial component
 * repeated N times - so successive runs measure the same work.
 */

import { buildSceneFromParsed } from '../../src/model';
import { buildInstancedSceneFromParsed } from '../../src/instanced';
import { toGLB } from '../../src/index';
import { toInstancedGLB } from '../../src/instanced-glb';
import { repeatedComponentScene } from '../helpers/instanced-fixtures';

const COUNTS = [1, 10, 100, 1000];
/** Faces per component: enough that a component is not trivial, small
 * enough that the 1,000-instance baked case still completes quickly. */
const FACES_PER_COMPONENT = 24;
/** Median of several runs, so one unlucky GC pause does not set the number. */
const REPEATS = 5;

function median(values: number[]): number {
  const sorted = values.slice().sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 === 0 ? (sorted[mid - 1] + sorted[mid]) / 2 : sorted[mid];
}

function timeMs(fn: () => void): number {
  const samples: number[] = [];
  for (let i = 0; i < REPEATS; i++) {
    const t0 = performance.now();
    fn();
    samples.push(performance.now() - t0);
  }
  return median(samples);
}

function bakedBytes(scene: ReturnType<typeof buildSceneFromParsed>): number {
  let total = 0;
  for (const p of scene.glbPrimitives) {
    total += p.positions.byteLength + p.normals.byteLength + p.uvs.byteLength + p.indices.byteLength;
  }
  return total;
}

function instancedBytes(scene: ReturnType<typeof buildInstancedSceneFromParsed>): number {
  let total = 0;
  for (const r of scene.meshResources) {
    for (const p of r.primitives) {
      total += p.positions.byteLength + p.normals.byteLength + p.uvs.byteLength + p.indices.byteLength;
    }
  }
  return total;
}

function countNodes(node: { children: any[] }): number {
  return 1 + node.children.reduce((s: number, c: any) => s + countNodes(c), 0);
}

const kb = (bytes: number) => (bytes / 1024).toFixed(1);

interface Row {
  count: number;
  bakedMs: number;
  instancedMs: number;
  bakedBytes: number;
  instancedBytes: number;
  bakedPrims: number;
  meshResources: number;
  instanceNodes: number;
  bakedGlb: number;
  instancedGlb: number;
}

const rows: Row[] = [];

for (const count of COUNTS) {
  // Rebuild the parsed input per measurement: both builders consume it
  // read-only, but sharing one instance across timed runs would let the
  // first run warm caches the others benefit from.
  const parsedFor = () => repeatedComponentScene(count, FACES_PER_COMPONENT);

  const bakedMs = timeMs(() => void buildSceneFromParsed(parsedFor()));
  const instancedMs = timeMs(() => void buildInstancedSceneFromParsed(parsedFor()));

  const baked = buildSceneFromParsed(parsedFor());
  const instanced = buildInstancedSceneFromParsed(parsedFor());

  rows.push({
    count,
    bakedMs,
    instancedMs,
    bakedBytes: bakedBytes(baked),
    instancedBytes: instancedBytes(instanced),
    bakedPrims: baked.glbPrimitives.length,
    meshResources: instanced.meshResources.length,
    instanceNodes: countNodes(instanced.sceneHierarchy) - 1, // exclude root
    bakedGlb: toGLB(baked).length,
    instancedGlb: toInstancedGLB(instanced).length,
  });
}

console.log(
  `\nSynthetic scene: one ${FACES_PER_COMPONENT}-face component repeated N times.` +
    `\nTimings are the median of ${REPEATS} runs. Node ${process.version} on ${process.platform}/${process.arch}.\n`
);

const header = [
  'N',
  'buildScene ms',
  'buildInstancedScene ms',
  'baked buf KB',
  'instanced buf KB',
  'baked prims',
  'mesh resources',
  'instance nodes',
  'baked GLB KB',
  'instanced GLB KB',
];

const table = rows.map((r) => [
  String(r.count),
  r.bakedMs.toFixed(2),
  r.instancedMs.toFixed(2),
  kb(r.bakedBytes),
  kb(r.instancedBytes),
  String(r.bakedPrims),
  String(r.meshResources),
  String(r.instanceNodes),
  kb(r.bakedGlb),
  kb(r.instancedGlb),
]);

const widths = header.map((h, i) =>
  Math.max(h.length, ...table.map((row) => row[i].length))
);

const line = (cells: string[]) =>
  '| ' + cells.map((c, i) => c.padStart(widths[i])).join(' | ') + ' |';

console.log(line(header));
console.log('|' + widths.map((w) => '-'.repeat(w + 2)).join('|') + '|');
for (const row of table) {
  console.log(line(row));
}

const last = rows[rows.length - 1];
console.log(
  `\nAt N=${last.count}: geometry buffers ${(last.bakedBytes / last.instancedBytes).toFixed(1)}x smaller, ` +
    `GLB ${(last.bakedGlb / last.instancedGlb).toFixed(1)}x smaller, ` +
    `build ${(last.bakedMs / last.instancedMs).toFixed(1)}x faster.\n`
);
