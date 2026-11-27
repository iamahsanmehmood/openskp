import { describe, it, expect } from 'vitest';
import * as fs from 'fs';
import * as path from 'path';
import { unzipSync, zipSync } from 'fflate';
import { create, buildScene, buildInstancedScene } from '../src/index';

const SQUARE: [number, number, number][] = [
  [0, 0, 0],
  [10, 0, 0],
  [10, 10, 0],
  [0, 10, 0],
];

function toArrayBuffer(bytes: Uint8Array): ArrayBuffer {
  return bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer;
}

function paintedModelBuffer(): ArrayBuffer {
  const builder = create();
  const red = builder.addMaterial('Red', [200, 10, 10, 255]);
  const blue = builder.addMaterial('Blue', [10, 10, 200, 128]);
  const leaf = builder.addComponentDefinition('Leaf', (def) => {
    def.addFace(SQUARE);
  });
  const outer = builder.addComponentDefinition('Outer', (def) => {
    def.addInstance(leaf, { name: 'InheritsPaint' });
    def.addInstance(leaf, { name: 'OwnPaint', material: blue, translation: [20, 0, 0] });
  });
  builder.addInstance(outer, { name: 'Painted', material: red });
  return toArrayBuffer(builder.toBytes());
}

describe('Classic instance paint and tag (#596)', () => {
  it('applies paint and inherits it (including translucency) across nested components', () => {
    const ab = paintedModelBuffer();
    const baked = buildScene(ab);
    const instanced = buildInstancedScene(ab);

    const bakedIndices = baked.glbPrimitives.map((p) => p.materialIndex);
    const instancedIndices = instanced.meshResources.flatMap((r) =>
      r.primitives.map((p) => p.materialIndex)
    );

    const round6 = (v: number) => Math.round(v * 1e6) / 1e6;
    for (const [gltfMats, indices] of [
      [baked.gltfMaterials, bakedIndices],
      [instanced.gltfMaterials, instancedIndices],
    ] as const) {
      const summaries = new Set(
        indices.map((i) => {
          const m = gltfMats[i] as Record<string, any>;
          const bcf = m.pbrMetallicRoughness.baseColorFactor as number[];
          return JSON.stringify([
            round6(bcf[0]),
            round6(bcf[1]),
            round6(bcf[2]),
            round6(bcf[3]),
            Boolean(m.doubleSided),
            m.alphaMode ?? 'OPAQUE',
          ]);
        })
      );
      expect(summaries).toEqual(
        new Set([
          JSON.stringify([
            round6(200 / 255),
            round6(10 / 255),
            round6(10 / 255),
            1,
            true,
            'OPAQUE',
          ]),
          JSON.stringify([
            round6(10 / 255),
            round6(10 / 255),
            round6(200 / 255),
            round6(128 / 255),
            true,
            'BLEND',
          ]),
        ])
      );
    }
  });

  it('gives each paint its own instanced mesh resource', () => {
    const scene = buildInstancedScene(paintedModelBuffer());
    const byName = new Map(scene.sceneHierarchy.children[0].children.map((n) => [n.name, n]));
    const inherits = byName.get('InheritsPaint');
    const own = byName.get('OwnPaint');
    expect(inherits?.meshResourceId).toBeDefined();
    expect(own?.meshResourceId).toBeDefined();
    expect(inherits?.meshResourceId).not.toBe(own?.meshResourceId);
  });

  it("places each instance on its own tag rather than its parent's", () => {
    const builder = create();
    const walls = builder.addLayer('Walls');
    const part = builder.addComponentDefinition('Part', (def) => {
      def.addFace(SQUARE);
    });
    builder.addInstance(part, { name: 'Tagged', layer: walls });
    builder.addInstance(part, { name: 'Untagged', translation: [20, 0, 0] });

    const ab = toArrayBuffer(builder.toBytes());
    for (const built of [buildScene(ab), buildInstancedScene(ab)]) {
      const layers = Object.fromEntries(
        built.sceneHierarchy.children.map((n) => [n.name, n.layer])
      );
      expect(layers).toEqual({ Tagged: 'Walls', Untagged: 'Layer0' });
    }
  });

  const PNG_1X1 = new Uint8Array([
    0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0x00, 0x00, 0x00, 0x0d, 0x49, 0x48, 0x44, 0x52,
    0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x01, 0x08, 0x02, 0x00, 0x00, 0x00, 0x90, 0x77, 0x53,
    0xde, 0x00, 0x00, 0x00, 0x0c, 0x49, 0x44, 0x41, 0x54, 0x78, 0x9c, 0x63, 0x38, 0x91, 0x62, 0x04,
    0x00, 0x03, 0x56, 0x01, 0x5f, 0xe8, 0x17, 0x84, 0x52, 0x00, 0x00, 0x00, 0x00, 0x49, 0x45, 0x4e,
    0x44, 0xae, 0x42, 0x60, 0x82,
  ]);

  const PNG_2X1 = new Uint8Array([
    0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0x00, 0x00, 0x00, 0x0d, 0x49, 0x48, 0x44, 0x52,
    0x00, 0x00, 0x00, 0x02, 0x00, 0x00, 0x00, 0x01, 0x08, 0x02, 0x00, 0x00, 0x00, 0x7b, 0x40, 0xe8,
    0xdd, 0x00, 0x00, 0x00, 0x0f, 0x49, 0x44, 0x41, 0x54, 0x78, 0x9c, 0x63, 0x30, 0x4a, 0x39, 0x61,
    0x94, 0x72, 0x02, 0x00, 0x08, 0x6d, 0x02, 0xbd, 0xeb, 0xae, 0x9b, 0xe7, 0x00, 0x00, 0x00, 0x00,
    0x49, 0x45, 0x4e, 0x44, 0xae, 0x42, 0x60, 0x82,
  ]);

  const uvBounds = (uvs: Float32Array): [number, number, number, number] => {
    let minU = uvs[0], maxU = uvs[0], minV = uvs[1], maxV = uvs[1];
    for (let i = 0; i < uvs.length; i += 2) {
      if (uvs[i] < minU) minU = uvs[i];
      if (uvs[i] > maxU) maxU = uvs[i];
      if (uvs[i + 1] < minV) minV = uvs[i + 1];
      if (uvs[i + 1] > maxV) maxV = uvs[i + 1];
    }
    const r4 = (v: number) => Math.round(v * 1e4) / 1e4;
    return [r4(minU), r4(maxU), r4(minV), r4(maxV)];
  };

  it('preserves textured instance paint, UVs, and tags on SDK classic and 2021+ fixtures (#559)', () => {
    for (const fname of ['instance_textured_paint_2020.skp', 'instance_textured_paint_2021.skp']) {
      const ab = toArrayBuffer(fs.readFileSync(path.join(__dirname, 'fixtures', fname)));
      const baked = buildScene(ab);
      const instanced = buildInstancedScene(ab);

      expect(baked.textures.length).toBe(2);
      expect(instanced.textures.length).toBe(2);
      expect(baked.glbPrimitives.length).toBe(10);
      expect(instanced.meshResources.length).toBe(6);

      for (const built of [baked, instanced]) {
        const topLayers = Object.fromEntries(
          built.sceneHierarchy.children.map((n) => [n.name, n.layer])
        );
        expect(topLayers).toEqual({
          BrickPanel: 'ExteriorTag',
          WoodPanel: 'InteriorTag',
          PlainPanel: 'Layer0',
          BrickGroup: 'ExteriorTag',
          OuterInst: 'ExteriorTag',
        });
        const outer = built.sceneHierarchy.children.find((n) => n.name === 'OuterInst')!;
        const nestedLayers = Object.fromEntries(outer.children.map((n) => [n.name, n.layer]));
        expect(nestedLayers).toEqual({
          ChildInherits: 'ExteriorTag',
          ChildOverrides: 'InteriorTag',
        });
      }

      const byId = new Map(instanced.meshResources.map((r) => [r.id, r]));
      const top = new Map(instanced.sceneHierarchy.children.map((n) => [n.name, n]));
      const nested = new Map(top.get('OuterInst')!.children.map((n) => [n.name, n]));

      const primSummary = (meshId: string) =>
        byId.get(meshId)!.primitives.map((p) => {
          const m = instanced.gltfMaterials[p.materialIndex] as any;
          const pbr = m.pbrMetallicRoughness;
          return {
            tex: pbr.baseColorTexture?.index ?? null,
            alpha: Math.round(pbr.baseColorFactor[3] * 1e4) / 1e4,
            uv: uvBounds(p.uvs),
          };
        });

      const groupPrims = primSummary(top.get('BrickGroup')!.meshResourceId!);
      const inhPrims = primSummary(nested.get('ChildInherits')!.meshResourceId!);
      const ovrPrims = primSummary(nested.get('ChildOverrides')!.meshResourceId!);
      expect(groupPrims).toHaveLength(1);
      expect(inhPrims).toHaveLength(1);
      expect(ovrPrims).toHaveLength(1);
      expect(groupPrims[0].tex).not.toBeNull();
      expect(ovrPrims[0].tex).not.toBeNull();
      expect(groupPrims[0].tex).not.toBe(ovrPrims[0].tex);
      expect(groupPrims[0].uv).toEqual([0, 1, 0, 1]);
      expect(inhPrims[0].uv).toEqual([0, 1, 0, 1]);
      expect(ovrPrims[0].uv).toEqual([0, 2.5, 0, 2.5]);
    }
  });

  it('resolves unnamed ("") and named textured materials on writer-made instances and groups (#559)', () => {
    const rect: [number, number, number][] = [
      [0, 0, 0],
      [10, 0, 0],
      [10, 20, 0],
      [0, 20, 0],
    ];
    const builder = create();
    const namedMat = builder.addTextureMaterial('Brick', PNG_1X1, 'a.png', 20, 10);
    const unnamedMat = builder.addTextureMaterial('', PNG_2X1, 'b.png', 8, 4);
    const ext = builder.addLayer('Ext');
    const inter = builder.addLayer('Int');
    const panel = builder.addComponentDefinition('Panel', (def) => {
      def.addFace(rect);
    });
    builder.addGroup(
      (grp) => {
        grp.addFace(rect);
      },
      { name: 'PaintedGroup', translation: [40, 0, 0], material: namedMat, layer: ext }
    );
    builder.addInstance(panel, { name: 'NamedInst', material: namedMat, layer: ext });
    builder.addInstance(panel, {
      name: 'UnnamedInst',
      translation: [20, 0, 0],
      material: unnamedMat,
      layer: inter,
    });

    const ab = toArrayBuffer(builder.toBytes());
    const baked = buildScene(ab);
    const instanced = buildInstancedScene(ab);

    expect(baked.textures.length).toBe(2);
    expect(instanced.textures.length).toBe(2);
    for (const built of [baked, instanced]) {
      const layers = Object.fromEntries(
        built.sceneHierarchy.children.map((n) => [n.name, n.layer])
      );
      expect(layers).toEqual({
        PaintedGroup: 'Ext',
        NamedInst: 'Ext',
        UnnamedInst: 'Int',
      });
    }

    const byId = new Map(instanced.meshResources.map((r) => [r.id, r]));
    const nodes = new Map(instanced.sceneHierarchy.children.map((n) => [n.name, n]));
    expect(nodes.get('NamedInst')!.meshResourceId).not.toBe(
      nodes.get('UnnamedInst')!.meshResourceId
    );
    expect(uvBounds(byId.get(nodes.get('NamedInst')!.meshResourceId!)!.primitives[0].uvs)).toEqual([
      0, 1, 0, 1,
    ]);
    expect(
      uvBounds(byId.get(nodes.get('UnnamedInst')!.meshResourceId!)!.primitives[0].uvs)
    ).toEqual([0, 2.5, 0, 2.5]);
  });

  it('isolates instanced mesh variants when inherited materials share name and RGB (#559)', () => {
    const raw = new Uint8Array(
      fs.readFileSync(path.join(__dirname, 'fixtures', 'instance_textured_paint_2021.skp'))
    );
    let pkPos = 0;
    for (let i = 0; i + 4 <= raw.length; i++) {
      if (raw[i] === 0x50 && raw[i + 1] === 0x4b && raw[i + 2] === 0x03 && raw[i + 3] === 0x04) {
        pkPos = i;
        break;
      }
    }
    const prefix = raw.slice(0, pkPos);
    const entries = unzipSync(raw.slice(pkPos));
    const dec = new TextDecoder();
    const enc = new TextEncoder();

    // Case 1: same name="Shared" and RGB (170, 58, 40), different textures and tile scales
    const mod1: Record<string, Uint8Array> = { ...entries };
    for (const k of ['materials/BrickTex/material.xml', 'materials/WoodTex/material.xml']) {
      let xml = dec.decode(entries[k]);
      xml = xml
        .replace('name="BrickTex"', 'name="Shared"')
        .replace('name="WoodTex"', 'name="Shared"')
        .replace(/colorRed="\d+"/, 'colorRed="170"')
        .replace(/colorGreen="\d+"/, 'colorGreen="58"')
        .replace(/colorBlue="\d+"/, 'colorBlue="40"');
      mod1[k] = enc.encode(xml);
    }
    const zipped1 = zipSync(mod1);
    const buf1 = new Uint8Array(prefix.length + zipped1.length);
    buf1.set(prefix, 0);
    buf1.set(zipped1, prefix.length);

    const inst1 = buildInstancedScene(toArrayBuffer(buf1));
    const byId1 = new Map(inst1.meshResources.map((r) => [r.id, r]));
    const outer1 = inst1.sceneHierarchy.children.find((n) => n.name === 'OuterInst')!;
    const nested1 = new Map(outer1.children.map((n) => [n.name, n]));
    expect(nested1.get('ChildInherits')!.meshResourceId).not.toBe(
      nested1.get('ChildOverrides')!.meshResourceId
    );
    expect(
      uvBounds(byId1.get(nested1.get('ChildInherits')!.meshResourceId!)!.primitives[0].uvs)
    ).toEqual([0, 1, 0, 1]);
    expect(
      uvBounds(byId1.get(nested1.get('ChildOverrides')!.meshResourceId!)!.primitives[0].uvs)
    ).toEqual([0, 2.5, 0, 2.5]);

    // Case 2: same name="Shared", RGB (170, 58, 40), texture bytes, and 10x20 scale,
    // differing only in transparency (1.0 vs 0.5)
    const mod2: Record<string, Uint8Array> = { ...entries };
    const brickXml = dec
      .decode(entries['materials/BrickTex/material.xml'])
      .replace('name="BrickTex"', 'name="Shared"')
      .replace(/colorRed="\d+"/, 'colorRed="170"')
      .replace(/colorGreen="\d+"/, 'colorGreen="58"')
      .replace(/colorBlue="\d+"/, 'colorBlue="40"');
    let woodTransXml = brickXml
      .replace(/useTrans="\d+"/, 'useTrans="1"')
      .replace(/trans="[^"]+"/, 'trans="0.5"');
    if (!woodTransXml.includes('useTrans=')) {
      woodTransXml = woodTransXml.replace(
        'name="Shared"',
        'name="Shared" useTrans="1" trans="0.5"'
      );
    }
    const brickImgKey = Object.keys(entries).find(
      (k) => k.startsWith('materials/BrickTex/') && !k.endsWith('.xml')
    )!;
    const woodImgKey = Object.keys(entries).find(
      (k) => k.startsWith('materials/WoodTex/') && !k.endsWith('.xml')
    )!;
    mod2['materials/BrickTex/material.xml'] = enc.encode(brickXml);
    mod2['materials/WoodTex/material.xml'] = enc.encode(woodTransXml);
    mod2[woodImgKey] = entries[brickImgKey];

    const zipped2 = zipSync(mod2);
    const buf2 = new Uint8Array(prefix.length + zipped2.length);
    buf2.set(prefix, 0);
    buf2.set(zipped2, prefix.length);

    const inst2 = buildInstancedScene(toArrayBuffer(buf2));
    const byId2 = new Map(inst2.meshResources.map((r) => [r.id, r]));
    const outer2 = inst2.sceneHierarchy.children.find((n) => n.name === 'OuterInst')!;
    const nested2 = new Map(outer2.children.map((n) => [n.name, n]));
    expect(nested2.get('ChildInherits')!.meshResourceId).not.toBe(
      nested2.get('ChildOverrides')!.meshResourceId
    );
    const matInh = inst2.gltfMaterials[
      byId2.get(nested2.get('ChildInherits')!.meshResourceId!)!.primitives[0].materialIndex
    ] as any;
    const matOvr = inst2.gltfMaterials[
      byId2.get(nested2.get('ChildOverrides')!.meshResourceId!)!.primitives[0].materialIndex
    ] as any;
    expect(matInh.pbrMetallicRoughness.baseColorFactor[3]).toBeCloseTo(1.0, 5);
    expect(matOvr.pbrMetallicRoughness.baseColorFactor[3]).toBeCloseTo(0.5, 5);
    expect(matOvr.alphaMode).toBe('BLEND');
  });
});
