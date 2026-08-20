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
