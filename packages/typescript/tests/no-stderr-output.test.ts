import { describe, it, expect, vi } from 'vitest';
import * as flatbuffers from 'flatbuffers';
import {
  toFragments,
  fromFragments,
  type InstancedScene,
  type InstancedNode,
  type InstancedMeshResource,
  type LocalPrimitive,
} from '../src/index';
import { Model, RepresentationClass } from '../src/_fragments_fb/index';

const IDENTITY = [
  1.0, 0.0, 0.0, 0.0,
  0.0, 1.0, 0.0, 0.0,
  0.0, 0.0, 1.0, 0.0,
  0.0, 0.0, 0.0, 1.0,
];

function makeCircleExtrusionFrag(): Uint8Array {
  const prim: LocalPrimitive = {
    positions: new Float32Array([0, 0, 0, 1, 0, 0, 1, 1, 0, 0, 1, 0]),
    normals: new Float32Array(12),
    uvs: new Float32Array(8),
    indices: new Uint32Array([0, 1, 2, 0, 2, 3]),
    materialIndex: 0,
  };
  const resource: InstancedMeshResource = {
    id: 'mesh_0',
    definitionId: 1,
    definitionName: 'Box',
    variantKey: '1|255,255,255',
    primitives: [prim],
  };
  const node: InstancedNode = {
    name: 'Box1',
    nameIsGenerated: false,
    definitionName: 'Box',
    layer: 'Layer0',
    matrix: IDENTITY,
    positionMm: [0, 0, 0],
    properties: {},
    guid: '',
    meshResourceId: 'mesh_0',
    children: [],
  };
  const root: InstancedNode = {
    name: 'ROOT',
    nameIsGenerated: false,
    definitionName: 'ROOT_MODEL',
    layer: 'Layer0',
    matrix: IDENTITY,
    positionMm: [0, 0, 0],
    properties: {},
    guid: '',
    children: [node],
  };
  const scene: InstancedScene = {
    bounds: null,
    sceneHierarchy: root,
    meshResources: [resource],
    gltfMaterials: [{ pbrMetallicRoughness: { baseColorFactor: [1.0, 1.0, 1.0, 1.0] } }],
    textures: [],
    layerHidden: {},
    unreadRootEntities: 0,
  };
  const raw = new Uint8Array(toFragments(scene, { raw: true }));
  const model = Model.getRootAsModel(new flatbuffers.ByteBuffer(raw));
  const rep = model.meshes()!.representations(0)!;
  raw[rep.bb_pos + 28] = RepresentationClass.CIRCLE_EXTRUSION;
  return raw;
}

describe('Silent by default - no stdout/stderr/console writes (#556)', () => {
  it('writes nothing to console or stdio when fromFragments skips an unsupported representation without logging configured', () => {
    const patched = makeCircleExtrusionFrag();
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    const logSpy = vi.spyOn(console, 'log').mockImplementation(() => {});
    let stdoutCalls = 0;
    let stderrCalls = 0;
    const origOut = process.stdout.write.bind(process.stdout);
    const origErr = process.stderr.write.bind(process.stderr);
    const outSpy = vi.spyOn(process.stdout, 'write').mockImplementation(((...args: unknown[]) => {
      stdoutCalls += 1;
      return (origOut as (...a: unknown[]) => boolean)(...args);
    }) as typeof process.stdout.write);
    const errSpy = vi.spyOn(process.stderr, 'write').mockImplementation(((...args: unknown[]) => {
      stderrCalls += 1;
      return (origErr as (...a: unknown[]) => boolean)(...args);
    }) as typeof process.stderr.write);

    try {
      const back = fromFragments(patched);
      expect(back.meshResources.every((r) => r.primitives.length === 0)).toBe(true);
      expect(warnSpy).not.toHaveBeenCalled();
      expect(errorSpy).not.toHaveBeenCalled();
      expect(logSpy).not.toHaveBeenCalled();
      expect(stdoutCalls).toBe(0);
      expect(stderrCalls).toBe(0);
    } finally {
      warnSpy.mockRestore();
      errorSpy.mockRestore();
      logSpy.mockRestore();
      outSpy.mockRestore();
      errSpy.mockRestore();
    }
  });

  it('delivers unsupported representation warnings through ParseOptions.onLog', () => {
    const patched = makeCircleExtrusionFrag();
    const seen: Array<[string, string]> = [];
    const back = fromFragments(patched, {
      onLog: (level, msg) => seen.push([level, msg]),
    });
    expect(back.meshResources.every((r) => r.primitives.length === 0)).toBe(true);
    expect(seen.some(([lvl, msg]) => lvl === 'warn' && msg.includes('RepresentationClass'))).toBe(true);
  });
});
