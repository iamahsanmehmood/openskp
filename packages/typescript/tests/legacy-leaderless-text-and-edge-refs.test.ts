import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';
import {
  SkpParseError,
  buildInstancedScene,
  buildScene,
  parseSkp,
} from '../src/index';

const FIXTURE = path.resolve(
  __dirname,
  'fixtures',
  'sdk_saved_leaderless_text_edges_2020.skp'
);

describe('legacy leaderless CText and CEdge vertex validation (#592)', () => {
  it('reads a leaderless text before shared-vertex loose edges with SDK parity', () => {
    const raw = new Uint8Array(fs.readFileSync(FIXTURE));
    const warnings: string[] = [];
    const options = {
      onLog: (level: string, msg: string) => {
        if (level === 'warn') warnings.push(msg);
      },
    };

    const model = parseSkp(raw, options);
    const scene = buildScene(raw, options);
    const instanced = buildInstancedScene(raw, options);

    expect(model.definitions.size).toBe(1);
    const post = Array.from(model.definitions.values())[0];
    expect(post.name).toBe('Post');
    expect(post.isGroup).toBe(false);
    expect(model.root.faces).toHaveLength(0);
    expect(model.root.edges).toHaveLength(3);
    expect(model.root.instances).toHaveLength(1);
    expect(model.root.texts.map((t) => t.text)).toEqual(['Screen title', 'Callout']);
    expect(model.unreadRootEntities).toBe(0);
    expect(scene.glbPrimitives).toHaveLength(1);
    expect(instanced.meshResources).toHaveLength(1);
    expect(warnings).toEqual([]);
  });

  it('reports a root edge whose vertex ref is not a vertex as a partial read', () => {
    const raw = new Uint8Array(fs.readFileSync(FIXTURE));
    expect(raw[5323]).toBe(0x31);
    expect(raw[5324]).toBe(0x00);
    raw[5323] = 0x2f; // point 2nd root edge v1 at 1st root CEdge

    const warnings: string[] = [];
    const options = {
      onLog: (level: string, msg: string) => {
        if (level === 'warn') warnings.push(msg);
      },
    };

    const model = parseSkp(raw, options);
    const scene = buildScene(raw);
    const instanced = buildInstancedScene(raw);

    expect(model.root.instances).toHaveLength(1);
    expect(model.root.texts.map((t) => t.text)).toEqual(['Screen title', 'Callout']);
    expect(model.root.edges).toHaveLength(1);
    expect(model.unreadRootEntities).toBe(2);
    expect(scene.glbPrimitives).toHaveLength(1);
    expect(instanced.meshResources).toHaveLength(1);
    expect(warnings.some((w) => w.includes('2 unread'))).toBe(true);
  });

  it('throws SkpParseError when a definition edge vertex ref is not a vertex', () => {
    const raw = new Uint8Array(fs.readFileSync(FIXTURE));
    expect(raw[4039]).toBe(0x14);
    expect(raw[4040]).toBe(0x00);
    raw[4039] = 0x11; // point 2nd definition edge v1 at 1st definition CEdge

    expect(() => parseSkp(raw)).toThrow(SkpParseError);
    expect(() => buildScene(raw)).toThrow(SkpParseError);
    expect(() => buildInstancedScene(raw)).toThrow(SkpParseError);
  });
});
