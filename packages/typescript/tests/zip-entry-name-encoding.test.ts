import { describe, it, expect } from 'vitest';
import { zipSync } from 'fflate';
import { randomBytes } from 'node:crypto';
import { extractSkpContents } from '../src/vff';

/**
 * SketchUp stores ZIP entry names as UTF-8 but leaves the language encoding
 * flag (general purpose bit 11) clear, so a spec-compliant reader decodes them
 * as CP437/Latin-1: a material folder named "ДСП Egger" arrives as
 * "ÐÐ¡Ð Egger". The mangled folder name is later matched against the
 * material's real name, and when it does not match the material keeps
 * `id: null` - every face using it then falls back to its layer colour and
 * loses its texture.
 */

function buildSkpBytes(zipBytes: Uint8Array): Uint8Array {
  const header = new Uint8Array(16); // VFF magic + padding
  header.set([0xff, 0xfe, 0xff, 0x0e], 0);
  const combined = new Uint8Array(header.length + zipBytes.length);
  combined.set(header, 0);
  combined.set(zipBytes, header.length);
  return combined;
}

/** What a Latin-1 reader makes of a UTF-8 name - the state we receive. */
function asLatin1(text: string): string {
  const utf8 = new TextEncoder().encode(text);
  let out = '';
  for (const byte of utf8) out += String.fromCharCode(byte);
  return out;
}

describe('ZIP entry names decoded as Latin-1', () => {
  it('restores a non-ASCII material folder name', () => {
    const folder = 'ДСП Egger F 204';
    const zipBytes = zipSync({
      'model.dat': new Uint8Array(randomBytes(1024)),
      [`materials/${asLatin1(folder)}/material.xml`]: new TextEncoder().encode('<material/>'),
    });

    const contents = extractSkpContents(buildSkpBytes(zipBytes));

    expect(Object.keys(contents.materialFiles)).toContain(`materials/${folder}/material.xml`);
  });

  it('leaves an ASCII name untouched', () => {
    const zipBytes = zipSync({
      'model.dat': new Uint8Array(randomBytes(1024)),
      'materials/Metal_Seamed/material.xml': new TextEncoder().encode('<material/>'),
    });

    const contents = extractSkpContents(buildSkpBytes(zipBytes));

    expect(Object.keys(contents.materialFiles)).toContain('materials/Metal_Seamed/material.xml');
  });

  it('keeps a name that is not valid UTF-8 as it is', () => {
    // 0xFF never starts a valid UTF-8 sequence, so the name stays as read
    // rather than being mangled further by a hopeful re-decode.
    const name = `materials/${String.fromCharCode(0xff)}odd/material.xml`;
    const zipBytes = zipSync({
      'model.dat': new Uint8Array(randomBytes(1024)),
      [name]: new TextEncoder().encode('<material/>'),
    });

    const contents = extractSkpContents(buildSkpBytes(zipBytes));

    expect(Object.keys(contents.materialFiles)).toContain(name);
  });
});
