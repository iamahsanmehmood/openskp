import { describe, it, expect } from 'vitest';
import * as fs from 'fs';
import * as path from 'path';
import { parseSkp } from '../src/index';

/**
 * readSectionPlane read and discarded the plane's 4 doubles (and its
 * name/label/drawbase) entirely - the returned record carried only
 * `{ k: 'sectionplane' }`, so the consumer's own `v.plane || [0, 0, 1, 0]`
 * fallback silently masked the gap with a plausible-looking default rather
 * than surfacing a missing value. Every section plane this reader has ever
 * parsed reported the same wrong `[0, 0, 1, 0]` regardless of its real
 * orientation/position, and `hidden` always read false regardless of the
 * file's real state. Found while investigating openskp#285's "Writer:
 * section planes" item (the reader turned out to already exist, just
 * broken).
 *
 * legacy_annotations.skp was generated with Python's own create() API
 * (add_face + add_section_plane + add_text + add_dimension) and its values
 * cross-checked against Python's own reader as ground truth - this
 * project's own writer doesn't have add_section_plane/add_text/
 * add_dimension yet (a separate, still-open openskp#285 item).
 */

function toBuffer(bytes: Uint8Array): ArrayBuffer {
  return bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer;
}

const fixture = fs.readFileSync(path.join(__dirname, 'fixtures', 'legacy_annotations.skp'));
const model = parseSkp(toBuffer(fixture));

describe('legacy section plane / text / dimension reading (openskp#285)', () => {
  it('reads the real plane coefficients, not the [0, 0, 1, 0] fallback default', () => {
    expect(model.root.sectionPlanes).toHaveLength(1);
    const sp = model.root.sectionPlanes[0];
    expect(sp.plane[0]).toBeCloseTo(0.0, 6);
    expect(sp.plane[1]).toBeCloseTo(0.0, 6);
    expect(sp.plane[2]).toBeCloseTo(1.0, 6);
    expect(sp.plane[3]).toBeCloseTo(-30.0, 6);
    expect(sp.hidden).toBe(false);
  });

  it('reads the text entity', () => {
    expect(model.root.texts).toHaveLength(1);
    expect(model.root.texts[0].text).toBe('Hello');
    expect(model.root.texts[0].hidden).toBe(false);
  });

  it('reads the dimension entity', () => {
    expect(model.root.dimensions).toHaveLength(1);
  });
});
