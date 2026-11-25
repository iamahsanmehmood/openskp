/**
 * Raw `.skp` byte input accepted by OpenSKP's buffer-based entry points: an
 * `ArrayBuffer` or any `Uint8Array` (including a Node.js `Buffer`).
 */
export type BinaryInput = ArrayBuffer | Uint8Array;

/**
 * Return a `Uint8Array` view over `input` without copying, preserving
 * `byteOffset` and `byteLength` when `input` views a slice of a larger
 * `ArrayBuffer` (such as a Node.js `Buffer` allocated from the shared pool).
 */
export function toUint8ArrayView(input: BinaryInput): Uint8Array {
  if (ArrayBuffer.isView(input)) {
    // Never the Buffer itself: its `slice` is a view where Uint8Array's is a
    // copy, and the textures read from it would be Buffers too.
    return new Uint8Array(input.buffer, input.byteOffset, input.byteLength);
  }
  return new Uint8Array(input);
}
