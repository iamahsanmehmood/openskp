import { describe, it, expect } from 'vitest';
import * as fs from 'fs';
import * as path from 'path';
import { parseSkp, buildScene, buildInstancedScene, isDrawableEdge } from '../src/index';
import { buildSceneFromParsed } from '../src/model';
import { buildInstancedSceneFromParsed } from '../src/instanced';
import { GeometryBuilder, type ParsedDefinition } from '../src/geometry';
import { makeParsed, translation } from './helpers/instanced-fixtures';

/**
 * SketchUp does not draw edges flagged soft/smooth/hidden, nor faces
 * flagged hidden. The flags are parsed; acting on them is opt-in.
 *
 * Two separate mechanisms, deliberately: `isDrawableEdge` for edge
 * consumers reading parseSkp() output (where the real saving is, on curved
 * models), and `respectEdgeVisibility` on the scene builders for hidden
 * FACES (which is all that can reach their output, since neither builder
 * emits edges).
 */

const readFixture = (name: string) => {
  const buf = fs.readFileSync(path.join(__dirname, 'fixtures', name));
  return buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength);
};

describe('isDrawableEdge', () => {
  it('accepts a plain edge and rejects each hidden kind', () => {
    const base = { soft: false, smooth: false, hidden: false };
    expect(isDrawableEdge(base)).toBe(true);
    expect(isDrawableEdge({ ...base, soft: true })).toBe(false);
    expect(isDrawableEdge({ ...base, smooth: true })).toBe(false);
    expect(isDrawableEdge({ ...base, hidden: true })).toBe(false);
  });

  it('filters real fixture edges without touching the parsed model', () => {
    const model = parseSkp(readFixture('gondola_v20.skp'));
    const all: any[] = [];
    const scan = (d: any) => all.push(...d.edges);
    scan(model.root);
    for (const [, d] of model.definitions) scan(d);

    const drawable = all.filter(isDrawableEdge);

    // gondola is a curved-surface model: most of its edges are smoothing
    // seams SketchUp never shows.
    expect(all.length).toBeGreaterThan(0);
    expect(drawable.length).toBeLessThan(all.length);
    expect(drawable.length).toBe(all.filter((e) => !e.soft && !e.smooth && !e.hidden).length);

    // the model itself is untouched - this is a read-only helper
    expect(all.length).toBe(
      [model.root, ...model.definitions.values()].reduce((n, d) => n + d.edges.length, 0)
    );
  });

  it('is a no-op on a model whose edges are nearly all drawable', () => {
    const model = parseSkp(readFixture('Untitled.skp'));
    const all: any[] = [];
    const scan = (d: any) => all.push(...d.edges);
    scan(model.root);
    for (const [, d] of model.definitions) scan(d);

    // ~99.8% drawable here - the saving is model-dependent, not universal.
    const drawable = all.filter(isDrawableEdge);
    expect(drawable.length / all.length).toBeGreaterThan(0.9);
  });
});

/** One square face, optionally flagged hidden. */
function faceDefinition(hidden: boolean, name = 'Panel'): ParsedDefinition {
  const builder = new GeometryBuilder();
  builder.vertices.set(1, [0, 0, 0]);
  builder.vertices.set(2, [24, 0, 0]);
  builder.vertices.set(3, [24, 24, 0]);
  builder.vertices.set(4, [0, 24, 0]);
  builder.edges.set(11, [1, 2]);
  builder.edges.set(12, [2, 3]);
  builder.edges.set(13, [3, 4]);
  builder.edges.set(14, [4, 1]);
  builder.faces.set(21, {
    loops: [
      [
        { edgeId: 11, orientation: 0 },
        { edgeId: 12, orientation: 0 },
        { edgeId: 13, orientation: 0 },
        { edgeId: 14, orientation: 0 },
      ],
    ],
    normal: [0, 0, 1],
    materialId: null,
    backMaterialId: null,
    hidden,
  });
  return { guid: name.toUpperCase(), name, isImage: false, alwaysFacesCamera: false, builder };
}

/** A visible face plus a hidden one, as two placed definitions. */
const mixedScene = () =>
  makeParsed({
    defs: new Map<number | string, ParsedDefinition>([
      [1, faceDefinition(false, 'Visible')],
      [2, faceDefinition(true, 'Hidden')],
    ]),
    rootInstances: [
      { refIdx: 1, name: 'visible', matrix: translation(0, 0, 0) },
      { refIdx: 2, name: 'hidden', matrix: translation(50, 0, 0) },
    ],
  });

describe('respectEdgeVisibility on the scene builders', () => {
  it('is OFF by default: hidden faces are still baked', () => {
    const scene = buildSceneFromParsed(mixedScene());
    expect(scene.glbPrimitives.length).toBe(2);
  });

  it('drops hidden faces when enabled', () => {
    const scene = buildSceneFromParsed(mixedScene(), { respectEdgeVisibility: true });
    expect(scene.glbPrimitives.length).toBe(1);
    // the surviving primitive is the visible definition's
    expect(scene.glbPrimitives[0].geomName).toContain('visible');
  });

  it('applies identically on the instanced path', () => {
    const off = buildInstancedSceneFromParsed(mixedScene());
    const on = buildInstancedSceneFromParsed(mixedScene(), { respectEdgeVisibility: true });

    expect(off.meshResources.length).toBe(2);
    expect(on.meshResources.length).toBe(1);
    expect(on.meshResources[0].definitionName).toBe('Visible');

    // the hidden definition's NODE survives - only its geometry is gone,
    // so hierarchy and metadata stay intact
    expect(on.sceneHierarchy.children.length).toBe(2);
    expect(on.sceneHierarchy.children[1].meshResourceId).toBeUndefined();
  });

  it('keeps both scene builders in agreement when enabled', () => {
    const baked = buildSceneFromParsed(mixedScene(), { respectEdgeVisibility: true });
    const instanced = buildInstancedSceneFromParsed(mixedScene(), { respectEdgeVisibility: true });

    let bakedTris = 0;
    for (const p of baked.glbPrimitives) bakedTris += p.indices.length / 3;
    let instTris = 0;
    for (const r of instanced.meshResources) {
      for (const p of r.primitives) instTris += p.indices.length / 3;
    }
    expect(instTris).toBe(bakedTris);
  });

  it('changes nothing on real fixtures, which carry no hidden faces', () => {
    // Documents the honest scope: the option is correct but rarely bites,
    // because Face.hidden is rare in practice. If a future fixture DOES
    // carry hidden faces, this test will notice.
    for (const name of ['gondola_v20.skp', 'Untitled.skp', 'capilla_quiroz_v17.skp']) {
      const ab = readFixture(name);
      const off = buildScene(ab);
      const on = buildScene(ab, { respectEdgeVisibility: true });
      expect(on.glbPrimitives.length).toBe(off.glbPrimitives.length);

      const offInst = buildInstancedScene(ab);
      const onInst = buildInstancedScene(ab, { respectEdgeVisibility: true });
      expect(onInst.meshResources.length).toBe(offInst.meshResources.length);
    }
  });
});
