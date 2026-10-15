/**
 * Edge endpoint storage.
 *
 * A `Map<number, [v1, v2]>` costs ~100 bytes per edge: every `[v1, v2]` is
 * a separate JS array with its own object header, and every Map entry adds
 * hash-bucket overhead on top. The endpoints themselves are two vertex ids.
 * On a real 411 MB legacy file that is 0.24 GB of the 2.27 GB the reader
 * keeps alive after the walk (openskp#201).
 *
 * The endpoints move into one flat `Uint32Array` (`[v1, v2, v1, v2, ...]`
 * in insertion order), next to an insertion-ordered id list, as
 * `vertex-store.ts` does for coordinates. Like vertex ids, edge ids are not
 * dense in real files (~39% across this repository's fixtures, see
 * `edge-flags.ts`), so an id -> index mapping is needed, and this module
 * provides three behind one interface so they can be benchmarked rather
 * than chosen blind:
 *
 * - {@link MapEdgeStore}: `Map<id, index>`. O(1) lookup; the Map is the
 *   dominant remaining cost.
 * - {@link SortedEdgeStore}: a sorted `Uint32Array` of ids plus binary
 *   search. O(log n) lookup, denser.
 * - {@link DenseEdgeStore}: an `Int32Array` indexed by `id - baseId`, sized
 *   to the id span (like `EdgeFlagStore`). O(1) and the densest at real
 *   id densities, with a guard that falls back to a Map when the ids are
 *   too sparse for a span-sized array to pay off.
 *
 * All three preserve insertion order for iteration, which
 * `buildDefinition()` relies on, and reproduce the Map's values exactly:
 * an endpoint is a vertex id (u32 in both formats) or `null`. Anything else
 * - which neither reader produces today - is kept verbatim in a side Map
 * rather than narrowed, so the store can never silently change a value.
 */

/** An edge's endpoints, as the readers store them. */
export type EdgeEnds = [number | null, number | null];

/** Read/write access to a definition's edges, keyed by edge id. */
export interface EdgeStore {
  /** Number of stored edges. */
  readonly size: number;
  /** Store (or overwrite) an edge's endpoints. */
  set(id: number, ends: EdgeEnds): void;
  /** Endpoints for `id` as a fresh tuple, or `undefined` when absent -
   * matching `Map.prototype.get`. Allocates; hot paths use `v1`/`v2`. */
  get(id: number): EdgeEnds | undefined;
  has(id: number): boolean;
  /** First endpoint of `id`: a vertex id, `null`, or `undefined` when the
   * edge is absent. No allocation. */
  v1(id: number): number | null | undefined;
  /** Second endpoint of `id`, as {@link v1}. */
  v2(id: number): number | null | undefined;
  /** Ids and endpoints in insertion order. */
  entries(): IterableIterator<[number, EdgeEnds]>;
}

/** Initial capacity in edges; grown geometrically. */
const INITIAL_CAPACITY = 256;
/** `null` endpoint. Not a value either reader can produce as a vertex id
 * that fits the fast path (see `fits`). */
const NULL_END = 0xffffffff;
/** Endpoint stored in the side Map instead of the array. */
const SIDE_END = 0xfffffffe;

/** True when `v` can live in the `Uint32Array` as itself. */
function fits(v: number): boolean {
  return Number.isInteger(v) && v >= 0 && v < SIDE_END;
}

function checkId(id: number): void {
  if (!Number.isInteger(id) || id < 0 || id > 0xffffffff) {
    throw new RangeError(`edge id ${id} is not a u32`);
  }
}

/**
 * Shared endpoint storage. Subclasses differ only in how they map an id to
 * its slot.
 */
abstract class BaseEdgeStore implements EdgeStore {
  /** `[v1, v2]` per slot, in insertion order. */
  protected ends = new Uint32Array(INITIAL_CAPACITY * 2);
  /** Ids in insertion order; `idList[i]` owns slot `i`. */
  protected idList = new Uint32Array(INITIAL_CAPACITY);
  protected count = 0;
  /** Endpoints that are not a u32 vertex id or `null`, keyed by
   * `slot * 2 + end`. Empty on every real file measured. */
  private side: Map<number, unknown> | null = null;

  get size(): number {
    return this.count;
  }

  private grow(): void {
    if (this.count < this.idList.length) return;
    const nextCapacity = this.idList.length * 2;
    const nextEnds = new Uint32Array(nextCapacity * 2);
    nextEnds.set(this.ends);
    this.ends = nextEnds;
    const nextIds = new Uint32Array(nextCapacity);
    nextIds.set(this.idList);
    this.idList = nextIds;
  }

  /** Slot for `id`, or -1. */
  protected abstract slotOf(id: number): number;
  /** Record that `id` now owns `slot`. */
  protected abstract remember(id: number, slot: number): void;

  private write(at: number, v: number | null): void {
    if (v === null) {
      this.ends[at] = NULL_END;
    } else if (fits(v)) {
      this.ends[at] = v;
    } else {
      this.ends[at] = SIDE_END;
      (this.side ??= new Map()).set(at, v);
      return;
    }
    this.side?.delete(at);
  }

  private read(at: number): number | null {
    const v = this.ends[at];
    if (v === NULL_END) return null;
    if (v === SIDE_END) return this.side!.get(at) as number | null;
    return v;
  }

  set(id: number, ends: EdgeEnds): void {
    checkId(id);
    const existing = this.slotOf(id);
    const slot = existing >= 0 ? existing : this.count;
    if (existing < 0) {
      this.grow();
      this.idList[slot] = id;
      this.count++;
      this.remember(id, slot);
    }
    this.write(slot * 2, ends[0]);
    this.write(slot * 2 + 1, ends[1]);
  }

  get(id: number): EdgeEnds | undefined {
    const slot = this.slotOf(id);
    if (slot < 0) return undefined;
    return [this.read(slot * 2), this.read(slot * 2 + 1)];
  }

  has(id: number): boolean {
    return this.slotOf(id) >= 0;
  }

  v1(id: number): number | null | undefined {
    const slot = this.slotOf(id);
    return slot < 0 ? undefined : this.read(slot * 2);
  }

  v2(id: number): number | null | undefined {
    const slot = this.slotOf(id);
    return slot < 0 ? undefined : this.read(slot * 2 + 1);
  }

  *entries(): IterableIterator<[number, EdgeEnds]> {
    for (let i = 0; i < this.count; i++) {
      yield [this.idList[i], [this.read(i * 2), this.read(i * 2 + 1)]];
    }
  }
}

/** id -> slot via a `Map`. O(1) lookup; the Map dominates memory. */
export class MapEdgeStore extends BaseEdgeStore {
  private index = new Map<number, number>();

  protected slotOf(id: number): number {
    const slot = this.index.get(id);
    return slot === undefined ? -1 : slot;
  }

  protected remember(id: number, slot: number): void {
    this.index.set(id, slot);
  }
}

/**
 * id -> slot via binary search over a sorted id array. Ids arrive in
 * essentially ascending order in real files, so this is usually an append;
 * an out-of-order id is inserted in place.
 */
export class SortedEdgeStore extends BaseEdgeStore {
  /** Ids in ASCENDING order (unlike `idList`, which is insertion order). */
  private sortedIds = new Uint32Array(INITIAL_CAPACITY);
  /** `slots[i]` is the storage slot for `sortedIds[i]`. */
  private slots = new Uint32Array(INITIAL_CAPACITY);
  private sortedCount = 0;

  /** Index into `sortedIds` where `id` is, or `-(insertionPoint) - 1`. */
  private search(id: number): number {
    let lo = 0;
    let hi = this.sortedCount - 1;
    while (lo <= hi) {
      const mid = (lo + hi) >>> 1;
      const v = this.sortedIds[mid];
      if (v === id) return mid;
      if (v < id) lo = mid + 1;
      else hi = mid - 1;
    }
    return -lo - 1;
  }

  protected slotOf(id: number): number {
    const at = this.search(id);
    return at >= 0 ? this.slots[at] : -1;
  }

  protected remember(id: number, slot: number): void {
    if (this.sortedCount >= this.sortedIds.length) {
      const nextCapacity = this.sortedIds.length * 2;
      const nextIds = new Uint32Array(nextCapacity);
      nextIds.set(this.sortedIds);
      this.sortedIds = nextIds;
      const nextSlots = new Uint32Array(nextCapacity);
      nextSlots.set(this.slots);
      this.slots = nextSlots;
    }
    // `remember` is only called for ids not already present.
    const pos = -this.search(id) - 1;
    if (pos < this.sortedCount) {
      this.sortedIds.copyWithin(pos + 1, pos, this.sortedCount);
      this.slots.copyWithin(pos + 1, pos, this.sortedCount);
    }
    this.sortedIds[pos] = id;
    this.slots[pos] = slot;
    this.sortedCount++;
  }
}

/** Below this many span entries the dense index is always allowed. */
const DENSE_MIN_SPAN = 1 << 16;
/** Span entries allowed per stored edge before falling back to a Map:
 * at 4 bytes per entry, 8 keeps the index under 32 bytes per edge, still
 * well below a Map entry. */
const DENSE_MAX_SPAN_PER_EDGE = 8;

/**
 * id -> slot via an `Int32Array` indexed by `id - baseId` (-1 = absent),
 * sized to the observed id span. Re-basing for an id below the base leaves
 * headroom proportional to the span used, the fix #394 made to
 * `EdgeFlagStore`, so descending ids re-base O(log n) times. When the span
 * would exceed {@link DENSE_MAX_SPAN_PER_EDGE} entries per edge, the index
 * converts once to a Map and stays one.
 */
export class DenseEdgeStore extends BaseEdgeStore {
  private dense: Int32Array | null = null;
  private baseId = 0;
  /** Highest offset in use in `dense`. */
  private high = -1;
  private fallback: Map<number, number> | null = null;

  protected slotOf(id: number): number {
    if (this.fallback) {
      const slot = this.fallback.get(id);
      return slot === undefined ? -1 : slot;
    }
    if (!this.dense) return -1;
    const at = id - this.baseId;
    return at >= 0 && at < this.dense.length ? this.dense[at] : -1;
  }

  private tooSparse(span: number): boolean {
    return span > DENSE_MIN_SPAN && span > (this.count + 1) * DENSE_MAX_SPAN_PER_EDGE;
  }

  private toMap(): void {
    const m = new Map<number, number>();
    for (let slot = 0; slot < this.count - 1; slot++) m.set(this.idList[slot], slot);
    this.fallback = m;
    this.dense = null;
  }

  protected remember(id: number, slot: number): void {
    if (this.fallback) {
      this.fallback.set(id, slot);
      return;
    }
    if (!this.dense) {
      this.dense = new Int32Array(INITIAL_CAPACITY).fill(-1);
      this.baseId = id;
    }
    let at = id - this.baseId;
    if (at < 0) {
      const used = this.high + 1;
      const shift = -at + Math.max(INITIAL_CAPACITY, used);
      if (this.tooSparse(shift + used)) {
        this.toMap();
        this.fallback!.set(id, slot);
        return;
      }
      const next = new Int32Array(shift + used).fill(-1);
      next.set(this.dense.subarray(0, used), shift);
      this.dense = next;
      this.baseId -= shift;
      this.high += shift;
      at += shift;
    } else if (at >= this.dense.length) {
      if (this.tooSparse(at + 1)) {
        this.toMap();
        this.fallback!.set(id, slot);
        return;
      }
      const next = new Int32Array(Math.max(at + 1, this.dense.length * 2)).fill(-1);
      next.set(this.dense);
      this.dense = next;
    }
    this.dense[at] = slot;
    if (at > this.high) this.high = at;
  }
}

/**
 * The implementation the parsers use.
 *
 * Chosen by measurement - see `tests/bench/edge-store-bench.ts`. One store
 * per definition of 600 edges (the legacy file's average), at the id
 * densities real files show:
 *
 * | storage                     | bytes/edge at ~20% | at ~50% | lookup  |
 * |-----------------------------|-------------------:|--------:|--------:|
 * | today: `Map<id,[v1,v2]>`    | 112                | 112     | 16.3 ns |
 * | `Map<id,index>`             | 69                 | 69      | 24.6 ns |
 * | sorted `Uint32Array`        | 36                 | 36      | 51.1 ns |
 * | dense `Int32Array`          | 49                 | 35      | 9.9 ns  |
 *
 * Unlike vertices (#208), the dense index wins on both axes: the loop
 * reconstruction reads one endpoint per edge use, so lookups dominate, and
 * at real densities the span-sized index costs 8 to 20 bytes per edge, far
 * below a Map entry. The sorted array is slightly smaller on legacy files
 * but 5x slower on the hot path. {@link MapEdgeStore} and
 * {@link SortedEdgeStore} stay tested and benchmarked so the choice remains
 * re-checkable.
 */
export const DefaultEdgeStore = DenseEdgeStore;
