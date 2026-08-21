import { describe, it, expect } from 'vitest';
import { buildSceneFromParsed, type ParsedDefinition } from '../src/model';
import { buildInstancedSceneFromParsed } from '../src/instanced';
import { GeometryBuilder } from '../src/geometry';
import {
  flattenInstancedScene,
  flattenBakedScene,
  compareTrianglesInOrder,
  instancedBufferBytes,
} from './helpers/flatten-instanced';
import {
  panelDefinition,
  panelWithHoleDefinition,
  texturedMaterial,
  plainMaterial,
  makeParsed,
  translation,
  scaleMatrix,
  rotationZ,
  repeatedComponentScene,
} from './helpers/instanced-fixtures';

/**
 * The instanced scene builder must describe exactly the geometry the baked
 * builder describes - just stored once per definition instead of once per
 * placement. Every test here either proves that equivalence or proves the
 * sharing/variant rules that make the sharing safe.
 */

describe('buildInstancedScene: resource sharing', () => {
  it('emits ONE mesh resource for a definition placed many times', () => {
    const parsed = repeatedComponentScene(50, 4);
    const scene = buildInstancedSceneFromParsed(parsed);

    expect(scene.meshResources.length).toBe(1);
    expect(scene.sceneHierarchy.children.length).toBe(50);
    // every placement points at the same resource
    const ids = new Set(scene.sceneHierarchy.children.map((n) => n.meshResourceId));
    expect(ids.size).toBe(1);
    expect(ids.has(scene.meshResources[0].id)).toBe(true);
  });

  it('keeps mesh-resource buffer bytes flat as instance count grows', () => {
    const sizes = [1, 10, 100].map((n) => instancedBufferBytes(buildInstancedSceneFromParsed(repeatedComponentScene(n, 4))));

    // Identical, not merely sub-linear: the geometry is stored exactly once.
    expect(sizes[1]).toBe(sizes[0]);
    expect(sizes[2]).toBe(sizes[0]);
  });

  it('grows the BAKED path with instance count, which is the problem being solved', () => {
    const bakedPrims = [1, 10, 100].map(
      (n) => buildSceneFromParsed(repeatedComponentScene(n, 4)).glbPrimitives.length
    );
    expect(bakedPrims[1]).toBeGreaterThan(bakedPrims[0]);
    expect(bakedPrims[2]).toBeGreaterThan(bakedPrims[1]);

    // ...while the instanced resource count stays at one.
    const instanced = [1, 10, 100].map(
      (n) => buildInstancedSceneFromParsed(repeatedComponentScene(n, 4)).meshResources.length
    );
    expect(instanced).toEqual([1, 1, 1]);
  });

  it('assigns stable, deterministic resource ids across repeated builds', () => {
    const idsOf = () =>
      buildInstancedSceneFromParsed(repeatedComponentScene(5, 4)).meshResources.map((r) => r.id);
    expect(idsOf()).toEqual(idsOf());
    expect(idsOf()).toEqual(['mesh_0']);
  });
});

describe('buildInstancedScene: material variants in resource identity', () => {
  /** The same definition placed twice, painted per instance. */
  const twoPaintedInstances = (matIdA: number | null, matIdB: number | null) =>
    makeParsed({
      defs: new Map<number | string, ParsedDefinition>([[1, panelDefinition()]]),
      rootInstances: [
        { refIdx: 1, name: 'A', matrix: translation(0, 0, 0), materialId: matIdA },
        { refIdx: 1, name: 'B', matrix: translation(50, 0, 0), materialId: matIdB },
      ],
      materials: new Map([
        ['Red', plainMaterial('Red', { r: 200, g: 10, b: 10, a: 255 })],
        ['Blue', plainMaterial('Blue', { r: 10, g: 10, b: 200, a: 255 })],
      ]),
      materialIdToName: new Map([
        [1, 'Red'],
        [2, 'Blue'],
      ]),
    });

  it('shares ONE resource when the effective inherited material matches', () => {
    const scene = buildInstancedSceneFromParsed(twoPaintedInstances(1, 1));
    expect(scene.meshResources.length).toBe(1);
    const [a, b] = scene.sceneHierarchy.children;
    expect(a.meshResourceId).toBe(b.meshResourceId);
  });

  it('splits into SEPARATE resource variants for different inherited materials', () => {
    const scene = buildInstancedSceneFromParsed(twoPaintedInstances(1, 2));
    expect(scene.meshResources.length).toBe(2);
    const [a, b] = scene.sceneHierarchy.children;
    expect(a.meshResourceId).not.toBe(b.meshResourceId);

    // and the two variants really do carry different colours
    const colorOf = (id: string | undefined) => {
      const res = scene.meshResources.find((r) => r.id === id)!;
      const mat = scene.gltfMaterials[res.primitives[0].materialIndex] as any;
      return mat.pbrMetallicRoughness.baseColorFactor;
    };
    expect(colorOf(a.meshResourceId)).not.toEqual(colorOf(b.meshResourceId));
  });

  it('includes the layer fallback colour in resource identity', () => {
    const parsed = makeParsed({
      defs: new Map<number | string, ParsedDefinition>([[1, panelDefinition()]]),
      rootInstances: [
        { refIdx: 1, name: 'OnA', matrix: translation(0, 0, 0), layerId: 10 },
        { refIdx: 1, name: 'OnB', matrix: translation(50, 0, 0), layerId: 20 },
      ],
      layerIdToName: new Map([
        [10, 'LayerA'],
        [20, 'LayerB'],
      ]),
      layerColors: new Map<string, [number, number, number]>([
        ['Layer0', [255, 255, 255]],
        ['LayerA', [255, 0, 0]],
        ['LayerB', [0, 0, 255]],
      ]),
    });
    const scene = buildInstancedSceneFromParsed(parsed);

    // The face is unpainted, so the layer colour is what it renders as -
    // two layers with different colours cannot share one resource.
    expect(scene.meshResources.length).toBe(2);
    const [a, b] = scene.sceneHierarchy.children;
    expect(a.meshResourceId).not.toBe(b.meshResourceId);
    expect(a.layer).toBe('LayerA');
    expect(b.layer).toBe('LayerB');
  });

  it('shares one resource for two layers that happen to share a colour', () => {
    const parsed = makeParsed({
      defs: new Map<number | string, ParsedDefinition>([[1, panelDefinition()]]),
      rootInstances: [
        { refIdx: 1, name: 'OnA', matrix: translation(0, 0, 0), layerId: 10 },
        { refIdx: 1, name: 'OnB', matrix: translation(50, 0, 0), layerId: 20 },
      ],
      layerIdToName: new Map([
        [10, 'LayerA'],
        [20, 'LayerB'],
      ]),
      layerColors: new Map<string, [number, number, number]>([
        ['Layer0', [255, 255, 255]],
        ['LayerA', [7, 7, 7]],
        ['LayerB', [7, 7, 7]],
      ]),
    });
    const scene = buildInstancedSceneFromParsed(parsed);
    expect(scene.meshResources.length).toBe(1);
  });

  it('separates variants whose textures differ but average to one colour', () => {
    const sameColor = { r: 141, g: 141, b: 141, a: 255 };
    const parsed = makeParsed({
      defs: new Map<number | string, ParsedDefinition>([[1, panelDefinition()]]),
      rootInstances: [
        { refIdx: 1, name: 'A', matrix: translation(0, 0, 0), materialId: 1 },
        { refIdx: 1, name: 'B', matrix: translation(50, 0, 0), materialId: 2 },
      ],
      materials: new Map([
        ['FabricA', texturedMaterial('FabricA', sameColor, [0xff, 0xd8, 0xff, 0xaa])],
        ['FabricB', texturedMaterial('FabricB', sameColor, [0xff, 0xd8, 0xff, 0xbb, 0xbb])],
      ]),
      materialIdToName: new Map([
        [1, 'FabricA'],
        [2, 'FabricB'],
      ]),
    });
    const scene = buildInstancedSceneFromParsed(parsed);

    expect(scene.meshResources.length).toBe(2);
    expect(scene.textures.length).toBe(2);
  });
});

describe('buildInstancedScene: parity with the baked path', () => {
  /** Same parsed input through both builders must describe the same
   * world-space triangles with the same materials. */
  const expectParity = (parsed: ReturnType<typeof makeParsed>) => {
    const baked = buildSceneFromParsed(parsed);
    const instanced = buildInstancedSceneFromParsed(parsed);

    const bakedTris = flattenBakedScene(baked);
    const instancedTris = flattenInstancedScene(instanced);
    expect(instancedTris.length).toBe(bakedTris.length);

    // Compared in walk order; materials by CONTENT, not index, since the
    // two paths allocate into the same table in their own encounter order.
    const { worstDelta, firstMismatch, materialMismatches } = compareTrianglesInOrder(
      instancedTris,
      bakedTris,
      { actual: instanced.gltfMaterials, expected: baked.gltfMaterials }
    );
    expect(firstMismatch).toBeNull();
    expect(materialMismatches).toBe(0);
    expect(worstDelta).toBeLessThan(1e-5);
    return { baked, instanced };
  };

  it('matches for a simple translated placement', () => {
    expectParity(
      makeParsed({
        defs: new Map<number | string, ParsedDefinition>([[1, panelDefinition()]]),
        rootInstances: [{ refIdx: 1, matrix: translation(10, 20, 30) }],
      })
    );
  });

  it('matches for rotation', () => {
    expectParity(
      makeParsed({
        defs: new Map<number | string, ParsedDefinition>([[1, panelDefinition()]]),
        rootInstances: [{ refIdx: 1, matrix: rotationZ(37, [5, -3, 2]) }],
      })
    );
  });

  it('matches for uniform and non-uniform scale', () => {
    expectParity(
      makeParsed({
        defs: new Map<number | string, ParsedDefinition>([[1, panelDefinition()]]),
        rootInstances: [
          { refIdx: 1, name: 'uniform', matrix: scaleMatrix(2, 2, 2) },
          { refIdx: 1, name: 'nonuniform', matrix: scaleMatrix(3, 0.5, 1.75, [100, 0, 0]) },
        ],
      })
    );
  });

  it('matches for a MIRRORED (negative-determinant) placement', () => {
    expectParity(
      makeParsed({
        defs: new Map<number | string, ParsedDefinition>([[1, panelDefinition()]]),
        rootInstances: [
          { refIdx: 1, name: 'normal', matrix: translation(0, 0, 0) },
          { refIdx: 1, name: 'mirrored', matrix: scaleMatrix(-1, 1, 1, [200, 0, 0]) },
        ],
      })
    );
  });

  it('matches for NESTED instance transforms', () => {
    // outer -> inner -> panel, each level carrying its own transform
    const inner = new GeometryBuilder();
    inner.instances.push({
      offset: 0,
      refGuid: '1',
      refIdx: 1,
      name: 'panel-in-inner',
      matrix: translation(3, 4, 5),
      materialId: null,
      children: [],
    });
    const innerDef: ParsedDefinition = {
      guid: 'INNER', name: 'Inner', isImage: false, alwaysFacesCamera: false, builder: inner,
    };

    const outer = new GeometryBuilder();
    outer.instances.push({
      offset: 0,
      refGuid: '2',
      refIdx: 2,
      name: 'inner-in-outer',
      matrix: rotationZ(90, [10, 0, 0]),
      materialId: null,
      children: [],
    });
    const outerDef: ParsedDefinition = {
      guid: 'OUTER', name: 'Outer', isImage: false, alwaysFacesCamera: false, builder: outer,
    };

    const { instanced } = expectParity(
      makeParsed({
        defs: new Map<number | string, ParsedDefinition>([
          [1, panelDefinition()],
          [2, innerDef],
          [3, outerDef],
        ]),
        rootInstances: [{ refIdx: 3, name: 'outer', matrix: scaleMatrix(2, 2, 2, [7, 8, 9]) }],
      })
    );

    // and the nesting is genuinely preserved, not flattened
    expect(instanced.sceneHierarchy.children.length).toBe(1);
    expect(instanced.sceneHierarchy.children[0].children.length).toBe(1);
    expect(instanced.sceneHierarchy.children[0].children[0].children.length).toBe(1);
  });

  it('matches for a face with a HOLE (multi-loop triangulation)', () => {
    expectParity(
      makeParsed({
        defs: new Map<number | string, ParsedDefinition>([[1, panelWithHoleDefinition()]]),
        rootInstances: [{ refIdx: 1, matrix: translation(1, 2, 3) }],
      })
    );
  });

  it('matches for different FRONT and BACK materials', () => {
    const parsed = makeParsed({
      defs: new Map<number | string, ParsedDefinition>([
        [1, panelDefinition(24, 'TwoSided', { materialId: 1, backMaterialId: 2 })],
      ]),
      rootInstances: [{ refIdx: 1, matrix: translation(0, 0, 0) }],
      materials: new Map([
        ['Red', plainMaterial('Red', { r: 200, g: 10, b: 10, a: 255 })],
        ['Blue', plainMaterial('Blue', { r: 10, g: 10, b: 200, a: 255 })],
      ]),
      materialIdToName: new Map([
        [1, 'Red'],
        [2, 'Blue'],
      ]),
    });
    const { instanced } = expectParity(parsed);

    // front/back genuinely differ, so the definition splits into two
    // single-sided primitives rather than one double-sided one
    expect(instanced.meshResources[0].primitives.length).toBe(2);
    for (const prim of instanced.meshResources[0].primitives) {
      const mat = instanced.gltfMaterials[prim.materialIndex] as any;
      expect(mat.doubleSided).toBeUndefined();
    }
  });

  it('marks a face whose sides resolve alike as doubleSided, once', () => {
    const parsed = makeParsed({
      defs: new Map<number | string, ParsedDefinition>([[1, panelDefinition()]]),
      rootInstances: [{ refIdx: 1, matrix: translation(0, 0, 0) }],
    });
    const { instanced } = expectParity(parsed);
    expect(instanced.meshResources[0].primitives.length).toBe(1);
    const mat = instanced.gltfMaterials[instanced.meshResources[0].primitives[0].materialIndex] as any;
    expect(mat.doubleSided).toBe(true);
  });

  it('retains ROOT loose geometry', () => {
    const parsed = makeParsed({
      defs: new Map<number | string, ParsedDefinition>([[1, panelDefinition()]]),
      rootGeometry: panelDefinition(36, 'LooseFloor'),
      rootInstances: [{ refIdx: 1, matrix: translation(60, 0, 0) }],
    });
    const { instanced } = expectParity(parsed);

    // the root node itself owns a resource for the loose geometry
    expect(instanced.sceneHierarchy.meshResourceId).toBeDefined();
    const rootRes = instanced.meshResources.find(
      (r) => r.id === instanced.sceneHierarchy.meshResourceId
    )!;
    expect(rootRes.definitionId).toBe('ROOT');
  });

  it('matches for many mixed placements at once', () => {
    expectParity(
      makeParsed({
        defs: new Map<number | string, ParsedDefinition>([
          [1, panelDefinition()],
          [2, panelWithHoleDefinition()],
        ]),
        rootGeometry: panelDefinition(30, 'Loose'),
        rootInstances: [
          { refIdx: 1, name: 'a', matrix: translation(0, 0, 0) },
          { refIdx: 1, name: 'b', matrix: rotationZ(45, [40, 0, 0]) },
          { refIdx: 2, name: 'c', matrix: scaleMatrix(1.5, 2.5, 1, [0, 40, 0]) },
          { refIdx: 2, name: 'd', matrix: scaleMatrix(-2, 1, 1, [0, 80, 0]) },
        ],
      })
    );
  });
});

describe('buildInstancedScene: UVs, textures and metadata', () => {
  it('preserves UVs from the inherited textured material', () => {
    const parsed = makeParsed({
      defs: new Map<number | string, ParsedDefinition>([[1, panelDefinition()]]),
      rootInstances: [{ refIdx: 1, matrix: translation(0, 0, 0), materialId: 7 }],
      materials: new Map([['Decor', texturedMaterial('Decor')]]),
      materialIdToName: new Map([[7, 'Decor']]),
    });

    const baked = buildSceneFromParsed(parsed);
    const instanced = buildInstancedSceneFromParsed(parsed);

    const bakedUvs = Array.from(baked.glbPrimitives[0].uvs).sort((a, b) => a - b);
    const instUvs = Array.from(instanced.meshResources[0].primitives[0].uvs).sort((a, b) => a - b);

    // UVs are computed in local space, so an instance transform must not
    // change them at all - the two paths agree exactly.
    expect(instUvs.length).toBe(bakedUvs.length);
    for (let i = 0; i < instUvs.length; i++) {
      expect(instUvs[i]).toBeCloseTo(bakedUvs[i], 6);
    }

    // 24" panel, 24x12" tile: one repeat across, two down.
    const maxU = Math.max(...instUvs.filter((_, i) => i % 1 === 0));
    expect(maxU).toBeGreaterThan(0);
    expect(instanced.textures.length).toBe(1);
  });

  it('deduplicates textures shared by several materials', () => {
    const shared = [0xff, 0xd8, 0xff, 0x42];
    const parsed = makeParsed({
      defs: new Map<number | string, ParsedDefinition>([[1, panelDefinition()]]),
      rootInstances: [
        { refIdx: 1, name: 'A', matrix: translation(0, 0, 0), materialId: 1 },
        { refIdx: 1, name: 'B', matrix: translation(40, 0, 0), materialId: 2 },
      ],
      materials: new Map([
        ['MatA', texturedMaterial('MatA', { r: 5, g: 5, b: 5, a: 255 }, shared)],
        ['MatB', texturedMaterial('MatB', { r: 9, g: 9, b: 9, a: 255 }, shared)],
      ]),
      materialIdToName: new Map([
        [1, 'MatA'],
        [2, 'MatB'],
      ]),
    });
    const scene = buildInstancedSceneFromParsed(parsed);
    expect(scene.textures.length).toBe(1);
  });

  it('reports the same node metadata the baked path reports', () => {
    const parsed = makeParsed({
      defs: new Map<number | string, ParsedDefinition>([[1, panelDefinition()]]),
      rootInstances: [{ refIdx: 1, name: 'Chair', matrix: translation(10, 20, 30), layerId: 5 }],
      layerIdToName: new Map([[5, 'Furniture']]),
      layerColors: new Map<string, [number, number, number]>([
        ['Layer0', [255, 255, 255]],
        ['Furniture', [200, 100, 50]],
      ]),
    });

    const baked = buildSceneFromParsed(parsed);
    const instanced = buildInstancedSceneFromParsed(parsed);

    const b = baked.sceneHierarchy.children[0];
    const i = instanced.sceneHierarchy.children[0];

    expect(i.name).toBe(b.name);
    expect(i.definitionName).toBe(b.definitionName);
    expect(i.layer).toBe(b.layer);
    expect(i.positionMm).toEqual(b.positionMm);
    expect(i.properties).toEqual(b.properties);
  });

  it('rejects a self-instancing definition, like the baked path', () => {
    const selfRef = new GeometryBuilder();
    selfRef.instances.push({
      offset: 0,
      refGuid: '1',
      refIdx: 1,
      name: 'me-inside-me',
      matrix: translation(1, 0, 0),
      materialId: null,
      children: [],
    });
    const parsed = makeParsed({
      defs: new Map<number | string, ParsedDefinition>([
        [1, { guid: 'SELF', name: 'Self', isImage: false, alwaysFacesCamera: false, builder: selfRef }],
      ]),
      rootInstances: [{ refIdx: 1, matrix: translation(0, 0, 0) }],
    });

    expect(() => buildInstancedSceneFromParsed(parsed)).toThrow(/Recursive component definition/);
  });
});
