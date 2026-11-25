import * as fs from 'fs';
import * as path from 'path';
import { describe, expect, it } from 'vitest';
import {
  buildInstancedScene,
  buildScene,
  type Page,
  pageCaptureFlags,
  PAGE_USE_AXES,
  PAGE_USE_CAMERA,
  PAGE_USE_HIDDEN,
  PAGE_USE_HIDDEN_GEOMETRY,
  PAGE_USE_HIDDEN_LAYERS,
  PAGE_USE_HIDDEN_OBJECTS,
  PAGE_USE_RENDERING_OPTIONS,
  PAGE_USE_SECTION_PLANES,
  PAGE_USE_SHADOW_INFO,
  SkpFile,
} from '../src';

const FIXTURES = path.resolve(__dirname, 'fixtures');
const CPP_FIXTURES = path.resolve(__dirname, '../../cpp/tests/fixtures');

const YEARS = [2015, 2016, 2017, 2018, 2019, 2020] as const;

const HIDDEN_ENTITIES = [
  'scene_hidden_entities_2017.skp',
  'scene_hidden_entities_2019.skp',
  'scene_hidden_entities_2020.skp',
  'scene_hidden_entities_2024.skp',
] as const;

const SCENE_ORDER = [
  'Camera',
  'RenderingOptions',
  'ShadowInfo',
  'Axes',
  'Hidden',
  'HiddenGeometry',
  'HiddenObjects',
  'HiddenLayers',
  'SectionPlanes',
  'ShadowsCamera',
  'AxesCamera',
];

const ALL_USE_PROPS = [
  'useCamera',
  'useRenderingOptions',
  'useShadowInfo',
  'useAxes',
  'useHiddenLayers',
  'useSectionPlanes',
  'useHiddenGeometry',
  'useHiddenObjects',
] as const;

type UseProp = (typeof ALL_USE_PROPS)[number];

function pagesByName(filePath: string): Map<string, Page> {
  const model = SkpFile.open(filePath).parse();
  return new Map(model.pages.map((p) => [p.name, p]));
}

describe('Scene capture flags (#660, #661)', () => {
  it('exposes the SketchUp capture-flag bit constants', () => {
    expect(PAGE_USE_CAMERA).toBe(0x001);
    expect(PAGE_USE_RENDERING_OPTIONS).toBe(0x002);
    expect(PAGE_USE_SHADOW_INFO).toBe(0x004);
    expect(PAGE_USE_AXES).toBe(0x008);
    expect(PAGE_USE_HIDDEN).toBe(0x010);
    expect(PAGE_USE_HIDDEN_LAYERS).toBe(0x020);
    expect(PAGE_USE_SECTION_PLANES).toBe(0x040);
    expect(PAGE_USE_HIDDEN_GEOMETRY).toBe(0x080);
    expect(PAGE_USE_HIDDEN_OBJECTS).toBe(0x100);
  });

  it.each(HIDDEN_ENTITIES)('tells an empty hidden list apart (%s)', (name) => {
    const pages = pagesByName(path.join(CPP_FIXTURES, name));
    expect(pages.get('All')!.hiddenEntityIds).toEqual([]);
    expect(pages.get('Not saved')!.hiddenEntityIds).toEqual([]);
    expect(pages.get('All')!.captureFlags).toBe(0x191);
    expect(pages.get('All')!.useHiddenObjects).toBe(true);
    expect(pages.get('All')!.useHiddenGeometry).toBe(true);
    expect(pages.get('Not saved')!.captureFlags).toBe(0x001);
    expect(pages.get('Not saved')!.useHiddenObjects).toBe(false);
    expect(pages.get('Not saved')!.useHiddenGeometry).toBe(false);
    expect(pages.get('Not saved')!.useCamera).toBe(true);
  });

  it.each(HIDDEN_ENTITIES)('marks every scene that hides something as saving it (%s)', (name) => {
    for (const page of pagesByName(path.join(CPP_FIXTURES, name)).values()) {
      if (page.hiddenEntityIds.length > 0) {
        expect(Boolean(page.useHiddenObjects || page.useHiddenGeometry)).toBe(true);
      }
    }
  });

  it('reads flags on VFF scenes that save no camera and propagates to built scenes', () => {
    const filePath = path.join(FIXTURES, 'scene_without_camera_2021.skp');
    const pages = pagesByName(filePath);
    expect(pages.get('With camera')!.captureFlags).toBe(0x001);
    expect(pages.get('With camera')!.useCamera).toBe(true);
    expect(pages.get('With camera')!.useHiddenLayers).toBe(false);
    expect(pages.get('No camera')!.captureFlags).toBe(0x020);
    expect(pages.get('No camera')!.useCamera).toBe(false);
    expect(pages.get('No camera')!.useHiddenLayers).toBe(true);
    expect(pages.get('No camera')!.eye).toBeNull();
    expect(pages.get('Plain')!.captureFlags).toBe(0x000);
    expect(pages.get('Plain')!.useCamera).toBe(false);
    expect(pages.get('Plain')!.useHiddenLayers).toBe(false);

    const raw = fs.readFileSync(filePath);
    const ab = raw.buffer.slice(raw.byteOffset, raw.byteOffset + raw.byteLength) as ArrayBuffer;
    expect(buildScene(ab).pages?.map((p) => p.captureFlags)).toEqual([0x001, 0x020, 0x000]);
    expect(buildInstancedScene(ab).pages?.map((p) => p.captureFlags)).toEqual([0x001, 0x020, 0x000]);
  });

  it.each(['sdk_saved_sections_2019.skp', 'sdk_saved_sections_2020.skp'] as const)(
    'reads all-or-nothing flags (%s)',
    (name) => {
      const pages = pagesByName(path.join(FIXTURES, name));
      const everything = pages.get('Everything')!;
      const plain = pages.get('Plain')!;
      expect(everything.captureFlags).toBe(0x1ff);
      expect(plain.captureFlags).toBe(0x000);
      for (const prop of ALL_USE_PROPS) {
        expect(everything[prop]).toBe(true);
        expect(plain[prop]).toBe(false);
      }
    },
  );

  it('reads an application-saved scene (gondola_v20.skp)', () => {
    const [page] = [...pagesByName(path.join(FIXTURES, 'gondola_v20.skp')).values()];
    expect(page.captureFlags).toBe(0x1ff);
    expect(page.useHiddenGeometry).toBe(true);
  });

  it('falls back to the older combined hidden bit until either split bit is set', () => {
    const old = pageCaptureFlags(0x011);
    expect(old.useHiddenGeometry).toBe(true);
    expect(old.useHiddenObjects).toBe(true);

    const split = pageCaptureFlags(0x111);
    expect(split.useHiddenObjects).toBe(true);
    expect(split.useHiddenGeometry).toBe(false);
  });

  it('keeps the properties when a page is copied (worker message, JSON)', () => {
    const [page] = [...pagesByName(path.join(FIXTURES, 'gondola_v20.skp')).values()];
    expect(structuredClone(page).useCamera).toBe(true);
    expect(JSON.parse(JSON.stringify(page)).useHiddenObjects).toBe(true);
  });

  it('returns null when captureFlags is null', () => {
    const page = pageCaptureFlags(null);
    expect(page.captureFlags).toBeNull();
    for (const prop of ALL_USE_PROPS) {
      expect(page[prop]).toBeNull();
    }
  });

  it.each(YEARS)('reads all 11 scenes with capture flags, cameras and hidden layers (%i)', (year) => {
    const model = SkpFile.open(
      path.join(CPP_FIXTURES, `scene_capture_flags_${year}.skp`),
    ).parse();
    expect(model.pages.map((p) => p.name)).toEqual(SCENE_ORDER);
    const byName = new Map<string, Page>(model.pages.map((p) => [p.name, p]));

    const expected: Record<string, [number, ReadonlySet<UseProp>]> = {
      Camera: [0x001, new Set(['useCamera'])],
      RenderingOptions: [0x002, new Set(['useRenderingOptions'])],
      ShadowInfo: [0x004, new Set(['useShadowInfo'])],
      Axes: [0x008, new Set(['useAxes'])],
      Hidden: [0x190, new Set(['useHiddenGeometry', 'useHiddenObjects'])],
      HiddenGeometry: [0x090, new Set(['useHiddenGeometry'])],
      HiddenObjects: [0x110, new Set(['useHiddenObjects'])],
      HiddenLayers: [0x020, new Set(['useHiddenLayers'])],
      SectionPlanes: [0x040, new Set(['useSectionPlanes'])],
      ShadowsCamera: [0x005, new Set(['useShadowInfo', 'useCamera'])],
      AxesCamera: [0x009, new Set(['useAxes', 'useCamera'])],
    };
    for (const [sceneName, [wantFlags, trueProps]] of Object.entries(expected)) {
      const p = byName.get(sceneName)!;
      expect(p.captureFlags).toBe(wantFlags);
      for (const prop of ALL_USE_PROPS) {
        expect(p[prop]).toBe(trueProps.has(prop));
      }
    }

    const cam = byName.get('Camera')!;
    expect(cam.eye).toEqual([120, -240, 90]);
    expect(cam.target).toEqual([5, 5, 0]);
    expect(cam.up![0]).toBeCloseTo(-0.13407807210149128, 9);
    expect(cam.up![1]).toBeCloseTo(0.28564458839013357, 9);
    expect(cam.up![2]).toBeCloseTo(0.9489100271917137, 9);
    expect(cam.parallel).toBe(false);
    expect(cam.fov).toBeCloseTo(42, 9);

    const shadowsCam = byName.get('ShadowsCamera')!;
    expect(shadowsCam.eye).toEqual([0, -300, 150]);
    expect(shadowsCam.target).toEqual([0, 0, 0]);
    expect(shadowsCam.up![0]).toBeCloseTo(0, 9);
    expect(shadowsCam.up![1]).toBeCloseTo(0.44721359549995804, 9);
    expect(shadowsCam.up![2]).toBeCloseTo(0.8944271909999161, 9);
    expect(shadowsCam.parallel).toBe(true);
    expect(shadowsCam.orthoHeight).toBeCloseTo(80, 9);

    const axesCam = byName.get('AxesCamera')!;
    expect(axesCam.eye).toEqual([-180, -180, 110]);
    expect(axesCam.target).toEqual([10, 10, 0]);
    expect(axesCam.up![0]).toBeCloseTo(0.2678945632789157, 9);
    expect(axesCam.up![1]).toBeCloseTo(0.2678945632789157, 9);
    expect(axesCam.up![2]).toBeCloseTo(0.9254539458726179, 9);
    expect(axesCam.parallel).toBe(false);
    expect(axesCam.fov).toBeCloseTo(55, 9);

    for (const name of SCENE_ORDER) {
      const p = byName.get(name)!;
      if (name !== 'Camera' && name !== 'ShadowsCamera' && name !== 'AxesCamera') {
        expect(p.eye).toBeNull();
        expect(p.target).toBeNull();
      }
      if (name === 'HiddenLayers') {
        expect(p.hiddenLayers).toEqual(['HiddenTag']);
      } else {
        expect(p.hiddenLayers).toEqual([]);
      }
    }
  });

  it.each(YEARS)('resolves hidden entities in 2017+ and drops slot refs in 2015–2016 (%i)', (year) => {
    const model = SkpFile.open(
      path.join(CPP_FIXTURES, `scene_capture_flags_${year}.skp`),
    ).parse();
    const byName = new Map<string, Page>(model.pages.map((p) => [p.name, p]));
    if (year < 2017) {
      for (const p of model.pages) {
        expect(p.hiddenEntityIds).toEqual([]);
      }
    } else {
      const crate = model.root.instances.find((i) => i.name === 'Crate');
      expect(crate?.id).toBe(30);
      expect(model.root.edges.some((e) => e.persistentId === 21)).toBe(true);
      expect(new Set(byName.get('Hidden')!.hiddenEntityIds)).toEqual(new Set([21, 30]));
      expect(byName.get('HiddenGeometry')!.hiddenEntityIds).toEqual([21]);
      expect(byName.get('HiddenObjects')!.hiddenEntityIds).toEqual([30]);
      for (const name of SCENE_ORDER) {
        if (name !== 'Hidden' && name !== 'HiddenGeometry' && name !== 'HiddenObjects') {
          expect(byName.get(name)!.hiddenEntityIds).toEqual([]);
        }
      }
    }
  });
});
