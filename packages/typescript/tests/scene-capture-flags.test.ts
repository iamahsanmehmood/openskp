import * as path from 'path';
import { describe, expect, it } from 'vitest';
import { Page, SkpFile } from '../src';

const CPP_FIXTURES = path.resolve(__dirname, '../../cpp/tests/fixtures');

const YEARS = [2015, 2016, 2017, 2018, 2019, 2020] as const;

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

describe('Classic 2015–2020 scene capture flags and section lengths (#661)', () => {
  it.each(YEARS)('reads all 11 scenes with cameras and hidden layers (%i)', (year) => {
    const model = SkpFile.open(
      path.join(CPP_FIXTURES, `scene_capture_flags_${year}.skp`),
    ).parse();
    expect(model.pages.map((p) => p.name)).toEqual(SCENE_ORDER);
    const byName = new Map<string, Page>(model.pages.map((p) => [p.name, p]));

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
