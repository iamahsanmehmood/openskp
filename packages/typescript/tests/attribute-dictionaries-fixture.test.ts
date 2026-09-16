import { describe, it, expect } from 'vitest';
import * as fs from 'fs';
import * as path from 'path';
import { buildScene, buildInstancedScene } from '../src/index';
import type { InstanceNode } from '../src/model';
import type { InstancedNode } from '../src/instanced';

/**
 * Real-fixture coverage for InstanceNode/InstancedNode/MeshMetadata's
 * `attributeDictionaries` field (openskp#285's "multiple dictionaries per
 * entity" reader side). Untitled.skp is a genuine SteelFramer-authored file
 * whose "W1" instance carries a "steelframer-dict" dictionary - NOT
 * SketchUp's own "dynamic_attributes" - cross-checked against Python's own
 * test_untitled_skp/test_untitled_skp_attribute_dictionaries_reach_glb_and_json_export
 * ground truth (packages/python/tests/test_parser.py), also mirrored in the
 * .NET port's DynamicPropertiesTests.cs.
 */

const fixture = (name: string) => path.join(__dirname, 'fixtures', name);

const readFixture = (name: string) => {
  const buf = fs.readFileSync(fixture(name));
  return buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength);
};

function findByName(node: InstanceNode, name: string): InstanceNode | null {
  if (node.name === name) return node;
  for (const child of node.children) {
    const found = findByName(child, name);
    if (found) return found;
  }
  return null;
}

function findInstancedByName(node: InstancedNode, name: string): InstancedNode | null {
  if (node.name === name) return node;
  for (const child of node.children) {
    const found = findInstancedByName(child, name);
    if (found) return found;
  }
  return null;
}

describe('attributeDictionaries - real fixture (openskp#285)', () => {
  it('Scene: exposes the third-party dictionary by its own name on the baked tree', () => {
    const scene = buildScene(readFixture('Untitled.skp'));
    const w1 = findByName(scene.sceneHierarchy, 'W1');

    expect(w1).not.toBeNull();
    // NOTE: unlike Python (whose `properties` is scoped to only the
    // "dynamic_attributes" dict), TypeScript's `properties` is populated via
    // the pre-existing, deliberately flatten-everything
    // extractDynamicProperties - a real, established divergence predating
    // this change. attributeDictionaries (below) is the actually-correct,
    // dictionary-scoped way to reach steelframer-dict's own data.
    expect(w1!.attributeDictionaries['steelframer-dict']).toBeDefined();
    expect(w1!.attributeDictionaries['steelframer-dict'].generator).toBe('SteelFramer::Engine::PanelGenerator');
    expect(w1!.attributeDictionaries['steelframer-dict'].profile).toBe('362S200-43');
  });

  it('InstancedScene: exposes the third-party dictionary by its own name on the instanced tree', () => {
    const instanced = buildInstancedScene(readFixture('Untitled.skp'));
    const w1 = findInstancedByName(instanced.sceneHierarchy, 'W1');

    expect(w1).not.toBeNull();
    expect(w1!.attributeDictionaries['steelframer-dict']).toBeDefined();
    expect(w1!.attributeDictionaries['steelframer-dict'].generator).toBe('SteelFramer::Engine::PanelGenerator');
    expect(w1!.attributeDictionaries['steelframer-dict'].profile).toBe('362S200-43');
  });

  it('meshIndex: MeshMetadata also carries a non-empty steelframer-dict somewhere in the tree', () => {
    // Several distinct instances each carry their own "steelframer-dict"
    // (with different keys per part type); W1 itself is a geometry-less
    // organizational wrapper (no mesh sits at its own path), so - matching
    // Python's own equally loose check - this only confirms SOME mesh
    // carries a non-empty "steelframer-dict", not necessarily W1's own.
    const scene = buildScene(readFixture('Untitled.skp'));
    const meshWithDict = Object.values(scene.meshIndex).find(
      (m) => Object.keys(m.attributeDictionaries['steelframer-dict'] ?? {}).length > 0
    );

    expect(meshWithDict).toBeDefined();
  });
});
