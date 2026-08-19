/**
 * The bundled blank-document scaffold `create.ts` splices new geometry
 * into, plus the handful of scaffold-specific byte offsets that make that
 * splicing possible - ported from Python's `openskp.create` module (see
 * that module's docstring, `_load_scaffold`, and the `SkpBuilder.__init__`
 * constants immediately below it, which this file mirrors).
 *
 * **Why this exists and where the bytes came from.** Every legacy `.skp`
 * file carries a header/material-manager/style-and-font-manager region
 * this project has not fully reverse-engineered - only enough of it is
 * understood to preserve it byte-for-byte and correctly renumber the
 * handful of internal references inside it that shift when new geometry
 * is inserted (see `TAIL_REF_POSITIONS` below). Rather than guess at
 * synthesizing that region from scratch, new files are built by splicing
 * genuinely-written geometry into this bundled minimal empty-document
 * template.
 *
 * The template's bytes came from Trimble's own official SketchUp SDK
 * during the Python writer's research phase (`SUModelCreate` + a bare
 * `SUModelSaveToFileWithVersion` call, nothing else) - disclosed here
 * plainly rather than hidden, exactly as Python's own module docstring
 * discloses it. Its content is SketchUp's own built-in empty-document
 * boilerplate (default style, default "Layer0", references to system
 * fonts like Arial/Tahoma) - the same bytes any brand-new SketchUp
 * document contains regardless of who created it, not anyone's creative
 * work or user/client data. This exact file (verified below by length and,
 * in this package's own tests, by SHA-256) is also already bundled as
 * `packages/typescript/tests/fixtures/blank_v17.skp`, used there to test
 * the *reader*'s handling of zero-material legacy files - the same file,
 * reused for the writer's own splice target.
 *
 * **Embedded as base64, not a separate binary file.** Python bundles this
 * as a standalone file under `_scaffold/` and reads it with
 * `importlib.resources` at construction time. This package targets both
 * Node and the browser and is bundled by tsup into a single JS file with
 * no binary-asset pipeline, so the bytes are embedded directly as a
 * base64 string constant instead - simpler, and works identically in
 * both environments with no filesystem access at all.
 *
 * **The offset constants below** (`MATERIAL_INSERT_POS`, `BASE`,
 * `LAYER_COUNT_POS`, etc.) are specific to this exact scaffold file's
 * bytes, not derived generically - they were produced once by running
 * Python's own `SkpBuilder.__init__` bootstrap (which parses the
 * scaffold's material-manager/layer-list/definition-list/root-entity-list
 * region with `openskp.legacy`'s own trusted reader to locate each
 * splice point) against this file and reading off the resulting field
 * values. Re-implementing that generic bootstrap-probing logic in
 * TypeScript purely to re-derive six numbers and a one-entry class-slot
 * map from a single fixed, hash-pinned file would only be a source of
 * subtle porting bugs for no benefit - the values themselves are exactly
 * as empirically-derived-and-then-hardcoded as `TAIL_REF_POSITIONS`
 * already is in both ports. If the bundled scaffold file is ever swapped,
 * these must be re-derived the same way (see `create.py`'s
 * `_load_scaffold` for the equivalent Python-side warning).
 */

/** Base64-encoded bytes of the blank v17 scaffold document (5796 bytes) -
 * byte-identical to `packages/python/src/openskp/_scaffold/blank_v17.skp`
 * and to this package's own `tests/fixtures/blank_v17.skp`. */
export const SCAFFOLD_BASE64 =
  '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';

// This package has no hard dependency on @types/node (it targets the
// browser too), so - like index.ts's own SkpFile.open - Node-only globals
// are declared `any` here rather than pulled in via @types/node.
declare const Buffer: any;

/** Decode `SCAFFOLD_BASE64` to bytes - works in both Node.js and browser
 * environments (no `Buffer`-only dependency). */
export function loadScaffold(): Uint8Array {
  let binary: string;
  if (typeof atob === 'function') {
    binary = atob(SCAFFOLD_BASE64);
  } else {
    binary = Buffer.from(SCAFFOLD_BASE64, 'base64').toString('binary');
  }
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) {
    bytes[i] = binary.charCodeAt(i);
  }
  return bytes;
}

/** Expected decoded length of the scaffold - a cheap sanity check that
 * catches base64 corruption/truncation immediately, the same role
 * Python's `_SCAFFOLD_SHA256` hash check plays (this package's own test
 * suite additionally checks the full SHA-256 against the same fixture
 * file bundled at `tests/fixtures/blank_v17.skp`, see `create.test.ts`). */
export const SCAFFOLD_LENGTH = 5796;

/** SHA-256 of the decoded scaffold bytes, for the test-time integrity
 * check (same value as Python's `_SCAFFOLD_SHA256`). */
export const SCAFFOLD_SHA256 = '809a1ab73a20a192ab13aaff197afb1c67d0e9352f6a353a9cd8030919f8a6c3';

// ---------------------------------------------------------------------
// Scaffold-specific splice-point offsets - see this file's own top
// docstring for how these were derived. All positions are absolute byte
// offsets into the scaffold buffer unless noted otherwise.
// ---------------------------------------------------------------------

/** Absolute offset of the material-manager insertion point - the position
 * right before the (zero, in this scaffold) material count / the "layer
 * list marker" a zero-material scaffold starts with. */
export const MATERIAL_INSERT_POS = 3400;

/** The archive slot new material/layer/definition/geometry writers start
 * allocating from - the scaffold's own `CLayer` class declaration slot. */
export const BASE = 9;

/** Absolute offset of the u32 layer-count field preceding the layer list. */
export const LAYER_COUNT_POS = 3405;

/** How many layers the scaffold already has (always 1: Layer0). */
export const ORIG_LAYER_COUNT = 1;

/** Absolute offset right after the scaffold's own (pre-existing) layer
 * list - where newly-added layers are spliced in. */
export const LAYER_INSERT_POS = 3505;

/** Absolute offset of the u32 definition-count field. */
export const DEF_COUNT_POS = 3507;

/** How many component definitions the scaffold already has (always 0). */
export const ORIG_DEF_COUNT = 0;

/** Absolute offset of the u32 root-entity-count field. */
export const ROOT_COUNT_POS = 3511;

/** How many root entities the scaffold already has (always 0). */
export const ORIG_ROOT_COUNT = 0;

/** Absolute offset where the document "tail" (undecoded style/font-manager
 * region) begins - everything from here to EOF is copied through with
 * only `TAIL_REF_POSITIONS`/the ISO camera patches touched. */
export const TAIL_POS = 3515;

/** The next free archive slot after parsing through the scaffold's own
 * material section (absent) + layer list (Layer0) + definition-list
 * anchor - i.e. where a material/layer/definition/geometry writer with
 * nothing ahead of it would start allocating from. */
export const SCAFFOLD_NEXT_SLOT = 11;

/** Same as `SCAFFOLD_NEXT_SLOT` but captured right before the
 * definition-list anchor (active-layer back-ref) is read - the starting
 * slot for a layer writer once the final material count is known (see
 * `SkpBuilder.addLayer` in create.ts, mirroring Python's
 * `_layer_writer_base`). Numerically identical to `SCAFFOLD_NEXT_SLOT`
 * for this scaffold (the anchor is a back-ref, so it allocates nothing),
 * kept as a separate named constant to mirror Python's own two distinct
 * fields of the same value. */
export const LAYER_WRITER_BASE = 11;

/** Every class already declared by the time the scaffold's root entity
 * list starts - just `CLayer`, at its `BASE` slot (Layer0's own class
 * declaration). */
export const SCAFFOLD_CLASS_SLOT: Readonly<Record<string, number>> = { CLayer: BASE };
