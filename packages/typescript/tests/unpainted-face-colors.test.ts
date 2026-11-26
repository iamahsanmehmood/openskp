import { describe, it, expect } from 'vitest';
import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';
import {
  SkpFile,
  create,
  toGLB,
  toInstancedGLB,
  toSTLAscii,
  toSTLBinary,
  toOBJ,
  toPLYAscii,
  toDXF,
  toIFC,
  toFragments,
  fromFragments,
} from '../src/index';

type Rgb = [number, number, number];
type Tri = [[number, number, number], [number, number, number], [number, number, number]];

const FRONT: Rgb = [11, 122, 233];
const BACK: Rgb = [201, 102, 3];
const VERSIONS = ['v4', 'v5', 'v6', '2016', '2017', '2018', '2019', '2020', '2021'] as const;

function fixturePath(name: string): string {
  return path.join(__dirname, 'fixtures', name);
}

function skpFromBuilder(builder: ReturnType<typeof create>): SkpFile {
  const bytes = builder.toBytes();
  const ab = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer;
  return SkpFile.fromBuffer(ab);
}

function rgbOf(built: { gltfMaterials: unknown[] }, materialIndex: number): Rgb {
  const mat = built.gltfMaterials[materialIndex] as any;
  const factor: number[] = mat.pbrMetallicRoughness.baseColorFactor;
  return [Math.round(factor[0] * 255), Math.round(factor[1] * 255), Math.round(factor[2] * 255)];
}

function doubleSidedOf(built: { gltfMaterials: unknown[] }, materialIndex: number): boolean {
  const mat = built.gltfMaterials[materialIndex] as any;
  return Boolean(mat?.doubleSided);
}

function corners(prim: { positions: Float32Array; indices: Uint32Array }): Tri[] {
  const pts: [number, number, number][] = [];
  for (let i = 0; i < prim.positions.length; i += 3) {
    pts.push([
      Number(prim.positions[i].toFixed(5)),
      Number(prim.positions[i + 1].toFixed(5)),
      Number(prim.positions[i + 2].toFixed(5)),
    ]);
  }
  const out: Tri[] = [];
  for (let t = 0; t < prim.indices.length; t += 3) {
    out.push([pts[prim.indices[t]], pts[prim.indices[t + 1]], pts[prim.indices[t + 2]]]);
  }
  return out;
}

function ptEq(a: [number, number, number], b: [number, number, number]): boolean {
  return a[0] === b[0] && a[1] === b[1] && a[2] === b[2];
}

function sameVertexSet(a: Tri, b: Tri): boolean {
  return a.every((pa) => b.some((pb) => ptEq(pa, pb))) && b.every((pb) => a.some((pa) => ptEq(pa, pb)));
}

function sameWinding(a: Tri, b: Tri): boolean {
  const rots: Tri[] = [
    [a[0], a[1], a[2]],
    [a[1], a[2], a[0]],
    [a[2], a[0], a[1]],
  ];
  return rots.some((r) => ptEq(r[0], b[0]) && ptEq(r[1], b[1]) && ptEq(r[2], b[2]));
}

function builtPairs(skp: SkpFile, options?: Parameters<SkpFile['buildScene']>[0]) {
  const baked = skp.buildScene(options);
  const instanced = skp.buildInstancedScene(options);
  return [
    [baked, baked.glbPrimitives] as const,
    [instanced, instanced.meshResources.flatMap((r) => r.primitives)] as const,
  ];
}

function linearToSrgb(c: number): number {
  if (c <= 0) return 0;
  if (c >= 1) return 1;
  return c <= 0.0031308 ? c * 12.92 : 1.055 * Math.pow(c, 1 / 2.4) - 0.055;
}

function glbMaterials(glbBytes: Uint8Array): [Rgb, boolean][] {
  const view = new DataView(glbBytes.buffer, glbBytes.byteOffset, glbBytes.byteLength);
  const jsonLen = view.getUint32(12, true);
  const jsonText = new TextDecoder().decode(glbBytes.subarray(20, 20 + jsonLen));
  const parsed = JSON.parse(jsonText);
  return (parsed.materials as any[])
    .map((m): [Rgb, boolean] => {
      const f = m.pbrMetallicRoughness.baseColorFactor as number[];
      return [
        [
          Math.round(linearToSrgb(f[0]) * 255),
          Math.round(linearToSrgb(f[1]) * 255),
          Math.round(linearToSrgb(f[2]) * 255),
        ],
        Boolean(m.doubleSided),
      ];
    })
    .sort((a, b) => a[0][0] - b[0][0] || a[0][1] - b[0][1] || a[0][2] - b[0][2]);
}

describe('Unpainted face colours in scenes and exports (#533)', () => {
  it.each(VERSIONS)('shows the model front and back face colours on face_colors_%s.skp', (version) => {
    const skp = SkpFile.open(fixturePath(`face_colors_${version}.skp`));
    for (const [built, prims] of builtPairs(skp)) {
      expect(prims).toHaveLength(2);
      const sorted = [...prims].sort((a, b) => Number(Boolean(a.unpaintedBack)) - Number(Boolean(b.unpaintedBack)));
      const [front, back] = sorted;
      expect([Boolean(front.unpaintedBack), Boolean(back.unpaintedBack)]).toEqual([false, true]);
      expect([rgbOf(built, front.materialIndex), rgbOf(built, back.materialIndex)]).toEqual([FRONT, BACK]);
      expect(doubleSidedOf(built, front.materialIndex)).toBe(false);
      expect(doubleSidedOf(built, back.materialIndex)).toBe(false);

      const fronts = corners(front);
      const backs = corners(back);
      expect(fronts.length).toBeGreaterThan(0);
      expect(fronts.length).toBe(backs.length);
      for (let i = 0; i < fronts.length; i++) {
        expect(sameVertexSet(fronts[i], backs[i])).toBe(true);
        expect(sameWinding(fronts[i], backs[i])).toBe(false);
      }
    }
  });

  it.each(['v4', '2017', '2021'] as const)('unpaintedFaces="tag" keeps old tag-colour behaviour on %s', (version) => {
    const skp = SkpFile.open(fixturePath(`face_colors_${version}.skp`));
    const layer = skp.parse().layers[0];
    for (const [built, prims] of builtPairs(skp, { unpaintedFaces: 'tag' })) {
      expect(prims).toHaveLength(1);
      const prim = prims[0];
      expect(rgbOf(built, prim.materialIndex)).toEqual([layer.color.r, layer.color.g, layer.color.b]);
      expect(doubleSidedOf(built, prim.materialIndex)).toBe(true);
      expect(Boolean(prim.unpaintedBack)).toBe(false);
    }
  });

  it('falls back to tag colour on a file without model face colours (SketchUp 3)', () => {
    const skp = SkpFile.open(fixturePath('legacy_v3_synthetic.skp'));
    expect(skp.parse().faceFrontColor).toBeNull();
    const stylePairs = builtPairs(skp);
    const tagPairs = builtPairs(skp, { unpaintedFaces: 'tag' });
    for (let i = 0; i < stylePairs.length; i++) {
      const [style, stylePrims] = stylePairs[i];
      const [tag, tagPrims] = tagPairs[i];
      expect(stylePrims.map((p) => rgbOf(style, p.materialIndex))).toEqual(
        tagPrims.map((p) => rgbOf(tag, p.materialIndex))
      );
      expect(stylePrims.some((p) => p.unpaintedBack)).toBe(false);
    }
  });

  it('throws on unknown unpaintedFaces option', () => {
    const skp = SkpFile.open(fixturePath('face_colors_2017.skp'));
    expect(() => skp.buildScene({ unpaintedFaces: 'layer' as any })).toThrow(/unpaintedFaces/);
    expect(() => skp.buildInstancedScene({ unpaintedFaces: 'layer' as any })).toThrow(/unpaintedFaces/);
  });

  it('painted face sides and painted components win over model face colours', () => {
    const builder = create();
    const red = builder.addMaterial('Red', [200, 10, 10, 255]);
    const square: [number, number, number][] = [
      [0, 0, 0],
      [10, 0, 0],
      [10, 10, 0],
      [0, 10, 0],
    ];
    const component = builder.addComponentDefinition('Painted', (def) => {
      def.addFace(square.map(([x, y]) => [x, y, 9] as [number, number, number]));
    });
    builder.addFace(square, { material: red }); // front painted, back unpainted
    builder.addFace(
      square.map(([x, y]) => [x, y, 5] as [number, number, number]),
      { backMaterial: red }
    ); // front unpainted, back painted
    builder.addInstance(component, { material: red });

    const skp = skpFromBuilder(builder);
    const model = skp.parse();
    const front = model.faceFrontColor!;
    const back = model.faceBackColor!;
    expect(front).not.toBeNull();
    expect(front).not.toEqual(back);

    for (const [built, prims] of builtPairs(skp)) {
      const sides = prims.map((p) => [
        rgbOf(built, p.materialIndex),
        doubleSidedOf(built, p.materialIndex),
        Boolean(p.unpaintedBack),
      ]);
      expect(sides).toContainEqual([[200, 10, 10], true, false]);
      expect(sides).toContainEqual([front, false, false]);
      expect(sides).toContainEqual([back, false, false]);
      expect(prims.some((p) => p.unpaintedBack)).toBe(false);
    }
  });

  it('emits one instanced mesh resource when an unpainted component sits on two tags', () => {
    const builder = create();
    const tagA = builder.addLayer('A', { color: [255, 0, 0] });
    const tagB = builder.addLayer('B', { color: [0, 0, 255] });
    const part = builder.addComponentDefinition('Part', (def) => {
      def.addFace([
        [0, 0, 0],
        [1, 0, 0],
        [1, 1, 0],
        [0, 1, 0],
      ]);
    });
    builder.addInstance(part, { layer: tagA });
    builder.addInstance(part, { layer: tagB, translation: [5, 0, 0] });
    const skp = skpFromBuilder(builder);
    expect(skp.buildInstancedScene().meshResources).toHaveLength(1);
  });

  it('unpaintedFaces="tag" resolves each face own tag colour and falls back to placement tag (#697)', () => {
    const rgbA: Rgb = [220, 40, 50];
    const rgbB: Rgb = [40, 180, 70];
    const rgbC: Rgb = [50, 90, 230];
    const rgbD: Rgb = [190, 140, 30];
    const builder = create();
    const tagA = builder.addLayer('TagA', { color: rgbA });
    const tagB = builder.addLayer('TagB', { color: rgbB });
    const tagC = builder.addLayer('TagC', { color: rgbC });
    const tagD = builder.addLayer('TagD', { color: rgbD });
    const panel = builder.addComponentDefinition('Panel', (def) => {
      def.addFace(
        [
          [0, 0, 0],
          [10, 0, 0],
          [10, 10, 0],
          [0, 10, 0],
        ],
        { layer: tagA }
      );
      def.addFace(
        [
          [0, 0, 10],
          [10, 0, 10],
          [10, 10, 10],
          [0, 10, 10],
        ],
        { layer: tagB }
      );
      def.addFace([
        [0, 0, 20],
        [10, 0, 20],
        [10, 10, 20],
        [0, 10, 20],
      ]);
    });
    builder.addInstance(panel, { layer: tagC });
    builder.addInstance(panel, { layer: tagD, translation: [0, 0, 100] });

    const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'openskp-697-'));
    try {
      const filePath = path.join(tmpDir, 'per_face_tags.skp');
      builder.save(filePath);
      const skp = SkpFile.open(filePath);
      const model = skp.parse();

      const baked = skp.buildScene({ unpaintedFaces: 'tag' });
      const bakedByZ = Object.fromEntries(
        baked.glbPrimitives.map((p) => [
          Math.round(p.positions[1] / 0.0254),
          [rgbOf(baked, p.materialIndex), doubleSidedOf(baked, p.materialIndex), Boolean(p.unpaintedBack)],
        ])
      );
      expect(bakedByZ).toEqual({
        0: [rgbA, true, false],
        10: [rgbB, true, false],
        20: [rgbC, true, false],
        100: [rgbA, true, false],
        110: [rgbB, true, false],
        120: [rgbD, true, false],
      });

      const instanced = skp.buildInstancedScene({ unpaintedFaces: 'tag' });
      expect(instanced.meshResources).toHaveLength(2);
      const resByZ = instanced.meshResources.map((r) =>
        Object.fromEntries(
          r.primitives.map((p) => [
            Math.round(p.positions[1] / 0.0254),
            [rgbOf(instanced, p.materialIndex), doubleSidedOf(instanced, p.materialIndex), Boolean(p.unpaintedBack)],
          ])
        )
      );
      expect(resByZ).toEqual([
        { 0: [rgbA, true, false], 10: [rgbB, true, false], 20: [rgbC, true, false] },
        { 0: [rgbA, true, false], 10: [rgbB, true, false], 20: [rgbD, true, false] },
      ]);

      const styleInst = skp.buildInstancedScene();
      expect(styleInst.meshResources).toHaveLength(1);
      expect(
        styleInst.meshResources[0].primitives
          .map((p) => [rgbOf(styleInst, p.materialIndex), Boolean(p.unpaintedBack)])
          .sort((a, b) => Number(a[1]) - Number(b[1]))
      ).toEqual([
        [model.faceFrontColor, false],
        [model.faceBackColor, true],
      ]);
    } finally {
      fs.rmSync(tmpDir, { recursive: true, force: true });
    }
  });

  it('single-colour exports and Fragments keep one copy of each unpainted face', () => {
    const builder = create();
    const p: [number, number, number][] = [];
    for (const z of [0, 10]) {
      for (const y of [0, 10]) {
        for (const x of [0, 10]) {
          p.push([x, y, z]);
        }
      }
    }
    for (const [a, b, c, d] of [
      [0, 2, 3, 1],
      [4, 5, 7, 6],
      [0, 1, 5, 4],
      [1, 3, 7, 5],
      [3, 2, 6, 7],
      [2, 0, 4, 6],
    ]) {
      builder.addFace([p[a], p[b], p[c], p[d]]);
    }
    const skp = skpFromBuilder(builder);
    const style = skp.buildScene();
    const tag = skp.buildScene({ unpaintedFaces: 'tag' });

    const sumTris = (prims: { indices: Uint32Array }[]) =>
      prims.reduce((acc, pr) => acc + Math.floor(pr.indices.length / 3), 0);
    expect(sumTris(style.glbPrimitives)).toBe(24);
    expect(sumTris(style.surfacePrimitives!)).toBe(12);
    expect(style.surfacePrimitives![0]).toBe(style.glbPrimitives[0]);

    expect(toSTLAscii(style)).toBe(toSTLAscii(tag));
    const stlBin = toSTLBinary(style);
    const stlView = new DataView(stlBin.buffer, stlBin.byteOffset, stlBin.byteLength);
    expect(stlView.getUint32(80, true)).toBe(12);
    expect((toOBJ(style).match(/\nf /g) || []).length).toBe(12);
    expect((toOBJ(tag).match(/\nf /g) || []).length).toBe(12);
    expect(toPLYAscii(style)).toContain('element face 12\n');
    const dxfStyleCount = (toDXF(style, undefined, '3dface').match(/3DFACE/g) || []).length;
    const dxfTagCount = (toDXF(tag, undefined, '3dface').match(/3DFACE/g) || []).length;
    expect(dxfStyleCount).toBeGreaterThan(0);
    expect(dxfStyleCount).toBe(dxfTagCount);
    expect((toIFC(style).match(/IFCTRIANGULATEDFACESET/g) || []).length).toBe(1);
    expect((toIFC(tag).match(/IFCTRIANGULATEDFACESET/g) || []).length).toBe(1);

    const fragTriangles = (inst: ReturnType<SkpFile['buildInstancedScene']>) => {
      const read = fromFragments(toFragments(inst));
      return read.meshResources.reduce(
        (acc, r) => acc + r.primitives.reduce((s, pr) => s + Math.floor(pr.indices.length / 3), 0),
        0
      );
    };
    expect(fragTriangles(skp.buildInstancedScene())).toBe(12);
    expect(fragTriangles(skp.buildInstancedScene({ unpaintedFaces: 'tag' }))).toBe(12);
  });

  it('GLB and instanced GLB exports show both unpainted sides by default and tag colour on request', () => {
    const skp = SkpFile.open(fixturePath('face_colors_2017.skp'));
    const layer = skp.parse().layers[0];
    const layerRgb: Rgb = [layer.color.r, layer.color.g, layer.color.b];
    const expectedStyle = [
      [FRONT, false] as [Rgb, boolean],
      [BACK, false] as [Rgb, boolean],
    ].sort((a, b) => a[0][0] - b[0][0]);

    expect(glbMaterials(toGLB(skp.buildScene()))).toEqual(expectedStyle);
    expect(glbMaterials(toGLB(skp.buildScene({ unpaintedFaces: 'tag' })))).toEqual([[layerRgb, true]]);

    expect(glbMaterials(toInstancedGLB(skp.buildInstancedScene()))).toEqual(expectedStyle);
    expect(glbMaterials(toInstancedGLB(skp.buildInstancedScene({ unpaintedFaces: 'tag' })))).toEqual([
      [layerRgb, true],
    ]);
  });

  it('single-colour exports write the primitives a caller kept', () => {
    const scene = SkpFile.fromBuffer(
      new Uint8Array(fs.readFileSync(fixturePath('face_colors_2020.skp'))).buffer
    ).buildScene();
    expect(scene.surfacePrimitives!.length).toBeGreaterThan(0);
    expect(toSTLAscii({ ...scene, glbPrimitives: [] })).not.toContain('facet');
  });
});
