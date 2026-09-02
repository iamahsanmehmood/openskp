import { describe, it, expect } from 'vitest';
import { create, SkpWriteError, _internal, Point3 } from '../src/create';
import { parseSkp, buildScene } from '../src/index';

/**
 * Tests for create.ts - the from-scratch legacy (v17) .skp writer, ported
 * from Python's openskp.create test suite (packages/python/tests/test_create.py).
 *
 * Round-tripping a written file through this package's own trusted reader
 * (parseSkp) proves internal consistency and, for the byte-size and slot-
 * boundary assertions below, cross-checks directly against numbers Python's
 * suite already validated against real SketchUp-SDK-authored ground truth
 * (see each test's own comment for the specific Python assertion it mirrors).
 */

const SQUARE: Point3[] = [
  [0.0, 0.0, 0.0],
  [100.0, 0.0, 0.0],
  [100.0, 100.0, 0.0],
  [0.0, 100.0, 0.0],
];

function toBuffer(bytes: Uint8Array): ArrayBuffer {
  return bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer;
}

describe('Builder errors', () => {
  it('saving with no geometry throws', () => {
    expect(() => create().toBytes()).toThrow(/no geometry/);
  });

  it('a face with fewer than 3 points throws', () => {
    const builder = create();
    expect(() => builder.addFace([[0, 0, 0], [1, 0, 0]])).toThrow(/at least 3 points/);
  });

  it('collinear points throw', () => {
    const builder = create();
    expect(() => builder.addFace([[0, 0, 0], [1, 0, 0], [2, 0, 0]])).toThrow(/collinear/);
  });

  it('non-planar points throw', () => {
    const builder = create();
    expect(() =>
      builder.addFace([[0, 0, 0], [100, 0, 0], [100, 100, 0], [0, 100, 50]])
    ).toThrow(/not coplanar/);
  });

  it('addFace rejects a layer handle passed as material', () => {
    // The exact real-world mistake this guards against: a caller
    // accidentally passes a layer handle into the `material` option (e.g.
    // via an argument-order slip in a wrapper function around addFace).
    // Before this check, the layer's slot silently became a dangling
    // material reference - openskp's own reader tolerated it, but real
    // SketchUp rejected the resulting file outright.
    const builder = create();
    const layer = builder.addLayer('Layer0');
    expect(() => builder.addFace(SQUARE, { material: layer })).toThrow(/material/);
  });

  it('addFace rejects a material handle passed as layer', () => {
    const builder = create();
    const mat = builder.addMaterial('Red', [255, 0, 0]);
    expect(() => builder.addFace(SQUARE, { layer: mat })).toThrow(/layer/);
  });

  it('addFace rejects an unrelated backMaterial handle', () => {
    const builder = create();
    const layer = builder.addLayer('Layer0');
    expect(() => builder.addFace(SQUARE, { backMaterial: layer })).toThrow(/backMaterial/);
  });

  it('addFace rejects a handle from a different builder', () => {
    const otherBuilder = create();
    const strayMaterial = otherBuilder.addMaterial('Blue', [0, 0, 255]);
    const builder = create();
    expect(() => builder.addFace(SQUARE, { material: strayMaterial })).toThrow(/material/);
  });

  it('addInstance rejects a layer handle passed as material', () => {
    const builder = create();
    const layer = builder.addLayer('Layer0');
    const chair = builder.addComponentDefinition('Chair', (def) => def.addFace(SQUARE));
    expect(() => builder.addInstance(chair, { material: layer })).toThrow(/material/);
  });

  it('addGroup rejects an unrelated layer handle', () => {
    const builder = create();
    const mat = builder.addMaterial('Red', [255, 0, 0]);
    expect(() =>
      builder.addGroup((def) => def.addFace(SQUARE), { name: 'Table', layer: mat })
    ).toThrow(/layer/);
  });

  it('a component-scope addFace rejects an unrelated handle', () => {
    const builder = create();
    const layer = builder.addLayer('Layer0');
    expect(() =>
      builder.addComponentDefinition('Chair', (def) => {
        def.addFace(SQUARE, { material: layer });
      })
    ).toThrow(/material/);
  });

  it('addFace accepts a real material and layer', () => {
    const builder = create();
    const mat = builder.addMaterial('Red', [255, 0, 0]);
    const layer = builder.addLayer('MyLayer');
    builder.addFace(SQUARE, { material: mat, layer });
    expect(builder.toBytes().length).toBeGreaterThan(0);
  });
});

describe('Single face', () => {
  it('matches the ground-truth byte size Python confirmed against a real SDK-authored file', () => {
    // Mirrors Python's test_create.py TestSingleFace.test_matches_ground_truth_byte_size:
    // a single SQUARE face against the (byte-identical) blank scaffold produces
    // exactly 6149 bytes there too - this is a direct cross-language check that
    // both writers encode the identical geometry into the identical byte count.
    const builder = create();
    builder.addFace(SQUARE);
    expect(builder.toBytes().length).toBe(6149);
  });

  it('self-parses to the expected structure', () => {
    const builder = create();
    builder.addFace(SQUARE);
    const model = parseSkp(toBuffer(builder.toBytes()));
    expect(model.root.faces.length).toBe(1);
    expect(model.root.edges.length).toBe(4);
    const face = model.root.faces[0];
    expect(face.normal[0]).toBeCloseTo(0, 9);
    expect(face.normal[1]).toBeCloseTo(0, 9);
    expect(face.normal[2]).toBeCloseTo(1, 9);
  });

  it('hidden/soft/smooth flags round-trip', () => {
    const builder = create();
    builder.addFace(SQUARE, { hidden: true, softEdges: true, smoothEdges: true, hiddenEdges: true });
    const model = parseSkp(toBuffer(builder.toBytes()));
    expect(model.root.faces[0].hidden).toBe(true);
    expect(model.root.edges.every((e) => e.hidden && e.soft && e.smooth)).toBe(true);
  });

  it('default flags are off', () => {
    const builder = create();
    builder.addFace(SQUARE);
    const model = parseSkp(toBuffer(builder.toBytes()));
    expect(model.root.faces[0].hidden).toBe(false);
    expect(model.root.edges.every((e) => !e.hidden && !e.soft && !e.smooth)).toBe(true);
  });
});

describe('Multi-face', () => {
  const face1: Point3[] = [[0, 0, 0], [100, 0, 0], [100, 100, 0], [0, 100, 0]];
  const face2: Point3[] = [[100, 0, 0], [200, 0, 0], [200, 100, 0], [100, 100, 0]];

  it('shares vertices and edges across faces (7 unique edges, 2 faces)', () => {
    const builder = create();
    builder.addFace(face1);
    builder.addFace(face2);
    const model = parseSkp(toBuffer(builder.toBytes()));
    expect(model.root.edges.length).toBe(7);
    expect(model.root.faces.length).toBe(2);
    expect(model.root.vertices.length).toBe(6); // 4 + 4 - 2 shared
  });

  it('a large disjoint mesh shifts tail references without byte overflow (30 quads)', () => {
    // Mirrors TestMultiFace.test_large_mesh_shifts_tail_references_without_byte_overflow.
    const builder = create();
    for (let i = 0; i < 30; i++) {
      const x0 = i * 200.0;
      builder.addFace([
        [x0, 0, 0], [x0 + 100, 0, 0], [x0 + 100, 100, 0], [x0, 100, 0],
      ]);
    }
    const model = parseSkp(toBuffer(builder.toBytes()));
    expect(model.root.faces.length).toBe(30);
    expect(model.root.edges.length).toBe(120);
  });
});

describe('Concave polygons', () => {
  // L-shape starting at its reflex (concave) vertex - the worst case for a
  // plane-normal computation that only looks at the first 3 points.
  const L_SHAPE: Point3[] = [
    [50, 50, 0], [100, 50, 0], [100, 100, 0], [0, 100, 0], [0, 0, 0], [50, 0, 0],
  ];

  it('a reflex first vertex still gets the correct normal (Newell, not first-3-points)', () => {
    const builder = create();
    builder.addFace(L_SHAPE);
    const model = parseSkp(toBuffer(builder.toBytes()));
    const n = model.root.faces[0].normal;
    expect(n[0]).toBeCloseTo(0, 9);
    expect(n[1]).toBeCloseTo(0, 9);
    expect(n[2]).toBeCloseTo(1, 9);
  });
});

describe('Auto-triangulate', () => {
  const NEAR_QUAD: Point3[] = [[0, 0, 0], [100, 0, 0], [100, 100, 5], [0, 100, 0]];

  it('a non-planar quad is rejected without autoTriangulate', () => {
    const builder = create();
    expect(() => builder.addFace(NEAR_QUAD)).toThrow(/not coplanar/);
  });

  it('a non-planar quad is fan-triangulated into 2 real planar triangles with autoTriangulate', () => {
    const builder = create();
    builder.addFace(NEAR_QUAD, { autoTriangulate: true });
    const model = parseSkp(toBuffer(builder.toBytes()));
    expect(model.root.faces.length).toBe(2);
  });

  it('already-planar input is written as a single face either way', () => {
    const builder = create();
    builder.addFace(SQUARE, { autoTriangulate: true });
    const model = parseSkp(toBuffer(builder.toBytes()));
    expect(model.root.faces.length).toBe(1);
  });

  it('autoTriangulate cannot be combined with frontUv/backUv', () => {
    const builder = create();
    expect(() =>
      builder.addFace(NEAR_QUAD, {
        autoTriangulate: true,
        frontUv: [[[0, 0, 0], [0, 0]], [[50, 0, 0], [1, 0]], [[0, 50, 0], [0, 1]]],
      })
    ).toThrow(/autoTriangulate/);
  });
});

describe('Face holes', () => {
  const WALL: Point3[] = [[0, 0, 0], [200, 0, 0], [200, 100, 0], [0, 100, 0]];
  const WINDOW: Point3[] = [[80, 30, 0], [120, 30, 0], [120, 70, 0], [80, 70, 0]];

  it('a face with one hole self-parses with 2 loops', () => {
    const builder = create();
    builder.addFace(WALL, { holes: [WINDOW] });
    const model = parseSkp(toBuffer(builder.toBytes()));
    expect(model.root.faces[0].loops.length).toBe(2);
    // outer boundary (4) + hole (4) = 8 unique edges, since they share no vertices
    expect(model.root.edges.length).toBe(8);
  });

  it('a hole off the face plane throws', () => {
    const builder = create();
    const badWindow: Point3[] = [[80, 30, 5], [120, 30, 5], [120, 70, 5], [80, 70, 5]];
    expect(() => builder.addFace(WALL, { holes: [badWindow] })).toThrow(/plane/);
  });

  it('multiple holes in one face all self-parse', () => {
    const builder = create();
    const window2: Point3[] = [[20, 20, 0], [40, 20, 0], [40, 40, 0], [20, 40, 0]];
    builder.addFace(WALL, { holes: [WINDOW, window2] });
    const model = parseSkp(toBuffer(builder.toBytes()));
    expect(model.root.faces[0].loops.length).toBe(3);
  });
});

describe('Materials', () => {
  it('registers a solid material and applies it to a face', () => {
    const builder = create();
    const red = builder.addMaterial('Red', [255, 0, 0]);
    builder.addFace(SQUARE, { material: red });
    const model = parseSkp(toBuffer(builder.toBytes()));
    expect(model.materials.length).toBe(1);
    expect(model.materials[0].name).toBe('Red');
    expect(model.materials[0].color).toEqual({ r: 255, g: 0, b: 0, a: 255 });
  });

  it('re-registering the same name returns the same handle, not a duplicate', () => {
    const builder = create();
    const a = builder.addMaterial('Red', [255, 0, 0]);
    const b = builder.addMaterial('Red', [0, 0, 0]); // ignored - already registered
    expect(a).toBe(b);
    builder.addFace(SQUARE, { material: a });
    const model = parseSkp(toBuffer(builder.toBytes()));
    expect(model.materials.length).toBe(1);
  });

  it('front and back materials are independently applied', () => {
    const builder = create();
    const front = builder.addMaterial('Front', [255, 0, 0]);
    const back = builder.addMaterial('Back', [0, 255, 0]);
    builder.addFace(SQUARE, { material: front, backMaterial: back });
    const model = parseSkp(toBuffer(builder.toBytes()));
    const face = model.root.faces[0];
    expect(model.materialsById.get(face.materialId as number)?.name).toBe('Front');
    expect(model.materialsById.get(face.backMaterialId as number)?.name).toBe('Back');
  });

  it('addMaterial after addFace throws', () => {
    const builder = create();
    builder.addFace(SQUARE);
    expect(() => builder.addMaterial('TooLate', [0, 0, 0])).toThrow(/before any addFace/);
  });

  it('rgba with alpha round-trips, 3-channel defaults to opaque', () => {
    const builder = create();
    const translucent = builder.addMaterial('Glass', [0, 128, 255, 128]);
    const opaque = builder.addMaterial('Solid', [10, 20, 30]);
    builder.addFace(SQUARE, { material: translucent });
    builder.addFace(
      [[0, 0, 10], [100, 0, 10], [100, 100, 10], [0, 100, 10]],
      { material: opaque }
    );
    const model = parseSkp(toBuffer(builder.toBytes()));
    const glass = model.materials.find((m) => m.name === 'Glass')!;
    const solid = model.materials.find((m) => m.name === 'Solid')!;
    expect(glass.color.a).toBe(128);
    expect(solid.color).toEqual({ r: 10, g: 20, b: 30, a: 255 });
  });

  it('carries opacity on a solid material (openskp#252)', () => {
    const builder = create();
    const glass = builder.addMaterial('Glass', [200, 220, 255], 0.35);
    builder.addFace(SQUARE, { material: glass });
    const model = parseSkp(toBuffer(builder.toBytes()));
    const mat = model.materials.find((m) => m.name === 'Glass')!;
    expect(mat.transparency).toBeCloseTo(0.35);
  });

  it('omitted opacity stays fully opaque', () => {
    const builder = create();
    const red = builder.addMaterial('Red', [255, 0, 0]);
    builder.addFace(SQUARE, { material: red });
    const model = parseSkp(toBuffer(builder.toBytes()));
    const mat = model.materials.find((m) => m.name === 'Red')!;
    expect(mat.transparency).toBe(1.0);
  });
});

describe('Textures', () => {
  // A minimal, dependency-free 4x4 solid-color PNG (raw deflate stored
  // blocks, no compression library needed) - mirrors Python's
  // _make_test_png fixture generator, avoiding an image-library dependency
  // just for a test fixture.
  function makeTestPng(size = 4, rgb: [number, number, number] = [200, 50, 50]): Uint8Array {
    function crc32(buf: number[]): number {
      let c: number;
      const table: number[] = [];
      for (let n = 0; n < 256; n++) {
        c = n;
        for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
        table[n] = c >>> 0;
      }
      let crc = 0xffffffff;
      for (const b of buf) crc = table[(crc ^ b) & 0xff] ^ (crc >>> 8);
      return (crc ^ 0xffffffff) >>> 0;
    }
    function u32be(v: number): number[] {
      return [(v >>> 24) & 0xff, (v >>> 16) & 0xff, (v >>> 8) & 0xff, v & 0xff];
    }
    function chunk(tag: number[], data: number[]): number[] {
      return [...u32be(data.length), ...tag, ...data, ...u32be(crc32([...tag, ...data]))];
    }
    // Raw, uncompressed zlib stream: zlib header (0x78 0x01) + one stored
    // (uncompressed) deflate block per scanline.
    const rawRows: number[][] = [];
    for (let y = 0; y < size; y++) {
      const row = [0]; // filter type 0 (none)
      for (let x = 0; x < size; x++) row.push(...rgb);
      rawRows.push(row);
    }
    const rawData = rawRows.flat();
    let adler1 = 1;
    let adler2 = 0;
    for (const b of rawData) {
      adler1 = (adler1 + b) % 65521;
      adler2 = (adler2 + adler1) % 65521;
    }
    const deflate: number[] = [];
    const CHUNK = 65535;
    for (let i = 0; i < rawData.length; i += CHUNK) {
      const slice = rawData.slice(i, i + CHUNK);
      const isFinal = i + CHUNK >= rawData.length;
      deflate.push(isFinal ? 1 : 0);
      const len = slice.length;
      deflate.push(len & 0xff, (len >> 8) & 0xff, ~len & 0xff, (~len >> 8) & 0xff);
      deflate.push(...slice);
    }
    const zlibStream = [0x78, 0x01, ...deflate, (adler2 >> 8) & 0xff, adler2 & 0xff, (adler1 >> 8) & 0xff, adler1 & 0xff];
    const sig = [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a];
    const ihdr = chunk([0x49, 0x48, 0x44, 0x52], [...u32be(size), ...u32be(size), 8, 2, 0, 0, 0]);
    const idat = chunk([0x49, 0x44, 0x41, 0x54], zlibStream);
    const iend = chunk([0x49, 0x45, 0x4e, 0x44], []);
    return Uint8Array.from([...sig, ...ihdr, ...idat, ...iend]);
  }

  it('registers a PNG-textured material and applies it to a face', () => {
    const builder = create();
    const png = makeTestPng();
    const brick = builder.addTextureMaterial('Brick', png, 'brick.png');
    builder.addFace(SQUARE, { material: brick });
    const model = parseSkp(toBuffer(builder.toBytes()));
    const mat = model.materials.find((m) => m.name === 'Brick')!;
    expect(mat.texture).not.toBeNull();
    expect(mat.texture!.data).not.toBeNull();
    expect(Array.from(mat.texture!.data as Uint8Array)).toEqual(Array.from(png));
  });

  it('an unrecognized image format throws', () => {
    const builder = create();
    expect(() => builder.addTextureMaterial('Bogus', Uint8Array.from([1, 2, 3, 4]))).toThrow(/unrecognized image format/);
  });

  it('carries the applied width and height on a textured material (openskp#252)', () => {
    // Real SketchUp writes the material's own tile size in BOTH axes (a
    // file authored in SketchUp Web carries 8.0 x 16.0 for a brick); a
    // texture applied without positioning carries no per-face UV record,
    // so this pair IS its mapping.
    const builder = create();
    const png = makeTestPng();
    const brick = builder.addTextureMaterial('Brick', png, 'brick.png', 16.0, 8.0);
    builder.addFace(SQUARE, { material: brick });
    const model = parseSkp(toBuffer(builder.toBytes()));
    const mat = model.materials.find((m) => m.name === 'Brick')!;
    expect(mat.texture!.width).toBe(8.0);
    expect(mat.texture!.height).toBe(16.0);
  });

  it('applied width and height both default to 1.0', () => {
    const builder = create();
    const png = makeTestPng();
    const brick = builder.addTextureMaterial('Brick', png, 'brick.png');
    builder.addFace(SQUARE, { material: brick });
    const model = parseSkp(toBuffer(builder.toBytes()));
    const mat = model.materials.find((m) => m.name === 'Brick')!;
    expect(mat.texture!.width).toBe(1.0);
    expect(mat.texture!.height).toBe(1.0);
  });

  it('carries opacity on a textured material (openskp#252)', () => {
    const builder = create();
    const png = makeTestPng();
    const voile = builder.addTextureMaterial('Voile', png, 'voile.png', undefined, undefined, 0.5);
    builder.addFace(SQUARE, { material: voile });
    const model = parseSkp(toBuffer(builder.toBytes()));
    const mat = model.materials.find((m) => m.name === 'Voile')!;
    expect(mat.transparency).toBeCloseTo(0.5);
  });

  it('addImage places a real Image entity, not a plain textured face', () => {
    const builder = create();
    const png = makeTestPng();
    builder.addImage(png, 48, 36, {
      translation: [0, 0, 40],
      rotation: { axis: [1, 0, 0], angleRadians: Math.PI / 2 },
    });
    const model = parseSkp(toBuffer(builder.toBytes()));

    const imageDefs = [...model.definitions.values()].filter((d) => d.isImage);
    expect(imageDefs.length).toBe(1);
    expect(imageDefs[0].faces.length).toBe(1);
    expect(model.root.instances.length).toBe(1);
    expect(model.root.instances[0].refIdx).toBe(imageDefs[0].id);
  });
});

describe('UV positioning', () => {
  it('front-only positioning leaves the back side as identity', () => {
    const builder = create();
    const brick = builder.addMaterial('Brick', [180, 90, 60]);
    builder.addFace(SQUARE, {
      material: brick,
      frontUv: [
        [[0, 0, 0], [0.0, 0.0]],
        [[50, 0, 0], [1.0, 0.0]],
        [[0, 50, 0], [0.0, 1.0]],
      ],
    });
    const model = parseSkp(toBuffer(builder.toBytes()));
    const face = model.root.faces[0];
    expect(face.uvTransform).not.toBeNull();
    expect(face.uvTransformBack).toEqual([1, 0, 0, 0, 1, 0, 0, 0, 1]);
  });

  it('a degenerate (collinear-uv) correspondence throws', () => {
    const builder = create();
    expect(() =>
      builder.addFace(SQUARE, {
        frontUv: [
          [[0, 0, 0], [0.0, 0.0]],
          [[50, 0, 0], [1.0, 0.0]],
          [[0, 50, 0], [2.0, 0.0]], // collinear in uv-space
        ],
      })
    ).toThrow(/collinear/);
  });
});

describe('Attribute dicts', () => {
  it('face attributes round-trip through the dynamic_attributes-style dictionary', () => {
    const builder = create();
    builder.addFace(SQUARE, {
      attributes: { part_number: 'A-100', qty: 4, weight: 2.5 },
      attributeDictName: 'attributes',
    });
    // No public reader surface exposes face-level attributes yet (matching
    // Python's own reader gap - edit.py's docstring notes this explicitly),
    // so this test only confirms the write path doesn't corrupt the file:
    // it must still self-parse with the expected face/edge shape.
    const model = parseSkp(toBuffer(builder.toBytes()));
    expect(model.root.faces.length).toBe(1);
    expect(model.root.edges.length).toBe(4);
  });

  it('an out-of-range int widens to float64 rather than throwing (documented TS judgment call)', () => {
    const builder = create();
    // 2**31 is out of signed-32-bit range - Python's writer raises for an
    // explicit `int` here; TypeScript has no int/float type distinction,
    // so this writer widens to float64 instead (see AttributeValue's docs).
    expect(() => builder.addFace(SQUARE, { attributes: { big: 2 ** 31 } })).not.toThrow();
  });

  it('a boolean attribute value throws', () => {
    const builder = create();
    expect(() => builder.addFace(SQUARE, { attributes: { flag: true as unknown as number } })).toThrow(/bool/);
  });
});

describe('Curved edges', () => {
  it('addCircle self-parses as a closed loop of numSegments edges', () => {
    const builder = create();
    builder.addCircle([50, 50, 0], [0, 0, 1], 40, { numSegments: 16 });
    const model = parseSkp(toBuffer(builder.toBytes()));
    expect(model.root.faces.length).toBe(1);
    expect(model.root.edges.length).toBe(16);
    const n = model.root.faces[0].normal;
    expect(n[2]).toBeCloseTo(1, 6);
  });

  it('addCircle rejects an out-of-range segment count', () => {
    const builder = create();
    expect(() => builder.addCircle([0, 0, 0], [0, 0, 1], 10, { numSegments: 2 })).toThrow(/num_segments/);
  });

  it('addArc self-parses as an open chain of numSegments edges (no face)', () => {
    const builder = create();
    builder.addArc([50, 50, 0], [0, 0, 1], 40, 0, Math.PI / 2, { numSegments: 8 });
    const model = parseSkp(toBuffer(builder.toBytes()));
    expect(model.root.faces.length).toBe(0);
    expect(model.root.edges.length).toBe(8);
  });

  it('addArc with equal start/end angle throws', () => {
    const builder = create();
    expect(() => builder.addArc([0, 0, 0], [0, 0, 1], 10, 0, 0)).toThrow(/must differ/);
  });

  it('addPolyline self-parses as an open chain, closed=true connects the loop', () => {
    const open = create();
    open.addPolyline([[0, 0, 0], [10, 10, 0], [20, 0, 0], [30, 10, 0]]);
    const openModel = parseSkp(toBuffer(open.toBytes()));
    expect(openModel.root.edges.length).toBe(3);

    const closed = create();
    closed.addPolyline([[0, 0, 0], [10, 10, 0], [20, 0, 0], [30, 10, 0]], { closed: true });
    const closedModel = parseSkp(toBuffer(closed.toBytes()));
    expect(closedModel.root.edges.length).toBe(4);
  });

  it('a polyline needs at least 2 points', () => {
    const builder = create();
    expect(() => builder.addPolyline([[0, 0, 0]])).toThrow(/at least 2 points/);
  });
});

describe('Component definitions', () => {
  it('a basic definition and instance self-parse with the right transform', () => {
    const builder = create();
    const chair = builder.addComponentDefinition('Chair', (def) => {
      def.addFace(SQUARE);
    });
    builder.addInstance(chair);
    const model = parseSkp(toBuffer(builder.toBytes()));
    expect(model.root.instances.length).toBe(1);
    const inst = model.root.instances[0];
    expect(inst.name).toBe('Chair');
    // identity 3x3 + zero translation + trailing 1.0
    expect(inst.matrix.slice(0, 13)).toEqual([1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1]);
  });

  it('multiple instances share one definition', () => {
    const builder = create();
    const chair = builder.addComponentDefinition('Chair', (def) => {
      def.addFace(SQUARE);
    });
    for (let i = 0; i < 5; i++) {
      builder.addInstance(chair, { name: `Chair${i}`, translation: [i * 40, 0, 0] });
    }
    const model = parseSkp(toBuffer(builder.toBytes()));
    expect(model.root.instances.length).toBe(5);
    expect(model.definitions.size).toBe(1);
    const translations = model.root.instances.map((i) => i.matrix[9]).sort((a, b) => a - b);
    expect(translations).toEqual([0, 40, 80, 120, 160]);
  });

  it('a transform matrix is applied', () => {
    const builder = create();
    const post = builder.addComponentDefinition('Post', (def) => {
      def.addFace(SQUARE);
    });
    builder.addInstance(post, { matrix3x3: [2, 0, 0, 0, 1, 0, 0, 0, 1] });
    const model = parseSkp(toBuffer(builder.toBytes()));
    expect(model.root.instances[0].matrix[0]).toBe(2);
  });

  it('a rotation option produces the same result as a hand-derived matrix', () => {
    const builder = create();
    const post = builder.addComponentDefinition('Post', (def) => {
      def.addFace(SQUARE);
    });
    builder.addInstance(post, { rotation: { axis: [0, 0, 1], angleRadians: Math.PI / 2 } });
    const model = parseSkp(toBuffer(builder.toBytes()));
    const m = model.root.instances[0].matrix;
    // Rotating +X by 90 degrees around +Z gives +Y: row-major m maps
    // (1,0,0) -> (m[0], m[3], m[6]).
    expect(m[0]).toBeCloseTo(0, 9);
    expect(m[3]).toBeCloseTo(1, 9);
  });

  it('an empty definition throws', () => {
    const builder = create();
    expect(() => builder.addComponentDefinition('Empty', () => {})).toThrow(/no geometry/);
  });

  it('adding a face to an already-closed definition throws', () => {
    const builder = create();
    let captured: any;
    const chair = builder.addComponentDefinition('Chair', (def) => {
      def.addFace(SQUARE);
      captured = def;
    });
    expect(chair).toBe(captured);
    expect(() => chair.addFace(SQUARE)).toThrow(/already closed/);
  });

  it('addMaterial after a definition has started throws', () => {
    const builder = create();
    builder.addComponentDefinition('Chair', (def) => def.addFace(SQUARE));
    expect(() => builder.addMaterial('TooLate', [0, 0, 0])).toThrow(/before any addComponentDefinition/);
  });

  it('addComponentDefinition after addFace throws', () => {
    const builder = create();
    builder.addFace(SQUARE);
    expect(() => builder.addComponentDefinition('TooLate', () => {})).toThrow(/before any addFace\/addInstance/);
  });

  it("a definition's own vertex/edge sharing never leaks to the root model", () => {
    const builder = create();
    const chair = builder.addComponentDefinition('Chair', (def) => def.addFace(SQUARE));
    builder.addFace(SQUARE); // same coordinates, root level
    builder.addInstance(chair);
    const model = parseSkp(toBuffer(builder.toBytes()));
    expect(model.root.faces.length).toBe(1);
    expect(model.root.edges.length).toBe(4);
  });

  it('instance dynamic attributes round-trip through Instance.properties', () => {
    // Was reader-side broken (extractLegacyDynamicProperties compared the
    // *class* name ar.readObject returns for each CAttributeContainer
    // child - always 'CAttributeNamed' - against the dictionary's own
    // name instead of comparing value.name, so 'dynamic_attributes' was
    // never recognized) - fixed 2026-08-26, ported from the same fix in
    // Python's legacy.py. Now genuinely round-trips.
    const builder = create();
    const chair = builder.addComponentDefinition('Chair', (def) => def.addFace(SQUARE));
    builder.addInstance(chair, { attributes: { sku: 'CH-1', count: 3 }, attributeDictName: 'dynamic_attributes' });
    const model = parseSkp(toBuffer(builder.toBytes()));
    expect(model.root.instances.length).toBe(1);
    expect(model.root.instances[0].properties).toEqual({ sku: 'CH-1', count: '3' });
  });
});

describe('Groups', () => {
  it('a basic group places itself on close', () => {
    const builder = create();
    builder.addGroup(
      (table) => {
        table.addFace(SQUARE);
      },
      { name: 'Table', translation: [50, 0, 0] }
    );
    const model = parseSkp(toBuffer(builder.toBytes()));
    expect(model.root.instances.length).toBe(1);
    const inst = model.root.instances[0];
    expect(inst.name).toBe('Table');
    expect(inst.matrix.slice(9, 12)).toEqual([50, 0, 0]);
  });

  it('a hidden group round-trips its hidden flag', () => {
    const builder = create();
    builder.addGroup((table) => table.addFace(SQUARE), { name: 'Table', hidden: true });
    const model = parseSkp(toBuffer(builder.toBytes()));
    expect(model.root.instances[0].hidden).toBe(true);
  });

  it('a group without geometry throws', () => {
    const builder = create();
    expect(() => builder.addGroup(() => {}, { name: 'Empty' })).toThrow(/no geometry/);
  });

  it('the default group name is "Group"', () => {
    const builder = create();
    builder.addGroup((g) => g.addFace(SQUARE));
    const model = parseSkp(toBuffer(builder.toBytes()));
    expect(model.root.instances[0].name).toBe('Group');
  });

  it('groups and component-definition instances coexist at the root', () => {
    const builder = create();
    const chair = builder.addComponentDefinition('Chair', (def) => def.addFace(SQUARE));
    builder.addGroup((table) => table.addFace(SQUARE), { name: 'Table', translation: [100, 0, 0] });
    builder.addInstance(chair, { translation: [0, 100, 0] });
    const model = parseSkp(toBuffer(builder.toBytes()));
    expect(model.root.instances.length).toBe(2);
  });

  it('many definitions, instances, and groups together self-parse at scale', () => {
    const builder = create();
    const defs = [];
    for (let d = 0; d < 20; d++) {
      defs.push(builder.addComponentDefinition(`Def${d}`, (comp) => comp.addFace(SQUARE)));
    }
    for (let g = 0; g < 10; g++) {
      builder.addGroup((grp) => grp.addFace(SQUARE), { name: `Grp${g}`, translation: [g * 30, 500, 0] });
    }
    for (let i = 0; i < 40; i++) {
      builder.addInstance(defs[i % 20], { name: `Inst${i}`, translation: [i * 25, 1000, 0] });
    }
    const model = parseSkp(toBuffer(builder.toBytes()));
    expect(model.root.instances.length).toBe(50); // 10 groups + 40 instances
    // 20 explicit definitions + 10 group definitions (a group is backed by
    // its own definition too, just self-placing - see addGroup's docs).
    expect(model.definitions.size).toBe(30);
    const defRefs = new Set(
      model.root.instances.filter((i) => i.name.startsWith('Inst')).map((i) => i.refIdx)
    );
    expect(defRefs.size).toBe(20);
  });
});

describe('Nested definitions and groups', () => {
  it('a definition nests instances of another definition; root sees only the outer one', () => {
    const builder = create();
    const wheel = builder.addComponentDefinition('Wheel', (def) => def.addFace(SQUARE));
    const car = builder.addComponentDefinition('Car', (def) => {
      def.addInstance(wheel, { translation: [0, 0, 0] });
      def.addInstance(wheel, { translation: [200, 0, 0] });
    });
    builder.addInstance(car);
    const model = parseSkp(toBuffer(builder.toBytes()));
    expect(model.root.instances.length).toBe(1);
    expect(model.root.instances[0].name).toBe('Car');
    // Look up "Car" by name instead of assuming a specific numeric id.
    const carDefinition = [...model.definitions.values()].find((d) => d.name === 'Car')!;
    expect(carDefinition.instances.length).toBe(2);
  });

  it('a definition cannot nest an instance of itself', () => {
    const builder = create();
    expect(() => {
      builder.addComponentDefinition('Self', (def) => {
        expect(() => def.addInstance(def)).toThrow(/cannot nest an instance of itself/);
        def.addFace(SQUARE);
      });
    }).not.toThrow();
  });

  it('a group can be nested as a group-instance inside a definition', () => {
    const builder = create();
    const engine = builder.addComponentDefinition('Engine', (def) => def.addFace(SQUARE));
    const car = builder.addComponentDefinition('Car', (def) => {
      def.addFace([[0, 0, 0], [150, 0, 0], [150, 60, 0], [0, 60, 0]]);
      def.addGroupInstance(engine, { translation: [50, 0, 10] });
    });
    builder.addInstance(car);
    const model = parseSkp(toBuffer(builder.toBytes()));
    const carDefinition = [...model.definitions.values()].find((d) => d.name === 'Car')!;
    expect(carDefinition.instances.length).toBe(1);
    expect(carDefinition.faces.length).toBe(1);
  });

  it('buildScene keeps each nested level\'s own instance name in meshIndex (openskp#240)', () => {
    // Regression: each mesh's own name must reflect the specific instance
    // that placed its OWN definition, not an ancestor's - matching how
    // sceneHierarchy already builds each InstanceNode from that same
    // instance directly. A prior bug backfilled meshIndex by a substring
    // match on the sanitized path string; since a shallow instance's path
    // is always a string prefix of every deeper descendant's path too,
    // the shallowest instance's own name silently overwrote every mesh
    // beneath it as recursion unwound.
    const builder = create();
    const leaf = builder.addComponentDefinition('Leaf', (def) =>
      def.addFace([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]])
    );
    const middle = builder.addComponentDefinition('Middle', (def) => {
      def.addFace([[0, 0, 10], [1, 0, 10], [1, 1, 10], [0, 1, 10]]);
      def.addInstance(leaf, { name: 'LeafInstance' });
    });
    const outer = builder.addComponentDefinition('Outer', (def) => {
      def.addFace([[0, 0, 20], [1, 0, 20], [1, 1, 20], [0, 1, 20]]);
      def.addInstance(middle, { name: 'MiddleInstance' });
    });
    builder.addInstance(outer, { name: 'OuterInstance' });

    const scene = buildScene(toBuffer(builder.toBytes()));
    const names = Object.values(scene.meshIndex).map((m) => m.name).sort();
    expect(names).toEqual(['LeafInstance', 'MiddleInstance', 'OuterInstance']);
  });
});

describe('Instance rotation', () => {
  it('rejects passing both matrix3x3 and rotation', () => {
    const builder = create();
    const post = builder.addComponentDefinition('Post', (def) => def.addFace(SQUARE));
    expect(() =>
      builder.addInstance(post, {
        matrix3x3: [1, 0, 0, 0, 1, 0, 0, 0, 1],
        rotation: { axis: [0, 0, 1], angleRadians: 1 },
      })
    ).toThrow(/at most one/);
  });

  it('a zero-vector rotation axis throws', () => {
    const builder = create();
    const post = builder.addComponentDefinition('Post', (def) => def.addFace(SQUARE));
    expect(() => builder.addInstance(post, { rotation: { axis: [0, 0, 0], angleRadians: 1 } })).toThrow(/zero vector/);
  });
});

describe('Layers', () => {
  it('registers a layer and applies it to a face', () => {
    const builder = create();
    const roof = builder.addLayer('Roof', { color: [10, 20, 30], hidden: true });
    builder.addFace(SQUARE, { layer: roof });
    const model = parseSkp(toBuffer(builder.toBytes()));
    const layer = model.layers.find((l) => l.name === 'Roof')!;
    expect(layer.color).toEqual({ r: 10, g: 20, b: 30 });
    expect(layer.hidden).toBe(true);
  });

  it('re-registering the same layer name returns the same handle', () => {
    const builder = create();
    const a = builder.addLayer('Roof');
    const b = builder.addLayer('Roof', { hidden: true }); // ignored - already registered
    expect(a).toBe(b);
  });

  it('addLayer after addFace throws', () => {
    const builder = create();
    builder.addFace(SQUARE);
    expect(() => builder.addLayer('TooLate')).toThrow(/before any addFace/);
  });

  it('many layers self-parse with distinct names', () => {
    const builder = create();
    const layers = [];
    // Start at 1, not 0 - "Layer0" would collide with the scaffold's own
    // pre-existing default layer name; the reader's public model keys
    // layers by name, so two same-named layers would collapse into one
    // entry there even though the writer legitimately produced two
    // distinct records (real SketchUp allows duplicate layer names too).
    for (let i = 1; i <= 25; i++) layers.push(builder.addLayer(`Layer${i}`));
    builder.addFace(SQUARE, { layer: layers[10] });
    const model = parseSkp(toBuffer(builder.toBytes()));
    expect(model.layers.length).toBe(25 + 1); // + the scaffold's own Layer0
  });
});

describe('Kitchen sink', () => {
  it('materials + layers + definitions + groups + curves + attributes all together', () => {
    const builder = create();
    const red = builder.addMaterial('Red', [255, 0, 0]);
    const roof = builder.addLayer('Roof');
    const chair = builder.addComponentDefinition('Chair', (def) => {
      def.addFace(SQUARE, { material: red });
      def.addCircle([50, 50, 0], [0, 0, 1], 10, { numSegments: 12 });
    });
    // Groups/definitions must all be declared before any root-level
    // addFace/addInstance call - they splice into the file earlier, so
    // their own slot numbering depends on nothing after them having
    // started yet (see SkpBuilder.addGroup's docs). addGroup here, then
    // addInstance/addFace after both are done.
    builder.addGroup((g) => g.addFace(SQUARE), { translation: [300, 0, 0] });
    builder.addInstance(chair, { translation: [0, 0, 0], layer: roof });
    builder.addFace(SQUARE, { material: red, layer: roof, attributes: { note: 'root face' } });
    builder.addArc([500, 0, 0], [0, 0, 1], 30, 0, Math.PI);
    const model = parseSkp(toBuffer(builder.toBytes()));
    expect(model.materials.length).toBe(1);
    expect(model.layers.length).toBe(1 + 1); // Roof + scaffold Layer0
    expect(model.definitions.size).toBe(2); // Chair + the group's own backing definition
    expect(model.root.instances.length).toBe(2); // the group + the chair instance
    expect(model.root.faces.length).toBe(1); // the root-level face
    expect(model.root.edges.length).toBeGreaterThan(0);
  });
});

describe('Slot-boundary encoding (0x7FFF)', () => {
  // Direct unit tests mirroring Python's TestSlotBoundaryEncoding - a slot
  // of exactly 0x7FFF is unrepresentable in the short 2-byte form no
  // matter which of the two short encodings would otherwise apply, since
  // it collides with a reserved marker value either way.
  const { ArchiveWriter, shiftRef } = _internal;

  it('a backref below the boundary uses the short form', () => {
    const w = new ArchiveWriter(1, {});
    w.writeBackref(0x7ffe);
    expect(w.bytes).toEqual([0xfe, 0x7f]);
  });

  it('a backref at the boundary uses the escape form, not the collision bytes', () => {
    const w = new ArchiveWriter(1, {});
    w.writeBackref(0x7fff);
    expect(w.bytes).toEqual([0xff, 0x7f, 0xff, 0x7f, 0x00, 0x00]);
    expect(w.bytes).not.toEqual([0xff, 0x7f]);
  });

  it('newOfKnownClass below the boundary uses the short class-ref form', () => {
    const w = new ArchiveWriter(1, { Foo: 0x7ffe });
    w.newOfKnownClass('Foo');
    expect(w.bytes).toEqual([0xfe, 0xff]); // 0x8000 | 0x7FFE = 0xFFFE
  });

  it('newOfKnownClass at the boundary uses the escape form, never 0xFFFF', () => {
    const w = new ArchiveWriter(1, { Foo: 0x7fff });
    w.newOfKnownClass('Foo');
    expect(w.bytes.slice(0, 2)).toEqual([0xff, 0x7f]);
    const tag = w.bytes[0] | (w.bytes[1] << 8);
    expect(tag).not.toBe(0xffff);
    const val = w.bytes[2] | (w.bytes[3] << 8) | (w.bytes[4] << 16) | (w.bytes[5] << 24);
    expect(val >>> 0).toBe((0x80000000 | 0x7fff) >>> 0);
  });

  it('shiftRef below the boundary stays short and reports no growth', () => {
    const buf = [100, 0];
    const grown = shiftRef(buf, 0, 0x7ffe - 100);
    expect(grown).toBe(0);
    expect(buf).toEqual([0xfe, 0x7f]);
  });

  it('shiftRef crossing the boundary widens and reports growth, preserving trailing bytes', () => {
    const buf = [100, 0, 0xaa, 0xbb];
    const grown = shiftRef(buf, 0, 0x7fff);
    expect(grown).toBe(4);
    expect(buf.length).toBe(8);
    const tag = buf[0] | (buf[1] << 8);
    const val = (buf[2] | (buf[3] << 8) | (buf[4] << 16) | (buf[5] << 24)) >>> 0;
    expect(tag).toBe(0x7fff);
    expect(val).toBe(100 + 0x7fff);
    expect(buf.slice(6, 8)).toEqual([0xaa, 0xbb]);
  });

  it('shiftRef preserves the class-ref tag bit when widening', () => {
    const tagged = 0x8000 | 50;
    const buf = [tagged & 0xff, (tagged >> 8) & 0xff];
    shiftRef(buf, 0, 0x7fff);
    const val = (buf[2] | (buf[3] << 8) | (buf[4] << 16) | (buf[5] << 24)) >>> 0;
    expect(val).toBe((0x80000000 | (50 + 0x7fff)) >>> 0);
  });
});

describe('Large model slot boundary (real-scale)', () => {
  // Mirrors Python's TestLargeModelSlotBoundary: enough unique
  // (non-shared-vertex) triangles to push the file's total archive-slot
  // count past 32,767 - the exact condition that corrupted large
  // flattened-geometry exports before the 0x7FFF fix. This is the
  // realistic end-to-end regression test; the unit tests above pin down
  // the exact byte-level mechanism.
  it('round-trips through the reader with the exact face count after crossing 0x7FFF slots', () => {
    const n = 5000;
    const builder = create();
    for (let i = 0; i < n; i++) {
      const x = i * 10.0;
      builder.addFace([[x, 0, 0], [x + 1, 0, 0], [x, 1, 0]]);
    }
    const model = parseSkp(toBuffer(builder.toBytes()));
    expect(model.root.faces.length).toBe(n);
  }, 30000);
});

describe('Scaffold integrity', () => {
  it('the embedded scaffold has the expected length and SHA-256', async () => {
    const { loadScaffold, SCAFFOLD_LENGTH, SCAFFOLD_SHA256 } = await import('../src/scaffold');
    const data = loadScaffold();
    expect(data.length).toBe(SCAFFOLD_LENGTH);
    // Node-only (crypto module) - this test suite already runs under
    // Node/vitest, so this is a safe, CI-covered guard against the
    // embedded base64 silently drifting from the real file.
    const crypto = await import('crypto');
    const hash = crypto.createHash('sha256').update(Buffer.from(data)).digest('hex');
    expect(hash).toBe(SCAFFOLD_SHA256);
  });

  it('matches the byte-identical fixture already bundled for reader tests', async () => {
    const fs = await import('fs');
    const path = await import('path');
    const { loadScaffold } = await import('../src/scaffold');
    const fixture = fs.readFileSync(path.join(__dirname, 'fixtures', 'blank_v17.skp'));
    const scaffold = loadScaffold();
    expect(Array.from(scaffold)).toEqual(Array.from(fixture));
  });
});
