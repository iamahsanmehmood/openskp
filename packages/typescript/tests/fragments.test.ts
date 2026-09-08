import { describe, it, expect } from 'vitest';
import * as fs from 'fs';
import * as path from 'path';
import * as fflate from 'fflate';
import * as flatbuffers from 'flatbuffers';
import { toFragments, buildInstancedScene, SkpFile } from '../src/index';
import { buildInstancedSceneFromParsed } from '../src/instanced';
import { repeatedComponentScene } from './helpers/instanced-fixtures';

describe('toFragments', () => {
  it('converts synthetic instanced scene to deflated .frag bytes', () => {
    const scene = buildInstancedSceneFromParsed(repeatedComponentScene(10, 4));
    const fragBytes = toFragments(scene);

    expect(fragBytes).toBeInstanceOf(Uint8Array);
    expect(fragBytes.length).toBeGreaterThan(0);

    // Verify it is zlib compressed (inflate without throwing)
    const inflated = fflate.unzlibSync(fragBytes);
    expect(inflated.length).toBeGreaterThan(fragBytes.length);

    // Verify FlatBuffers buffer validity
    const bb = new flatbuffers.ByteBuffer(inflated);
    const rootPos = bb.readInt32(bb.position()) + bb.position();
    expect(rootPos).toBeGreaterThan(0);
  });

  it('supports raw uncompressed output', () => {
    const scene = buildInstancedSceneFromParsed(repeatedComponentScene(5, 4));
    const rawBytes = toFragments(scene, { raw: true });

    expect(rawBytes).toBeInstanceOf(Uint8Array);
    const bb = new flatbuffers.ByteBuffer(rawBytes);
    const rootPos = bb.readInt32(bb.position()) + bb.position();
    expect(rootPos).toBeGreaterThan(0);
  });

  it('converts real SketchUp fixture SU_File.skp', () => {
    const fixturePath = path.join(__dirname, 'fixtures', 'SU_File.skp');
    if (fs.existsSync(fixturePath)) {
      const buffer = fs.readFileSync(fixturePath);
      const scene = buildInstancedScene(buffer.buffer.slice(buffer.byteOffset, buffer.byteOffset + buffer.byteLength));
      const fragBytes = toFragments(scene);

      expect(fragBytes.length).toBeGreaterThan(0);

      const inflated = fflate.unzlibSync(fragBytes);
      const bb = new flatbuffers.ByteBuffer(inflated);
      const rootPos = bb.readInt32(bb.position()) + bb.position();
      expect(rootPos).toBeGreaterThan(0);
    }
  });

  it('works via SkpFile.toFragments() API', () => {
    const fixturePath = path.join(__dirname, 'fixtures', 'SU_File.skp');
    if (fs.existsSync(fixturePath)) {
      const skpFile = SkpFile.open(fixturePath);
      const fragBytes = skpFile.toFragments();

      expect(fragBytes.length).toBeGreaterThan(0);
      const inflated = fflate.unzlibSync(fragBytes);
      expect(inflated.length).toBeGreaterThan(fragBytes.length);
    }
  });

  it('converts complex model gondola_v20.skp with preserved instancing', () => {
    const fixturePath = path.join(__dirname, 'fixtures', 'gondola_v20.skp');
    if (fs.existsSync(fixturePath)) {
      const buffer = fs.readFileSync(fixturePath);
      const scene = buildInstancedScene(buffer.buffer.slice(buffer.byteOffset, buffer.byteOffset + buffer.byteLength));
      const fragBytes = toFragments(scene);

      expect(fragBytes.length).toBeGreaterThan(0);
      // Compresses to a fraction of the original
      expect(fragBytes.length).toBeLessThan(buffer.length);
    }
  });
});
