# CDimensionLinear (legacy v17/v18) — field notes for the writer

Harvested from real files (quiroz: 28 dims, casa bueno: 16, yanque 661MB)
while building the reader fixes (PR #194). Basis for `add_dimension()`.

## Record layout (after the class tag)

```
preamble                  attrs ref (null) + pid mask/bytes (v17+)
drawbase                  10 bytes (mat u16, hidden, soft, smooth, layer u16)
text     utf16            EMPTY on every auto-computed dimension observed
font     object           CSkFont — new on first use, back-ref after
B37      37 bytes         u8 + u32(=3 v18 / 1 v17?) + 32 bytes zeros
ref1     MFC tag          connection 1 → CVertex back-ref (2 or 6 bytes)
B42      42 bytes         u16 + 40 bytes: u32(=2) + u32(=4) + zeros
ref2     MFC tag          connection 2 → CVertex back-ref
B82      82 bytes         u16 + f64×7 + u32 + f64(OFFSET, inches, signed)
                          + f64(0) + u32(=3)
```

- NO coordinates are cached: geometry comes 100% from the two vertex
  back-refs. Anchored dims in v17 (quiroz) burn NO MapObject indices —
  writing anchored dims is safe for slot accounting.
- The B82 offset f64 (bytes 62..69) is the dimension-line offset from
  the measured edge, inches, signed.
- The u32 at B82+58 varies (1/2/3) — placement/alignment mode?
- The 7 doubles at B82+2..57 are the unsolved part: values are 0/±1
  (plus float-noise) and DO vary with orientation, but the samples are
  too axis-aligned to fix the semantics:
    quiroz  (dir ±Z): (0, 0,  1, 0, -1, 0, 0)   u32=2
    casa#0  (dir +X): (0, 0,  0, -1, -1, 0, 0)  u32=1
    casa#2  (dir +Y): (0, ~0, ~0, -1, -1, ~0, ~0) u32=2
  Candidate readings: two 2D unit attachment points + spare, or a plane
  basis with per-axis sign flags. TO RESOLVE: template-write dims in
  varied orientations and check rendering in real SketchUp (Web) — the
  same controlled-file method the July texture calibration used.

## CSkFont

Small record (~34 bytes) with the face name; one per file re-used by
back-ref. Template from quiroz.

## Writer strategy

Template + patch: emit quiroz's byte template, patching refs (our
vertex slots), offset, font ref, pid bytes; leave the 7 doubles as a
per-orientation hypothesis to be calibrated against SketchUp Web
rendering. Validate every generated file by round-tripping through the
(fixed) legacy reader before human inspection.

## RESOLVED — SDK ground truth (2026-08-20)

A MinGW-built helper (`mkdim`) drove the real SketchUpAPI.dll under
Wine to CREATE dimensions in every orientation and save as SU2017
(`SUModelSaveToFileWithVersion`, version enum: 6={13} … 10={17} 11={18}).
The harvested records settle everything:

- B37 / B42 are the CONNECTION blocks: `[flags][u32 TYPE][u32 4]
  [point3d]` with TYPE **1 = free point stored inline** (object ref is
  null) and **2 = anchored** (point zeroed, back-ref follows) — the same
  1/2 enum as VFF connections.
- The placement head for FREE dimensions is a constant SketchUp
  default: seven doubles `(0,0,0, 1,1,0, 0)`, mode u32 = 0, trailing
  u32 = 1. The offset lives at B82+62 and can be patched in.
- The orientation-specific heads seen in human-drawn corpus files
  (quiroz) apply to ANCHORED dimensions; decoding those fully would be
  the follow-up if anchored writing is wanted (extend mkdim with
  anchored creates).

`add_dimension()` writes byte-exact SDK-shaped free dimensions;
verified by SDK acceptance, reader round-trip, and rendering in real
SketchUp (Web) across X/Y/Z/diagonal orientations.

## CText (leader texts) — same Rosetta method (2026-08-20)

A second SDK helper (`mktext`) settled the text record. Key findings:

- A `SUTextRef` needs a FONT assigned (`SUTextSetFont` with a font from
  `SUModelGetFonts`) or the model fails to serialize (SUResult 7) in
  every version — the failure is the text, not the target version.
- Record layout: preamble + drawbase + font (inline first use /
  back-ref) + two f64 0.5 + the SAME free-connection block dimensions
  use (`[u32 1][u32 4][point3d]`) + a fixed 75-byte tail + string +
  5 zero bytes.
- The reader's "text delimiter" block encodes the ARROW TYPE in its
  u32 (values 0-4 seen); the old predicate hard-required 3 and silently
  dropped any text with a different arrow (root-list tolerance ate the
  error). Now any arrow ≤ 8 matches.
- `_read_skfont` consumed the pid MASK but not the pid bytes it
  declares — fixed; SDK-written fonts also carry a longer tail, which
  the text reader's delimiter scan absorbs.
- Leader VECTORS did not persist through `SUTextSetLeaderVector` in the
  harness (records carry only the anchor point); point-labels are what
  `add_text()` writes, which matches IngeTrazo's text entities.
