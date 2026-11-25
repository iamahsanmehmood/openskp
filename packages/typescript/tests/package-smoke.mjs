// Runs the built package (npm run build first) as Node.js users load it, with
// `import` and with `require`: reading, editing and writing files on disk.
// The unit tests run the sources, so they can't see a broken build.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { createRequire } from 'node:module';
import os from 'node:os';
import path from 'node:path';
import * as esm from 'openskp';

const cjs = createRequire(import.meta.url)('openskp');
const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'openskp-smoke-'));
try {
  for (const [label, openskp] of [['import', esm], ['require', cjs]]) {
    const file = name => path.join(dir, label, name);
    fs.mkdirSync(path.join(dir, label));

    const builder = openskp.create();
    builder.addFace([[0, 0, 0], [10, 0, 0], [10, 10, 0], [0, 10, 0]]);
    builder.save(file('a.skp'));
    const edited = openskp.openExisting(file('a.skp')).builder;
    edited.addFace([[0, 0, 5], [10, 0, 5], [10, 10, 5]]);
    edited.save(file('b.skp'));

    const skp = openskp.SkpFile.open(file('b.skp'));
    assert.equal(skp.parse().root.faces.length, 2, label);
    assert.throws(() => openskp.SkpFile.open(file('missing.skp')), /File not found/);

    // Pooled Buffer input (non-zero byteOffset into Node's shared slab)
    const prevPool = Buffer.poolSize;
    Buffer.poolSize = 64 * 1024;
    let pooled;
    try {
      Buffer.allocUnsafe(8192).fill(0xff);
      const rawBytes = fs.readFileSync(file('b.skp'));
      pooled = Buffer.from(rawBytes);
    } finally {
      Buffer.poolSize = prevPool;
    }
    assert.ok(pooled.byteOffset > 0 && pooled.buffer.byteLength > pooled.byteLength, label);
    assert.equal(openskp.parseSkp(pooled).root.faces.length, 2, label);
    assert.equal(openskp.SkpFile.fromBuffer(pooled).parse().root.faces.length, 2, label);
    const scene = skp.buildScene();
    assert.equal(openskp.buildScene(pooled).glbPrimitives.length, scene.glbPrimitives.length, label);
    assert.equal(openskp.buildInstancedScene(pooled).meshResources.length, 1, label);
    assert.equal(openskp.extractThumbnail(pooled), null, label);
    assert.equal(openskp.openExisting(pooled).builder.toBytes().length > 0, true, label);
    openskp.exportDXF(scene, file('out/a.dxf'));
    openskp.exportIFC(scene, file('out/a.ifc'));
    openskp.exportOBJ(scene, file('out/a.obj'));
    openskp.exportPLY(scene, file('out/a.ply'));
    openskp.exportSTL(scene, file('out/a.stl'));
    assert.deepEqual(
      fs.readdirSync(file('out')).sort(),
      ['a.dxf', 'a.ifc', 'a.mtl', 'a.obj', 'a.ply', 'a.stl'],
      label
    );
    console.log(`${label}: ok`);
  }
} finally {
  fs.rmSync(dir, { recursive: true, force: true });
}
