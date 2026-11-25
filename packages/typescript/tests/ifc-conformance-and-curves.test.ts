import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';
import { describe, it, expect } from 'vitest';
import { toIFC, exportIFC, stepText, generateIFCGUID, meshIsClosed } from '../src/ifc';
import { buildScene, CurveSetMetadata, SkpScene } from '../src/index';

const FIXTURES = path.join(__dirname, 'fixtures');

function entityLine(text: string, token: string): string {
  const line = text.split(/\r?\n/).find((l) => l.includes(token));
  if (!line) throw new Error(`No entity line containing ${token}`);
  return line;
}

function splitAttributes(line: string): string[] {
  const body = line.slice(line.indexOf('(') + 1, line.lastIndexOf(')'));
  const out: string[] = [];
  let depth = 0;
  let current = '';
  for (const ch of body) {
    if (ch === '(') depth++;
    else if (ch === ')') depth--;
    if (ch === ',' && depth === 0) {
      out.push(current);
      current = '';
    } else {
      current += ch;
    }
  }
  out.push(current);
  return out;
}

function assertIdsAreDense(text: string): number {
  const ids: number[] = [];
  for (const m of text.matchAll(/^#(\d+)=/gm)) {
    ids.push(Number(m[1]));
  }
  expect(ids.length).toBeGreaterThan(0);
  expect(new Set(ids).size).toBe(ids.length);
  expect(Math.max(...ids)).toBe(ids.length);
  return ids.length;
}

function meshScene(
  name: string,
  positions: number[],
  indices: number[],
  extra?: {
    layer?: string;
    layerHidden?: Record<string, boolean>;
    properties?: Record<string, string>;
  }
): SkpScene {
  return {
    sceneHierarchy: {
      name: 'Root',
      nameIsGenerated: false,
      definitionName: 'RootDef',
      layer: 'Layer0',
      positionMm: [0, 0, 0],
      properties: {},
      attributeDictionaries: {},
      guid: '',
      children: [],
    },
    meshIndex: {
      [name]: {
        name,
        definitionName: 'Def',
        layer: extra?.layer ?? 'Layer0',
        positionMm: [0, 0, 0],
        properties: extra?.properties ?? {},
        attributeDictionaries: {},
        path: `ROOT / ${name}`,
      },
    },
    glbPrimitives: [
      {
        geomName: name,
        materialIndex: 0,
        positions: new Float32Array(positions),
        normals: new Float32Array(new Array(positions.length).fill(0)),
        uvs: new Float32Array(new Array((positions.length / 3) * 2).fill(0)),
        indices: new Uint32Array(indices),
      },
    ],
    gltfMaterials: [{ pbrMetallicRoughness: { baseColorFactor: [0.5, 0.5, 0.5, 1.0] } }],
    bounds: null,
    textures: [],
    layerHidden: extra?.layerHidden ?? {},
  };
}

function curveScene(curveSets: CurveSetMetadata[]): SkpScene {
  return {
    sceneHierarchy: {
      name: 'Root',
      nameIsGenerated: false,
      definitionName: 'RootDef',
      layer: 'Layer0',
      positionMm: [0, 0, 0],
      properties: {},
      attributeDictionaries: {},
      guid: '',
      children: [],
    },
    meshIndex: {},
    glbPrimitives: [],
    curveSets,
    gltfMaterials: [],
    bounds: null,
    textures: [],
    layerHidden: {},
  };
}

describe('IFC4 STEP conformance and loose-edge curve annotations (#285)', () => {
  it('encodes non-ASCII text with ISO 10303-21 \\X2\\ and \\X4\\ escapes', () => {
    expect(stepText("it's a\\b")).toBe("it''s a\\\\b");
    expect(stepText('测试项目')).toBe('\\X2\\6D4B8BD5987976EE\\X0\\');
    expect(stepText('emoji\u{1F600}墙')).toBe('emoji\\X4\\0001F60000005899\\X0\\');
    expect(stepText('剪力墙 JLQ-1')).toBe('\\X2\\526A529B5899\\X0\\ JLQ-1');
    expect(stepText('\u{1F600}a墙')).toBe('\\X4\\0001F600\\X0\\a\\X4\\00005899\\X0\\');

    const guid = generateIFCGUID();
    expect(guid).toHaveLength(22);
    expect(['0', '1', '2', '3']).toContain(guid[0]);

    const text = toIFC(
      meshScene('剪力墙 JLQ-1', [0, 0, 0, 1, 0, 0, 0, 1, 0], [0, 1, 2], {
        layer: '隐藏层',
        layerHidden: { 隐藏层: true },
        properties: { 名称: '承重墙' },
      })
    );
    expect(text).not.toContain('剪力墙');
    expect(text).toContain('\\X2\\526A529B5899\\X0\\ JLQ-1');
    expect(text).toContain('\\X2\\540D79F0\\X0\\');
    expect(text).toContain('\\X2\\627F91CD5899\\X0\\');
    const layerLine = entityLine(text, 'IFCPRESENTATIONLAYERWITHSTYLE(');
    expect(layerLine).toContain(',.F.,.F.,.F.,())');
  });

  it('writes IFC4 enum spellings and per-entity attribute counts', () => {
    const doorText = toIFC(meshScene('Front Door', [0, 0, 0, 1, 0, 0, 0, 1, 0], [0, 1, 2]));
    expect(doorText).not.toContain('.TRUE.');
    expect(doorText).not.toContain('.FALSE.');
    expect(doorText).not.toContain('.READWRITE.');
    expect(doorText).not.toContain('.STERADIANUNIT.');
    expect(doorText).toContain('.NOCHANGE.');
    expect(doorText).toContain('.SOLIDANGLEUNIT.');
    expect(doorText).toContain('.STERADIAN.');
    expect(splitAttributes(entityLine(doorText, 'IFCDOOR('))).toHaveLength(13);

    const wallText = toIFC(meshScene('Outer Wall', [0, 0, 0, 1, 0, 0, 0, 1, 0], [0, 1, 2]));
    expect(splitAttributes(entityLine(wallText, 'IFCWALL('))).toHaveLength(9);

    const door2x3 = toIFC(
      meshScene('Front Door', [0, 0, 0, 1, 0, 0, 0, 1, 0], [0, 1, 2]),
      1000,
      'IFC2X3'
    );
    expect(splitAttributes(entityLine(door2x3, 'IFCDOOR('))).toHaveLength(10);
  });

  it('computes Closed on IfcTriangulatedFaceSet from welded manifold topology', () => {
    const A = [0, 0, 0];
    const B = [1, 0, 0];
    const C = [0, 1, 0];
    const D = [0, 0, 1];
    const closedFlag = (txt: string): string =>
      splitAttributes(entityLine(txt, 'IFCTRIANGULATEDFACESET('))[2];

    expect(closedFlag(toIFC(meshScene('Open', [...A, ...B, ...C], [0, 1, 2])))).toBe('.F.');
    expect(
      closedFlag(
        toIFC(
          meshScene(
            'PerFaceTetra',
            [...A, ...B, ...C, ...A, ...B, ...D, ...A, ...C, ...D, ...B, ...C, ...D],
            [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]
          )
        )
      )
    ).toBe('.T.');
    expect(
      closedFlag(
        toIFC(
          meshScene('TJunction', [...A, ...B, ...C, ...D, 0, -1, 0], [0, 1, 2, 0, 1, 3, 0, 1, 4])
        )
      )
    ).toBe('.F.');
  });

  it('exports polyline and analytic-arc curveSets as IfcAnnotation with inline IfcArcIndex', () => {
    const polyText = toIFC(
      curveScene([
        {
          name: 'ClosedOutline',
          definitionName: '',
          layer: 'Framing',
          path: 'ROOT',
          nameIsGenerated: false,
          pointsM: [
            [0, 0, 0],
            [1, 0, 0],
            [0, 0, 1],
          ],
          closed: true,
          arc: null,
          attributeDictionaries: { 'frame-meta': { mark: 'B1' } },
        },
      ])
    );
    assertIdsAreDense(polyText);
    expect(polyText).toContain("IFCGEOMETRICREPRESENTATIONSUBCONTEXT('Annotation','Model'");
    expect(polyText).toContain('IFCANNOTATION(');
    expect(polyText).toContain("'ClosedOutline'");
    expect(polyText).toContain("'Pset_frame-meta'");
    const polyRefs = splitAttributes(entityLine(polyText, 'IFCPOLYLINE('))[0].match(/#\d+/g) ?? [];
    expect(polyRefs).toHaveLength(4);
    expect(entityLine(polyText, "'Framing'")).toContain('IFCPRESENTATIONLAYERWITHSTYLE');

    const arcText = toIFC(
      curveScene([
        {
          name: 'ArcRun',
          definitionName: '',
          layer: 'Layer0',
          path: 'ROOT',
          nameIsGenerated: false,
          pointsM: [
            [1, 0, 0],
            [0, 0, -1],
          ],
          closed: false,
          arc: {
            points: [
              [1, 0, 0],
              [Math.SQRT1_2, 0, -Math.SQRT1_2],
              [0, 0, -1],
            ],
            segments: [[0, 1, 2]],
          },
          attributeDictionaries: {},
        },
      ])
    );
    assertIdsAreDense(arcText);
    expect(arcText).toContain('IFCINDEXEDPOLYCURVE(');
    expect(arcText).toContain('IFCARCINDEX((1,2,3))');
    expect(arcText).not.toMatch(/^#\d+=IFCARCINDEX\(/m);

    const capillaBytes = fs.readFileSync(path.join(FIXTURES, 'capilla_quiroz_v17.skp'));
    const capillaScene = buildScene(
      capillaBytes.buffer.slice(
        capillaBytes.byteOffset,
        capillaBytes.byteOffset + capillaBytes.byteLength
      ) as ArrayBuffer
    );
    const capillaIfc = toIFC(capillaScene);
    assertIdsAreDense(capillaIfc);
    expect((capillaIfc.match(/=IFCANNOTATION\(/g) ?? []).length).toBe(20);

    const arcsBytes = fs.readFileSync(path.join(FIXTURES, 'arcs_2021.skp'));
    const arcsScene = buildScene(
      arcsBytes.buffer.slice(
        arcsBytes.byteOffset,
        arcsBytes.byteOffset + arcsBytes.byteLength
      ) as ArrayBuffer
    );
    const arcsIfc = toIFC(arcsScene);
    assertIdsAreDense(arcsIfc);
    expect((arcsIfc.match(/=IFCINDEXEDPOLYCURVE\(/g) ?? []).length).toBe(1);
    expect(arcsIfc).toContain('IFCARCINDEX((1,2,3))');
  });

  it('escapes control characters and 0x7F and rejects non-finite coordinates or scales', () => {
    expect(stepText('line1\r\nline2\t\x00\x1f\x7f')).toBe(
      'line1\\X2\\000D000A\\X0\\line2\\X2\\00090000001F007F\\X0\\'
    );
    const ctrlText = toIFC(
      meshScene('Wall\nOne\x7f', [0, 0, 0, 1, 0, 0, 0, 1, 0], [0, 1, 2], {
        properties: { 'note\tkey': 'a\nb\x7f' },
      })
    );
    expect(ctrlText).toContain('Wall\\X2\\000A\\X0\\One\\X2\\007F\\X0\\');
    expect(ctrlText).toContain('note\\X2\\0009\\X0\\key');
    expect(ctrlText).toContain('a\\X2\\000A\\X0\\b\\X2\\007F\\X0\\');
    const lines = ctrlText.split('\r\n');
    const dataStart = lines.indexOf('DATA;') + 1;
    const dataEnd = lines.indexOf('ENDSEC;', dataStart);
    for (let i = dataStart; i < dataEnd; i++) {
      expect(lines[i].startsWith('#')).toBe(true);
      expect(lines[i].endsWith(');')).toBe(true);
    }

    const good = meshScene('Wall', [0, 0, 0, 1, 0, 0, 0, 1, 0], [0, 1, 2]);
    expect(() => toIFC(good, Number.NaN)).toThrow(/scale must be finite/);
    expect(() => toIFC(good, Number.POSITIVE_INFINITY)).toThrow(/scale must be finite/);

    const badMesh = meshScene('Wall', [Number.NaN, 0, 0, 1, 0, 0, 0, 1, 0], [0, 1, 2]);
    expect(() => toIFC(badMesh)).toThrow(/coordinates must be finite/);

    const badCurve = curveScene([
      {
        name: 'C',
        definitionName: '',
        layer: 'Layer0',
        path: 'ROOT',
        nameIsGenerated: false,
        pointsM: [
          [0, 0, 0],
          [1e308, 0, 0],
        ],
        closed: false,
        arc: null,
        attributeDictionaries: {},
      },
    ]);
    expect(() => toIFC(badCurve, 1000)).toThrow(/coordinates must be finite/);

    const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'openskp-ifc-nan-'));
    const outPath = path.join(tmpDir, 'sub', 'bad.ifc');
    try {
      expect(() => exportIFC(badMesh, outPath)).toThrow(/coordinates must be finite/);
      expect(fs.existsSync(outPath)).toBe(false);
      expect(fs.existsSync(path.dirname(outPath))).toBe(false);
    } finally {
      fs.rmSync(tmpDir, { recursive: true, force: true });
    }
  });

  it('handles 400k-triangle toIFC quickly and > 2^24 distinct edges in meshIsClosed without throwing', { timeout: 60_000 }, () => {
    const buildGridTorus = (nx: number, ny: number) => {
      const vCount = nx * ny;
      const triCount = vCount * 2;
      const positions = new Float32Array(vCount * 3);
      for (let i = 0; i < nx; i++) {
        const x = i * 0.01;
        for (let j = 0; j < ny; j++) {
          const idx = (i * ny + j) * 3;
          positions[idx] = x;
          positions[idx + 1] = j * 0.01;
          positions[idx + 2] = ((i * 7 + j * 13) % 17) * 0.01;
        }
      }
      const indices = new Uint32Array(triCount * 3);
      let k = 0;
      for (let i = 0; i < nx; i++) {
        const i1 = (i + 1) % nx;
        const row0 = i * ny;
        const row1 = i1 * ny;
        for (let j = 0; j < ny; j++) {
          const j1 = (j + 1) % ny;
          const v00 = row0 + j;
          const v10 = row1 + j;
          const v01 = row0 + j1;
          const v11 = row1 + j1;
          indices[k++] = v00;
          indices[k++] = v10;
          indices[k++] = v11;
          indices[k++] = v00;
          indices[k++] = v11;
          indices[k++] = v01;
        }
      }
      return {
        geomName: 'Torus',
        materialIndex: 0,
        positions,
        normals: new Float32Array(0),
        uvs: new Float32Array(0),
        indices,
      };
    };

    // 500 x 400 grid torus = 200,000 vertices, 400,000 triangles (closed).
    {
      const torus400k = buildGridTorus(500, 400);
      const scene: SkpScene = {
        sceneHierarchy: {
          name: 'Root',
          definitionName: 'RootDef',
          layer: 'Layer0',
          positionMm: [0, 0, 0],
          properties: {},
          children: [],
        },
        meshIndex: {
          Torus: {
            name: 'Torus',
            definitionName: 'TorusDef',
            layer: 'Layer0',
            positionMm: [0, 0, 0],
            properties: {},
            path: 'ROOT / Torus',
          },
        },
        glbPrimitives: [torus400k],
        gltfMaterials: [{ pbrMetallicRoughness: { baseColorFactor: [0.5, 0.5, 0.5, 1.0] } }],
      };
      const ifc400k = toIFC(scene);
      expect(ifc400k).toContain(',.T.,');
    }

    // 2900 x 1930 grid torus = 5,597,000 vertices, 11,194,000 triangles,
    // 16,791,000 (> 2^24 = 16,777,216) distinct undirected edges.
    {
      const hugeTorus = buildGridTorus(2900, 1930);
      expect(meshIsClosed(hugeTorus)).toBe(true);
    }
  });
});
