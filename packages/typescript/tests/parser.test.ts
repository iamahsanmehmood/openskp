import { describe, it, expect } from 'vitest';
import { validateHeader, readVersion } from '../src/vff';
import { readU32, readF64, parseVarInt, parseTlvRecursive } from '../src/parser';
import { transformPoint, multiplyMatrices, isIdentity } from '../src/transforms';
import { computeFaceNormal, triangulateFace3D } from '../src/triangulator';
import {
  GeometryBuilder,
  extractGeometryFromNodes,
  extractUvTransforms,
  collectDefs,
  collectLayers,
  parseMaterialXml,
  extractAttributeDictionaries,
  isGenericDefinitionName,
  findNameOverride,
} from '../src/geometry';

/** Build a single TLV element: 2-byte tag (hex) + 4-byte LE size + payload. */
function tlv(tagHex: string, payload: Uint8Array): Uint8Array {
  const tagBytes = new Uint8Array(tagHex.match(/.{2}/g)!.map((h) => parseInt(h, 16)));
  const sizeBytes = new Uint8Array(4);
  new DataView(sizeBytes.buffer).setUint32(0, payload.length, true);
  const result = new Uint8Array(2 + 4 + payload.length);
  result.set(tagBytes, 0);
  result.set(sizeBytes, 2);
  result.set(payload, 6);
  return result;
}

function concatBytes(...arrays: Uint8Array[]): Uint8Array {
  const total = arrays.reduce((sum, a) => sum + a.length, 0);
  const result = new Uint8Array(total);
  let offset = 0;
  for (const a of arrays) {
    result.set(a, offset);
    offset += a.length;
  }
  return result;
}

describe('VFF Header and Version Parsing', () => {
  it('should validate VFF header', () => {
    const validHeader = new Uint8Array([0xFF, 0xFE, 0xFF, 0x0E, 0x01, 0x02]);
    const invalidHeader = new Uint8Array([0xFF, 0xFF, 0xFF, 0x0E]);
    expect(validateHeader(validHeader)).toBe(true);
    expect(validateHeader(invalidHeader)).toBe(false);
  });

  it('should extract SketchUp version', () => {
    // A mock VFF header containing the version string in UTF-16LE inside braces
    // The second \xFF\xFE\xFF marker is at some offset, say index 6.
    // So: [0xFF, 0xFE, 0xFF, 0x0E, 0x00, 0x00, 0xFF, 0xFE, 0xFF, 0x00, ...]
    // Version starts at second_marker + 4, which is 6 + 4 = 10.
    const text = 'Some info {23.0.123}';
    const encoder = new TextEncoder();
    // Convert to UTF-16LE
    const textBytes = new Uint8Array(text.length * 2);
    for (let i = 0; i < text.length; i++) {
      const code = text.charCodeAt(i);
      textBytes[i * 2] = code & 0xff;
      textBytes[i * 2 + 1] = (code >> 8) & 0xff;
    }

    const data = new Uint8Array(10 + textBytes.length);
    data.set([0xFF, 0xFE, 0xFF, 0x0E, 0x00, 0x00, 0xFF, 0xFE, 0xFF, 0x00], 0);
    data.set(textBytes, 10);

    expect(readVersion(data)).toBe('{23.0.123}');
  });
});

describe('Low-Level Parser Decoders', () => {
  it('should read uint32 little-endian', () => {
    const data = new Uint8Array([0xEF, 0xBE, 0xAD, 0xDE]);
    expect(readU32(data, 0)).toBe(0xDEADBEEF);
  });

  it('should read float64 little-endian', () => {
    // 1.0 in float64 is 0x3FF0000000000000
    const data = new Uint8Array([0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0xF0, 0x3F]);
    expect(readF64(data, 0)).toBe(1.0);
  });

  it('should parse variable length integer', () => {
    const data = new Uint8Array([0x01, 0x02, 0x03]);
    expect(parseVarInt(data, 0, 1)).toBe(1);
    expect(parseVarInt(data, 0, 2)).toBe(0x0201);
    expect(parseVarInt(data, 0, 3)).toBe(0x030201);
  });

  it('should recursively decode TLV', () => {
    // Let's build a tag. Tag F601 is a container.
    // Structure:
    // [0xF6, 0x01] -> tag
    // [0x08, 0x00, 0x00, 0x00] -> size: 8
    //   Children payload (8 bytes):
    //   [0x03, 0x00] -> tag 0300 (Vertex)
    //   [0x00, 0x00, 0x00, 0x00] -> size: 0
    //   [0x00, 0x00] -> tag 0000
    //   [0x00, 0x00, 0x00, 0x00] -> size: 0 (or padding)
    const data = new Uint8Array([
      0xF6, 0x01, 0x06, 0x00, 0x00, 0x00, // Tag F601, size 6
      0x03, 0x00, 0x00, 0x00, 0x00, 0x00  // Tag 0300, size 0
    ]);
    const nodes = parseTlvRecursive(data, 0, data.length);
    expect(nodes.length).toBe(1);
    expect(nodes[0].tag).toBe('F601');
    expect(nodes[0].children.length).toBe(1);
    expect(nodes[0].children[0].tag).toBe('0300');
  });
});

describe('Transforms and Matrices', () => {
  it('should transform 3D points', () => {
    const translationMatrix = [
      1.0, 0.0, 0.0,
      0.0, 1.0, 0.0,
      0.0, 0.0, 1.0,
      5.0, 10.0, -2.0, // translation
      1.0
    ];
    const point: [number, number, number] = [1.0, 2.0, 3.0];
    const transformed = transformPoint(translationMatrix, point);
    expect(transformed).toEqual([6.0, 12.0, 1.0]);
  });

  it('should multiply 13-element matrices', () => {
    const m1 = [
      1, 0, 0,
      0, 1, 0,
      0, 0, 1,
      1, 2, 3,
      1.0
    ];
    const m2 = [
      1, 0, 0,
      0, 1, 0,
      0, 0, 1,
      4, 5, 6,
      1.0
    ];
    const result = multiplyMatrices(m1, m2);
    // Translation should be added
    expect(result[9]).toBe(5);
    expect(result[10]).toBe(7);
    expect(result[11]).toBe(9);
  });
});

describe('Triangulation', () => {
  it('should compute face normal', () => {
    const points: [number, number, number][] = [
      [0, 0, 0],
      [1, 0, 0],
      [1, 1, 0],
      [0, 1, 0]
    ];
    const normal = computeFaceNormal(points);
    expect(normal).toBeDefined();
    expect(normal![2]).toBeCloseTo(1.0);
  });

  it('should triangulate 3D polygons', () => {
    const vertices = new Map<number, [number, number, number]>([
      [0, [0, 0, 0]],
      [1, [1, 0, 0]],
      [2, [1, 1, 0]],
      [3, [0, 1, 0]]
    ]);
    const loops = [[0, 1, 2, 3]];
    const normal: [number, number, number] = [0, 0, 1];
    const triangles = triangulateFace3D(vertices, loops, normal);
    expect(triangles.length).toBe(2); // Two triangles for a quad
    expect(triangles[0]).toEqual([0, 1, 2]);
    expect(triangles[1]).toEqual([0, 2, 3]);
  });
});

describe('Instance material (paint the component)', () => {
  it('reads the D007/D107 material id from a 6419 instance node', () => {
    const d107 = tlv('D107', new Uint8Array([0x33, 0x73])); // id 0x7333
    const d007 = tlv('D007', d107);
    const ref = tlv('6719', new Uint8Array([0x05])); // refIdx 5
    const node = tlv('6419', concatBytes(ref, d007));

    const elements = parseTlvRecursive(node, 0, node.length);
    const builder = new GeometryBuilder();
    extractGeometryFromNodes(elements, builder);

    expect(builder.instances.length).toBe(1);
    expect(builder.instances[0].refIdx).toBe(5);
    expect(builder.instances[0].materialId).toBe(0x7333);
  });

  it('defaults materialId to null when the instance has no D007/D107', () => {
    const ref = tlv('6719', new Uint8Array([0x05]));
    const node = tlv('6419', ref);

    const elements = parseTlvRecursive(node, 0, node.length);
    const builder = new GeometryBuilder();
    extractGeometryFromNodes(elements, builder);

    expect(builder.instances.length).toBe(1);
    expect(builder.instances[0].materialId).toBeNull();
  });
});

describe('Face UV transform (positioned texture mapping)', () => {
  const ROT90 = [0.0, 1.0, 0.0, -1.0, 0.0, 0.0, 96.0, -96.0, 1.0];

  function packF64Array(values: number[]): Uint8Array {
    const buf = new Uint8Array(values.length * 8);
    const view = new DataView(buf.buffer);
    values.forEach((v, i) => view.setFloat64(i * 8, v, true));
    return buf;
  }

  function dc05(front: number[] | null, back: number[] | null): Uint8Array {
    const side = (tag: string, mat: number[]) => {
      const m1527 = tlv('1527', packF64Array(mat));
      const inner1327 = tlv('1327', concatBytes(tlv('1427', new Uint8Array([0x01])), m1527));
      return tlv(tag, inner1327);
    };
    let inner = new Uint8Array(0);
    if (front !== null) inner = concatBytes(inner, side('1127', front));
    if (back !== null) inner = concatBytes(inner, side('1227', back));
    const t1027 = tlv('1027', inner);
    return concatBytes(
      tlv('DE05', new Uint8Array([0x2a])),
      tlv('DD05', tlv('B136', tlv('B236', t1027)))
    );
  }

  it('extracts the front matrix', () => {
    const [front, back] = extractUvTransforms(dc05(ROT90, null));
    expect(front).not.toBeNull();
    front!.forEach((v, i) => expect(v).toBeCloseTo(ROT90[i]));
    expect(back).toBeNull();
  });

  it('extracts both sides', () => {
    const other = ROT90.map((v) => v * 2);
    const [front, back] = extractUvTransforms(dc05(ROT90, other));
    expect(front).not.toBeNull();
    expect(back).not.toBeNull();
    front!.forEach((v, i) => expect(v).toBeCloseTo(ROT90[i]));
    back!.forEach((v, i) => expect(v).toBeCloseTo(other[i]));
  });

  it('returns null for an untouched texture (no DD05 block)', () => {
    const plain = tlv('DE05', new Uint8Array([0x2a])); // entity id only
    const [front, back] = extractUvTransforms(plain);
    expect(front).toBeNull();
    expect(back).toBeNull();
  });
});

describe('Image entities', () => {
  it('extracts the instance wrapped in 9013 -> 401F placement containers', () => {
    const inner6419 = tlv('6419', tlv('6719', new Uint8Array([0x07]))); // refIdx 7
    const node = tlv('9013', tlv('401F', inner6419));

    const elements = parseTlvRecursive(node, 0, node.length);
    const builder = new GeometryBuilder();
    extractGeometryFromNodes(elements, builder);

    expect(builder.instances.length).toBe(1);
    expect(builder.instances[0].refIdx).toBe(7);
  });

  it('marks Definition.isImage when the 8315 kind byte is 2', () => {
    const defOn = tlv(
      '7C15',
      concatBytes(
        tlv('DE05', new Uint8Array([0x01])),
        tlv('7D15', new Uint8Array(16).fill(0x11)),
        tlv('7E15', new TextEncoder().encode('imagen#1')),
        tlv('8315', new Uint8Array([0x02]))
      )
    );
    const defOff = tlv(
      '7C15',
      concatBytes(
        tlv('DE05', new Uint8Array([0x02])),
        tlv('7D15', new Uint8Array(16).fill(0x22)),
        tlv('7E15', new TextEncoder().encode('Grupo')),
        tlv('8315', new Uint8Array([0x00]))
      )
    );
    const buf = concatBytes(defOn, defOff);

    const elements = parseTlvRecursive(buf, 0, buf.length);
    const defsDict = collectDefs(elements);

    const names = Array.from(defsDict.values()).map((d) => [d.name, d.isImage]);
    expect(names).toContainEqual(['imagen#1', true]);
    expect(names).toContainEqual(['Grupo', false]);
  });
});

describe('Always faces camera and shadows face sun (component behavior flags)', () => {
  it('marks Definition.alwaysFacesCamera and shadowsFaceSun when 581B carries 5D1B and 5E1B', () => {
    const behaviorOn = tlv('581B', concatBytes(tlv('5D1B', new Uint8Array([0x01])), tlv('5E1B', new Uint8Array([0x01]))));
    const behaviorOff = tlv('581B', concatBytes(tlv('5D1B', new Uint8Array([0x00])), tlv('5E1B', new Uint8Array([0x00]))));

    const susan = tlv(
      '7C15',
      concatBytes(tlv('DE05', new Uint8Array([0x01])), tlv('7E15', new TextEncoder().encode('Susan')), behaviorOn)
    );
    const chair = tlv(
      '7C15',
      concatBytes(tlv('DE05', new Uint8Array([0x02])), tlv('7E15', new TextEncoder().encode('Chair')), behaviorOff)
    );
    const buf = concatBytes(susan, chair);

    const elements = parseTlvRecursive(buf, 0, buf.length);
    const defsDict = collectDefs(elements);

    const names = Array.from(defsDict.values()).map((d) => [d.name, d.alwaysFacesCamera, d.shadowsFaceSun]);
    expect(names).toContainEqual(['Susan', true, true]);
    expect(names).toContainEqual(['Chair', false, false]);
  });
});

describe('Back-side material (AF0D)', () => {
  it('extracts the back-side material while front stays unpainted', () => {
    const dc05 = tlv('DC05', tlv('DE05', new Uint8Array([0x2a])));
    const af0d = tlv('AF0D', new Uint8Array([0x85, 0x8b, 0x06]));
    const node = tlv('AC0D', concatBytes(dc05, af0d));

    const elements = parseTlvRecursive(node, 0, node.length);
    const builder = new GeometryBuilder();
    extractGeometryFromNodes(elements, builder);

    expect(builder.faces.has(0x2a)).toBe(true);
    const f = builder.faces.get(0x2a)!;
    expect(f.materialId ?? null).toBeNull();
    expect(f.backMaterialId).toBe(0x068b85);
  });
});

describe("Material transparency (useTrans gating)", () => {
  it('applies trans as an opacity (1 - trans) when useTrans="1"', () => {
    const xml =
      '<mat:material name="M" colorRed="1" colorGreen="2" colorBlue="3" trans="0.27" useTrans="1"/>';
    const parsed = parseMaterialXml(xml);
    expect(parsed).not.toBeNull();
    // trans stores a TRANSPARENCY; the exposed value is the resulting
    // opacity, so trans="0.27" reads back as 0.73.
    expect(parsed!.trans).toBeCloseTo(0.73, 9);
  });

  it('stays fully opaque when useTrans is absent or "0" (trans is a leftover default)', () => {
    const xml =
      '<mat:material name="M" colorRed="1" colorGreen="2" colorBlue="3" trans="0" useTrans="0"/>';
    const parsed = parseMaterialXml(xml);
    expect(parsed).not.toBeNull();
    expect(parsed!.trans).toBe(1.0);
  });
});

describe('XML entity decoding (parseMaterialXml uses regex, not a real XML parser)', () => {
  it('decodes "&lt;auto&gt;" to the literal "<auto>" - SketchUp\'s own default-material naming convention', () => {
    // A real XML attribute value with < or > MUST be escaped in the
    // source (the raw bytes literally contain "&lt;auto&gt;") - a real
    // XML parser (this project's other four ports all use one) decodes
    // that back to the literal characters automatically. This project's
    // TS/C++ readers extract attributes via regex instead, which had no
    // decoding step at all: the name came through still escaped.
    const xml = '<mat:material name="&lt;auto&gt;" colorRed="255" colorGreen="128" colorBlue="128"/>';
    const parsed = parseMaterialXml(xml);
    expect(parsed).not.toBeNull();
    expect(parsed!.name).toBe('<auto>');
  });

  it('decodes &amp;/&apos;/&quot; and numeric character references, without double-decoding &amp;lt;', () => {
    const xml =
      '<mat:material name="Tom &amp;amp; Jerry&apos;s &quot;Wood&quot; &#65;&#x42;" colorRed="1" colorGreen="1" colorBlue="1"/>';
    const parsed = parseMaterialXml(xml);
    expect(parsed).not.toBeNull();
    // &amp;amp; must decode to the single literal "&amp;", not further to
    // "&" - a naive multi-pass replace (decode &amp; first, then &lt; on
    // the result) would over-decode this.
    expect(parsed!.name).toBe('Tom &amp; Jerry\'s "Wood" AB');
  });
});

describe('Edge display flags (D307)', () => {
  it('decodes plain / hidden / soft+smooth edges from the D307 flag byte', () => {
    const buildEdge = (entityId: number, flag: number) => {
      const de05 = tlv('DE05', new Uint8Array([entityId]));
      const d307 = tlv('D307', new Uint8Array([flag]));
      const d007 = tlv('D007', d307);
      return tlv('B80B', concatBytes(de05, d007));
    };

    const plain = buildEdge(0x01, 0x06);
    const hidden = buildEdge(0x02, 0x07);
    const softSmooth = buildEdge(0x03, 0x1e);
    const buf = concatBytes(plain, hidden, softSmooth);

    const elements = parseTlvRecursive(buf, 0, buf.length);
    const builder = new GeometryBuilder();
    extractGeometryFromNodes(elements, builder);

    expect(builder.edgeFlags.get(0x01)).toBe(0x06);
    expect(builder.edgeFlags.get(0x02)).toBe(0x07);
    expect(builder.edgeFlags.get(0x03)).toBe(0x1e);
  });
});

describe('Section plane, text entity, and dimension defaults', () => {
  it('exposes sectionPlanes, texts, and dimensions arrays on GeometryBuilder', () => {
    const builder = new GeometryBuilder();
    expect(builder.sectionPlanes).toEqual([]);
    expect(builder.texts).toEqual([]);
    expect(builder.dimensions).toEqual([]);
  });
});

describe('extractAttributeDictionaries - dictionary-name-aware TLV walk (openskp#254/#285)', () => {
  // Real B436(name)/B536(entries) dictionary-boundary shape - the TLV
  // structure extractAttributeDictionaries needs but extractDynamicProperties
  // never required, since it flattens regardless of dictionary boundaries.
  // Confirmed byte-for-byte against a real FrameBuilder-authored production
  // file (see Python's TestVffAttributeDictionaries for the full
  // verification history this mirrors).
  const enc = (s: string) => new TextEncoder().encode(s);

  function entry(key: string, value: string): Uint8Array {
    return concatBytes(tlv('B636', enc(key)), tlv('A438', tlv('AD38', enc(value))));
  }

  function namedDict(name: string, entriesBytes: Uint8Array): Uint8Array {
    return concatBytes(tlv('B436', enc(name)), tlv('B536', entriesBytes));
  }

  function makeD007(dc05Payload: Uint8Array): ReturnType<typeof parseTlvRecursive>[number] {
    const bytes = tlv('D007', tlv('DC05', dc05Payload));
    const elements = parseTlvRecursive(bytes, 0, bytes.length);
    return elements[0];
  }

  it('groups entries by their dictionary name', () => {
    const d007 = makeD007(namedDict('fbd-einfo', entry('code', 'Ks')));
    const dicts = extractAttributeDictionaries(d007);
    expect(dicts['fbd-einfo']).toEqual({ code: 'Ks' });
  });

  it('keeps two dictionaries distinct', () => {
    const dc05 = concatBytes(
      namedDict('dynamic_attributes', entry('width', '10')),
      namedDict('FrameBuilder', entry('name', 'W-2'))
    );
    const dicts = extractAttributeDictionaries(makeD007(dc05));
    expect(dicts['dynamic_attributes']).toEqual({ width: '10' });
    expect(dicts['FrameBuilder']).toEqual({ name: 'W-2' });
    expect(dicts['dynamic_attributes']['name']).toBeUndefined();
  });

  it('returns {} when there is no DC05 child', () => {
    const d007Bytes = tlv('D007', new Uint8Array(0));
    const elements = parseTlvRecursive(d007Bytes, 0, d007Bytes.length);
    expect(extractAttributeDictionaries(elements[0])).toEqual({});
  });

  // Multi-value-type decoding (openskp#285's VFF 9-value-type item). Byte
  // shapes mirror Python's TestVffAttributeDictionaries exactly - same
  // fixture-construction approach, same real tag pairings, ground-truthed
  // there first (also mirrored in .NET's DynamicPropertiesTests).
  function f64(n: number): Uint8Array {
    const b = new Uint8Array(8);
    new DataView(b.buffer).setFloat64(0, n, true);
    return b;
  }
  function i32(n: number): Uint8Array {
    const b = new Uint8Array(4);
    new DataView(b.buffer).setInt32(0, n, true);
    return b;
  }
  function entryValue(innerTlv: Uint8Array): Uint8Array {
    return tlv('A438', innerTlv);
  }
  function entryRaw(key: string, innerValueTlv: Uint8Array): Uint8Array {
    return concatBytes(tlv('B636', enc(key)), entryValue(innerValueTlv));
  }

  it('decodes AF38 (Length) and A938 (plain Float) as distinct tags, both f64', () => {
    const entries = concatBytes(
      entryRaw('depth', tlv('AF38', f64(15.5))),
      entryRaw('price', tlv('A938', f64(120.0)))
    );
    const dicts = extractAttributeDictionaries(makeD007(namedDict('fbd-einfo', entries)));
    expect(dicts['fbd-einfo']).toEqual({ depth: 15.5, price: 120.0 });
  });

  it('decodes A738 as a round-tripped integer', () => {
    const entries = entryRaw('angle', tlv('A738', i32(-7)));
    const dicts = extractAttributeDictionaries(makeD007(namedDict('fbd-einfo', entries)));
    expect(dicts['fbd-einfo']).toEqual({ angle: -7 });
  });

  it('decodes an A438 with no children as null', () => {
    const entries = entryRaw('child_thickness', new Uint8Array(0));
    const dicts = extractAttributeDictionaries(makeD007(namedDict('fbd-einfo', entries)));
    expect(dicts['fbd-einfo']).toEqual({ child_thickness: null });
  });

  it('decodes B438 Point3d and B538 Vector3d as flat 3-tuples', () => {
    const pointBytes = tlv('B438', concatBytes(f64(0.0), f64(0.807085), f64(14.6551)));
    const vectorBytes = tlv('B538', concatBytes(f64(1.0), f64(0.0), f64(0.0)));
    const entries = concatBytes(entryRaw('end_pos', pointBytes), entryRaw('vector_new', vectorBytes));
    const dicts = extractAttributeDictionaries(makeD007(namedDict('fbd-einfo', entries)));
    expect(dicts['fbd-einfo']).toEqual({
      end_pos: [0.0, 0.807085, 14.6551],
      vector_new: [1.0, 0.0, 0.0],
    });
  });

  it('decodes an empty AE38 array as []', () => {
    const entries = entryRaw('added_bolt_holes', tlv('AE38', new Uint8Array(0)));
    const dicts = extractAttributeDictionaries(makeD007(namedDict('fbd-einfo', entries)));
    expect(dicts['fbd-einfo']).toEqual({ added_bolt_holes: [] });
  });

  it('decodes an AE38 array of floats', () => {
    const elems = concatBytes(
      entryValue(tlv('A938', f64(0.728))),
      entryValue(tlv('A938', f64(11.358))),
      entryValue(tlv('A938', f64(14.655)))
    );
    const entries = entryRaw('flangeholes', tlv('AE38', elems));
    const dicts = extractAttributeDictionaries(makeD007(namedDict('fbd-einfo', entries)));
    expect(dicts['fbd-einfo']).toEqual({ flangeholes: [0.728, 11.358, 14.655] });
  });

  it('decodes a nested AE38 array (openskp#253 mirrored on the read side)', () => {
    const inner1 = concatBytes(entryValue(tlv('A938', f64(25.17))), entryValue(tlv('A938', f64(0.07))));
    const inner2 = concatBytes(entryValue(tlv('A938', f64(25.17))), entryValue(tlv('A938', f64(15.35))));
    const outer = concatBytes(entryValue(tlv('AE38', inner1)), entryValue(tlv('AE38', inner2)));
    const entries = entryRaw('lip_side1_cords', tlv('AE38', outer));
    const dicts = extractAttributeDictionaries(makeD007(namedDict('fbd-einfo', entries)));
    expect(dicts['fbd-einfo']).toEqual({
      lip_side1_cords: [
        [25.17, 0.07],
        [25.17, 15.35],
      ],
    });
  });

  it('leaves an unrecognized value tag as null rather than guessing', () => {
    const entries = entryRaw('mystery', tlv('EE99', new Uint8Array([0x01, 0x02])));
    const dicts = extractAttributeDictionaries(makeD007(namedDict('fbd-einfo', entries)));
    expect(dicts['fbd-einfo']).toEqual({ mystery: null });
  });
});

describe('isGenericDefinitionName', () => {
  it.each([
    ['Group#1', true],
    ['Component#12', true],
    ['W-2', false],
    ['Truss1', false],
    ['', false],
  ])('%s -> %s', (name, expected) => {
    expect(isGenericDefinitionName(name)).toBe(expected);
  });
});

describe('findNameOverride', () => {
  it('skips dynamic_attributes and SU_InstanceSet', () => {
    const dicts = {
      dynamic_attributes: { name: 'should-be-ignored' },
      SU_InstanceSet: { label: 'also-ignored' },
      FrameBuilder: { name: 'W-2' },
    };
    expect(findNameOverride(dicts)).toBe('W-2');
  });

  it('returns null when no override is present', () => {
    expect(findNameOverride(null)).toBeNull();
    expect(findNameOverride({ dynamic_attributes: { width: '10' } })).toBeNull();
  });
});

describe('collectLayers - VFF per-layer-hidden flag (openskp#285)', () => {
  // VFF layers derive their COLOR from Layer_<name>-prefixed materials,
  // which carry no visibility flag of their own - real visibility lives on
  // the model.dat layer manager's own 993A/8C3C node, as a single-byte
  // 8E3C child sibling to the already-read DC05 (id) and 8D3C (name):
  // 1 = hidden, 0 = visible. Byte shapes mirror Python's own
  // TestVffLayerHidden exactly (confirmed against a real production file's
  // Tags panel), also mirrored in the .NET port's LayerHiddenTests.cs.
  const enc = (s: string) => new TextEncoder().encode(s);

  function layerNode(id: number, name: string, hidden: boolean | null): Uint8Array {
    const parts = [tlv('DC05', new Uint8Array([id])), tlv('8D3C', enc(name))];
    if (hidden !== null) parts.push(tlv('8E3C', new Uint8Array([hidden ? 1 : 0])));
    return tlv('8C3C', concatBytes(...parts));
  }

  function layerManager(...layers: Uint8Array[]): ReturnType<typeof parseTlvRecursive>[number] {
    const bytes = tlv('993A', concatBytes(...layers));
    return parseTlvRecursive(bytes, 0, bytes.length)[0];
  }

  it('reads hidden and visible layers correctly', () => {
    const root = layerManager(
      layerNode(5, 'wall_external_cladding_1', true),
      layerNode(6, 'wall', false)
    );

    const layerIdToName = new Map<number, string>();
    const layerHidden = new Map<string, boolean>();
    collectLayers([root], layerIdToName, undefined, layerHidden);

    expect(layerIdToName.get(5)).toBe('wall_external_cladding_1');
    expect(layerIdToName.get(6)).toBe('wall');
    expect(layerHidden.get('wall_external_cladding_1')).toBe(true);
    expect(layerHidden.get('wall')).toBe(false);
  });

  it('leaves layerHidden unset when there is no 8E3C tag', () => {
    const root = layerManager(layerNode(1, 'Layer0', null));

    const layerIdToName = new Map<number, string>();
    const layerHidden = new Map<string, boolean>();
    collectLayers([root], layerIdToName, undefined, layerHidden);

    expect(layerIdToName.get(1)).toBe('Layer0');
    expect(layerHidden.has('Layer0')).toBe(false);
  });

  it('the layerHidden parameter is optional', () => {
    const root = layerManager(layerNode(1, 'Layer0', true));

    const layerIdToName = new Map<number, string>();
    expect(() => collectLayers([root], layerIdToName)).not.toThrow();
    expect(layerIdToName.get(1)).toBe('Layer0');
  });
});
