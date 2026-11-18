import { describe, it, expect } from 'vitest';
import { create, type ComponentDefinitionBuilder } from '../src/create';
import { buildScene, buildInstancedScene, toGLB, toInstancedGLB } from '../src/index';

/**
 * glTF 2.0 requires every GLB chunk to start on a 4-byte boundary, so the
 * JSON chunk has to be padded to a multiple of 4 *bytes*. Padding by the JS
 * string's UTF-16 length leaves the chunk misaligned as soon as a node or
 * material name has a character outside ASCII (it takes 2-4 bytes in UTF-8).
 */
function nonAsciiModel(): ArrayBuffer {
  const builder = create();
  const wood = builder.addMaterial('Kayu ñ 木材', [200, 150, 100, 255]);
  const chair = builder.addComponentDefinition('Kursi ñ 椅子', (def: ComponentDefinitionBuilder) => {
    def.addFace([[0, 0, 0], [10, 0, 0], [10, 10, 0], [0, 10, 0]], { material: wood });
  });
  builder.addInstance(chair, { name: 'Kursi ñ 椅子' });
  return builder.toBytes().slice().buffer;
}

function expectAlignedChunks(glb: Uint8Array): void {
  const view = new DataView(glb.buffer, glb.byteOffset, glb.byteLength);
  expect(view.getUint32(0, true)).toBe(0x46546c67); // 'glTF'
  expect(view.getUint32(8, true)).toBe(glb.byteLength);

  const jsonLength = view.getUint32(12, true);
  expect(view.getUint32(16, true)).toBe(0x4e4f534a); // 'JSON'
  expect(jsonLength % 4).toBe(0);
  // The JSON still parses with the padding, and the names survive.
  const jsonText = new TextDecoder().decode(glb.subarray(20, 20 + jsonLength));
  expect(jsonText).toContain('Kursi ñ 椅子');
  JSON.parse(jsonText);

  const binHeader = 20 + jsonLength;
  expect(binHeader % 4).toBe(0);
  expect(view.getUint32(binHeader + 4, true)).toBe(0x004e4942); // 'BIN'
  const binLength = view.getUint32(binHeader, true);
  expect(binLength % 4).toBe(0);
  expect(binHeader + 8 + binLength).toBe(glb.byteLength);
}

describe('GLB JSON chunk padding with non-ASCII names', () => {
  it('toGLB pads the JSON chunk by UTF-8 byte length', () => {
    // toGLB writes scene.gltfMaterials through unchanged, so a material
    // name a caller sets ends up in the JSON chunk.
    const scene = buildScene(nonAsciiModel());
    scene.gltfMaterials = scene.gltfMaterials.map((mat) => ({ ...(mat as object), name: 'Kursi ñ 椅子' }));
    expectAlignedChunks(toGLB(scene));
  });

  it('toInstancedGLB pads the JSON chunk by UTF-8 byte length', () => {
    expectAlignedChunks(toInstancedGLB(buildInstancedScene(nonAsciiModel())));
  });
});
