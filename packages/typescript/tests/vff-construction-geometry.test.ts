import { describe, expect, it } from 'vitest';
import { GeometryBuilder, extractGeometryFromNodes } from '../src/geometry';
import { parseTlvRecursive } from '../src/parser';

function tlv(tag: string, body: Uint8Array): Uint8Array {
  const bytes = new Uint8Array(6 + body.length);
  bytes[0] = parseInt(tag.slice(0, 2), 16);
  bytes[1] = parseInt(tag.slice(2, 4), 16);
  new DataView(bytes.buffer).setUint32(2, body.length, true);
  bytes.set(body, 6);
  return bytes;
}

function doubles(...values: number[]): Uint8Array {
  const bytes = new Uint8Array(values.length * 8);
  const view = new DataView(bytes.buffer);
  values.forEach((value, i) => view.setFloat64(i * 8, value, true));
  return bytes;
}

function join(...items: Uint8Array[]): Uint8Array {
  const result = new Uint8Array(items.reduce((sum, item) => sum + item.length, 0));
  let at = 0;
  for (const item of items) {
    result.set(item, at);
    at += item.length;
  }
  return result;
}

describe('VFF construction line and point records (#285)', () => {
  it('decodes bounded/unbounded lines and guide points through TLV containers', () => {
    const bounded = tlv('6942', tlv('6A42', doubles(2, 3, 4, 0, 1, 0, -2, 7)));
    const infinite = tlv('6942', tlv('6A42', doubles(0, 0, 4, 0, 0, 1, -1e30, 1e30)));
    const point = tlv('6C42', join(
      tlv('6D42', doubles(8, 9, 10)),
      tlv('6E42', doubles(0, 0, 0)),
      tlv('6F42', Uint8Array.of(0)),
    ));
    const data = join(tlv('9113', join(bounded, infinite)), tlv('9213', point));
    const builder = new GeometryBuilder();
    extractGeometryFromNodes(parseTlvRecursive(data, 0, data.length), builder);

    expect(builder.constructionLines).toEqual([
      { point: [2, 3, 4], direction: [0, 1, 0], start: [2, 1, 4], end: [2, 10, 4], layerId: null, hidden: false },
      { point: [0, 0, 4], direction: [0, 0, 1], start: null, end: null, layerId: null, hidden: false },
    ]);
    expect(builder.constructionPoints).toEqual([{ position: [8, 9, 10], layerId: null, hidden: false }]);
  });

  it('reads VFF guide hidden flag and layer id from 6842 -> D007', () => {
    const drawbase = (flags: number, layerId?: number): Uint8Array => {
      const d307 = tlv('D307', Uint8Array.of(flags));
      const d007 = layerId !== undefined ? join(tlv('D207', Uint8Array.of(layerId)), d307) : d307;
      return tlv('6842', tlv('D007', d007));
    };
    const data = join(
      tlv('9213', join(
        tlv('6C42', join(drawbase(0x07), tlv('6D42', doubles(1, 2, 3)))),
        tlv('6C42', join(drawbase(0x06, 4), tlv('6D42', doubles(4, 5, 6)))),
      )),
      tlv('9113', tlv('6942', join(drawbase(0x07, 3), tlv('6A42', doubles(0, 0, 0, 1, 0, 0, 0, 12))))),
    );
    const builder = new GeometryBuilder();
    extractGeometryFromNodes(parseTlvRecursive(data, 0, data.length), builder);
    expect(builder.constructionPoints).toEqual([
      { position: [1, 2, 3], layerId: null, hidden: true },
      { position: [4, 5, 6], layerId: 4, hidden: false },
    ]);
    expect(builder.constructionLines).toEqual([
      { point: [0, 0, 0], direction: [1, 0, 0], start: [0, 0, 0], end: [12, 0, 0], layerId: 3, hidden: true },
    ]);
  });

  it('ignores truncated VFF guide payloads', () => {
    const data = join(
      tlv('9113', tlv('6942', tlv('6A42', doubles(1, 2, 3)))),
      tlv('9213', tlv('6C42', tlv('6D42', doubles(1, 2)))),
    );
    const builder = new GeometryBuilder();
    extractGeometryFromNodes(parseTlvRecursive(data, 0, data.length), builder);
    expect(builder.constructionLines).toEqual([]);
    expect(builder.constructionPoints).toEqual([]);
  });
});
