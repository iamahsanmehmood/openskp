import { describe, it, expect } from 'vitest';
import { zipSync } from 'fflate';
import { parseSkp } from '../src/index';

function buildSyntheticSkp(entries: Record<string, Uint8Array>): ArrayBuffer {
  const allEntries: Record<string, Uint8Array> = {
    'model.dat': new Uint8Array(0),
    ...entries,
  };
  const zipBytes = zipSync(allEntries);
  const header = new Uint8Array(32); // VFF magic + padding
  header.set([0xff, 0xfe, 0xff, 0x0e], 0);
  const result = new Uint8Array(header.length + zipBytes.length);
  result.set(header, 0);
  result.set(zipBytes, header.length);
  return result.buffer.slice(result.byteOffset, result.byteOffset + result.byteLength) as ArrayBuffer;
}

const texturedMaterialXml = (name: string, filename: string) =>
  new TextEncoder().encode(`<?xml version="1.0" encoding="UTF-8"?>
<materialDocument xmlns="http://sketchup.google.com/schemas/sketchup/1.0/material"
                  xmlns:mat="http://sketchup.google.com/schemas/sketchup/1.0/material">
  <mat:material name="${name}" type="1" colorRed="10" colorGreen="20"
                colorBlue="30" trans="1" hasTexture="1">
    <mat:texture textureFilename="${filename}" xScale="24" yScale="12"/>
  </mat:material>
</materialDocument>
`);

const plainMaterialXml = new TextEncoder().encode(`<?xml version="1.0" encoding="UTF-8"?>
<materialDocument xmlns="http://sketchup.google.com/schemas/sketchup/1.0/material"
                  xmlns:mat="http://sketchup.google.com/schemas/sketchup/1.0/material">
  <mat:material name="Plain" type="0" colorRed="1" colorGreen="2"
                colorBlue="3" trans="1" hasTexture="0"/>
</materialDocument>
`);

describe('Texture extraction', () => {
  it('extracts a textured material (filename, tile size, image bytes)', () => {
    const jpeg = new Uint8Array([0xff, 0xd8, ...Array.from(new TextEncoder().encode('syntheticjpegbytes'))]);
    const buf = buildSyntheticSkp({
      'materials/Wood/material.xml': texturedMaterialXml('Wood', 'wood.jpg'),
      'materials/Wood/wood.jpg': jpeg,
      'materials/Plain/material.xml': plainMaterialXml,
    });
    const model = parseSkp(buf);

    const byName = new Map(model.materials.map((m) => [m.name, m]));
    const wood = byName.get('Wood');
    expect(wood).toBeDefined();
    expect(wood!.texture).not.toBeNull();
    expect(wood!.texture!.filename).toBe('wood.jpg');
    expect(wood!.texture!.width).toBe(24);
    expect(wood!.texture!.height).toBe(12);
    expect(wood!.texture!.data).toEqual(jpeg);
    expect(byName.get('Plain')!.texture).toBeNull();
  });

  it('falls back to the folder sibling when the stored image name mismatches textureFilename', () => {
    // Observed in real files: XML says "..._Safety.jpg" while the stored
    // image is "..._Saftey.jpg" - the folder sibling must win.
    const jpeg = new Uint8Array([0xff, 0xd8, ...Array.from(new TextEncoder().encode('siblingbytes'))]);
    const buf = buildSyntheticSkp({
      'materials/Glass/material.xml': texturedMaterialXml('Glass', 'glass_safety.jpg'),
      'materials/Glass/glass_saftey.jpg': jpeg,
    });
    const model = parseSkp(buf);

    const glass = model.materials.find((m) => m.name === 'Glass');
    expect(glass).toBeDefined();
    expect(glass!.texture).not.toBeNull();
    expect(glass!.texture!.data).toEqual(jpeg);
  });

  it('resolves a colourized copy sharing the source material image', () => {
    // A colourized copy ("[Name]1", type="2") stores no image of its own -
    // its <mat:image path> points into the SOURCE material's folder.
    const png = new Uint8Array([0x89, ...Array.from(new TextEncoder().encode('PNGsharedchainlink'))]);
    const colorizedXml = new TextEncoder().encode(`<?xml version="1.0" encoding="UTF-8"?>
<materialDocument xmlns="http://sketchup.google.com/schemas/sketchup/1.0/material"
                  xmlns:mat="http://sketchup.google.com/schemas/sketchup/1.0/material">
  <mat:material name="[Fence]1" type="2" colorRed="27" colorGreen="135"
                colorBlue="59" colorizeType="0" trans="1" hasTexture="1">
    <mat:texture textureFilename="fence.png" xScale="2.75" yScale="2.75">
      <mat:images>
        <mat:image id="1" path="materials/Fence/fence.png" file_name="fence.png"/>
      </mat:images>
    </mat:texture>
  </mat:material>
</materialDocument>
`);
    const buf = buildSyntheticSkp({
      'materials/Fence/material.xml': texturedMaterialXml('Fence', 'fence.png'),
      'materials/Fence/fence.png': png,
      'materials/[Fence]1/material.xml': colorizedXml,
    });
    const model = parseSkp(buf);

    const byName = new Map(model.materials.map((m) => [m.name, m]));
    const copy = byName.get('[Fence]1');
    expect(copy).toBeDefined();
    expect(copy!.texture).not.toBeNull();
    expect(copy!.texture!.data).toEqual(png); // borrowed from materials/Fence/
    expect(copy!.colorized).toBe(true);
    expect(copy!.colorizeType).toBe(0);
    expect(copy!.color).toEqual({ r: 27, g: 135, b: 59 });

    const base = byName.get('Fence');
    expect(base!.colorized).toBe(false);
  });
});
