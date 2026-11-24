import { describe, it, expect } from 'vitest';
import * as fs from 'node:fs';
import * as path from 'node:path';
import {
  VffBuilder,
  VffLength,
  VffPoint3d,
  VffTimestamp,
  VffVector3d,
  checkIntegrity,
  createVff,
  openExistingVff,
  parseSkp,
  pngAverageColor,
} from '../src/index';
import { portableCos, portableSin } from '../src/create-vff';
import { decodeVffAttrValue, stringifyVffAttrValue } from '../src/geometry';
import { readZip, zipOffset } from '../src/vff-writer/core';

const FIXTURES = path.join(__dirname, 'fixtures', 'vff_writer');
const SPEC = JSON.parse(fs.readFileSync(path.join(FIXTURES, 'spec.json'), 'utf8')) as any;
const GOLDEN = new Uint8Array(fs.readFileSync(path.join(FIXTURES, 'golden', 'all_features.skp')));

function convert(value: any): any {
  if (Array.isArray(value)) return value.map(convert);
  if (value && typeof value === 'object' && '$type' in value) {
    if (value.$type === 'timestamp') return new VffTimestamp(Number(value.value));
    if (value.$type === 'length') return new VffLength(Number(value.value));
    if (value.$type === 'point') return new VffPoint3d(...(value.value as [number, number, number]));
    if (value.$type === 'vector') return new VffVector3d(...(value.value as [number, number, number]));
  }
  return value;
}

function attributes(name: string | undefined): any {
  if (!name || !SPEC.attributes) return undefined;
  const out: Record<string, any> = {};
  for (const [key, value] of Object.entries(SPEC.attributes)) out[key] = convert(value);
  return out;
}

class SpecRunner {
  builder: VffBuilder;
  handles: Record<string, any> = {};
  materials: Record<string, number> = {};
  layers: Record<string, number> = {};

  constructor() {
    this.builder = createVff(undefined, SPEC.options);
    for (const [image, info] of Object.entries(SPEC.textures as Record<string, any>)) {
      const bytes = new Uint8Array(fs.readFileSync(path.join(FIXTURES, image)));
      this.materials[info.name] = this.builder.addTextureMaterial(info.name, bytes, {
        appliedWidth: info.width,
        appliedHeight: info.height,
        averageColor: info.averageColor,
      });
    }
    for (const op of SPEC.ops) this.run(this.builder, op);
  }

  run(ctx: any, op: any): void {
    switch (op.op) {
      case 'material':
        this.materials[op.name] = this.builder.addMaterial(op.name, op.rgba, op.opacity);
        break;
      case 'texture':
        break;
      case 'layer':
        this.layers[op.name] = this.builder.addLayer(op.name, op.color, op.hidden ?? false);
        break;
      case 'face':
        ctx.addFace(op.points, {
          material: this.materials[op.material],
          layer: this.layers[op.layer],
          backMaterial: this.materials[op.backMaterial],
          hidden: op.hidden ?? false,
          holes: op.holes,
          frontUv: op.frontUv?.map(([point, uv]: any) => [point, uv]),
          backUv: op.backUv?.map(([point, uv]: any) => [point, uv]),
          attributes: attributes(op.attributes),
        });
        break;
      case 'circle':
        ctx.addCircle(op.center, op.normal, op.radius, {
          numSegments: op.numSegments,
          material: this.materials[op.material],
          layer: this.layers[op.layer],
          attributes: attributes(op.attributes),
        });
        break;
      case 'arc':
        ctx.addArc(op.center, op.normal, op.radius, op.startAngle, op.endAngle, { numSegments: op.numSegments });
        break;
      case 'polyline':
        ctx.addPolyline(op.points, { closed: op.closed ?? false });
        break;
      case 'definition': {
        const definition = this.builder.addComponentDefinition(op.name, { attributes: attributes(op.attributes) });
        for (const inner of op.ops ?? []) this.run(definition, inner);
        definition.close();
        this.handles[op.name] = definition;
        break;
      }
      case 'instance':
        ctx.addInstance(this.handles[op.definition], {
          translation: op.translation ?? [0, 0, 0],
          rotation: op.rotation
            ? { axis: op.rotation.axis, angleRadians: op.rotation.angle }
            : undefined,
          attributes: attributes(op.attributes),
        });
        break;
      case 'group': {
        const group = ctx.addGroup(op.name, { translation: op.placement?.translation ?? [0, 0, 0] });
        for (const inner of op.ops ?? []) this.run(group, inner);
        group.close();
        break;
      }
      case 'text':
        if (op.screen) ctx.addScreenText(op.text, op.point, op.vector);
        else ctx.addText(op.text, op.point, op.vector);
        break;
      case 'dimension':
        ctx.addDimension(op.start, op.end, op.offset ?? 10, op.offsetVector ?? null, op.anchor ?? false);
        break;
      case 'guideLine':
        ctx.addGuideLine(op.point, op.point2 ?? null, op.direction ?? null);
        break;
      case 'guidePoint':
        ctx.addGuidePoint(op.position);
        break;
      case 'section':
        ctx.addSectionPlane(op.point, op.normal, {
          name: op.name,
          symbol: op.symbol,
          active: op.active ?? false,
        });
        break;
      case 'scene':
        ctx.addScene(op.name, op.eye, op.target, {
          perspective: op.perspective ?? true,
          fov: op.fov ?? 35,
          orthoHeight: op.orthoHeight ?? 200,
          hiddenLayers: (op.hiddenLayers ?? []).map((name: string) => this.layers[name]),
        });
        break;
      case 'camera':
        ctx.setCamera(op.eye, op.target, {
          perspective: op.perspective ?? true,
          fov: op.fov ?? 35,
          orthoHeight: op.orthoHeight ?? 200,
        });
        break;
      case 'modelAttributes':
        this.builder.setModelAttributes({ attributes: attributes(op.attributes) });
        break;
      default:
        throw new Error(`unknown spec op ${op.op}`);
    }
  }
}

function parity(actual: Uint8Array, expected: Uint8Array): void {
  const actualZip = zipOffset(actual);
  const expectedZip = zipOffset(expected);
  const a = readZip(actual, actualZip);
  const b = readZip(expected, expectedZip);
  expect(a.map((e) => [e.name, e.flag, e.internalAttr])).toEqual(b.map((e) => [e.name, e.flag, e.internalAttr]));
  for (let i = 0; i < a.length; i++) {
    expect(a[i].name).toBe(b[i].name);
    expect(Array.from(a[i].data)).toEqual(Array.from(b[i].data));
  }
  expect(Array.from(actual.subarray(0, actualZip))).toEqual(Array.from(expected.subarray(0, expectedZip)));
}

function toBuffer(bytes: Uint8Array): ArrayBuffer {
  return bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer;
}

describe('VFF writer parity with Python', () => {
  it('builds the all-features model byte-identically (by the parity definition)', () => {
    const data = new SpecRunner().builder.toBytes();
    fs.writeFileSync('/tmp/ts_all.skp', data);
    expect(checkIntegrity(data)).toBe(true);
    parity(data, GOLDEN);
  });

  it('re-parses its own output with the TS reader', () => {
    const model = parseSkp(toBuffer(new SpecRunner().builder.toBytes()));
    expect(model.root.faces.length).toBe(5);
    expect(model.definitions.size).toBe(4); // Widget, Sub, InnerGroup, RootGroup
    expect(model.root.instances.length).toBe(3);
    expect([...model.definitions.values()].some((d) => d.name === 'Widget')).toBe(true);
    expect(model.pages.map((p) => p.name)).toEqual(['Perspective', 'HideB']);
    expect(model.pages[1].hiddenLayers).toContain('TagB');
    expect(model.pages[0].eye).toEqual([40, -40, 30]);
    expect(model.materials.map((m) => m.name)).toContain('TexPNG');
    expect(model.dimensions.length).toBe(2);
    expect(model.root.sectionPlanes.length).toBe(2);
    expect(model.root.constructionLines.length).toBe(2);
    expect(model.root.constructionPoints.length).toBe(1);
    expect(model.root.texts.length).toBe(2);
  });

  it('writes every attribute value type into the golden model', () => {
    const modelDat = readZip(GOLDEN, zipOffset(GOLDEN)).find((e) => e.name === 'model.dat')!.data;
    const hex = Buffer.from(modelDat).toString('hex');
    for (const tag of ['a738', 'a938', 'aa38', 'ac38', 'ad38', 'ae38', 'af38', 'b438', 'b538']) {
      expect(hex).toContain(tag);
    }
    // The reader decodes the new bool/time tags (unit-tested below).
    const model = parseSkp(toBuffer(new SpecRunner().builder.toBytes()));
    expect([...model.definitions.values()].some((d) => d.name === 'Widget')).toBe(true);
  });

  it('portable trig matches Math.sin/cos and default circle points', () => {
    const ulp = (value: number): number => {
      if (value === 0) return Number.MIN_VALUE;
      const buffer = new ArrayBuffer(8);
      const view = new DataView(buffer);
      view.setFloat64(0, value, true);
      const bits = view.getBigUint64(0, true);
      view.setBigUint64(0, bits + 1n, true);
      return Math.abs(view.getFloat64(0, true) - value) || Number.MIN_VALUE;
    };
    for (let i = 0; i <= 20000; i++) {
      const angle = -4 * Math.PI + (8 * Math.PI * i) / 20000;
      expect(Math.abs(portableSin(angle) - Math.sin(angle))).toBeLessThanOrEqual(4 * ulp(Math.max(Math.abs(angle), 1)));
      expect(Math.abs(portableCos(angle) - Math.cos(angle))).toBeLessThanOrEqual(4 * ulp(Math.max(Math.abs(angle), 1)));
    }
    const vertices = (portable: boolean): number[][] => {
      const builder = createVff(undefined, { ...SPEC.options, portableTrig: portable });
      builder.addCircle([3, 4, 5], [0, 0, 1], 12.5, { numSegments: 24 });
      const model = parseSkp(toBuffer(builder.toBytes()));
      return model.root.vertices.map((v) => [v.x, v.y, v.z]);
    };
    const defaults = vertices(false);
    const portable = vertices(true);
    expect(portable.length).toBe(defaults.length);
    for (let i = 0; i < defaults.length; i++) {
      for (let axis = 0; axis < 3; axis++) {
        expect(Math.abs(portable[i][axis] - defaults[i][axis])).toBeLessThanOrEqual(1e-12 * Math.max(1, Math.abs(defaults[i][axis])));
      }
    }
  });

  it('computes the same PNG average colour as Python/Pillow for the fixture', () => {
    const bytes = new Uint8Array(fs.readFileSync(path.join(FIXTURES, 'tex.png')));
    expect(pngAverageColor(bytes)).toEqual([121, 68, 114]);
  });

  it('decodes AA38 bool / AC38 time_t reader values with Python stringification', () => {
    const boolNode: any = { tag: 'A438', payload: new Uint8Array(0), children: [{ tag: 'AA38', payload: new Uint8Array([1]), children: null }] };
    const timeNode: any = { tag: 'A438', payload: new Uint8Array(0), children: [{ tag: 'AC38', payload: new Uint8Array([0, 0, 0, 0]), children: null }] };
    expect(decodeVffAttrValue(boolNode)).toBe(true);
    expect(stringifyVffAttrValue(true)).toBe('1');
    expect(decodeVffAttrValue(timeNode)).toBe(0);
  });

  it('openExistingVff uses the source as template and allows additions', () => {
    const [builder, warnings, definitions] = openExistingVff(GOLDEN);
    expect(warnings).toEqual([]);
    expect(Object.keys(definitions)).toContain('Widget');
    const mat = builder.addMaterial('AddedRed', [10, 200, 30]);
    const tag = builder.addLayer('AddedTag', [0, 120, 240]);
    builder.addFace([[500, 0, 0], [510, 0, 0], [510, 10, 0], [500, 10, 0]], { material: mat, layer: tag });
    builder.addInstance(definitions.Widget, { translation: [200, 0, 0] });
    const group = builder.addGroup('AddedGroup', { translation: [300, 0, 0] });
    group.addFace([[0, 0, 0], [10, 0, 0], [10, 10, 0], [0, 10, 0]]);
    group.close();
    const data = builder.toBytes();
    expect(checkIntegrity(data)).toBe(true);
    const model = parseSkp(toBuffer(data));
    expect(model.pages.map((p) => p.name)).toEqual(['Perspective', 'HideB']);
    expect(model.materials.map((m) => m.name)).toContain('AddedRed');
    expect([...model.definitions.values()].map((d) => d.name)).toContain('AddedGroup');
    expect(model.root.instances.length).toBe(5);
  });

  it('refuses a newer VFF revision', () => {
    const newer = new Uint8Array(GOLDEN);
    const marker = 'VFF';
    let header = -1;
    for (let i = 0; i < newer.length - 7; i++) {
      if (new TextDecoder().decode(newer.subarray(i, i + 3)) === marker && newer[i + 3] === 8) { header = i; break; }
    }
    newer[header + 7] = 18;
    expect(() => openExistingVff(newer)).toThrow(/revision 18/);
  });
});
