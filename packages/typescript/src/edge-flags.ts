/**
 * Storage for edges' D307 display-flag byte, keyed by edge id.
 *
 * The payload is a SINGLE BYTE per edge (base 0x06, plus 0x01 hidden,
 * 0x08 soft, 0x10 smooth), which a `Map<number, number>` stores at roughly
 * 30 bytes per entry - about 23x the data itself, once V8's boxed-number
 * and hash-bucket overhead is counted. On a large model that is tens of
 * megabytes spent on a value that fits in a `Uint8Array` slot.
 *
 * Backed by a `Uint8Array` indexed by `id - baseId`, grown geometrically.
 * Edge ids are not dense in real files (~39% across this repository's
 * fixtures), so the array is sized to the observed id SPAN rather than the
 * edge count - still far smaller than the Map it replaces, because one
 * slot costs one byte instead of a whole hash entry.
 *
 * Semantics match the `Map` exactly, including the distinction the legacy
 * (pre-2021 MFC) reader relies on: it only records an edge when its flags
 * are non-zero, so an id that was never written must read back as 0 and
 * report `has() === false`. A separate presence bitmap keeps that
 * observable difference intact rather than conflating "absent" with
 * "stored zero".
 */
export class EdgeFlagStore {
  /** Flag byte per slot; index is `id - baseId`. */
  private flags: Uint8Array = new Uint8Array(0);
  /** One bit per slot recording whether that id was ever written. */
  private present: Uint8Array = new Uint8Array(0);
  /** Id that maps to slot 0. Set on first write. */
  private baseId = 0;
  private initialized = false;
  private count = 0;

  /** Number of ids actually stored. */
  get size(): number {
    return this.count;
  }

  private slotFor(id: number): number {
    return id - this.baseId;
  }

  /** Grow (and, for an id below `baseId`, shift) so `id` has a slot. */
  private ensureSlot(id: number): number {
    if (!this.initialized) {
      this.baseId = id;
      this.flags = new Uint8Array(64);
      this.present = new Uint8Array(8);
      this.initialized = true;
      return 0;
    }

    if (id < this.baseId) {
      // An id below the current base: re-base, shifting existing data up.
      const shift = this.baseId - id;
      const needed = shift + this.flags.length;
      const nextFlags = new Uint8Array(Math.max(needed, this.flags.length * 2));
      nextFlags.set(this.flags, shift);
      this.flags = nextFlags;

      const nextPresent = new Uint8Array(Math.ceil(nextFlags.length / 8));
      // Re-stamp presence bit by bit: the bitmap is not byte-aligned to the
      // shift, so it cannot simply be block-copied.
      for (let slot = 0; slot < this.flags.length - shift; slot++) {
        if ((this.present[slot >> 3] & (1 << (slot & 7))) !== 0) {
          const moved = slot + shift;
          nextPresent[moved >> 3] |= 1 << (moved & 7);
        }
      }
      this.present = nextPresent;
      this.baseId = id;
      return 0;
    }

    const slot = this.slotFor(id);
    if (slot >= this.flags.length) {
      const nextLength = Math.max(slot + 1, this.flags.length * 2);
      const nextFlags = new Uint8Array(nextLength);
      nextFlags.set(this.flags);
      this.flags = nextFlags;

      const nextPresent = new Uint8Array(Math.ceil(nextLength / 8));
      nextPresent.set(this.present);
      this.present = nextPresent;
    }
    return slot;
  }

  set(id: number, flags: number): void {
    const slot = this.ensureSlot(id);
    if ((this.present[slot >> 3] & (1 << (slot & 7))) === 0) {
      this.present[slot >> 3] |= 1 << (slot & 7);
      this.count++;
    }
    this.flags[slot] = flags & 0xff;
  }

  /** The stored byte, or `undefined` when the id was never written -
   * matching `Map.prototype.get`, so callers' `?? 0` fallbacks behave
   * exactly as before. */
  get(id: number): number | undefined {
    if (!this.initialized) return undefined;
    const slot = this.slotFor(id);
    if (slot < 0 || slot >= this.flags.length) return undefined;
    if ((this.present[slot >> 3] & (1 << (slot & 7))) === 0) return undefined;
    return this.flags[slot];
  }

  has(id: number): boolean {
    return this.get(id) !== undefined;
  }
}
