import { describe, it, expect } from 'vitest';
import * as fs from 'fs';
import * as path from 'path';
import { buildScene, buildInstancedScene } from '../src/index';
import {
  flattenInstancedScene,
  flattenBakedScene,
  compareTrianglesInOrder,
  instancedBufferBytes,
  bakedBufferBytes,
} from './helpers/flatten-instanced';

/**
 * The strongest correctness evidence available: run BOTH builders over the
 * repository's real .skp fixtures and require that flattening the instanced
 * result reproduces the baked result's world-space triangles exactly.
 *
 * This covers, on genuine files, everything the synthetic tests cover
 * piecewise - nested groups/components, instance-painted materials, layers,
 * front/back materials, textures, holes, mirrored transforms - because
 * whatever those files happen to contain has to come out the same either
 * way.
 */

const fixture = (name: string) => path.join(__dirname, 'fixtures', name);

const readFixture = (name: string) => {
  const buf = fs.readFileSync(fixture(name));
  return buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength);
};

// One modern VFF container plus two legacy MFC ones, so both parse paths
// feed the instanced builder in CI.
const FIXTURES = ['SU_File.skp', 'Untitled.skp', 'capilla_quiroz_v17.skp', 'gondola_v20.skp', 'single_material_v17.skp'];

describe('instanced vs baked parity on real fixtures', () => {
  for (const name of FIXTURES) {
    it(`reproduces buildScene's world-space triangles for ${name}`, () => {
      const ab = readFixture(name);

      const baked = buildScene(ab);
      const instanced = buildInstancedScene(ab);

      const bakedTris = flattenBakedScene(baked);
      const instancedTris = flattenInstancedScene(instanced);

      expect(instancedTris.length).toBe(bakedTris.length);

      // Compared in walk order, with materials matched by CONTENT rather
      // than index: both paths build the same material table but allocate
      // into it in their own encounter order.
      const { worstDelta, firstMismatch, materialMismatches } = compareTrianglesInOrder(
        instancedTris,
        bakedTris,
        { actual: instanced.gltfMaterials, expected: baked.gltfMaterials }
      );

      expect(firstMismatch).toBeNull();
      expect(materialMismatches).toBe(0);
      // Float32 round-off only. Measured worst case across these fixtures
      // is ~1.6e-6 m (1.6 micrometres, on a ~47 m model) - consistent with
      // one float32 ulp at that magnitude, not with a transform error.
      expect(worstDelta).toBeLessThan(1e-5);
    });
  }

  it('never stores more geometry than the baked path', () => {
    for (const name of FIXTURES) {
      const ab = readFixture(name);
      const bakedBytes = bakedBufferBytes(buildScene(ab));
      const instBytes = instancedBufferBytes(buildInstancedScene(ab));
      // Equal when nothing repeats; strictly smaller once anything does.
      expect(instBytes).toBeLessThanOrEqual(bakedBytes);
    }
  });

  it('resolves the same layers and dynamic properties per node', () => {
    for (const name of FIXTURES) {
      const ab = readFixture(name);
      const baked = buildScene(ab);
      const instanced = buildInstancedScene(ab);

      // Walk both trees in lockstep: the instance walk order is identical,
      // so a divergence in metadata shows up as a mismatch here.
      const walk = (b: any, i: any) => {
        expect(i.name).toBe(b.name);
        expect(i.definitionName).toBe(b.definitionName);
        expect(i.layer).toBe(b.layer);
        expect(i.positionMm).toEqual(b.positionMm);
        expect(i.properties).toEqual(b.properties);
        expect(i.children.length).toBe(b.children.length);
        for (let k = 0; k < b.children.length; k++) {
          walk(b.children[k], i.children[k]);
        }
      };
      walk(baked.sceneHierarchy, instanced.sceneHierarchy);
    }
  });
});
