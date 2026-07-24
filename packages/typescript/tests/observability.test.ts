import { describe, it, expect } from 'vitest';
import * as fs from 'fs';
import * as path from 'path';
import { parseSkp, buildScene } from '../src/index';
import { SkpParseError } from '../src/errors';
import { LogLevel } from '../src/observability';

/**
 * openskp exposes progress via optional onProgress/onLog callbacks (see
 * observability.ts) - silent by default, and raises SkpParseError with
 * structured location context (stage, recordIndex, tag, ...) on failure -
 * so a production pipeline can trace exactly where a model got stuck
 * instead of a bare stack trace.
 */
describe('Observability: progress callbacks + structured error context', () => {
  const filePath = path.join(__dirname, 'fixtures', 'capilla_quiroz_v17.skp');
  const buf = fs.readFileSync(filePath);
  const arrayBuffer = buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength) as ArrayBuffer;

  it('is silent by default (no callbacks fire without options)', () => {
    // Nothing to assert on directly - this just verifies parseSkp() works
    // fine with no options argument, matching the "silent by default"
    // design (no forced logging/printing of any kind).
    const model = parseSkp(arrayBuffer);
    expect(model.version).toBe('{17.0.18899}');
  });

  it('reports onLog messages for a real parse', () => {
    const messages: { level: LogLevel; message: string }[] = [];
    parseSkp(arrayBuffer, { onLog: (level, message) => messages.push({ level, message }) });

    expect(messages.some((m) => m.message.includes('Parsing legacy'))).toBe(true);
    expect(messages.some((m) => m.message.includes('Parse complete'))).toBe(true);
  });

  it('reports onLog messages for buildScene', () => {
    const messages: { level: LogLevel; message: string }[] = [];
    buildScene(arrayBuffer, { onLog: (level, message) => messages.push({ level, message }) });

    expect(messages.some((m) => m.message.includes('Building scene'))).toBe(true);
    expect(messages.some((m) => m.message.includes('Scene build complete'))).toBe(true);
  });

  it('raises SkpParseError with stage="zip_extract" for a corrupt file', () => {
    const bad = new Uint8Array(200).fill(0x41); // "AAAA..." - not a valid header
    const badBuffer = bad.buffer;

    expect(() => parseSkp(badBuffer)).toThrow(SkpParseError);
    try {
      parseSkp(badBuffer);
      expect.fail('expected parseSkp to throw');
    } catch (e) {
      expect(e).toBeInstanceOf(SkpParseError);
      expect((e as SkpParseError).stage).toBe('zip_extract');
    }
  });

  it('SkpParseError message includes structured context', () => {
    const err = new SkpParseError('boom', { stage: 'tlv_walk', recordIndex: 3, totalRecords: 10, tag: 'F601' });
    expect(err.message).toContain('stage=tlv_walk');
    expect(err.message).toContain('record=3/10');
    expect(err.message).toContain('tag=F601');
  });

  it('SkpParseError preserves the original error as .cause', () => {
    const original = new Error('inner failure');
    const wrapped = new SkpParseError('wrapped', { stage: 'tlv_walk', cause: original });
    expect(wrapped.cause).toBe(original);
  });
});
