import { describe, it, expect } from 'vitest';
import * as fs from 'fs';
import * as path from 'path';
import { parseSkp } from '../src/index';
import { isLegacy } from '../src/legacy';

/**
 * Regression for SketchUp 2018 (file version 18) saves with no CMaterial
 * records and a custom tag listed ahead of Layer0.
 *
 * Those files take probeLayerAnchorBases (the two-material bootstrap needs
 * matCount >= 2). The declared layerCount is 1; Layer0 follows after a
 * 16-byte colour-layer extension. Missing that extension left the probe
 * on padding: `base probe: anchor resolved to`.
 *
 * Fixture is a SketchUp 2018 layout sample (two construction lines, no
 * faces). Identifiable strings were replaced with same-length ASCII so the
 * MFC record sizes are unchanged.
 */
describe('Legacy MFC reader - zero-material custom tag ahead of Layer0', () => {
  const fixturePath = path.join(__dirname, 'fixtures', 'zero_material_custom_layer_v18.skp');

  function load(filePath: string) {
    const buf = fs.readFileSync(filePath);
    const data = new Uint8Array(buf.buffer, buf.byteOffset, buf.byteLength);
    const arrayBuffer = buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength) as ArrayBuffer;
    return { data, arrayBuffer };
  }

  it('detects the fixture as a legacy container', () => {
    expect(isLegacy(load(fixturePath).data)).toBe(true);
  });

  it('parses a v18 file with a custom tag ahead of Layer0', () => {
    const model = parseSkp(load(fixturePath).arrayBuffer);
    expect(model.version).toBe('{18.0.16975}');
    expect(model.materials.length).toBe(0);
    const names = model.layers.map((l) => l.name);
    expect(names).toContain('Layer0');
    expect(names).toContain('Guide');
    expect(model.layers.length).toBeGreaterThanOrEqual(2);
    expect(model.root.faces.length).toBe(0);
    expect(model.root.constructionLines.length).toBe(2);
  });
});
