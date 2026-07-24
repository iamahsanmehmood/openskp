import { describe, it, expect } from 'vitest';
import * as fs from 'fs';
import * as path from 'path';
import { parseSkp, buildScene } from '../src/index';
import { isLegacy } from '../src/legacy';

/**
 * Real-file regression test for the classic (pre-2021) MFC .skp reader.
 *
 * Fixture: fixtures/capilla_quiroz_v17.skp - a small chapel authored in
 * SketchUp 2017 (v17.0.18899, ~212 KB), the same real-file fixture Marco
 * Sumari contributed for the Python legacy reader (PR #14). Every
 * assertion here was cross-checked directly against Python's
 * `SkpFile.open(...).parse()` on this exact file - byte-for-byte matching
 * per-definition face/edge/vertex counts, individual vertex coordinates,
 * materials, layers, and bounding box.
 */
describe('Legacy MFC reader (classic pre-2021 .skp)', () => {
  const filePath = path.join(__dirname, 'fixtures', 'capilla_quiroz_v17.skp');
  const buf = fs.readFileSync(filePath);
  const data = new Uint8Array(buf.buffer, buf.byteOffset, buf.byteLength);

  it('is detected as a legacy container', () => {
    expect(isLegacy(data)).toBe(true);
  });

  it('parses a real v17 file matching Python ground truth exactly', () => {
    const arrayBuffer = buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength) as ArrayBuffer;
    const model = parseSkp(arrayBuffer);

    // 1. Version
    expect(model.version).toBe('{17.0.18899}');

    // 2. Definitions - ROOT is excluded from model.definitions by design
    // (same as the VFF path), so only the two named component definitions
    // show up here.
    expect(model.definitions.size).toBe(2);

    const puerta = model.definitions.get(40);
    expect(puerta).toBeDefined();
    expect(puerta!.name).toBe('puerta');
    expect(puerta!.faces.length).toBe(24);
    expect(puerta!.edges.length).toBe(95);
    expect(puerta!.vertices.length).toBe(64);

    const grada = model.definitions.get(395);
    expect(grada).toBeDefined();
    expect(grada!.name).toBe('grada');
    expect(grada!.faces.length).toBe(11);
    expect(grada!.edges.length).toBe(30);
    expect(grada!.vertices.length).toBe(20);

    // Spot-check individual vertex coordinates (not just counts) against
    // Python's output for the same definition.
    const v45 = puerta!.vertices.find((v) => v.id === 45);
    expect(v45).toBeDefined();
    expect(v45!.x).toBeCloseTo(60.671292283583, 9);
    expect(v45!.y).toBeCloseTo(8.526512829121202e-14, 20);
    expect(v45!.z).toBeCloseTo(109.03580700984524, 9);

    // 3. Materials - 16 real materials, exact name set
    expect(model.materials.length).toBe(16);
    const materialNames = model.materials.map((m) => m.name).sort();
    expect(materialNames).toEqual(
      [
        '*1',
        '[0037_SandyBrown]',
        '[0048_PaleGoldenrod]',
        '[0050_LemonChiffon]',
        '[0062_YellowGreen]',
        '[0064_Chartreuse]',
        '[0069_LimeGreen]',
        '[0070_SpringGreen]',
        '[0097_DeepSkyBlue]',
        '[0102_RoyalBlue]',
        '[Color G03]',
        '[Polished Concrete New]',
        '[Polished Concrete Old]',
        '[Roofing Tile Spanish]',
        '[Translucent Glass Blue]',
        '[Translucent Glass Safety]',
      ].sort()
    );

    // 4. Layers
    expect(model.layers.map((l) => l.name)).toEqual(['Layer0']);

    // 5. Bounding box across the (ROOT-excluded) definitions, matching
    // Python's bbox computed the same way.
    let minX = Infinity, minY = Infinity, minZ = Infinity;
    let maxX = -Infinity, maxY = -Infinity, maxZ = -Infinity;
    for (const d of model.definitions.values()) {
      for (const v of d.vertices) {
        if (v.x < minX) minX = v.x;
        if (v.x > maxX) maxX = v.x;
        if (v.y < minY) minY = v.y;
        if (v.y > maxY) maxY = v.y;
        if (v.z < minZ) minZ = v.z;
        if (v.z > maxZ) maxZ = v.z;
      }
    }
    expect(minX).toBeCloseTo(0.0, 2);
    expect(minY).toBeCloseTo(0.0, 2);
    expect(minZ).toBeCloseTo(0.0, 2);
    expect(maxX).toBeCloseTo(77.402, 2);
    expect(maxY).toBeCloseTo(51.969, 2);
    expect(maxZ).toBeCloseTo(133.071, 2);

    // 6. Scene hierarchy: 3 root-level instances (2x grada, 1x puerta),
    // matching Python's ROOT definition instance count. buildScene() is a
    // separate, opt-in step from parseSkp() - re-parses independently.
    const scene = buildScene(arrayBuffer);
    expect(scene.sceneHierarchy.children.length).toBe(3);
    const instanceDefNames = scene.sceneHierarchy.children.map((c) => c.definitionName).sort();
    expect(instanceDefNames).toEqual(['grada', 'grada', 'puerta']);

    // 7. model.root: the same 3 root-level instances are also reachable
    // straight off the light parseSkp() result (no buildScene() needed),
    // matching Python/.NET/Dart's parse() - which all expose root-level
    // placements without requiring the heavier scene bake.
    expect(model.root.instances.length).toBe(3);
    const rootRefNames = model.root.instances
      .map((i) => model.definitions.get(i.refIdx)?.name)
      .sort();
    expect(rootRefNames).toEqual(['grada', 'grada', 'puerta']);
  });
});
