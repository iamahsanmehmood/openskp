import { describe, it, expect } from 'vitest';
import * as fs from 'fs';
import * as path from 'path';
import { create, parseSkp, buildScene, buildInstancedScene, fromFragments, toFragments } from '../src/index';
import { LogLevel } from '../src/observability';

function toArrayBuffer(bytes: Uint8Array): ArrayBuffer {
  return bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer;
}

function findSubsequence(haystack: Uint8Array, needle: number[]): number {
  outer: for (let i = 0; i + needle.length <= haystack.length; i++) {
    for (let j = 0; j < needle.length; j++) {
      if (haystack[i + j] !== needle[j]) continue outer;
    }
    return i;
  }
  return -1;
}

// The classic root reader keeps what it read before the first record it can't
// parse and drops the rest of the root. SkpModel.unreadRootEntities says how
// many entities that dropped and onLog emits a 'warn' message when it is
// nonzero, so a partial read isn't silent.
describe('Legacy unreadRootEntities', () => {
  it('complete files report 0 and emit no warning', () => {
    for (const name of [
      'legacy_annotations.skp',
      'capilla_quiroz_v17.skp',
      'gondola_v20.skp',
      'legacy_text_arrows_2017.skp',
      'nesting_2017.skp',
      'SU_File.skp',
    ]) {
      const buf = fs.readFileSync(path.join(__dirname, 'fixtures', name));
      const logs: { level: LogLevel; message: string }[] = [];
      const model = parseSkp(toArrayBuffer(buf), {
        onLog: (level, message) => logs.push({ level, message }),
      });
      expect(model.unreadRootEntities, name).toBe(0);
      expect(logs.some((entry) => entry.level === 'warn'), name).toBe(false);
    }
  });

  it('an unreadable text drops the rest of the root and logs a warning', () => {
    const builder = create();
    builder.addFace([
      [0, 0, 0],
      [10, 0, 0],
      [10, 10, 0],
      [0, 10, 0],
    ]);
    builder.addText('First', [0, 0, 0]);
    builder.addText('Broken', [5, 0, 0]);
    builder.addFace([
      [20, 0, 0],
      [30, 0, 0],
      [30, 10, 0],
      [20, 10, 0],
    ]);
    const bytes = new Uint8Array(builder.toBytes());
    const completeBuf = toArrayBuffer(bytes);
    const complete = parseSkp(completeBuf);
    expect(complete.root.texts.length).toBe(2);
    expect(complete.unreadRootEntities).toBe(0);
    expect(buildScene(completeBuf).unreadRootEntities).toBe(0);
    const completeInstanced = buildInstancedScene(completeBuf);
    expect(completeInstanced.unreadRootEntities).toBe(0);
    expect(fromFragments(toFragments(completeInstanced)).unreadRootEntities).toBe(0);

    // Break the second text's string marker so the reader can't find its text.
    const marker = [0x01, 0xff, 0xfe, 0xff, 0x06, 0x42, 0x00];
    const at = findSubsequence(bytes, marker);
    expect(at).toBeGreaterThanOrEqual(0);
    bytes.fill(0x00, at, at + 4);

    const logs: { level: LogLevel; message: string }[] = [];
    const brokenBuf = toArrayBuffer(bytes);
    const model = parseSkp(brokenBuf, {
      onLog: (level, message) => logs.push({ level, message }),
    });
    expect(model.root.texts.length).toBe(1);
    expect(model.root.texts[0].text).toBe('First');
    expect(model.unreadRootEntities).toBeGreaterThan(0);
    expect(buildScene(brokenBuf).unreadRootEntities).toBe(model.unreadRootEntities);
    expect(buildInstancedScene(brokenBuf).unreadRootEntities).toBe(model.unreadRootEntities);
    expect(
      logs.some(
        (entry) =>
          entry.level === 'warn' &&
          entry.message.includes('stopped early') &&
          entry.message.includes(`${model.unreadRootEntities} unread`)
      )
    ).toBe(true);
  });
});
