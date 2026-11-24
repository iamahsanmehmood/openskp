import { describe, it, expect } from 'vitest';
import { toDXF, METRES_TO_INCHES } from '../src/dxf';
import { SkpScene } from '../src/model';

function crc32Ascii(text: string): number {
  let crc = 0xffffffff;
  for (let i = 0; i < text.length; i++) {
    crc ^= text.charCodeAt(i) & 0xff;
    for (let bit = 0; bit < 8; bit++) {
      crc = (crc >>> 1) ^ (crc & 1 ? 0xedb88320 : 0);
    }
  }
  return (crc ^ 0xffffffff) >>> 0;
}

function makeScene(prims: SkpScene['glbPrimitives']): SkpScene {
  return {
    sceneHierarchy: {
      name: 'Root',
      definitionName: 'RootDef',
      layer: 'Layer0',
      positionMm: [0, 0, 0],
      properties: {},
      children: [],
    },
    meshIndex: {},
    glbPrimitives: prims,
    gltfMaterials: [],
  };
}

function triangle(name: string): SkpScene['glbPrimitives'][number] {
  return {
    geomName: name,
    materialIndex: 0,
    positions: new Float32Array([0, 0, 0, 1, 0, 0, 0, 1, 0]),
    normals: new Float32Array([0, 0, 1, 0, 0, 1, 0, 0, 1]),
    uvs: new Float32Array([0, 0, 1, 0, 0, 1]),
    indices: new Uint32Array([0, 1, 2]),
  };
}

function surrogateEscape(bytes: number[]): string {
  let out = '';
  for (const b of bytes) {
    out += b < 0x80 ? String.fromCharCode(b) : String.fromCharCode(0xdc00 | b);
  }
  return out;
}

function check(ok: boolean, message: string): void {
  if (!ok) throw new Error(message);
}

function inspect(dxf: string): { layers: Set<string>; meshLayers: string[] } {
  for (let i = 0; i < dxf.length; i++) {
    check(dxf.charCodeAt(i) < 128, 'R2000 DXF contains raw non-ASCII bytes');
  }
  check(dxf.endsWith('\r\n'), 'DXF must end with CRLF');
  const lines = dxf.slice(0, -2).split('\r\n');
  check(lines.length % 2 === 0, 'incomplete DXF code/value pair');

  const layers = new Set<string>();
  const meshLayers: string[] = [];
  const references: string[] = [];
  let record = '';
  let tableCount = 0;
  let layerTable = false;
  let entity = false;

  for (let k = 0; k < lines.length; k += 2) {
    const group = Number.parseInt(lines[k].trim(), 10);
    const value = lines[k + 1];
    if (group >= 60 && group <= 79) {
      const iv = Number.parseInt(value, 10);
      check(iv >= -32768 && iv <= 32767, `16-bit DXF field overflow: ${iv}`);
    }
    if (group === 0) {
      record = value;
      entity = false;
    }
    if (group === 100 && value === 'AcDbEntity') entity = true;
    if (record === 'TABLE' && group === 2) layerTable = value === 'LAYER';
    if (record === 'TABLE' && layerTable && group === 70) {
      tableCount = Number.parseInt(value, 10);
    }
    if (record === 'LAYER' && group === 2) {
      check(!layers.has(value), `duplicate LAYER ${value}`);
      layers.add(value);
      check(value.length <= 2049, 'encoded layer exceeds R2000 string limit');
      const decoded: number[] = [];
      for (let i = 0; i < value.length; ) {
        if (value[i] === '\\') {
          check(
            value.slice(i, i + 3) === '\\U+' && i + 7 <= value.length,
            'bad Unicode escape',
          );
          decoded.push(Number.parseInt(value.slice(i + 3, i + 7), 16));
          i += 7;
        } else {
          const c = value.charCodeAt(i++);
          check(c >= 32 && c !== 127, 'control character in LAYER');
          decoded.push(c);
        }
      }
      check(
        decoded.length >= 1 && decoded.length <= 255,
        'invalid logical layer name length',
      );
      for (let i = 0; i < decoded.length; i++) {
        if (decoded[i] >= 0xd800 && decoded[i] <= 0xdbff) {
          i++;
          check(
            i < decoded.length && decoded[i] >= 0xdc00 && decoded[i] <= 0xdfff,
            'split Unicode surrogate pair',
          );
        } else {
          check(
            decoded[i] < 0xdc00 || decoded[i] > 0xdfff,
            'unpaired low surrogate',
          );
        }
      }
    }
    if (group === 8 && entity) {
      references.push(value);
      if (record === 'POLYLINE' || record === '3DFACE') {
        meshLayers.push(value);
      }
    }
  }
  check(tableCount === layers.size, 'incorrect LAYER table count');
  for (const ref of references) {
    check(layers.has(ref), `entity refers to missing layer ${ref}`);
  }
  return { layers, meshLayers };
}

describe('DXF 3D Exporter', () => {
  it('serializes SkpScene to 3D DXF format in polyface mode by default', () => {
    const back = { ...triangle('UnpaintedBack'), unpaintedBack: true };
    const scene = makeScene([triangle('Walls'), back]);
    const dxfText = toDXF(scene);
    expect(dxfText).toContain('$ACADVER');
    expect(dxfText).toContain('AC1015');
    expect(dxfText).toContain('POLYLINE');
    expect(dxfText).toContain('AcDbPolyFaceMesh');
    expect(dxfText).toContain('Walls');
    expect(dxfText).not.toContain('UnpaintedBack');
    expect(dxfText).toContain('EOF');
  });

  it('serializes SkpScene to 3DFACE format when mode is 3dface', () => {
    const scene = makeScene([triangle('Walls')]);
    const dxfText = toDXF(scene, METRES_TO_INCHES, '3dface');
    expect(dxfText).toContain('3DFACE');
    expect(dxfText).toContain('AcDbFace');
  });

  it('escapes Unicode layer names, deduplicates case-insensitively, and matches C++ reference bytes', () => {
    const chinese = 'mesh_27_ROOT__\u4e0a\u67dc(\u4fa7\u5305\u9876\u5e95)__\u9876\u677f_\u540a\u67dc';
    const escaped =
      'mesh_27_ROOT__\\U+4E0A\\U+67DC(\\U+4FA7\\U+5305\\U+9876\\U+5E95)__\\U+9876\\U+677F_\\U+540A\\U+67DC';
    const longName = '\u67dc'.repeat(260);
    const names = [
      chinese,
      'Caf\u00e9',
      '\u67dc\u{1f600}',
      `${longName}A`,
      `${longName}B`,
      `${'x'.repeat(245)}\u{1f600}${'x'.repeat(20)}`,
      'A/B',
      'A:B',
      'walls',
      'WALLS',
      '0',
      'Defpoints',
      '',
      '\t ',
      'line\nbreak\tname',
      chinese,
      'y'.repeat(300),
    ];
    const scene = makeScene(names.map((n) => triangle(n)));
    for (const mode of ['polyface', '3dface'] as const) {
      const parsed = inspect(toDXF(scene, 1.0, mode));
      expect(parsed.layers.has(escaped)).toBe(true);
      expect(parsed.layers.has('Caf\\U+00E9')).toBe(true);
      expect(parsed.layers.has('\\U+67DC\\U+D83D\\U+DE00')).toBe(true);
      expect(parsed.meshLayers.length).toBe(names.length);
      expect(parsed.meshLayers[0]).toBe(parsed.meshLayers[15]);
      const unique = parsed.meshLayers.filter((_, i) => i !== 15);
      const lowered = new Set(unique.map((m) => m.toLowerCase()));
      expect(lowered.size).toBe(unique.length);
    }

    const polyDxf = toDXF(scene, 1.0, 'polyface');
    expect(polyDxf.length).toBe(62908);
    expect(crc32Ascii(polyDxf)).toBe(0x561316a9);

    const faceDxf = toDXF(scene, 1.0, '3dface');
    expect(faceDxf.length).toBe(31833);
    expect(crc32Ascii(faceDxf)).toBe(0x89dcd865);

    const emptyDxf = toDXF(makeScene([]), METRES_TO_INCHES, 'polyface');
    expect(emptyDxf.length).toBe(16676);
    expect(crc32Ascii(emptyDxf)).toBe(0xfdeaa351);
  });

  it('falls back to 3DFACE when polyfaces exceed 32767 vertices or triangles', () => {
    const positions = new Float32Array(32768 * 3);
    positions[3] = 1.0;
    positions[7] = 1.0;
    const large: SkpScene['glbPrimitives'][number] = {
      geomName: 'Large mesh',
      materialIndex: 0,
      positions,
      normals: new Float32Array(32768 * 3),
      uvs: new Float32Array(32768 * 2),
      indices: new Uint32Array([0, 1, 32767]),
    };
    const manyIndices = new Uint32Array(32768 * 3);
    for (let i = 0; i < 32768; i++) {
      manyIndices[i * 3] = 0;
      manyIndices[i * 3 + 1] = 1;
      manyIndices[i * 3 + 2] = 2;
    }
    const many: SkpScene['glbPrimitives'][number] = {
      geomName: 'Many faces',
      materialIndex: 0,
      positions: new Float32Array([0, 0, 0, 1, 0, 0, 0, 1, 0]),
      normals: new Float32Array(9),
      uvs: new Float32Array(6),
      indices: manyIndices,
    };
    const dxf = toDXF(makeScene([large, many, triangle('Fits')]), 1.0, 'polyface');
    const parsed = inspect(dxf);
    expect(parsed.meshLayers.length).toBe(1 + 32768 + 1);
    expect(dxf.split('\r\nPOLYLINE\r\n').length - 1).toBe(1);
    expect(dxf.length).toBe(8863208);
    expect(crc32Ascii(dxf)).toBe(0x1445729a);
  });

  it('rejects malformed meshes and non-finite scales, and repairs malformed UTF-8 / surrogates', () => {
    for (let failure = 0; failure < 5; failure++) {
      const bad = triangle('Invalid mesh');
      if (failure === 0) bad.positions = new Float32Array([0, 0, 0, 1, 0, 0, 0, 1]);
      if (failure === 1) bad.indices = new Uint32Array([0, 1]);
      if (failure === 2) bad.indices = new Uint32Array([3, 1, 2]);
      if (failure === 3) bad.positions[0] = Number.NaN;
      const scale = failure === 4 ? Number.POSITIVE_INFINITY : 1.0;
      for (const mode of ['polyface', '3dface'] as const) {
        expect(() => toDXF(makeScene([bad]), scale, mode)).toThrow();
      }
    }

    const cases: [string, string][] = [
      [surrogateEscape([0x80]), '_'],
      [surrogateEscape([0xff]), '_'],
      [surrogateEscape([0xc0, 0xaf]), '__'],
      [surrogateEscape([0xe0, 0x80, 0xaf]), '_'],
      [surrogateEscape([0xf0, 0x80, 0x80, 0xaf]), '_'],
      [surrogateEscape([0xe4, 0x6f, 0x6b]), '_ok'],
      ['\ud800ok', '_ok'],
      [surrogateEscape([0x80, 0xf0, 0x9f, 0x98, 0x80]), '_\\U+D83D\\U+DE00'],
    ];
    for (const [bad, replacement] of cases) {
      const name = `before${bad}after`;
      const scene = makeScene([triangle(name), triangle('unaffected'), triangle(name)]);
      const parsed = inspect(toDXF(scene, 1.0, 'polyface'));
      expect(parsed.meshLayers).toEqual([
        `before${replacement}after`,
        'unaffected',
        `before${replacement}after`,
      ]);
    }
  });
});
